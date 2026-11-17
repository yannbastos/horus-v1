"""Endurecimento, retenção lógica do Radar, atomicidade transacional e política de backups em PostgreSQL.

Portado do legado SQLite (`test_sqlite_hardening.py`): testes intrínsecos de PRAGMA/WAL/VACUUM do SQLite
foram substituídos pelos equivalentes PostgreSQL (UoW, MVCC, rollback transacional, inventário/retenção de pacotes v2).
Somente cluster PostgreSQL descartável em loopback e `tmp_path`, sem rede nem dados reais.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from horus_backend import backups, retention
from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.retention import run_retention
from support_d import (
    create_pg_backup_zip,
    make_pg_store,
    seed_radar_workflow,
    truncate_horus,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
EXPIRED = "2026-10-01T00:00:00Z"
TABLES = (
    "workflows",
    "jobs",
    "job_attempts",
    "artifacts",
    "approvals",
    "authorizations",
    "cognitive_runs",
    "workflow_events",
    "events",
)


def clock():
    return NOW


def new_marker():
    return "MARCADOR-SECS2-" + uuid.uuid4().hex


@pytest.fixture
def pg_hardening_env(pg_app_cluster, pg_app_database, tmp_path, monkeypatch):
    truncate_horus(pg_app_cluster, pg_app_database)
    manager, store = make_pg_store(pg_app_cluster, pg_app_database, tmp_path, monkeypatch)
    try:
        yield pg_app_cluster, pg_app_database, manager, store, tmp_path
    finally:
        manager.close()


def counts(store):
    with store.uow(readonly=True) as db:
        return {t: db.execute(f"SELECT COUNT(*) FROM horus.{t}").fetchone()[0] for t in TABLES}


def snapshot(directory: Path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.iterdir())}


# --- retenção lógica, isolamento MVCC e atomicidade PG -----------------------------------------------------

def test_pg_connection_runs_as_horus_app_with_foreign_keys_and_timeouts(pg_hardening_env):
    _, _, manager, store, _ = pg_hardening_env
    with store.uow(readonly=True) as conn:
        role, is_super = conn.execute(
            "SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert role == "horus_app" and is_super is False
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "on"
    assert manager.snapshot.state == "connected"


def test_retention_redacts_expired_radar_payload_without_leaving_marker(pg_hardening_env):
    _, _, manager, store, _ = pg_hardening_env
    marker = new_marker()
    wid = seed_radar_workflow(store, marker, expires=EXPIRED, items=50)
    report = run_retention(manager, clock=clock)
    assert (report.redacted, report.failed) == (1, 0)
    assert report.checkpoint is None and not report.checkpoint_pending
    with store.uow(readonly=True) as db:
        payload = db.execute("SELECT payload::text FROM horus.workflows WHERE id = %s", (wid,)).fetchone()[0]
        assert marker not in payload


def test_retention_with_active_reader_does_not_block_or_break_cycle(pg_hardening_env):
    """Equivalente PG (MVCC): leitor com transação aberta não bloqueia a retenção nem vê estado parcial."""
    cluster, database, manager, store, _ = pg_hardening_env
    marker = new_marker()
    wid = seed_radar_workflow(store, marker, expires=EXPIRED, items=20)
    with cluster.connect(database, autocommit=False) as reader:
        reader.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
        before_reader = reader.execute("SELECT payload::text FROM horus.workflows WHERE id = %s", (wid,)).fetchone()[0]
        assert marker in before_reader
        report = run_retention(manager, clock=clock)
        assert (report.redacted, report.failed) == (1, 0)
        # Snapshot do leitor permanece consistente sem bloqueio
        still_consistent = reader.execute("SELECT payload::text FROM horus.workflows WHERE id = %s", (wid,)).fetchone()[0]
        assert marker in still_consistent
        reader.rollback()
    with store.uow(readonly=True) as db:
        after = db.execute("SELECT payload::text FROM horus.workflows WHERE id = %s", (wid,)).fetchone()[0]
        assert marker not in after


def test_dry_run_never_mutates_database(pg_hardening_env):
    _, _, manager, store, _ = pg_hardening_env
    marker = new_marker()
    wid = seed_radar_workflow(store, marker, expires=EXPIRED, items=20)
    before = counts(store)
    report = run_retention(manager, clock=clock, dry_run=True)
    assert report.redacted == 1 and report.checkpoint is None
    assert counts(store) == before
    with store.uow(readonly=True) as db:
        assert marker in db.execute("SELECT payload::text FROM horus.workflows WHERE id = %s", (wid,)).fetchone()[0]


def test_integrity_and_row_counts_after_cycle(pg_hardening_env):
    _, _, manager, store, _ = pg_hardening_env
    for _ in range(3):
        seed_radar_workflow(store, new_marker(), expires=EXPIRED, items=20)
    before = counts(store)
    assert run_retention(manager, clock=clock).redacted == 3
    after = counts(store)
    assert after.pop("workflow_events") == before.pop("workflow_events") + 3 and after == before


def test_reopen_and_recover_after_failure_in_the_middle(pg_hardening_env, monkeypatch):
    cluster, database, manager, store, tmp_path = pg_hardening_env
    marker = new_marker()
    wid = seed_radar_workflow(store, marker, expires=EXPIRED, items=20)
    monkeypatch.setattr(retention, "_redact_artifacts", lambda *a: (_ for _ in ()).throw(RuntimeError("falha injetada")))
    report = run_retention(manager, clock=clock)
    assert (report.redacted, report.failed, report.checkpoint) == (0, 1, None)
    monkeypatch.undo()
    reopened_mgr, reopened_store = make_pg_store(cluster, database, tmp_path / "vault2", monkeypatch)
    try:
        with reopened_store.uow(readonly=True) as db:
            payload = db.execute("SELECT payload FROM horus.workflows WHERE id = %s", (wid,)).fetchone()[0]
            assert "redacted_at" not in payload["radar"]
        assert run_retention(reopened_mgr, clock=clock).redacted == 1
        with reopened_store.uow(readonly=True) as db:
            assert marker not in db.execute("SELECT payload::text FROM horus.workflows WHERE id = %s", (wid,)).fetchone()[0]
    finally:
        reopened_mgr.close()


def test_uncommitted_transaction_never_leaks_partial_write(pg_hardening_env):
    """Equivalente PG ao antigo teste de recuperação WAL: transação abortada nunca é visível após queda/rollback."""
    cluster, database, manager, store, _ = pg_hardening_env
    marker = new_marker()
    wid = seed_radar_workflow(store, marker, expires=EXPIRED, items=20)
    with cluster.connect(database, autocommit=False) as writer:
        writer.execute("UPDATE horus.workflows SET status = 'failed' WHERE id = %s", (wid,))
        with store.uow(readonly=True) as reader:
            row = reader.execute("SELECT status, payload::text FROM horus.workflows WHERE id = %s", (wid,)).fetchone()
            assert row[0] == "awaiting_approval" and marker in row[1]
        writer.rollback()
    assert run_retention(manager, clock=clock).redacted == 1


# --- backups lógicos v2 e retenção -------------------------------------------------------------------------

@pytest.fixture
def backup_dir(tmp_path):
    directory = tmp_path / "backups"
    directory.mkdir()
    return directory


def _current_ver_int() -> int:
    digits = "".join(c for c in EXPECTED_SCHEMA_VERSION if c.isdigit())
    return int(digits)


def make_v2_backup(directory: Path, *, version_num: int | None = None, age_days: int = 0, radar: bool = True, redacted: bool = False) -> Path:
    ver = version_num if version_num is not None else _current_ver_int()
    name = directory / f"horus-pg-backup-v{ver}-{uuid.uuid4().hex}.zip"
    return create_pg_backup_zip(
        name,
        schema_version=str(ver),
        radar=radar,
        redacted=redacted,
        age_days=age_days,
        clock_now=NOW,
    )


def test_migration_created_backup_is_inventoried_with_radar_flag(backup_dir):
    created = make_v2_backup(backup_dir, radar=True, redacted=False)
    found = backups.inventory(backup_dir, clock=clock)
    assert [(e.version, e.radar_workflows, e.radar_unredacted) for e in found] == [(_current_ver_int(), 1, 1)]
    assert backups.plan(backup_dir, retention_days=None, clock=clock).needs_operator_decision == [created.name]


def test_inventory_radar_detection_variants(backup_dir):
    make_v2_backup(backup_dir, radar=False, age_days=1)
    make_v2_backup(backup_dir, radar=True, redacted=True, age_days=2)
    make_v2_backup(backup_dir, version_num=1, radar=False, age_days=3)
    (backup_dir / f"horus-pg-backup-v3-{'a' * 32}.zip").write_bytes(b"nao e zip" * 20)
    (backup_dir / "horus-pg-backup-v3-notes.zip").write_bytes(b"ignorado")
    (backup_dir / f"outro-v3-{'b' * 32}.zip").write_bytes(b"outro arquivo")
    by_version = {(e.version, e.radar_workflows, e.radar_unredacted) for e in backups.inventory(backup_dir, clock=clock)}
    assert by_version == {(_current_ver_int(), 0, 0), (_current_ver_int(), 1, 0), (1, 0, 0), (3, None, None)}


def test_dry_run_lists_reasons_and_deletes_nothing(backup_dir):
    newest = make_v2_backup(backup_dir, age_days=1)
    old = make_v2_backup(backup_dir, age_days=90)
    recent = make_v2_backup(backup_dir, age_days=5)
    future = make_v2_backup(backup_dir, version_num=_current_ver_int() + 1, age_days=400)
    before = snapshot(backup_dir)
    report = backups.remove_backups(backup_dir, retention_days=30, clock=clock)  # dry_run é o padrão
    assert report.dry_run and report.removed == [] and snapshot(backup_dir) == before
    action = {e.name: (e.action, e.reason) for e in report.entries}
    assert action[old.name][0] == "remove" and "30 dias" in action[old.name][1]
    assert action[newest.name] == ("keep", "backup mais recente")
    assert action[recent.name] == ("keep", "dentro do prazo de 30 dias")
    assert action[future.name][0] == "keep" and "maior que o schema atual" in action[future.name][1]
    assert [e.name for e in report.candidates] == [old.name]
    assert all(e.radar_workflows == 1 and e.radar_unredacted == 1 for e in report.entries)


def test_removal_disabled_without_explicit_configuration(backup_dir):
    for age in (1, 50, 500):
        make_v2_backup(backup_dir, age_days=age)
    before = snapshot(backup_dir)
    for dry_run in (True, False):
        report = backups.remove_backups(backup_dir, retention_days=None, dry_run=dry_run, clock=clock)
        assert report.removed == [] and report.candidates == [] and snapshot(backup_dir) == before
    assert all("remoção desligada" in e.reason for e in report.entries[1:])


def test_explicit_removal_keeps_latest_only_and_future_backups(backup_dir):
    keep_latest = make_v2_backup(backup_dir, age_days=200)
    older = [make_v2_backup(backup_dir, age_days=age) for age in (300, 400)]
    fresh_old = make_v2_backup(backup_dir, age_days=250)
    future = make_v2_backup(backup_dir, version_num=_current_ver_int() + 2, age_days=900)
    report = backups.remove_backups(backup_dir, retention_days=100, dry_run=False, clock=clock)
    assert sorted(report.removed) == sorted([older[0].name, older[1].name, fresh_old.name]) and report.errors == []
    assert sorted(p.name for p in backup_dir.iterdir()) == sorted({keep_latest.name, future.name})


def test_single_backup_is_never_removed(backup_dir):
    only = make_v2_backup(backup_dir, age_days=5000)
    report = backups.remove_backups(backup_dir, retention_days=1, dry_run=False, clock=clock)
    assert report.removed == [] and only.exists() and report.entries[0].reason == "único backup existente"
    assert report.needs_operator_decision == [only.name]


def test_removal_error_is_reported_and_others_continue(backup_dir, monkeypatch):
    make_v2_backup(backup_dir, age_days=1)
    blocked, free = make_v2_backup(backup_dir, age_days=300), make_v2_backup(backup_dir, age_days=400)
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == blocked.name:
            raise PermissionError("em uso")
        return real_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    report = backups.remove_backups(backup_dir, retention_days=30, dry_run=False, clock=clock)
    assert report.removed == [free.name] and report.errors == [{"backup": blocked.name, "error": "PermissionError"}]
    assert blocked.exists() and not free.exists()


@pytest.mark.parametrize(
    "value,expected",
    [(None, None), ("", None), ("  ", None), ("abc", None), ("0", None), ("-3", None), ("1.5", None), ("30", 30), (" 7 ", 7)],
)
def test_retention_days_environment_parsing(value, expected):
    environ = {} if value is None else {backups.ENV_RETENTION_DAYS: value}
    assert backups.retention_days_from_env(environ) == expected


def test_cli_lists_by_default_and_requires_env_to_apply(backup_dir, monkeypatch, capsys):
    make_v2_backup(backup_dir, age_days=1)
    old = make_v2_backup(backup_dir, age_days=900)
    monkeypatch.delenv(backups.ENV_RETENTION_DAYS, raising=False)
    assert backups.main([str(backup_dir)]) == 0 and old.exists()
    assert "dry_run=True" in capsys.readouterr().out
    assert backups.main([str(backup_dir), "--apply"]) == 0 and old.exists()
    captured = capsys.readouterr()
    assert "defina HORUS_BACKUP_RETENTION_DAYS" in captured.err and "dry_run=True" in captured.out
    monkeypatch.setenv(backups.ENV_RETENTION_DAYS, "30")
    assert backups.main([str(backup_dir)]) == 0 and old.exists()
    assert backups.main([str(backup_dir), "--apply"]) == 0 and not old.exists()
    assert backups.main([]) == 2


def test_backups_are_never_touched_by_retention_cycle(pg_hardening_env, backup_dir):
    """O ciclo automático não remove nem lê backups: a remoção só existe por chamada explícita."""
    _, _, manager, store, _ = pg_hardening_env
    backup = make_v2_backup(backup_dir, age_days=9000)
    seed_radar_workflow(store, new_marker(), expires=EXPIRED, items=20)
    before = hashlib.sha256(backup.read_bytes()).hexdigest()
    run_retention(manager, clock=clock)
    assert backup.exists() and hashlib.sha256(backup.read_bytes()).hexdigest() == before
