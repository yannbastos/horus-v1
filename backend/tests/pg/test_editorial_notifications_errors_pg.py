"""Testes PostgreSQL para NotificationService e ErrorCenter (HOR-164 / I7)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.database.manager import DatabaseManager
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from horus_backend.error_center import ErrorCenter
from horus_backend.notifications import NotificationService
from horus_backend.storage import DatabaseUnavailable, Store, canonical_json_bytes
from schema_support import apply_chain
from test_database_contract import CONFIG, MemoryKeyring


def _make_pg_manager(pg_cluster, database: str, tmp_path: Path, monkeypatch, *, configure: bool = True) -> DatabaseManager:
    vault = ConnectionVault(tmp_path, MemoryKeyring())
    if configure:
        vault.write(ConnectionRequest.model_validate(CONFIG))
    manager = DatabaseManager(vault)

    def local_type(config):
        class LocalConnection(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo="", **kwargs):
                return super().connect(
                    pg_cluster.dsn(database, "horus_app"),
                    autocommit=True,
                    password=config.password.get_secret_value(),
                    prepare_threshold=None,
                )

        return LocalConnection

    monkeypatch.setattr(manager, "_make_connection_type", local_type)
    monkeypatch.setattr("horus_backend.database.manager.resolve_public", lambda *_: "8.8.8.8")
    if configure:
        assert manager.test().state == "connected"
    return manager


@pytest.fixture(scope="module")
def _editorial_schema_db(pg_cluster):
    with pg_cluster.connect(autocommit=True) as bootstrap:
        for name in ("anon", "authenticated"):
            bootstrap.execute(
                f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{name}') "
                f"THEN CREATE ROLE {name} NOLOGIN; END IF; END $$"
            )
    with pg_cluster.database() as pool:
        with pool.connection() as app:
            database = app.info.dbname
        with pg_cluster.connect(database, autocommit=True) as admin:
            admin.execute("DROP SCHEMA horus CASCADE")
            admin.execute("CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY)")
            apply_chain(admin)
            admin.execute(
                "INSERT INTO horus.schema_migrations(version, checksum, applied_at) "
                "VALUES (%s, %s, clock_timestamp()) ON CONFLICT (version) DO NOTHING",
                (EXPECTED_SCHEMA_VERSION, "a" * 64),
            )
            admin.execute(
                "INSERT INTO horus.database_identity(singleton, instance_id, created_at) "
                "VALUES (true, gen_random_uuid(), clock_timestamp()) ON CONFLICT (singleton) DO NOTHING"
            )
            yield database, admin


@pytest.fixture
def pg_store(pg_cluster, _editorial_schema_db, tmp_path, monkeypatch):
    database, admin = _editorial_schema_db
    manager = _make_pg_manager(pg_cluster, database, tmp_path, monkeypatch)
    store = Store(manager, data_dir=tmp_path)
    try:
        yield store, admin, manager
    finally:
        manager.close()
        admin.execute(
            "TRUNCATE horus.notification_state, horus.preferences, horus.productions, "
            "horus.scripts, horus.artifacts, horus.job_attempts, horus.workflows, horus.jobs RESTART IDENTITY CASCADE"
        )


def _seed_job(admin, *, job_id: str, status: str, updated_at: datetime, result=None, payload=None, workflow_id=None, step_key=None, error=None):
    payload_obj = payload or {"operation": "factory.profile"}
    admin.execute(
        """
        INSERT INTO horus.jobs (id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, result, error, workflow_id, step_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            job_id,
            f"k-{job_id}",
            Jsonb(payload_obj),
            canonical_json_bytes(payload_obj),
            status,
            updated_at,
            updated_at,
            Jsonb(result) if result is not None else None,
            error,
            workflow_id,
            step_key,
        ),
    )


def _seed_workflow(admin, *, wf_id: str, status: str, updated_at: datetime, revision: int = 1):
    payload_obj = {"topic": "teste"}
    admin.execute(
        """
        INSERT INTO horus.workflows (id, idempotency_key, payload, payload_canonical, revision, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (wf_id, f"wf-{wf_id}", Jsonb(payload_obj), canonical_json_bytes(payload_obj), revision, status, updated_at, updated_at),
    )


def _seed_script(admin, *, script_id: str, wf_id: str, version: int, status: str, created_at: datetime):
    art_id = f"art-{script_id}"
    art_job_id = f"job-art-{script_id}"
    att_id = f"att-{script_id}"
    _seed_job(admin, job_id=art_job_id, status="succeeded", updated_at=created_at, workflow_id=wf_id, step_key="proposal")
    in_art = {}
    admin.execute(
        """
        INSERT INTO horus.job_attempts (id, job_id, number, status, executor, input_hash, input_artifacts, input_artifacts_canonical, started_at, finished_at)
        VALUES (%s, %s, 1, 'succeeded', 'simulated', %s, %s, %s, %s, %s)
        """,
        (att_id, art_job_id, "0" * 64, Jsonb(in_art), canonical_json_bytes(in_art), created_at, created_at),
    )
    art_content = {"proposal": "ok"}
    admin.execute(
        """
        INSERT INTO horus.artifacts (id, workflow_id, revision, stage, job_id, attempt_id, schema_version, content, content_canonical, content_hash, created_at)
        VALUES (%s, %s, %s, 'proposal', %s, %s, 1, %s, %s, %s, %s)
        """,
        (art_id, wf_id, version, art_job_id, att_id, Jsonb(art_content), canonical_json_bytes(art_content), "a" * 64, created_at),
    )
    doc = {"title": "Roteiro secreto", "sections": [{"heading": "H1", "narration": "Texto privado", "source_ids": []}]}
    admin.execute(
        """
        INSERT INTO horus.scripts
            (id, workflow_id, workflow_revision, proposal_artifact_id, proposal_content_hash, version, status, document, document_canonical, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (script_id, wf_id, version, art_id, "a" * 64, version, status, Jsonb(doc), canonical_json_bytes(doc), created_at),
    )


def _seed_production(admin, *, prod_id: str, script_id: str, job_id: str, created_at: datetime):
    sr = {"decision": "approve"}
    plan = {"mode": "simulated"}
    admin.execute(
        """
        INSERT INTO horus.productions
            (id, script_id, script_version, script_content_hash, script_review, script_review_canonical,
             job_id, mode, plan, plan_canonical, artifacts, created_at)
        VALUES (%s, %s, 1, %s, %s, %s, %s, 'simulated', %s, %s, '[]'::jsonb, %s)
        """,
        (prod_id, script_id, "b" * 64, Jsonb(sr), canonical_json_bytes(sr), job_id, Jsonb(plan), canonical_json_bytes(plan), created_at),
    )


def test_notifications_and_error_center_unavailable_raise_503(pg_cluster, _editorial_schema_db, tmp_path, monkeypatch):
    database, _ = _editorial_schema_db
    unconfigured = _make_pg_manager(pg_cluster, database, tmp_path / "u", monkeypatch, configure=False)
    try:
        for store in (Store(None, data_dir=tmp_path), Store(unconfigured, data_dir=tmp_path)):
            notif = NotificationService(store, tmp_path)
            ec = ErrorCenter(store)
            for call in (
                notif.unread_count,
                notif.page,
                lambda: notif.set_read(ids=["production:p1:succeeded"], all=False, read=True),
                ec.page,
                ec.summary,
            ):
                with pytest.raises(DatabaseUnavailable) as exc:
                    call()
                assert exc.value.http_status == 503
                assert exc.value.code == "database_unavailable"
        assert not (tmp_path / "notifications-state.json").exists()
    finally:
        unconfigured.close()


def test_notifications_empty_and_derived_events(pg_store, tmp_path):
    store, admin, _ = pg_store
    now_dt = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
    svc = NotificationService(store, tmp_path, clock=lambda: now_dt)

    empty = svc.page()
    assert empty == {"items": [], "unread_count": 0, "next_after": None}
    assert svc.unread_count() == 0

    _seed_workflow(admin, wf_id="wf000001", status="awaiting_approval", updated_at=now_dt - timedelta(minutes=10), revision=2)
    _seed_script(admin, script_id="sc000001", wf_id="wf000001", version=1, status="awaiting_review", created_at=now_dt - timedelta(minutes=8))
    _seed_job(admin, job_id="job-auth", status="awaiting_authorization", updated_at=now_dt - timedelta(minutes=6), workflow_id="wf000001")
    _seed_job(admin, job_id="job-ok", status="succeeded", updated_at=now_dt - timedelta(minutes=4))
    _seed_production(admin, prod_id="prod0001", script_id="sc000001", job_id="job-ok", created_at=now_dt - timedelta(minutes=5))
    _seed_job(
        admin,
        job_id="job-fail",
        status="failed",
        updated_at=now_dt - timedelta(minutes=2),
        result={"failure": {"code": "render_failed", "stderr": "C:\\segredo\\token=123"}},
    )
    _seed_production(admin, prod_id="prod0002", script_id="sc000001", job_id="job-fail", created_at=now_dt - timedelta(minutes=3))

    page = svc.page()
    assert page["unread_count"] == 5
    kinds = [item["kind"] for item in page["items"]]
    assert kinds == [
        "production.failed",
        "production.succeeded",
        "workflow.awaiting_authorization",
        "script.awaiting_review",
        "workflow.awaiting_approval",
    ]
    assert "Código do diagnóstico: render_failed." in page["items"][0]["message"]
    assert "segredo" not in json.dumps(page)
    assert "Roteiro secreto" not in json.dumps(page)
    assert not (tmp_path / "notifications-state.json").exists()


@pytest.mark.parametrize(
    "status,kind,severity",
    [
        ("failed", "production.failed", "error"),
        ("blocked", "production.failed", "error"),
        ("interrupted", "production.interrupted", "warning"),
    ],
)
def test_notifications_production_statuses_and_unsafe_code_ignored(pg_store, tmp_path, status, kind, severity):
    store, admin, _ = pg_store
    now_dt = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
    _seed_workflow(admin, wf_id="wf1", status="running", updated_at=now_dt)
    _seed_script(admin, script_id="sc1", wf_id="wf1", version=1, status="approved", created_at=now_dt)
    _seed_job(admin, job_id="j1", status=status, updated_at=now_dt, result={"failure": {"code": "BAD CODE!"}})
    _seed_production(admin, prod_id="p1", script_id="sc1", job_id="j1", created_at=now_dt)

    svc = NotificationService(store, tmp_path, clock=lambda: now_dt)
    items = [i for i in svc.page()["items"] if i["kind"] == kind]
    assert len(items) == 1
    assert items[0]["severity"] == severity
    assert "BAD CODE" not in items[0]["message"]


def test_notifications_read_unread_persistence_and_watermark(pg_store, tmp_path):
    store, admin, _ = pg_store
    t0 = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    clock = [t0]
    _seed_workflow(admin, wf_id="wf1", status="awaiting_approval", updated_at=t0 - timedelta(minutes=5))
    _seed_script(admin, script_id="sc1", wf_id="wf1", version=1, status="awaiting_review", created_at=t0 - timedelta(minutes=3))

    svc = NotificationService(store, tmp_path, clock=lambda: clock[0])
    assert svc.unread_count() == 2

    res = svc.set_read(ids=["script:sc1:v1"], all=False, read=True)
    assert res == {"unread_count": 1}

    # Reinício: nova instância lê do PostgreSQL
    svc2 = NotificationService(store, tmp_path, clock=lambda: clock[0])
    assert svc2.unread_count() == 1
    assert svc2.page(unread=True)["items"][0]["id"] == "workflow:wf1:r1"

    # Marcar todas como lidas e depois surgir evento novo
    svc2.set_read(ids=None, all=True, read=True)
    assert svc2.unread_count() == 0

    clock[0] = t0 + timedelta(minutes=10)
    _seed_job(admin, job_id="j-new", status="awaiting_authorization", updated_at=clock[0], workflow_id="wf1")
    assert svc2.unread_count() == 1

    # Marcar individualmente como não lido sobrescreve watermark
    svc2.set_read(ids=["script:sc1:v1"], all=False, read=False)
    assert svc2.unread_count() == 2

    # Desmarcar tudo
    svc2.set_read(ids=None, all=True, read=False)
    assert svc2.unread_count() == 3
    assert not (tmp_path / "notifications-state.json").exists()


def test_notifications_pagination_validation_and_query_count(pg_store, tmp_path):
    store, admin, _ = pg_store
    now_dt = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
    _seed_workflow(admin, wf_id="wf1", status="awaiting_approval", updated_at=now_dt)
    for i in range(10):
        _seed_script(admin, script_id=f"sc{i:02d}", wf_id="wf1", version=i + 1, status="awaiting_review", created_at=now_dt - timedelta(minutes=i))

    svc = NotificationService(store, tmp_path, clock=lambda: now_dt)
    queries = []
    orig_uow = store.uow

    class CountingCursor:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=None, **kwargs):
            queries.append(sql)
            return self._inner.execute(sql, params, **kwargs)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    class CountingConn:
        def __init__(self, inner):
            self._inner = inner

        def cursor(self, *args, **kwargs):
            return CountingCursor(self._inner.cursor(*args, **kwargs))

        def __getattr__(self, name):
            return getattr(self._inner, name)

    from contextlib import contextmanager

    @contextmanager
    def tracked_uow(*args, **kwargs):
        with orig_uow(*args, **kwargs) as conn:
            yield CountingConn(conn)

    store.uow = tracked_uow
    p1 = svc.page(limit=4)
    assert len(p1["items"]) == 4
    assert p1["next_after"] is not None
    assert len(queries) <= 6  # O(1) queries, sem N+1

    p2 = svc.page(limit=4, after=p1["next_after"])
    assert len(p2["items"]) == 4
    assert {i["id"] for i in p1["items"]}.isdisjoint({i["id"] for i in p2["items"]})

    with pytest.raises(ValueError):
        svc.page(after="bad-cursor")
    with pytest.raises(ValueError):
        svc.page(kind="unknown")
    with pytest.raises(ValueError):
        svc.set_read(ids=["a", "a"], all=False, read=True)
    with pytest.raises(ValueError):
        svc.set_read(ids=None, all=False, read=True)


def test_error_center_filters_counts_pagination_and_no_leak(pg_store):
    store, admin, _ = pg_store
    now_dt = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
    _seed_workflow(admin, wf_id="wf-only", status="failed", updated_at=now_dt - timedelta(minutes=20))
    _seed_workflow(admin, wf_id="wf-step", status="failed", updated_at=now_dt - timedelta(minutes=10))
    _seed_job(
        admin,
        job_id="j-step",
        status="failed",
        updated_at=now_dt - timedelta(minutes=10),
        workflow_id="wf-step",
        step_key="analysis",
        result={"failure": {"code": "workflow_step_failed", "stage": "analysis", "duration_seconds": 1.5, "exit_code": 2}},
        error="C:\\Users\\secret\\token=xyz",
    )
    _seed_job(
        admin,
        job_id="j-standalone",
        status="blocked",
        updated_at=now_dt - timedelta(minutes=5),
        payload={"operation": "transcription.run"},
        result={"failure": {"code": "unlisted_code"}},
    )
    _seed_script(admin, script_id="sc1", wf_id="wf-step", version=1, status="approved", created_at=now_dt)
    _seed_job(
        admin,
        job_id="j-prod",
        status="interrupted",
        updated_at=now_dt - timedelta(minutes=1),
        result={"failure": {"code": "production_interrupted", "stage": "render"}},
    )
    _seed_production(admin, prod_id="prod1", script_id="sc1", job_id="j-prod", created_at=now_dt - timedelta(minutes=1))

    ec = ErrorCenter(store)
    summary = ec.summary()
    assert summary["total"] == 4
    assert summary["by_kind"][0]["count"] >= 1

    page = ec.page(limit=2)
    assert len(page["items"]) == 2
    assert page["next_after"] is not None
    page2 = ec.page(limit=2, after=page["next_after"])
    assert len(page2["items"]) == 2
    assert page2["next_after"] is None

    serialized = json.dumps(page) + json.dumps(page2)
    assert "token=xyz" not in serialized
    assert "C:\\Users" not in serialized

    filtered = ec.page(kind="production", stage="render", code="production_interrupted")
    assert len(filtered["items"]) == 1
    assert filtered["items"][0]["ref"] == {"kind": "production", "id": "prod1", "workflow_id": None}

    bounded = ec.page(since=(now_dt - timedelta(minutes=6)).isoformat(), until=now_dt.isoformat())
    assert len(bounded["items"]) == 2

    with pytest.raises(ValueError) as exc:
        ec.page(stage="bad stage!")
    assert "bad stage!" not in str(exc.value)
    with pytest.raises(ValueError):
        ec.page(since="not-a-date")
