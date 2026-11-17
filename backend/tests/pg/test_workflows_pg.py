"""Testes PostgreSQL para WorkflowService e workflow_hermes (HOR-164 / I4)."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
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
import horus_backend.security as security
from horus_backend.storage import DatabaseUnavailable, Store, canonical_json_bytes
from horus_backend.workflows import (
    AuthorizationRequest,
    ManualWorkflowRequest,
    ReviewRequest,
    WorkflowConflict,
    WorkflowRequest,
    WorkflowService,
)
from horus_backend.workflow_hermes import HermesStages
from schema_support import schema_database
from test_storage_jobs import _make_pg_manager

FIXTURES = Path(__file__).resolve().parents[3] / "contracts/hermes/fixtures"
ENVELOPE = json.loads((FIXTURES / "envelope-ficticio.json").read_text(encoding="utf-8"))
HERMES_REQUEST = {name: ENVELOPE[name] for name in ("objective", "dataset", "channel_profile")}


@pytest.fixture(scope="module")
def _workflows_schema_db(pg_cluster):
    with schema_database(pg_cluster) as state:
        yield state


@pytest.fixture
def pg_pool(request, pg_cluster):
    if request.node.get_closest_marker("pg_strong"):
        with schema_database(pg_cluster) as (_, pool):
            yield pool
    else:
        admin, pool = request.getfixturevalue("_workflows_schema_db")
        try:
            yield pool
        finally:
            admin.execute(
                """TRUNCATE horus.workflows, horus.jobs, horus.job_attempts, horus.artifacts,
                            horus.approvals, horus.workflow_events, horus.cognitive_runs,
                            horus.authorizations, horus.human_checkpoints, horus.transmissions, horus.events
                   RESTART IDENTITY CASCADE"""
            )


@pytest.fixture
def settings(tmp_path):
    profile = tmp_path / "profile"
    security.harden_profile(profile)
    connection = tmp_path / "connection.json"
    connection.write_text(json.dumps({"base_url": "http://127.0.0.1:8643", "token": "connection-ficticia-01234567890123"}))
    return Settings(
        token="workflows-pg-test-token-0123456789012345",
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


def _make_service(store: Store, settings: Settings | None = None, *, hermes_client=None) -> WorkflowService:
    queue: asyncio.Queue = asyncio.Queue()

    async def noop():
        return None

    service = WorkflowService(store, queue, noop, settings=settings)
    if hermes_client is not None:
        service.hermes = HermesStages(store, settings, client_factory=lambda: hermes_client)
    return service


async def _drain(service: WorkflowService, store: Store, *, max_steps: int = 10) -> None:
    steps = 0
    while not service.queue.empty() and steps < max_steps:
        job_id = service.queue.get_nowait()
        job = store.get_job(job_id)
        if job:
            await service.run_step(job)
        steps += 1


def test_complete_simulated_workflow_and_idempotent_create_and_review(pg_pool, settings):
    store = Store(pg_pool)
    service = _make_service(store, settings)
    req = WorkflowRequest(objective="Explicar iluminação cinematográfica", references=["https://example.com/a"])

    async def scenario():
        wf = await service.create(req, "wf-k1")
        again = await service.create(req, "wf-k1")
        assert again["id"] == wf["id"]
        with pytest.raises(WorkflowConflict):
            await service.create(WorkflowRequest(objective="Outro objetivo"), "wf-k1")
        await _drain(service, store)
        done = service.get(wf["id"])
        assert done["status"] == "awaiting_approval"
        assert len(done["artifacts"]) == 4
        assert done["current_artifact_id"] == done["artifacts"][-1]["id"]
        rev = ReviewRequest(expected_revision=1, artifact_id=done["current_artifact_id"], decision="approve", feedback="ok")
        res = await service.review(wf["id"], rev, "rev-k1")
        assert res["workflow"]["status"] == "approved"
        assert (await service.review(wf["id"], rev, "rev-k1"))["approval"]["id"] == res["approval"]["id"]
        with pytest.raises(WorkflowConflict):
            await service.review(wf["id"], ReviewRequest(expected_revision=1, artifact_id=done["current_artifact_id"], decision="reject", feedback="não"), "rev-k1")

    asyncio.run(scenario())


def test_request_changes_creates_revision_and_rejects_obsolete_approval(pg_pool, settings):
    store = Store(pg_pool)
    service = _make_service(store, settings)

    async def scenario():
        wf = await service.create(WorkflowRequest(objective="Roteiro de áudio"), "wf-rev")
        await _drain(service, store)
        first_art = service.get(wf["id"])["current_artifact_id"]
        res = await service.review(
            wf["id"],
            ReviewRequest(expected_revision=1, artifact_id=first_art, decision="request_changes", feedback="Mais exemplos"),
            "r1",
        )
        assert res["workflow"]["revision"] == 2
        assert res["workflow"]["status"] == "queued"
        await _drain(service, store)
        v2 = service.get(wf["id"])
        assert v2["status"] == "awaiting_approval" and v2["revision"] == 2
        with pytest.raises(WorkflowConflict):
            await service.review(wf["id"], ReviewRequest(expected_revision=1, artifact_id=first_art, decision="approve"), "r2")
        final = await service.review(
            wf["id"],
            ReviewRequest(expected_revision=2, artifact_id=v2["current_artifact_id"], decision="reject", feedback="descartado"),
            "r3",
        )
        assert final["workflow"]["status"] == "rejected"

    asyncio.run(scenario())


def test_manual_workflow_creation_idempotency_and_conflict(pg_pool, settings):
    store = Store(pg_pool)
    service = _make_service(store, settings)
    req = ManualWorkflowRequest(title="Episódio 1", objective="Roteiro manual")
    created, is_new = service.create_manual(req, "m1")
    assert is_new is True and created["status"] == "approved"
    dup, is_new2 = service.create_manual(req, "m1")
    assert is_new2 is False and dup["workflow_id"] == created["workflow_id"]
    with pytest.raises(WorkflowConflict):
        service.create_manual(ManualWorkflowRequest(title="Outro"), "m1")
    wf = service.get(created["workflow_id"])
    assert wf["executor"] == "human" and wf["simulated"] is False and len(wf["approvals"]) == 1


def test_tampered_dependency_artifact_blocks_step(pg_pool, settings):
    store = Store(pg_pool)
    service = _make_service(store, settings)

    async def scenario():
        wf = await service.create(WorkflowRequest(objective="Integridade"), "wf-tamper")
        # Executa apenas etapa 1 (planning)
        job1 = store.get_job(service.queue.get_nowait())
        await service.run_step(job1)
        # Adultera conteúdo sem atualizar content_hash
        with store.uow() as conn:
            conn.execute(
                "UPDATE horus.artifacts SET content = %s::jsonb, content_canonical = %s WHERE workflow_id = %s",
                (json.dumps({"adulterado": True}), canonical_json_bytes({"adulterado": True}), wf["id"]),
            )
        job2 = store.get_job(service.queue.get_nowait())
        await service.run_step(job2)
        updated = service.get(wf["id"])
        assert updated["status"] == "failed"
        assert updated["steps"][1]["status"] == "blocked"

    asyncio.run(scenario())


def test_late_result_from_old_attempt_cannot_change_recovered_workflow(pg_pool, settings):
    store = Store(pg_pool)
    service = _make_service(store, settings)

    async def scenario():
        wf = await service.create(WorkflowRequest(objective="Recuperação"), "wf-rec")
        job_id = service.queue.get_nowait()
        attempt_id, _ = service._claim(job_id)
        service.recover()
        after_recover = service.get(wf["id"])
        assert after_recover["status"] == "interrupted"
        # Resultado tardio da tentativa antiga não deve alterar o estado já recuperado
        assert service._finish(job_id, attempt_id, "succeeded", content={"schema_version": 1, "simulated": True, "stage": "planning", "title": "t", "text": "x", "source_artifact_ids": []}) is None
        assert service.get(wf["id"])["status"] == "interrupted"

    asyncio.run(scenario())


@pytest.mark.pg_strong
def test_competing_reviews_have_exactly_one_winner(pg_pool, settings):
    store = Store(pg_pool)
    service = _make_service(store, settings)

    async def setup():
        wf = await service.create(WorkflowRequest(objective="Concorrência"), "wf-conc-rev")
        await _drain(service, store)
        return service.get(wf["id"])

    wf = asyncio.run(setup())
    art_id = wf["current_artifact_id"]
    barrier = threading.Barrier(4)
    outcomes = []
    lock = threading.Lock()

    def vote(i: int):
        s = _make_service(store, settings)
        barrier.wait()
        try:
            asyncio.run(s.review(wf["id"], ReviewRequest(expected_revision=1, artifact_id=art_id, decision="approve"), f"k-{i}"))
            with lock:
                outcomes.append("ok")
        except WorkflowConflict:
            with lock:
                outcomes.append("conflict")

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(vote, range(4)))
    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 3


def test_hermes_authorization_checkpoint_and_reconcile_without_resend(pg_pool, settings):
    class FakeHermes:
        base_url = "http://127.0.0.1:8643"

        def __init__(self):
            self.submits = 0
            self.fail_status = True

        def submit(self, body, key):
            self.submits += 1
            self.last_body = body
            return {"run_id": "run_fake_1", "status": "queued"}

        def status(self, ref):
            if self.fail_status:
                raise TimeoutError("rede caiu")
            result = editorial_result(RunEnvelope.model_validate_json(self.last_body["input"]))
            return {"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}, "output": json.dumps(result)}

    fake = FakeHermes()
    store = Store(pg_pool)
    service = _make_service(store, settings, hermes_client=fake)
    req = WorkflowRequest(**HERMES_REQUEST)

    async def scenario():
        wf = await service.create(req, "wf-hermes-1", executor="hermes")
        await _drain(service, store)  # planning + research -> analysis awaiting_authorization
        waiting = service.get(wf["id"])
        assert waiting["pending_authorization"]["stage"] == "analysis"
        auth_job = waiting["pending_authorization"]["job_id"]

        # Autoriza e executa: status falha -> marca unknown e interrompe
        await service.authorize(wf["id"], AuthorizationRequest(expected_revision=1, job_id=auth_job), "auth-1")
        await _drain(service, store)
        unknown_wf = service.get(wf["id"])
        assert unknown_wf["status"] == "interrupted"
        assert unknown_wf["cognitive_runs"][0]["status"] == "unknown"
        assert fake.submits == 1

        # retry deve ser bloqueado enquanto houver run unknown
        with pytest.raises(WorkflowConflict, match="desconhecido"):
            await service.retry(wf["id"], 1)

        # reconcile retoma o MESMO run_id sem reenviar submit!
        fake.fail_status = False
        await service.reconcile(wf["id"], 1)
        await _drain(service, store)
        done = service.get(wf["id"])
        assert done["status"] == "awaiting_approval"
        assert fake.submits == 1

    asyncio.run(scenario())


def test_workflows_raises_503_when_database_unavailable(pg_cluster, tmp_path, monkeypatch, settings):
    with schema_database(pg_cluster) as (_, pool):
        with pool.connection() as conn:
            db_name = conn.info.dbname
        unconfigured = _make_pg_manager(pg_cluster, db_name, tmp_path / "unconfigured", monkeypatch, configure=False)
        service = _make_service(Store(unconfigured), settings)
        with pytest.raises(DatabaseUnavailable) as err:
            service.list()
        assert err.value.http_status == 503
        with pytest.raises(DatabaseUnavailable):
            asyncio.run(service.create(WorkflowRequest(objective="x"), "k-503"))
