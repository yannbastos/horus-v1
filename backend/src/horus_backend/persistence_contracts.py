"""Portas locais de persistência: não substituem Store nem abrem rede."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
import json
import re
import sqlite3
import time
from typing import Iterator, Protocol
from uuid import UUID

from .storage import Store

MAX_PAYLOAD_BYTES = 32768
MAX_PENDING = 1000
MAX_JOURNAL_BYTES = 4 * 1024 * 1024
KINDS = {'workflow', 'script', 'comment', 'review_history'}
_SECRET = re.compile(r'(?i)bearer\s+\S+|postgres(?:ql)?://|sb_secret_|eyJ[\w-]+\.eyJ[\w-]+\.|(?:access_token|refresh_token|service_role|password|connection_string)')


class Conflict(ValueError):
    pass


class JobRepository(Protocol):
    def submit(self, payload: dict, key: str) -> tuple[dict, bool]: ...
    def get(self, job_id: str) -> dict | None: ...
    def list(self, *, media_only: bool = False, offset: int = 0) -> list[dict]: ...


class LocalJobRepository:
    """Adaptador explícito da implementação atual, sem transação remota implícita."""
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
        if (self.kind not in KINDS or type(self.base_version) is not int or self.base_version < 0
                or type(self.deleted) is not bool or not isinstance(self.payload, dict)
                or (self.deleted and self.payload)):
            raise ValueError('Invalid sync change')
        # DTO transporta apenas conteúdo editorial selecionado, nunca configuração arbitrária.
        if not set(self.payload) <= {'title', 'text', 'historical_only'}:
            raise ValueError('Payload field outside sync allowlist')
        if any(not isinstance(v, str) for k, v in self.payload.items() if k != 'historical_only'):
            raise ValueError('Invalid editorial text')
        if 'historical_only' in self.payload and self.payload['historical_only'] is not True:
            raise ValueError('Remote review must be historical')
        if self.kind == 'review_history' and self.payload.get('historical_only') is not True:
            raise ValueError('Remote review cannot authorize execution')
        encoded = json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode()) > MAX_PAYLOAD_BYTES or _SECRET.search(encoded):
            raise ValueError('Payload too large or contains credential material')
        for value in (self.owner_id, self.workspace_id, self.operation_id, self.entity_id):
            if not isinstance(value, UUID):
                raise ValueError('Sync IDs must be UUID')
        return json.dumps({**self.__dict__, 'owner_id': str(self.owner_id), 'workspace_id': str(self.workspace_id),
            'operation_id': str(self.operation_id), 'entity_id': str(self.entity_id)},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class SyncJournal(Protocol):
    def enqueue(self, change: Change) -> None: ...
    def pending(self, owner_id: UUID, limit: int = 100) -> list[dict]: ...
    def ack(self, owner_id: UUID, operation_id: UUID, server_version: int) -> None: ...
    def logout(self, owner_id: UUID) -> None: ...


class RemoteMetadataGateway(Protocol):
    """Implementação remota futura exige JWT individual validado, TLS e limites no servidor."""
    def push(self, changes: list[Change]) -> list[dict]: ...
    def pull(self, workspace_id: UUID, cursor: int, limit: int) -> list[dict]: ...


_DDL = (
    '''CREATE TABLE IF NOT EXISTS sync_outbox (
      owner_id TEXT NOT NULL, operation_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
      request_hash TEXT NOT NULL, envelope TEXT NOT NULL, server_version INTEGER,
      PRIMARY KEY(owner_id,operation_id))''',
    '''CREATE TABLE IF NOT EXISTS sync_inbox (
      owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL, cursor INTEGER NOT NULL,
      envelope TEXT NOT NULL, PRIMARY KEY(owner_id,workspace_id,cursor))''',
    '''CREATE TABLE IF NOT EXISTS sync_cursors (
      owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL, cursor INTEGER NOT NULL,
      PRIMARY KEY(owner_id,workspace_id))''',
)


class LocalSyncJournal:
    """Somente usa a conexão da UoW; nenhuma conexão própria/commit/rede."""
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def enqueue(self, change: Change) -> None:
        encoded = change.encoded()
        hashed = hashlib.sha256(encoded.encode()).hexdigest()
        key = (str(change.owner_id), str(change.operation_id))
        old = self.db.execute('SELECT request_hash FROM sync_outbox WHERE owner_id=? AND operation_id=?', key).fetchone()
        if old:
            if old[0] != hashed:
                raise Conflict('Operation reused with different content')
            return
        count, size = self.db.execute('SELECT count(*),coalesce(sum(length(cast(envelope AS BLOB))),0) FROM sync_outbox').fetchone()
        if count >= MAX_PENDING or size + len(encoded.encode()) > MAX_JOURNAL_BYTES:
            raise ValueError('Sync journal quota reached')
        self.db.execute('INSERT INTO sync_outbox VALUES(?,?,?,?,?,NULL)',
                        (*key, str(change.workspace_id), hashed, encoded))

    def pending(self, owner_id: UUID, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError('Batch outside limit')
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT envelope FROM sync_outbox WHERE owner_id=? AND server_version IS NULL ORDER BY rowid LIMIT ?',
            (str(owner_id), limit))]

    def ack(self, owner_id: UUID, operation_id: UUID, server_version: int) -> None:
        if type(server_version) is not int or server_version < 1:
            raise ValueError('Invalid server version')
        row = self.db.execute('SELECT server_version FROM sync_outbox WHERE owner_id=? AND operation_id=?',
                              (str(owner_id), str(operation_id))).fetchone()
        if row is None or (row[0] is not None and row[0] != server_version):
            raise Conflict('Unknown or divergent receipt')
        self.db.execute('UPDATE sync_outbox SET server_version=? WHERE owner_id=? AND operation_id=?',
                        (server_version, str(owner_id), str(operation_id)))

    def apply_inbox(self, owner_id: UUID, workspace_id: UUID, cursor: int, changes: list[Change]) -> None:
        # Materialização somente no journal; jamais chama JobManager/aprovações.
        if not changes or len(changes) > 100 or type(cursor) is not int or cursor < 1:
            raise ValueError('Invalid inbox page')
        if any(c.owner_id != owner_id or c.workspace_id != workspace_id for c in changes):
            raise ValueError('Cross-account inbox refused')
        envelope = '[' + ','.join(c.encoded() for c in changes) + ']'
        if len(envelope.encode()) > MAX_JOURNAL_BYTES:
            raise ValueError('Inbox page quota reached')
        count, size = self.db.execute('SELECT count(*),coalesce(sum(length(cast(envelope AS BLOB))),0) FROM sync_inbox').fetchone()
        if count >= MAX_PENDING or size + len(envelope.encode()) > MAX_JOURNAL_BYTES:
            raise ValueError('Inbox journal quota reached')
        key = (str(owner_id), str(workspace_id))
        old = self.db.execute('SELECT cursor FROM sync_cursors WHERE owner_id=? AND workspace_id=?', key).fetchone()
        if old and cursor <= old[0]:
            raise Conflict('Cursor must advance explicitly')
        self.db.execute('INSERT INTO sync_inbox VALUES(?,?,?,?)', (*key, cursor, envelope))
        self.db.execute('INSERT INTO sync_cursors VALUES(?,?,?) ON CONFLICT(owner_id,workspace_id) DO UPDATE SET cursor=excluded.cursor', (*key,cursor))

    def logout(self, owner_id: UUID) -> None:
        # Somente cópias de transporte: originais locais continuam no Store.
        for table in ('sync_outbox', 'sync_inbox', 'sync_cursors'):
            self.db.execute(f'DELETE FROM {table} WHERE owner_id=?', (str(owner_id),))


class LocalUnitOfWork:
    """Opt-in explícito e desligado: app atual não instancia esta preparação."""
    def __init__(self, store: Store, *, enabled: bool = False):
        self.store, self.enabled = store, enabled

    @contextmanager
    def transaction(self) -> Iterator[LocalSyncJournal]:
        if not self.enabled:
            raise ValueError('Remote persistence preparation disabled')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for statement in _DDL:
                db.execute(statement)
            yield LocalSyncJournal(db)


def validate_verified_identity(claims: dict, *, algorithm: str, issuer: str, now: int | None = None) -> UUID:
    """Contrato pós-verificação criptográfica; NÃO verifica assinatura nem autentica pedidos.

    O futuro serviço deve chamar biblioteca JOSE/JWKS antes desta validação semântica.
    Esta função não recebe tokens e não está ligada à API local.
    """
    current = int(time.time()) if now is None else now
    if (algorithm not in {'ES256', 'RS256'} or claims.get('aud') != 'authenticated'
            or claims.get('iss') != issuer or type(claims.get('exp')) not in (int,float)
            or not math.isfinite(claims['exp']) or claims['exp'] <= current or claims.get('role') != 'authenticated'):
        raise ValueError('Invalid verified identity claims')
    if not isinstance(claims.get('sub'), str):
        raise ValueError('Missing individual subject')
    return UUID(claims['sub'])
