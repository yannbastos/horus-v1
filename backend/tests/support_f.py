"""Helpers do Grupo F (Radar, retenção e contratos de persistência) portados para PostgreSQL (HOR-164)."""
import pytest

from pg_app_support import PG_TEST_CONFIG, _patch_pg_transport
from keyring_support import MemoryKeyring


@pytest.fixture
def pg_manager(pg_app_cluster, pg_app_database, tmp_path, monkeypatch):
    """`DatabaseManager` conectado ao banco descartável com schema `horus` completo (um banco por teste)."""
    from horus_backend.database.manager import DatabaseManager
    from horus_backend.database.models import ConnectionRequest
    from horus_backend.database.vault import ConnectionVault

    _patch_pg_transport(pg_app_cluster, pg_app_database, MemoryKeyring(), monkeypatch)
    vault = ConnectionVault(tmp_path / "vault")
    vault.write(ConnectionRequest.model_validate(PG_TEST_CONFIG))
    manager = DatabaseManager(vault)
    assert manager.test().state == "connected"
    try:
        yield manager
    finally:
        manager.close()


@pytest.fixture
def pg_store(pg_manager, tmp_path):
    from horus_backend.storage import Store

    return Store(pg_manager, data_dir=tmp_path / "data")


# --- Retenção/roteiros via API completa (create_app em PostgreSQL) -------------------------------------------
import ipaddress
import json
import socket

from fastapi.testclient import TestClient

RETENTION_TOKEN = 'retention-test-token-local-1234567'
RETENTION_HEADERS = {'Authorization': f'Bearer {RETENTION_TOKEN}'}


def retention_settings(tmp_path):
    from horus_backend.config import Settings

    return Settings(token=RETENTION_TOKEN, data_dir=tmp_path / 'data', factory_root=None, hermes_executable=None,
                    hermes_config=tmp_path / 'never-read.json', hermes_poll_seconds=.01)


def loopback_only(monkeypatch):
    """Bloqueia qualquer conexão de socket que não seja loopback (o PostgreSQL descartável é loopback)."""
    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        try:
            allowed = ipaddress.ip_address(host).is_loopback
        except ValueError:
            allowed = host == 'localhost'
        if not allowed:
            raise AssertionError('Socket proibido')
        return real_connect(self, address, *args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', guarded)


@pytest.fixture
def retention_env(pg_app_cluster, pg_app_database, tmp_path, monkeypatch):
    """Settings + transporte PG + cofre configurado; Hermes/rede real proibidos."""
    from support_a import configure_pg_vault

    def forbidden(*args, **kwargs):
        raise AssertionError('Rede/conexão proibida')
    monkeypatch.setattr('horus_backend.workflow_hermes.read_connection', forbidden)
    _patch_pg_transport(pg_app_cluster, pg_app_database, MemoryKeyring(), monkeypatch)
    settings = retention_settings(tmp_path)
    configure_pg_vault(settings.data_dir)
    return settings


@pytest.fixture
def app(retention_env):
    from horus_backend.api import create_app

    return create_app(retention_env)


@pytest.fixture
def client(app, monkeypatch):
    with TestClient(app, base_url='http://127.0.0.1', headers=RETENTION_HEADERS) as client:
        loopback_only(monkeypatch)
        try:
            yield client
        finally:
            manager = getattr(client.app.state, 'manager', None)
            if manager is not None and getattr(manager, 'worker_lock', None) is not None:
                manager.worker_lock.close()


def rows(store, sql, params=()):
    with store.uow(readonly=True) as conn:
        return conn.execute(sql, params).fetchall()


def as_text(value):
    """Texto de uma coluna `jsonb` (dict/list) ou `text` para varredura de literais."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
