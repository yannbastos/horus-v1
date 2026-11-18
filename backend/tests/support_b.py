"""Helpers compartilhados para o porte dos testes do Grupo B para PostgreSQL (HOR-164)."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import json
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
import pytest

from pg_app_support import PG_TEST_CONFIG, _patch_pg_transport
from horus_backend.api import create_app
from horus_backend.config import Settings
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from horus_backend.security import harden_profile
from horus_backend.storage import Store
from keyring_support import MemoryKeyring

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "hermes"
if str(CONTRACTS) not in sys.path:
    sys.path.insert(0, str(CONTRACTS))
from fake_server import FakeHermes, Scenario  # noqa: E402

WORKFLOW_TOKEN = "workflow-tests-local-session-token-123456789"
HERMES_TOKEN = "workflow-hermes-tests-session-token-123456789"
ENVELOPE = json.loads((CONTRACTS / "fixtures" / "envelope-ficticio.json").read_text(encoding="utf-8"))
VALID_HERMES_RESULT = json.loads((CONTRACTS / "fixtures" / "resultado-valido.json").read_text(encoding="utf-8"))
VALID_SCRIPT_OUTPUT = json.loads((CONTRACTS / "fixtures" / "script-saida-valida.json").read_text(encoding="utf-8"))
WORKFLOW_REQUEST = {
    "objective": "Planejar um vídeo original sobre cidades",
    "references": ["referência fornecida pelo operador"],
}
HERMES_WORKFLOW_REQUEST = {
    "objective": ENVELOPE["objective"],
    "dataset": ENVELOPE["dataset"],
    "channel_profile": ENVELOPE["channel_profile"],
}


def echo(result: dict = VALID_HERMES_RESULT):
    """Resposta válida que ecoa o task_id recebido no envelope."""
    return lambda body: json.dumps(
        {**result, "task_id": json.loads(body["input"])["task_id"]},
        ensure_ascii=False,
    )


@pytest.fixture
def settings(tmp_path):
    factory = tmp_path / "fake_factory.py"
    factory.write_text('import json; print(json.dumps({"id":"canal01"}))', encoding="utf-8")
    return Settings(
        token=WORKFLOW_TOKEN,
        data_dir=tmp_path / "data",
        factory_root=tmp_path,
        factory_command=(sys.executable, str(factory)),
        hermes_executable=None,
        hermes_config=tmp_path / "unused-private-config.json",
    )


@pytest.fixture
def hermes():
    with FakeHermes() as server:
        yield server


@pytest.fixture
def hermes_settings(tmp_path, hermes):
    connection = tmp_path / "hermes.json"
    connection.write_text(
        json.dumps({"base_url": hermes.base_url, "token": hermes.token}),
        encoding="utf-8",
    )
    profile = tmp_path / "profile"
    harden_profile(profile)
    return Settings(
        hermes_profile=profile,
        max_tokens_per_run=100000,
        max_tokens_per_day=10000000,
        max_runs_per_day=100,
        token=HERMES_TOKEN,
        data_dir=tmp_path / "data",
        factory_root=None,
        hermes_executable=None,
        hermes_config=connection,
        workflow_executor="hermes",
        hermes_poll_seconds=0.05,
        hermes_run_timeout=5,
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
    )


@pytest.fixture
def pg_store(pg_app_cluster, pg_app_database):
    """Store conectado ao banco PostgreSQL descartável da sessão de teste."""
    dsn = pg_app_cluster.dsn(pg_app_database, "horus_app")
    with ConnectionPool(dsn, min_size=1, max_size=4, open=True) as pool:
        yield Store(pool)


@pytest.fixture
def pg_admin(pg_app_cluster, pg_app_database):
    """Conexão administrativa (owner/postgres) para inspeção ou adulteração em testes."""
    with pg_app_cluster.connect(pg_app_database, autocommit=True) as conn:
        yield conn


@pytest.fixture
def client_for(pg_app_cluster, pg_app_database, monkeypatch):
    """Fábrica de TestClient conectado ao PostgreSQL descartável (preserva estado entre reinícios)."""
    memory_keyring = MemoryKeyring()
    _patch_pg_transport(pg_app_cluster, pg_app_database, memory_keyring, monkeypatch)

    @contextmanager
    def _make(cfg: Settings):
        ConnectionVault(cfg.data_dir).write(ConnectionRequest.model_validate(PG_TEST_CONFIG))
        app = create_app(cfg)
        headers = {"Authorization": f"Bearer {cfg.token}"}
        with TestClient(app, base_url="http://127.0.0.1", headers=headers) as client:
            try:
                yield client
            finally:
                # Contorno de teste: no corte PG, db_manager._pool é fechado antes de manager.close() no shutdown do TestClient.
                app.state.manager.worker_lock.close()

    return _make


def query_rows(store: Store, sql_text: str, params=()) -> list[dict]:
    with store.uow(readonly=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(sql_text, params) if params else cur.execute(sql_text)
            return [dict(r) for r in rows.fetchall()]


def create_workflow(client, key: str = "request-1", payload=None):
    response = client.post(
        "/v1/workflows",
        json=payload or WORKFLOW_REQUEST,
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    return response.json()


def create_hermes_workflow(client, key: str = "pedido-1", payload=None):
    return create_workflow(client, key=key, payload=payload or HERMES_WORKFLOW_REQUEST)


def wait_workflow(client, workflow_id: str, check=("awaiting_approval",), timeout: float = 8.0):
    predicate = check if callable(check) else (lambda w: w["status"] in check)
    deadline = time.monotonic() + timeout
    workflow = None
    while time.monotonic() < deadline:
        response = client.get(f"/v1/workflows/{workflow_id}")
        assert response.status_code == 200, response.text
        workflow = response.json()
        if predicate(workflow):
            return workflow
        time.sleep(0.025)
    pytest.fail(f"Workflow não atingiu condição esperada: {workflow}")


def pending_workflow(client, workflow_id: str):
    return wait_workflow(client, workflow_id, lambda w: w["pending_authorization"] is not None)


def authorize_workflow(client, workflow: dict, key: str = "autorizacao-1"):
    response = client.post(
        f"/v1/workflows/{workflow['id']}/authorize",
        headers={"Idempotency-Key": key},
        json={
            "expected_revision": workflow["revision"],
            "job_id": workflow["pending_authorization"]["job_id"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def review_workflow(client, workflow: dict, decision: str = "approve", key: str = "review-1", **extra):
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


def approved_hermes_workflow(client, server: FakeHermes):
    server.scenarios.append(Scenario(output=echo()))
    created = create_workflow(client, key="pedido-1", payload=HERMES_WORKFLOW_REQUEST)
    workflow = pending_workflow(client, created["id"])
    authorize_workflow(client, workflow)
    workflow = wait_workflow(client, workflow["id"], lambda w: w["status"] == "awaiting_approval")
    response = review_workflow(client, workflow, decision="approve", key="approve")
    assert response.status_code == 200, response.text
    return workflow


def approved_simulated_workflow(client, key: str = "request-1"):
    created = create_workflow(client, key=key)
    workflow = wait_workflow(client, created["id"])
    response = review_workflow(client, workflow, decision="approve", key=f"approve-{key}")
    assert response.status_code == 200, response.text
    return workflow


def generate_script(client, workflow: dict, key: str = "generate", **extra):
    return client.post(
        f"/v1/workflows/{workflow['id']}/scripts",
        headers={"Idempotency-Key": key},
        json={
            "expected_revision": workflow["revision"],
            "proposal_artifact_id": workflow["current_artifact_id"],
            **extra,
        },
    )


def decide_script(client, script: dict, decision: str = "approve", key: str = "script-review", **extra):
    return client.post(
        f"/v1/scripts/{script['id']}/review",
        headers={"Idempotency-Key": key},
        json={"expected_version": script["version"], "decision": decision, **extra},
    )


def manual_doc(**override):
    value = {
        "title": "Meu primeiro vídeo",
        "sections": [
            {"id": "hook", "kind": "hook", "heading": "Gancho", "narration": "Você sabia que isso muda tudo?"},
            {"id": "body", "kind": "body", "heading": "Meio", "narration": "Texto que eu mesmo escrevi."},
            {"id": "end", "kind": "conclusion", "heading": "Fim", "narration": "Obrigado por assistir."},
        ],
    }
    return {**value, **override}


def manual_script(client, workflow: dict, document=None, key: str = "manual", **extra):
    return client.post(
        f"/v1/workflows/{workflow['id']}/scripts",
        headers={"Idempotency-Key": key},
        json={
            "source": "manual",
            "expected_revision": workflow["revision"],
            "proposal_artifact_id": workflow["current_artifact_id"],
            "document": document or manual_doc(),
            **extra,
        },
    )


def version_script(client, script: dict, document=None, key: str = "v", expected=None, **extra):
    return client.post(
        f"/v1/scripts/{script['id']}/versions",
        headers={"Idempotency-Key": key},
        json={
            "expected_version": expected or script["version"],
            "document": document or manual_doc(),
            **extra,
        },
    )
