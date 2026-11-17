"""Testes PostgreSQL para backups, exportação local v2, restauração isolada e update_backup (HOR-164 / I8)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import uuid
import zipfile

import psycopg
import pytest

from horus_backend import backups as bk
from horus_backend import local_data_export as lde
from horus_backend import update_backup as ub
from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.database.manager import DatabaseManager
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from horus_backend.storage import DatabaseUnavailable, Store, canonical_json_bytes
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


@contextmanager
def _provision_horus_db(pg_cluster):
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


@pytest.fixture(scope="module")
def _backup_schema_db(pg_cluster):
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
def pg_store(pg_cluster, _backup_schema_db, tmp_path, monkeypatch):
    with pg_cluster.connect(_backup_schema_db, autocommit=True) as admin:
        tables = admin.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'horus' "
            "AND tablename NOT IN ('schema_migrations', 'database_identity') ORDER BY tablename"
        ).fetchall()
        if tables:
            joined = ", ".join(f"horus.{t[0]}" for t in tables)
            admin.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")
    manager = _make_pg_manager(pg_cluster, _backup_schema_db, tmp_path / "vault", monkeypatch)
    try:
        yield Store(manager, data_dir=tmp_path / "data")
    finally:
        manager.close()


def _seed_operational_data(store: Store, data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    job, _ = store.create_job({"operation": "transcription.run", "title": "Teste PG"}, "job-key-1")
    store.event(job["id"], "progress", "50%")
    store.update(job["id"], "succeeded")

    wf_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    wf_payload = {"topic": "horus", "radar": {"source": "yt", "redacted_at": None}}
    with store.uow() as conn:
        conn.execute(
            """
            INSERT INTO horus.workflows (
                id, idempotency_key, payload, revision, status, feedback,
                created_at, updated_at, executor, cognitive_client, payload_canonical
            ) VALUES (%s, %s, %s, 1, 'queued', '', %s, %s, 'simulated', 'live', %s)
            """,
            (
                wf_id,
                f"wf-{wf_id}",
                json.dumps(wf_payload),
                now,
                now,
                canonical_json_bytes(wf_payload),
            ),
        )
        conn.execute(
            "INSERT INTO horus.preferences (device_id, key, value, updated_at) VALUES ('local', 'narration.preference', %s, %s)",
            (json.dumps({"voice": "pt-BR"}), now),
        )

    prod_dir = data_dir / "productions" / uuid.uuid4().hex
    prod_dir.mkdir(parents=True)
    (prod_dir / "plan.json").write_text(json.dumps({"steps": ["intro"]}), encoding="utf-8")
    (data_dir / "integrations").mkdir(exist_ok=True)
    (data_dir / "integrations" / "secret.json").write_text('{"token": "' + "sk-" + "secret-123456789012345678901234567890" + '"}', encoding="utf-8")
    (data_dir / "models").mkdir(exist_ok=True)
    (data_dir / "exports").mkdir(exist_ok=True)
    (data_dir / "video.mp4").write_bytes(b"media-excluded")
    return {"job_id": job["id"], "workflow_id": wf_id}


def test_unavailable_pg_raises_503_without_sqlite_fallback(tmp_path):
    store = Store(None, data_dir=tmp_path)
    with pytest.raises(DatabaseUnavailable) as exc1:
        bk.inspect_database(store)
    assert exc1.value.http_status == 503
    assert exc1.value.code == "database_unavailable"

    with pytest.raises(DatabaseUnavailable):
        lde.export_local_data(tmp_path, tmp_path / "out.zip", store=None)

    with pytest.raises(DatabaseUnavailable):
        ub.create_update_backup(tmp_path, store=None)

    assert not (tmp_path / "horus.sqlite3").exists()


def test_inspect_database_uses_horus_app_and_counts_tables(pg_store, tmp_path):
    data_dir = tmp_path / "data"
    _seed_operational_data(pg_store, data_dir)
    info = bk.inspect_database(pg_store)
    assert info["ok"] is True
    assert info["schema_version"] == EXPECTED_SCHEMA_VERSION
    assert info["instance_id"]
    assert info["tables"]["jobs"] == 1
    assert info["tables"]["events"] == 1
    assert info["tables"]["workflows"] == 1
    assert info["tables"]["preferences"] == 1


@pytest.mark.pg_strong
def test_export_verify_and_roundtrip_restore_to_isolated_empty_db(pg_cluster, tmp_path, monkeypatch):
    with _provision_horus_db(pg_cluster) as db_a, _provision_horus_db(pg_cluster) as db_b:
        mgr_a = _make_pg_manager(pg_cluster, db_a, tmp_path / "vault_a", monkeypatch)
        try:
            store_a = Store(mgr_a, data_dir=tmp_path / "data_a")
            _seed_operational_data(store_a, tmp_path / "data_a")
            before_a = bk.inspect_database(store_a)

            pkg = tmp_path / "export-v2.zip"
            exported = lde.export_local_data(tmp_path / "data_a", pkg, store=store_a)
            assert exported["ok"] is True
            assert exported["schema_version"] == 2
            assert exported["format"] == "horus-pg-export-v2"

            verified = lde.verify_export(pkg)
            assert verified["ok"] is True, verified

            with zipfile.ZipFile(pkg) as archive:
                names = set(archive.namelist())
                assert "pg_dump/jobs.jsonl" in names
                assert "pg_dump/workflows.jsonl" in names
                assert any(n.startswith("local_files/productions/") for n in names)
                assert not any("integrations" in n or "models" in n or "video.mp4" in n or ".sqlite3" in n for n in names)

            # Restauração em banco B isolado e vazio (usando conexão owner/admin isolada).
            restore_dir = tmp_path / "restored_a"
            with pg_cluster.connect(db_b, autocommit=False) as target_conn:
                restored = lde.restore_local_data(pkg, restore_dir, target_conn=target_conn)
            assert restored["ok"] is True
            assert restored["pg_restored"] is True
            assert (restore_dir / "productions").is_dir()

            # Recusa sobrescrever banco B que agora já possui dados!
            with pg_cluster.connect(db_b, autocommit=False) as target_conn:
                with pytest.raises(ValueError, match="não está vazio"):
                    bk.restore_to_isolated_pg(pkg, target_conn)

            # Compara contagens e hashes por tabela entre DB_A e DB_B!
            mgr_b = _make_pg_manager(pg_cluster, db_b, tmp_path / "vault_b", monkeypatch)
            try:
                store_b = Store(mgr_b, data_dir=restore_dir)
                after_b = bk.inspect_database(store_b)
                assert after_b["tables"] == before_a["tables"]

                pkg_b = tmp_path / "export-b.zip"
                lde.export_local_data(restore_dir, pkg_b, store=store_b)
                with zipfile.ZipFile(pkg) as za, zipfile.ZipFile(pkg_b) as zb:
                    ma = json.loads(za.read("manifest.json"))["database"]["tables"]
                    mb = json.loads(zb.read("manifest.json"))["database"]["tables"]
                    for table in ma:
                        if table in {"database_identity", "schema_migrations"}:
                            continue
                        assert mb[table] == ma[table], f"divergência na tabela {table}"
            finally:
                mgr_b.close()
        finally:
            mgr_a.close()


def test_export_refuses_existing_dest_inside_data_dir_secrets_and_links(pg_store, tmp_path):
    data_dir = tmp_path / "data"
    _seed_operational_data(pg_store, data_dir)

    # Destino dentro de data_dir
    with pytest.raises(ValueError, match="dentro da pasta de dados"):
        lde.export_local_data(data_dir, data_dir / "pkg.zip", store=pg_store)

    # Destino já existente
    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"already")
    with pytest.raises(ValueError, match="já existe"):
        lde.export_local_data(data_dir, existing, store=pg_store)

    # JSON com formato de credencial e JSON inválido são excluídos com motivo explícito
    bad_prod = data_dir / "productions" / uuid.uuid4().hex
    bad_prod.mkdir(parents=True)
    (bad_prod / "secret.json").write_text('{"api_key": "' + "sk-ant-" + "123456789012345678901234567890123456" + '"}', encoding="utf-8")
    (bad_prod / "broken.json").write_text("{broken", encoding="utf-8")

    pkg = tmp_path / "filtered.zip"
    res = lde.export_local_data(data_dir, pkg, store=pg_store)
    reasons = {item["path"]: item["reason"] for item in res["excluded"]}
    assert any("secret.json" in k and v == "formato de credencial" for k, v in reasons.items())
    assert any("broken.json" in k and v == "JSON inválido" for k, v in reasons.items())


def test_verify_detects_tampering_and_unsafe_names(pg_store, tmp_path):
    data_dir = tmp_path / "data"
    _seed_operational_data(pg_store, data_dir)
    pkg = tmp_path / "valid.zip"
    lde.export_local_data(data_dir, pkg, store=pg_store)

    # Adultera conteúdo de tabela PG no ZIP
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(pkg) as src, zipfile.ZipFile(tampered, "w") as dst:
        for info in src.infolist():
            content = src.read(info.filename)
            if info.filename == "pg_dump/jobs.jsonl":
                content = content + b'{"id":"fake"}\n'
            dst.writestr(info, content)
    check = lde.verify_export(tampered)
    assert check["ok"] is False

    # Nome inseguro com ..
    unsafe = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as dst:
        dst.writestr("../evil.txt", "x")
    assert lde.verify_export(unsafe)["ok"] is False


def test_backup_retention_policy_and_radar_flags(pg_store, tmp_path):
    data_dir = tmp_path / "data"
    _seed_operational_data(pg_store, data_dir)
    bdir = tmp_path / "backups"
    bdir.mkdir()
    p1 = bdir / "horus-pg-backup-v20261117160300-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.zip"
    p2 = bdir / "horus-pg-backup-v20261117160300-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.zip"
    lde.export_local_data(data_dir, p1, store=pg_store)
    lde.export_local_data(data_dir, p2, store=pg_store)

    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    import os
    os.utime(p1, (old_ts, old_ts))

    dry = bk.remove_backups(bdir, retention_days=30, dry_run=True)
    assert dry.dry_run is True
    assert p1.exists() and p2.exists()
    assert len(dry.candidates) == 1 and dry.candidates[0].name == p1.name
    assert dry.needs_operator_decision  # p2 mantido tem Radar não redigido

    applied = bk.remove_backups(bdir, retention_days=30, dry_run=False)
    assert applied.removed == [p1.name]
    assert not p1.exists() and p2.exists()


def test_update_backup_fail_closed_and_success(pg_store, tmp_path):
    data_dir = tmp_path / "data"
    _seed_operational_data(pg_store, data_dir)

    res = ub.create_update_backup(data_dir, store=pg_store)
    assert res["ok"] is True
    assert res["code"] == "update_backup_verified"
    assert res["label"].startswith("pre-update-") and res["label"].endswith(".zip")
    assert res["remote_proof"]["schema_version"] == EXPECTED_SCHEMA_VERSION

    # Comprovante incompatível bloqueia update (falha fechada)
    with pytest.raises(ub.UpdateBackupBlocked) as exc:
        ub.create_update_backup(
            data_dir,
            store=pg_store,
            remote_receipt={"instance_id": "00000000-0000-0000-0000-000000000000", "verified_at": "2026-11-17T00:00:00Z"},
        )
    assert exc.value.code == "update_backup_remote_proof_invalid"

    # Arquivo JSON inválido no workspace bloqueia update (backup incompleto)
    bad_dir = data_dir / "productions" / uuid.uuid4().hex
    bad_dir.mkdir(parents=True)
    (bad_dir / "broken.json").write_text("{invalid", encoding="utf-8")
    with pytest.raises(ub.UpdateBackupBlocked) as exc2:
        ub.create_update_backup(data_dir, store=pg_store)
    assert exc2.value.code == "update_backup_incomplete"
