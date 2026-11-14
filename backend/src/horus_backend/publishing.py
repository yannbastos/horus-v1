"""Thumbnail e rascunho editorial determinístico para publicação manual, sempre local."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import subprocess
from threading import RLock
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .license_reviews import LicenseReviewService
from .media_contracts import UNSAFE_TEXT, apply_license_reviews
from .local_pilot import registered_json
from .production import confined, inspect_artifact
from .storage import now
from .workflows import WorkflowConflict, digest


FAILED_MARKER = 'failed.json'
LICENSES_PENDING = 'Licenças pendentes: confira os direitos de todos os assets antes de publicar.'
LICENSES_REVIEWED = 'Licenças revisadas: a publicação manual continua sob responsabilidade humana.'
DISCLOSURE_MARKER = chr(10) * 2 + 'Divulgações obrigatórias:' + chr(10)


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


def approved_script(db, row):
    for receipt in db.execute('SELECT response FROM script_receipts WHERE scope=?', (f"review:{row['script_id']}",)):
        snapshot = json.loads(receipt['response'])
        if (snapshot.get('status') == 'approved' and snapshot.get('version') == row['script_version']
                and snapshot.get('review') == json.loads(row['script_review'])
                and digest(snapshot.get('document')) == row['script_content_hash']):
            return snapshot
    raise WorkflowConflict('Snapshot do roteiro aprovado indisponível.')


class PublishingService:
    def __init__(self, productions):
        self.productions, self.store, self.settings = productions, productions.store, productions.settings
        self.lock = RLock()

    def _approved(self, db, production_id, verify=True):
        """Estado de aprovação no banco; `verify=False` adia a leitura pesada do vídeo para fora da transação."""
        row = self.productions.require(db, production_id)
        review = json.loads(row['review']) if row['review'] else {}
        job = self.store.job(db.execute('SELECT * FROM jobs WHERE id=?', (row['job_id'],)).fetchone())
        if review.get('decision') != 'approve' or job['status'] != 'succeeded':
            raise WorkflowConflict('Produção concluída e aprovada é necessária para preparar a publicação manual.')
        video = next((a for a in json.loads(row['artifacts']) if a['kind'] == 'video'
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
        with self.store.connect() as db:
            _, video = self._approved(db, production_id, verify=False)
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

    def _append(self, db, row, artifacts):
        db.execute('UPDATE productions SET artifacts=? WHERE id=?',
                   (json.dumps(json.loads(row['artifacts']) + artifacts), row['id']))

    def _run(self, command, timeout):
        environment = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        try:
            return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  env=environment, timeout=timeout, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            raise WorkflowConflict('FFmpeg/ffprobe não conseguiu preparar a thumbnail; confira as ferramentas locais e o vídeo aprovado.') from None

    def thumbnail(self, production_id, request, key):
        with self.lock:
            with self.store.connect() as db:
                row, video = self._approved(db, production_id, verify=False)
            self._verify_video(production_id, video)  # hash fora da transação
            root = self.productions.root(production_id)
            ident = hashlib.sha256(key.encode()).hexdigest()
            relative = f'publishing/thumbnails/{ident}'
            receipt_path = relative + '/receipt.json'
            saved = registered_json(root, json.loads(row['artifacts']), receipt_path)
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
            try:
                return self._extract(production_id, request, root, relative, receipt_path, folder, video, ffmpeg, path)
            except (WorkflowConflict, OSError, ValueError) as failure:
                mark_failed(folder, failure_message(failure))  # a chave fica registrada como falha; nova chave funciona
                raise

    def _extract(self, production_id, request, root, relative, receipt_path, folder, video, ffmpeg, path):
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
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')  # só conferência barata do estado no banco + registro
            fresh, fresh_video = self._approved(db, production_id, verify=False)
            if fresh_video != video:
                raise WorkflowConflict('Vídeo aprovado mudou durante a extração.')
            self._append(db, fresh, [*artifacts, manifest_entry])
        return saved, True

    def _latest_saved(self, row):
        artifacts = json.loads(row['artifacts'])
        latest = next((a for a in reversed(artifacts) if re.fullmatch(r'publishing/metadata/[a-f0-9]{32}\.json', a['relative_path'])), None)
        if not latest:
            return None
        saved = registered_json(self.productions.root(row['id']), artifacts, latest['relative_path'])
        if saved is None:
            raise WorkflowConflict('Metadados registrados adulterados.')
        return saved

    def _metadata(self, db, row, edit=None, strict=False):
        document = approved_script(db, row)['document']
        licenses = LicenseReviewService(self.store, self.settings.data_dir)
        provenance = licenses._provenance(db, row['id'])
        effective = apply_license_reviews(provenance, licenses._history(db, row['id'])) if provenance is not None else None
        pending = effective is None or any(a['status'] != 'verified' for a in effective['assets'])
        result = self.store.job(db.execute('SELECT * FROM jobs WHERE id=?', (row['job_id'],)).fetchone())['result'] or {}
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
        script = db.execute('SELECT workflow_id,workflow_revision FROM scripts WHERE id=?', (row['script_id'],)).fetchone()
        analysis = db.execute("SELECT content,content_hash FROM artifacts WHERE workflow_id=? AND revision=? AND stage='analysis'",
                              (script['workflow_id'], script['workflow_revision'])).fetchone()
        if analysis:
            content = json.loads(analysis['content'])
            if digest(content) != analysis['content_hash']:
                raise WorkflowConflict('Snapshot de fontes com integridade inválida.')
            sources = {s['id']: s for s in content.get('result', {}).get('sources', [])}
        return {'title': title, 'description': body + footer,
                'sources': [{'id': ident, 'url': sources.get(ident, {}).get('url'),
                             'title': sources.get(ident, {}).get('title')} for ident in cited],
                'disclosures': disclosures, 'licenses_pending': pending, 'publishable': False}

    def metadata(self, production_id):
        video = self._verified_state(production_id)
        with self.store.connect() as db:
            row, fresh = self._approved(db, production_id, verify=False)
            self._same_approval(video, fresh)
            return self._metadata(db, row)

    def edit_metadata(self, production_id, request):
        with self.lock:
            video = self._verified_state(production_id)  # leitura pesada antes de abrir a transação de escrita
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row, fresh = self._approved(db, production_id, verify=False)
                self._same_approval(video, fresh)
                return self._write_metadata(db, row, production_id, request)

    def _write_metadata(self, db, row, production_id, request):
        value = self._metadata(db, row, request, strict=True)
        root = self.productions.root(production_id)
        artifacts = json.loads(row['artifacts'])
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
        self._append(db, row, [inspect_artifact(root, relative, 'manifest')])
        return value

    def optional_files(self, db, row):
        artifacts, root = json.loads(row['artifacts']), self.productions.root(row['id'])
        payload = {}
        if any(re.fullmatch(r'publishing/metadata/[a-f0-9]{32}\.json', a['relative_path']) for a in artifacts):
            payload['metadata.json'] = json.dumps(self._metadata(db, row), ensure_ascii=False, indent=2).encode()
        latest = next((a for a in reversed(artifacts) if re.fullmatch(r'publishing/thumbnails/[a-f0-9]{64}/receipt\.json', a['relative_path'])), None)
        if latest:
            receipt = registered_json(root, artifacts, latest['relative_path'])
            if receipt is None or receipt['video_sha256'] != json.loads(row['review'])['artifact_sha256']:
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
