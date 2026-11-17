"""Helpers e fixtures compartilhados para os testes do Grupo C em PostgreSQL (HOR-164)."""
import sys
import time

import pytest
from fastapi.testclient import TestClient

from conftest import PG_TEST_CONFIG, _patch_pg_transport
from horus_backend.api import create_app
from horus_backend.config import Settings
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from keyring_support import MemoryKeyring

TOKEN = "workflow-tests-local-session-token-123456789"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
REQUEST = {"objective": "Planejar um vídeo original sobre cidades", "references": ["referência fornecida pelo operador"]}

_PG_BINDINGS: dict[str, tuple] = {}


@pytest.fixture
def settings(pg_app_cluster, pg_app_database, tmp_path, monkeypatch):
    factory = tmp_path / "fake_factory.py"
    factory.write_text('import json; print(json.dumps({"id":"canal01"}))', encoding="utf-8")
    s = Settings(
        token=TOKEN,
        data_dir=tmp_path / "data",
        factory_root=tmp_path,
        factory_command=(sys.executable, str(factory)),
        hermes_executable=None,
        hermes_config=tmp_path / "unused-private-config.json",
    )
    memory_keyring = MemoryKeyring()
    _patch_pg_transport(pg_app_cluster, pg_app_database, memory_keyring, monkeypatch)
    ConnectionVault(s.data_dir).write(ConnectionRequest.model_validate(PG_TEST_CONFIG))
    _PG_BINDINGS[str(s.data_dir)] = (pg_app_cluster, pg_app_database)
    try:
        yield s
    finally:
        _PG_BINDINGS.pop(str(s.data_dir), None)


def pg_connect(settings: Settings, *, user: str = "postgres", autocommit: bool = True):
    cluster, dbname = _PG_BINDINGS[str(settings.data_dir)]
    return cluster.connect(dbname, user=user, autocommit=autocommit)


def schema_state(conn):
    """Retorna assinatura imutável do schema `horus` (migrations aplicadas e tabelas)."""
    versions = conn.execute("SELECT version FROM horus.schema_migrations ORDER BY version").fetchall()
    tables = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'horus' ORDER BY tablename"
    ).fetchall()
    return ([r[0] for r in versions], [r[0] for r in tables])


def client_for(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), base_url="http://127.0.0.1", headers=HEADERS)


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


def approved(client):
    workflow = wait(client, create(client)["id"])
    assert review(client, workflow).status_code == 200
    return workflow


def generate(client, workflow, key="generate", **extra):
    return client.post(
        f"/v1/workflows/{workflow['id']}/scripts",
        headers={"Idempotency-Key": key},
        json={
            "expected_revision": workflow["revision"],
            "proposal_artifact_id": workflow["current_artifact_id"],
            **extra,
        },
    )


def decide(client, script, decision="approve", key="script-review", **extra):
    return client.post(
        f"/v1/scripts/{script['id']}/review",
        headers={"Idempotency-Key": key},
        json={"expected_version": script["version"], "decision": decision, **extra},
    )


def script_for(client):
    script = generate(client, approved(client)).json()
    assert decide(client, script).status_code == 200
    return script


def start(client, script, key="production", mode="simulated", **extra):
    return client.post(
        f"/v1/scripts/{script['id']}/productions",
        headers={"Idempotency-Key": key},
        json={"expected_version": script["version"], "mode": mode, **extra},
    )


def finished(client, production):
    for _ in range(200):
        item = client.get("/v1/productions/" + production["id"]).json()
        if item["status"] not in {"queued", "running", "cancel_requested"}:
            return item
        time.sleep(0.025)
    pytest.fail(str(item))


def license_body(**over):
    value = {
        "asset_id": "scene-1",
        "asset_sha256": "a" * 64,
        "decision": "verified",
        "license": {
            "id": "own",
            "name": "Obra própria",
            "url": "https://example.org/termos",
            "commercial_use": "allowed",
            "attribution_required": False,
        },
        "evidence": "Termos conferidos pelo operador.",
        "reviewed_by": "human",
    }
    return value | over
