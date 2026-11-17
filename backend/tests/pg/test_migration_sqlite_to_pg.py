"""Testes PG verificáveis da ferramenta de migração SQLite -> PostgreSQL (HOR-164 I-mig)."""
from __future__ import annotations

from contextlib import closing
import ctypes
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

from migrate_sqlite_pg.guards import (  # noqa: E402
    MigrationGuardError,
    validate_output_dir,
    validate_report_out,
    validate_source_path,
)
from migrate_sqlite_pg.mapping import LOAD_ORDER, TABLE_SPECS  # noqa: E402
from migrate_sqlite_pg.pg_loader import check_migration_run  # noqa: E402
from migrate_sqlite_pg.runner import run_migration  # noqa: E402
from migrate_sqlite_pg.sqlite_reader import compute_table_stats  # noqa: E402
from migration_fixtures.synthetic_builder import (  # noqa: E402
    LARGE_SEQ_BASE,
    PRIVATE_MARKER_PROMPT,
    PRIVATE_MARKER_SCRIPT,
    build_synthetic_data_dir,
)
from horus_backend.migrations import MIGRATIONS  # noqa: E402


def _assert_no_private_markers(obj: object) -> None:
    serialized = json.dumps(obj, ensure_ascii=False)
    assert PRIVATE_MARKER_PROMPT not in serialized
    assert PRIVATE_MARKER_SCRIPT not in serialized


def _win_short_path(path: Path) -> str | None:
    if os.name != "nt" or not path.exists():
        return None
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
    return buf.value if 0 < n < len(buf) else None


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

        # 2. Carga transacional única (--apply) em novo backup_dir vazio.
        report = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=tmp_path / "backups_apply",
            run_id=run_id,
            apply=True,
        )
        assert report["status"] == "succeeded", report
        assert report["inconsistencies"] == []
        assert report["rejected"] == []
        _assert_no_private_markers(report)

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

        # 3. Idempotência de run_id.
        idem_report = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=tmp_path / "backups_idem",
            run_id=run_id,
            apply=True,
        )
        assert idem_report["status"] == "already_applied"
        assert idem_report["run_id"] == run_id

        # 4. Repetição no mesmo destino com novo run_id é recusada.
        repeat_report = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=tmp_path / "backups_rep",
            run_id=str(uuid.uuid4()),
            apply=True,
        )
        assert repeat_report["status"] == "refused"
        assert repeat_report["error_code"] == "guard_refused"


@pytest.mark.pg_strong
@pytest.mark.parametrize("ver", [9, 10, 11])
def test_migration_v9_to_v11_compatibility(pg_cluster, tmp_path, ver):
    source_dir = build_synthetic_data_dir(tmp_path / f"copy_v{ver}", target_version=ver, include_radar=False)
    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")
        report = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=tmp_path / f"bkp_v{ver}",
            apply=True,
        )
        assert report["status"] == "succeeded", report
        assert report["source_schema_version"] == ver
        assert report["tables"]["scripts"]["count"] == 1
        assert report["tables"]["transmissions"]["count"] == (1 if ver >= 11 else 0)
        assert report["tables"]["human_checkpoints"]["count"] == 0
        if ver == 9:
            assert admin.execute("SELECT generator FROM horus.scripts WHERE id='scr-1'").fetchone()[0] is None


@pytest.mark.pg_strong
def test_migration_empty_v12_database(pg_cluster, tmp_path):
    empty_dir = tmp_path / "empty_v12"
    empty_dir.mkdir()
    with closing(sqlite3.connect(empty_dir / "horus.sqlite3")) as db:
        for ver in range(1, 13):
            for stmt in MIGRATIONS[ver]:
                db.execute(stmt)
            db.execute(f"PRAGMA user_version={ver}")
        db.commit()

    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")
        res = run_migration(target_dsn=dsn, source_dir=empty_dir, backup_dir=tmp_path / "bkp_empty", apply=True)
        assert res["status"] == "succeeded", res
        assert all(res["tables"][t]["count"] == 0 for t in LOAD_ORDER)


@pytest.mark.pg_strong
def test_migration_guards_path_variations_outputs_hot_and_nonempty(pg_cluster, tmp_path):
    source_dir = build_synthetic_data_dir(tmp_path / "copy_ok", target_version=12)
    local_app = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))

    # Variações de bypass na guarda operacional (\\?\, \\.\, UNC, relativo, case, ..).
    for folder in ("com.horus.audiovisual", "Horus", "Programs/Horus"):
        op_db = local_app / folder / "horus.sqlite3"
        variants = [
            str(op_db),
            str(op_db).upper(),
            rf"\\?\{op_db}",
            rf"\\.\{op_db}",
            str(local_app / folder / "sub" / ".." / "horus.sqlite3"),
        ]
        for variant in variants:
            with pytest.raises(MigrationGuardError):
                validate_source_path(variant, must_exist=False)
            with pytest.raises(MigrationGuardError):
                validate_output_dir(variant)
            with pytest.raises(MigrationGuardError):
                validate_report_out(variant)

    for bad_path in (
        "relative/horus.sqlite3",
        r"\\server\share\horus.sqlite3",
        r"\\?\UNC\server\share\horus.sqlite3",
        r"\\.\C:\Temp\horus.sqlite3",
    ):
        with pytest.raises(MigrationGuardError):
            validate_source_path(bad_path, must_exist=False)

    # 8.3 short path dentro de pasta operacional (se existir) ou caminho não operacional fora de TEMP.
    short_local = _win_short_path(local_app)
    if short_local:
        with pytest.raises(MigrationGuardError):
            validate_source_path(Path(short_local) / "Horus" / "horus.sqlite3", must_exist=False)

    # Symlink / junction recusados se o ambiente permitir criar.
    link_target = tmp_path / "link_target"
    link_target.mkdir()
    link_path = tmp_path / "link_junction"
    try:
        link_path.symlink_to(link_target, target_is_directory=True)
        with pytest.raises(MigrationGuardError):
            validate_source_path(link_path / "horus.sqlite3", must_exist=False)
        with pytest.raises(MigrationGuardError):
            validate_output_dir(link_path / "bkp")
    except OSError:
        pass

    # --backup-dir não vazio / --report-out existente / fora de TEMP recusados.
    non_empty_bkp = tmp_path / "non_empty_bkp"
    non_empty_bkp.mkdir()
    (non_empty_bkp / "old.sqlite3").write_bytes(b"x")
    with pytest.raises(MigrationGuardError):
        validate_output_dir(non_empty_bkp)
    with pytest.raises(MigrationGuardError):
        validate_output_dir(Path(os.environ.get("TEMP", str(tmp_path))))

    existing_report = tmp_path / "existing.json"
    existing_report.write_text("{}", encoding="utf-8")
    with pytest.raises(MigrationGuardError):
        validate_report_out(existing_report)

    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")

        # Destino remoto / senha em DSN recusado.
        for bad_dsn in (
            "host=db.fakeproject.supabase.co port=5432 dbname=postgres user=horus_app",
            "host=aws-0-sa-east-1.pooler.supabase.com port=5432 dbname=postgres user=horus_app",
            f"{dsn} password=segredo",
        ):
            res = run_migration(target_dsn=bad_dsn, source_dir=source_dir, apply=False)
            assert res["status"] == "refused"
            assert res["error_code"] == "guard_refused"

        # Cópia quente (-wal, -shm, -journal) recusada.
        for sidecar in ("-wal", "-shm", "-journal"):
            hot_dir = build_synthetic_data_dir(tmp_path / f"hot_{sidecar}", target_version=12)
            (hot_dir / f"horus.sqlite3{sidecar}").write_bytes(b"sidecar")
            hot_res = run_migration(target_dsn=dsn, source_dir=hot_dir, apply=False)
            assert hot_res["status"] == "refused"
            assert hot_res["error_code"] == "guard_refused"

        # Destino não vazio em tabela de coordenação (160300) recusado.
        admin.execute(
            "INSERT INTO horus.preferences(device_id,key,value,updated_at) "
            "VALUES ('dev-1','k','{}'::jsonb,'2026-11-17T00:00:00Z')"
        )
        non_empty = run_migration(target_dsn=dsn, source_dir=source_dir, apply=True)
        assert non_empty["status"] == "refused"
        assert non_empty["error_code"] == "guard_refused"


@pytest.mark.pg_strong
def test_migration_dry_run_rejects_nul_utf8_null_constraints_and_cycles(pg_cluster, tmp_path):
    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")

        def _check_dry_rejected(name: str, mutator) -> None:
            sdir = build_synthetic_data_dir(tmp_path / name, target_version=12)
            with closing(sqlite3.connect(sdir / "horus.sqlite3")) as db:
                mutator(db)
                db.commit()
            res = run_migration(target_dsn=dsn, source_dir=sdir, backup_dir=tmp_path / f"bkp_{name}", apply=False)
            assert res["status"] == "rejected", f"{name} deveria rejeitar no dry-run: {res}"
            assert res["rejected"], f"{name} deveria popular rejected"

        _check_dry_rejected("nul_str", lambda db: db.execute("UPDATE events SET message = 'bad' || char(0) || 'msg'"))
        _check_dry_rejected("nul_json", lambda db: db.execute("UPDATE jobs SET payload = '{\"k\":\"a\\u0000b\"}' WHERE id='job-1'"))
        _check_dry_rejected("bad_utf8", lambda db: db.execute("UPDATE media SET name = CAST(X'FFFE' AS TEXT)"))
        def _insert_null_not_null(db: sqlite3.Connection) -> None:
            db.executescript(
                "DROP TABLE media;"
                "CREATE TABLE media(id TEXT PRIMARY KEY, path TEXT, name TEXT, size INTEGER, modified_ns INTEGER, created_at TEXT);"
                "INSERT INTO media VALUES ('med-1', '/a.mp4', NULL, 1, 1, '2026-11-17T00:00:00Z');"
            )

        _check_dry_rejected("null_not_null", _insert_null_not_null)
        _check_dry_rejected("scalar_json", lambda db: db.execute("UPDATE jobs SET payload = '123' WHERE id='job-1'"))
        _check_dry_rejected("nan_json", lambda db: db.execute("UPDATE jobs SET payload = '{\"x\":NaN}' WHERE id='job-1'"))
        _check_dry_rejected(
            "neg_decimal",
            lambda db: db.execute(
                "INSERT INTO transmissions(seq,id,at,kind,stage,result,cost_usd) "
                "VALUES (99,'tx-neg','2026-11-17T10:00:00Z','submit','proposal','completed','-1.5')"
            ),
        )
        _check_dry_rejected("bad_ts", lambda db: db.execute("UPDATE media SET created_at = 'not-a-date'"))
        _check_dry_rejected(
            "pg_check_expires",
            lambda db: db.execute(
                "INSERT INTO human_checkpoints(seq,id,action,subject,description,approved_by,operator_id,approved_at,expires_at,request) "
                "VALUES (99,'hc-bad','publish',?,'desc','human','op','2026-11-17T10:05:00Z','2026-11-17T10:00:00Z','{}')",
                ("1" * 64,),
            ),
        )
        _check_dry_rejected("cycle_jobs", lambda db: db.execute("UPDATE jobs SET depends_on = 'job-2' WHERE id = 'job-1'"))


@pytest.mark.pg_strong
def test_migration_mutation_on_target_triggers_verification_failure_and_rollback(pg_cluster, tmp_path):
    source_dir = build_synthetic_data_dir(tmp_path / "copy_mut", target_version=12)
    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")
        # Injeta mutação no destino para provar que a verificação pós-carga não é vácua.
        admin.execute(
            "CREATE FUNCTION horus.test_tamper_media() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN NEW.size := NEW.size + 1; RETURN NEW; END $$;"
            "CREATE TRIGGER test_tamper_media BEFORE INSERT ON horus.media "
            "FOR EACH ROW EXECUTE FUNCTION horus.test_tamper_media();"
        )
        res = run_migration(target_dsn=dsn, source_dir=source_dir, backup_dir=tmp_path / "bkp_mut", apply=True)
        assert res["status"] == "failed"
        assert res["error_code"] == "verification_failed"
        assert any(i["table"] == "media" and i["check"] == "sha256" for i in res["inconsistencies"])
        assert admin.execute("SELECT COUNT(*) FROM horus.media").fetchone()[0] == 0
        assert admin.execute("SELECT COUNT(*) FROM horus.migration_runs").fetchone()[0] == 0


@pytest.mark.pg_strong
def test_migration_mid_load_failure_rolls_back_completely(pg_cluster, tmp_path):
    source_dir = build_synthetic_data_dir(tmp_path / "copy_fail", target_version=12)
    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")
        res = run_migration(
            target_dsn=dsn,
            source_dir=source_dir,
            backup_dir=tmp_path / "bkp_fail",
            apply=True,
            inject_failure_after="artifacts",
        )
        assert res["status"] == "failed"
        _assert_no_private_markers(res)
        for tbl in LOAD_ORDER:
            assert admin.execute(f"SELECT COUNT(*) FROM horus.{tbl}").fetchone()[0] == 0
        assert admin.execute("SELECT COUNT(*) FROM horus.migration_runs").fetchone()[0] == 0


@pytest.mark.pg_strong
def test_migration_rejects_unexpected_table_and_orphan_row(pg_cluster, tmp_path):
    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")

        bad_tbl_dir = build_synthetic_data_dir(tmp_path / "bad_tbl", target_version=12)
        with closing(sqlite3.connect(bad_tbl_dir / "horus.sqlite3")) as db:
            db.execute("CREATE TABLE rogue_table(id INTEGER PRIMARY KEY)")
            db.commit()
        res_tbl = run_migration(target_dsn=dsn, source_dir=bad_tbl_dir, backup_dir=tmp_path / "b1", apply=True)
        assert res_tbl["status"] == "rejected"
        assert any(r["table"] == "rogue_table" for r in res_tbl["rejected"])

        orphan_dir = build_synthetic_data_dir(tmp_path / "orphan", target_version=12)
        with closing(sqlite3.connect(orphan_dir / "horus.sqlite3")) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("UPDATE jobs SET depends_on='job-inexistente' WHERE id='job-2'")
            db.commit()
        res_orphan = run_migration(target_dsn=dsn, source_dir=orphan_dir, backup_dir=tmp_path / "b2", apply=True)
        assert res_orphan["status"] in {"rejected", "failed"}
        assert admin.execute("SELECT COUNT(*) FROM horus.jobs").fetchone()[0] == 0


@pytest.mark.pg_strong
def test_migration_pg_dump_restore_revalidates_hashes(pg_cluster, tmp_path):
    suffix = ".exe" if os.name == "nt" else ""
    pg_dump = pg_cluster.bin / f"pg_dump{suffix}"
    pg_restore = pg_cluster.bin / f"pg_restore{suffix}"
    if not pg_dump.exists() or not pg_restore.exists():
        pytest.skip("pg_dump/pg_restore ausentes no binário do harness")

    source_dir = build_synthetic_data_dir(tmp_path / "dump_src", target_version=12, include_radar=True)
    dump_file = tmp_path / "horus_migrated.dump"

    with schema_database(pg_cluster) as (admin, _pool):
        src_db = admin.info.dbname
        dsn = pg_cluster.dsn(src_db, "postgres")
        res = run_migration(target_dsn=dsn, source_dir=source_dir, backup_dir=tmp_path / "bkp_dump", apply=True)
        assert res["status"] == "succeeded", res

        subprocess.run(
            [
                str(pg_dump),
                "-h", "127.0.0.1",
                "-p", str(pg_cluster.port),
                "-U", "postgres",
                "-Fc",
                "--schema=horus",
                "-f", str(dump_file),
                src_db,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    with schema_database(pg_cluster) as (restore_admin, _restore_pool):
        restored_db = restore_admin.info.dbname
        restore_admin.execute("DROP SCHEMA horus CASCADE")
        subprocess.run(
            [
                str(pg_restore),
                "-h", "127.0.0.1",
                "-p", str(pg_cluster.port),
                "-U", "postgres",
                "-d", restored_db,
                str(dump_file),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        for tbl in LOAD_ORDER:
            spec = TABLE_SPECS[tbl]
            cols = ", ".join(spec.pg_cols)
            order = ", ".join(spec.pk_cols)
            rows = restore_admin.execute(f"SELECT {cols} FROM horus.{tbl} ORDER BY {order}").fetchall()
            col_names = list(spec.pg_cols)
            row_dicts = [dict(zip(col_names, r)) for r in rows]
            stats = compute_table_stats(tbl, row_dicts)
            assert stats["count"] == res["tables"][tbl]["count"]
            assert stats["sha256"] == res["tables"][tbl]["sha256"]


@pytest.mark.pg_strong
def test_migration_cli_subprocess_exit_codes(pg_cluster, tmp_path):
    source_dir = build_synthetic_data_dir(tmp_path / "cli_src", target_version=12)
    report_dry = tmp_path / "report_dry.json"
    report_apply = tmp_path / "report_apply.json"
    run_id = str(uuid.uuid4())
    script = Path(__file__).resolve().parents[2] / "tools" / "migrate_sqlite_to_pg.py"

    with schema_database(pg_cluster) as (admin, _pool):
        dsn = pg_cluster.dsn(admin.info.dbname, "postgres")

        dry = subprocess.run(
            [
                sys.executable,
                str(script),
                "--source-dir", str(source_dir),
                "--target-dsn", dsn,
                "--backup-dir", str(tmp_path / "cli_bkp_dry"),
                "--run-id", run_id,
                "--report-out", str(report_dry),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert dry.returncode == 0, dry.stderr
        assert report_dry.exists()
        _assert_no_private_markers(report_dry.read_text(encoding="utf-8"))

        # --report-out existente é recusado (não sobrescreve).
        overwrite_refused = subprocess.run(
            [
                sys.executable,
                str(script),
                "--source-dir", str(source_dir),
                "--target-dsn", dsn,
                "--report-out", str(report_dry),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert overwrite_refused.returncode != 0

        applied = subprocess.run(
            [
                sys.executable,
                str(script),
                "--source-dir", str(source_dir),
                "--target-dsn", dsn,
                "--backup-dir", str(tmp_path / "cli_bkp_apply"),
                "--run-id", run_id,
                "--apply",
                "--report-out", str(report_apply),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert applied.returncode == 0, applied.stderr

        chk = subprocess.run(
            [
                sys.executable,
                str(script),
                "--target-dsn", dsn,
                "--check-run-id", run_id,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert chk.returncode == 0, chk.stderr
        assert run_id in chk.stdout

        dup = subprocess.run(
            [
                sys.executable,
                str(script),
                "--source-dir", str(source_dir),
                "--target-dsn", dsn,
                "--apply",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert dup.returncode != 0
