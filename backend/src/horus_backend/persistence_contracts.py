"""Contratos de persistência PostgreSQL: não substituem Store nem abrem rede."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
from typing import Iterator, Protocol
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from .storage import Store

MAX_PAYLOAD_BYTES = 32768
MAX_PENDING = 1000
MAX_JOURNAL_BYTES = 4 * 1024 * 1024
KINDS = {"workflow", "script", "comment", "review_history"}
_SECRET = re.compile(
    r"(?i)bearer\s+\S+|postgres(?:ql)?://|sb_secret_|eyJ[\w-]+\.eyJ[\w-]+\.|"
    r"(?:access_token|refresh_token|service_role|password|connection_string)"
)


class Conflict(ValueError):
    pass


class JobRepository(Protocol):
    def submit(self, payload: dict, key: str) -> tuple[dict, bool]: ...
    def get(self, job_id: str) -> dict | None: ...
    def list(self, *, media_only: bool = False, offset: int = 0) -> list[dict]: ...


class LocalJobRepository:
    """Adaptador explícito sobre `Store` PostgreSQL, sem transação remota implícita."""

    def __init__(self, store: Store):
        self.store = store

    def submit(self, payload: dict, key: str) -> tuple[dict, bool]:
        return self.store.create_job(payload, key)

    def get(self, job_id: str) -> dict | None:
        return self.store.get_job(job_id)

    def list(self, *, media_only: bool = False, offset: int = 0) -> list[dict]:
        return self.store.list_jobs(media_only=media_only, offset=offset)


@dataclass(frozen=True)
class Change:
    owner_id: UUID
    workspace_id: UUID
    operation_id: UUID
    entity_id: UUID
    base_version: int
    kind: str
    payload: dict
    deleted: bool = False

    def encoded(self) -> str:
        if (
            self.kind not in KINDS
            or type(self.base_version) is not int
            or self.base_version < 0
            or type(self.deleted) is not bool
            or not isinstance(self.payload, dict)
            or (self.deleted and self.payload)
        ):
            raise ValueError("Invalid sync change")
        # DTO transporta apenas conteúdo editorial selecionado, nunca configuração arbitrária.
        if not set(self.payload) <= {"title", "text", "historical_only"}:
            raise ValueError("Payload field outside sync allowlist")
        if any(not isinstance(v, str) for k, v in self.payload.items() if k != "historical_only"):
            raise ValueError("Invalid editorial text")
        if "historical_only" in self.payload and self.payload["historical_only"] is not True:
            raise ValueError("Remote review must be historical")
        if self.kind == "review_history" and self.payload.get("historical_only") is not True:
            raise ValueError("Remote review cannot authorize execution")
        encoded = json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > MAX_PAYLOAD_BYTES or _SECRET.search(encoded):
            raise ValueError("Payload too large or contains credential material")
        for value in (self.owner_id, self.workspace_id, self.operation_id, self.entity_id):
            if not isinstance(value, UUID):
                raise ValueError("Sync IDs must be UUID")
        return json.dumps(
            {
                **self.__dict__,
                "owner_id": str(self.owner_id),
                "workspace_id": str(self.workspace_id),
                "operation_id": str(self.operation_id),
                "entity_id": str(self.entity_id),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


class SyncJournal(Protocol):
    def enqueue(self, change: Change) -> None: ...
    def pending(self, owner_id: UUID, limit: int = 100) -> list[dict]: ...
    def ack(self, owner_id: UUID, operation_id: UUID, server_version: int) -> None: ...
    def logout(self, owner_id: UUID) -> None: ...


class RemoteMetadataGateway(Protocol):
    """Implementação remota futura exige JWT individual validado, TLS e limites no servidor."""

    def push(self, changes: list[Change]) -> list[dict]: ...
    def pull(self, workspace_id: UUID, cursor: int, limit: int) -> list[dict]: ...


class LocalSyncJournal:
    """Usa exclusivamente a conexão PostgreSQL da UoW; nenhuma conexão própria, commit ou rede."""

    def __init__(self, db: psycopg.Connection):
        self.db = db

    @staticmethod
    def _device(owner_id: UUID) -> str:
        return f"sync:{owner_id}"

    def _load(self, owner_id: UUID, key: str, default):
        row = self.db.execute(
            "SELECT value FROM horus.preferences WHERE device_id = %s AND key = %s FOR UPDATE",
            (self._device(owner_id), key),
        ).fetchone()
        if row is None:
            return default
        return row[0]

    def _save(self, owner_id: UUID, key: str, value) -> None:
        self.db.execute(
            """
            INSERT INTO horus.preferences (device_id, key, value, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (device_id, key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
            """,
            (self._device(owner_id), key, Jsonb(value), datetime.now(timezone.utc)),
        )

    def enqueue(self, change: Change) -> None:
        encoded = change.encoded()
        hashed = hashlib.sha256(encoded.encode()).hexdigest()
        outbox = list(self._load(change.owner_id, "sync_outbox", []))
        op_id = str(change.operation_id)
        for entry in outbox:
            if entry["operation_id"] == op_id:
                if entry["request_hash"] != hashed:
                    raise Conflict("Operation reused with different content")
                return
        total_bytes = sum(len(entry["envelope"].encode()) for entry in outbox)
        if len(outbox) >= MAX_PENDING or total_bytes + len(encoded.encode()) > MAX_JOURNAL_BYTES:
            raise ValueError("Sync journal quota reached")
        outbox.append(
            {
                "operation_id": op_id,
                "workspace_id": str(change.workspace_id),
                "request_hash": hashed,
                "envelope": encoded,
                "server_version": None,
            }
        )
        self._save(change.owner_id, "sync_outbox", outbox)

    def pending(self, owner_id: UUID, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("Batch outside limit")
        outbox = self._load(owner_id, "sync_outbox", [])
        return [json.loads(entry["envelope"]) for entry in outbox if entry["server_version"] is None][:limit]

    def ack(self, owner_id: UUID, operation_id: UUID, server_version: int) -> None:
        if type(server_version) is not int or server_version < 1:
            raise ValueError("Invalid server version")
        outbox = list(self._load(owner_id, "sync_outbox", []))
        op_id = str(operation_id)
        for entry in outbox:
            if entry["operation_id"] == op_id:
                if entry["server_version"] is not None and entry["server_version"] != server_version:
                    raise Conflict("Unknown or divergent receipt")
                entry["server_version"] = server_version
                self._save(owner_id, "sync_outbox", outbox)
                return
        raise Conflict("Unknown or divergent receipt")

    def apply_inbox(self, owner_id: UUID, workspace_id: UUID, cursor: int, changes: list[Change]) -> None:
        # Materialização somente no journal; jamais chama JobManager/aprovações.
        if not changes or len(changes) > 100 or type(cursor) is not int or cursor < 1:
            raise ValueError("Invalid inbox page")
        if any(c.owner_id != owner_id or c.workspace_id != workspace_id for c in changes):
            raise ValueError("Cross-account inbox refused")
        envelope = "[" + ",".join(c.encoded() for c in changes) + "]"
        if len(envelope.encode()) > MAX_JOURNAL_BYTES:
            raise ValueError("Inbox page quota reached")
        inbox = list(self._load(owner_id, "sync_inbox", []))
        total_bytes = sum(len(entry["envelope"].encode()) for entry in inbox)
        if len(inbox) >= MAX_PENDING or total_bytes + len(envelope.encode()) > MAX_JOURNAL_BYTES:
            raise ValueError("Inbox journal quota reached")
        cursors = dict(self._load(owner_id, "sync_cursors", {}))
        ws_key = str(workspace_id)
        old_cursor = cursors.get(ws_key)
        if old_cursor is not None and cursor <= old_cursor:
            raise Conflict("Cursor must advance explicitly")
        inbox.append({"workspace_id": ws_key, "cursor": cursor, "envelope": envelope})
        cursors[ws_key] = cursor
        self._save(owner_id, "sync_inbox", inbox)
        self._save(owner_id, "sync_cursors", cursors)

    def cursor(self, owner_id: UUID, workspace_id: UUID) -> int | None:
        cursors = self._load(owner_id, "sync_cursors", {})
        return cursors.get(str(workspace_id))

    def inbox_count(self, owner_id: UUID) -> int:
        return len(self._load(owner_id, "sync_inbox", []))

    def logout(self, owner_id: UUID) -> None:
        # Somente cópias de transporte: originais operacionais continuam no PostgreSQL.
        self.db.execute(
            "DELETE FROM horus.preferences WHERE device_id = %s AND key = ANY(%s)",
            (self._device(owner_id), ["sync_outbox", "sync_inbox", "sync_cursors"]),
        )


class LocalUnitOfWork:
    """Opt-in explícito e desligado: app atual não instancia esta preparação."""

    def __init__(self, store: Store, *, enabled: bool = False):
        self.store, self.enabled = store, enabled

    @contextmanager
    def transaction(self) -> Iterator[LocalSyncJournal]:
        if not self.enabled:
            raise ValueError("Remote persistence preparation disabled")
        with self.store.uow() as conn:
            yield LocalSyncJournal(conn)


def validate_verified_identity(claims: dict, *, algorithm: str, issuer: str, now: int | None = None) -> UUID:
    """Contrato pós-verificação criptográfica; NÃO verifica assinatura nem autentica pedidos.

    O futuro serviço deve chamar biblioteca JOSE/JWKS antes desta validação semântica.
    Esta função não recebe tokens e não está ligada à API local.
    """
    current = int(time.time()) if now is None else now
    if (
        algorithm not in {"ES256", "RS256"}
        or claims.get("aud") != "authenticated"
        or claims.get("iss") != issuer
        or type(claims.get("exp")) not in (int, float)
        or not math.isfinite(claims["exp"])
        or claims["exp"] <= current
        or claims.get("role") != "authenticated"
    ):
        raise ValueError("Invalid verified identity claims")
    if not isinstance(claims.get("sub"), str):
        raise ValueError("Missing individual subject")
    return UUID(claims["sub"])
