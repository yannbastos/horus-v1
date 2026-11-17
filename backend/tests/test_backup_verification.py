"""Testes de verificação, inventário e restauração isolada de backups em PostgreSQL (portado do legado)."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import psycopg
import pytest

from horus_backend.backups import inspect_database, inventory, restore_to_isolated_pg
from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.local_data_export import export_local_data, main as export_main, verify_export
from horus_backend.storage import DatabaseUnavailable, Store
from support_d import (
    create_pg_backup_zip,
    isolated_empty_pg_db,
    make_pg_store,
    seed_radar_workflow,
    truncate_horus,
)


@pytest.fixture
def pg_backup_env(pg_app_cluster, pg_app_database, tmp_path, monkeypatch):
    truncate_horus(pg_app_cluster, pg_app_database)
    manager, store = make_pg_store(pg_app_cluster, pg_app_database, tmp_path, monkeypatch)
    try:
        yield pg_app_cluster, pg_app_database, manager, store, tmp_path
    finally:
        manager.close()


def test_verify_integrity_counts_comparison_and_no_writes(pg_backup_env):
    _, _, _, store, tmp_path = pg_backup_env
    seed_radar_workflow(store, "M1", items=2)
    info_before = inspect_database(store)
    assert info_before["ok"] is True
    assert info_before["schema_version"] == EXPECTED_SCHEMA_VERSION
    assert info_before["instance_id"]
    assert info_before["tables"]["workflows"] == 1

    package = tmp_path / "horus-pg-backup-v2-snap1.zip"
    report = export_local_data(store.data_dir, package, store=store)
    assert report["ok"] is True
    assert verify_export(package) == {"ok": True, "errors": []}

    seed_radar_workflow(store, "M2", items=2)
    info_after = inspect_database(store)
    assert info_after["tables"]["workflows"] == 2
    # inspect_database é estritamente somente leitura
    assert inspect_database(store) == info_after


def test_corrupt_backup_is_reported_and_cannot_restore(pg_backup_env, capsys):
    cluster, _, _, _, tmp_path = pg_backup_env
    corrupt = tmp_path / "horus-pg-backup-v2-corrupt.zip"
    corrupt.write_bytes(b"not a zip backup")

    checked = verify_export(corrupt)
    assert checked["ok"] is False and checked["errors"]
    assert export_main(["verify", str(corrupt)]) == 1
    assert '"ok": false' in capsys.readouterr().out.lower()

    with isolated_empty_pg_db(cluster) as (admin, _):
        with pytest.raises(ValueError, match="não verificado"):
            restore_to_isolated_pg(corrupt, admin)
        assert admin.execute("SELECT COUNT(*) FROM horus.workflows").fetchone()[0] == 0


def test_restore_and_current_version_migrate_only_copy(pg_backup_env):
    cluster, _, _, store, tmp_path = pg_backup_env
    wid = seed_radar_workflow(store, "RESTORE-OK", items=3)
    package = tmp_path / "horus-pg-backup-v2-valid.zip"
    export_local_data(store.data_dir, package, store=store)
    before_sha = hashlib.sha256(package.read_bytes()).hexdigest()

    with isolated_empty_pg_db(cluster) as (admin, _):
        report = restore_to_isolated_pg(package, admin)
        assert report["ok"] is True
        assert report["schema_version"] == EXPECTED_SCHEMA_VERSION
        assert report["restored_tables"]["workflows"] == 1
        assert "isolado e vazio" in report["instruction"]
        row = admin.execute("SELECT id, status FROM horus.workflows WHERE id = %s", (wid,)).fetchone()
        assert row == (wid, "awaiting_approval")

    assert hashlib.sha256(package.read_bytes()).hexdigest() == before_sha


def test_existing_destination_and_source_refused(pg_backup_env):
    cluster, _, _, store, tmp_path = pg_backup_env
    seed_radar_workflow(store, "ORIGIN", items=1)
    package = tmp_path / "horus-pg-backup-v2-src.zip"
    export_local_data(store.data_dir, package, store=store)
    original_sha = hashlib.sha256(package.read_bytes()).hexdigest()

    with isolated_empty_pg_db(cluster) as (admin, _):
        admin.execute(
            "INSERT INTO horus.media(id, path, name, size, modified_ns, created_at) "
            "VALUES ('m-existing', 'C:/x.mp4', 'x.mp4', 1, 1, now())"
        )
        with pytest.raises(ValueError, match="não está vazio"):
            restore_to_isolated_pg(package, admin)
        assert admin.execute("SELECT COUNT(*) FROM horus.media").fetchone()[0] == 1

    assert hashlib.sha256(package.read_bytes()).hexdigest() == original_sha


def test_consistent_repeatable_read_snapshot_ignores_uncommitted_writer(pg_backup_env):
    """Equivalente PG ao antigo teste de WAL sidecar: leitura REPEATABLE READ ignora escrita concorrente não commitada."""
    cluster, database, _, store, tmp_path = pg_backup_env
    seed_radar_workflow(store, "COMMITTED", items=1)

    with cluster.connect(database, autocommit=False) as writer:
        writer.execute(
            "INSERT INTO horus.media(id, path, name, size, modified_ns, created_at) "
            "VALUES ('m-uncommitted', 'C:/u.mp4', 'u.mp4', 1, 1, now())"
        )
        package = tmp_path / "horus-pg-backup-v2-consistent.zip"
        report = export_local_data(store.data_dir, package, store=store)
        assert report["ok"] is True
        import zipfile
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        assert manifest["database"]["tables"]["workflows"]["rows"] == 1
        assert manifest["database"]["tables"]["media"]["rows"] == 0
        writer.rollback()


def test_missing_current_is_not_created(tmp_path):
    with pytest.raises(DatabaseUnavailable):
        inspect_database(None)
    assert list(tmp_path.iterdir()) == []


def test_corruption_does_not_skip_other_backups(tmp_path):
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    good = create_pg_backup_zip(tmp_path / f"horus-pg-backup-v2-{'a' * 32}.zip", radar=True, redacted=False, clock_now=now)
    broken = tmp_path / f"horus-pg-backup-v2-{'b' * 32}.zip"
    broken.write_bytes(b"corrupt")

    entries = inventory(tmp_path, clock=lambda: now)
    by_name = {item.name: item for item in entries}
    assert by_name[good.name].radar_workflows == 1 and by_name[good.name].radar_unredacted == 1
    assert by_name[broken.name].radar_workflows is None and by_name[broken.name].radar_unredacted is None


def test_counts_quote_table_names_and_show_new_tables(pg_backup_env):
    cluster, database, _, store, _ = pg_backup_env
    with cluster.connect(database, autocommit=True) as admin:
        admin.execute('CREATE TABLE horus."quoted""name" (id INTEGER)')
        admin.execute('INSERT INTO horus."quoted""name" VALUES (42)')
        admin.execute('GRANT SELECT ON horus."quoted""name" TO horus_app')
    try:
        info = inspect_database(store)
        assert info["tables"]['quoted"name'] == 1
    finally:
        with cluster.connect(database, autocommit=True) as admin:
            admin.execute('DROP TABLE IF EXISTS horus."quoted""name"')
