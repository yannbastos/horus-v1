"""Lease e fencing monotônico de executor em PostgreSQL (`horus.worker_instances`, HOR-164 / I3)."""
from __future__ import annotations

import platform
import uuid

from .database.manager import DatabaseManager
from .storage import DatabaseUnavailable, Store

# Namespace 164, chave 100 reservada para serializar registro e token de fencing monotônico.
WORKER_FENCING_LOCK = (164, 100)
DEFAULT_LEASE_TTL_SECONDS = 30.0
DEFAULT_RENEW_INTERVAL_SECONDS = 10.0


class WorkerLock:
    """Coordena exclusividade por dispositivo e fencing token monotônico via PostgreSQL."""

    def __init__(
        self,
        backend: Store | DatabaseManager | None,
        *,
        device_id: str | None = None,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
        renew_interval_seconds: float = DEFAULT_RENEW_INTERVAL_SECONDS,
        instance_id: uuid.UUID | None = None,
    ):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds deve ser positivo.")
        self._store = backend if isinstance(backend, Store) else Store(backend)
        self.device_id = (device_id or platform.node() or "local-device")[:128]
        self.ttl_seconds = float(ttl_seconds)
        self.renew_interval_seconds = float(renew_interval_seconds)
        self.instance_id = instance_id or uuid.uuid4()
        self.fencing_token: int | None = None
        self.active = False

    def acquire(self) -> int:
        if self.active:
            raise RuntimeError("Trava do executor já adquirida por esta instância.")
        with self._store.uow() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", WORKER_FENCING_LOCK)
            existing = conn.execute(
                """
                SELECT id FROM horus.worker_instances
                WHERE device_id = %s AND lease_until > clock_timestamp()
                LIMIT 1
                """,
                (self.device_id,),
            ).fetchone()
            if existing is not None:
                raise RuntimeError("Outro backend já executa tarefas neste banco do Hórus.")
            next_fencing = conn.execute(
                """
                SELECT GREATEST(
                    COALESCE((SELECT MAX(fencing) FROM horus.worker_instances), 0),
                    COALESCE((SELECT MAX(fencing_token) FROM horus.jobs), 0)
                ) + 1
                """
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO horus.worker_instances(id, device_id, lease_until, fencing)
                VALUES (%s, %s, clock_timestamp() + make_interval(secs => %s), %s)
                ON CONFLICT (id) DO UPDATE
                SET device_id = EXCLUDED.device_id,
                    lease_until = EXCLUDED.lease_until,
                    fencing = EXCLUDED.fencing
                """,
                (self.instance_id, self.device_id, self.ttl_seconds, next_fencing),
            )
        self.fencing_token = int(next_fencing)
        self.active = True
        return self.fencing_token

    def renew(self) -> bool:
        if not self.active or self.fencing_token is None:
            return False
        with self._store.uow() as conn:
            row = conn.execute(
                """
                UPDATE horus.worker_instances
                SET lease_until = clock_timestamp() + make_interval(secs => %s)
                WHERE id = %s AND fencing = %s AND lease_until > clock_timestamp()
                RETURNING lease_until
                """,
                (self.ttl_seconds, self.instance_id, self.fencing_token),
            ).fetchone()
        if row is None:
            self.active = False
            return False
        return True

    def close(self) -> None:
        if not self.active:
            return
        self.active = False
        token = self.fencing_token
        if token is None:
            return
        try:
            with self._store.uow() as conn:
                conn.execute(
                    """
                    UPDATE horus.worker_instances
                    SET lease_until = clock_timestamp()
                    WHERE id = %s AND fencing = %s
                    """,
                    (self.instance_id, token),
                )
        except DatabaseUnavailable:
            # Encerramento com banco já indisponível: o TTL expirará o lease automaticamente.
            pass
