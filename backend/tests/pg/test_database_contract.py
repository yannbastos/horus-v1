"""Diagnóstico HTTP isolado; cofre em memória e nenhuma credencial real."""
import json
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from horus_backend.database.manager import DatabaseManager, DatabaseRefused
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.router import database_router
from horus_backend.database.transport import ConnectFailure
from horus_backend.database.vault import ConnectionVault

PASSWORD = "sintética:'\\@?#%$\"= 空白 !"
CONFIG = {"host": "aws-0-sa-east-1.pooler.supabase.com", "port": 5432,
          "database": "postgres", "user": "horus_app.abcdefghijklmnopqrst", "password": PASSWORD}


class MemoryKeyring:
    def __init__(self):
        self.values = {}
        self.fail = False

    def get_password(self, service, account):
        if self.fail:
            raise RuntimeError(PASSWORD)
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        if self.fail:
            raise RuntimeError(PASSWORD)
        self.values[service, account] = value

    def delete_password(self, service, account):
        if self.fail:
            raise RuntimeError(PASSWORD)
        del self.values[service, account]


@pytest.fixture
def diagnostic(tmp_path, monkeypatch):
    backend = MemoryKeyring()
    vault = ConnectionVault(tmp_path, backend)
    manager = DatabaseManager(vault)
    monkeypatch.setattr("horus_backend.database.manager.resolve_public", lambda *_: "8.8.8.8")
    class Offline:
        @classmethod
        def connect(cls):
            raise ConnectFailure("database_dns_failed")
    monkeypatch.setattr(manager, "_make_connection_type", lambda _: Offline)
    app = FastAPI()
    app.include_router(database_router(manager))
    with TestClient(app) as client:
        yield client, manager, backend


def test_endpoints_and_write_only(diagnostic, caplog):
    client, manager, backend = diagnostic
    initial = client.get("/v1/database/status")
    assert initial.status_code == 200
    assert initial.json()["state"] == "not_configured"
    configured = client.post("/v1/database/connection", json=CONFIG)
    assert configured.status_code == 200
    assert configured.json()["state"] == "unreachable"
    assert configured.json()["last_error_code"] == "database_dns_failed"
    assert configured.json()["host_label"] == "Supabase (sessão 5432)"
    for response in (configured, client.post("/v1/database/test"), client.post("/v1/database/test", json={})):
        assert PASSWORD not in response.text
        assert CONFIG["user"] not in response.text
        assert CONFIG["host"] not in response.text
        assert response.headers["cache-control"] == "no-store"
    assert manager.vault.read().password.get_secret_value() == PASSWORD
    assert PASSWORD not in caplog.text
    assert client.delete("/v1/database/connection").json()["state"] == "not_configured"
    assert client.delete("/v1/database/connection").json()["state"] == "not_configured"
    assert not backend.values


@pytest.mark.parametrize("state,code", [
    ("not_configured", None), ("connecting", None), ("connected", None),
    ("unreachable", "database_tls_invalid"), ("auth_failed", "database_auth_failed"),
    ("schema_mismatch", "database_schema_mismatch"), ("migrating", None),
    ("vault_unavailable", "database_vault_unavailable")])
def test_eight_states_poll_is_cached(diagnostic, monkeypatch, state, code):
    client, manager, _ = diagnostic
    monkeypatch.setattr(manager, "_make_connection_type", lambda *_: pytest.fail("GET abriu conexão"))
    monkeypatch.setattr(manager.vault, "read", lambda: pytest.fail("GET releu cofre"))
    manager._set(state, code, latency=1 if state == "connected" else None)
    before = manager.snapshot.model_dump()
    for _ in range(10):
        response = client.get("/v1/database/status")
        assert response.status_code == 200
        assert response.json() == before


@pytest.mark.parametrize("patch", [
    {"password": ""}, {"password": "x"*4097}, {"password": "a\x00b"},
    {"port": 6543}, {"port": 5432.0}, {"port": "5432"},
    {"user": "postgres.abcdefghijklmnopqrst"}, {"user": "service_role"},
    {"user": "horus_app."+"a"*130}, {"database": "postgres options=-c"},
    {"host": "127.0.0.1"}, {"host": "db.abcdefghijklmnopqrst.supabase.co"},
    {"host": "aws-0-sa-east-1.pooler.supabase.com.evil.com"},
    {"host": "postgresql://aws-0-sa-east-1.pooler.supabase.com"},
    {"host": "aws-0-sa-east-1.pooler.supabase.com,evil.com"}, {"options": "sslmode=disable"},
    {"host": "AWS-0-SA-EAST-1.pooler.supabase.com"},
    {"host": "são-paulo.pooler.supabase.com"}, {"host": "例子.pooler.supabase.com"},
    {"host": "user@aws-0-sa-east-1.pooler.supabase.com"},
    {"host": "aws-0-sa-east-1.pooler.supabase.com#fragment"},
    {"host": "aws-0-sa-east-1.pooler.supabase.com/path"},
    {"host": "aws-0-sa-east-1.pooler.supabase.com."},
    {"host": "::1"}, {"host": "[2001:4860:4860::8888]"}, {"host": "2130706433"},
])
def test_invalid_connection_never_saved_or_reflected(diagnostic, patch):
    client, manager, backend = diagnostic
    response = client.post("/v1/database/connection", json={**CONFIG, **patch})
    assert response.status_code == 422
    assert set(response.json()["detail"]) == {"code", "message", "status"}
    assert PASSWORD not in response.text
    assert not backend.values
    assert manager.snapshot.state == "not_configured"


def test_body_limit_and_sanitized_errors(diagnostic):
    client, _, _ = diagnostic
    for body in (b"x"*16385, b'{"password":"secret",', json.dumps({**CONFIG, "extra": PASSWORD}).encode()):
        response = client.post("/v1/database/connection", content=body)
        assert response.status_code == 422
        assert "input" not in response.text and "secret" not in response.text and PASSWORD not in response.text
    for method, path in (("post", "test"), ("delete", "connection"), ("get", "status")):
        response = client.request(method, "/v1/database/"+path, json={"password": PASSWORD})
        assert response.status_code == 422
        assert PASSWORD not in response.text


def test_vault_missing_and_fail_closed(diagnostic):
    client, manager, backend = diagnostic
    assert client.post("/v1/database/test").json()["state"] == "not_configured"
    backend.fail = True
    assert client.post("/v1/database/test").json()["state"] == "vault_unavailable"
    status = client.get("/v1/database/status")
    assert status.status_code == 200 and status.json()["host_label"] is None
    for response in (client.post("/v1/database/connection", json=CONFIG), client.delete("/v1/database/connection")):
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "database_vault_unavailable"
        assert response.json()["detail"]["status"]["host_label"] is None
        assert PASSWORD not in response.text
    assert manager._pool is None and manager._coordination is None


def test_initial_vault_failure_and_insecure_backend(tmp_path, monkeypatch):
    backend = MemoryKeyring()
    backend.fail = True
    manager = DatabaseManager(ConnectionVault(tmp_path, backend))
    assert manager.snapshot.state == "vault_unavailable"
    import keyring
    with monkeypatch.context() as patch:
        patch.setattr(keyring, "get_keyring", lambda: MemoryKeyring())
        assert DatabaseManager(ConnectionVault(tmp_path)).snapshot.state == "vault_unavailable"


def test_busy_before_changes_and_drain_timeout(diagnostic, monkeypatch):
    client, manager, backend = diagnostic
    monkeypatch.setattr(manager, "busy", lambda: True)
    before = manager.snapshot
    for response in (client.post("/v1/database/connection", json=CONFIG), client.delete("/v1/database/connection")):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "database_busy"
    assert not backend.values and manager.snapshot == before
    monkeypatch.setattr(manager, "busy", lambda: False)
    manager._active = 1
    with pytest.raises(DatabaseRefused, match="database_busy"):
        manager.delete()
    assert not manager.changing


def test_concurrent_test_is_deduplicated(diagnostic):
    client, manager, _ = diagnostic
    manager._set("connecting", checked=False)
    with manager._operation:
        assert client.post("/v1/database/test").json()["state"] == "connecting"


def test_openapi_is_write_only(diagnostic):
    client, _, _ = diagnostic
    schema = client.app.openapi()
    request = schema["paths"]["/v1/database/connection"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert request["properties"]["password"]["writeOnly"]
    assert request["additionalProperties"] is False
    assert set(request["required"]) == set(CONFIG)
    assert PASSWORD not in json.dumps(schema)


def test_no_sqlite_or_file_fallback(tmp_path, monkeypatch):
    import ast
    from pathlib import Path
    root = Path(__file__).parents[2] / "src/horus_backend/database"
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        assert not any(isinstance(n, ast.Import) and any(a.name in {"sqlite3", "_sqlite3"} for a in n.names)
                       or isinstance(n, ast.ImportFrom) and n.module in {"sqlite3", "_sqlite3"} for n in ast.walk(tree))
    backend = MemoryKeyring()
    manager = DatabaseManager(ConnectionVault(tmp_path, backend))
    assert manager.test().state == "not_configured"
    backend.fail = True
    assert manager.test().state == "vault_unavailable"
    assert not list(tmp_path.rglob("*"))


@pytest.fixture
def api_diagnostic(tmp_path, monkeypatch):
    from horus_backend.api import create_app
    from horus_backend.config import Settings
    backend = MemoryKeyring()
    # A fixture global já proíbe acesso ao keyring real. Esta injeção habilita
    # apenas o cofre em memória para exercitar o wiring de produção.
    def install(app, data_dir, busy):
        manager = DatabaseManager(ConnectionVault(data_dir, backend), busy=busy)
        class Offline:
            @classmethod
            def connect(cls):
                raise ConnectFailure("database_dns_failed")
        monkeypatch.setattr(manager, "_make_connection_type", lambda _: Offline)
        app.state.database = manager
        app.include_router(database_router(manager))
    monkeypatch.setattr("horus_backend.api.install_database", install)
    monkeypatch.setattr("horus_backend.database.manager.resolve_public", lambda *_: "8.8.8.8")
    token = "database-api-synthetic-token-0123456789"
    settings = Settings(token=token, data_dir=tmp_path, factory_root=None, hermes_executable=None,
                        hermes_config=tmp_path/"hermes.json")
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, app.state.database, backend, token


def test_api_auth_body_limits_and_update_busy(api_diagnostic):
    client, manager, backend, token = api_diagnostic
    app = client.app
    assert client.post("/v1/database/connection", content=b"x"*17000).status_code == 401
    client.headers["Authorization"] = "Bearer "+token
    for body in (b"x"*17000, b"x"*(3*1024*1024)):
        result = client.post("/v1/database/connection", content=body)
        assert result.status_code == 422
        assert result.json()["detail"]["code"] == "database_invalid_connection"
        assert result.headers["cache-control"] == "no-store"
    app.state.manager.active_id = "synthetic-active-job"
    try:
        result = client.post("/v1/database/connection", json=CONFIG)
        assert result.status_code == 409
        assert not backend.values
    finally:
        app.state.manager.active_id = None
    assert client.post("/v1/update/reservation").status_code == 200
    result = client.delete("/v1/database/connection")
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "database_busy"
    assert client.post("/v1/database/test").status_code == 200


def test_api_concurrent_diagnostic_deduplicates_and_limits_busy(api_diagnostic, monkeypatch):
    client, manager, _, token = api_diagnostic
    client.headers["Authorization"] = "Bearer "+token
    manager.vault.write(ConnectionRequest.model_validate(CONFIG))
    entered, release = threading.Event(), threading.Event()
    calls, responses, failures = [], [], []
    class Pending:
        @classmethod
        def connect(cls):
            calls.append(1)
            entered.set()
            assert release.wait(5)
            raise ConnectFailure("database_dns_failed")
    monkeypatch.setattr(manager, "_make_connection_type", lambda _: Pending)
    def first():
        try:
            responses.append(client.post("/v1/database/test"))
        except Exception as error:
            failures.append(type(error).__name__)
    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(3)
        assert manager.changing and manager.snapshot.state == "connecting"
        result = client.post("/v1/database/test")
        assert result.status_code == 200 and result.json()["state"] == "connecting"
        assert result.headers["cache-control"] == "no-store" and len(calls) == 1
        for method in ("post", "delete"):
            result = client.request(method, "/v1/database/connection", json=CONFIG if method == "post" else None)
            assert result.status_code == 409 and result.json()["detail"]["code"] == "database_busy"
        # Alteração local sem relação com conexão não recebe a mensagem de update.
        result = client.post("/v1/notifications/read-state", json={"all": True, "read": True})
        # Notificações agora vivem no PG: sem conexão a operação de domínio recusa com 503 estável
        # (nunca fallback), e a recusa não é a mensagem de reserva de atualização.
        assert result.status_code == 503
        assert result.json()["detail"]["code"] == "database_unavailable"
        assert "Atualização reservada" not in result.text
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not failures
    assert len(responses) == 1 and responses[0].status_code == 200 and len(calls) == 1


@pytest.mark.parametrize("size,status", [(16*1024, 200), (16*1024+1, 422)])
def test_api_streamed_body_limit_without_content_length(api_diagnostic, size, status):
    import asyncio
    import httpx
    client, manager, backend, token = api_diagnostic
    initial = manager.snapshot
    chunks = []
    async def content():
        data = b"{}"+b" "*(size-2)
        for offset in range(0, size, 4096):
            chunks.append(1)
            yield data[offset:offset+4096]
    async def app(scope, receive, send):
        headers = dict(scope["headers"])
        assert b"content-length" not in headers
        assert headers[b"transfer-encoding"] == b"chunked"
        await client.app(scope, receive, send)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as sender:
            return await sender.post("/v1/database/test", content=content(),
                                     headers={"Authorization": "Bearer "+token})
    response = asyncio.run(exercise())
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert len(chunks) >= 4 and not backend.values
    if status == 422:
        assert response.json()["detail"]["code"] == "database_invalid_connection"
        assert manager.snapshot == initial


@pytest.mark.parametrize("code,attempts", [("database_unreachable", 3), ("database_auth_failed", 1),
                                         ("database_tls_invalid", 1)])
def test_diagnostic_retry_is_bounded(diagnostic, monkeypatch, code, attempts):
    client, manager, _ = diagnostic
    calls, sleeps = [], []
    class Failed:
        @classmethod
        def connect(cls):
            calls.append(1)
            raise ConnectFailure(code)
    monkeypatch.setattr(manager, "_make_connection_type", lambda _: Failed)
    monkeypatch.setattr("horus_backend.database.manager.time.sleep", sleeps.append)
    response = client.post("/v1/database/connection", json=CONFIG)
    assert response.status_code == 200
    assert response.json()["last_error_code"] == code
    assert len(calls) == attempts and len(sleeps) == attempts-1
    if sleeps:
        assert 1 <= sleeps[0] <= 1.25 and 2 <= sleeps[1] <= 2.25


def test_router_lifespan_closes_pool_only_when_it_owns_the_shutdown(tmp_path):
    # D4: com close_on_shutdown=False (uso por api.py) o pool segue aberto no shutdown do router;
    # quem fecha é o dono do ciclo de vida, depois dos workers/etapas.
    for owns, expected in ((True, 1), (False, 0)):
        manager = DatabaseManager(ConnectionVault(tmp_path, MemoryKeyring()))
        closed = []
        manager.close = lambda closed=closed: closed.append(1)
        app = FastAPI()
        app.include_router(database_router(manager, close_on_shutdown=owns))
        with TestClient(app):
            assert closed == []
        assert len(closed) == expected
