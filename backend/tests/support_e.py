"""Helpers do Grupo E (API, acesso a arquivos, token e atualização) portados para PostgreSQL."""
import pytest

from pg_app_support import PG_TEST_CONFIG, _patch_pg_transport
from horus_backend.api import create_app
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from keyring_support import MemoryKeyring


@pytest.fixture
def pg_transport(pg_app_cluster, pg_app_database, monkeypatch):
    """Aponta o transporte do `DatabaseManager` para o banco descartável (loopback) e isola o cofre em memória."""
    _patch_pg_transport(pg_app_cluster, pg_app_database, MemoryKeyring(), monkeypatch)
    return pg_app_database


def pg_create_app(settings):
    """`create_app` com a conexão PG sintética gravada no cofre (em memória) de `settings.data_dir`."""
    vault = ConnectionVault(settings.data_dir)
    if vault.read() is None:
        vault.write(ConnectionRequest.model_validate(PG_TEST_CONFIG))
    return create_app(settings)


from contextlib import contextmanager  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402


@contextmanager
def pg_client_for(settings, **kwargs):
    """`TestClient` sobre `pg_create_app` que libera a trava do executor ao sair (permite reinício no mesmo banco)."""
    with TestClient(pg_create_app(settings), base_url="http://127.0.0.1", **kwargs) as client:
        try:
            yield client
        finally:
            manager = getattr(client.app.state, "manager", None)
            if manager is not None and getattr(manager, "worker_lock", None) is not None:
                manager.worker_lock.close()


def seed_cognitive_run(store, suffix, *, live=True, status="completed", usage=None, request=None):
    """Semeia workflow + job + tentativa + execução cognitiva (FKs reais) para testar a contabilidade diária em PG."""
    import json
    import uuid
    from datetime import datetime, timezone

    from horus_backend.storage import canonical_json_bytes

    workflow, job, attempt, run = (uuid.uuid5(uuid.NAMESPACE_OID, prefix + suffix).hex for prefix in ("w-", "j-", "a-", "r-"))
    now = datetime.now(timezone.utc)
    body = request if request is not None else {}
    with store.uow() as conn:
        conn.execute(
            """INSERT INTO horus.workflows(id, idempotency_key, payload, payload_canonical, revision, status,
                created_at, updated_at, executor, cognitive_client)
               VALUES (%s, %s, '{}'::jsonb, %s, 1, 'running', %s, %s, 'hermes', %s)""",
            (workflow, workflow, canonical_json_bytes({}), now, now, "live" if live else "simulated"))
        conn.execute(
            """INSERT INTO horus.jobs(id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, workflow_id)
               VALUES (%s, %s, '{}'::jsonb, %s, 'running', %s, %s, %s)""",
            (job, job, canonical_json_bytes({}), now, now, workflow))
        conn.execute(
            """INSERT INTO horus.job_attempts(id, job_id, number, status, executor, input_hash, input_artifacts,
                input_artifacts_canonical, started_at)
               VALUES (%s, %s, 1, 'running', 'hermes-v1', 'fixture', '[]'::jsonb, %s, %s)""",
            (attempt, job, canonical_json_bytes([]), now))
        conn.execute(
            """INSERT INTO horus.cognitive_runs(id, job_id, attempt_id, provider, idempotency_key, request, request_hash,
                request_canonical, status, usage, created_at, updated_at)
               VALUES (%s, %s, %s, 'hermes', %s, %s::jsonb, 'h', %s, %s, %s::jsonb, %s, %s)""",
            (run, job, attempt, f"k-{suffix}", json.dumps(body), canonical_json_bytes(body), status,
             json.dumps(usage) if usage is not None else None, now, now))
