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

from pydantic import BaseModel, ConfigDict, Field

from .license_reviews import LicenseReviewService
from .media_contracts import apply_license_reviews
from .local_pilot import registered_json
from .production import confined, inspect_artifact
from .storage import now
from .workflows import WorkflowConflict, digest


class ThumbnailRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    timecode_seconds: float = Field(ge=0, allow_inf_nan=False)


class MetadataRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=5000)


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

    def _approved(self, db, production_id):
        row = self.productions.require(db, production_id)
        review = json.loads(row['review']) if row['review'] else {}
        job = self.store.job(db.execute('SELECT * FROM jobs WHERE id=?', (row['job_id'],)).fetchone())
        if review.get('decision') != 'approve' or job['status'] != 'succeeded':
            raise WorkflowConflict('Produção concluída e aprovada é necessária para preparar a publicação manual.')
        video = next((a for a in json.loads(row['artifacts']) if a['kind'] == 'video'
                      and a['sha256'] == review.get('artifact_sha256')), None)
        if not video or inspect_artifact(self.productions.root(production_id), video['relative_path'], 'video') != video:
            raise WorkflowConflict('Vídeo aprovado ausente ou adulterado.')
        return row, video

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
                row, video = self._approved(db, production_id)
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
            if (root / relative).exists():
                raise WorkflowConflict('Tentativa anterior de thumbnail presente; preserve-a e use outra chave.')
            folder = self._new_folder(root, relative)
            png, jpg = folder / 'thumbnail.png', folder / 'thumbnail.jpg'
            self._run([str(ffmpeg), '-nostdin', '-n', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-ss', str(request.timecode_seconds), '-i', str(path), '-map', '0:v:0', '-frames:v', '1', '-an',
                '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2', str(png)], 15)
            # Converte o mesmo quadro PNG, sem procurar outro frame do vídeo.
            confined(root, relative + '/thumbnail.png')
            self._run([str(ffmpeg), '-nostdin', '-n', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-i', str(png), '-frames:v', '1', '-q:v', '2', str(jpg)], 10)
            artifacts = [inspect_artifact(root, relative + '/thumbnail.' + suffix, 'thumbnail') for suffix in ('png', 'jpg')]
            with png.open('rb') as stream:
                header = stream.read(24)
            if len(header) != 24 or struct.unpack('>II', header[16:24]) != (1080, 1920):
                raise WorkflowConflict('Thumbnail não tem as dimensões esperadas de 1080×1920.')
            saved = {'timecode_seconds': request.timecode_seconds, 'video_sha256': video['sha256'],
                     'width': 1080, 'height': 1920, 'artifacts': artifacts, 'created_at': now(), 'publishable': False}
            (folder / 'receipt.json').write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                fresh, fresh_video = self._approved(db, production_id)
                if fresh_video != video:
                    raise WorkflowConflict('Vídeo aprovado mudou durante a extração.')
                self._append(db, fresh, [*artifacts, inspect_artifact(root, receipt_path, 'manifest')])
            return saved, True

    def _metadata(self, db, row, edit=None):
        document = approved_script(db, row)['document']
        licenses = LicenseReviewService(self.store, self.settings.data_dir)
        provenance = licenses._provenance(db, row['id'])
        effective = apply_license_reviews(provenance, licenses._history(db, row['id'])) if provenance is not None else None
        pending = effective is None or any(a['status'] != 'verified' for a in effective['assets'])
        result = self.store.job(db.execute('SELECT * FROM jobs WHERE id=?', (row['job_id'],)).fetchone())['result'] or {}
        narration = result.get('narration_disclosure') or 'Narração sintética/voz local: confira a voz selecionada e seus termos de uso.'
        disclosures = [narration,
                       'Licenças pendentes: confira os direitos de todos os assets antes de publicar.' if pending else
                       'Licenças revisadas: a publicação manual continua sob responsabilidade humana.',
                       'publishable=false: este rascunho não autoriza publicação automática.']
        footer = '\n\nDivulgações obrigatórias:\n' + '\n'.join(disclosures)
        if edit is None:
            artifacts = json.loads(row['artifacts'])
            latest = next((a for a in reversed(artifacts) if re.fullmatch(r'publishing/metadata/[a-f0-9]{32}\.json', a['relative_path'])), None)
            if latest:
                saved = registered_json(self.productions.root(row['id']), artifacts, latest['relative_path'])
                if saved is None:
                    raise WorkflowConflict('Metadados registrados adulterados.')
                edit = MetadataRequest(title=saved['title'], description=saved['description'].split('\n\nDivulgações obrigatórias:\n')[0])
        cap = min(600, max(0, (5000 - len(footer) - 64 * len(document['sections'])) // len(document['sections'])))
        title = edit.title if edit else document['title'][:100]
        body = edit.description.split('\n\nDivulgações obrigatórias:\n')[0] if edit else '\n\n'.join(
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
        with self.store.connect() as db:
            row, _ = self._approved(db, production_id)
            return self._metadata(db, row)

    def edit_metadata(self, production_id, request):
        with self.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row, _ = self._approved(db, production_id)
            value = self._metadata(db, row, request)
            root = self.productions.root(production_id)
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
                data = confined(root, item['relative_path']).read_bytes()
                if hashlib.sha256(data).hexdigest() != item['sha256']:
                    raise WorkflowConflict('Thumbnail alterada durante a exportação.')
                payload['thumbnail.' + item['relative_path'].rsplit('.', 1)[1]] = data
        return payload
