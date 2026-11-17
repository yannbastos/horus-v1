"""Fluxo editorial fixo e simulado. Executado exclusivamente pelo JobManager."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .hermes_contract.models import ChannelProfile, Dataset
from .human_checkpoints import CheckpointService
from .security import acquire_budget_lock, pg_clock
from .storage import Store, _iso, canonical_json_bytes
from .workflow_hermes import CognitiveFailure, CognitiveUnknown, HermesStageOutput, HermesStages

WORKFLOW_IDEMPOTENCY_LOCK_NS = 16413

STAGES = ("planning", "research", "analysis", "proposal")
EXECUTORS = {"simulated": "editorial.simulated", "hermes": "editorial.hermes"}
# Etapas que podem gerar custo: exigem autorização humana explícita por execução.
AUTHORIZED_STAGES = {"hermes": {"analysis"}}
Stage = Literal["planning", "research", "analysis", "proposal"]


class WorkflowConflict(ValueError):
    pass


RADAR_REDACTED = "Dataset Radar expirado e removido pela política de retenção; colete uma nova revisão do Radar."


def radar_redacted(payload) -> bool:
    """True quando as cópias de dados do YouTube já foram redigidas (ver retention.py)."""
    radar = payload.get("radar") if isinstance(payload, dict) else None
    return bool(radar and radar.get("redacted_at"))


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    objective: str = Field(min_length=1, max_length=4000)
    references: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(default_factory=list, max_length=5)
    # Exigidos pelo executor Hermes; o executor é escolhido pelo servidor, nunca pelo payload.
    dataset: Dataset | None = None
    channel_profile: ChannelProfile | None = None


class ManualWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    objective: str | None = Field(default=None, max_length=2000)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)


class AuthorizationRequest(RevisionRequest):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class ReviewRequest(RevisionRequest):
    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    decision: Literal["approve", "reject", "request_changes"]
    feedback: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_feedback(self):
        if self.decision == "request_changes" and not self.feedback.strip():
            raise ValueError("Descreva as alterações solicitadas.")
        return self


class SimulatedOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    simulated: Literal[True]
    stage: Stage
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=8000)
    source_artifact_ids: list[str] = Field(max_length=1)


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


async def simulate(context):
    """Sem rede, subprocessos, modelos, métricas inventadas ou execução de instruções."""
    await asyncio.sleep(0)
    descriptions = {
        "planning": "Plano fixo: organizar referências fornecidas, analisar e propor conteúdo para revisão humana.",
        "research": "Pesquisa simulada. As referências foram apenas registradas; nenhuma fonte externa foi consultada.",
        "analysis": "Análise simulada da solicitação. Não representa verificação factual ou análise real de vídeo.",
        "proposal": "Proposta editorial simulada. Requer revisão humana e não autoriza produção ou publicação.",
    }
    return {"schema_version": 1, "simulated": True, "stage": context["stage"],
            "title": f"{context['stage']} — simulação editorial",
            "text": f"{descriptions[context['stage']]} Objetivo: {context['objective']}\nAjustes: {context['feedback']}",
            "source_artifact_ids": [item["id"] for item in context["sources"]]}


def _audit(store):
    from .transmissions import TransmissionLog
    return TransmissionLog(store)


def _subject(job_id, content_hash):
    return hashlib.sha256(f"{job_id}:{content_hash}".encode()).hexdigest()


def _reservation(settings):
    """Reserva em USD da execução paga, ou None se o orçamento não está configurado (execução simulada)."""
    if settings is None:
        return None
    from .security import SecurityGate, cost_reservation
    try:
        return cost_reservation(settings)
    except SecurityGate:
        return None


def _json_val(value):
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value


class WorkflowService:
    def __init__(self, store: Store, queue: asyncio.Queue, notify, settings=None):
        self.store, self.queue, self.notify = store, queue, notify
        self.settings = settings
        self.checkpoints = CheckpointService(store, _audit(store), settings=settings)
        # Substituição somente em testes; nunca aceitar um executor informado pela API.
        self.executor = simulate
        self.executor_kind = getattr(settings, "workflow_executor", "simulated")
        if self.executor_kind not in EXECUTORS:
            raise ValueError("HORUS_WORKFLOW_EXECUTOR inválido. Use simulated ou hermes.")
        from .hermes_simulator import HermesSimulator
        simulator = HermesSimulator()
        self.hermes_simulated = HermesStages(store, settings, client_factory=lambda: simulator)
        self.hermes = HermesStages(store, settings) if settings is not None else None
        self.active_task: asyncio.Task | None = None
        self.active_job_id: str | None = None
        self.stop_requested: str | None = None

    @staticmethod
    def _require(cur, workflow_id, *, for_update: bool = False):
        sql = "SELECT * FROM horus.workflows WHERE id = %s" + (" FOR UPDATE" if for_update else "")
        row = cur.execute(sql, (workflow_id,)).fetchone()
        if not row:
            raise KeyError(workflow_id)
        return row

    @staticmethod
    def _event(cur, workflow_id, revision, kind, data):
        cur.execute(
            "INSERT INTO horus.workflow_events(workflow_id, revision, kind, data, created_at) VALUES (%s, %s, %s, %s, %s)",
            (workflow_id, revision, kind, Jsonb(data), datetime.now(timezone.utc)),
        )

    @staticmethod
    def _job_event(cur, job_id, status):
        cur.execute(
            "INSERT INTO horus.events(job_id, kind, message, created_at) VALUES (%s, 'state', %s, %s)",
            (job_id, status, datetime.now(timezone.utc)),
        )

    @classmethod
    def _job_status(cls, cur, job_id, status, *, result=None, error=None):
        cur.execute(
            "UPDATE horus.jobs SET status = %s, updated_at = %s, result = %s, error = %s WHERE id = %s",
            (status, datetime.now(timezone.utc), Jsonb(result) if result is not None else None, error, job_id),
        )
        cls._job_event(cur, job_id, status)

    @classmethod
    def _create_steps(cls, cur, workflow_id, revision, executor="simulated"):
        previous = None
        first = None
        for index, stage in enumerate(STAGES):
            job_id = uuid.uuid4().hex
            status = "waiting" if previous else "queued"
            payload = {"operation": EXECUTORS[executor], "stage": stage}
            timestamp = datetime.now(timezone.utc)
            cur.execute(
                """INSERT INTO horus.jobs(
                    id, idempotency_key, payload, payload_canonical, status, created_at, updated_at,
                    workflow_id, workflow_revision, step_key, step_index, depends_on
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    job_id,
                    f"workflow:{workflow_id}:{revision}:{stage}",
                    Jsonb(payload),
                    canonical_json_bytes(payload),
                    status,
                    timestamp,
                    timestamp,
                    workflow_id,
                    revision,
                    stage,
                    index,
                    previous,
                ),
            )
            cls._job_event(cur, job_id, status)
            first = first or job_id
            previous = job_id
        return first

    def get(self, workflow_id):
        with self.store.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                item = dict(self._require(cur, workflow_id))
                item.pop("idempotency_key", None)
                item.pop("payload_canonical", None)
                item["request"] = _json_val(item.pop("payload"))
                item["created_at"] = _iso(item.get("created_at"))
                item["updated_at"] = _iso(item.get("updated_at"))
                if item.pop("cognitive_client") == "simulated":
                    item["executor"] = "hermes_simulated"
                item["request"].pop("_radar_request", None)
                item["simulated"] = item["executor"] in {"simulated", "hermes_simulated"}
                if item["request"].get("origin") == "human":
                    item["executor"], item["simulated"] = "human", False
                if "radar" in item["request"]:
                    item["radar"] = item["request"]["radar"]
                item["steps"] = [
                    self.store.job(row)
                    for row in cur.execute(
                        "SELECT * FROM horus.jobs WHERE workflow_id = %s ORDER BY workflow_revision, step_index",
                        (workflow_id,),
                    ).fetchall()
                ]
                attempts = []
                for row in cur.execute(
                    """SELECT a.* FROM horus.job_attempts a
                       JOIN horus.jobs j ON j.id = a.job_id
                       WHERE j.workflow_id = %s
                       ORDER BY j.workflow_revision, j.step_index, a.number""",
                    (workflow_id,),
                ).fetchall():
                    attempt = dict(row)
                    attempt.pop("input_artifacts_canonical", None)
                    attempt.pop("result_canonical", None)
                    attempt["input_artifacts"] = _json_val(attempt["input_artifacts"])
                    attempt["result"] = _json_val(attempt["result"]) if attempt["result"] is not None else None
                    attempt["started_at"] = _iso(attempt.get("started_at"))
                    attempt["finished_at"] = _iso(attempt.get("finished_at"))
                    attempts.append(attempt)
                item["attempts"] = attempts
                artifacts = []
                for row in cur.execute(
                    "SELECT * FROM horus.artifacts WHERE workflow_id = %s ORDER BY revision, created_at",
                    (workflow_id,),
                ).fetchall():
                    artifact = dict(row)
                    artifact.pop("content_canonical", None)
                    artifact["content"] = _json_val(artifact["content"])
                    artifact["created_at"] = _iso(artifact.get("created_at"))
                    artifacts.append(artifact)
                item["artifacts"] = artifacts
                item["approvals"] = [
                    self._approval(row)
                    for row in cur.execute(
                        "SELECT * FROM horus.approvals WHERE workflow_id = %s ORDER BY created_at",
                        (workflow_id,),
                    ).fetchall()
                ]
                # Corpo do pedido fica fora da resposta; chave, referência externa e consumo são rastreáveis.
                runs = []
                for row in cur.execute(
                    """SELECT r.id, r.job_id, r.attempt_id, r.provider, r.idempotency_key, r.external_ref,
                              r.status, r.remote_status, r.usage, r.error, r.created_at, r.updated_at
                       FROM horus.cognitive_runs r
                       JOIN horus.jobs j ON j.id = r.job_id
                       WHERE j.workflow_id = %s
                       ORDER BY r.created_at""",
                    (workflow_id,),
                ).fetchall():
                    run = dict(row)
                    run["usage"] = _json_val(run["usage"]) if run["usage"] is not None else None
                    run["created_at"] = _iso(run.get("created_at"))
                    run["updated_at"] = _iso(run.get("updated_at"))
                    runs.append(run)
                item["cognitive_runs"] = runs
                auths = []
                for row in cur.execute(
                    "SELECT * FROM horus.authorizations WHERE workflow_id = %s ORDER BY created_at",
                    (workflow_id,),
                ).fetchall():
                    auth = {
                        key: value
                        for key, value in dict(row).items()
                        if key not in {"request", "request_canonical", "idempotency_key"}
                    }
                    auth["created_at"] = _iso(auth.get("created_at"))
                    auths.append(auth)
                item["authorizations"] = auths
                pending = next(
                    (
                        step
                        for step in item["steps"]
                        if step["status"] == "awaiting_authorization" and step["workflow_revision"] == item["revision"]
                    ),
                    None,
                )
                item["pending_authorization"] = {"job_id": pending["id"], "stage": pending["step_key"]} if pending else None
                return item

    def list(self):
        with self.store.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    "SELECT id, status, revision, created_at, updated_at FROM horus.workflows ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["created_at"] = _iso(item.get("created_at"))
            item["updated_at"] = _iso(item.get("updated_at"))
            items.append(item)
        return items

    @staticmethod
    def _approval(row):
        item = dict(row)
        item.pop("idempotency_key", None)
        item.pop("request", None)
        item.pop("request_canonical", None)
        item["created_at"] = _iso(item.get("created_at"))
        return item

    def events(self, workflow_id, after=0):
        with self.store.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                self._require(cur, workflow_id)
                items = []
                for row in cur.execute(
                    "SELECT * FROM horus.workflow_events WHERE workflow_id = %s AND sequence > %s ORDER BY sequence LIMIT 200",
                    (workflow_id, after),
                ).fetchall():
                    item = dict(row)
                    item["data"] = _json_val(item["data"])
                    item["created_at"] = _iso(item.get("created_at"))
                    items.append(item)
                return items

    def create_manual(self, request: ManualWorkflowRequest, key: str):
        """Registra a ação humana e sua aprovação atomicamente, sem enfileirar IA."""
        payload_obj = {**request.model_dump(mode="json"), "origin": "human"}
        payload_canonical = canonical_json_bytes(payload_obj)
        with self.store.uow() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (WORKFLOW_IDEMPOTENCY_LOCK_NS, key))
            with conn.cursor(row_factory=dict_row) as cur:
                existing = cur.execute("SELECT * FROM horus.workflows WHERE idempotency_key = %s", (key,)).fetchone()
                if existing:
                    if bytes(existing["payload_canonical"]) != payload_canonical:
                        raise WorkflowConflict("A chave de idempotência já identifica outro pedido editorial.")
                    return {
                        "workflow_id": existing["id"],
                        "status": "approved",
                        "proposal_artifact_id": existing["current_artifact_id"],
                    }, False
                workflow_id, job_id, attempt_id, artifact_id, approval_id = (uuid.uuid4().hex for _ in range(5))
                timestamp = datetime.now(timezone.utc)
                content = {
                    "schema_version": 1,
                    "simulated": False,
                    "origin": "human",
                    "stage": "proposal",
                    "title": request.title,
                    "text": request.objective or request.title,
                    "source_artifact_ids": [],
                }
                # O schema anterior restringe executor a simulated/hermes; a autoria real fica no payload e na tentativa.
                cur.execute(
                    """INSERT INTO horus.workflows(
                        id, idempotency_key, payload, payload_canonical, revision, status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, 1, 'approved', %s, %s)""",
                    (workflow_id, key, Jsonb(payload_obj), payload_canonical, timestamp, timestamp),
                )
                job_payload = {"operation": "editorial.manual", "stage": "proposal"}
                job_result = {"artifact_id": artifact_id, "origin": "human"}
                cur.execute(
                    """INSERT INTO horus.jobs(
                        id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, attempt,
                        workflow_id, workflow_revision, step_key, step_index, result
                    ) VALUES (%s, %s, %s, %s, 'succeeded', %s, %s, 1, %s, 1, 'proposal', 0, %s)""",
                    (
                        job_id,
                        f"manual:{workflow_id}",
                        Jsonb(job_payload),
                        canonical_json_bytes(job_payload),
                        timestamp,
                        timestamp,
                        workflow_id,
                        Jsonb(job_result),
                    ),
                )
                cur.execute(
                    """INSERT INTO horus.job_attempts(
                        id, job_id, number, status, executor, input_hash, input_artifacts, input_artifacts_canonical,
                        started_at, finished_at, result, result_canonical
                    ) VALUES (%s, %s, 1, 'succeeded', 'human', %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        attempt_id,
                        job_id,
                        digest(payload_obj),
                        Jsonb([]),
                        canonical_json_bytes([]),
                        timestamp,
                        timestamp,
                        Jsonb(content),
                        canonical_json_bytes(content),
                    ),
                )
                cur.execute(
                    """INSERT INTO horus.artifacts(
                        id, workflow_id, revision, stage, job_id, attempt_id, schema_version,
                        content, content_canonical, content_hash, created_at
                    ) VALUES (%s, %s, 1, 'proposal', %s, %s, 1, %s, %s, %s, %s)""",
                    (
                        artifact_id,
                        workflow_id,
                        job_id,
                        attempt_id,
                        Jsonb(content),
                        canonical_json_bytes(content),
                        digest(content),
                        timestamp,
                    ),
                )
                review = ReviewRequest(
                    expected_revision=1,
                    artifact_id=artifact_id,
                    decision="approve",
                    feedback="Proposta manual criada e aprovada pelo operador local.",
                )
                review_obj = review.model_dump()
                cur.execute(
                    """INSERT INTO horus.approvals(
                        id, workflow_id, revision, artifact_id, decision, feedback, operator_id, scope,
                        idempotency_key, request, request_canonical, created_at
                    ) VALUES (%s, %s, 1, %s, 'approve', %s, 'local-operator', 'editorial_proposal', %s, %s, %s, %s)""",
                    (
                        approval_id,
                        workflow_id,
                        artifact_id,
                        review.feedback,
                        key,
                        Jsonb(review_obj),
                        canonical_json_bytes(review_obj),
                        timestamp,
                    ),
                )
                cur.execute("UPDATE horus.workflows SET current_artifact_id = %s WHERE id = %s", (artifact_id, workflow_id))
                self._job_event(cur, job_id, "succeeded")
                self._event(cur, workflow_id, 1, "workflow.created", {"origin": "human", "simulated": False})
                self._event(cur, workflow_id, 1, "review.recorded", {"approval_id": approval_id, "decision": "approve"})
        return {"workflow_id": workflow_id, "status": "approved", "proposal_artifact_id": artifact_id}, True

    async def create(self, request: WorkflowRequest, key: str, *, executor=None, radar=None, radar_request=None):
        data = request.model_dump(mode="json", exclude_none=True)
        if radar is not None:
            data["radar"] = radar
            data["_radar_request"] = radar_request
        payload_canonical = canonical_json_bytes(data)
        executor = executor or self.executor_kind
        cognitive_client = "simulated" if executor == "hermes_simulated" else "live"
        executor = "hermes" if executor == "hermes_simulated" else executor
        if executor in AUTHORIZED_STAGES and (request.dataset is None or request.channel_profile is None):
            raise WorkflowConflict("O executor Hermes exige dataset e channel_profile no pedido.")
        first = None
        with self.store.uow() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (WORKFLOW_IDEMPOTENCY_LOCK_NS, key))
            with conn.cursor(row_factory=dict_row) as cur:
                existing = cur.execute("SELECT * FROM horus.workflows WHERE idempotency_key = %s", (key,)).fetchone()
                if existing:
                    if bytes(existing["payload_canonical"]) != payload_canonical:
                        raise WorkflowConflict("A chave de idempotência já identifica outro pedido editorial.")
                    workflow_id = existing["id"]
                else:
                    workflow_id, timestamp = uuid.uuid4().hex, datetime.now(timezone.utc)
                    cur.execute(
                        """INSERT INTO horus.workflows(
                            id, idempotency_key, payload, payload_canonical, revision, status,
                            created_at, updated_at, executor, cognitive_client
                        ) VALUES (%s, %s, %s, %s, 1, 'queued', %s, %s, %s, %s)""",
                        (workflow_id, key, Jsonb(data), payload_canonical, timestamp, timestamp, executor, cognitive_client),
                    )
                    first = self._create_steps(cur, workflow_id, 1, executor)
                    self._event(
                        cur,
                        workflow_id,
                        1,
                        "workflow.created",
                        {
                            "simulated": executor == "simulated" or cognitive_client == "simulated",
                            "executor": "hermes_simulated" if cognitive_client == "simulated" else executor,
                        },
                    )
        if first:
            self.queue.put_nowait(first)
            await self.notify()
        return self.get(workflow_id)

    def _claim(self, job_id):
        from .human_checkpoints import CheckpointRefused
        try:
            return self._claim_tx(job_id)
        except CheckpointRefused as error:
            self.checkpoints.audit_refusal(error)   # fora da transação já revertida
            raise

    def _claim_tx(self, job_id):
        with self.store.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                job_ref = cur.execute("SELECT workflow_id FROM horus.jobs WHERE id = %s", (job_id,)).fetchone()
                if not job_ref or not job_ref["workflow_id"]:
                    raise KeyError(job_id)
                workflow = self._require(cur, job_ref["workflow_id"], for_update=True)
                job = cur.execute("SELECT * FROM horus.jobs WHERE id = %s FOR UPDATE", (job_id,)).fetchone()
                if job["status"] != "queued" or workflow["status"] not in {"queued", "running"}:
                    return None
                if job["workflow_revision"] != workflow["revision"]:
                    return None
                request = _json_val(workflow["payload"])
                if radar_redacted(request):
                    raise WorkflowConflict(RADAR_REDACTED)
                stage = job["step_key"]
                if stage not in STAGES or job["step_index"] != STAGES.index(stage):
                    raise WorkflowConflict("Etapa editorial inválida.")
                executor = workflow["executor"]
                if _json_val(job["payload"]) != {"operation": EXECUTORS[executor], "stage": stage}:
                    raise WorkflowConflict("Operação editorial não autorizada.")
                sources = []
                if job["step_index"]:
                    previous = cur.execute(
                        """SELECT * FROM horus.jobs
                           WHERE id = %s AND workflow_id = %s AND workflow_revision = %s AND step_index = %s""",
                        (job["depends_on"], workflow["id"], workflow["revision"], job["step_index"] - 1),
                    ).fetchone()
                    if not previous or previous["status"] != "succeeded":
                        raise WorkflowConflict("A dependência desta etapa não foi concluída.")
                    source = cur.execute("SELECT * FROM horus.artifacts WHERE job_id = %s", (previous["id"],)).fetchone()
                    source_content = _json_val(source["content"]) if source else None
                    if not source or digest(source_content) != source["content_hash"]:
                        raise WorkflowConflict("Artefato de entrada ausente ou com integridade inválida.")
                    sources = [{"id": source["id"], "content": source_content, "hash": source["content_hash"]}]
                elif job["depends_on"] is not None:
                    raise WorkflowConflict("A primeira etapa não aceita dependências.")
                context = {
                    **request,
                    "workflow_id": workflow["id"],
                    "revision": workflow["revision"],
                    "job_id": job_id,
                    "stage": stage,
                    "feedback": workflow["feedback"],
                    "sources": sources,
                    "executor": executor,
                    "cognitive_client": workflow["cognitive_client"],
                }
                attempt_id, timestamp = uuid.uuid4().hex, datetime.now(timezone.utc)
                if stage in AUTHORIZED_STAGES.get(executor, ()):
                    unknown = cur.execute(
                        """SELECT * FROM horus.cognitive_runs
                           WHERE job_id = %s AND status = 'unknown'
                           ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                        (job_id,),
                    ).fetchone()
                    if unknown:
                        # Conciliação: segue a execução remota existente, sem nova autorização nem novo envio.
                        run_dict = dict(unknown)
                        run_dict.pop("request_canonical", None)
                        run_dict["created_at"] = _iso(run_dict.get("created_at"))
                        run_dict["updated_at"] = _iso(run_dict.get("updated_at"))
                        context["resume_run"] = run_dict
                    else:
                        grant = cur.execute(
                            """SELECT id FROM horus.authorizations
                               WHERE job_id = %s AND revision = %s AND consumed_attempt_id IS NULL
                               ORDER BY created_at LIMIT 1 FOR UPDATE""",
                            (job_id, workflow["revision"]),
                        ).fetchone()
                        if not grant:
                            raise WorkflowConflict("Execução cognitiva sem autorização humana vigente.")
                        context["authorization_id"] = grant["id"]
                        # HOR-107: gasto exige checkpoint humano válido (vigente, dentro do limite, uso único); falha desfaz a tentativa.
                        source = cur.execute(
                            """SELECT a.content_hash FROM horus.authorizations z
                               JOIN horus.artifacts a ON a.id = z.input_artifact_id
                               WHERE z.id = %s""",
                            (grant["id"],),
                        ).fetchone()
                        reservation = _reservation(self.settings)
                        self.checkpoints.consume(
                            action="hermes_run",
                            subject=_subject(job_id, source["content_hash"]),
                            amount=reservation,
                            currency="USD" if reservation is not None else None,
                            ref=attempt_id,
                            db=conn,
                        )
                executor_name = "simulated-v1" if executor == "simulated" else (
                    "hermes-v1" if stage in AUTHORIZED_STAGES.get(executor, ()) else "local-v1"
                )
                cur.execute(
                    "UPDATE horus.jobs SET status = 'running', attempt = attempt + 1, updated_at = %s, error = NULL, result = NULL WHERE id = %s",
                    (timestamp, job_id),
                )
                input_artifact_ids = [s["id"] for s in sources]
                cur.execute(
                    """INSERT INTO horus.job_attempts(
                        id, job_id, number, status, executor, input_hash, input_artifacts, input_artifacts_canonical, started_at
                    ) VALUES (%s, %s, %s, 'running', %s, %s, %s, %s, %s)""",
                    (
                        attempt_id,
                        job_id,
                        job["attempt"] + 1,
                        executor_name,
                        digest(context),
                        Jsonb(input_artifact_ids),
                        canonical_json_bytes(input_artifact_ids),
                        timestamp,
                    ),
                )
                if context.get("authorization_id"):
                    cur.execute(
                        "UPDATE horus.authorizations SET consumed_attempt_id = %s WHERE id = %s",
                        (attempt_id, context["authorization_id"]),
                    )
                cur.execute("UPDATE horus.workflows SET status = 'running', updated_at = %s WHERE id = %s", (timestamp, workflow["id"]))
                self._job_event(cur, job_id, "running")
                self._event(cur, workflow["id"], workflow["revision"], "step.started", {"job_id": job_id, "attempt_id": attempt_id, "stage": stage})
                return attempt_id, context

    @classmethod
    def _cancel_remaining(cls, cur, workflow_id):
        for row in cur.execute(
            """SELECT id FROM horus.jobs WHERE workflow_id = %s
               AND status IN ('waiting', 'queued', 'awaiting_authorization')""",
            (workflow_id,),
        ).fetchall():
            cls._job_status(cur, row["id"], "cancelled")

    def _finish(self, job_id, attempt_id, outcome, *, content=None, error=None):
        next_job = None
        with self.store.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                job_ref = cur.execute("SELECT workflow_id FROM horus.jobs WHERE id = %s", (job_id,)).fetchone()
                if not job_ref or not job_ref["workflow_id"]:
                    return None
                workflow = self._require(cur, job_ref["workflow_id"], for_update=True)
                attempt = cur.execute(
                    "SELECT * FROM horus.job_attempts WHERE id = %s AND job_id = %s FOR UPDATE",
                    (attempt_id, job_id),
                ).fetchone()
                if not attempt or attempt["status"] != "running":
                    return None
                job = cur.execute("SELECT * FROM horus.jobs WHERE id = %s FOR UPDATE", (job_id,)).fetchone()
                if workflow["status"] == "cancel_requested":
                    outcome, content = "cancelled", None
                elif workflow["revision"] != job["workflow_revision"] or workflow["status"] != "running":
                    outcome, content, error = "interrupted", None, "Resultado fora da revisão ativa."
                result = None
                timestamp = datetime.now(timezone.utc)
                if outcome == "succeeded":
                    artifact_id = uuid.uuid4().hex
                    cur.execute(
                        """INSERT INTO horus.artifacts(
                            id, workflow_id, revision, stage, job_id, attempt_id, schema_version,
                            content, content_canonical, content_hash, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s)""",
                        (
                            artifact_id,
                            workflow["id"],
                            workflow["revision"],
                            job["step_key"],
                            job_id,
                            attempt_id,
                            Jsonb(content),
                            canonical_json_bytes(content),
                            digest(content),
                            timestamp,
                        ),
                    )
                    result = {"artifact_id": artifact_id, "simulated": True, "stage": job["step_key"]}
                    following = cur.execute("SELECT * FROM horus.jobs WHERE depends_on = %s", (job_id,)).fetchone()
                    if following:
                        if (following["status"] != "waiting" or following["workflow_revision"] != workflow["revision"]
                                or following["workflow_id"] != workflow["id"] or following["step_index"] != job["step_index"] + 1):
                            raise WorkflowConflict("Transição inconsistente para a próxima etapa.")
                        if following["step_key"] in AUTHORIZED_STAGES.get(workflow["executor"], ()):
                            # Não enfileirar: custo potencial exige autorização humana desta revisão.
                            self._job_status(cur, following["id"], "awaiting_authorization")
                            cur.execute("UPDATE horus.workflows SET status = 'queued' WHERE id = %s", (workflow["id"],))
                            self._event(
                                cur,
                                workflow["id"],
                                workflow["revision"],
                                "workflow.authorization_required",
                                {"job_id": following["id"], "stage": following["step_key"]},
                            )
                        else:
                            self._job_status(cur, following["id"], "queued")
                            next_job = following["id"]
                    else:
                        if job["step_key"] != "proposal":
                            raise WorkflowConflict("O fluxo editorial não pode terminar antes da proposta.")
                        cur.execute(
                            "UPDATE horus.workflows SET status = 'awaiting_approval', current_artifact_id = %s WHERE id = %s",
                            (artifact_id, workflow["id"]),
                        )
                        self._event(cur, workflow["id"], workflow["revision"], "workflow.awaiting_approval", result)
                else:
                    cur.execute("UPDATE horus.workflows SET status = %s WHERE id = %s", (outcome, workflow["id"]))
                    if outcome == "cancelled":
                        self._cancel_remaining(cur, workflow["id"])
                self._job_status(cur, job_id, outcome, result=result, error=error)
                cur.execute(
                    "UPDATE horus.job_attempts SET status = %s, finished_at = %s, result = %s, result_canonical = %s, error = %s WHERE id = %s",
                    (
                        outcome,
                        timestamp,
                        Jsonb(result) if result is not None else None,
                        canonical_json_bytes(result) if result is not None else None,
                        error,
                        attempt_id,
                    ),
                )
                cur.execute("UPDATE horus.workflows SET updated_at = %s WHERE id = %s", (timestamp, workflow["id"]))
                self._event(
                    cur,
                    workflow["id"],
                    workflow["revision"],
                    f"step.{outcome}",
                    {"job_id": job_id, "attempt_id": attempt_id, "stage": job["step_key"], "error": error},
                )
        return next_job

    async def run_step(self, job):
        try:
            claimed = self._claim(job["id"])
        except (ValueError, KeyError) as error:
            with self.store.uow() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._require(cur, job["workflow_id"], for_update=True)
                    self._job_status(cur, job["id"], "blocked", error=str(error))
                    cur.execute(
                        "UPDATE horus.workflows SET status = 'failed', updated_at = %s WHERE id = %s",
                        (datetime.now(timezone.utc), job["workflow_id"]),
                    )
                    self._event(cur, job["workflow_id"], job["workflow_revision"], "step.blocked", {"job_id": job["id"], "error": str(error)})
            await self.notify()
            return
        if not claimed:
            return
        attempt_id, context = claimed
        self.active_job_id = job["id"]
        try:
            if context["executor"] in AUTHORIZED_STAGES:
                stages = self.hermes_simulated if context["cognitive_client"] == "simulated" else self.hermes
                # Etapas locais são instantâneas; a cognitiva controla o próprio prazo e o cancelamento remoto.
                stage = context["stage"]
                if stage == "analysis":
                    work = stages.analysis(context, attempt_id, lambda: self.stop_requested == job["id"])
                else:
                    work = asyncio.to_thread(getattr(stages, stage), context)
                self.active_task = asyncio.create_task(work)
                await self.notify()
                content = await self.active_task
                content["simulated"] = context["cognitive_client"] == "simulated"
                output = HermesStageOutput.model_validate(content)
            else:
                self.active_task = asyncio.create_task(self.executor(context))
                await self.notify()
                output = SimulatedOutput.model_validate(await asyncio.wait_for(self.active_task, timeout=5))
            if output.stage != context["stage"] or output.source_artifact_ids != [s["id"] for s in context["sources"]]:
                raise WorkflowConflict("Resultado não corresponde à etapa ou aos artefatos de entrada.")
            next_job = self._finish(job["id"], attempt_id, "succeeded", content=output.model_dump())
            if next_job:
                self.queue.put_nowait(next_job)
        except asyncio.CancelledError:
            interrupted = asyncio.current_task().cancelling() > 0
            self._finish(job["id"], attempt_id, "interrupted" if interrupted else "cancelled", error="Execução interrompida.")
            if interrupted:
                raise
        except CognitiveUnknown as error:
            # Sem nova tentativa até conciliar a mesma execução remota.
            self._finish(job["id"], attempt_id, "interrupted", error=str(error)[:2000])
        except CognitiveFailure as error:
            self._finish(job["id"], attempt_id, "failed", error=str(error)[:2000])
        except Exception as error:
            self._finish(job["id"], attempt_id, "failed", error=f"{type(error).__name__}: {error}"[:2000])
        finally:
            if self.active_task and not self.active_task.done():
                self.active_task.cancel()
                try:
                    await self.active_task
                except asyncio.CancelledError:
                    pass
            self.active_task = None
            self.active_job_id = None
            if self.stop_requested == job["id"]:
                self.stop_requested = None
            await self.notify()

    def recover(self):
        with self.store.uow() as conn:
            acquire_budget_lock(conn)
            timestamp = pg_clock(conn)
            with conn.cursor(row_factory=dict_row) as cur:
                # Execução remota pode ter continuado: desfecho desconhecido até conciliar.
                cur.execute(
                    """UPDATE horus.cognitive_runs
                       SET status = 'unknown', updated_at = %s,
                           error = COALESCE(error, 'Backend encerrado durante a execução remota.')
                       WHERE status IN ('submitting', 'admitted')""",
                    (timestamp,),
                )
                active = cur.execute(
                    """SELECT j.id, a.id AS attempt_id
                       FROM horus.jobs j
                       JOIN horus.job_attempts a ON a.job_id = j.id
                       WHERE j.workflow_id IS NOT NULL AND a.status = 'running'"""
                ).fetchall()
        for row in active:
            self._finish(row["id"], row["attempt_id"], "interrupted", error="Backend interrompido. Nova tentativa exige ação explícita.")

    async def cancel(self, workflow_id):
        active_job = None
        with self.store.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                workflow = self._require(cur, workflow_id, for_update=True)
                if workflow["status"] in {"approved", "rejected", "cancelled"}:
                    return self.get(workflow_id)
                active = cur.execute(
                    "SELECT id FROM horus.jobs WHERE workflow_id = %s AND status IN ('running', 'cancel_requested')",
                    (workflow_id,),
                ).fetchone()
                self._cancel_remaining(cur, workflow_id)
                status = "cancel_requested" if active else "cancelled"
                if active:
                    active_job = active["id"]
                    self._job_status(cur, active_job, "cancel_requested")
                cur.execute(
                    "UPDATE horus.workflows SET status = %s, updated_at = %s WHERE id = %s",
                    (status, datetime.now(timezone.utc), workflow_id),
                )
                self._event(cur, workflow_id, workflow["revision"], f"workflow.{status}", {})
        if active_job == self.active_job_id and self.active_task:
            self.stop_requested = active_job  # cancelamento do operador: pedir parada remota
            self.active_task.cancel()
        await self.notify()
        return self.get(workflow_id)

    async def retry(self, workflow_id, revision):
        with self.store.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                workflow = self._require(cur, workflow_id, for_update=True)
                if radar_redacted(_json_val(workflow["payload"])):
                    raise WorkflowConflict(RADAR_REDACTED)
                if workflow["revision"] != revision or workflow["status"] not in {"failed", "interrupted"}:
                    raise WorkflowConflict("Somente a revisão atual com falha ou interrupção pode ser retomada.")
                job = cur.execute(
                    """SELECT * FROM horus.jobs
                       WHERE workflow_id = %s AND workflow_revision = %s
                         AND status IN ('failed', 'interrupted', 'blocked')
                       ORDER BY step_index LIMIT 1""",
                    (workflow_id, revision),
                ).fetchone()
                if not job:
                    raise WorkflowConflict("Não há etapa recuperável nesta revisão.")
                paid = job["step_key"] in AUTHORIZED_STAGES.get(workflow["executor"], ())
                if paid and cur.execute(
                    "SELECT 1 FROM horus.cognitive_runs WHERE job_id = %s AND status = 'unknown'",
                    (job["id"],),
                ).fetchone():
                    raise WorkflowConflict("A execução cognitiva anterior tem desfecho desconhecido. Concilie antes de tentar novamente.")
                # Nova execução paga nunca é automática: retry volta a pedir autorização humana.
                self._job_status(cur, job["id"], "awaiting_authorization" if paid else "queued")
                cur.execute(
                    "UPDATE horus.workflows SET status = 'queued', updated_at = %s WHERE id = %s",
                    (datetime.now(timezone.utc), workflow_id),
                )
                self._event(
                    cur,
                    workflow_id,
                    revision,
                    "workflow.authorization_required" if paid else "workflow.retried",
                    {"job_id": job["id"], "stage": job["step_key"]},
                )
        if not paid:
            self.queue.put_nowait(job["id"])
        await self.notify()
        return self.get(workflow_id)

    async def authorize(self, workflow_id, request: AuthorizationRequest, key: str):
        """Autorização humana de UMA execução cognitiva sobre o snapshot desta revisão."""
        request_obj = request.model_dump()
        request_canonical = canonical_json_bytes(request_obj)
        with self.store.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                workflow = self._require(cur, workflow_id, for_update=True)
                payload = _json_val(workflow["payload"])
                if radar_redacted(payload):
                    raise WorkflowConflict(RADAR_REDACTED)
                radar = payload.get("radar")
                if radar and datetime.fromisoformat(radar["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                    raise WorkflowConflict("Dataset Radar expirado; colete uma nova revisão antes de autorizar.")
                existing = cur.execute(
                    "SELECT * FROM horus.authorizations WHERE workflow_id = %s AND idempotency_key = %s",
                    (workflow_id, key),
                ).fetchone()
                if existing:
                    if bytes(existing["request_canonical"]) != request_canonical:
                        raise WorkflowConflict("Chave de autorização já usada com outro pedido.")
                    return self.get(workflow_id)
                job = cur.execute(
                    "SELECT * FROM horus.jobs WHERE id = %s AND workflow_id = %s FOR UPDATE",
                    (request.job_id, workflow_id),
                ).fetchone()
                if (workflow["revision"] != request.expected_revision or not job or job["status"] != "awaiting_authorization"
                        or job["workflow_revision"] != workflow["revision"]
                        or job["step_key"] not in AUTHORIZED_STAGES.get(workflow["executor"], ())):
                    raise WorkflowConflict("Nenhuma etapa desta revisão aguarda autorização com esse identificador.")
                source = cur.execute("SELECT * FROM horus.artifacts WHERE job_id = %s", (job["depends_on"],)).fetchone()
                source_content = _json_val(source["content"]) if source else None
                if not source or digest(source_content) != source["content_hash"]:
                    raise WorkflowConflict("Artefato de entrada ausente ou com integridade inválida.")
                authorization_id = uuid.uuid4().hex
                timestamp = datetime.now(timezone.utc)
                cur.execute(
                    """INSERT INTO horus.authorizations(
                        id, workflow_id, revision, job_id, input_artifact_id, scope, max_runs,
                        operator_id, idempotency_key, request, request_canonical, created_at
                    ) VALUES (%s, %s, %s, %s, %s, 'cognitive_run', 1, 'local-operator', %s, %s, %s, %s)""",
                    (
                        authorization_id,
                        workflow_id,
                        workflow["revision"],
                        job["id"],
                        source["id"],
                        key,
                        Jsonb(request_obj),
                        request_canonical,
                        timestamp,
                    ),
                )
                reservation = _reservation(self.settings)
                self.checkpoints.grant(
                    action="hermes_run",
                    subject=_subject(job["id"], source["content_hash"]),
                    description=f"Execução cognitiva {job['step_key']} do workflow {workflow_id[:12]}",
                    limit_amount=None if reservation is None else format(reservation, "f"),
                    currency=None if reservation is None else "USD",
                    key="authorize:" + authorization_id,
                    db=conn,
                )
                self._job_status(cur, job["id"], "queued")
                cur.execute(
                    "UPDATE horus.workflows SET status = 'queued', updated_at = %s WHERE id = %s",
                    (timestamp, workflow_id),
                )
                self._event(
                    cur,
                    workflow_id,
                    workflow["revision"],
                    "workflow.authorized",
                    {"authorization_id": authorization_id, "job_id": job["id"], "max_runs": 1},
                )
        self.queue.put_nowait(job["id"])
        await self.notify()
        return self.get(workflow_id)

    async def reconcile(self, workflow_id, revision):
        """Retoma o acompanhamento da MESMA execução remota de desfecho desconhecido; nunca reenvia."""
        with self.store.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                workflow = self._require(cur, workflow_id, for_update=True)
                if radar_redacted(_json_val(workflow["payload"])):
                    raise WorkflowConflict(RADAR_REDACTED)
                if workflow["revision"] != revision or workflow["status"] not in {"failed", "interrupted"}:
                    raise WorkflowConflict("Somente a revisão atual com falha ou interrupção pode ser conciliada.")
                job = cur.execute(
                    """SELECT j.* FROM horus.jobs j
                       JOIN horus.cognitive_runs r ON r.job_id = j.id
                       WHERE j.workflow_id = %s AND j.workflow_revision = %s
                         AND j.status IN ('failed', 'interrupted') AND r.status = 'unknown'
                       ORDER BY r.created_at DESC LIMIT 1""",
                    (workflow_id, revision),
                ).fetchone()
                if not job:
                    raise WorkflowConflict("Não há execução cognitiva com desfecho desconhecido nesta revisão.")
                self._job_status(cur, job["id"], "queued")
                cur.execute(
                    "UPDATE horus.workflows SET status = 'queued', updated_at = %s WHERE id = %s",
                    (datetime.now(timezone.utc), workflow_id),
                )
                self._event(cur, workflow_id, revision, "workflow.reconciling", {"job_id": job["id"]})
        self.queue.put_nowait(job["id"])
        await self.notify()
        return self.get(workflow_id)

    async def review(self, workflow_id, request: ReviewRequest, key: str):
        request_obj = request.model_dump()
        request_canonical = canonical_json_bytes(request_obj)
        next_job = None
        with self.store.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                workflow = self._require(cur, workflow_id, for_update=True)
                existing = cur.execute(
                    "SELECT * FROM horus.approvals WHERE workflow_id = %s AND idempotency_key = %s",
                    (workflow_id, key),
                ).fetchone()
                if existing:
                    if bytes(existing["request_canonical"]) != request_canonical:
                        raise WorkflowConflict("Chave de revisão já usada com outra decisão.")
                    approval_id = existing["id"]
                else:
                    if (workflow["status"] != "awaiting_approval" or workflow["revision"] != request.expected_revision
                            or workflow["current_artifact_id"] != request.artifact_id):
                        raise WorkflowConflict("Proposta ou revisão obsoleta, ou workflow fora da revisão humana.")
                    artifact = cur.execute(
                        "SELECT * FROM horus.artifacts WHERE id = %s AND workflow_id = %s AND revision = %s AND stage = 'proposal'",
                        (request.artifact_id, workflow_id, request.expected_revision),
                    ).fetchone()
                    artifact_content = _json_val(artifact["content"]) if artifact else None
                    if not artifact or digest(artifact_content) != artifact["content_hash"]:
                        raise WorkflowConflict("Proposta com integridade inválida.")
                    if request.decision == "request_changes" and radar_redacted(_json_val(workflow["payload"])):
                        raise WorkflowConflict(RADAR_REDACTED)  # nova revisão reexecutaria análise sem o dataset
                    approval_id = uuid.uuid4().hex
                    timestamp = datetime.now(timezone.utc)
                    cur.execute(
                        """INSERT INTO horus.approvals(
                            id, workflow_id, revision, artifact_id, decision, feedback, operator_id, scope,
                            idempotency_key, request, request_canonical, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, 'local-operator', 'editorial_proposal', %s, %s, %s, %s)""",
                        (
                            approval_id,
                            workflow_id,
                            workflow["revision"],
                            request.artifact_id,
                            request.decision,
                            request.feedback,  # operador vem do backend, nunca do payload do executor
                            key,
                            Jsonb(request_obj),
                            request_canonical,
                            timestamp,
                        ),
                    )
                    self._event(
                        cur,
                        workflow_id,
                        workflow["revision"],
                        "review.recorded",
                        {"approval_id": approval_id, "decision": request.decision},
                    )
                    if request.decision == "request_changes":
                        revision = workflow["revision"] + 1
                        cur.execute(
                            """UPDATE horus.workflows
                               SET revision = %s, status = 'queued', current_artifact_id = NULL, feedback = %s, updated_at = %s
                               WHERE id = %s""",
                            (revision, request.feedback, timestamp, workflow_id),
                        )
                        next_job = self._create_steps(cur, workflow_id, revision, workflow["executor"])
                        self._event(cur, workflow_id, revision, "workflow.revised", {"previous_revision": workflow["revision"]})
                    else:
                        cur.execute(
                            "UPDATE horus.workflows SET status = %s, updated_at = %s WHERE id = %s",
                            ("approved" if request.decision == "approve" else "rejected", timestamp, workflow_id),
                        )
                approval = self._approval(cur.execute("SELECT * FROM horus.approvals WHERE id = %s", (approval_id,)).fetchone())
        if next_job:
            self.queue.put_nowait(next_job)
        await self.notify()
        return {"approval": approval, "workflow": self.get(workflow_id)}
