"""Thumbnail e rascunho editorial determinístico para publicação manual, sempre local (HOR-164 I7)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import subprocess
from datetime import datetime, timezone
from threading import RLock
import uuid

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .local_pilot import registered_json
from .license_reviews import LicenseReviewService
from .media_contracts import PILOT_INPUT_PATH, UNSAFE_TEXT, ProvenanceAsset, apply_license_reviews, commercial_use_permitted, validate_asset_provenance
from .processes import run_sync
from .production import confined, inspect_artifact
from .storage import DatabaseUnavailable, canonical_json_bytes, now
from .workflows import WorkflowConflict, digest


FAILED_MARKER = 'failed.json'
LICENSES_PENDING = 'Licenças pendentes: confira os direitos de todos os assets antes de publicar.'
LICENSES_REVIEWED = 'Licenças revisadas: a publicação manual continua sob responsabilidade humana.'
DISCLOSURE_MARKER = chr(10) * 2 + 'Divulgações obrigatórias:' + chr(10)
_INPUTS = PILOT_INPUT_PATH


def _as_json(value):
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    return json.loads(value)


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def licenses_pending(effective) -> bool:
    """Pendente até todo asset estar verificado por humano *e* com uso comercial `allowed` (HOR-097).

    `verified` com `restricted`/`unknown` registra a leitura da licença, mas não libera uso comercial: segue pendente.
    """
    return effective is None or not all(commercial_use_permitted(ProvenanceAsset.model_validate(a)) for a in effective['assets'])


class MetadataTextError(ValueError):
    """Texto de metadados que não pode ser aceito sem esconder dados do usuário (vira 422 com explicação)."""


def failure_message(error):
    """Motivo registrável: mensagens de WorkflowConflict são fixas; erros de E/S viram texto genérico (sem caminhos)."""
    if isinstance(error, WorkflowConflict):
        return str(error)[:300]
    return 'Falha de leitura ou escrita durante a operação; confira o disco e os artefatos.'


def mark_failed(folder, message):
    """Falha definitiva da chave: apaga o que a tentativa criou (a pasta é nova) e deixa só o motivo, sem conteúdo."""
    try:
        for child in list(folder.iterdir()):
            if child.is_file() and not child.is_symlink():
                child.unlink()
        (folder / FAILED_MARKER).write_text(json.dumps({'schema_version': 1, 'failed_at': now(), 'reason': message[:300]},
                                                       ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass  # pasta reservada permanece: a mesma chave continua recusada, nunca reexecutada em silêncio


def read_failure(folder):
    try:
        marker = folder / FAILED_MARKER
        if folder.is_symlink() or marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
            return None
        reason = json.loads(marker.read_text(encoding='utf-8')).get('reason')
        return reason if isinstance(reason, str) else None
    except (OSError, ValueError):
        return None


MAX_IMAGE_BYTES = 16 * 1024 * 1024  # 1080x1920 PNG tem poucos MiB; acima disso a saída da ferramenta é suspeita


class ThumbnailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    timecode_seconds: float = Field(ge=0, allow_inf_nan=False)


class MetadataRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=5000)

    @field_validator('title')
    @classmethod
    def single_line_title(cls, value):
        if UNSAFE_TEXT.search(value) or any(char in value for char in ('\r', '\n', '\t')):
            raise ValueError('Título com quebra de linha, controle ou marca de direção.')
        return value

    @field_validator('description')
    @classmethod
    def safe_description(cls, value):
        text = value.replace('\r\n', '\n')  # CRLF de editores Windows é aceito; CR isolado não
        if UNSAFE_TEXT.search(text) or '\r' in text:
            raise ValueError('Descrição com caractere de controle ou marca de direção.')
        return value


def approved_script(conn, row):
    script_review = _as_json(row['script_review'])
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT response FROM horus.script_receipts WHERE scope = %s', (f"review:{row['script_id']}",))
        for receipt in cur.fetchall():
            snapshot = _as_json(receipt['response'])
            if (snapshot.get('status') == 'approved' and snapshot.get('version') == row['script_version']
                    and snapshot.get('review') == script_review
                    and digest(snapshot.get('document')) == row['script_content_hash']):
                return snapshot
    raise WorkflowConflict('Snapshot do roteiro aprovado indisponível.')


class PublishingService:
    def __init__(self, productions):
        self.productions, self.store, self.settings = productions, productions.store, productions.settings
        self.lock = RLock()

    def _uow(self, *, readonly: bool = False):
        if self.store is None or not hasattr(self.store, 'uow'):
            raise DatabaseUnavailable('database_unavailable')
        return self.store.uow(readonly=readonly)

    @staticmethod
    def _require_production(conn, production_id: str, *, for_update: bool = False) -> dict:
        sql = 'SELECT * FROM horus.productions WHERE id = %s' + (' FOR UPDATE' if for_update else '')
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (production_id,))
            row = cur.fetchone()
        if row is None:
            raise KeyError(production_id)
        return row

    def _provenance_and_history(self, conn, production_id: str, artifacts: list[dict]) -> tuple[dict | None, list[dict]]:
        root = self.productions.root(production_id)
        inputs = {a['relative_path']: a['sha256'] for a in artifacts if _INPUTS.fullmatch(a['relative_path'])}
        data = registered_json(root, artifacts, 'asset-provenance.json')
        verdict = validate_asset_provenance(data, artifacts=inputs)
        provenance = verdict['value'] if verdict['ok'] else None
        history = LicenseReviewService._history(conn, production_id)
        return provenance, history

    def _approved(self, conn, production_id, verify=True, *, for_update: bool = False):
        """Estado de aprovação no banco; `verify=False` adia a leitura pesada do vídeo para fora da transação."""
        row = self._require_production(conn, production_id, for_update=for_update)
        review = _as_json(row['review']) or {}
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT * FROM horus.jobs WHERE id = %s', (row['job_id'],))
            job = self.store.job(cur.fetchone())
        if review.get('decision') != 'approve' or job['status'] != 'succeeded':
            raise WorkflowConflict('Produção concluída e aprovada é necessária para preparar a publicação manual.')
        artifacts = _as_json(row['artifacts']) or []
        video = next((a for a in artifacts if a['kind'] == 'video'
                      and a['sha256'] == review.get('artifact_sha256')), None)
        if not video:
            raise WorkflowConflict('Vídeo aprovado ausente ou adulterado.')
        if verify:
            self._verify_video(production_id, video)
        return row, video

    def _verify_video(self, production_id, video):
        """Hash completo do arquivo: chamar SEM transação aberta (não segura o lock do banco)."""
        try:
            matches = inspect_artifact(self.productions.root(production_id), video['relative_path'], 'video') == video
        except WorkflowConflict:
            matches = False
        if not matches:
            raise WorkflowConflict('Vídeo aprovado ausente ou adulterado.')

    def _verified_state(self, production_id):
        """Aprovação vigente + vídeo conferido por hash, sem manter transação durante a leitura."""
        with self._uow(readonly=True) as conn:
            _, video = self._approved(conn, production_id, verify=False)
        self._verify_video(production_id, video)
        return video

    @staticmethod
    def _same_approval(video, fresh_video):
        if fresh_video != video:
            raise WorkflowConflict('Aprovação ou vídeo mudaram durante a operação; tente novamente.')

    def _new_folder(self, root, relative):
        # Verifica links antes de criar qualquer arquivo, inclusive em ancestrais.
        folder = root / relative
        for part in (folder, *folder.parents):
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise WorkflowConflict('Links não são permitidos nos arquivos de publicação manual.')
        folder.mkdir(parents=True, exist_ok=False)
        return folder

    def _append(self, conn, row, artifacts):
        current = _as_json(row['artifacts']) or []
        conn.execute(
            'UPDATE horus.productions SET artifacts = %s WHERE id = %s',
            (Jsonb(current + artifacts), row['id']),
        )

    def _run(self, command, timeout):
        environment = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        try:
            return run_sync(command, timeout=timeout, env=environment)
        except (OSError, subprocess.SubprocessError):
            raise WorkflowConflict('FFmpeg/ffprobe não conseguiu preparar a thumbnail; confira as ferramentas locais e o vídeo aprovado.') from None

    def _record_thumbnail_failure(self, production_id: str, key: str, reason: str) -> None:
        payload = {'schema_version': 1, 'failed_at': now(), 'reason': reason[:300]}
        moment = datetime.now(timezone.utc)
        with self._uow() as conn:
            conn.execute(
                """
                UPDATE horus.publishing_receipts
                SET status = 'failed', response = %s, response_canonical = %s, updated_at = %s
                WHERE production_id = %s AND scope = 'thumbnail' AND idempotency_key = %s
                """,
                (Jsonb(payload), canonical_json_bytes(payload), moment, production_id, key),
            )

    def thumbnail(self, production_id, request, key):
        with self.lock:
            root = self.productions.root(production_id)
            ident = hashlib.sha256(key.encode()).hexdigest()
            relative = f'publishing/thumbnails/{ident}'
            receipt_path = relative + '/receipt.json'
            with self._uow(readonly=True) as conn:
                _, approved_video = self._approved(conn, production_id, verify=False)
            self._verify_video(production_id, approved_video)  # hash fora da transação, também no replay (paridade com o legado)
            with self._uow() as conn:
                row, video = self._approved(conn, production_id, verify=False)
                req_hash = hashlib.sha256(canonical_json_bytes({
                    'timecode_seconds': request.timecode_seconds,
                    'video_sha256': video['sha256'],
                })).hexdigest()
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT request_hash, response, status
                        FROM horus.publishing_receipts
                        WHERE production_id = %s AND scope = 'thumbnail' AND idempotency_key = %s
                        FOR UPDATE
                        """,
                        (production_id, key),
                    )
                    existing = cur.fetchone()
                if existing is not None:
                    if existing['status'] == 'succeeded':
                        if existing['request_hash'] != req_hash:
                            raise WorkflowConflict('Chave de thumbnail usada com outro timecode ou vídeo.')
                        saved = _as_json(existing['response'])
                        for artifact in saved['artifacts']:
                            if inspect_artifact(root, artifact['relative_path'], 'thumbnail') != artifact:
                                raise WorkflowConflict('Thumbnail registrada adulterada.')
                        return saved, False
                    if existing['status'] == 'failed':
                        reason = (_as_json(existing['response']) or {}).get('reason')
                        raise WorkflowConflict(
                            f'Tentativa anterior desta chave falhou ({reason}); use uma nova chave.' if reason
                            else 'Tentativa anterior de thumbnail presente; preserve-a e use outra chave.'
                        )
                    raise WorkflowConflict('Tentativa anterior de thumbnail presente; preserve-a e use outra chave.')

                artifacts_list = _as_json(row['artifacts']) or []
                saved = registered_json(root, artifacts_list, receipt_path)
                if saved is not None:
                    if saved['timecode_seconds'] != request.timecode_seconds or saved['video_sha256'] != video['sha256']:
                        raise WorkflowConflict('Chave de thumbnail usada com outro timecode ou vídeo.')
                    for artifact in saved['artifacts']:
                        if inspect_artifact(root, artifact['relative_path'], 'thumbnail') != artifact:
                            raise WorkflowConflict('Thumbnail registrada adulterada.')
                    return saved, False

                if (root / relative).exists() or (root / relative).is_symlink():
                    reason = read_failure(root / relative)  # chave que já falhou: recusa antes de rodar qualquer ferramenta
                    raise WorkflowConflict(f'Tentativa anterior desta chave falhou ({reason}); use uma nova chave.' if reason
                                           else 'Tentativa anterior de thumbnail presente; preserve-a e use outra chave.')

                moment = datetime.now(timezone.utc)
                empty = {}
                conn.execute(
                    """
                    INSERT INTO horus.publishing_receipts
                        (production_id, scope, idempotency_key, request_hash, response, response_canonical, status, created_at, updated_at)
                    VALUES (%s, 'thumbnail', %s, %s, %s, %s, 'pending', %s, %s)
                    """,
                    (production_id, key, req_hash, Jsonb(empty), canonical_json_bytes(empty), moment, moment),
                )

            try:
                self._verify_video(production_id, video)  # hash fora da transação
                ffmpeg = self.settings.ffmpeg
                if not ffmpeg or not ffmpeg.is_file():
                    raise WorkflowConflict('Configure HORUS_FFMPEG com o executável FFmpeg local antes de criar a thumbnail.')
                ffprobe = ffmpeg.with_name('ffprobe' + ffmpeg.suffix)
                if not ffprobe.is_file():
                    raise WorkflowConflict('Prepare ffprobe ao lado de HORUS_FFMPEG para conferir a duração do vídeo.')
                path = confined(root, video['relative_path'])
                try:
                    result = json.loads(self._run([str(ffprobe), '-v', 'error', '-protocol_whitelist', 'file,pipe',
                        '-show_entries', 'format=duration', '-of', 'json', str(path)], 5))
                    duration = float(result['format']['duration'])
                except (ValueError, KeyError, TypeError):
                    raise WorkflowConflict('ffprobe não informou duração válida para o vídeo aprovado.') from None
                if not math.isfinite(duration) or duration <= 0 or request.timecode_seconds >= duration:
                    raise WorkflowConflict('Timecode fora da duração do vídeo; use um valor de zero até antes do fim.')
                if (root / relative).exists() or (root / relative).is_symlink():
                    reason = read_failure(root / relative)
                    raise WorkflowConflict(f'Tentativa anterior desta chave falhou ({reason}); use uma nova chave.' if reason
                                           else 'Tentativa anterior de thumbnail presente; preserve-a e use outra chave.')
                folder = self._new_folder(root, relative)
            except (WorkflowConflict, OSError, ValueError) as failure:
                self._record_thumbnail_failure(production_id, key, failure_message(failure))
                raise

            try:
                return self._extract(production_id, key, request, root, relative, receipt_path, folder, video, ffmpeg, path)
            except (WorkflowConflict, OSError, ValueError) as failure:
                reason = failure_message(failure)
                mark_failed(folder, reason)  # a chave fica registrada como falha; nova chave funciona
                self._record_thumbnail_failure(production_id, key, reason)
                raise

    def _extract(self, production_id, key, request, root, relative, receipt_path, folder, video, ffmpeg, path):
        png, jpg = folder / 'thumbnail.png', folder / 'thumbnail.jpg'
        self._run([str(ffmpeg), '-nostdin', '-n', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-ss', str(request.timecode_seconds), '-i', str(path), '-map', '0:v:0', '-frames:v', '1', '-an',
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2', str(png)], 15)
        # Converte o mesmo quadro PNG, sem procurar outro frame do vídeo.
        confined(root, relative + '/thumbnail.png')
        self._run([str(ffmpeg), '-nostdin', '-n', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-i', str(png), '-frames:v', '1', '-q:v', '2', str(jpg)], 10)
        for image in (png, jpg):
            if image.is_file() and image.stat().st_size > MAX_IMAGE_BYTES:
                raise WorkflowConflict('Saída de imagem acima do limite esperado; thumbnail recusada.')
        artifacts = [inspect_artifact(root, relative + '/thumbnail.' + suffix, 'thumbnail') for suffix in ('png', 'jpg')]
        with png.open('rb') as stream:
            header = stream.read(24)
        if len(header) != 24 or struct.unpack('>II', header[16:24]) != (1080, 1920):
            raise WorkflowConflict('Thumbnail não tem as dimensões esperadas de 1080×1920.')
        saved = {'timecode_seconds': request.timecode_seconds, 'video_sha256': video['sha256'],
                 'width': 1080, 'height': 1920, 'artifacts': artifacts, 'created_at': now(), 'publishable': False}
        (folder / 'receipt.json').write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
        manifest_entry = inspect_artifact(root, receipt_path, 'manifest')
        self._verify_video(production_id, video)  # hash do vídeo DEPOIS da extração e FORA da transação
        moment = datetime.now(timezone.utc)
        with self._uow() as conn:
            fresh, fresh_video = self._approved(conn, production_id, verify=False, for_update=True)
            if fresh_video != video:
                raise WorkflowConflict('Vídeo aprovado mudou durante a extração.')
            self._append(conn, fresh, [*artifacts, manifest_entry])
            conn.execute(
                """
                UPDATE horus.publishing_receipts
                SET status = 'succeeded', response = %s, response_canonical = %s, updated_at = %s
                WHERE production_id = %s AND scope = 'thumbnail' AND idempotency_key = %s
                """,
                (Jsonb(saved), canonical_json_bytes(saved), moment, production_id, key),
            )
        return saved, True

    def _latest_saved(self, row):
        artifacts = _as_json(row['artifacts']) or []
        latest = next((a for a in reversed(artifacts) if re.fullmatch(r'publishing/metadata/[a-f0-9]{32}\.json', a['relative_path'])), None)
        if not latest:
            return None
        saved = registered_json(self.productions.root(row['id']), artifacts, latest['relative_path'])
        if saved is None:
            raise WorkflowConflict('Metadados registrados adulterados.')
        return saved

    def _metadata(self, conn, row, edit=None, strict=False):
        document = approved_script(conn, row)['document']
        artifacts = _as_json(row['artifacts']) or []
        provenance, history = self._provenance_and_history(conn, row['id'], artifacts)
        effective = apply_license_reviews(provenance, history) if provenance is not None else None
        pending = licenses_pending(effective)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT * FROM horus.jobs WHERE id = %s', (row['job_id'],))
            job = self.store.job(cur.fetchone())
        result = (job or {}).get('result') or {}
        narration = result.get('narration_disclosure') or 'Narração sintética/voz local: confira a voz selecionada e seus termos de uso.'
        disclosures = [narration,
                       LICENSES_PENDING if pending else LICENSES_REVIEWED,
                       'publishable=false: este rascunho não autoriza publicação automática.']
        footer = '\n\nDivulgações obrigatórias:\n' + '\n'.join(disclosures)
        if edit is None:
            saved = self._latest_saved(row)
            if saved:
                edit = MetadataRequest(title=saved['title'], description=saved['description'].split(DISCLOSURE_MARKER)[0])
        elif strict and DISCLOSURE_MARKER in edit.description:
            # Devolver o texto lido por GET é permitido (o rodapé é regerado); qualquer outro texto depois do marcador
            # seria descartado em silêncio, então é recusado com explicação.
            previous = self._latest_saved(row)
            allowed = {part.strip() for item in (*disclosures, LICENSES_PENDING, LICENSES_REVIEWED) for part in item.splitlines()}
            if previous:
                allowed |= {line.strip() for line in previous['description'].split(DISCLOSURE_MARKER, 1)[-1].splitlines()}
            tail = edit.description.split(DISCLOSURE_MARKER, 1)[1]
            if any(line.strip() and line.strip() not in allowed for line in tail.splitlines()):
                raise MetadataTextError('O texto depois de "Divulgações obrigatórias:" não pode ser editado: essas linhas são '
                                        'geradas automaticamente e seriam descartadas. Escreva seu texto ANTES desse marcador '
                                        'ou remova o marcador.')
        cap = min(600, max(0, (5000 - len(footer) - 64 * len(document['sections'])) // len(document['sections'])))
        title = edit.title if edit else document['title'][:100]
        body = edit.description.split(DISCLOSURE_MARKER)[0] if edit else '\n\n'.join(
            s['heading'][:60] + ': ' + s['narration'][:cap] for s in document['sections'])
        if not title.strip() or len(body + footer) > 5000:
            raise ValueError('Título vazio ou descrição sem espaço para as divulgações obrigatórias.')
        cited = list(dict.fromkeys(source for section in document['sections'] for source in section['source_ids']))
        sources = {}
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT workflow_id, workflow_revision FROM horus.scripts WHERE id = %s', (row['script_id'],))
            script = cur.fetchone()
            analysis = None
            if script is not None:
                cur.execute(
                    "SELECT content, content_hash FROM horus.artifacts WHERE workflow_id = %s AND revision = %s AND stage = 'analysis'",
                    (script['workflow_id'], script['workflow_revision']),
                )
                analysis = cur.fetchone()
        if analysis:
            content = _as_json(analysis['content'])
            if digest(content) != analysis['content_hash']:
                raise WorkflowConflict('Snapshot de fontes com integridade inválida.')
            sources = {s['id']: s for s in content.get('result', {}).get('sources', [])}
        return {'title': title, 'description': body + footer,
                'sources': [{'id': ident, 'url': sources.get(ident, {}).get('url'),
                             'title': sources.get(ident, {}).get('title')} for ident in cited],
                'disclosures': disclosures, 'licenses_pending': pending, 'publishable': False}

    def metadata(self, production_id):
        video = self._verified_state(production_id)
        with self._uow(readonly=True) as conn:
            row, fresh = self._approved(conn, production_id, verify=False)
            self._same_approval(video, fresh)
            return self._metadata(conn, row)

    def edit_metadata(self, production_id, request):
        with self.lock:
            video = self._verified_state(production_id)  # leitura pesada antes de abrir a transação de escrita
            with self._uow() as conn:
                row, fresh = self._approved(conn, production_id, verify=False, for_update=True)
                self._same_approval(video, fresh)
                return self._write_metadata(conn, row, production_id, request)

    def _write_metadata(self, conn, row, production_id, request):
        value = self._metadata(conn, row, request, strict=True)
        root = self.productions.root(production_id)
        artifacts = _as_json(row['artifacts']) or []
        latest = next((a for a in reversed(artifacts) if re.fullmatch(r'publishing/metadata/[a-f0-9]{32}\.json', a['relative_path'])), None)
        if latest and registered_json(root, artifacts, latest['relative_path']) == value:
            return value  # mesma edição: não registra outro arquivo
        relative = 'publishing/metadata/' + uuid.uuid4().hex + '.json'
        parent = root / 'publishing/metadata'
        if not parent.exists():
            self._new_folder(root, 'publishing/metadata')
        # Confina também pastas existentes antes de escrever um nome novo.
        for part in (parent, *parent.parents):
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise WorkflowConflict('Links não são permitidos nos metadados.')
        with (root / relative).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
        self._append(conn, row, [inspect_artifact(root, relative, 'manifest')])
        return value

    def optional_files(self, conn, row):
        artifacts, root = _as_json(row['artifacts']) or [], self.productions.root(row['id'])
        payload = {}
        if any(re.fullmatch(r'publishing/metadata/[a-f0-9]{32}\.json', a['relative_path']) for a in artifacts):
            payload['metadata.json'] = json.dumps(self._metadata(conn, row), ensure_ascii=False, indent=2).encode()
        latest = next((a for a in reversed(artifacts) if re.fullmatch(r'publishing/thumbnails/[a-f0-9]{64}/receipt\.json', a['relative_path'])), None)
        if latest:
            receipt = registered_json(root, artifacts, latest['relative_path'])
            review = _as_json(row['review']) or {}
            if receipt is None or receipt['video_sha256'] != review.get('artifact_sha256'):
                raise WorkflowConflict('Recibo da thumbnail inválido.')
            for item in receipt['artifacts']:
                if item not in artifacts or inspect_artifact(root, item['relative_path'], 'thumbnail') != item:
                    raise WorkflowConflict('Thumbnail adulterada.')
                image = confined(root, item['relative_path'])
                if image.stat().st_size > MAX_IMAGE_BYTES:
                    raise WorkflowConflict('Thumbnail acima do limite esperado.')
                data = image.read_bytes()
                if hashlib.sha256(data).hexdigest() != item['sha256']:
                    raise WorkflowConflict('Thumbnail alterada durante a exportação.')
                payload['thumbnail.' + item['relative_path'].rsplit('.', 1)[1]] = data
        return payload
