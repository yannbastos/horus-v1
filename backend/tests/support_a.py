"""Helpers compartilhados para os testes do Grupo A portados para PostgreSQL."""
from contextlib import contextmanager
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

import psycopg
from psycopg.rows import dict_row
import pytest
from fastapi.testclient import TestClient

from pg_app_support import PG_TEST_CONFIG, _patch_pg_transport
from horus_backend.api import create_app
from horus_backend.config import Settings
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from keyring_support import MemoryKeyring

TOKEN = "workflow-tests-local-session-token-123456789"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
REQUEST = {
    "objective": "Planejar um vídeo original sobre cidades",
    "references": ["referência fornecida pelo operador"],
}


def configure_pg_vault(data_dir) -> None:
    """Garante que o cofre em memória possua a configuração PG de teste para `data_dir`."""
    vault = ConnectionVault(data_dir)
    if vault.read() is None:
        vault.write(ConnectionRequest.model_validate(PG_TEST_CONFIG))


@pytest.fixture
def settings(pg_app_cluster, pg_app_database, tmp_path, monkeypatch):
    factory = tmp_path / "fake_factory.py"
    factory.write_text('import json; print(json.dumps({"id":"canal01"}))', encoding="utf-8")
    data_dir = tmp_path / "data"
    _patch_pg_transport(pg_app_cluster, pg_app_database, MemoryKeyring(), monkeypatch)
    configure_pg_vault(data_dir)
    return Settings(
        token=TOKEN,
        data_dir=data_dir,
        factory_root=tmp_path,
        factory_command=(sys.executable, str(factory)),
        hermes_executable=None,
        hermes_config=tmp_path / "unused-private-config.json",
    )


@contextmanager
def client_for(s: Settings):
    configure_pg_vault(s.data_dir)
    with TestClient(
        create_app(s),
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {s.token}"},
    ) as client:
        try:
            yield client
        finally:
            manager = getattr(client.app.state, "manager", None)
            if manager is not None and getattr(manager, "worker_lock", None) is not None:
                manager.worker_lock.close()


def pg_admin_connect(pg_app_cluster, pg_app_database, *, role: str = "postgres", as_dict: bool = False) -> psycopg.Connection:
    """Abre conexão direta ao banco descartável (como `postgres` ou `horus_app`) para asserções/adulterações."""
    kwargs = {"row_factory": dict_row} if as_dict else {}
    if role == "horus_app":
        return psycopg.connect(
            pg_app_cluster.dsn(pg_app_database, "horus_app"),
            autocommit=True,
            password=PG_TEST_CONFIG["password"],
            prepare_threshold=None,
            **kwargs,
        )
    conn = pg_app_cluster.connect(pg_app_database, autocommit=True)
    if as_dict:
        conn.row_factory = dict_row
    return conn


@pytest.fixture
def hermes_settings(pg_app_cluster, pg_app_database, tmp_path, hermes, monkeypatch):
    from horus_backend.security import harden_profile

    connection = tmp_path / "hermes.json"
    connection.write_text(json.dumps({"base_url": hermes.base_url, "token": hermes.token}), encoding="utf-8")
    profile = tmp_path / "profile"
    harden_profile(profile)
    data_dir = tmp_path / "data"
    _patch_pg_transport(pg_app_cluster, pg_app_database, MemoryKeyring(), monkeypatch)
    configure_pg_vault(data_dir)
    return Settings(
        hermes_profile=profile,
        max_tokens_per_run=100000,
        max_tokens_per_day=10000000,
        max_runs_per_day=100,
        token="workflow-hermes-tests-session-token-123456789",
        data_dir=data_dir,
        factory_root=None,
        hermes_executable=None,
        hermes_config=connection,
        workflow_executor="hermes",
        hermes_poll_seconds=0.05,
        hermes_run_timeout=5,
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
    )


def create(client, key="request-1", payload=None):
    response = client.post("/v1/workflows", json=payload or REQUEST, headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    return response.json()


def wait(client, workflow_id, statuses=("awaiting_approval",)):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/v1/workflows/{workflow_id}")
        assert response.status_code == 200
        workflow = response.json()
        if workflow["status"] in statuses:
            return workflow
        time.sleep(0.02)
    pytest.fail(f"Workflow não chegou a {statuses}: {workflow['status']}")


def review(client, workflow, decision="approve", key="review-1", **extra):
    return client.post(
        f"/v1/workflows/{workflow['id']}/review",
        headers={"Idempotency-Key": key},
        json={
            "expected_revision": workflow["revision"],
            "artifact_id": workflow["current_artifact_id"],
            "decision": decision,
            **extra,
        },
    )
