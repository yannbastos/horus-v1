"""Aplicador administrativo das migrations SQL do Hórus."""
import argparse
from getpass import GetPassWarning, getpass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import warnings

import psycopg

from horus_backend.database.manager import MIGRATION_LOCK
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.transport import CA_FILE, resolve_public


MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"


class MigrationRefused(Exception):
    """Código fixo para o relatório de manutenção."""


def migration_files(directory=MIGRATIONS):
    files = {}
    for path in sorted(directory.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if not re.fullmatch(r"[0-9]{14}", version) or version in files:
            raise MigrationRefused("invalid_migration_version")
        sql = path.read_text(encoding="utf-8-sig")
        files[version] = sql, sha256(sql.encode("utf-8")).hexdigest()
    if not files:
        raise MigrationRefused("missing_migrations")
    return files


def run_migrations(conn, *, apply=False, directory=MIGRATIONS):
    files = migration_files(directory)
    if not conn.autocommit:
        raise MigrationRefused("administrative_session_required")
    with conn.transaction():
        if not apply:
            conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL search_path = pg_catalog")
        conn.execute("SET LOCAL lock_timeout = '5s'")
        conn.execute("SET LOCAL statement_timeout = '120s'")
        conn.execute("SET LOCAL row_security = off")
        admin = conn.execute("""SELECT current_user = session_user
            AND (rolsuper OR (rolcreaterole AND rolbypassrls))
            FROM pg_roles WHERE rolname = current_user""").fetchone()
        if not admin or not admin[0]:
            raise MigrationRefused("administrative_session_required")
        if apply:
            conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", MIGRATION_LOCK)
        else:
            conn.execute("SELECT pg_advisory_xact_lock_shared(%s, %s)", MIGRATION_LOCK)
        ledger = conn.execute("SELECT to_regclass('horus.schema_migrations')").fetchone()[0]
        recorded = dict(conn.execute(
            "SELECT version, checksum FROM horus.schema_migrations"
        ).fetchall()) if ledger else {}
        if set(recorded) - files.keys():
            raise MigrationRefused("unknown_applied_version")
        pending = []
        for version, (_, digest) in files.items():
            if version not in recorded:
                pending.append(version)
            elif pending:
                raise MigrationRefused("migration_history_gap")
            elif recorded[version] != digest:
                raise MigrationRefused("checksum_mismatch")
        schema, sync_schema = conn.execute(
            "SELECT to_regnamespace('horus'), to_regnamespace('horus_sync')"
        ).fetchone()
        if (schema and not ledger) or bool(sync_schema) != ("20261115150000" in recorded):
            raise MigrationRefused("untracked_schema")
        if apply:
            # A migration histórica antecede a criação do ledger. A transação
            # abrange a cadeia inteira, inclusive o registro da primeira versão.
            entries = []
            for version in pending:
                sql, digest = files[version]
                conn.execute(sql, prepare=False)
                entries.append((version, digest))
            if entries:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO horus.schema_migrations(version, checksum, applied_at)
                           VALUES (%s, %s, clock_timestamp())""", entries,
                    )
    return {"status": "applied" if apply else "dry_run",
            "applied" if apply else "pending": pending, "verified": sorted(recorded)}


def admin_connection():
    # Nenhuma configuração libpq ambiental ou credencial do aplicativo.
    if any(name.upper().startswith("PG") for name in os.environ):
        raise MigrationRefused("unsafe_libpq_environment")
    print("Host do pooler de sessão (projeto dev): ", end="", file=sys.stderr, flush=True)
    host = ConnectionRequest.hostname(input().strip())
    print("Referência do projeto dev: ", end="", file=sys.stderr, flush=True)
    project = input().strip()
    if not re.fullmatch(r"[a-z0-9]{20}", project):
        raise MigrationRefused("invalid_project_ref")
    address = resolve_public(host, 5432)
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        password = getpass("Senha administrativa do banco dev: ")
    if not password:
        raise MigrationRefused("missing_admin_password")
    return psycopg.connect(host=host, hostaddr=address, port=5432, dbname="postgres",
                           user=f"postgres.{project}", password=password,
                           sslmode="verify-full", sslrootcert=str(CA_FILE),
                           sslcert="", sslkey="", sslcrl="", gssencmode="disable",
                           connect_timeout=5, autocommit=True, prepare_threshold=None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Aplica no projeto dev selecionado.")
    args = parser.parse_args()
    try:
        with admin_connection() as conn:
            report = run_migrations(conn, apply=args.apply)
    except MigrationRefused as error:
        print(json.dumps({"status": "failed", "code": str(error)}))
        return 1
    except Exception:
        print(json.dumps({"status": "failed", "code": "maintenance_failed"}))
        return 2
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
