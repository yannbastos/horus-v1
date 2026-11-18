"""Testes PostgreSQL para exclusão segura de dados locais (HOR-117 / HOR-164 I8)."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import psycopg
import pytest

from horus_backend import data_deletion as dd
from horus_backend.database.manager import DatabaseManager
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from horus_backend.local_data_export import verify_export
from horus_backend.storage import DatabaseUnavailable, Store, canonical_json_bytes
from horus_backend.workflows import WorkflowConflict
from schema_support import apply_chain
from test_database_contract import CONFIG, MemoryKeyring


def _make_pg_manager(pg_cluster, database: str, tmp_path: Path, monkeypatch) -> DatabaseManager:
    vault = ConnectionVault(tmp_path, MemoryKeyring())
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
    assert manager.test().state == "connected"
    return manager


@pytest.fixture(scope="module")
def _deletion_schema_db(pg_cluster):
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
        yield database


@pytest.fixture
def deletion_world(pg_cluster, _deletion_schema_db, tmp_path, monkeypatch):
    with pg_cluster.connect(_deletion_schema_db, autocommit=True) as admin:
        tables = admin.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'horus' "
            "AND tablename NOT IN ('schema_migrations', 'database_identity') ORDER BY tablename"
        ).fetchall()
        if tables:
            joined = ", ".join(f"horus.{t[0]}" for t in tables)
            admin.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")
    manager = _make_pg_manager(pg_cluster, _deletion_schema_db, tmp_path / "vault", monkeypatch)
    try:
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        store = Store(manager, data_dir=data_dir)
        job, _ = store.create_job({"operation": "production.run"}, "prod-job-1")
        store.update(job["id"], "succeeded")

        wf_id = uuid.uuid4().hex
        art_id = uuid.uuid4().hex
        att_id = uuid.uuid4().hex
        script_id = uuid.uuid4().hex
        prod_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)

        with store.uow() as conn:
            wf_payload = {"topic": "horus"}
            conn.execute(
                "INSERT INTO horus.workflows(id,idempotency_key,payload,revision,status,feedback,created_at,updated_at,executor,cognitive_client,payload_canonical) "
                "VALUES(%s,%s,%s,1,'approved','',%s,%s,'simulated','live',%s)",
                (wf_id, f"wf-{wf_id}", json.dumps(wf_payload), now, now, canonical_json_bytes(wf_payload)),
            )
            conn.execute(
                "INSERT INTO horus.job_attempts(id,job_id,number,status,executor,input_hash,input_artifacts,started_at,input_artifacts_canonical) "
                "VALUES(%s,%s,1,'succeeded','simulated',%s,'{}',%s,%s)",
                (att_id, job["id"], "a" * 64, now, canonical_json_bytes({})),
            )
            art_content = {"ok": True}
            conn.execute(
                "INSERT INTO horus.artifacts(id,workflow_id,revision,stage,job_id,attempt_id,schema_version,content,content_hash,created_at,content_canonical) "
                "VALUES(%s,%s,1,'proposal',%s,%s,1,%s,%s,%s,%s)",
                (art_id, wf_id, job["id"], att_id, json.dumps(art_content), "b" * 64, now, canonical_json_bytes(art_content)),
            )
            doc = {"title": "Roteiro"}
            conn.execute(
                "INSERT INTO horus.scripts(id,workflow_id,workflow_revision,proposal_artifact_id,proposal_content_hash,version,status,document,created_at,document_canonical) "
                "VALUES(%s,%s,1,%s,%s,1,'approved',%s,%s,%s)",
                (script_id, wf_id, art_id, "b" * 64, json.dumps(doc), now, canonical_json_bytes(doc)),
            )
            sr = {"approved": True}
            plan_doc = {"scenes": []}
            conn.execute(
                "INSERT INTO horus.productions(id,script_id,script_version,script_content_hash,script_review,job_id,mode,plan,artifacts,created_at,script_review_canonical,plan_canonical) "
                "VALUES(%s,%s,1,%s,%s,%s,'simulated',%s,'[]',%s,%s,%s)",
                (prod_id, script_id, "c" * 64, json.dumps(sr), job["id"], json.dumps(plan_doc), now, canonical_json_bytes(sr), canonical_json_bytes(plan_doc)),
            )

        (data_dir / "productions" / prod_id / "extra").mkdir(parents=True)
        (data_dir / "productions" / prod_id / "plan.json").write_text('{"ok":true}', encoding="utf-8")
        (data_dir / "productions" / prod_id / "extra" / "cena.png").write_bytes(b"png-sintetico" * 50)
        (data_dir / "exports" / prod_id / "e1").mkdir(parents=True)
        (data_dir / "exports" / prod_id / "e1" / "package.zip").write_bytes(b"zip-sintetico" * 30)
        (data_dir / "production-comments").mkdir(exist_ok=True)
        (data_dir / "production-comments" / f"{prod_id}.json").write_text('{"comments":[]}', encoding="utf-8")

        audit = dd.DeletionAudit(store=store, data_dir=data_dir)
        fake_manager = SimpleNamespace(
            settings=SimpleNamespace(data_dir=data_dir),
            store=store,
            productions=None,
            deletion_audit=audit,
            active_id=None,
        )
        service = dd.DataDeletionService(fake_manager)
        yield service, store, prod_id, job["id"], data_dir
    finally:
        manager.close()


def test_deletion_unavailable_pg_raises_503(tmp_path):
    audit = dd.DeletionAudit(tmp_path)
    with pytest.raises(DatabaseUnavailable) as exc:
        audit.events()
    assert exc.value.http_status == 503


def test_preview_is_exact_lists_db_kept_and_honest_retention_notice(deletion_world):
    service, _store, pid, _jid, data_dir = deletion_world
    plan = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))
    assert plan["can_execute"] is True
    assert plan["blockers"] == []
    assert plan["confirmation_phrase"] == f"APAGAR producao {pid[:8]}"
    assert {k["table"] for k in plan["db_kept"]} >= {"productions", "jobs"}
    assert any(d["kind"] == "script" for d in plan["dependencies"])
    assert "MVCC" in plan["notice"] and "secure_delete" not in plan["notice"]


def test_wrong_phrase_wrong_token_unknown_plan_and_single_use(deletion_world):
    service, _store, pid, _jid, data_dir = deletion_world
    plan = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))

    with pytest.raises(WorkflowConflict):
        service.execute(dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token=plan["intent_token"], phrase="errado"))
    with pytest.raises(WorkflowConflict):
        service.execute(dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token="x" * 24, phrase=plan["confirmation_phrase"]))
    with pytest.raises(WorkflowConflict):
        service.execute(dd.DeletionExecuteRequest(plan_id="0" * 32, intent_token=plan["intent_token"], phrase=plan["confirmation_phrase"]))

    assert (data_dir / "productions" / pid).is_dir()
    res = service.execute(
        dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token=plan["intent_token"], phrase=plan["confirmation_phrase"])
    )
    assert res["audit_id"]

    # Uso único
    with pytest.raises(WorkflowConflict):
        service.execute(
            dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token=plan["intent_token"], phrase=plan["confirmation_phrase"])
        )


def test_execute_backup_quarantine_pg_audit_chain_and_restore(deletion_world):
    service, store, pid, _jid, data_dir = deletion_world
    plan = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))
    result = service.execute(
        dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token=plan["intent_token"], phrase=plan["confirmation_phrase"])
    )
    aid = result["audit_id"]
    assert not (data_dir / "productions" / pid).exists()
    assert (data_dir / ".quarentena" / aid / "productions" / pid / "extra" / "cena.png").is_file()

    pkg = data_dir.parent / result["backup"]["location"] / result["backup"]["name"]
    assert pkg.is_file() and verify_export(pkg)["ok"] is True

    page = service.page()
    assert page["chain_ok"] is True
    assert [e["action"] for e in reversed(page["items"])] == ["started", "completed"]
    assert service.audit.local_data("production", pid)["state"] == "deleted"

    # Banco PG preservado intacto
    with store.uow(readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM horus.productions WHERE id = %s", (pid,)).fetchone()[0] == 1

    # Restauração
    restored = service.restore(aid)
    assert restored["id"] == pid
    assert (data_dir / "productions" / pid / "extra" / "cena.png").is_file()
    assert service.audit.local_data("production", pid)["state"] == "present"

    # Não pode restaurar duas vezes
    with pytest.raises(WorkflowConflict):
        service.restore(aid)


def test_active_jobs_and_changed_files_block_execution(deletion_world):
    service, store, pid, jid, data_dir = deletion_world
    plan = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))

    store.update(jid, "running")
    with pytest.raises(WorkflowConflict, match="tarefa ativa"):
        service.execute(
            dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token=plan["intent_token"], phrase=plan["confirmation_phrase"])
        )
    blocked = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))
    assert [b["code"] for b in blocked["blockers"]] == ["active_jobs"]

    store.update(jid, "succeeded")
    plan2 = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))
    (data_dir / "productions" / pid / "extra.txt").write_text("mudou", encoding="utf-8")
    with pytest.raises(WorkflowConflict, match="mudaram"):
        service.execute(
            dd.DeletionExecuteRequest(plan_id=plan2["plan_id"], intent_token=plan2["intent_token"], phrase=plan2["confirmation_phrase"])
        )


def test_backup_failure_aborts_before_move_and_without_audit_start(deletion_world, monkeypatch):
    service, _store, pid, _jid, data_dir = deletion_world
    plan = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))
    monkeypatch.setattr(dd, "export_local_data", lambda *a, **k: (_ for _ in ()).throw(ValueError("falha")))
    with pytest.raises(WorkflowConflict):
        service.execute(
            dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token=plan["intent_token"], phrase=plan["confirmation_phrase"])
        )
    assert (data_dir / "productions" / pid).is_dir()
    assert service.audit.events()[0] == []


def test_move_failure_rolls_back_and_audits_failed(deletion_world, monkeypatch):
    service, _store, pid, _jid, data_dir = deletion_world
    plan = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))
    real_rename = dd.os.rename
    calls = {"n": 0}

    def flaky_rename(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disco ocupado")
        return real_rename(src, dst)

    monkeypatch.setattr(dd.os, "rename", flaky_rename)
    with pytest.raises(WorkflowConflict):
        service.execute(
            dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token=plan["intent_token"], phrase=plan["confirmation_phrase"])
        )
    assert (data_dir / "productions" / pid).is_dir()
    events, ok = service.audit.events()
    assert ok and [e["action"] for e in events] == ["started", "failed"]


def test_network_drop_after_move_leaves_incomplete_and_reconciles_by_hash(deletion_world, monkeypatch):
    service, _store, pid, _jid, data_dir = deletion_world
    plan = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))
    real_append = service.audit.append

    def drop_on_completed(**kwargs):
        if kwargs.get("action") == "completed":
            raise DatabaseUnavailable("database_unavailable")
        return real_append(**kwargs)

    monkeypatch.setattr(service.audit, "append", drop_on_completed)
    with pytest.raises(DatabaseUnavailable):
        service.execute(
            dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token=plan["intent_token"], phrase=plan["confirmation_phrase"])
        )

    # Arquivos já foram movidos para quarentena e PG tem `started` -> estado `incomplete`
    monkeypatch.setattr(service.audit, "append", real_append)
    state = service.audit.local_data("production", pid)
    assert state["state"] == "incomplete"
    aid = state["audit_id"]

    # Prévia bloqueada enquanto incomplete
    blocked = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))
    assert any(b["code"] == "incomplete_deletion" for b in blocked["blockers"])

    # Reconciliação por hash conclui para `deleted`
    rec = service.reconcile(aid)
    assert rec["outcome"] == "completed" and rec["state"] == "deleted"
    assert service.audit.local_data("production", pid)["state"] == "deleted"


def test_pg_triggers_enforce_append_only_and_valid_hash_chain(deletion_world):
    service, store, pid, _jid, _data_dir = deletion_world
    plan = service.preview(dd.DeletionPreviewRequest(scope="production", id=pid))
    service.execute(
        dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token=plan["intent_token"], phrase=plan["confirmation_phrase"])
    )

    with store.uow() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE horus.deletion_events SET action = 'tampered'")
    with store.uow() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM horus.deletion_events")
    with store.uow() as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                """
                INSERT INTO horus.deletion_events (
                    id, audit_id, action, target, device_id, created_at,
                    payload, payload_canonical, prev_hash, hash
                ) VALUES ('bad', 'bad', 'started', 'production:x', 'local', now(), '{}'::jsonb, '{}'::bytea, %s, %s)
                """,
                ("0" * 64, "1" * 64),
            )


def test_imports_can_be_deleted_and_restored(deletion_world):
    service, _store, _pid, _jid, data_dir = deletion_world
    imp_id = uuid.uuid4().hex
    imp_dir = data_dir / "imports" / imp_id
    imp_dir.mkdir(parents=True)
    (imp_dir / "video.mp4").write_bytes(b"import-video")

    plan = service.preview(dd.DeletionPreviewRequest(scope="import", id=imp_id))
    assert plan["can_execute"] is True
    res = service.execute(
        dd.DeletionExecuteRequest(plan_id=plan["plan_id"], intent_token=plan["intent_token"], phrase=plan["confirmation_phrase"])
    )
    assert not imp_dir.exists()
    service.restore(res["audit_id"])
    assert (imp_dir / "video.mp4").is_file()
