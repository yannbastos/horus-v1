"""Valida em modo somente leitura o Supabase dev salvo nos Ajustes do Hórus."""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from psycopg_pool import ConnectionPool

from horus_backend.config import default_data_dir
from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.database.manager import MIGRATION_LOCK
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.transport import ConnectFailure, connection_type
from horus_backend.database.vault import ConnectionVault
from horus_backend.worker_lock import WORKER_FENCING_LOCK

from pg_apply_migrations import migration_files


EXIT = {
    "vault": 10, "endpoint": 11, "dns": 12, "tls": 13, "connection": 14,
    "authentication": 15, "role": 16, "ledger": 17, "rls": 18, "rollback": 19,
    "pool": 20, "lease": 21,
}
TRANSPORT_ERRORS = {
    "database_endpoint_not_allowed": "endpoint",
    "database_dns_failed": "dns",
    "database_tls_invalid": "tls",
    "database_auth_failed": "authentication",
}


def role_is_restricted(conn):
    return conn.execute("""SELECT current_user = 'horus_app' AND session_user = current_user
        AND NOT (r.rolsuper OR r.rolbypassrls OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication)
        AND NOT EXISTS (SELECT FROM pg_auth_members WHERE member = r.oid)
        AND NOT has_database_privilege(current_database(), 'CREATE,TEMPORARY')
        AND NOT EXISTS (SELECT FROM pg_namespace
            WHERE has_schema_privilege(oid, 'CREATE') OR nspowner = r.oid)
        AND NOT EXISTS (SELECT FROM pg_class WHERE relowner = r.oid)
        AND NOT EXISTS (SELECT FROM pg_proc WHERE proowner = r.oid)
        AND NOT EXISTS (SELECT FROM pg_type WHERE typowner = r.oid)
        FROM pg_roles r WHERE r.rolname = current_user""").fetchone() == (True,)


def ledger_matches(conn):
    expected = {version: digest for version, (_, digest) in migration_files().items()}
    rows = dict(conn.execute("SELECT version, checksum FROM horus.schema_migrations").fetchall())
    # A migration de sincronização pode pertencer ao ledger histórico do Supabase.
    if "20261115150000" not in rows:
        expected.pop("20261115150000", None)
    return rows == expected and max(rows, default=None) == EXPECTED_SCHEMA_VERSION


def rls_is_forced(conn):
    return conn.execute("""SELECT count(*) >= 33 AND bool_and(c.relrowsecurity AND c.relforcerowsecurity)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'horus' AND c.relkind IN ('r', 'p')""").fetchone() == (True,)


def tls_in_use(conn):
    return conn.pgconn.ssl_in_use


def rollback_round_trip(conn):
    key = "horus.validation_probe"
    value = uuid4().hex
    query = "SELECT current_setting(%s, true)"
    with conn.transaction(force_rollback=True):
        conn.execute("SET TRANSACTION READ ONLY")
        if conn.execute("SELECT set_config(%s, %s, true)", (key, value)).fetchone() != (value,):
            return False
        if conn.execute(query, (key,)).fetchone() != (value,):
            return False
    # Um GUC customizado recém-criado pode ficar com valor vazio após ROLLBACK.
    return conn.execute(query, (key,)).fetchone() in {(None,), ("",)}


def check_lease(conn):
    # O app mantém o lock compartilhado da migração durante a sessão.
    pid, locked = conn.execute("SELECT pg_backend_pid(), pg_try_advisory_lock_shared(%s, %s)",
                               MIGRATION_LOCK).fetchone()
    if not locked:
        return False
    try:
        with conn.transaction(force_rollback=True):
            conn.execute("SET TRANSACTION READ ONLY")
            if conn.execute("SELECT pg_try_advisory_xact_lock_shared(%s, %s)",
                            WORKER_FENCING_LOCK).fetchone() != (True,):
                return False
            conn.execute("SELECT id, lease_until, fencing FROM horus.worker_instances LIMIT 0")
            conn.execute("SELECT worker_instance_id, lease_until, fencing_token FROM horus.jobs LIMIT 0")
    finally:
        returned = conn.execute("SELECT pg_backend_pid(), pg_advisory_unlock_shared(%s, %s)",
                                MIGRATION_LOCK).fetchone()
    return returned == (pid, True)


def validate(vault):
    stage = "vault"
    checks = []
    try:
        saved = vault.read()
        if saved is None:
            return result("not_configured", stage, checks)
        checks.append(stage)
        stage = "endpoint"
        config = ConnectionRequest.model_validate(saved.model_dump())
        checks.append(stage)
        stage = "connection"
        kind = connection_type(config)
        with kind.connect() as conn:
            stage = "tls"
            if not tls_in_use(conn):
                return result("tls", stage, checks)
            checks.extend(("tls", "connection"))
            stage = "role"
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                for stage, check in (("role", role_is_restricted), ("ledger", ledger_matches),
                                     ("rls", rls_is_forced)):
                    if not check(conn):
                        return result(stage, stage, checks)
                    checks.append(stage)
            stage = "rollback"
            if not rollback_round_trip(conn):
                return result(stage, stage, checks)
            checks.append(stage)
            stage = "pool"
            with ConnectionPool("", connection_class=kind, min_size=1, max_size=1,
                                timeout=3, reconnect_timeout=3, open=False) as pool:
                pool.wait(timeout=3)
                with pool.connection() as leased:
                    if not tls_in_use(leased):
                        return result(stage, stage, checks)
                    pid = leased.execute("SELECT pg_backend_pid()").fetchone()
                with pool.connection() as leased:
                    if leased.execute("SELECT pg_backend_pid()").fetchone() != pid:
                        return result(stage, stage, checks)
                    checks.append(stage)
                    stage = "lease"
                    if not check_lease(leased):
                        return result(stage, stage, checks)
                    checks.append(stage)
    except ConnectFailure as error:
        if stage == "connection":
            stage = TRANSPORT_ERRORS.get(error.code, stage)
        return result(stage, stage, checks)
    except Exception:
        return result(stage, stage, checks)
    return result(None, None, checks)


def result(code, stage, checks):
    return EXIT[stage] if stage else 0, {
        "status": "failed" if stage else "passed", "failed_check": code, "checks": checks,
        "expected_schema_version": EXPECTED_SCHEMA_VERSION,
    }


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    path = Path(os.environ.get("HORUS_DATA_DIR", default_data_dir()))
    code, report = validate(ConnectionVault(path))
    print(json.dumps(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
