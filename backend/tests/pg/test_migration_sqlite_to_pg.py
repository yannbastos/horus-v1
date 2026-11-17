"""Testes PG verificáveis da ferramenta de migração SQLite -> PostgreSQL (HOR-164 I-mig)."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

from schema_support import schema_database

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from migrate_sqlite_pg.mapping import LOAD_ORDER  # noqa: E402
from migrate_sqlite_pg.pg_loader import check_migration_run  # noqa: E402
from migrate_sqlite_pg.runner import run_migration  # noqa: E402
from migration_fixtures.synthetic_builder import (  # noqa: E402
    LARGE_SEQ_BASE,
    PRIVATE_MARKER_PROMPT,
    PRIVATE_MARKER_SCRIPT,
    build_synthetic_data_dir,
)


def _assert_no_private_markers(obj: object) -> None:
    serialized = json.dumps(obj, ensure_ascii=False)
    assert PRIVATE_MARKER_PROMPT not in serialized
    assert PRIVATE_MARKER_SCRIPT not in serialized


@pytest.mark.pg_strong
def test_migration_dry_run_default_and_full_apply_21_tables(pg_cluster, tmp_path):
    source_dir = build_synthetic_data_dir(tmp_path / "copy_v12", target_version=12, include_radar=True)
    backup_dir = tmp_path / "backups"
    run_id = str(uuid.uuid4())

    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")

        # 1. Dry-run por padrão (apply=False) não escreve nada no PG.
        dry_report = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=backup_dir,
            run_id=run_id,
            apply=False,
        )
        assert dry_report["status"] == "dry_run_ok"
        assert dry_report["mode"] == "dry_run"
        assert dry_report["source_schema_version"] == 12
        _assert_no_private_markers(dry_report)
        assert admin.execute("SELECT COUNT(*) FROM horus.workflows").fetchone()[0] == 0
        assert admin.execute("SELECT COUNT(*) FROM horus.migration_runs").fetchone()[0] == 0

        # 2. Carga transacional única (--apply).
        report = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=backup_dir,
            run_id=run_id,
            apply=True,
        )
        assert report["status"] == "succeeded", report
        assert report["inconsistencies"] == []
        assert report["rejected"] == []
        _assert_no_private_markers(report)

        # Todas as 21 tabelas migradas com contagens e hashes idênticos.
        for tbl in LOAD_ORDER:
            assert report["tables"][tbl]["count"] == dry_report["tables"][tbl]["count"]
            assert report["tables"][tbl]["sha256"] == dry_report["tables"][tbl]["sha256"]
            assert report["tables"][tbl]["count"] >= 1

        # Preservação byte a byte de *_canonical (incluindo 1.0 vs 1).
        wf_canon = admin.execute("SELECT payload_canonical FROM horus.workflows WHERE id='wf-1'").fetchone()[0]
        assert b'"ratio":1.0' in bytes(wf_canon)
        j1_canon = admin.execute("SELECT payload_canonical FROM horus.jobs WHERE id='job-1'").fetchone()[0]
        j2_canon = admin.execute("SELECT payload_canonical FROM horus.jobs WHERE id='job-2'").fetchone()[0]
        assert b'"scale":1.0' in bytes(j1_canon)
        assert b'"scale":1' in bytes(j2_canon)

        # IDs grandes e reseed de sequences.
        max_seq = admin.execute("SELECT MAX(seq) FROM horus.human_checkpoints").fetchone()[0]
        assert max_seq == LARGE_SEQ_BASE + 40
        next_seq = admin.execute(
            "SELECT nextval(pg_get_serial_sequence('horus.human_checkpoints', 'seq'))"
        ).fetchone()[0]
        assert next_seq > max_seq

        # Ledger horus.migration_runs gravado na mesma transação.
        ledger = check_migration_run(dsn, run_id)
        assert ledger is not None
        assert ledger["source_sha256"] == report["source_sha256"]
        assert ledger["source_schema_version"] == 12

        # 3. Idempotência de run_id (consulta após COMMIT ambíguo).
        idem_report = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=backup_dir,
            run_id=run_id,
            apply=True,
        )
        assert idem_report["status"] == "already_applied"
        assert idem_report["run_id"] == run_id

        # 4. Repetição no mesmo destino com novo run_id é recusada.
        repeat_report = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=backup_dir,
            run_id=str(uuid.uuid4()),
            apply=True,
        )
        assert repeat_report["status"] == "refused"
        assert repeat_report["error_code"] == "guard_refused"


@pytest.mark.pg_strong
def test_migration_v9_compatibility(pg_cluster, tmp_path):
    source_dir = build_synthetic_data_dir(tmp_path / "copy_v9", target_version=9, include_radar=False)
    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")
        report = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=tmp_path / "bkp",
            apply=True,
        )
        assert report["status"] == "succeeded", report
        assert report["source_schema_version"] == 9
        assert report["tables"]["scripts"]["count"] == 1
        assert report["tables"]["transmissions"]["count"] == 0
        assert report["tables"]["human_checkpoints"]["count"] == 0
        assert admin.execute("SELECT generator FROM horus.scripts WHERE id='scr-1'").fetchone()[0] is None


@pytest.mark.pg_strong
def test_migration_guards_operational_hot_nonempty_and_remote(pg_cluster, tmp_path):
    source_dir = build_synthetic_data_dir(tmp_path / "copy_ok", target_version=12)

    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")

        # Origem operacional recusada (sem tocar em nada real; usa caminho construído).
        local_app = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        for folder in ("com.horus.audiovisual", "Horus", "Programs/Horus"):
            op_db = local_app / folder / "horus.sqlite3"
            res = run_migration(target_dsn=dsn, source_db=op_db, apply=False)
            assert res["status"] == "refused"
            assert res["error_code"] == "guard_refused"

        # Destino remoto / senha em DSN recusado.
        for bad_dsn in (
            "host=db.fakeproject.supabase.co port=5432 dbname=postgres user=horus_app",
            "host=aws-0-sa-east-1.pooler.supabase.com port=5432 dbname=postgres user=horus_app",
            f"{dsn} password=segredo",
        ):
            res = run_migration(target_dsn=bad_dsn, source_dir=source_dir, apply=False)
            assert res["status"] == "refused"
            assert res["error_code"] == "guard_refused"

        # Cópia quente (-wal presente) recusada.
        hot_dir = build_synthetic_data_dir(tmp_path / "hot_copy", target_version=12)
        (hot_dir / "horus.sqlite3-wal").write_bytes(b"wal")
        hot_res = run_migration(target_dsn=dsn, source_dir=hot_dir, apply=False)
        assert hot_res["status"] == "refused"
        assert hot_res["error_code"] == "guard_refused"

        # Destino não vazio recusado.
        admin.execute(
            "INSERT INTO horus.media(id,path,name,size,modified_ns,created_at) "
            "VALUES ('pre','/pre.mp4','pre.mp4',1,1,'2026-11-17T00:00:00Z')"
        )
        non_empty = run_migration(target_dsn=dsn, source_dir=source_dir, apply=True)
        assert non_empty["status"] == "refused"
        assert non_empty["error_code"] == "guard_refused"


@pytest.mark.pg_strong
def test_migration_mid_load_failure_rolls_back_completely(pg_cluster, tmp_path):
    source_dir = build_synthetic_data_dir(tmp_path / "copy_fail", target_version=12)
    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")
        res = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=tmp_path / "bkp",
            apply=True,
            inject_failure_after="artifacts",
        )
        assert res["status"] == "failed"
        _assert_no_private_markers(res)
        # Rollback total: nada gravado em nenhuma tabela nem em migration_runs.
        for tbl in LOAD_ORDER:
            assert admin.execute(f"SELECT COUNT(*) FROM horus.{tbl}").fetchone()[0] == 0
        assert admin.execute("SELECT COUNT(*) FROM horus.migration_runs").fetchone()[0] == 0


@pytest.mark.pg_strong
def test_migration_rejects_unexpected_table_and_orphan_row(pg_cluster, tmp_path):
    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")

        # 1. Tabela inesperada rejeitada.
        bad_tbl_dir = build_synthetic_data_dir(tmp_path / "bad_tbl", target_version=12)
        with closing(sqlite3.connect(bad_tbl_dir / "horus.sqlite3")) as db:
            db.execute("CREATE TABLE rogue_table(id INTEGER PRIMARY KEY)")
            db.commit()
        res_tbl = run_migration(target_dsn=dsn, source_dir=bad_tbl_dir, backup_dir=tmp_path / "b1", apply=True)
        assert res_tbl["status"] == "rejected"
        assert any(r["table"] == "rogue_table" for r in res_tbl["rejected"])

        # 2. Linha inconsistente (órfão em depends_on sem FK ativa no SQLite) -> rollback e erro.
        orphan_dir = build_synthetic_data_dir(tmp_path / "orphan", target_version=12)
        with closing(sqlite3.connect(orphan_dir / "horus.sqlite3")) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("UPDATE jobs SET depends_on='job-inexistente' WHERE id='job-2'")
            db.commit()
        res_orphan = run_migration(target_dsn=dsn, source_dir=orphan_dir, backup_dir=tmp_path / "b2", apply=True)
        assert res_orphan["status"] in {"rejected", "failed"}
        assert admin.execute("SELECT COUNT(*) FROM horus.jobs").fetchone()[0] == 0


@pytest.mark.pg_strong
def test_migration_cli_subprocess_exit_codes(pg_cluster, tmp_path):
    source_dir = build_synthetic_data_dir(tmp_path / "cli_src", target_version=12)
    report_path = tmp_path / "report.json"
    run_id = str(uuid.uuid4())
    script = Path(__file__).resolve().parents[2] / "tools" / "migrate_sqlite_to_pg.py"

    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")

        # Dry-run via CLI.
        dry = subprocess.run(
            [
                sys.executable,
                str(script),
                "--source-dir",
                str(source_dir),
                "--target-dsn",
                dsn,
                "--run-id",
                run_id,
                "--report-out",
                str(report_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert dry.returncode == 0, dry.stderr
        assert report_path.exists()
        _assert_no_private_markers(report_path.read_text(encoding="utf-8"))

        # Apply via CLI.
        applied = subprocess.run(
            [
                sys.executable,
                str(script),
                "--source-dir",
                str(source_dir),
                "--target-dsn",
                dsn,
                "--run-id",
                run_id,
                "--apply",
                "--report-out",
                str(report_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert applied.returncode == 0, applied.stderr

        # Consulta --check-run-id.
        chk = subprocess.run(
            [
                sys.executable,
                str(script),
                "--target-dsn",
                dsn,
                "--check-run-id",
                run_id,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert chk.returncode == 0, chk.stderr
        assert run_id in chk.stdout

        # Repetição com novo run_id retorna exit != 0.
        dup = subprocess.run(
            [
                sys.executable,
                str(script),
                "--source-dir",
                str(source_dir),
                "--target-dsn",
                dsn,
                "--apply",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert dup.returncode != 0
