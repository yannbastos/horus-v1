"""CLI verificável de migração SQLite (v9-v12) -> PostgreSQL (HOR-164 I-mig).

Dry-run por padrão. Exige cópia fria explícita fora de pastas operacionais e
destino PostgreSQL descartável em loopback. Nunca executa autorização, produção
ou inferência e não registra conteúdo privado em logs ou relatórios.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from migrate_sqlite_pg.guards import MigrationGuardError
from migrate_sqlite_pg.pg_loader import check_migration_run
from migrate_sqlite_pg.runner import run_migration


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migração verificável SQLite (v9-v12) -> PostgreSQL (dry-run por padrão)."
    )
    parser.add_argument("--source-dir", type=Path, help="Cópia explícita completa de data_dir")
    parser.add_argument("--source-db", type=Path, help="Cópia explícita fria de horus.sqlite3")
    parser.add_argument("--radar-db", type=Path, help="Cópia explícita fria de radar-youtube.sqlite3")
    parser.add_argument("--target-dsn", required=True, help="DSN PostgreSQL loopback descartável (sem senha)")
    parser.add_argument("--backup-dir", type=Path, help="Diretório para backup imutável da cópia")
    parser.add_argument("--run-id", help="UUID explícito da execução (para idempotência)")
    parser.add_argument(
        "--check-run-id",
        help="Consulta horus.migration_runs após COMMIT ambíguo e encerra sem escrever",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Efetua carga transacional única (sem esta flag, executa dry-run)",
    )
    parser.add_argument("--report-out", type=Path, help="Caminho para gravar relatório JSON sanitizado")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.check_run_id:
        try:
            row = check_migration_run(args.target_dsn, args.check_run_id)
        except (MigrationGuardError, ValueError) as exc:
            print(json.dumps({"status": "refused", "error": str(exc)}, ensure_ascii=False))
            return 1
        if row is None:
            print(json.dumps({"status": "not_found", "run_id": args.check_run_id}, ensure_ascii=False))
            return 1
        out = {
            "status": "found",
            "run_id": row["run_id"],
            "source_sha256": row["source_sha256"],
            "mapping_version": row["mapping_version"],
            "source_schema_version": row["source_schema_version"],
            "applied_at": row["applied_at"].isoformat(),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    report = run_migration(
        target_dsn=args.target_dsn,
        source_dir=args.source_dir,
        source_db=args.source_db,
        radar_db=args.radar_db,
        backup_dir=args.backup_dir,
        run_id=args.run_id,
        apply=args.apply,
    )

    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") in {"dry_run_ok", "succeeded", "already_applied"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
