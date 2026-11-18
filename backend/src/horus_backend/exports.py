"""Pacotes locais auditáveis em PostgreSQL; sem publicação, migração ou caminhos fornecidos pelo cliente (HOR-164 I7)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
import re
from threading import RLock
import uuid
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo, BadZipFile

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .av_quality import provenance_check
from .local_pilot import confirmed_media, registered_json
from .license_reviews import LicenseReviewService
from .media_contracts import PILOT_INPUT_PATH, QualityCheck, apply_license_reviews, derive_report_status, validate_asset_provenance
from .production import confined, inspect_artifact
from .publishing import PublishingService, _as_json, _iso, approved_script, failure_message, mark_failed, read_failure
from .storage import DatabaseUnavailable, canonical_json_bytes, now
from .workflows import WorkflowConflict

HORUS_VERSION = '0.1.0'  # mesma versão do backend em /health e pyproject.toml
SCHEMA_VERSION = 12
_INPUTS = PILOT_INPUT_PATH
# Raízes POSIX comuns: uma barra seguida de uma delas já é caminho absoluto. Fora delas, exige-se ao menos dois
# segmentos ('/a/b'): '100 km /h', '/ou/' e 'dia /noite' são texto comum em pt-BR e não são caminhos.
_ROOTS = r'(?:home|users|etc|var|tmp|usr|opt|root|mnt|srv|dev|proc|sys|bin|sbin|lib|volumes|private|library|applications|system|media|run|boot)'
PATH_START = (r'(?i)(?:(?<!\w)[a-z]:[\\/]'                                  # C:\ ou c:/
              r'|(?<![\w\\])\\\\(?=[\w.$-])'                                # \\servidor\compartilhado
              r'|(?<![\w:/])//[\w.$-]+/[\w.$-]'                             # //servidor/compartilhado
              r'|\bfile:/'                                                  # file:///...
              r'|(?<!\w)~[\\/]|%[a-z_]+%[\\/]'                              # ~/ e %USERPROFILE%\
              r'|(?<![\w:/])/(?!/)(?:' + _ROOTS + r'(?![\w-])|[\w.~-]+/[\w.~-]))')  # /home, /a/b
# A chave não pode estar colada a outra palavra alfanumérica, mas '_' separa ('access_token=', 'client_secret=').
CREDENTIAL = r'(?<![A-Za-z0-9])(?:bearer\s+\S+|(?:api[_-]?key|token|secret|password)'
FILES = {'video.mp4', 'subtitles.srt', 'script.json', 'script.txt', 'quality-report.json', 'licenses.json', 'review.json'}
OPTIONAL_FILES = {'metadata.json', 'thumbnail.png', 'thumbnail.jpg'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


class ExportService:
    def __init__(self, productions):
        self.productions = productions
        self.store, self.settings = productions.store, productions.settings
        self.lock = RLock()

    def _uow(self, *, readonly: bool = False):
        if self.store is None or not hasattr(self.store, 'uow'):
            raise DatabaseUnavailable('database_unavailable')
        return self.store.uow(readonly=readonly)

    @staticmethod
    def _require_production(conn, production_id: str) -> dict:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT * FROM horus.productions WHERE id = %s', (production_id,))
            row = cur.fetchone()
        if row is None:
            raise KeyError(production_id)
        return row

    def _root(self, production_id):
        self.productions.root(production_id)  # valida o ID antes de compor caminhos
        root = self.settings.data_dir / 'exports' / production_id
        for part in (root, *root.parents):
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise WorkflowConflict('Links não são permitidos na pasta de exportação.')
        return root

    def _tokens(self):
        """Token atual e, durante uma rotação, também o novo: ambos são redigidos."""
        tokens = getattr(getattr(getattr(self, 'productions', None), 'manager', None), 'tokens', None)
        return list(dict.fromkeys([self.settings.token, *(tokens.valid_tokens() if tokens else [])]))

    def _private(self, data: bytes):
        text = data.decode('utf-8')
        # Não alterar silenciosamente o roteiro aprovado ou as evidências.
        # URLs públicas e caminhos relativos de artefatos são preservados.
        paths = re.compile(PATH_START)
        credentials = re.compile(CREDENTIAL + r'\s*[=:]\s*\S+)', re.I)
        if any(token in text for token in self._tokens()) or paths.search(text) or credentials.search(text):
            raise WorkflowConflict('O pacote contém caminho absoluto ou possível credencial em texto; revise o conteúdo antes de exportar.')

    def _audit_metadata(self, value):
        """Cópia portátil dos metadados: nunca alterar artefatos ou decisões persistidas."""
        if isinstance(value, dict):
            return {key: '[credencial omitida]' if re.fullmatch(r'(?i)(?:authorization|.*(?:api[_-]?key|token|secret|password))', key)
                    else self._audit_metadata(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._audit_metadata(item) for item in value]
        if not isinstance(value, str):
            return value
        for token in self._tokens():
            value = value.replace(token, '[credencial omitida]')
        value = re.sub(CREDENTIAL + r'''["']?\s*[=:]\s*["']?[^\s,;"']+)''', '[credencial omitida]', value, flags=re.I)
        return re.sub(PATH_START + r'''[^\r\n;"']*''', '[caminho omitido]', value)

    def _metadata(self, production_id, export_id):
        if not re.fullmatch(r'[a-f0-9]{64}', export_id):
            raise KeyError(export_id)
        root = self._root(production_id)
        archive = confined(root, f'{export_id}/package.zip')
        try:
            with ZipFile(archive) as zipped:
                manifest = json.loads(zipped.read('manifest.json'))
                if manifest['schema_version'] != 1 or not isinstance(manifest['created_at'], str):
                    raise ValueError()
                datetime.fromisoformat(manifest['created_at'])
                self._private(_json(manifest))
                if manifest['id'] != export_id or manifest['production_id'] != production_id:
                    raise ValueError()
                expected = {item['name']: item for item in manifest['files']}
                if not FILES <= set(expected) or not set(expected) <= FILES | OPTIONAL_FILES:
                    raise ValueError()
                if set(zipped.namelist()) != {'manifest.json', *expected} or len(zipped.namelist()) != len(expected) + 1:
                    raise ValueError()
                for name, item in expected.items():
                    if zipped.getinfo(name).file_size != item['size_bytes']:
                        raise ValueError()
                    with zipped.open(name) as stream:
                        if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                            raise ValueError()
            with archive.open('rb') as stream:
                sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        except (OSError, ValueError, KeyError, TypeError, BadZipFile):
            raise WorkflowConflict('Pacote de exportação ausente ou adulterado; preserve-o e use uma nova chave para gerar outro.') from None
        return {'id': export_id, 'production_id': production_id, 'created_at': manifest['created_at'],
                'relative_path': f'exports/{production_id}/{export_id}/package.zip',
                'size_bytes': archive.stat().st_size, 'sha256': sha, 'publishable': False}

    def list(self, production_id):
        with self._uow(readonly=True) as conn:
            self._require_production(conn, production_id)
        with self.lock:
            root = self._root(production_id)
            items, invalid = [], []
            for p in (sorted(root.iterdir()) if root.exists() else []):
                if re.fullmatch(r'[a-f0-9]{64}', p.name) and (p / 'package.zip').exists():
                    try:
                        items.append(self._metadata(production_id, p.name))
                    except WorkflowConflict:  # um pacote adulterado não esconde os demais
                        invalid.append({'id': p.name, 'detail': 'Pacote ausente ou adulterado; preserve-o e gere outro com nova chave.'})
            result = {'items': sorted(items, key=lambda item: (item['created_at'], item['id']))}
            if invalid:
                result['invalid'] = invalid
            return result

    def file(self, production_id, export_id):
        self.productions.require_local_data(production_id)
        with self._uow(readonly=True) as conn:
            self._require_production(conn, production_id)
        self._metadata(production_id, export_id)
        return confined(self._root(production_id), f'{export_id}/package.zip')

    def create(self, production_id, key):
        self.productions.require_local_data(production_id)
        try:
            return self._create(production_id, key)
        except WorkflowConflict:
            raise
        except DatabaseUnavailable:
            raise
        except (OSError, ValueError, TypeError, BadZipFile):
            raise WorkflowConflict('Não foi possível exportar com integridade; confira os artefatos e a pasta de exportação e use outra chave se a tentativa foi interrompida.') from None

    def _record_export_receipt(self, production_id: str, key: str, status: str, response: dict) -> None:
        moment = datetime.now(timezone.utc)
        with self._uow() as conn:
            conn.execute(
                """
                UPDATE horus.publishing_receipts
                SET status = %s, response = %s, response_canonical = %s, updated_at = %s
                WHERE production_id = %s AND scope = 'export' AND idempotency_key = %s
                """,
                (status, Jsonb(response), canonical_json_bytes(response), moment, production_id, key),
            )

    def _create(self, production_id, key):
        export_id = hashlib.sha256(f'{production_id}:{key}'.encode()).hexdigest()
        req_hash = hashlib.sha256(canonical_json_bytes({'production_id': production_id, 'key': key})).hexdigest()
        with self.lock:
            # 1) Transação curta: só leituras do banco, reserva do recibo e montagem dos textos. Nenhum hash de arquivo aqui.
            with self._uow() as conn:
                row = self._require_production(conn, production_id)
                root = self._root(production_id)
                folder = root / export_id
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT status, response
                        FROM horus.publishing_receipts
                        WHERE production_id = %s AND scope = 'export' AND idempotency_key = %s
                        FOR UPDATE
                        """,
                        (production_id, key),
                    )
                    receipt = cur.fetchone()
                if receipt is not None:
                    if receipt['status'] == 'failed':
                        reason = (_as_json(receipt['response']) or {}).get('reason') or 'falha anterior'
                        raise WorkflowConflict(f'Tentativa anterior desta chave falhou ({reason}); use uma nova chave.')
                    if receipt['status'] == 'succeeded':
                        return self._metadata(production_id, export_id), False
                    raise WorkflowConflict('Tentativa anterior desta chave ainda está pendente; use uma nova chave.')
                if folder.exists() or folder.is_symlink():
                    reason = read_failure(folder)
                    if reason:
                        raise WorkflowConflict(f'Tentativa anterior desta chave falhou ({reason}); use uma nova chave.')
                    return self._metadata(production_id, export_id), False
                review = _as_json(row['review']) or {}
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute('SELECT * FROM horus.jobs WHERE id = %s', (row['job_id'],))
                    job = self.store.job(cur.fetchone())
                if review.get('decision') != 'approve' or job['status'] != 'succeeded':
                    raise WorkflowConflict('Exportação exige produção concluída e aprovada por revisão humana.')
                artifacts = _as_json(row['artifacts']) or []
                source = self.productions.root(production_id)
                video = next((a for a in artifacts if a['kind'] == 'video' and a['sha256'] == review.get('artifact_sha256')), None)
                subtitles = next((a for a in artifacts if a['kind'] == 'subtitles'), None)
                if not video or not subtitles:
                    raise WorkflowConflict('Exportação exige vídeo aprovado e legendas registrados.')
                snapshot = approved_script(conn, row)

                media = confirmed_media(source, artifacts, production_id)
                quality = media['quality_report']
                inputs = {a['relative_path']: a['sha256'] for a in artifacts if _INPUTS.fullmatch(a['relative_path'])}
                prov_data = registered_json(source, artifacts, 'asset-provenance.json')
                prov_verdict = validate_asset_provenance(prov_data, artifacts=inputs)
                provenance = prov_verdict['value'] if prov_verdict['ok'] else None
                history = LicenseReviewService._history(conn, production_id)
                if quality is not None and provenance is not None and history:
                    try:
                        effective_for_qc = apply_license_reviews(provenance, history)
                    except ValueError:
                        effective_for_qc = provenance
                    qc_inputs = {a['relative_path']: a['sha256'] for a in artifacts if PILOT_INPUT_PATH.fullmatch(a['relative_path'])}
                    quality['checks'] = [
                        provenance_check(effective_for_qc, qc_inputs)
                        if c['id'] == 'asset_provenance' and c['status'] in {'pass', 'warn'} else c
                        for c in quality['checks']
                    ]
                    quality['status'] = derive_report_status([QualityCheck.model_validate(c) for c in quality['checks']])
                    quality['warnings'] = [c['id'] for c in quality['checks'] if c['status'] == 'warn']
                    quality['blocking_errors'] = [c['id'] for c in quality['checks'] if c['status'] == 'fail']

                effective = apply_license_reviews(provenance, history) if provenance is not None else None
                document = snapshot['document']
                text = document['title'] + '\n\n' + '\n\n'.join(
                    section['heading'] + '\n' + section['narration'] for section in document['sections']) + '\n'
                audit = {'quality-report.json': quality or {'status': 'not_run',
                               'reason': 'Relatório de QC registrado indisponível nesta produção.'},
                           'licenses.json': {'original_provenance': provenance, 'history': history,
                               'effective_provenance': effective, 'publishable': False},
                           'review.json': {'production_review': review, 'script_review': snapshot['review'],
                               'script_id': row['script_id'], 'script_version': row['script_version'],
                               'script_content_hash': row['script_content_hash'], 'publishable': False}}
                safe_audit = self._audit_metadata(audit)
                payload = {name: _json(data) for name, data in safe_audit.items()}
                payload.update({'script.json': _json(document), 'script.txt': text.encode('utf-8')})
                optional = PublishingService(self.productions).optional_files(conn, row)
                if 'metadata.json' in optional:
                    original_metadata = json.loads(optional['metadata.json'])
                    safe_metadata = self._audit_metadata(original_metadata)
                    optional['metadata.json'] = _json(safe_metadata)
                else:
                    original_metadata = safe_metadata = None
                payload.update(optional)
                for data in payload.values():
                    if not data.startswith((b'\x89PNG', b'\xff\xd8\xff')):
                        self._private(data)
                redacted = safe_audit != audit or safe_metadata != original_metadata

                moment = datetime.now(timezone.utc)
                empty = {}
                conn.execute(
                    """
                    INSERT INTO horus.publishing_receipts
                        (production_id, scope, idempotency_key, request_hash, response, response_canonical, status, created_at, updated_at)
                    VALUES (%s, 'export', %s, %s, %s, %s, 'pending', %s, %s)
                    """,
                    (production_id, key, req_hash, Jsonb(empty), canonical_json_bytes(empty), moment, moment),
                )

            # 2) Fora da transação: hash de todos os artefatos registrados e leitura das legendas.
            try:
                for item in artifacts:
                    if inspect_artifact(source, item['relative_path'], item['kind']) != item:
                        raise WorkflowConflict('Artefato adulterado; exportação recusada.')
                # Só os artefatos explicitamente selecionados entram no ZIP.
                srt = confined(source, subtitles['relative_path']).read_bytes()
                if hashlib.sha256(srt).hexdigest() != subtitles['sha256']:
                    raise WorkflowConflict('Legendas alteradas durante a exportação.')
                self._private(srt)
                payload['subtitles.srt'] = srt
                manifest = {'schema_version': 1, 'horus_version': HORUS_VERSION, 'database_schema_version': SCHEMA_VERSION,
                            'id': export_id, 'production_id': production_id, 'created_at': now(), 'publishable': False,
                            'metadata_redacted': redacted, 'files': []}
                root.mkdir(parents=True, exist_ok=True)
                folder.mkdir()  # reserva exclusiva; uma tentativa interrompida nunca é sobrescrita
            except (WorkflowConflict, OSError, ValueError, BadZipFile) as failure:
                self._record_export_receipt(production_id, key, 'failed', {'reason': failure_message(failure)})
                raise

            temporary = folder / ('partial-' + uuid.uuid4().hex)
            try:
                with ZipFile(temporary, 'x', compression=ZIP_DEFLATED) as zipped:
                    for name, data in payload.items():
                        zipped.writestr(name, data)
                        manifest['files'].append({'name': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)})
                    sha, size = hashlib.sha256(), 0
                    # MP4 já comprimido: armazenar sem nova compressão e permitir ZIP64. O hash é conferido DURANTE a
                    # cópia: o arquivo verificado é exatamente o que entra no pacote.
                    with confined(source, video['relative_path']).open('rb') as stream, zipped.open(ZipInfo('video.mp4'), 'w', force_zip64=True) as target:
                        while block := stream.read(1024 * 1024):
                            sha.update(block)
                            size += len(block)
                            target.write(block)
                    if sha.hexdigest() != video['sha256'] or size != video['size_bytes']:
                        raise WorkflowConflict('Vídeo alterado durante a exportação.')
                    manifest['files'].append({'name': 'video.mp4', 'sha256': sha.hexdigest(), 'size_bytes': size})
                    zipped.writestr('manifest.json', _json(manifest))
                temporary.rename(folder / 'package.zip')
            except (WorkflowConflict, OSError, ValueError, BadZipFile) as failure:
                reason = failure_message(failure)
                mark_failed(folder, reason)  # a chave fica registrada como falha; nova chave funciona
                self._record_export_receipt(production_id, key, 'failed', {'reason': reason})
                raise
            finally:
                if temporary.exists():
                    temporary.unlink()  # apenas o temporário criado por esta chamada
            meta = self._metadata(production_id, export_id)
            self._record_export_receipt(production_id, key, 'succeeded', meta)
            return meta, True
