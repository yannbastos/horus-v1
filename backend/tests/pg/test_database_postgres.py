"""Pool e catálogos reais, exclusivamente no cluster sintético loopback."""
import hashlib
import ssl
from pathlib import Path
import threading
import time

import psycopg
import pytest

from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.database.manager import DatabaseManager, DatabaseRefused, MIGRATION_LOCK
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.transport import CA_FILE, ConnectFailure, connection_type, resolve_public
from horus_backend.database.vault import ConnectionVault
from test_database_contract import CONFIG, MemoryKeyring, PASSWORD


@pytest.fixture
def local_database(pg_cluster, pg_pool, tmp_path, monkeypatch):
    # O clone forte permite catálogo próprio e conexões adicionais.
    with pg_pool.connection() as conn:
        database = conn.info.dbname
    with pg_cluster.connect(database, autocommit=True) as admin:
        admin.execute("""SET ROLE horus_owner;
            CREATE TABLE horus.schema_migrations(version text PRIMARY KEY, checksum text,
              applied_at timestamptz DEFAULT now(), app_min text, app_max text);
            CREATE TABLE horus.database_identity(singleton boolean PRIMARY KEY CHECK(singleton),
              instance_id uuid UNIQUE, created_at timestamptz DEFAULT now());
            GRANT SELECT ON horus.schema_migrations,horus.database_identity TO horus_app;
            RESET ROLE;""")
        admin.execute("INSERT INTO horus.schema_migrations(version,checksum) VALUES (%s,%s)",
                      (EXPECTED_SCHEMA_VERSION, "a"*64))
        admin.execute("INSERT INTO horus.database_identity VALUES (true,gen_random_uuid(),now())")
    vault = ConnectionVault(tmp_path, MemoryKeyring())
    vault.write(ConnectionRequest.model_validate(CONFIG))
    manager = DatabaseManager(vault)
    generations = []
    def local_type(config):
        class LocalConnection(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo="", **kwargs):
                # Única exceção de transporte; validada pelo harness, sem env/HTTP.
                return super().connect(pg_cluster.dsn(database, "horus_app"), autocommit=True,
                                       password=config.password.get_secret_value(), prepare_threshold=None)
        generations.append(LocalConnection)
        return LocalConnection
    monkeypatch.setattr(manager, "_make_connection_type", local_type)
    monkeypatch.setattr("horus_backend.database.manager.resolve_public", lambda *_: "8.8.8.8")
    try:
        yield manager, database, generations
    finally:
        manager.close()


@pytest.mark.pg_strong
def test_real_pool_schema_role_rotation_and_uow(local_database, pg_cluster):
    manager, database, generations = local_database
    assert manager.test().state == "connected"
    first_pool = manager._pool
    first_coordination = manager._coordination
    with manager.connection() as conn:
        assert conn.execute("SELECT current_user").fetchone()[0] == "horus_app"
        with conn.transaction():
            assert conn.execute("SELECT count(*) FROM horus.schema_migrations").fetchone()[0] == 1
    status = manager.configure(ConnectionRequest.model_validate({**CONFIG, "password": PASSWORD+"rotacionada"}))
    assert status.state == "connected"
    assert first_pool.closed and first_coordination.closed
    assert first_pool is not manager._pool and generations[0] is not generations[1]
    assert manager.vault.read().password.get_secret_value() == PASSWORD+"rotacionada"
    assert manager.snapshot.latency_ms >= 0
    assert manager.snapshot.checked_at.endswith("Z")
    assert manager.delete().state == "not_configured"
    with pytest.raises(DatabaseRefused):
        with manager.connection():
            pytest.fail("Admissão aberta sem banco")


@pytest.mark.pg_strong
@pytest.mark.parametrize("mutation,code,version", [
    ("UPDATE horus.schema_migrations SET version='old'", "database_schema_mismatch", "old"),
    ("DROP TABLE horus.schema_migrations", "database_schema_mismatch", None),
    ("DELETE FROM horus.database_identity", "database_schema_mismatch", EXPECTED_SCHEMA_VERSION),
    ("REVOKE SELECT ON horus.schema_migrations FROM horus_app", "database_role_invalid", None),
    ("GRANT CREATE ON SCHEMA horus TO horus_app", "database_role_invalid", None),
])
def test_real_schema_and_grants_rejected(local_database, pg_cluster, mutation, code, version):
    manager, database, _ = local_database
    with pg_cluster.connect(database, autocommit=True) as admin:
        admin.execute(mutation)
    status = manager.test()
    assert status.state == "schema_mismatch"
    assert status.last_error_code == code and status.schema_version == version
    assert manager._pool is None and manager._coordination is None


@pytest.mark.pg_strong
def test_real_migrating_lock(local_database, pg_cluster):
    manager, database, _ = local_database
    with pg_cluster.connect(database, autocommit=True) as migrator:
        migrator.execute("SELECT pg_advisory_lock(%s,%s)", MIGRATION_LOCK)
        assert manager.test().state == "migrating"
        with pytest.raises(DatabaseRefused, match="database_busy"):
            manager.delete()
        migrator.execute("SELECT pg_advisory_unlock(%s,%s)", MIGRATION_LOCK)
    assert manager.test().state == "connected"


@pytest.mark.pg_strong
def test_real_uow_drain_and_no_new_checkout(local_database):
    manager, _, _ = local_database
    assert manager.test().state == "connected"
    entered, release = threading.Event(), threading.Event()
    def use():
        with manager.connection() as conn:
            entered.set()
            assert release.wait(3)
            assert conn.execute("SELECT 1").fetchone()[0] == 1
    thread = threading.Thread(target=use)
    thread.start()
    assert entered.wait(3)
    result = []
    deletion = threading.Thread(target=lambda: result.append(manager.delete()))
    deletion.start()
    try:
        deadline = time.monotonic()+2
        while not manager.changing and time.monotonic() < deadline:
            time.sleep(0.01)
        assert manager.changing
        with pytest.raises(DatabaseRefused):
            with manager.connection():
                pytest.fail("Checkout durante drenagem")
    finally:
        release.set()
        thread.join(3)
        deletion.join(3)
    assert result[0].state == "not_configured"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "0.0.0.0",
                                        "::1", "::ffff:127.0.0.1", "224.0.0.1", "192.0.2.1"])
def test_dns_rejects_every_private_address(monkeypatch, address):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 5432)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 5432))])
    with pytest.raises(ConnectFailure, match="database_endpoint_not_allowed"):
        resolve_public(CONFIG["host"], 5432)


def test_destination_rejected_before_vault_write(tmp_path, monkeypatch):
    backend = MemoryKeyring()
    manager = DatabaseManager(ConnectionVault(tmp_path, backend))
    def denied(*_):
        raise ConnectFailure("database_endpoint_not_allowed")
    monkeypatch.setattr("horus_backend.database.manager.resolve_public", denied)
    with pytest.raises(DatabaseRefused, match="database_endpoint_not_allowed") as caught:
        manager.configure(ConnectionRequest.model_validate(CONFIG))
    assert caught.value.http_status == 422
    assert not backend.values and manager.snapshot.state == "not_configured"


def test_new_connections_resolve_again_and_pin_hostaddr(monkeypatch):
    import socket
    calls = []
    def dns(*args, **kwargs):
        address = "8.8.8.8" if not calls else "1.1.1.1"
        calls.append(args)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 5432))]
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    received = []
    def fake_connect(cls, **kwargs):
        received.append(kwargs)
        return object()
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(fake_connect))
    kind = connection_type(ConnectionRequest.model_validate(CONFIG))
    kind.connect()
    kind.connect()
    assert len(calls) == 2
    assert [kw["hostaddr"] for kw in received] == ["8.8.8.8", "1.1.1.1"]
    assert all(kw["host"] == CONFIG["host"] and kw["sslmode"] == "verify-full" and
               kw["sslrootcert"] == str(CA_FILE) and kw["password"] == PASSWORD for kw in received)


def test_pool_connect_failure_is_sanitized(monkeypatch):
    monkeypatch.setattr("horus_backend.database.transport.resolve_public", lambda *_: "8.8.8.8")
    def fail(cls, **kwargs):
        raise psycopg.OperationalError("password authentication failed "+PASSWORD)
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(fail))
    kind = connection_type(ConnectionRequest.model_validate(CONFIG))
    with pytest.raises(ConnectFailure) as caught:
        kind.connect()
    assert str(caught.value) == "database_auth_failed"
    assert PASSWORD not in str(caught.value)


def test_ca_fingerprint_and_expiration():
    der = ssl.PEM_cert_to_DER_cert(CA_FILE.read_text())
    assert hashlib.sha256(der).hexdigest() == "807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(str(CA_FILE))
    assert context.cert_store_stats()["x509_ca"] == 1


def test_actual_self_signed_tls_refused(tmp_path, monkeypatch):
    import os
    import shutil
    import socket
    import subprocess
    openssl = shutil.which("openssl")
    if openssl is None and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/openssl.exe"
        if candidate.is_file():
            openssl = str(candidate)
    if openssl is None:
        pytest.fail("OpenSSL necessário para gerar certificado TLS sintético local")
    cert, key = tmp_path / "cert.crt", tmp_path / "synthetic.key"
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(6)
        port = listener.getsockname()[1]
        errors = []
        def serve():
            try:
                client, _ = listener.accept()
                with client:
                    client.settimeout(5)
                    assert client.recv(8) == b"\x00\x00\x00\x08\x04\xd2\x16/"
                    client.sendall(b"S")
                    with context.wrap_socket(client, server_side=True):
                        errors.append("TLS aceitou CA inválida")
            except ssl.SSLError:
                pass  # alerta TLS esperado do cliente que rejeitou o certificado
            except Exception as error:
                errors.append(type(error).__name__)
        server = threading.Thread(target=serve)
        server.start()
        monkeypatch.setattr("horus_backend.database.transport.resolve_public", lambda *_: "127.0.0.1")
        # Bypass interno SOMENTE para porta efêmera do teste; HTTP continua fixo 5432.
        config = ConnectionRequest.model_validate(CONFIG).model_copy(update={"port": port})
        try:
            with pytest.raises(ConnectFailure, match="database_tls_invalid"):
                connection_type(config).connect()
        finally:
            server.join(6)
        assert not server.is_alive() and not errors


@pytest.mark.pg_strong
def test_real_scram_auth_failure_and_repair(local_database, pg_cluster):
    from psycopg import sql
    manager, database, _ = local_database
    hba = pg_cluster.data / "pg_hba.conf"
    original = hba.read_text()
    with pg_cluster.connect(autocommit=True) as admin:
        admin.execute(sql.SQL("ALTER ROLE horus_app PASSWORD {}").format(sql.Literal(PASSWORD)))
        hba.write_text("host all horus_app 127.0.0.1/32 scram-sha-256\n"+original, encoding="ascii")
        admin.execute("SELECT pg_reload_conf()")
    try:
        # Confirma que o reload passou a exigir SCRAM antes de testar o erro.
        deadline = time.monotonic()+3
        enforced = False
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(pg_cluster.dsn(database, "horus_app"), password="synthetic-wrong"):
                    pass
            except psycopg.OperationalError:
                enforced = True
                break
            time.sleep(0.02)
        assert enforced
        manager.vault.write(ConnectionRequest.model_validate({**CONFIG, "password": "synthetic-wrong"}))
        assert manager.test().state == "auth_failed"
        assert manager.snapshot.last_error_code == "database_auth_failed"
        assert manager._pool is None and manager._coordination is None
        assert manager.configure(ConnectionRequest.model_validate(CONFIG)).state == "connected"
    finally:
        manager.close()
        hba.write_text(original, encoding="ascii")
        with pg_cluster.connect(autocommit=True) as admin:
            admin.execute("ALTER ROLE horus_app PASSWORD NULL")
            admin.execute("SELECT pg_reload_conf()")


@pytest.mark.pg_strong
def test_privileged_role_rejected(local_database, pg_cluster):
    manager, _, _ = local_database
    with pg_cluster.connect(autocommit=True) as admin:
        admin.execute("ALTER ROLE horus_app BYPASSRLS")
    try:
        assert manager.test().state == "schema_mismatch"
        assert manager.snapshot.last_error_code == "database_role_invalid"
    finally:
        with pg_cluster.connect(autocommit=True) as admin:
            admin.execute("ALTER ROLE horus_app NOBYPASSRLS")


def test_environment_cannot_override_connection(monkeypatch):
    monkeypatch.setenv("PGSERVICE", "unexpected-service")
    with pytest.raises(ConnectFailure, match="database_endpoint_not_allowed"):
        connection_type(ConnectionRequest.model_validate(CONFIG)).connect()


@pytest.mark.pg_strong
def test_auth_failure_invalidates_entire_generation(local_database):
    manager, _, generations = local_database
    assert manager.test().state == "connected"
    pool, coordination, generation = manager._pool, manager._coordination, manager._generation
    # Callback real instalado na classe de conexão da geração; simula o
    # primeiro 28P01 de uma reconexão do worker do pool.
    generations[-1].on_failure("database_auth_failed")
    assert manager.snapshot.state == "auth_failed" and manager._generation != generation
    deadline = time.monotonic()+3
    while not pool.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pool.closed and coordination.closed
    with pytest.raises(DatabaseRefused, match="database_auth_failed"):
        with manager.connection():
            pytest.fail("Admissão aberta após erro de autenticação")


@pytest.mark.pg_strong
def test_vault_failure_discards_pool(local_database):
    manager, _, _ = local_database
    assert manager.test().state == "connected"
    pool, coordination = manager._pool, manager._coordination
    manager.vault._backend.fail = True
    assert manager.test().state == "vault_unavailable"
    assert pool.closed and coordination.closed
    assert manager._pool is None and manager._coordination is None


def test_expected_schema_contract_with_i2():
    migrations = Path(__file__).resolve().parents[3] / "supabase/migrations"
    operational = sorted(migrations.glob("*_horus_*.sql"))
    if not operational:
        pytest.skip("I2 ainda não integrado; contrato fixado pelo coordenador em 20261117130000")
    assert EXPECTED_SCHEMA_VERSION == operational[-1].name.split("_", 1)[0]
