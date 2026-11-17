"""Roteiros locais simulados; conteúdo versionado e revisão humana transacionais."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import uuid
from typing import Literal

from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .script_contract import validate_script
from .storage import DatabaseUnavailable
from .workflows import RADAR_REDACTED, WorkflowConflict, WorkflowService, digest, encode, radar_redacted


class ScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    proposal_artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class HermesScriptRequest(ScriptRequest):
    source: Literal["hermes"]
    authorize: Literal[True]

    @field_validator("authorize", mode="before")
    @classmethod
    def explicit_authorization(cls, value):
        if value is not True:
            raise ValueError("Autorização explícita necessária.")
        return value


class ScriptReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1, strict=True)
    decision: Literal["approve", "reject", "request_changes"]
    feedback: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_feedback(self):
        if self.decision == "request_changes" and not self.feedback:
            raise ValueError("Descreva as alterações solicitadas.")
        return self


class ManualScriptRequest(BaseModel):
    """Primeiro roteiro escrito pelo usuário; mesmas exigências de proposta aprovada da geração simulada."""
    model_config = ConfigDict(extra="forbid")
    source: Literal["manual"]
    expected_revision: int = Field(ge=1, strict=True)
    proposal_artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    document: dict


class ScriptVersionRequest(BaseModel):
    """Nova versão com o texto do usuário. O documento é validado depois, pelo contrato, com erro sanitizado."""
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    document: dict


def diff_documents(previous, document):
    """Diferença de campos entre duas versões; só ids e nomes de campos, nunca o texto."""
    before = {section["id"]: section for section in previous["sections"]}
    after = {section["id"]: section for section in document["sections"]}
    changed = [{"id": key, "fields": [field for field in ("kind", "heading", "narration", "source_ids")
                                       if before[key][field] != after[key][field]]}
               for key in after if key in before and before[key] != after[key]]
    return {"title_changed": previous["title"] != document["title"],
            "sections_added": [key for key in after if key not in before],
            "sections_removed": [key for key in before if key not in after],
            "sections_changed": changed,
            "order_changed": [k for k in before if k in after] != [k for k in after if k in before]}


def simulate_script(proposal, feedback, allowed_source_ids):
    """Transformação fixa do snapshot, sem inferência ou acesso externo."""
    recommendation = proposal.get("recommendation") or {}
    premise = recommendation.get("premise") or proposal.get("text") or "Proposta editorial para revisão humana."
    return {"schema_version": 1, "simulated": True, "title": proposal["title"][:200], "sections": [
        {"id": "hook", "kind": "hook", "heading": "Abertura simulada",
         "narration": f"Roteiro simulado: {proposal['title']}", "source_ids": []},
        {"id": "body", "kind": "body", "heading": "Desenvolvimento simulado",
         "narration": f"Simulação baseada na proposta aprovada: {premise[:5000]}" + (
             f"\nAjustes da revisão anterior: {feedback.strip()}" if (feedback or "").strip() else ""),
         "source_ids": sorted(allowed_source_ids)[:50]},
        {"id": "conclusion", "kind": "conclusion", "heading": "Conclusão simulada",
         "narration": "Encerramento simulado. A aprovação deste roteiro não autoriza produção ou publicação.", "source_ids": []}]}


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


class ScriptService:
    def __init__(self, store, settings=None):
        self.store, self.settings = store, settings
        from .transmissions import TransmissionLog
        self.audit = TransmissionLog(store)

    def _uow(self, *, readonly: bool = False):
        if self.store is None or not hasattr(self.store, "uow"):
            raise DatabaseUnavailable("database_unavailable")
        return self.store.uow(readonly=readonly)

    def refuse_gate(self, workflow_id):
        self.audit.best_effort("gate_refusal", "script", "refused_gate", workflow_id=workflow_id, detail="security_gate")

    @staticmethod
    def _record(row):
        item = dict(row)
        item.pop("document_canonical", None)
        item.pop("review_canonical", None)
        item["document"] = _json_val(item["document"])
        item["review"] = _json_val(item["review"]) if item.get("review") is not None else None
        item["created_at"] = _iso(item.get("created_at"))
        generator = item.pop("generator", None)
        item["origin"] = "hermes" if generator == "hermes" else item.get("origin", "simulated")  # esquema anterior à v9
        item["authored_by"] = item["origin"]
        return item

    @staticmethod
    def _require(cur, script_id):
        row = cur.execute("SELECT * FROM horus.scripts WHERE id = %s", (script_id,)).fetchone()
        if row is None:
            raise KeyError(script_id)
        return row

    @staticmethod
    def _replay(cur, scope, key, request):
        row = cur.execute(
            "SELECT * FROM horus.script_receipts WHERE scope = %s AND idempotency_key = %s",
            (scope, key),
        ).fetchone()
        if row:
            response = _json_val(row["response"])
            if any(response.get(state) for state in ("pending", "failed")):
                raise WorkflowConflict("Execução já admitida; nova tentativa exige outra autorização.")
            if bytes(row["request_canonical"]) != request.encode("utf-8"):
                raise WorkflowConflict("Chave de idempotência já usada com outro pedido de roteiro.")
            return response

    @staticmethod
    def _receipt(cur, scope, key, request, record):
        resp_encoded = encode(record)
        cur.execute(
            """INSERT INTO horus.script_receipts(
                scope, idempotency_key, request, response, request_canonical, response_canonical
            ) VALUES(%s, %s, %s::jsonb, %s::jsonb, %s, %s)""",
            (scope, key, request, resp_encoded, request.encode("utf-8"), resp_encoded.encode("utf-8")),
        )

    @staticmethod
    def _artifact(cur, artifact_id, workflow_id, revision, stage):
        row = cur.execute(
            "SELECT * FROM horus.artifacts WHERE id = %s AND workflow_id = %s AND revision = %s AND stage = %s",
            (artifact_id, workflow_id, revision, stage),
        ).fetchone()
        if not row:
            raise WorkflowConflict("Snapshot aprovado ausente ou inválido.")
        try:
            content = _json_val(row["content"])
            if digest(content) != row["content_hash"]:
                raise ValueError()
        except (ValueError, TypeError):
            raise WorkflowConflict("Snapshot aprovado com integridade inválida.") from None
        return row, content

    def get(self, script_id):
        with self._uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                return self._record(self._require(cur, script_id))

    def list(self, workflow_id):
        with self._uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                WorkflowService._require(cur, workflow_id)
                rows = cur.execute(
                    "SELECT * FROM horus.scripts WHERE workflow_id = %s ORDER BY version DESC",
                    (workflow_id,),
                ).fetchall()
                items = [self._record(row) for row in rows]
                for index, item in enumerate(items):
                    older = items[index + 1] if index + 1 < len(items) else None
                    item["changes"] = ({"previous_version": older["version"], **diff_documents(older["document"], item["document"])}
                                       if older else None)
                return {"items": items}

    def _allowed_sources(self, cur, workflow_id, proposal, revision):
        # Proveniência de artefatos não é referência de fonte editorial.
        allowed = set()
        if proposal.get("schema_version") == 2:
            refs = proposal.get("source_artifact_ids", [])
            if len(refs) != 1:
                raise WorkflowConflict("Snapshot da análise inválido.")
            _, analysis = self._artifact(cur, refs[0], workflow_id, revision, "analysis")
            allowed = {source["id"] for source in analysis["result"]["sources"]}
        return allowed

    def _insert(self, cur, scope, key, encoded, workflow_id, revision, artifact, version, document, origin):
        script_id = uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc)
        doc_encoded = encode(document)
        origin_col = "simulated" if origin == "hermes" else origin
        generator_col = "hermes" if origin == "hermes" else None
        inserted = cur.execute(
            """INSERT INTO horus.scripts(
                id, workflow_id, workflow_revision, proposal_artifact_id, proposal_content_hash,
                version, status, document, created_at, review, origin, generator, document_canonical, review_canonical
            ) VALUES(%s, %s, %s, %s, %s, %s, 'awaiting_review', %s::jsonb, %s, NULL, %s, %s, %s, NULL)
            RETURNING *""",
            (
                script_id, workflow_id, revision, artifact["id"], artifact["content_hash"],
                version, doc_encoded, timestamp, origin_col, generator_col, doc_encoded.encode("utf-8"),
            ),
        ).fetchone()
        record = self._record(inserted)
        self._receipt(cur, scope, key, encoded, record)
        WorkflowService._event(cur, workflow_id, revision, "script.created",
                               {"script_id": script_id, "version": version, "origin": origin})
        return record

    def create(self, workflow_id, request: ScriptRequest | ManualScriptRequest, key, *, _prepare=False, _document=None):
        manual = isinstance(request, ManualScriptRequest)
        encoded, scope = encode(request.model_dump()), f"generate:{workflow_id}"
        encoded_bytes = encoded.encode("utf-8")
        with self._uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                workflow = WorkflowService._require(cur, workflow_id, for_update=True)
                if _document is not None:
                    cur.execute(
                        "DELETE FROM horus.script_receipts WHERE scope = %s AND idempotency_key = %s AND request_canonical = %s",
                        (scope, key, encoded_bytes),
                    )
                replay = self._replay(cur, scope, key, encoded)
                if replay is not None:
                    return replay, False
                redacted = radar_redacted(_json_val(workflow["payload"]))
                if (workflow["status"] != "approved" or workflow["revision"] != request.expected_revision
                        or workflow["current_artifact_id"] != request.proposal_artifact_id or (redacted and not manual)):
                    raise WorkflowConflict("Proposta aprovada e revisão atual são necessárias.")
                approval = cur.execute(
                    """SELECT 1 FROM horus.approvals
                       WHERE workflow_id = %s AND revision = %s AND artifact_id = %s
                         AND decision = 'approve' AND operator_id = 'local-operator' AND scope = 'editorial_proposal'""",
                    (workflow_id, request.expected_revision, request.proposal_artifact_id),
                ).fetchone()
                if not approval:
                    raise WorkflowConflict("Aprovação humana persistida necessária.")
                artifact, proposal = self._artifact(cur, request.proposal_artifact_id, workflow_id, request.expected_revision, "proposal")
                previous = cur.execute(
                    "SELECT * FROM horus.scripts WHERE workflow_id = %s ORDER BY version DESC LIMIT 1",
                    (workflow_id,),
                ).fetchone()
                if previous and previous["status"] != "changes_requested":
                    raise WorkflowConflict("O roteiro atual não permite nova geração.")
                if _prepare:
                    if workflow["cognitive_client"] != "live":
                        raise WorkflowConflict("Workflow com cliente cognitivo simulado não pode acionar o Hermes real.")
                    if proposal.get("schema_version") != 2 or not proposal.get("recommendation"):
                        raise WorkflowConflict("Proposta com recomendação e fontes validadas necessária para o Hermes.")
                    if cur.execute(
                        "SELECT 1 FROM horus.script_receipts WHERE scope = %s AND response = %s::jsonb",
                        (scope, encode({"pending": True})),
                    ).fetchone():
                        raise WorkflowConflict("Execução pendente; conciliação necessária.")
                    allowed = self._allowed_sources(cur, workflow_id, proposal, request.expected_revision)
                    _, analysis = self._artifact(cur, proposal["source_artifact_ids"][0], workflow_id, request.expected_revision, "analysis")
                    recommendation = proposal.get("recommendation") or {}
                    from .hermes_contract.script import ScriptRunEnvelope
                    job_id, attempt_id = uuid.uuid4().hex, uuid.uuid4().hex
                    timestamp = datetime.now(timezone.utc)
                    # Alias dentro deste envelope; o artefato real fica na autorização persistida.
                    envelope = ScriptRunEnvelope(task_id=job_id, attempt_id=attempt_id, revision=request.expected_revision,
                        proposal={"proposal_id": "P1", "title": proposal["title"],
                                  **{k: recommendation[k] for k in ("premise", "angle", "format")}},
                        allowed_sources=[{"source_id": source["id"], "title": source.get("title") or source["id"]}
                                         for source in analysis["result"]["sources"] if source["id"] in allowed],
                        constraints={"target_duration_seconds": 60 if recommendation["format"] == "short" else 600})
                    # O job precisa de workflow_id: o orçamento (execuções/tokens/USD) só soma cognitive_runs ligados a um
                    # workflow. Nasce 'interrupted' (desfecho honesto se o processo morrer em voo); o recover() dos workflows
                    # só toca tentativas 'running' e rebaixaria o workflow aprovado. create_hermes promove no final.
                    cur.execute(
                        """INSERT INTO horus.jobs(
                            id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, workflow_id, workflow_revision
                        ) VALUES(%s, %s, '{}'::jsonb, %s, 'interrupted', %s, %s, %s, %s)""",
                        (job_id, "script:" + attempt_id, b"{}", timestamp, timestamp, workflow_id, request.expected_revision),
                    )
                    input_artifacts = encode([artifact["id"]])
                    cur.execute(
                        """INSERT INTO horus.job_attempts(
                            id, job_id, number, status, executor, input_hash, input_artifacts, input_artifacts_canonical, started_at
                        ) VALUES(%s, %s, 1, 'interrupted', 'script.hermes', %s, %s::jsonb, %s, %s)""",
                        (attempt_id, job_id, digest(envelope.model_dump()), input_artifacts, input_artifacts.encode("utf-8"), timestamp),
                    )
                    cur.execute(
                        """INSERT INTO horus.authorizations(
                            id, workflow_id, revision, job_id, input_artifact_id, scope, max_runs,
                            operator_id, idempotency_key, request, request_canonical, consumed_attempt_id, created_at
                        ) VALUES(%s, %s, %s, %s, %s, 'cognitive_run', 1, 'local-operator', %s, %s::jsonb, %s, %s, %s)""",
                        (uuid.uuid4().hex, workflow_id, request.expected_revision, job_id, artifact["id"],
                         "script:" + key, encoded, encoded_bytes, attempt_id, timestamp),
                    )
                    # HOR-107: a autorização explícita do roteiro vira checkpoint humano registrado e consumido na mesma transação.
                    from .human_checkpoints import CheckpointService
                    checkpoints = CheckpointService(self.store, self.audit, settings=self.settings)
                    subject = digest(envelope.model_dump())
                    checkpoints.grant(action="hermes_script", subject=subject, description=f"Roteiro via Hermes do workflow {workflow_id[:12]}",
                                      key="script:" + key, db=conn)
                    checkpoints.consume(action="hermes_script", subject=subject, ref=attempt_id, db=conn)
                    self._receipt(cur, scope, key, encoded, {"pending": True})
                    return {"job_id": job_id, "workflow_id": workflow_id, "script_envelope": envelope.model_dump(mode="json")}, True
                if _document is not None:
                    document = validate_script(_document, self._allowed_sources(cur, workflow_id, proposal, request.expected_revision), "hermes")
                elif manual:
                    # Texto do usuário é dado: não é interpretado nem enviado a nenhum executor.
                    allowed = set() if redacted else self._allowed_sources(cur, workflow_id, proposal, request.expected_revision)
                    document = validate_script(request.document, allowed, "human")
                else:
                    allowed = self._allowed_sources(cur, workflow_id, proposal, request.expected_revision)
                    feedback = _json_val(previous["review"])["feedback"] if previous else ""
                    document = validate_script(simulate_script(proposal, feedback, allowed), allowed)
                version = previous["version"] + 1 if previous else 1
                record = self._insert(cur, scope, key, encoded, workflow_id, request.expected_revision, artifact, version,
                                      document.model_dump(mode="json"), "hermes" if _document is not None else "human" if manual else "simulated")
                return record, True

    def create_hermes(self, workflow_id, request, key):
        from .security import SecurityGate, require_secure
        from .workflow_hermes import HermesStages, CognitiveFailure, CognitiveUnknown
        if self.settings.workflow_executor != "hermes":
            raise WorkflowConflict("Executor Hermes não selecionado pelo operador.")
        try:
            require_secure(self.settings)
        except SecurityGate:
            self.refuse_gate(workflow_id)
            raise
        context, created = self.create(workflow_id, request, key, _prepare=True)
        if not created:
            return context, False
        attempt_id = context["script_envelope"]["attempt_id"]
        status, error, unknown = "failed", "Execução de roteiro interrompida.", True
        try:
            document = asyncio.run(HermesStages(self.store, self.settings).analysis(context, attempt_id, lambda: False))
            result = self.create(workflow_id, request, key, _document=document)
            status, error = "succeeded", None
            return result
        except (CognitiveFailure, CognitiveUnknown) as failure:
            error = str(failure)
            unknown = isinstance(failure, CognitiveUnknown)
            raise WorkflowConflict(error) from None
        finally:
            with self._uow() as conn:
                if status == "failed" and not unknown:
                    failed_resp = encode({"failed": True})
                    conn.execute(
                        "UPDATE horus.script_receipts SET response = %s::jsonb, response_canonical = %s WHERE scope = %s AND idempotency_key = %s",
                        (failed_resp, failed_resp.encode("utf-8"), f"generate:{workflow_id}", key),
                    )
                moment = datetime.now(timezone.utc)
                conn.execute(
                    "UPDATE horus.job_attempts SET status = %s, error = %s, finished_at = %s WHERE id = %s",
                    (status, error, moment, attempt_id),
                )
                conn.execute(
                    "UPDATE horus.jobs SET status = %s, error = %s, updated_at = %s WHERE id = %s",
                    (status, error, moment, context["job_id"]),
                )

    def reconcile_hermes(self, workflow_id, expected_revision):
        """Conciliação técnica: segue a MESMA execução remota registrada (mesma chave, mesmo corpo).

        Mesmo padrão de `WorkflowService.reconcile`/`HermesStages.analysis(resume_run)`: nunca cria nova
        execução nem nova reserva. Resultado válido vira roteiro; falha definitiva fecha o recibo (gasto já
        contabilizado pela reserva/uso) e só uma nova autorização humana (nova chave) libera outra tentativa;
        desfecho ainda desconhecido mantém o recibo pendente e pode ser conciliado de novo.
        """
        from .security import SecurityGate, require_secure
        from .workflow_hermes import HermesStages, CognitiveFailure, CognitiveUnknown
        if self.settings.workflow_executor != "hermes":
            raise WorkflowConflict("Executor Hermes não selecionado pelo operador.")
        scope = f"generate:{workflow_id}"
        with self._uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                workflow = WorkflowService._require(cur, workflow_id)
                if workflow["revision"] != expected_revision:
                    raise WorkflowConflict("Somente a revisão atual pode ser conciliada.")
                receipt = cur.execute(
                    "SELECT * FROM horus.script_receipts WHERE scope = %s AND response = %s::jsonb",
                    (scope, encode({"pending": True})),
                ).fetchone()
                if receipt is None:
                    done = cur.execute(
                        """SELECT * FROM horus.scripts
                           WHERE workflow_id = %s AND workflow_revision = %s AND generator = 'hermes'
                           ORDER BY version DESC LIMIT 1""",
                        (workflow_id, expected_revision),
                    ).fetchone()
                    if done:  # conciliação repetida: devolve o resultado já aplicado, sem tocar no Hermes
                        return self._record(done), False
                    raise WorkflowConflict("Não há execução de roteiro pendente nesta revisão.")
                key, encoded = receipt["idempotency_key"], _json_val(receipt["request"])
                run = cur.execute(
                    """SELECT r.* FROM horus.cognitive_runs r
                       JOIN horus.authorizations a ON a.job_id = r.job_id
                       WHERE a.workflow_id = %s AND a.idempotency_key = %s
                       ORDER BY r.created_at DESC LIMIT 1""",
                    (workflow_id, "script:" + key),
                ).fetchone()
                if run is None:
                    raise WorkflowConflict("Execução pendente sem registro de envio; intervenção técnica necessária.")
                run = dict(run)
        try:
            require_secure(self.settings)
        except SecurityGate:
            self.refuse_gate(workflow_id)
            raise
        request = HermesScriptRequest.model_validate(encoded)
        attempt_id, job_id = run["attempt_id"], run["job_id"]
        run_input = _json_val(_json_val(run["request"])["input"])
        context = {"script_envelope": run_input, "resume_run": run,
                   "job_id": job_id, "workflow_id": workflow_id, "revision": expected_revision}
        status, error, final = "failed", "Conciliação de roteiro interrompida.", False
        try:
            document = asyncio.run(HermesStages(self.store, self.settings).analysis(context, attempt_id, lambda: False))
            try:
                result = self.create(workflow_id, request, key, _document=document)
            except WorkflowConflict as conflict:
                # Resultado válido e já pago, mas o workflow não aceita mais o roteiro: fechar sem aplicar.
                error, final = f"Resultado não aplicável: {conflict}", True
                raise WorkflowConflict(error) from None
            status, error = "succeeded", None
            return result
        except CognitiveFailure as failure:
            error, final = str(failure), True
            raise WorkflowConflict(error) from None
        except CognitiveUnknown as failure:
            error = str(failure)
            raise WorkflowConflict(error) from None
        finally:
            with self._uow() as conn:
                if final and status == "failed":
                    failed_resp = encode({"failed": True})
                    conn.execute(
                        "UPDATE horus.script_receipts SET response = %s::jsonb, response_canonical = %s WHERE scope = %s AND idempotency_key = %s",
                        (failed_resp, failed_resp.encode("utf-8"), scope, key),
                    )
                moment = datetime.now(timezone.utc)
                conn.execute(
                    "UPDATE horus.job_attempts SET status = %s, error = %s, finished_at = %s WHERE id = %s",
                    (status, error, moment, attempt_id),
                )
                conn.execute(
                    "UPDATE horus.jobs SET status = %s, error = %s, updated_at = %s WHERE id = %s",
                    (status, error, moment, job_id),
                )

    def new_version(self, script_id, request: ScriptVersionRequest, key):
        """Versão N+1 escrita pelo usuário; nunca aprova: a decisão continua em `review`."""
        encoded, scope = encode(request.model_dump()), f"version:{script_id}"
        with self._uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                meta = cur.execute("SELECT workflow_id FROM horus.scripts WHERE id = %s", (script_id,)).fetchone()
                if meta is None:
                    raise KeyError(script_id)
                workflow = WorkflowService._require(cur, meta["workflow_id"], for_update=True)
                row = self._require(cur, script_id)
                replay = self._replay(cur, scope, key, encoded)
                if replay is not None:
                    return replay, False
                latest = cur.execute(
                    "SELECT MAX(version) AS latest FROM horus.scripts WHERE workflow_id = %s",
                    (row["workflow_id"],),
                ).fetchone()["latest"]
                if row["version"] != request.expected_version or row["version"] != latest:
                    raise WorkflowConflict("Versão obsoleta: edite a versão mais recente do roteiro.")
                if row["status"] not in ("awaiting_review", "changes_requested"):
                    raise WorkflowConflict("Roteiro aprovado ou rejeitado não aceita nova versão.")
                if (workflow["status"] != "approved" or workflow["revision"] != row["workflow_revision"]
                        or workflow["current_artifact_id"] != row["proposal_artifact_id"]):
                    raise WorkflowConflict("Proposta aprovada e revisão atual são necessárias.")
                artifact, proposal = self._artifact(cur, row["proposal_artifact_id"], row["workflow_id"], row["workflow_revision"], "proposal")
                allowed = (set() if radar_redacted(_json_val(workflow["payload"]))
                           else self._allowed_sources(cur, row["workflow_id"], proposal, row["workflow_revision"]))
                document = validate_script(request.document, allowed, "human")
                record = self._insert(cur, scope, key, encoded, row["workflow_id"], row["workflow_revision"], artifact,
                                      row["version"] + 1, document.model_dump(mode="json"), "human")
                return record, True

    def review(self, script_id, request: ScriptReviewRequest, key):
        encoded, scope = encode(request.model_dump()), f"review:{script_id}"
        with self._uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                meta = cur.execute("SELECT workflow_id FROM horus.scripts WHERE id = %s", (script_id,)).fetchone()
                if meta is None:
                    raise KeyError(script_id)
                workflow = WorkflowService._require(cur, meta["workflow_id"], for_update=True)
                row = self._require(cur, script_id)
                replay = self._replay(cur, scope, key, encoded)
                if replay is not None:
                    return replay
                if request.decision == "request_changes":
                    if radar_redacted(_json_val(workflow["payload"])):
                        raise WorkflowConflict(RADAR_REDACTED)
                latest = cur.execute(
                    "SELECT MAX(version) AS latest FROM horus.scripts WHERE workflow_id = %s",
                    (row["workflow_id"],),
                ).fetchone()["latest"]
                if row["status"] != "awaiting_review" or row["version"] != request.expected_version or row["version"] != latest:
                    raise WorkflowConflict("Versão obsoleta ou roteiro fora da revisão humana.")
                status = {"approve": "approved", "reject": "rejected", "request_changes": "changes_requested"}[request.decision]
                review = {"decision": request.decision, "feedback": request.feedback, "reviewed_at": datetime.now(timezone.utc).isoformat()}
                review_encoded = encode(review)
                updated = cur.execute(
                    "UPDATE horus.scripts SET status = %s, review = %s::jsonb, review_canonical = %s WHERE id = %s RETURNING *",
                    (status, review_encoded, review_encoded.encode("utf-8"), script_id),
                ).fetchone()
                record = self._record(updated)
                self._receipt(cur, scope, key, encoded, record)
                WorkflowService._event(cur, row["workflow_id"], row["workflow_revision"], "script.reviewed",
                    {"script_id": script_id, "version": row["version"], "decision": request.decision, "operator_id": "local-operator"})
                return record
