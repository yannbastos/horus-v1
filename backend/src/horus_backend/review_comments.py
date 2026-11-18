"""Comentários de revisão por timecode e versão da produção, com trilha de decisão em PostgreSQL (HOR-088 / HOR-164 I7).

"Versão" é o SHA-256 do vídeo registrado na produção. A trilha de cada comentário é append-only em
`horus.review_comment_events` (com triggers `no_update`/`no_delete`): não há edição nem remoção; resolver e reabrir
acrescentam eventos. Os recibos idempotentes ficam em `horus.review_comment_receipts` com `response_canonical`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .storage import DatabaseUnavailable, canonical_json_bytes
from .workflows import WorkflowConflict

SCHEMA = 1
MAX_COMMENTS = 500
MAX_EVENTS = 50
MAX_PAGE = 200
PRODUCTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
COMMENT_ID = re.compile(r"^[a-f0-9]{32}$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

Author = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")]


class CommentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    at_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False, strict=True)
    text: str = Field(min_length=1, max_length=2000, strict=True)
    author: Author


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    author: Author
    note: str | None = Field(default=None, max_length=500, strict=True)


def _digest(value) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _seconds(value) -> float:
    return float(value) if isinstance(value, Decimal) else float(value)


class ReviewCommentService:
    def __init__(self, data_dir: Path | None, productions, store=None):
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.productions = productions
        self.store = store if store is not None else getattr(productions, "store", None)

    def _uow(self, *, readonly: bool = False):
        if self.store is None or not hasattr(self.store, "uow"):
            raise DatabaseUnavailable("database_unavailable")
        return self.store.uow(readonly=readonly)

    @staticmethod
    def _public(comment: dict, trail: list[dict]) -> dict:
        return {
            "id": comment["id"],
            "production_id": comment["production_id"],
            "artifact_sha256": comment["artifact_sha256"],
            "at_seconds": _seconds(comment["at_seconds"]),
            "text": comment["text"],
            "author": comment["author"],
            "author_kind": comment["author_kind"],
            "status": comment["status"],
            "created_at": _iso(comment["created_at"]),
            "trail": trail,
        }

    @staticmethod
    def _trail_entry(row: dict) -> dict:
        data = row["data"] if isinstance(row["data"], dict) else {}
        entry = {
            "action": row["action"],
            "author": row["operator_id"],
            "at": _iso(row["created_at"]),
        }
        if isinstance(data.get("note"), str) and data["note"]:
            entry["note"] = data["note"]
        return entry

    def _load_comment(self, conn, comment_id: str) -> dict:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM horus.review_comments WHERE id = %s", (comment_id,))
            comment = cur.fetchone()
            if comment is None:
                raise KeyError(comment_id)
            cur.execute(
                "SELECT comment_id, action, operator_id, created_at, data FROM horus.review_comment_events WHERE comment_id = %s ORDER BY seq ASC",
                (comment_id,),
            )
            trail = [self._trail_entry(r) for r in cur.fetchall()]
        return self._public(comment, trail)

    def _videos(self, production_id: str) -> set[str]:
        record = self.productions.get(production_id)  # KeyError → 404
        if record.get("local_data", {}).get("state") != "present":
            raise WorkflowConflict("Os dados locais desta produção foram excluídos; restaure da quarentena para comentar.")
        return {a["sha256"] for a in record["artifacts"] if a["kind"] == "video"}

    # --- operações -------------------------------------------------------------------------------------------------

    def create(self, production_id: str, request: CommentRequest, key: str) -> tuple[dict, bool]:
        if CONTROL.search(request.text):
            raise ValueError("Texto com caracteres de controle.")
        if not PRODUCTION_ID.fullmatch(production_id):
            raise KeyError(production_id)
        if request.artifact_sha256 not in self._videos(production_id):
            raise WorkflowConflict("O hash não corresponde a um vídeo desta produção.")
        req_hash = _digest(request.model_dump())
        scope = "create"
        with self._uow() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(16409, hashtext(%s))", (production_id,))
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT request_hash, response
                    FROM horus.review_comment_receipts
                    WHERE production_id = %s AND scope = %s AND idempotency_key = %s
                    """,
                    (production_id, scope, key),
                )
                receipt = cur.fetchone()
                if receipt is not None:
                    if receipt["request_hash"] != req_hash:
                        raise WorkflowConflict("Chave de idempotência usada com outro comentário.")
                    existing_id = receipt["response"].get("id")
                    if existing_id:
                        return self._load_comment(conn, existing_id), False
                    return receipt["response"], False

                cur.execute(
                    "SELECT COUNT(*) AS total FROM horus.review_comments WHERE production_id = %s",
                    (production_id,),
                )
                if cur.fetchone()["total"] >= MAX_COMMENTS:
                    raise WorkflowConflict(f"Limite de {MAX_COMMENTS} comentários por produção atingido.")

                moment = datetime.now(timezone.utc)
                moment_iso = _iso(moment)
                comment_id = uuid.uuid4().hex
                event_data = {"action": "created", "author": request.author, "at": moment_iso}

                cur.execute(
                    """
                    INSERT INTO horus.review_comments
                        (id, production_id, artifact_sha256, at_seconds, text, author, author_kind, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'human', 'open', %s, %s)
                    """,
                    (
                        comment_id,
                        production_id,
                        request.artifact_sha256,
                        request.at_seconds,
                        request.text,
                        request.author,
                        moment,
                        moment,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO horus.review_comment_events (comment_id, action, operator_id, created_at, data)
                    VALUES (%s, 'created', %s, %s, %s)
                    """,
                    (comment_id, request.author, moment, Jsonb(event_data)),
                )
                public = {
                    "id": comment_id,
                    "production_id": production_id,
                    "artifact_sha256": request.artifact_sha256,
                    "at_seconds": float(request.at_seconds),
                    "text": request.text,
                    "author": request.author,
                    "author_kind": "human",
                    "status": "open",
                    "created_at": moment_iso,
                    "trail": [event_data],
                }
                cur.execute(
                    """
                    INSERT INTO horus.review_comment_receipts
                        (production_id, scope, idempotency_key, request_hash, response, response_canonical)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (production_id, scope, key, req_hash, Jsonb(public), canonical_json_bytes(public)),
                )
                return public, True

    def decide(self, production_id: str, comment_id: str, action: str, request: DecisionRequest, key: str) -> dict:
        if not COMMENT_ID.fullmatch(comment_id) or not PRODUCTION_ID.fullmatch(production_id):
            raise KeyError(comment_id)
        if request.note and CONTROL.search(request.note):
            raise ValueError("Nota com caracteres de controle.")
        self.productions.get(production_id)
        req_hash = _digest(request.model_dump() | {"comment_id": comment_id})
        scope = f"{action}:{comment_id}"
        target = "resolved" if action == "resolve" else "open"
        event_action = "resolved" if target == "resolved" else "reopened"

        with self._uow() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT * FROM horus.review_comments WHERE id = %s AND production_id = %s FOR UPDATE",
                    (comment_id, production_id),
                )
                comment = cur.fetchone()
                if comment is None:
                    raise KeyError(comment_id)

                cur.execute(
                    """
                    SELECT request_hash, response
                    FROM horus.review_comment_receipts
                    WHERE production_id = %s AND scope = %s AND idempotency_key = %s
                    """,
                    (production_id, scope, key),
                )
                receipt = cur.fetchone()
                if receipt is not None:
                    if receipt["request_hash"] != req_hash:
                        raise WorkflowConflict("Chave de idempotência usada com outra decisão.")
                    return self._load_comment(conn, comment_id)

                if comment["status"] == target:
                    raise WorkflowConflict("O comentário já está " + ("resolvido." if target == "resolved" else "aberto."))

                cur.execute(
                    "SELECT COUNT(*) AS total FROM horus.review_comment_events WHERE comment_id = %s",
                    (comment_id,),
                )
                if cur.fetchone()["total"] >= MAX_EVENTS:
                    raise WorkflowConflict("A trilha deste comentário atingiu o limite de eventos.")

                moment = datetime.now(timezone.utc)
                moment_iso = _iso(moment)
                event_data = {"action": event_action, "author": request.author, "at": moment_iso}
                if request.note:
                    event_data["note"] = request.note

                cur.execute(
                    """
                    INSERT INTO horus.review_comment_events (comment_id, action, operator_id, created_at, data)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (comment_id, event_action, request.author, moment, Jsonb(event_data)),
                )
                cur.execute(
                    "UPDATE horus.review_comments SET status = %s, updated_at = %s WHERE id = %s",
                    (target, moment, comment_id),
                )
                public = self._load_comment(conn, comment_id)
                cur.execute(
                    """
                    INSERT INTO horus.review_comment_receipts
                        (production_id, scope, idempotency_key, request_hash, response, response_canonical)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (production_id, scope, key, req_hash, Jsonb(public), canonical_json_bytes(public)),
                )
                return public

    @staticmethod
    def _cursor(item) -> str:
        return base64.urlsafe_b64encode(json.dumps([item["at_seconds"], item["id"]]).encode()).decode().rstrip("=")

    @staticmethod
    def _decode(cursor: str):
        try:
            at, ident = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if isinstance(at, (int, float)) and not isinstance(at, bool) and isinstance(ident, str):
                return at, ident
        except (ValueError, TypeError, UnicodeError):
            pass
        raise ValueError("Cursor inválido.")

    def page(self, production_id: str, *, status=None, artifact_sha256=None, limit=50, after=None) -> dict:
        if status not in (None, "open", "resolved"):
            raise ValueError("Estado desconhecido.")
        if artifact_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", artifact_sha256):
            raise ValueError("Hash inválido.")
        if not PRODUCTION_ID.fullmatch(production_id):
            raise KeyError(production_id)
        self.productions.get(production_id)
        boundary = self._decode(after) if after else None
        limit = max(1, min(int(limit), MAX_PAGE))

        with self._uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT status, COUNT(*) AS total
                    FROM horus.review_comments
                    WHERE production_id = %s
                      AND (%s::text IS NULL OR artifact_sha256 = %s)
                    GROUP BY status
                    """,
                    (production_id, artifact_sha256, artifact_sha256),
                )
                counts = {"open": 0, "resolved": 0}
                for row in cur.fetchall():
                    if row["status"] in counts:
                        counts[row["status"]] = int(row["total"])

                boundary_at = boundary[0] if boundary else None
                boundary_id = boundary[1] if boundary else None
                cur.execute(
                    """
                    SELECT *
                    FROM horus.review_comments
                    WHERE production_id = %s
                      AND (%s::text IS NULL OR artifact_sha256 = %s)
                      AND (%s::text IS NULL OR status = %s)
                      AND (%s::numeric IS NULL OR (at_seconds, id) > (%s::numeric, %s))
                    ORDER BY at_seconds ASC, id ASC
                    LIMIT %s
                    """,
                    (
                        production_id,
                        artifact_sha256,
                        artifact_sha256,
                        status,
                        status,
                        boundary_at,
                        boundary_at,
                        boundary_id,
                        limit + 1,
                    ),
                )
                rows = cur.fetchall()
                more = len(rows) > limit
                window_rows = rows[:limit]
                trails: dict[str, list[dict]] = {r["id"]: [] for r in window_rows}
                if window_rows:
                    cur.execute(
                        """
                        SELECT comment_id, action, operator_id, created_at, data
                        FROM horus.review_comment_events
                        WHERE comment_id = ANY(%s)
                        ORDER BY comment_id, seq ASC
                        """,
                        (list(trails.keys()),),
                    )
                    for ev in cur.fetchall():
                        trails[ev["comment_id"]].append(self._trail_entry(ev))

        items = [self._public(row, trails[row["id"]]) for row in window_rows]
        return {
            "items": items,
            "counts": counts,
            "next_after": self._cursor(items[-1]) if more and items else None,
        }
