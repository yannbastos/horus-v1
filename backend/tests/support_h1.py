"""Helpers compartilhados pelo Grupo H1 (orçamento, Hermes e ciclo de vida) portado para PostgreSQL."""
from datetime import datetime, timedelta, timezone
import uuid

from horus_backend.storage import canonical_json_bytes
from pg_app_support import _patch_pg_transport
from keyring_support import MemoryKeyring
from support_a import configure_pg_vault
from support_d import make_pg_store

START = datetime(2026, 11, 12, 23, 59, 30, tzinfo=timezone.utc)


class FakeClock:
    """Relógio UTC controlado: callable injetado em HermesStages/security (`clock=`)."""

    def __init__(self, initial=START):
        self.current = initial

    def __call__(self):
        return self.current

    def advance(self, **kw):
        self.current += timedelta(**kw)


def store_for(cluster, database, tmp_path, monkeypatch):
    """Store PG isolado (banco descartável por teste)."""
    return make_pg_store(cluster, database, tmp_path, monkeypatch)[1]


def wire_app_database(cluster, database, settings, monkeypatch):
    """Liga `create_app(settings)` ao PG descartável (transporte local + cofre em memória)."""
    _patch_pg_transport(cluster, database, MemoryKeyring(), monkeypatch)
    configure_pg_vault(settings.data_dir)
    return settings


def admin(cluster, database):
    """Conexão administrativa para adulterar/ler o banco descartável (equivale ao antigo `sqlite3.connect`)."""
    return cluster.connect(database, autocommit=True)


def make_context(store, suffix, timestamp, objective, request, simulated=False):
    """Tentativa local válida (workflow, job, attempt) inserida em PG."""
    workflow, job, attempt = (uuid.uuid5(uuid.NAMESPACE_OID, prefix + suffix).hex for prefix in ("w-", "j-", "a-"))
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp)
    with store.uow() as conn:
        conn.execute(
            """INSERT INTO horus.workflows(
                id, idempotency_key, payload, payload_canonical, revision, status,
                created_at, updated_at, executor, cognitive_client
            ) VALUES (%s, %s, '{}'::jsonb, %s, 1, 'running', %s, %s, 'hermes', %s)""",
            (workflow, workflow, canonical_json_bytes({}), timestamp, timestamp, "simulated" if simulated else "live"),
        )
        conn.execute(
            """INSERT INTO horus.jobs(
                id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, workflow_id
            ) VALUES (%s, %s, '{}'::jsonb, %s, 'running', %s, %s, %s)""",
            (job, job, canonical_json_bytes({}), timestamp, timestamp, workflow),
        )
        conn.execute(
            """INSERT INTO horus.job_attempts(
                id, job_id, number, status, executor, input_hash, input_artifacts, input_artifacts_canonical, started_at
            ) VALUES (%s, %s, 1, 'running', 'hermes-v1', 'fixture', '[]'::jsonb, %s, %s)""",
            (attempt, job, canonical_json_bytes([]), timestamp),
        )
    return {
        "workflow_id": workflow,
        "job_id": job,
        "revision": 1,
        "objective": objective,
        "cognitive_client": "simulated" if simulated else "live",
        "sources": [{"id": "snapshot", "content": {name: request[name] for name in ("dataset", "channel_profile")}}],
    }, attempt
