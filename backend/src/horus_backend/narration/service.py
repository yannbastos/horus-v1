from __future__ import annotations
import json
import tempfile
import threading
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
import keyring
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from starlette.background import BackgroundTask
from ..storage import DatabaseUnavailable
from .providers import WindowsSapi, Piper, ElevenLabs, NarrationError

from .kokoro import Kokoro

DEFAULT_TEXT = 'Esta é uma frase fictícia para testar a voz de narração.'


class Credentials:
    def __init__(self, workspace):
        import hashlib
        self.account = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    def get(self):
        try: return keyring.get_password('horus.narration.elevenlabs', self.account)
        except Exception: raise NarrationError('credential_store_unavailable') from None
    def put(self, key):
        try: keyring.set_password('horus.narration.elevenlabs', self.account, key)
        except Exception: raise NarrationError('credential_store_unavailable') from None
    def delete(self):
        try:
            if self.get() is not None: keyring.delete_password('horus.narration.elevenlabs', self.account)
        except Exception: raise NarrationError('credential_store_unavailable') from None


class EstimateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: str = Field(max_length=80)
    voice_id: str = Field(max_length=1000)
    model_id: str | None = Field(default=None, max_length=100)
    text: str | None = Field(default=None, max_length=1000000)
    script_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    script_version: int | None = Field(default=None, ge=1)
    @model_validator(mode='after')
    def source(self):
        if (self.text is None) == (self.script_id is None): raise ValueError('Escolha texto ou roteiro.')
        if self.script_id is not None and self.script_version is None: raise ValueError('Versão necessária.')
        if self.text is not None and self.script_version is not None: raise ValueError('Versão sem roteiro.')
        return self


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: str = Field(max_length=80)
    voice_id: str = Field(max_length=1000)
    text: str = Field(default=DEFAULT_TEXT, min_length=1, max_length=300)


class Preference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: str = Field(max_length=80)
    voice_id: str = Field(max_length=1000)


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: SecretStr = Field(min_length=1, max_length=4096)


def provider_registry(settings, checkpoints=None):
    credentials = Credentials(settings.data_dir)
    return {p.id: p for p in (WindowsSapi(), Piper(settings.piper_executable, settings.piper_models),
                              Kokoro(getattr(settings, "kokoro_python", None), getattr(settings, "kokoro_model_dir", None)),
                              ElevenLabs(settings, credentials, checkpoints=checkpoints))}


PREF_KEY_NARRATION = "narration.preference"


class NarrationService:
    def __init__(self, settings, store, *, device_id: str = "default"):
        self.settings, self.store = settings, store
        self.device_id = device_id
        self.credentials = Credentials(settings.data_dir)
        from ..human_checkpoints import CheckpointService
        self.providers = provider_registry(settings, CheckpointService(store, settings=settings))
        self.previews = {}
        self.lock = threading.Lock()
        self.active_previews = 0

    def _uow(self, *, readonly: bool = False):
        if self.store is None or not hasattr(self.store, "uow"):
            raise DatabaseUnavailable("database_unavailable")
        return self.store.uow(readonly=readonly)

    def provider(self, ident):
        if ident not in self.providers: raise NarrationError('provider_not_found', 404)
        return self.providers[ident]

    @staticmethod
    def _approved_document(conn, script_id: str, version: int) -> dict:
        from ..workflows import WorkflowConflict, digest
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM horus.scripts WHERE id = %s", (script_id,))
            script = cur.fetchone()
            if script is None:
                raise KeyError(script_id)
            cur.execute("SELECT MAX(version) AS latest FROM horus.scripts WHERE workflow_id = %s", (script["workflow_id"],))
            latest = cur.fetchone()["latest"]
            review = script["review"] if isinstance(script["review"], dict) else (json.loads(script["review"]) if script["review"] else {})
            if script["status"] != "approved" or script["version"] != version or version != latest or review.get("decision") != "approve":
                raise WorkflowConflict("Roteiro mais recente e aprovação humana persistida são necessários.")
            document = script["document"] if isinstance(script["document"], dict) else json.loads(script["document"])
            doc_hash = digest(document)
            cur.execute("SELECT response FROM horus.script_receipts WHERE scope = %s", (f"review:{script_id}",))
            for row in cur.fetchall():
                approved = row["response"] if isinstance(row["response"], dict) else json.loads(row["response"])
                if (
                    approved.get("status") == "approved"
                    and approved.get("version") == version
                    and approved.get("review") == review
                    and digest(approved.get("document")) == doc_hash
                ):
                    return document
        raise WorkflowConflict("Aprovação do roteiro indisponível para o documento atual.")

    def estimate(self, request):
        provider = self.provider(request.provider)
        text = request.text
        if text is None:
            from ..workflows import WorkflowConflict
            try:
                with self._uow(readonly=True) as conn:
                    document = self._approved_document(conn, request.script_id, request.script_version)
                text = '\n'.join(section['narration'] for section in document['sections'])
            except KeyError: raise NarrationError('script_not_found', 404) from None
            except (WorkflowConflict, ValueError): raise NarrationError('approved_script_required', 409) from None
        return provider.estimate(text)

    def get_preference(self) -> dict:
        with self._uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT value FROM horus.preferences WHERE device_id = %s AND key = %s",
                    (self.device_id, PREF_KEY_NARRATION),
                )
                row = cur.fetchone()
        if row is None:
            return {'provider': None, 'voice_id': None}
        try:
            return Preference.model_validate(row["value"]).model_dump()
        except ValueError:
            return {'provider': None, 'voice_id': None}

    def save_preference(self, request):
        provider = self.provider(request.provider)
        if request.voice_id not in {v['id'] for v in provider.voices()}: raise NarrationError('voice_not_found', 422)
        data = request.model_dump()
        moment = datetime.now(timezone.utc)
        with self._uow() as conn:
            conn.execute(
                """
                INSERT INTO horus.preferences (device_id, key, value, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (device_id, key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                """,
                (self.device_id, PREF_KEY_NARRATION, Jsonb(data), moment),
            )
        return data

    def remove_preview(self, ident):
        with self.lock: item = self.previews.pop(ident, None)
        if item: item[0].cleanup()

    def close(self):
        for ident in list(self.previews): self.remove_preview(ident)

    def preview(self, request):
        with self.lock: self.active_previews += 1
        try:
            return self._preview(request)
        finally:
            with self.lock: self.active_previews -= 1

    def _preview(self, request):
        provider = self.provider(request.provider)
        if provider.kind == 'paid_api': raise NarrationError('paid_provider_not_authorized', 403)
        temp = tempfile.TemporaryDirectory(prefix='horus-narration-')
        output = Path(temp.name) / 'preview.wav'
        try:
            provider.preview(request.text, request.voice_id, output)
            if output.is_symlink() or not output.is_file() or output.stat().st_size > 20_000_000:
                raise NarrationError('invalid_preview')
            with wave.open(str(output), 'rb') as wav:
                if wav.getnframes() <= 0: raise NarrationError('invalid_preview')
            ident = uuid.uuid4().hex
            with self.lock: self.previews[ident] = (temp, output, time.monotonic() + 300)
            timer = threading.Timer(300, self.remove_preview, args=(ident,)); timer.daemon = True; timer.start()
            return {'id': ident, 'url': f'/v1/narration/previews/{ident}', 'expires_in_seconds': 300}
        except NarrationError:
            temp.cleanup(); raise
        except Exception:
            temp.cleanup(); raise NarrationError('invalid_preview') from None


def narration_router(service):
    router = APIRouter(prefix='/v1/narration')
    def call(action):
        try: return action()
        except DatabaseUnavailable as error:
            return JSONResponse({'detail': {'code': error.code, 'message': 'Banco de dados indisponível.'}}, status_code=error.http_status)
        except NarrationError as error:
            if error.code == 'paid_provider_not_authorized':
                return JSONResponse({'code': error.code}, status_code=error.status)
            return JSONResponse({'code': error.code, 'message': error.message, 'failure': error.detail}, status_code=error.status)
    @router.get('/providers')
    def providers(): return call(lambda: [p.status() for p in service.providers.values()])
    @router.post('/estimate')
    def estimate(request: EstimateRequest): return call(lambda: service.estimate(request))
    @router.post('/preview')
    def preview(request: PreviewRequest): return call(lambda: service.preview(request))
    @router.put('/providers/elevenlabs/credential')
    def credential(request: CredentialRequest):
        def save():
            service.credentials.put(request.key.get_secret_value()); return {'configured': True}
        return call(save)
    @router.delete('/providers/elevenlabs/credential')
    def delete_credential():
        def delete():
            service.credentials.delete(); return {'configured': False}
        return call(delete)
    @router.get('/preference')
    def preference():
        return call(service.get_preference)
    @router.put('/preference')
    def save_preference(request: Preference): return call(lambda: service.save_preference(request))
    @router.get('/previews/{ident}')
    def audio(ident: str):
        with service.lock: item = service.previews.pop(ident, None)
        if not item or item[2] < time.monotonic():
            if item: item[0].cleanup()
            return JSONResponse({'code': 'preview_not_found'}, status_code=404)
        return FileResponse(item[1], media_type='audio/wav', background=BackgroundTask(item[0].cleanup))
    return router
