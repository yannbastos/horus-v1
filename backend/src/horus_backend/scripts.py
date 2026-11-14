"""Roteiros locais simulados; conteúdo versionado e revisão humana transacionais."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .script_contract import validate_script
from .storage import now
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


class ScriptService:
    def __init__(self, store, settings=None):
        self.store, self.settings = store, settings

    @staticmethod
    def _record(row):
        item = dict(row)
        item["document"] = json.loads(item["document"])
        item["review"] = json.loads(item["review"]) if item["review"] else None
        generator = item.pop("generator", None)
        item["origin"] = "hermes" if generator == "hermes" else item.get("origin", "simulated")  # esquema anterior à v9
        item["authored_by"] = item["origin"]
        return item

    @staticmethod
    def _require(db, script_id):
        row = db.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
        if row is None:
            raise KeyError(script_id)
        return row

    @staticmethod
    def _replay(db, scope, key, request):
        row = db.execute("SELECT * FROM script_receipts WHERE scope=? AND idempotency_key=?", (scope, key)).fetchone()
        if row:
            if any(json.loads(row["response"]).get(state) for state in ("pending", "failed")):
                raise WorkflowConflict("Execução já admitida; nova tentativa exige outra autorização.")
            if row["request"] != request:
                raise WorkflowConflict("Chave de idempotência já usada com outro pedido de roteiro.")
            return json.loads(row["response"])

    @staticmethod
    def _receipt(db, scope, key, request, record):
        db.execute("INSERT INTO script_receipts VALUES(?,?,?,?)", (scope, key, request, encode(record)))

    @staticmethod
    def _artifact(db, artifact_id, workflow_id, revision, stage):
        row = db.execute("SELECT * FROM artifacts WHERE id=? AND workflow_id=? AND revision=? AND stage=?",
                         (artifact_id, workflow_id, revision, stage)).fetchone()
        if not row:
            raise WorkflowConflict("Snapshot aprovado ausente ou inválido.")
        try:
            content = json.loads(row["content"])
            if digest(content) != row["content_hash"]:
                raise ValueError()
        except (ValueError, TypeError):
            raise WorkflowConflict("Snapshot aprovado com integridade inválida.") from None
        return row, content

    def get(self, script_id):
        with self.store.connect() as db:
            return self._record(self._require(db, script_id))

    def list(self, workflow_id):
        with self.store.connect() as db:
            db.execute("BEGIN")
            WorkflowService._require(db, workflow_id)
            items = [self._record(row) for row in db.execute(
                "SELECT * FROM scripts WHERE workflow_id=? ORDER BY version DESC", (workflow_id,))]
            for index, item in enumerate(items):
                older = items[index + 1] if index + 1 < len(items) else None
                item["changes"] = ({"previous_version": older["version"], **diff_documents(older["document"], item["document"])}
                                   if older else None)
            return {"items": items}

    def _allowed_sources(self, db, workflow_id, proposal, revision):
        # Proveniência de artefatos não é referência de fonte editorial.
        allowed = set()
        if proposal.get("schema_version") == 2:
            refs = proposal.get("source_artifact_ids", [])
            if len(refs) != 1:
                raise WorkflowConflict("Snapshot da análise inválido.")
            _, analysis = self._artifact(db, refs[0], workflow_id, revision, "analysis")
            allowed = {source["id"] for source in analysis["result"]["sources"]}
        return allowed

    def _insert(self, db, scope, key, encoded, workflow_id, revision, artifact, version, document, origin):
        script_id, timestamp = uuid.uuid4().hex, now()
        # Simulado usa o INSERT legado (origin assume o padrão), compatível com bancos de esquema anterior.
        db.execute("""INSERT INTO scripts(id,workflow_id,workflow_revision,proposal_artifact_id,proposal_content_hash,
            version,status,document,created_at,review) VALUES(?,?,?,?,?,?, 'awaiting_review',?,?,NULL)""",
            (script_id, workflow_id, revision, artifact["id"], artifact["content_hash"], version, encode(document), timestamp))
        if origin == "hermes":
            # CHECK v9 preservado: generator é a proveniência efetiva projetada por _record.
            db.execute("UPDATE scripts SET generator='hermes' WHERE id=?", (script_id,))
        elif origin != "simulated":
            db.execute("UPDATE scripts SET origin=? WHERE id=?", (origin, script_id))
        record = self._record(self._require(db, script_id))
        self._receipt(db, scope, key, encoded, record)
        WorkflowService._event(db, workflow_id, revision, "script.created",
                               {"script_id": script_id, "version": version, "origin": origin})
        return record

    def create(self, workflow_id, request: ScriptRequest | ManualScriptRequest, key, *, _prepare=False, _document=None):
        manual = isinstance(request, ManualScriptRequest)
        encoded, scope = encode(request.model_dump()), f"generate:{workflow_id}"
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            workflow = WorkflowService._require(db, workflow_id)
            if _document is not None:
                db.execute("DELETE FROM script_receipts WHERE scope=? AND idempotency_key=? AND request=?",
                           (scope, key, encoded))
            replay = self._replay(db, scope, key, encoded)
            if replay is not None:
                return replay, False
            redacted = radar_redacted(json.loads(workflow["payload"]))
            if (workflow["status"] != "approved" or workflow["revision"] != request.expected_revision
                    or workflow["current_artifact_id"] != request.proposal_artifact_id or (redacted and not manual)):
                raise WorkflowConflict("Proposta aprovada e revisão atual são necessárias.")
            approval = db.execute("""SELECT 1 FROM approvals WHERE workflow_id=? AND revision=? AND artifact_id=?
                AND decision='approve' AND operator_id='local-operator' AND scope='editorial_proposal'""",
                (workflow_id, request.expected_revision, request.proposal_artifact_id)).fetchone()
            if not approval:
                raise WorkflowConflict("Aprovação humana persistida necessária.")
            artifact, proposal = self._artifact(db, request.proposal_artifact_id, workflow_id, request.expected_revision, "proposal")
            previous = db.execute("SELECT * FROM scripts WHERE workflow_id=? ORDER BY version DESC LIMIT 1", (workflow_id,)).fetchone()
            if previous and previous["status"] != "changes_requested":
                raise WorkflowConflict("O roteiro atual não permite nova geração.")
            if _prepare:
                if workflow["cognitive_client"] != "live":
                    raise WorkflowConflict("Workflow com cliente cognitivo simulado não pode acionar o Hermes real.")
                if proposal.get("schema_version") != 2 or not proposal.get("recommendation"):
                    raise WorkflowConflict("Proposta com recomendação e fontes validadas necessária para o Hermes.")
                if db.execute("SELECT 1 FROM script_receipts WHERE scope=? AND response=?", (scope, encode({"pending": True}))).fetchone():
                    raise WorkflowConflict("Execução pendente; conciliação necessária.")
                allowed = self._allowed_sources(db, workflow_id, proposal, request.expected_revision)
                _, analysis = self._artifact(db, proposal["source_artifact_ids"][0], workflow_id, request.expected_revision, "analysis")
                recommendation = proposal.get("recommendation") or {}
                from .hermes_contract.script import ScriptRunEnvelope
                job_id, attempt_id, timestamp = uuid.uuid4().hex, uuid.uuid4().hex, now()
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
                db.execute("""INSERT INTO jobs(id,idempotency_key,payload,status,created_at,updated_at,workflow_id,workflow_revision)
                    VALUES(?,?,?,'interrupted',?,?,?,?)""",
                           (job_id, "script:" + attempt_id, '{}', timestamp, timestamp, workflow_id, request.expected_revision))
                db.execute("""INSERT INTO job_attempts(id,job_id,number,status,executor,input_hash,input_artifacts,started_at)
                    VALUES(?,?,1,'interrupted','script.hermes',?,?,?)""", (attempt_id, job_id, digest(envelope.model_dump()), encode([artifact["id"]]), timestamp))
                db.execute("""INSERT INTO authorizations(id,workflow_id,revision,job_id,input_artifact_id,scope,max_runs,
                    operator_id,idempotency_key,request,consumed_attempt_id,created_at) VALUES(?,?,?,?,?,'cognitive_run',1,'local-operator',?,?,?,?)""",
                    (uuid.uuid4().hex, workflow_id, request.expected_revision, job_id, artifact["id"], "script:" + key, encoded, attempt_id, timestamp))
                self._receipt(db, scope, key, encoded, {"pending": True})
                return {"job_id": job_id, "workflow_id": workflow_id, "script_envelope": envelope.model_dump(mode="json")}, True
            if _document is not None:
                document = validate_script(_document, self._allowed_sources(db, workflow_id, proposal, request.expected_revision), "hermes")
            elif manual:
                # Texto do usuário é dado: não é interpretado nem enviado a nenhum executor.
                allowed = set() if redacted else self._allowed_sources(db, workflow_id, proposal, request.expected_revision)
                document = validate_script(request.document, allowed, "human")
            else:
                allowed = self._allowed_sources(db, workflow_id, proposal, request.expected_revision)
                feedback = json.loads(previous["review"])["feedback"] if previous else ""
                document = validate_script(simulate_script(proposal, feedback, allowed), allowed)
            version = previous["version"] + 1 if previous else 1
            record = self._insert(db, scope, key, encoded, workflow_id, request.expected_revision, artifact, version,
                                  document.model_dump(mode="json"), "hermes" if _document is not None else "human" if manual else "simulated")
            return record, True

    def create_hermes(self, workflow_id, request, key):
        from .security import require_secure
        from .workflow_hermes import HermesStages, CognitiveFailure, CognitiveUnknown
        if self.settings.workflow_executor != "hermes":
            raise WorkflowConflict("Executor Hermes não selecionado pelo operador.")
        require_secure(self.settings)
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
            with self.store.connect() as db:
                if status == "failed" and not unknown:
                    db.execute("UPDATE script_receipts SET response=? WHERE scope=? AND idempotency_key=?",
                               (encode({"failed": True}), f"generate:{workflow_id}", key))
                db.execute("UPDATE job_attempts SET status=?,error=?,finished_at=? WHERE id=?", (status, error, now(), attempt_id))
                db.execute("UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?", (status, error, now(), context["job_id"]))

    def reconcile_hermes(self, workflow_id, expected_revision):
        """Conciliação técnica: segue a MESMA execução remota registrada (mesma chave, mesmo corpo).

        Mesmo padrão de `WorkflowService.reconcile`/`HermesStages.analysis(resume_run)`: nunca cria nova
        execução nem nova reserva. Resultado válido vira roteiro; falha definitiva fecha o recibo (gasto já
        contabilizado pela reserva/uso) e só uma nova autorização humana (nova chave) libera outra tentativa;
        desfecho ainda desconhecido mantém o recibo pendente e pode ser conciliado de novo.
        """
        from .security import require_secure
        from .workflow_hermes import HermesStages, CognitiveFailure, CognitiveUnknown
        if self.settings.workflow_executor != "hermes":
            raise WorkflowConflict("Executor Hermes não selecionado pelo operador.")
        scope = f"generate:{workflow_id}"
        with self.store.connect() as db:
            workflow = WorkflowService._require(db, workflow_id)
            if workflow["revision"] != expected_revision:
                raise WorkflowConflict("Somente a revisão atual pode ser conciliada.")
            receipt = db.execute("SELECT * FROM script_receipts WHERE scope=? AND response=?",
                                 (scope, encode({"pending": True}))).fetchone()
            if receipt is None:
                done = db.execute("""SELECT * FROM scripts WHERE workflow_id=? AND workflow_revision=? AND generator='hermes'
                    ORDER BY version DESC LIMIT 1""", (workflow_id, expected_revision)).fetchone()
                if done:  # conciliação repetida: devolve o resultado já aplicado, sem tocar no Hermes
                    return self._record(done), False
                raise WorkflowConflict("Não há execução de roteiro pendente nesta revisão.")
            key, encoded = receipt["idempotency_key"], receipt["request"]
            run = db.execute("""SELECT r.* FROM cognitive_runs r JOIN authorizations a ON a.job_id=r.job_id
                WHERE a.workflow_id=? AND a.idempotency_key=? ORDER BY r.created_at DESC LIMIT 1""",
                             (workflow_id, "script:" + key)).fetchone()
            if run is None:
                raise WorkflowConflict("Execução pendente sem registro de envio; intervenção técnica necessária.")
            run = dict(run)
        require_secure(self.settings)
        request = HermesScriptRequest.model_validate(json.loads(encoded))
        attempt_id, job_id = run["attempt_id"], run["job_id"]
        context = {"script_envelope": json.loads(json.loads(run["request"])["input"]), "resume_run": run,
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
            with self.store.connect() as db:
                if final and status == "failed":
                    db.execute("UPDATE script_receipts SET response=? WHERE scope=? AND idempotency_key=?",
                               (encode({"failed": True}), scope, key))
                db.execute("UPDATE job_attempts SET status=?,error=?,finished_at=? WHERE id=?", (status, error, now(), attempt_id))
                db.execute("UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?", (status, error, now(), job_id))

    def new_version(self, script_id, request: ScriptVersionRequest, key):
        """Versão N+1 escrita pelo usuário; nunca aprova: a decisão continua em `review`."""
        encoded, scope = encode(request.model_dump()), f"version:{script_id}"
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._require(db, script_id)
            replay = self._replay(db, scope, key, encoded)
            if replay is not None:
                return replay, False
            workflow = WorkflowService._require(db, row["workflow_id"])
            latest = db.execute("SELECT MAX(version) FROM scripts WHERE workflow_id=?", (row["workflow_id"],)).fetchone()[0]
            if row["version"] != request.expected_version or row["version"] != latest:
                raise WorkflowConflict("Versão obsoleta: edite a versão mais recente do roteiro.")
            if row["status"] not in ("awaiting_review", "changes_requested"):
                raise WorkflowConflict("Roteiro aprovado ou rejeitado não aceita nova versão.")
            if (workflow["status"] != "approved" or workflow["revision"] != row["workflow_revision"]
                    or workflow["current_artifact_id"] != row["proposal_artifact_id"]):
                raise WorkflowConflict("Proposta aprovada e revisão atual são necessárias.")
            artifact, proposal = self._artifact(db, row["proposal_artifact_id"], row["workflow_id"], row["workflow_revision"], "proposal")
            allowed = (set() if radar_redacted(json.loads(workflow["payload"]))
                       else self._allowed_sources(db, row["workflow_id"], proposal, row["workflow_revision"]))
            document = validate_script(request.document, allowed, "human")
            record = self._insert(db, scope, key, encoded, row["workflow_id"], row["workflow_revision"], artifact,
                                  row["version"] + 1, document.model_dump(mode="json"), "human")
            return record, True

    def review(self, script_id, request: ScriptReviewRequest, key):
        encoded, scope = encode(request.model_dump()), f"review:{script_id}"
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._require(db, script_id)
            replay = self._replay(db, scope, key, encoded)
            if replay is not None:
                return replay
            if request.decision == "request_changes":
                workflow = WorkflowService._require(db, row["workflow_id"])
                if radar_redacted(json.loads(workflow["payload"])):
                    raise WorkflowConflict(RADAR_REDACTED)
            latest = db.execute("SELECT MAX(version) FROM scripts WHERE workflow_id=?", (row["workflow_id"],)).fetchone()[0]
            if row["status"] != "awaiting_review" or row["version"] != request.expected_version or row["version"] != latest:
                raise WorkflowConflict("Versão obsoleta ou roteiro fora da revisão humana.")
            status = {"approve": "approved", "reject": "rejected", "request_changes": "changes_requested"}[request.decision]
            review = {"decision": request.decision, "feedback": request.feedback, "reviewed_at": now()}
            db.execute("UPDATE scripts SET status=?,review=? WHERE id=?", (status, encode(review), script_id))
            record = self._record(self._require(db, script_id))
            self._receipt(db, scope, key, encoded, record)
            WorkflowService._event(db, row["workflow_id"], row["workflow_revision"], "script.reviewed",
                {"script_id": script_id, "version": row["version"], "decision": request.decision, "operator_id": "local-operator"})
            return record
