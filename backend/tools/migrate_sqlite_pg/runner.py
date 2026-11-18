"""Orquestrador da migração verificável SQLite -> PostgreSQL."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
from typing import Any
import uuid

from .guards import MigrationGuardError, supabase_target, validate_output_dir, validate_source_path
from .mapping import MAPPING_VERSION
from .pg_loader import MigrationVerificationError, execute_pg_load
from .sqlite_reader import (
    SqliteSourceError,
    backup_and_inspect_sqlite,
    read_and_validate_sqlite,
)


def _resolve_source_files(
    *,
    source_dir: Path | None = None,
    source_db: Path | None = None,
    radar_db: Path | None = None,
) -> tuple[Path, Path | None]:
    if source_dir is not None and source_db is not None:
        raise MigrationGuardError("Informe --source-dir ou --source-db, não ambos.")
    if source_dir is None and source_db is None:
        raise MigrationGuardError("Origem obrigatória (--source-dir ou --source-db).")

    if source_dir is not None:
        sdir = validate_source_path(source_dir)
        if not sdir.is_dir():
            raise MigrationGuardError("--source-dir deve apontar para um diretório.")
        core = sdir / "horus.sqlite3"
        radar_candidate = sdir / "radar" / "radar-youtube.sqlite3"
        radar = radar_candidate if radar_candidate.exists() else None
        return core, radar

    core = validate_source_path(source_db)  # type: ignore[arg-type]
    radar = validate_source_path(radar_db) if radar_db is not None else None
    return core, radar


def run_migration(
    *,
    target_dsn: str,
    source_dir: Path | None = None,
    source_db: Path | None = None,
    radar_db: Path | None = None,
    backup_dir: Path | None = None,
    run_id: str | None = None,
    apply: bool = False,
    inject_failure_after: str | None = None,
    target_mode: str = "loopback",
    user_authorized: bool = False,
) -> dict[str, Any]:
    """Executa prévia (dry-run por padrão) ou migração transacional verificável."""
    parsed_run_id = uuid.UUID(run_id) if run_id else uuid.uuid4()

    report: dict[str, Any] = {
        "run_id": str(parsed_run_id),
        "mode": "apply" if apply else "dry_run",
        "mapping_version": MAPPING_VERSION,
        "status": "failed",
        "rejected": [],
        "inconsistencies": [],
        "tables": {},
    }

    try:
        if target_mode == "supabase":
            supabase_target(target_dsn, user_authorized=user_authorized)
        if backup_dir is None:
            resolved_backup_dir = Path(tempfile.mkdtemp(prefix="horus-mig-backup-"))
        else:
            resolved_backup_dir = validate_output_dir(backup_dir)

        core_path, radar_path = _resolve_source_files(
            source_dir=source_dir, source_db=source_db, radar_db=radar_db
        )
        core_backup, core_sha, user_version = backup_and_inspect_sqlite(
            core_path, resolved_backup_dir, label="horus"
        )
        radar_backup = None
        combined_sha = core_sha
        if radar_path is not None:
            radar_backup, radar_sha, _ = backup_and_inspect_sqlite(
                radar_path, resolved_backup_dir, label="radar"
            )
            combined_sha = hashlib.sha256(
                f"{core_sha}:{radar_sha}".encode("ascii")
            ).hexdigest()

        report["source_schema_version"] = user_version
        report["source_sha256"] = combined_sha
        report["backup_core"] = str(core_backup)
        if radar_backup is not None:
            report["backup_radar"] = str(radar_backup)

        data, sqlite_stats, rejected = read_and_validate_sqlite(
            core_backup, user_version, radar_backup=radar_backup
        )
        report["rejected"] = rejected

        result = execute_pg_load(
            target_dsn,
            data,
            sqlite_stats,
            source_sha256=combined_sha,
            source_schema_version=user_version,
            run_id=parsed_run_id,
            apply=apply,
            inject_failure_after=inject_failure_after,
            target_mode=target_mode,
            user_authorized=user_authorized,
        )
        report.update(result)
        return report

    except SqliteSourceError as exc:
        report["status"] = "rejected"
        report["error_code"] = "sqlite_source_rejected"
        report["error"] = str(exc)
        report["rejected"] = exc.rejected
        return report
    except MigrationVerificationError as exc:
        report["status"] = "failed"
        report["error_code"] = "verification_failed"
        report["error"] = str(exc)
        report["inconsistencies"] = exc.inconsistencies
        return report
    except MigrationGuardError as exc:
        report["status"] = "refused"
        report["error_code"] = "guard_refused"
        report["error"] = str(exc)
        return report
    except Exception as exc:
        report["status"] = "failed"
        report["error_code"] = type(exc).__name__
        report["error"] = (
            "Falha na migração; se ocorreu durante o COMMIT, consulte --check-run-id antes de repetir."
        )
        return report
