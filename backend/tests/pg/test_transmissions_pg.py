"""Testes PostgreSQL para transmissions (HOR-164 / I4): append-only, hash sem conteúdo, paginação e bloqueio."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import hashlib
import json
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from horus_backend.config import Settings
from horus_backend.storage import DatabaseUnavailable, Store, canonical_json_bytes
from horus_backend.transmissions import TransmissionLog
from horus_backend.workflow_hermes import CognitiveFailure, HermesStages
from schema_support import schema_database
from test_storage_jobs import _make_pg_manager

SECRET = "SEGREDO-MARCADOR-8f3a1c-NAO-VAZAR"


@pytest.fixture(scope="module")
def _transmissions_schema_db(pg_cluster):
    with schema_database(pg_cluster) as state:
        yield state


@pytest.fixture
def pg_pool(request, pg_cluster):
    admin, pool = request.getfixturevalue("_transmissions_schema_db")
    try:
        yield pool
    finally:
        admin.execute(
            "TRUNCATE horus.transmissions, horus.authorizations, horus.cognitive_runs, horus.job_attempts, horus.jobs, horus.workflows RESTART IDENTITY CASCADE"
        )


def test_record_hashes_envelope_without_storing_content_and_paginates(pg_pool):
    store = Store(pg_pool)
    log = TransmissionLog(store)
    envelope = json.dumps({"objective": f"Analisar {SECRET}", "data": [1, 2, 3]})
    log.record("submit", "analysis", "attempted", workflow_id="wf1", job_id="job1", attempt_id="att1",
               run_id="run1", destination="http://127.0.0.1:8643", envelope=envelope, detail="ok_1")
    log.record("submit", "analysis", "sent", workflow_id="wf1", job_id="job1", attempt_id="att1",
               run_id="run1", destination="http://127.0.0.1:8643", envelope=envelope, detail="detalhe inválido!")
    log.record("outcome", "analysis", "completed", workflow_id="wf1", job_id="job1", attempt_id="att1",
               run_id="run1", destination="http://127.0.0.1:8643", tokens_total=2000, cost_usd=Decimal("0.003"))

    p1 = log.page(limit=2)
    assert len(p1["items"]) == 2
    assert p1["next_after"] == p1["items"][-1]["seq"]
    p2 = log.page(limit=2, after=p1["next_after"])
    assert len(p2["items"]) == 1 and p2["next_after"] is None

    items = p1["items"] + p2["items"]
    assert [(i["kind"], i["result"]) for i in items] == [
        ("submit", "attempted"),
        ("submit", "sent"),
        ("outcome", "completed"),
    ]
    assert items[0]["envelope_bytes"] == len(envelope.encode("utf-8"))
    assert items[0]["envelope_sha256"] == hashlib.sha256(envelope.encode("utf-8")).hexdigest()
    assert items[1]["detail"] == "detail_redacted"
    assert items[2]["tokens_total"] == 2000 and Decimal(items[2]["cost_usd"]) == Decimal("0.003")
    assert SECRET not in json.dumps(items)


def test_transmissions_are_append_only_and_validate_constraints(pg_pool):
    store = Store(pg_pool)
    log = TransmissionLog(store)
    log.record("submit", "analysis", "sent", workflow_id="wf1", run_id="r1")

    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation)):
        with store.uow() as conn:
            conn.execute("UPDATE horus.transmissions SET result = 'completed'")
    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation)):
        with store.uow() as conn:
            conn.execute("DELETE FROM horus.transmissions")
    with pytest.raises(psycopg.errors.CheckViolation):
        with store.uow() as conn:
            conn.execute(
                """INSERT INTO horus.transmissions(id, at, kind, stage, result, envelope_sha256)
                   VALUES ('x', now(), 'submit', 'a', 'sent', 'curto')"""
            )
    assert len(log.page()["items"]) == 1


def test_strict_audit_failure_blocks_hermes_submission(pg_pool, tmp_path):
    store = Store(pg_pool)
    # Inserir contexto mínimo de workflow/job/attempt
    wf_id, job_id, att_id = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    with store.uow() as conn:
        conn.execute(
            """INSERT INTO horus.workflows(id, idempotency_key, payload, payload_canonical, revision, status, created_at, updated_at, executor, cognitive_client)
               VALUES (%s, %s, '{}'::jsonb, %s, 1, 'running', now(), now(), 'hermes', 'live')""",
            (wf_id, wf_id, canonical_json_bytes({})),
        )
        conn.execute(
            """INSERT INTO horus.jobs(id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, workflow_id)
               VALUES (%s, %s, '{}'::jsonb, %s, 'running', now(), now(), %s)""",
            (job_id, job_id, canonical_json_bytes({}), wf_id),
        )
        conn.execute(
            """INSERT INTO horus.job_attempts(id, job_id, number, status, executor, input_hash, input_artifacts, input_artifacts_canonical, started_at)
               VALUES (%s, %s, 1, 'running', 'hermes-v1', 'h', '[]'::jsonb, %s, now())""",
            (att_id, job_id, canonical_json_bytes([])),
        )

    calls = []

    class FakeClient:
        base_url = "http://127.0.0.1:8643"

        def submit(self, body, key):
            calls.append((body, key))
            return {"run_id": "fake_1", "status": "queued"}

    settings = Settings(
        token="t" * 32,
        data_dir=tmp_path,
        max_tokens_per_run=10000,
        max_tokens_per_day=20000,
        max_runs_per_day=5,
        budget_total_usd=Decimal("10"),
        budget_daily_usd=Decimal("5"),
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
    )
    stages = HermesStages(store, settings, client_factory=lambda: FakeClient())
    # Simular falha na auditoria estrita da intenção antes do envio
    stages.audit.record = lambda *a, **kw: (_ for _ in ()).throw(DatabaseUnavailable("database_unavailable"))

    from pathlib import Path
    fixtures = Path(__file__).resolve().parents[3] / "contracts/hermes/fixtures"
    envelope = json.loads((fixtures / "envelope-ficticio.json").read_text(encoding="utf-8"))
    ctx = {
        "workflow_id": wf_id,
        "job_id": job_id,
        "revision": 1,
        "objective": envelope["objective"],
        "cognitive_client": "live",
        "sources": [{"id": "snap", "content": {"dataset": envelope["dataset"], "channel_profile": envelope["channel_profile"]}}],
    }

    with pytest.raises(CognitiveFailure, match="Auditoria de transmissões indisponível"):
        asyncio.run(stages.analysis(ctx, att_id, lambda: False))
    assert calls == []
    with store.uow(readonly=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            run = cur.execute("SELECT status, error FROM horus.cognitive_runs WHERE job_id = %s", (job_id,)).fetchone()
    assert run["status"] == "failed"


def test_transmissions_raises_503_when_unavailable_and_best_effort_swallows(pg_cluster, tmp_path, monkeypatch):
    with schema_database(pg_cluster) as (_, pool):
        with pool.connection() as conn:
            db_name = conn.info.dbname
        unconfigured = _make_pg_manager(pg_cluster, db_name, tmp_path / "unconfigured", monkeypatch, configure=False)
        log = TransmissionLog(Store(unconfigured))
        with pytest.raises(DatabaseUnavailable) as err:
            log.record("submit", "analysis", "attempted")
        assert err.value.http_status == 503
        assert err.value.code == "database_unavailable"
        # best_effort não derruba o fluxo
        log.best_effort("gate_refusal", "checkpoint:publish", "refused_gate", detail="checkpoint_missing")
        with pytest.raises(DatabaseUnavailable):
            log.page()
