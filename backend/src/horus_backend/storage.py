"""Repositório PostgreSQL exclusivo para jobs, eventos e mídia (HOR-164 / I3)."""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .database.manager import DatabaseManager, DatabaseRefused

ACTIVE_JOB_STATUSES = frozenset({"running", "cancel_requested"})
MAX_EVENTS_PER_JOB = 2000


class DatabaseUnavailable(DatabaseRefused):
    """Erro 503 estável quando o PostgreSQL está indisponível ou não configurado."""

    def __init__(self, code: str = "database_unavailable", *, cause_code: str | None = None, http_status: int = 503):
        super().__init__(code, http_status=http_status)
        self.cause_code = cause_code or code


class LeaseLostError(RuntimeError):
    """Instância perdeu o lease ou o token de fencing foi superado."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_bytes(value: object) -> bytes:
    """Serialização JSON UTF-8 determinística para comparação/hash canônico (emenda A4)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _media_dict(row: dict | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["created_at"] = _iso(item.get("created_at"))
    return item


def _event_dict(row: dict) -> dict:
    item = dict(row)
    item["created_at"] = _iso(item.get("created_at"))
    return item


class Store:
    """Fronteira de repositório e Unit of Work síncrona sobre PostgreSQL (I1)."""

    def __init__(self, db: DatabaseManager | None, *, data_dir: Path | None = None):
        self.db = db if db is not None and hasattr(db, "connection") else None
        self.data_dir = data_dir
        self._database_id: str | None = None

    @property
    def database_id(self) -> str:
        if self._database_id is None:
            with self.uow(readonly=True) as conn:
                row = conn.execute(
                    "SELECT instance_id::text FROM horus.database_identity WHERE singleton = true"
                ).fetchone()
                if not row or not row[0]:
                    raise DatabaseUnavailable("database_unavailable", cause_code="database_schema_mismatch")
                self._database_id = row[0]
        return self._database_id

    @contextmanager
    def uow(self, *, readonly: bool = False):
        """Abre uma Unit of Work síncrona com transação explícita PostgreSQL."""
        if self.db is None:
            raise DatabaseUnavailable("database_unavailable")
        try:
            with self.db.connection() as conn:
                with conn.transaction():
                    if readonly:
                        conn.execute("SET LOCAL TRANSACTION READ ONLY")
                    yield conn
        except DatabaseRefused as error:
            raise DatabaseUnavailable("database_unavailable", cause_code=error.code) from None
        except (psycopg.OperationalError, psycopg.InterfaceError) as error:
            raise DatabaseUnavailable("database_unavailable", cause_code=type(error).__name__) from None

    @staticmethod
    def job(row: dict | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item.pop("idempotency_key", None)
        item.pop("payload_canonical", None)
        if isinstance(item.get("payload"), (str, bytes, bytearray)):
            item["payload"] = json.loads(item["payload"])
        if isinstance(item.get("result"), (str, bytes, bytearray)):
            item["result"] = json.loads(item["result"])
        item["created_at"] = _iso(item.get("created_at"))
        item["updated_at"] = _iso(item.get("updated_at"))
        if item.get("lease_until") is not None:
            item["lease_until"] = _iso(item["lease_until"])
        if item.get("worker_instance_id") is not None:
            item["worker_instance_id"] = str(item["worker_instance_id"])
        return item

    @staticmethod
    def _verify_worker_claim(
        conn: psycopg.Connection,
        job_row: dict,
        worker_instance_id: uuid.UUID | None,
        fencing_token: int | None,
        pg_now: datetime,
    ) -> None:
        if worker_instance_id is None and fencing_token is None:
            return
        if worker_instance_id is None or fencing_token is None:
            raise LeaseLostError("Claim de executor incompleto.")
        worker = conn.execute(
            "SELECT fencing, lease_until FROM horus.worker_instances WHERE id = %s",
            (worker_instance_id,),
        ).fetchone()
        if not worker or worker[0] != fencing_token or worker[1] <= pg_now:
            raise LeaseLostError("Lease da instância expirado ou invalidado.")
        if (
            job_row["worker_instance_id"] != worker_instance_id
            or job_row["fencing_token"] != fencing_token
            or job_row["lease_until"] is None
            or job_row["lease_until"] <= pg_now
        ):
            raise LeaseLostError("Lease do job expirado ou assumido por outro executor.")

    def create_job(self, payload: dict, key: str) -> tuple[dict, bool]:
        encoded_bytes = canonical_json_bytes(payload)
        encoded_str = encoded_bytes.decode("utf-8")
        job_id = uuid.uuid4().hex
        with self.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                inserted = cur.execute(
                    """
                    INSERT INTO horus.jobs(
                        id, idempotency_key, payload, payload_canonical, status, created_at, updated_at
                    )
                    VALUES (%s, %s, %s::jsonb, %s, 'queued', clock_timestamp(), clock_timestamp())
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING *
                    """,
                    (job_id, key, encoded_str, encoded_bytes),
                ).fetchone()
                if inserted is not None:
                    return self.job(inserted), True
                existing = cur.execute(
                    "SELECT * FROM horus.jobs WHERE idempotency_key = %s",
                    (key,),
                ).fetchone()
                if existing is None:
                    raise DatabaseUnavailable("database_unavailable")
                if bytes(existing["payload_canonical"]) != encoded_bytes:
                    raise ValueError("A chave de idempotência já foi usada com outra entrada.")
                return self.job(existing), False

    def get_job(self, job_id: str) -> dict | None:
        with self.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute("SELECT * FROM horus.jobs WHERE id = %s", (job_id,)).fetchone()
                return self.job(row)

    def list_jobs(self, *, media_only: bool = False, offset: int = 0) -> list[dict]:
        with self.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                where = (
                    "WHERE payload->>'operation' IN ('transcription.run', 'youtube.download')"
                    if media_only
                    else ""
                )
                rows = cur.execute(
                    f"SELECT * FROM horus.jobs {where} ORDER BY created_at DESC, id DESC LIMIT 100 OFFSET %s",
                    (offset,),
                ).fetchall()
                return [self.job(row) for row in rows]

    def queued_ids(self) -> list[str]:
        with self.uow(readonly=True) as conn:
            rows = conn.execute(
                "SELECT id FROM horus.jobs WHERE status = 'queued' ORDER BY created_at ASC, id ASC"
            ).fetchall()
            return [row[0] for row in rows]

    def claim_job(
        self,
        job_id: str,
        worker_instance_id: uuid.UUID,
        fencing_token: int,
        *,
        ttl_seconds: float = 30.0,
    ) -> dict | None:
        with self.uow() as conn:
            worker = conn.execute(
                "SELECT fencing, lease_until, clock_timestamp() FROM horus.worker_instances WHERE id = %s",
                (worker_instance_id,),
            ).fetchone()
            if not worker or worker[0] != fencing_token or worker[1] <= worker[2]:
                raise LeaseLostError("Lease da instância expirado ou invalidado.")
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute(
                    """
                    UPDATE horus.jobs
                    SET status = 'running',
                        attempt = attempt + 1,
                        worker_instance_id = %s,
                        lease_until = clock_timestamp() + make_interval(secs => %s),
                        fencing_token = %s,
                        updated_at = clock_timestamp(),
                        result = CASE
                            WHEN payload->>'operation' = 'transcription.run' THEN
                                jsonb_build_object(
                                    'text', '',
                                    'partial', true,
                                    'sequence', COALESCE(
                                        (SELECT MAX(sequence) FROM horus.events WHERE job_id = horus.jobs.id),
                                        0
                                    )
                                )
                            ELSE result
                        END
                    WHERE id = %s
                      AND status = 'queued'
                      AND worker_instance_id IS NULL
                    RETURNING *
                    """,
                    (worker_instance_id, float(ttl_seconds), fencing_token, job_id),
                ).fetchone()
                return self.job(row)

    def renew_job_lease(
        self,
        job_id: str,
        worker_instance_id: uuid.UUID,
        fencing_token: int,
        *,
        ttl_seconds: float = 30.0,
    ) -> str:
        with self.uow() as conn:
            worker = conn.execute(
                "SELECT fencing, lease_until, clock_timestamp() FROM horus.worker_instances WHERE id = %s",
                (worker_instance_id,),
            ).fetchone()
            if not worker or worker[0] != fencing_token or worker[1] <= worker[2]:
                raise LeaseLostError("Lease da instância expirado ou invalidado.")
            row = conn.execute(
                """
                UPDATE horus.jobs
                SET lease_until = clock_timestamp() + make_interval(secs => %s),
                    updated_at = clock_timestamp()
                WHERE id = %s
                  AND worker_instance_id = %s
                  AND fencing_token = %s
                  AND lease_until > clock_timestamp()
                  AND status IN ('running', 'cancel_requested')
                RETURNING lease_until
                """,
                (float(ttl_seconds), job_id, worker_instance_id, fencing_token),
            ).fetchone()
            if not row:
                raise LeaseLostError("Lease do job expirado ou assumido por outro executor.")
            return _iso(row[0])  # type: ignore[return-value]

    def update(
        self,
        job_id: str,
        status: str,
        *,
        result=None,
        error: str | None = None,
        new_attempt: bool = False,
        worker_instance_id: uuid.UUID | None = None,
        fencing_token: int | None = None,
    ) -> dict | None:
        with self.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute(
                    "SELECT *, clock_timestamp() AS pg_now FROM horus.jobs WHERE id = %s FOR UPDATE",
                    (job_id,),
                ).fetchone()
                if row is None:
                    return None
                self._verify_worker_claim(conn, row, worker_instance_id, fencing_token, row["pg_now"])
                if new_attempt:
                    payload = row["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    if payload.get("operation") == "transcription.run":
                        seq = conn.execute(
                            "SELECT COALESCE(MAX(sequence), 0) FROM horus.events WHERE job_id = %s",
                            (job_id,),
                        ).fetchone()[0]
                        result = {"text": "", "partial": True, "sequence": seq}
                encoded_result = json.dumps(result, ensure_ascii=False) if result is not None else None
                clear_claim = status not in ACTIVE_JOB_STATUSES
                updated = cur.execute(
                    """
                    UPDATE horus.jobs
                    SET status = %s,
                        updated_at = clock_timestamp(),
                        result = %s::jsonb,
                        error = %s,
                        attempt = attempt + %s,
                        worker_instance_id = CASE WHEN %s THEN NULL ELSE worker_instance_id END,
                        lease_until = CASE WHEN %s THEN NULL ELSE lease_until END,
                        fencing_token = CASE WHEN %s THEN NULL ELSE fencing_token END
                    WHERE id = %s
                    RETURNING *
                    """,
                    (status, encoded_result, error, int(new_attempt), clear_claim, clear_claim, clear_claim, job_id),
                ).fetchone()
                return self.job(updated)

    def event(
        self,
        job_id: str,
        kind: str,
        message: str,
        *,
        worker_instance_id: uuid.UUID | None = None,
        fencing_token: int | None = None,
    ) -> int:
        with self.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute(
                    "SELECT *, clock_timestamp() AS pg_now FROM horus.jobs WHERE id = %s FOR UPDATE",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(job_id)
                self._verify_worker_claim(conn, row, worker_instance_id, fencing_token, row["pg_now"])
                sequence = conn.execute(
                    """
                    INSERT INTO horus.events(job_id, kind, message, created_at)
                    VALUES (%s, %s, %s, clock_timestamp())
                    RETURNING sequence
                    """,
                    (job_id, kind, message[:2000]),
                ).fetchone()[0]
                if kind == "segment":
                    payload = row["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    if payload.get("operation") == "transcription.run":
                        prev = row["result"] or {}
                        if isinstance(prev, str):
                            prev = json.loads(prev)
                        checkpoint = {"text": prev.get("text", "") + message + "\n", "partial": True, "sequence": sequence}
                        # Checkpoint e evento são atômicos; a poda de logs não apaga o texto integral.
                        conn.execute(
                            "UPDATE horus.jobs SET result = %s::jsonb, updated_at = clock_timestamp() WHERE id = %s",
                            (json.dumps(checkpoint, ensure_ascii=False), job_id),
                        )
                conn.execute(
                    """
                    DELETE FROM horus.events
                    WHERE job_id = %s
                      AND sequence NOT IN (
                          SELECT sequence FROM horus.events
                          WHERE job_id = %s
                          ORDER BY sequence DESC
                          LIMIT %s
                      )
                    """,
                    (job_id, job_id, MAX_EVENTS_PER_JOB),
                )
                return sequence

    def events(self, job_id: str, after: int) -> list[dict]:
        with self.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    "SELECT * FROM horus.events WHERE job_id = %s AND sequence > %s ORDER BY sequence ASC LIMIT 200",
                    (job_id, after),
                ).fetchall()
                return [_event_dict(row) for row in rows]

    def recover(self) -> list[str]:
        """Recupera apenas jobs sem lease ou com lease vencida; nunca interrompe job vivo."""
        recovered: list[str] = []
        with self.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    """
                    SELECT * FROM horus.jobs
                    WHERE workflow_id IS NULL
                      AND status IN ('running', 'cancel_requested')
                      AND (worker_instance_id IS NULL OR lease_until <= clock_timestamp())
                    FOR UPDATE SKIP LOCKED
                    """
                ).fetchall()
                for row in rows:
                    payload = row["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    result = row["result"]
                    if isinstance(result, str):
                        result = json.loads(result)
                    error = "Execução interrompida. Revise antes de tentar novamente."
                    if payload.get("operation") == "production.run":
                        current_result = result or {}
                        if not current_result.get("failure"):
                            from .processes import failure_detail

                            result = {
                                **current_result,
                                "failure": failure_detail("production_interrupted", current_result.get("stage", "prepare")),
                            }
                            error = (
                                "Backend encerrado durante a produção; revise os artefatos e crie nova "
                                "produção explicitamente. Nenhuma etapa foi repetida."
                            )
                    encoded_result = json.dumps(result, ensure_ascii=False) if result is not None else None
                    conn.execute(
                        """
                        UPDATE horus.jobs
                        SET status = 'interrupted',
                            updated_at = clock_timestamp(),
                            result = %s::jsonb,
                            error = %s,
                            worker_instance_id = NULL,
                            lease_until = NULL,
                            fencing_token = NULL
                        WHERE id = %s
                        """,
                        (encoded_result, error, row["id"]),
                    )
                    conn.execute(
                        """
                        INSERT INTO horus.events(job_id, kind, message, created_at)
                        VALUES (%s, 'state', 'interrupted', clock_timestamp())
                        """,
                        (row["id"],),
                    )
                    recovered.append(row["id"])
        return recovered

    def register_media(self, path: Path) -> dict:
        stat = path.stat()
        with self.uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute(
                    """
                    INSERT INTO horus.media(id, path, name, size, modified_ns, created_at)
                    VALUES (%s, %s, %s, %s, %s, clock_timestamp())
                    ON CONFLICT (path) DO UPDATE
                    SET size = EXCLUDED.size,
                        modified_ns = EXCLUDED.modified_ns,
                        created_at = EXCLUDED.created_at
                    RETURNING *
                    """,
                    (uuid.uuid4().hex, str(path), path.name, stat.st_size, stat.st_mtime_ns),
                ).fetchone()
                return _media_dict(row)  # type: ignore[return-value]

    def media(self, media_id: str) -> dict | None:
        with self.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute("SELECT * FROM horus.media WHERE id = %s", (media_id,)).fetchone()
                return _media_dict(row)

    def list_media(self) -> list[dict]:
        with self.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    "SELECT * FROM horus.media ORDER BY created_at DESC, id DESC LIMIT 100"
                ).fetchall()
                return [_media_dict(row) for row in rows]  # type: ignore[misc]
