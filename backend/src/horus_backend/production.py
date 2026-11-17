"""Produção técnica M4 no JobManager existente; aprovação final permanece humana."""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import uuid
import time
from typing import Literal

from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .production_contract import ProductionPlan, build_production_plan
from .script_contract import ScriptDocument
from .scripts import ScriptService
from .storage import DatabaseUnavailable, LeaseLostError
from .workflows import WorkflowConflict, digest, encode
from .processes import OwnedProcess, failure_detail, sanitized_tail


class NarrationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=80)
    voice_id: str = Field(min_length=1, max_length=1000)


class ProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narration: NarrationSelection | None = None
    expected_version: int = Field(ge=1, strict=True)
    mode: Literal["simulated", "local_demo", "local_pilot"]
    render_quality: Literal["draft", "final"] = "draft"

    @model_validator(mode="after")
    def quality_mode(self):
        if self.render_quality == "final" and self.mode != "local_pilot":
            raise ValueError("render final requer local_pilot")
        if self.narration is not None and self.mode != "local_pilot":
            raise ValueError("narração requer local_pilot")
        return self


class ProductionReviewNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at_seconds: float = Field(ge=0, allow_inf_nan=False, strict=True)
    text: str = Field(min_length=1, max_length=2000, strict=True)


class ProductionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    notes: list[ProductionReviewNote] = Field(default_factory=list, max_length=200)
    acknowledge_quality_errors: bool = Field(default=False, strict=True)


def confined(root: Path, relative: str) -> Path:
    path = Path(relative)
    if not relative or path.is_absolute() or "\\" in relative or ":" in relative or any(p in {".", ".."} for p in relative.split("/")):
        raise WorkflowConflict("Caminho de artefato inválido.")
    target = root / path
    for part in [root, *target.parents, target]:
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise WorkflowConflict("Links não são permitidos em artefatos.")
    if not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
        raise WorkflowConflict("Artefato ausente ou fora da produção.")
    return target


def inspect_artifact(root, relative, kind):
    path = confined(root, relative)
    suffixes = {"plan": {".json"}, "scene": {".png"}, "audio": {".wav"}, "subtitles": {".srt"},
                "video": {".mp4"}, "manifest": {".json"}, "font": {".ttf", ".otf"}, "thumbnail": {".png", ".jpg"}}
    if kind not in suffixes or path.suffix not in suffixes[kind] or path.stat().st_size == 0:
        raise WorkflowConflict("Tipo ou conteúdo de artefato inválido.")
    with path.open("rb") as stream:
        data = stream.read(16)
        stream.seek(0)
        content_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    if kind in {"plan", "manifest"}:
        json.loads(path.read_bytes())
    elif kind == "scene" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise WorkflowConflict("Imagem inválida.")
    elif kind == 'thumbnail' and not (data.startswith(b'\x89PNG\r\n\x1a\n') if path.suffix == '.png' else data.startswith(b'\xff\xd8\xff')):
        raise WorkflowConflict('Thumbnail inválida.')
    elif kind == "audio" and not (data.startswith(b"RIFF") and data[8:12] == b"WAVE"):
        raise WorkflowConflict("Áudio inválido.")
    elif kind == "subtitles" and " --> " not in path.read_text(encoding="utf-8"):
        raise WorkflowConflict("Legenda inválida.")
    elif kind == "video" and data[4:8] != b"ftyp":
        raise WorkflowConflict("Vídeo inválido.")
    return {"id": hashlib.sha256(f"{kind}:{relative}".encode()).hexdigest()[:32], "kind": kind,
            "relative_path": relative, "size_bytes": path.stat().st_size, "sha256": content_hash}


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _json_val(value):
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value


class ProductionService:
    def __init__(self, manager):
        self.manager, self.store, self.settings = manager, manager.store, manager.settings

    def _uow(self, *, readonly: bool = False):
        if self.store is None or not hasattr(self.store, "uow"):
            raise DatabaseUnavailable("database_unavailable")
        return self.store.uow(readonly=readonly)

    def root(self, production_id):
        if not re.fullmatch(r"[a-f0-9]{32}", production_id):
            raise KeyError(production_id)
        return self.settings.data_dir / "productions" / production_id

    @staticmethod
    def require(cur, production_id, *, for_update: bool = False):
        sql = "SELECT * FROM horus.productions WHERE id = %s" + (" FOR UPDATE" if for_update else "")
        row = cur.execute(sql, (production_id,)).fetchone()
        if row is None:
            raise KeyError(production_id)
        return row

    def record(self, conn, row, *, job_row=None, history=None):
        if job_row is None:
            with conn.cursor(row_factory=dict_row) as cur:
                job_row = cur.execute("SELECT * FROM horus.jobs WHERE id = %s", (row["job_id"],)).fetchone()
        job = self.store.job(job_row)
        result = job["result"] or {}
        # A coluna é o plano persistido na submissão; não depende do worker nem
        # lê plan.json do disco. Não aplicar defaults atuais a registros antigos.
        try:
            saved_plan = _json_val(row["plan"])
        except (ValueError, TypeError):
            saved_plan = {}
        if not isinstance(saved_plan, dict):
            saved_plan = {}
        render_quality = saved_plan.get('render_quality')
        if render_quality not in ('draft', 'final'):
            render_quality = None
        narration = None
        audio = saved_plan.get('audio')
        if isinstance(audio, dict):
            values = {key: audio.get(key) for key in ('provider', 'voice_id')}
            if all(isinstance(value, str) and 0 < len(value) <= limit
                   and not PurePosixPath(value).is_absolute() and not PureWindowsPath(value).is_absolute()
                   for value, limit in ((values['provider'], 80), (values['voice_id'], 1000))):
                narration = values
        artifacts_val = _json_val(row["artifacts"]) or []
        from .local_pilot import confirmed_media
        media = confirmed_media(self.root(row['id']), artifacts_val, row['id'])
        quality = media['quality_report']
        if quality is not None and media['asset_provenance'] is not None:
            from .license_reviews import LicenseReviewService
            from .av_quality import provenance_check
            from .media_contracts import apply_license_reviews, derive_report_status, QualityCheck, PILOT_INPUT_PATH
            if history is None:
                history = LicenseReviewService._history(conn, row['id'])
            if history:
                try:
                    effective = apply_license_reviews(media['asset_provenance'], history)
                except ValueError:
                    # Histórico incompatível com o documento atual não libera licenças.
                    effective = media['asset_provenance']
                inputs = {a['relative_path']: a['sha256'] for a in artifacts_val
                          if PILOT_INPUT_PATH.fullmatch(a['relative_path'])}
                # Preserva falhas de integridade já medidas; só atualiza o diagnóstico de licença.
                quality['checks'] = [provenance_check(effective, inputs)
                    if c['id'] == 'asset_provenance' and c['status'] in {'pass', 'warn'} else c
                    for c in quality['checks']]
                quality['status'] = derive_report_status([QualityCheck.model_validate(c) for c in quality['checks']])
                quality['warnings'] = [c['id'] for c in quality['checks'] if c['status'] == 'warn']
                quality['blocking_errors'] = [c['id'] for c in quality['checks'] if c['status'] == 'fail']
        acknowledgement_required = row['mode'] == 'local_pilot' and (
            quality is None or quality['status'] == 'not_run' or bool(quality['blocking_errors']))
        review_val = _json_val(row["review"]) if row.get("review") is not None else None
        return {
            "id": row["id"],
            "script_id": row["script_id"],
            "script_version": row["script_version"],
            "script_content_hash": row["script_content_hash"],
            "job_id": row["job_id"],
            "mode": row["mode"],
            "created_at": _iso(row["created_at"]),
            "updated_at": max(job["updated_at"], (review_val or {}).get("reviewed_at", "")) if review_val else job["updated_at"],
            "stage": result.get("stage", "prepare"),
            "status": job["status"],
            "artifacts": artifacts_val,
            "review": review_val,
            "error": job["error"],
            "failure": result.get('failure'),
            "demo_only": True,
            "real_voice": False,
            **media,
            "quality_acknowledgement_required": acknowledgement_required,
            "render_quality": render_quality,
            "narration": narration,
            "local_data": self.manager.deletion_audit.local_data("production", row["id"]),
            "publishable": False,
            "narration_kind": result.get("narration_kind"),
            "narration_disclosure": result.get("narration_disclosure"),
            "alignment_method": result.get("alignment_method"),
        }

    def get(self, production_id):
        with self._uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                return self.record(conn, self.require(cur, production_id))

    def list(self, script_id):
        with self._uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                ScriptService._require(cur, script_id)
                rows = cur.execute(
                    "SELECT * FROM horus.productions WHERE script_id = %s ORDER BY created_at DESC",
                    (script_id,),
                ).fetchall()
                if not rows:
                    return {"items": []}
                job_ids = [r["job_id"] for r in rows]
                prod_ids = [r["id"] for r in rows]
                jobs_by_id = {
                    j["id"]: j
                    for j in cur.execute("SELECT * FROM horus.jobs WHERE id = ANY(%s)", (job_ids,)).fetchall()
                }
                from .license_reviews import _row as _license_row
                hist_by_prod: dict[str, list[dict]] = {pid: [] for pid in prod_ids}
                for lr in cur.execute(
                    "SELECT * FROM horus.license_reviews WHERE production_id = ANY(%s) ORDER BY seq",
                    (prod_ids,),
                ).fetchall():
                    hist_by_prod[lr["production_id"]].append(_license_row(lr))
                return {
                    "items": [
                        self.record(conn, row, job_row=jobs_by_id.get(row["job_id"]), history=hist_by_prod[row["id"]])
                        for row in rows
                    ]
                }

    @staticmethod
    def approved(conn, script_id, version):
        with conn.cursor(row_factory=dict_row) as cur:
            script = ScriptService._require(cur, script_id)
            latest = cur.execute(
                "SELECT MAX(version) AS latest FROM horus.scripts WHERE workflow_id = %s",
                (script["workflow_id"],),
            ).fetchone()["latest"]
            review = _json_val(script["review"]) if script.get("review") is not None else {}
            if script["status"] != "approved" or script["version"] != version or version != latest or review.get("decision") != "approve":
                raise WorkflowConflict("Roteiro mais recente e aprovação humana persistida são necessários.")
            document = _json_val(script["document"])
            # O recibo M3 contém o documento exato devolvido na aprovação. Assim uma
            # edição posterior no banco não recebe uma aprovação antiga por acidente.
            approvals = cur.execute(
                "SELECT response FROM horus.script_receipts WHERE scope = %s",
                (f"review:{script_id}",),
            ).fetchall()
        matched = False
        for receipt in approvals:
            approved = _json_val(receipt["response"])
            if (approved.get("id") == script_id and approved.get("version") == version
                    and approved.get("status") == "approved" and approved.get("review") == review
                    and digest(approved.get("document")) == digest(document)):
                matched = True
                break
        if not matched:
            raise WorkflowConflict("Documento não corresponde ao snapshot da aprovação M3 persistida.")
        ScriptDocument.model_validate(document)
        return script, document

    async def create(self, script_id, request, key):
        body = request.model_dump()
        selection = None
        default_selection = False
        if request.mode == 'local_pilot':
            if request.narration:
                selection = request.narration.model_dump()
            else:
                pref_row = None
                with self._uow(readonly=True) as conn:
                    with conn.cursor(row_factory=dict_row) as cur:
                        pref_row = cur.execute(
                            "SELECT value FROM horus.preferences WHERE device_id = %s AND key = 'narration.preference'",
                            ("default",),
                        ).fetchone()
                if pref_row is not None:
                    try:
                        selection = NarrationSelection.model_validate(_json_val(pref_row["value"])).model_dump()
                    except ValueError:
                        raise WorkflowConflict('Preferência de narração inválida; revise Ajustes.') from None
                else:
                    default_selection = True
                    from .narration.providers import WindowsSapi, NarrationError
                    try:
                        voices = await asyncio.to_thread(WindowsSapi().voices)
                    except NarrationError:
                        voices = []
                    voice = next((v['id'] for v in voices if v['language'].lower() == 'pt-br'), 'default_pt_BR')
                    selection = {'provider': 'windows_sapi', 'voice_id': voice}
            body['narration'] = selection
        else:
            body.pop('narration')
        if request.render_quality == "draft":
            body.pop("render_quality")  # preserva recibos anteriores ao preset explicito
        scope, encoded = f"create:{script_id}", encode(body)
        with self._uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(16420, hashtext(%s))", (script_id,))
                receipt = cur.execute(
                    "SELECT * FROM horus.production_receipts WHERE scope = %s AND idempotency_key = %s",
                    (scope, key),
                ).fetchone()
                if receipt:
                    previous = _json_val(receipt['request'])
                    compared = body
                    # Recibos anteriores à seleção continuam recuperáveis pelo pedido padrão.
                    if default_selection and 'narration' not in previous:
                        compared = {k: v for k, v in body.items() if k != 'narration'}
                    if bytes(receipt["request_canonical"]) != encode(compared).encode("utf-8"):
                        raise WorkflowConflict("Chave de idempotência usada com outro pedido.")
                    return self.record(conn, self.require(cur, receipt["production_id"])), False
                script, document = self.approved(conn, script_id, request.expected_version)
                content_hash = digest(document)
                plan = build_production_plan(ScriptDocument.model_validate(document), script_id=script_id,
                    script_version=script["version"], script_content_hash=content_hash, mode=request.mode, render_quality=request.render_quality, narration=selection)
                production_id, job_id = uuid.uuid4().hex, uuid.uuid4().hex
                timestamp = datetime.now(timezone.utc)
                payload = encode({"operation": "production.run", "production_id": production_id})
                cur.execute(
                    """INSERT INTO horus.jobs(id, idempotency_key, payload, payload_canonical, status, created_at, updated_at)
                       VALUES(%s, %s, %s::jsonb, %s, 'queued', %s, %s)""",
                    (job_id, f"production:{production_id}", payload, payload.encode("utf-8"), timestamp, timestamp),
                )
                plan_encoded = encode(plan.model_dump(mode="json"))
                script_review_canonical = bytes(script["review_canonical"]) if script.get("review_canonical") is not None else encode(_json_val(script["review"])).encode("utf-8")
                cur.execute(
                    """INSERT INTO horus.productions(
                        id, script_id, script_version, script_content_hash, script_review, script_review_canonical,
                        job_id, mode, plan, plan_canonical, created_at
                    ) VALUES(%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s, %s)""",
                    (
                        production_id, script_id, script["version"], content_hash,
                        encode(_json_val(script["review"])), script_review_canonical,
                        job_id, request.mode, plan_encoded, plan_encoded.encode("utf-8"), timestamp,
                    ),
                )
                cur.execute(
                    """INSERT INTO horus.production_receipts(scope, idempotency_key, request, production_id, request_canonical)
                       VALUES(%s, %s, %s::jsonb, %s, %s)""",
                    (scope, key, encoded, production_id, encoded.encode("utf-8")),
                )
                record = self.record(conn, self.require(cur, production_id))
        self.manager.queue.put_nowait(job_id)
        await self.manager.emit(job_id, "state", "queued")
        return record, True

    def require_local_data(self, production_id):
        """Dados locais excluídos (quarentena) nunca são oferecidos: prévia, exports e artefatos recusam com 409."""
        if self.manager.deletion_audit.state("production", production_id):
            raise WorkflowConflict("Os dados locais desta produção foram excluídos; restaure da quarentena para usá-los.")

    def artifact(self, production_id, artifact_id):
        self.require_local_data(production_id)
        record = self.get(production_id)
        artifact = next((a for a in record["artifacts"] if a["id"] == artifact_id), None)
        if not artifact:
            raise KeyError(artifact_id)
        actual = inspect_artifact(self.root(production_id), artifact["relative_path"], artifact["kind"])
        if actual != artifact:
            raise WorkflowConflict("Integridade do artefato alterada; revisão necessária.")
        return confined(self.root(production_id), artifact["relative_path"])

    def review(self, production_id, request, key):
        self.require_local_data(production_id)
        body = request.model_dump()
        if not request.notes:
            body.pop("notes")  # Preserva recibos anteriores ao campo opcional.
        if not request.acknowledge_quality_errors:
            body.pop('acknowledge_quality_errors')
        encoded, scope = encode(body), f"review:{production_id}"
        encoded_bytes = encoded.encode("utf-8")
        with self._uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = self.require(cur, production_id, for_update=True)
                receipt = cur.execute(
                    "SELECT * FROM horus.production_receipts WHERE scope = %s AND idempotency_key = %s",
                    (scope, key),
                ).fetchone()
                if receipt:
                    if bytes(receipt["request_canonical"]) != encoded_bytes:
                        raise WorkflowConflict("Chave de revisão usada com outra decisão.")
                    return self.record(conn, row)
                record = self.record(conn, row)
                if (request.decision == 'approve' and record['quality_acknowledgement_required']
                        and not request.acknowledge_quality_errors):
                    raise WorkflowConflict('QC reprovado ou indisponível exige confirmação explícita dos problemas de qualidade.')
                if record["status"] != "succeeded" or row["mode"] not in {"local_demo", "local_pilot"} or row["review"] is not None:
                    raise WorkflowConflict("Revisão exige vídeo real concluído e ainda não revisado.")
                video = next((a for a in record["artifacts"] if a["kind"] == "video" and a["sha256"] == request.artifact_sha256), None)
                if video is None:
                    raise WorkflowConflict("Hash não corresponde ao vídeo registrado.")
                actual = inspect_artifact(self.root(production_id), video["relative_path"], "video")
                if actual != video:
                    raise WorkflowConflict("Vídeo alterado após render.")
                review = request.model_dump() | {"reviewed_at": datetime.now(timezone.utc).isoformat()}
                review_encoded = encode(review)
                updated = cur.execute(
                    "UPDATE horus.productions SET review = %s::jsonb, review_canonical = %s WHERE id = %s RETURNING *",
                    (review_encoded, review_encoded.encode("utf-8"), production_id),
                ).fetchone()
                cur.execute(
                    """INSERT INTO horus.production_receipts(scope, idempotency_key, request, production_id, request_canonical)
                       VALUES(%s, %s, %s::jsonb, %s, %s)""",
                    (scope, key, encoded, production_id, encoded_bytes),
                )
                return self.record(conn, updated)

    async def run(self, job):
        manager, job_id = self.manager, job["id"]
        manager.active_id = job_id
        started = time.monotonic()
        lease_lost = False
        fencing = manager.worker_lock.fencing_token if manager.worker_lock.active else None
        instance_id = manager.worker_lock.instance_id if manager.worker_lock.active else None
        if manager.worker_lock.active:
            if fencing is None:
                manager.active_id = None
                return
            claimed = self.store.claim_job(
                job_id, instance_id, fencing, ttl_seconds=manager.worker_lock.ttl_seconds
            )
            if claimed is None:
                manager.active_id = None
                return
            manager.claimed_id = job_id

        def fail(status, message, detail):
            current = self.store.get_job(job_id)['result'] or {}
            self.store.update(
                job_id,
                status,
                result={**current, 'failure': detail},
                error=message,
                worker_instance_id=instance_id,
                fencing_token=fencing,
            )

        try:
            production_id = job["payload"]["production_id"]
            with self._uow(readonly=True) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    row = self.require(cur, production_id)
                script, document = self.approved(conn, row["script_id"], row["script_version"])
                script_rev_can = bytes(script["review_canonical"] or b"")
                prod_rev_can = bytes(row["script_review_canonical"])
                if digest(document) != row["script_content_hash"] or script_rev_can != prod_rev_can:
                    raise WorkflowConflict("Roteiro/aprovação mudou após submissão.")
                plan_text = bytes(row["plan_canonical"]).decode("utf-8")
                saved = json.loads(plan_text)
                expected_plan = build_production_plan(ScriptDocument.model_validate(document), script_id=row["script_id"],
                    script_version=row["script_version"], script_content_hash=row["script_content_hash"], mode=row["mode"],
                    render_quality=saved.get("render_quality", "draft"),
                    narration={k: saved['audio'][k] for k in ('provider', 'voice_id') if saved['audio'].get(k) is not None})
                if digest(ProductionPlan.model_validate_json(plan_text).model_dump(mode="json")) != digest(expected_plan.model_dump(mode="json")):
                    raise WorkflowConflict("Plano não corresponde ao roteiro aprovado.")
            from .worker_runtime import production_worker_available, worker_command
            if not production_worker_available():
                raise WorkflowConflict("Worker de produção instalado indisponível; repare o aplicativo e confira o diagnóstico.")
            root = self.root(production_id)
            # Nenhum retry escreve sobre arquivos de tentativa anterior.
            if root.exists():
                raise WorkflowConflict("Saída de tentativa anterior presente. Concilie os artefatos e crie nova produção explícita.")
            root.mkdir(parents=True)
            if root.resolve() != root.absolute() or any(p.is_symlink() or p.is_junction() for p in [root, *root.parents]):
                raise WorkflowConflict("Diretório de produção não pode conter links.")
            (root / "plan.json").write_text(plan_text, encoding="utf-8")
            self.store.update(
                job_id,
                "running",
                new_attempt=True,
                result={"stage": "prepare"},
                worker_instance_id=instance_id,
                fencing_token=fencing,
            )
            await manager.emit(job_id, "state", "running")
            command = worker_command("production-worker", root,
                development=[str(Path(__file__).with_name("production_worker.py"))])
            if row["mode"] in {"local_demo", "local_pilot"} and self.settings.factory_root:
                command += ["--factory-source", str(self.settings.factory_root)]
            # Ambiente mínimo: nenhum token, .env, perfil ou credencial de operador.
            env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
            from .bundled_runtime import tool_path
            env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
            env['PATH'] = tool_path(env.get('PATH', ''))
            if row["mode"] == "local_pilot":
                env["HORUS_ALIGNMENT_MODEL"] = str(self.settings.alignment_models_dir.resolve())
            if row["mode"] == "local_pilot" and self.settings.ffmpeg:
                env["HORUS_FFMPEG"] = str(self.settings.ffmpeg.resolve())
            if row["mode"] == "local_pilot" and self.settings.subtitle_font:
                env["HORUS_SUBTITLE_FONT"] = str(self.settings.subtitle_font.resolve())
            if row["mode"] == "local_pilot":
                for name, value in (("HORUS_PIPER_EXECUTABLE", self.settings.piper_executable),
                                    ("HORUS_PIPER_MODELS", self.settings.piper_models),
                                    ("HORUS_KOKORO_PYTHON", self.settings.kokoro_python),
                                    ("HORUS_KOKORO_MODEL_DIR", self.settings.kokoro_model_dir)):
                    if value: env[name] = str(value.resolve())
            manager.active_process = await OwnedProcess.start(command, env=env, cwd=root)
            process = manager.active_process.process
            if self.store.get_job(job_id)["status"] == "cancel_requested":
                await manager.active_process.stop()
            async def consume(reader):
                while raw := await reader.readline():
                    try:
                        event = json.loads(raw)
                        stage = event.get("stage")
                        if stage in {"prepare", "narration", "scenes", "align", "subtitles", "render", "quality", "review"}:
                            current = self.store.get_job(job_id)
                            self.store.update(
                                job_id,
                                current["status"],
                                result={"stage": stage},
                                worker_instance_id=instance_id,
                                fencing_token=fencing,
                            )
                            await manager.emit(job_id, "stage", stage)
                    except (ValueError, AttributeError):
                        pass  # Não persistir logs brutos de ferramentas externas.
            readers = asyncio.gather(consume(process.stdout), consume(process.stderr))
            try:
                await asyncio.wait_for(process.wait(), timeout=3600)
                await readers
            finally:
                await manager.active_process.stop()
                readers.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await readers
            if self.store.get_job(job_id)["status"] == "cancel_requested":
                self.store.update(
                    job_id,
                    "cancelled",
                    result=self.store.get_job(job_id)["result"],
                    worker_instance_id=instance_id,
                    fencing_token=fencing,
                )
                return
            if not (root / "result.json").is_file():
                raise RuntimeError("Worker encerrou sem resultado validável.")
            result = json.loads(confined(root, "result.json").read_text(encoding="utf-8"))
            failed = process.returncode != 0 or result.get('blocked') or bool(result.get('error'))
            # Um resultado de erro nunca registra um MP4 parcial como prévia válida.
            artifacts = [inspect_artifact(root, a["relative_path"], a["kind"]) for a in result.get("artifacts", [])
                         if not failed or a['kind'] != 'video']
            if failed:
                with self._uow() as conn:
                    conn.execute(
                        "UPDATE horus.productions SET artifacts = %s::jsonb WHERE id = %s",
                        (encode(artifacts), production_id),
                    )
                detail = result.get('audit_error')
                if (isinstance(detail, dict) and set(detail) == {'code', 'type', 'message'}
                        and all(isinstance(v, str) and len(v) <= 400 for v in detail.values())):
                    current = self.store.get_job(job_id)['result'] or {}
                    self.store.update(
                        job_id,
                        self.store.get_job(job_id)['status'],
                        result={**current, 'audit_error': detail},
                        worker_instance_id=instance_id,
                        fencing_token=fencing,
                    )
                    await manager.emit(job_id, 'error', result.get('error', 'Auditoria de rede inválida.'))
                detail = result.get('failure')
                if not isinstance(detail, dict):
                    detail = failure_detail('production_worker_failed', (self.store.get_job(job_id)['result'] or {}).get('stage', 'prepare'),
                                            started=started, exit_code=process.returncode)
                fail('blocked' if result.get('blocked') else 'failed',
                     sanitized_tail(result.get('error', 'Falha técnica no render offline; confira as dependências e crie nova produção.')),
                     detail)
                return
            if row["mode"] == "simulated" and any(a["kind"] == "video" for a in artifacts):
                raise WorkflowConflict("Simulação não pode registrar vídeo.")
            if row["mode"] in {"local_demo", "local_pilot"} and not any(a["kind"] == "video" for a in artifacts):
                raise WorkflowConflict("Render sem vídeo real verificável.")
            if row['mode'] in {'local_demo', 'local_pilot'}:
                from .local_pilot import registered_json
                validation = registered_json(root, artifacts, 'canais/demo/videos/fixture/render/validation.json')
                video = next(a for a in artifacts if a['kind'] == 'video')
                try:
                    valid = (validation['sha256'] == video['sha256']
                             and int(validation['stream']['nb_read_frames']) >= 2
                             and int(validation['audio_stream']['nb_read_frames']) >= 1
                             and all(math.isfinite(float(validation[key]['duration'])) and float(validation[key]['duration']) > 0
                                     for key in ('stream', 'audio_stream')))
                except (TypeError, ValueError, KeyError):
                    valid = False
                if not valid:
                    raise WorkflowConflict('Vídeo sem validação física íntegra de áudio/frames; confira o diagnóstico e crie nova produção.')
            if row["mode"] == "local_pilot":
                from .local_pilot import confirmed_metadata
                metadata = confirmed_metadata(root, artifacts)
            else:
                metadata = {"narration_kind": "fixture_tone", "narration_disclosure": "Tom sintético local; sem narração."}
            with self._uow() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    # Registro dos artefatos e sucesso precisam sobreviver juntos a um crash.
                    job_row = cur.execute(
                        "SELECT *, clock_timestamp() AS pg_now FROM horus.jobs WHERE id = %s FOR UPDATE",
                        (job_id,),
                    ).fetchone()
                    if job_row is None:
                        return
                    self.store._verify_worker_claim(conn, job_row, instance_id, fencing, job_row["pg_now"])
                    if job_row["status"] == 'cancel_requested':
                        cur.execute(
                            """UPDATE horus.jobs
                               SET status = 'cancelled', updated_at = clock_timestamp(), error = NULL,
                                   worker_instance_id = NULL, lease_until = NULL, fencing_token = NULL
                               WHERE id = %s""",
                            (job_id,),
                        )
                        return
                    cur.execute(
                        "UPDATE horus.productions SET artifacts = %s::jsonb WHERE id = %s",
                        (encode(artifacts), production_id),
                    )
                    cur.execute(
                        """UPDATE horus.jobs
                           SET status = 'succeeded', updated_at = clock_timestamp(), result = %s::jsonb, error = NULL,
                               worker_instance_id = NULL, lease_until = NULL, fencing_token = NULL
                           WHERE id = %s""",
                        (encode({'stage': 'review', 'demo_only': True, **metadata}), job_id),
                    )
        except LeaseLostError:
            lease_lost = True
            if manager.active_process:
                await manager.active_process.stop()
        except asyncio.CancelledError:
            if manager.active_process:
                await manager.active_process.stop()
            with contextlib.suppress(LeaseLostError, DatabaseUnavailable):
                fail('interrupted', 'Execução interrompida; concilie os artefatos antes de criar nova produção.',
                     failure_detail('production_interrupted', (self.store.get_job(job_id)['result'] or {}).get('stage', 'prepare'), started=started))
            raise
        except WorkflowConflict as error:
            with contextlib.suppress(LeaseLostError):
                fail('blocked', str(error), failure_detail('production_precondition_failed',
                     (self.store.get_job(job_id)['result'] or {}).get('stage', 'prepare'), started=started, error=error))
        except Exception as error:
            with contextlib.suppress(LeaseLostError):
                fail('failed', 'Falha na produção offline; revise dependências e artefatos locais antes de criar nova produção.',
                     failure_detail('production_timeout' if isinstance(error, TimeoutError) else 'production_failed',
                        (self.store.get_job(job_id)['result'] or {}).get('stage', 'prepare'), started=started, error=error,
                        exit_code=manager.active_process.process.returncode if manager.active_process else None))
        finally:
            if manager.active_process:
                await manager.active_process.stop()
                manager.active_process.close()
            if not lease_lost:
                with contextlib.suppress(LeaseLostError, DatabaseUnavailable):
                    await manager.emit(job_id, "state", self.store.get_job(job_id)["status"])
            manager.active_process, manager.active_id, manager.claimed_id = None, None, None
