"""Testes PostgreSQL para ScriptService (HOR-164 / I5)."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import threading
import uuid

import pytest
from psycopg.rows import dict_row

from horus_backend.config import Settings
from horus_backend.hermes_contract.models import RunEnvelope
from horus_backend.hermes_simulator import editorial_result
from horus_backend.retention import run_retention
from horus_backend.scripts import (
    HermesScriptRequest,
    ManualScriptRequest,
    ScriptRequest,
    ScriptReviewRequest,
    ScriptService,
    ScriptVersionRequest,
)
import horus_backend.security as security
from horus_backend.storage import DatabaseUnavailable, Store
from horus_backend.workflows import (
    RADAR_REDACTED,
    AuthorizationRequest,
    ReviewRequest,
    WorkflowConflict,
    WorkflowRequest,
    WorkflowService,
    digest,
    encode,
)
from schema_support import schema_database
from test_workflows_pg import _drain, _make_service as _make_workflow_service

FIXTURES = Path(__file__).resolve().parents[3] / "contracts/hermes/fixtures"
ENVELOPE = json.loads((FIXTURES / "envelope-ficticio.json").read_text(encoding="utf-8"))
SCRIPT_FIXTURE = json.loads((FIXTURES / "script-saida-valida.json").read_text(encoding="utf-8"))
HERMES_REQUEST = {name: ENVELOPE[name] for name in ("objective", "dataset", "channel_profile")}


@pytest.fixture(scope="module")
def _scripts_schema_db(pg_cluster):
    with schema_database(pg_cluster) as state:
        yield state


@pytest.fixture
def pg_pool(request, pg_cluster):
    if request.node.get_closest_marker("pg_strong"):
        with schema_database(pg_cluster) as (_, pool):
            yield pool
    else:
        admin, pool = request.getfixturevalue("_scripts_schema_db")
        try:
            yield pool
        finally:
            admin.execute(
                """TRUNCATE horus.workflows, horus.jobs, horus.job_attempts, horus.artifacts,
                            horus.approvals, horus.workflow_events, horus.cognitive_runs,
                            horus.authorizations, horus.human_checkpoints, horus.transmissions,
                            horus.events, horus.scripts, horus.script_receipts
                   RESTART IDENTITY CASCADE"""
            )


@pytest.fixture
def settings(tmp_path):
    profile = tmp_path / "profile"
    security.harden_profile(profile)
    connection = tmp_path / "connection.json"
    connection.write_text(json.dumps({"base_url": "http://127.0.0.1:8643", "token": "connection-ficticia-01234567890123"}))
    return Settings(
        token="scripts-pg-test-token-0123456789012345",
        data_dir=tmp_path / "data",
        factory_root=None,
        hermes_executable=None,
        hermes_profile=profile,
        hermes_config=connection,
        workflow_executor="simulated",
        max_tokens_per_run=10000,
        max_tokens_per_day=50000,
        max_runs_per_day=5,
        budget_total_usd=Decimal("10"),
        budget_daily_usd=Decimal("5"),
        price_input_usd_per_mtok=Decimal("10"),
        price_output_usd_per_mtok=Decimal("20"),
        hermes_poll_seconds=0.01,
        hermes_run_timeout=5,
    )


def _approved_workflow(store: Store, settings: Settings, *, key: str = "wf-1") -> dict:
    wf_service = _make_workflow_service(store, settings)

    async def run():
        wf = await wf_service.create(WorkflowRequest(**HERMES_REQUEST), key)
        await _drain(wf_service, store)
        wf = wf_service.get(wf["id"])
        await wf_service.review(
            wf["id"],
            ReviewRequest(expected_revision=1, artifact_id=wf["current_artifact_id"], decision="approve"),
            f"{key}-approve",
        )
        return wf_service.get(wf["id"])

    return asyncio.run(run())


def _approved_hermes_workflow(store: Store, settings: Settings, client, *, key: str = "wf-hermes") -> dict:
    wf_service = _make_workflow_service(store, settings, hermes_client=client)

    async def run():
        wf = await wf_service.create(WorkflowRequest(**HERMES_REQUEST), key, executor="hermes")
        await _drain(wf_service, store)
        waiting = wf_service.get(wf["id"])
        auth_job = waiting["pending_authorization"]["job_id"]
        await wf_service.authorize(wf["id"], AuthorizationRequest(expected_revision=1, job_id=auth_job), f"{key}-auth")
        await _drain(wf_service, store)
        done = wf_service.get(wf["id"])
        await wf_service.review(
            wf["id"],
            ReviewRequest(expected_revision=1, artifact_id=done["current_artifact_id"], decision="approve"),
            f"{key}-approve",
        )
        return wf_service.get(wf["id"])

    return asyncio.run(run())


def _manual_doc(title: str = "Roteiro manual", *, source_ids: list[str] | None = None) -> dict:
    return {
        "schema_version": 1,
        "simulated": False,
        "title": title,
        "sections": [
            {"id": "hook", "kind": "hook", "heading": "Abertura", "narration": "Abertura manual.", "source_ids": []},
            {"id": "body", "kind": "body", "heading": "Corpo", "narration": "Corpo manual.", "source_ids": source_ids or []},
            {"id": "conclusion", "kind": "conclusion", "heading": "Fim", "narration": "Fim manual.", "source_ids": []},
        ],
    }


def test_script_create_list_diff_review_and_idempotency(pg_pool, settings):
    store = Store(pg_pool, data_dir=settings.data_dir)
    wf = _approved_workflow(store, settings)
    scripts = ScriptService(store, settings)

    req1 = ScriptRequest(expected_revision=1, proposal_artifact_id=wf["current_artifact_id"])
    s1, created1 = scripts.create(wf["id"], req1, "gen-1")
    assert created1 is True
    assert s1["version"] == 1
    assert s1["status"] == "awaiting_review"
    assert s1["origin"] == "simulated"
    assert s1["authored_by"] == "simulated"

    # Replay idempotente
    s1_replay, created_replay = scripts.create(wf["id"], req1, "gen-1")
    assert created_replay is False
    assert s1_replay == s1

    # Conflito de chave de idempotência com outro pedido
    with pytest.raises(WorkflowConflict):
        scripts.create(wf["id"], ScriptRequest(expected_revision=2, proposal_artifact_id=wf["current_artifact_id"]), "gen-1")

    # Não permite gerar v2 antes de request_changes
    with pytest.raises(WorkflowConflict):
        scripts.create(wf["id"], req1, "gen-2")

    # Review request_changes
    reviewed = scripts.review(
        s1["id"],
        ScriptReviewRequest(expected_version=1, decision="request_changes", feedback="Ajustar abertura"),
        "rev-1",
    )
    assert reviewed["status"] == "changes_requested"
    assert reviewed["review"]["feedback"] == "Ajustar abertura"

    # Replay do review
    assert scripts.review(
        s1["id"],
        ScriptReviewRequest(expected_version=1, decision="request_changes", feedback="Ajustar abertura"),
        "rev-1",
    ) == reviewed

    # Gerar v2 após request_changes
    s2, created2 = scripts.create(wf["id"], req1, "gen-2")
    assert created2 is True
    assert s2["version"] == 2
    assert "Ajustar abertura" in s2["document"]["sections"][1]["narration"]

    # Revisão obsoleta na v1 é recusada
    with pytest.raises(WorkflowConflict):
        scripts.review(s1["id"], ScriptReviewRequest(expected_version=1, decision="approve"), "rev-v1-stale")

    # Aprovação da v2
    approved = scripts.review(s2["id"], ScriptReviewRequest(expected_version=2, decision="approve"), "rev-2")
    assert approved["status"] == "approved"

    # Listagem com diff_documents
    listed = scripts.list(wf["id"])["items"]
    assert [item["version"] for item in listed] == [2, 1]
    assert listed[0]["changes"]["previous_version"] == 1
    assert listed[1]["changes"] is None
    assert scripts.get(s2["id"])["status"] == "approved"


def test_manual_script_and_new_version_preserve_origin_and_canonical(pg_pool, settings):
    store = Store(pg_pool, data_dir=settings.data_dir)
    wf = _approved_workflow(store, settings, key="wf-man")
    scripts = ScriptService(store, settings)

    doc1 = _manual_doc("Título v1")
    s1, created = scripts.create(
        wf["id"],
        ManualScriptRequest(
            source="manual",
            expected_revision=1,
            proposal_artifact_id=wf["current_artifact_id"],
            document=doc1,
        ),
        "man-1",
    )
    assert created is True
    assert s1["origin"] == "human"
    assert s1["authored_by"] == "human"

    doc2 = _manual_doc("Título v2")
    s2, created2 = scripts.new_version(s1["id"], ScriptVersionRequest(expected_version=1, document=doc2), "ver-1")
    assert created2 is True
    assert s2["version"] == 2
    assert s2["origin"] == "human"

    # Versão obsoleta recusada
    with pytest.raises(WorkflowConflict):
        scripts.new_version(s1["id"], ScriptVersionRequest(expected_version=1, document=doc2), "ver-stale")

    # Aprovar v2 e verificar recusa de nova versão após aprovação
    s2 = scripts.review(s2["id"], ScriptReviewRequest(expected_version=2, decision="approve"), "rev-man")
    with pytest.raises(WorkflowConflict):
        scripts.new_version(s2["id"], ScriptVersionRequest(expected_version=2, document=_manual_doc("v3")), "ver-after-approve")

    # Verificar bytes canonical gravados no PG
    with pg_pool.connection() as conn:
        row = conn.execute("SELECT document_canonical, review_canonical FROM horus.scripts WHERE id = %s", (s2["id"],)).fetchone()
    assert bytes(row[0]) == encode(s2["document"]).encode("utf-8")
    assert bytes(row[1]) == encode(s2["review"]).encode("utf-8")


@pytest.mark.pg_strong
def test_competing_script_versions_have_one_winner(pg_pool, settings):
    store = Store(pg_pool, data_dir=settings.data_dir)
    wf = _approved_workflow(store, settings, key="wf-conc")
    scripts = ScriptService(store, settings)
    s1, _ = scripts.create(
        wf["id"],
        ManualScriptRequest(
            source="manual",
            expected_revision=1,
            proposal_artifact_id=wf["current_artifact_id"],
            document=_manual_doc("Inicial"),
        ),
        "init",
    )
    barrier = threading.Barrier(2)

    def worker(idx: int):
        barrier.wait(timeout=5)
        try:
            scripts.new_version(
                s1["id"],
                ScriptVersionRequest(expected_version=1, document=_manual_doc(f"Concorrente {idx}")),
                f"key-{idx}",
            )
            return "ok"
        except WorkflowConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, [1, 2]))
    assert sorted(results) == ["conflict", "ok"]
    assert len(scripts.list(wf["id"])["items"]) == 2


def test_hermes_script_create_and_reconcile(pg_pool, settings, monkeypatch):
    settings = replace(settings, workflow_executor="hermes")
    store = Store(pg_pool, data_dir=settings.data_dir)

    class FakeHermes:
        base_url = "http://127.0.0.1:8643"

        def __init__(self):
            self.calls = 0

        def submit(self, body, key):
            self.calls += 1
            self.last_body = body
            return {"run_id": f"remote-{self.calls}", "status": "queued"}

        def status(self, external_ref):
            if self.calls == 1:
                output = editorial_result(RunEnvelope.model_validate_json(self.last_body["input"]))
            else:
                env = json.loads(self.last_body["input"])
                output = {**SCRIPT_FIXTURE, "task_id": env["task_id"]}
            return {"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300}, "output": json.dumps(output)}

    client = FakeHermes()
    wf = _approved_hermes_workflow(store, settings, client, key="wf-hermes")
    import horus_backend.workflow_hermes as wh
    monkeypatch.setattr(wh, "HermesRunClient", lambda *a, **kw: client)
    scripts = ScriptService(store, settings)

    req = HermesScriptRequest(
        source="hermes",
        authorize=True,
        expected_revision=1,
        proposal_artifact_id=wf["current_artifact_id"],
    )
    rec, created = scripts.create_hermes(wf["id"], req, "hermes-1")
    assert created is True
    assert rec["origin"] == "hermes"
    assert rec["authored_by"] == "hermes"
    assert client.calls == 2

    # Replay idempotente não chama Hermes de novo
    rec_replay, created_replay = scripts.create_hermes(wf["id"], req, "hermes-1")
    assert created_replay is False
    assert rec_replay == rec
    assert client.calls == 2

    # Checkpoint humano foi consumido
    with pg_pool.connection() as conn:
        consumed = conn.execute("SELECT COUNT(*) FROM horus.human_checkpoints WHERE action = 'hermes_script' AND consumed_at IS NOT NULL").fetchone()[0]
    assert consumed == 1


def test_hermes_script_unknown_then_reconcile_without_resubmit(pg_pool, settings, monkeypatch):
    settings = replace(settings, workflow_executor="hermes")
    store = Store(pg_pool, data_dir=settings.data_dir)

    class UnknownThenDoneClient:
        base_url = "http://127.0.0.1:8643"

        def __init__(self):
            self.submits = 0
            self.fail_script_status = False

        def submit(self, body, key):
            self.submits += 1
            self.last_body = body
            return {"run_id": f"remote-{self.submits}", "status": "queued"}

        def status(self, external_ref):
            if self.submits == 1:
                output = editorial_result(RunEnvelope.model_validate_json(self.last_body["input"]))
                return {"status": "completed", "usage": {"input_tokens": 50, "output_tokens": 50, "total_tokens": 100}, "output": json.dumps(output)}
            if self.fail_script_status:
                raise TimeoutError("timeout simulado")
            env = json.loads(self.last_body["input"])
            output = {**SCRIPT_FIXTURE, "task_id": env["task_id"]}
            return {"status": "completed", "usage": {"input_tokens": 50, "output_tokens": 100, "total_tokens": 150}, "output": json.dumps(output)}

    client = UnknownThenDoneClient()
    wf = _approved_hermes_workflow(store, settings, client, key="wf-rec")
    client.fail_script_status = True
    import horus_backend.workflow_hermes as wh
    monkeypatch.setattr(wh, "HermesRunClient", lambda *a, **kw: client)
    scripts = ScriptService(store, settings)

    req = HermesScriptRequest(
        source="hermes",
        authorize=True,
        expected_revision=1,
        proposal_artifact_id=wf["current_artifact_id"],
    )
    with pytest.raises(WorkflowConflict):
        scripts.create_hermes(wf["id"], req, "hermes-unk")
    assert client.submits == 2

    # Nova tentativa com mesma chave é recusada (pendente)
    with pytest.raises(WorkflowConflict):
        scripts.create_hermes(wf["id"], req, "hermes-unk")

    # Conciliação recupera sem reenviar
    client.fail_script_status = False
    reconciled, created = scripts.reconcile_hermes(wf["id"], 1)
    assert created is True
    assert reconciled["origin"] == "hermes"
    assert client.submits == 2

    # Conciliação repetida devolve o roteiro aplicado
    again, created_again = scripts.reconcile_hermes(wf["id"], 1)
    assert created_again is False
    assert again == reconciled


def test_radar_redaction_blocks_simulated_script_and_request_changes(pg_pool, settings):
    store = Store(pg_pool, data_dir=settings.data_dir)
    wf = _approved_workflow(store, settings, key="wf-red")
    scripts = ScriptService(store, settings)

    s1, _ = scripts.create(wf["id"], ScriptRequest(expected_revision=1, proposal_artifact_id=wf["current_artifact_id"]), "pre-red")
    # Forçar expiração do Radar
    with pg_pool.connection() as conn:
        with conn.transaction():
            row = conn.execute("SELECT payload FROM horus.workflows WHERE id = %s", (wf["id"],)).fetchone()[0]
            row["radar"] = {"snapshot_id": "snap-1", "revision": 1, "expires_at": "2020-01-01T00:00:00Z"}
            row["dataset"] = {"snapshot_id": "snap-1", "revision": 1, "items": []}
            encoded = encode(row)
            conn.execute(
                "UPDATE horus.workflows SET payload = %s::jsonb, payload_canonical = %s WHERE id = %s",
                (encoded, encoded.encode("utf-8"), wf["id"]),
            )
    report = run_retention(pg_pool)
    assert report.redacted == 1

    with pytest.raises(WorkflowConflict, match="Radar"):
        scripts.review(s1["id"], ScriptReviewRequest(expected_version=1, decision="request_changes", feedback="Mudar"), "rev-red")

    # Aprovação final continua permitida
    approved = scripts.review(s1["id"], ScriptReviewRequest(expected_version=1, decision="approve"), "rev-ok")
    assert approved["status"] == "approved"


def test_scripts_unavailable_raises_503():
    scripts = ScriptService(Store(None))
    with pytest.raises(DatabaseUnavailable) as caught:
        scripts.get("a" * 32)
    assert caught.value.http_status == 503
