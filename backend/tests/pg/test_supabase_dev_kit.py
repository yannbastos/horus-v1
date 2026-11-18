"""Valida o kit com dados sintéticos, DNS e TLS simulados."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from horus_backend.database import transport
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault, SERVICE


@pytest.fixture(scope="module")
def kit():
    from importlib import import_module
    import sys

    scripts = Path(__file__).resolve().parents[3] / "scripts"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "path", [str(scripts), *sys.path])
        yield import_module("pg_apply_migrations"), import_module("validate_supabase_dev")


@pytest.fixture
def fresh_database(pg_cluster):
    with pg_cluster.connect(autocommit=True) as admin:
        for role in ("anon", "authenticated"):
            admin.execute(sql.SQL("DO $$ BEGIN CREATE ROLE {}; EXCEPTION WHEN duplicate_object "
                                  "THEN NULL; END $$").format(sql.Identifier(role)))
    with pg_cluster.database() as pool:
        with pool.connection() as conn:
            name = conn.info.dbname
        with pg_cluster.connect(name, autocommit=True) as admin:
            admin.execute("DROP SCHEMA horus CASCADE; CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY)")
            yield admin, name


def test_apply_dry_run_replay_and_checksum(kit, fresh_database, tmp_path):
    runner, _ = kit
    admin, _ = fresh_database
    files = runner.migration_files()
    copied = tmp_path / "migrations"
    copied.mkdir()
    for path in runner.MIGRATIONS.glob("*.sql"):
        content = path.read_text(encoding="utf-8-sig")
        (copied / path.name).write_bytes(b"\xef\xbb\xbf" + content.replace("\n", "\r\n").encode())
    assert runner.migration_files(copied) == files
    assert runner.run_migrations(admin)["pending"] == list(files)
    assert admin.execute("SELECT to_regnamespace('horus_sync'), to_regnamespace('horus')").fetchone() == (None, None)
    assert runner.run_migrations(admin, apply=True, directory=copied)["applied"] == list(files)
    assert runner.run_migrations(admin, apply=True)["applied"] == []
    rows = admin.execute("SELECT version, checksum, applied_at IS NOT NULL, app_min, app_max "
                         "FROM horus.schema_migrations ORDER BY version").fetchall()
    assert rows == [(v, sha256(content.encode()).hexdigest(), True, None, None)
                    for v, (content, _) in files.items()]
    admin.execute("UPDATE horus.schema_migrations SET checksum = repeat('f', 64)")
    with pytest.raises(runner.MigrationRefused, match="checksum_mismatch"):
        runner.run_migrations(admin, apply=True)


def test_atomic_failure_and_concurrent_retry(kit, fresh_database, pg_cluster, tmp_path):
    runner, _ = kit
    admin, name = fresh_database
    for path in runner.MIGRATIONS.glob("*.sql"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / "20261117160400_fail.sql").write_text("SELECT 1 / 0", encoding="utf-8")
    with pytest.raises(psycopg.errors.DivisionByZero):
        runner.run_migrations(admin, apply=True, directory=tmp_path)
    assert admin.execute("SELECT to_regnamespace('horus_sync'), to_regnamespace('horus')").fetchone() == (None, None)

    def apply():
        with pg_cluster.connect(name, autocommit=True) as conn:
            return runner.run_migrations(conn, apply=True)["applied"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(map(len, executor.map(lambda _: apply(), range(2)))) == [0, 5]
    with pg_cluster.connect(name, "horus_app", autocommit=True) as forbidden:
        with pytest.raises(runner.MigrationRefused, match="administrative_session_required"):
            runner.run_migrations(forbidden, apply=True)


def test_preexisting_schema_without_ledger_is_refused(kit, fresh_database):
    runner, _ = kit
    admin, _ = fresh_database
    admin.execute("CREATE SCHEMA horus_sync")
    with pytest.raises(runner.MigrationRefused, match="untracked_schema"):
        runner.run_migrations(admin, apply=True)
    assert admin.execute("SELECT to_regnamespace('horus')").fetchone() == (None,)


def test_managed_admin_does_not_need_superuser(kit, fresh_database):
    runner, _ = kit
    admin, _ = fresh_database
    admin.execute("CREATE ROLE horus_dev_maintainer CREATEROLE BYPASSRLS")
    admin.execute("GRANT horus_owner TO horus_dev_maintainer")
    # Outros testes do mesmo cluster podem ter criado esse papel global.
    admin.execute("""DO $$ BEGIN
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'horus_sync_service') THEN
            GRANT horus_sync_service TO horus_dev_maintainer WITH ADMIN OPTION;
        END IF;
        END $$""")
    admin.execute("ALTER SCHEMA auth OWNER TO horus_dev_maintainer")
    admin.execute("ALTER TABLE auth.users OWNER TO horus_dev_maintainer")
    admin.execute(sql.SQL("ALTER DATABASE {} OWNER TO horus_dev_maintainer").format(
        sql.Identifier(admin.info.dbname)))
    admin.execute("SET SESSION AUTHORIZATION horus_dev_maintainer")
    try:
        assert len(runner.run_migrations(admin, apply=True)["applied"]) == 5
    finally:
        admin.execute("RESET SESSION AUTHORIZATION")


@pytest.fixture
def configured(kit, fresh_database, pg_cluster, monkeypatch, tmp_path):
    runner, validator = kit
    admin, name = fresh_database
    runner.run_migrations(admin, apply=True)
    config = ConnectionRequest(host="synthetic.pooler.supabase.com", port=5432, database="postgres",
                               user="horus_app." + "a" * 20, password="synthetic-only")

    class VaultBackend:
        def get_password(self, service, account):
            assert service == SERVICE
            connection = config.model_dump()
            connection["password"] = config.password.get_secret_value()
            return json.dumps({"version": 1, "connection": connection})

    for key in transport.os.environ:
        if key.upper().startswith("PG"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(transport, "resolve_public", lambda host, port: "192.0.2.10")
    original = psycopg.Connection.connect.__func__
    attempts = []

    def connect(cls, **kwargs):
        assert kwargs["host"] == config.host and kwargs["port"] == 5432
        assert kwargs["user"] == config.user and kwargs["hostaddr"] == "192.0.2.10"
        assert kwargs["sslmode"] == "verify-full" and kwargs["sslrootcert"] == str(transport.CA_FILE)
        attempts.append(True)
        return original(cls, pg_cluster.dsn(name, "horus_app"), autocommit=True)

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(connect))
    monkeypatch.setattr(validator, "tls_in_use", lambda conn: True)
    return validator, ConnectionVault(tmp_path, backend=VaultBackend()), admin, attempts


def test_validated_endpoint_rollback_pool_and_lease(configured):
    validator, vault, admin, attempts = configured
    code, report = validator.validate(vault)
    assert (code, report["status"], report["failed_check"]) == (0, "passed", None)
    assert report["checks"] == ["vault", "endpoint", "tls", "connection", "role", "ledger",
                                "rls", "rollback", "pool", "lease"]
    assert len(attempts) == 2
    assert admin.execute("SELECT count(*) FROM horus.worker_instances").fetchone() == (0,)
    assert admin.execute("SELECT pg_try_advisory_xact_lock(%s, %s)",
                         validator.WORKER_FENCING_LOCK).fetchone() == (True,)


@pytest.mark.parametrize(("statement", "stage"), [
    ("GRANT CREATE ON SCHEMA horus TO horus_app", "role"),
    ("UPDATE horus.schema_migrations SET checksum = repeat('a', 64)", "ledger"),
    ("ALTER TABLE horus.jobs NO FORCE ROW LEVEL SECURITY", "rls"),
    ("SELECT pg_advisory_lock(164, 100)", "lease"),
])
def test_catalog_and_lease_fail_closed(configured, statement, stage):
    validator, vault, admin, _ = configured
    admin.execute(statement)
    code, report = validator.validate(vault)
    assert (code, report["failed_check"]) == (validator.EXIT[stage], stage)
    assert "synthetic" not in json.dumps(report)


@pytest.mark.parametrize(("error", "stage"), [
    (ConnectionError("synthetic private details"), "connection"),
    (psycopg.OperationalError("certificate rejected for synthetic private host"), "tls"),
    (psycopg.OperationalError("password authentication failed for synthetic private user"), "authentication"),
])
def test_transport_failures_are_sanitized(configured, monkeypatch, error, stage):
    validator, vault, _, _ = configured

    def fail(cls, **kwargs):
        raise error

    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(fail))
    code, report = validator.validate(vault)
    assert (code, report["failed_check"]) == (validator.EXIT[stage], stage)
    assert "synthetic" not in json.dumps(report)


def test_endpoint_rejected_before_dns(configured):
    validator, vault, _, attempts = configured
    invalid = vault.read().model_copy(update={"host": "127.0.0.1"})
    code, report = validator.validate(type("Vault", (), {"read": lambda self: invalid})())
    assert (code, report["failed_check"], attempts) == (11, "endpoint", [])


def test_dns_failure_never_opens_connection(configured, monkeypatch):
    validator, vault, _, attempts = configured

    def fail(host, port):
        raise transport.ConnectFailure("database_dns_failed")

    monkeypatch.setattr(transport, "resolve_public", fail)
    code, report = validator.validate(vault)
    assert (code, report["failed_check"], attempts) == (12, "dns", [])


def test_empty_vault_never_opens_connection(configured, tmp_path):
    validator, _, _, attempts = configured

    class EmptyVault:
        def get_password(self, service, account):
            return None

    vault = ConnectionVault(tmp_path, backend=EmptyVault())
    code, report = validator.validate(vault)
    assert (code, report["failed_check"], attempts) == (10, "not_configured", [])
