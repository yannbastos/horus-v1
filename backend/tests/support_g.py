"""Helpers compartilhados pelos testes PostgreSQL do Grupo G (transcrição, download, narração, Whisper) — HOR-164."""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from horus_backend.api import create_app
from horus_backend.config import Settings
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from keyring_support import MemoryKeyring
from pg_app_support import PG_TEST_CONFIG, _patch_pg_transport
from support_d import make_pg_store


@pytest.fixture
def pg_transport(pg_app_cluster, pg_app_database, monkeypatch):
    """Aponta o transporte do `DatabaseManager` para o banco PostgreSQL descartável do teste."""
    _patch_pg_transport(pg_app_cluster, pg_app_database, MemoryKeyring(), monkeypatch)


@pytest.fixture
def client_for(pg_transport):
    """Fábrica de `TestClient` conectado ao PostgreSQL descartável (cofre de conexão sintético por `data_dir`)."""

    @contextmanager
    def _make(settings: Settings):
        ConnectionVault(settings.data_dir).write(ConnectionRequest.model_validate(PG_TEST_CONFIG))
        app = create_app(settings)
        headers = {"Authorization": f"Bearer {settings.token}"}
        with TestClient(app, base_url="http://127.0.0.1", headers=headers) as client:
            try:
                yield client
            finally:
                # Contorno de teste: o pool fecha antes de `manager.close()` no shutdown do TestClient (defeito conhecido).
                lock = getattr(app.state.manager, "worker_lock", None)
                if lock is not None:
                    lock.close()

    return _make


@pytest.fixture
def pg_store(pg_app_cluster, pg_app_database, tmp_path, monkeypatch):
    """`Store` conectado ao banco PostgreSQL descartável (sem aplicação)."""
    manager, store = make_pg_store(pg_app_cluster, pg_app_database, tmp_path, monkeypatch)
    try:
        yield store
    finally:
        manager.close()
