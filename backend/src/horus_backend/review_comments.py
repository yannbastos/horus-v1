"""Comentários de revisão por timecode e versão da produção, com trilha de decisão (HOR-088, backend).

Sem tabela nova nem migração: um JSON atômico por produção em `<data_dir>/production-comments/<production_id>.json`
(padrão das notificações). "Versão" é o SHA-256 do vídeo registrado na produção. A trilha de cada comentário é
append-only: não há edição nem remoção; resolver e reabrir acrescentam eventos. O autor é um rótulo humano informado pelo
operador (`author_kind` é sempre `human`). Arquivo ilegível nunca é sobrescrito: a API recusa com 409 até o operador agir.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .storage import now
from .workflows import WorkflowConflict

SCHEMA = 1
MAX_COMMENTS = 500
MAX_EVENTS = 50
MAX_RECEIPTS = 1000
MAX_PAGE = 200
DIRECTORY = "production-comments"
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
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ReviewCommentService:
    def __init__(self, data_dir: Path, productions):
        self.directory, self.productions = Path(data_dir) / DIRECTORY, productions
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, production_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(production_id, threading.Lock())

    # --- armazenamento ---------------------------------------------------------------------------------------------

    def _path(self, production_id: str) -> Path:
        if not PRODUCTION_ID.fullmatch(production_id):
            raise KeyError(production_id)
        return self.directory / f"{production_id}.json"

    def _load(self, production_id: str) -> dict:
        path = self._path(production_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA or not isinstance(raw.get("comments"), list)
                    or not isinstance(raw.get("receipts"), dict)):
                raise ValueError("estrutura inválida")
            return raw
        except FileNotFoundError:
            return {"schema_version": SCHEMA, "comments": [], "receipts": {}}
        except (OSError, ValueError, UnicodeError):
            raise WorkflowConflict("O arquivo de comentários desta produção está ilegível; nada foi alterado. "
                                   "Restaure-o de um pacote de dados ou mova-o para outro lugar.") from None

    def _save(self, production_id: str, document: dict) -> None:
        path = self._path(production_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        receipts = document["receipts"]
        if len(receipts) > MAX_RECEIPTS:
            document["receipts"] = dict(list(receipts.items())[-MAX_RECEIPTS:])
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".comments-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False)
            for attempt in range(6):    # antivírus/indexador podem segurar o arquivo por instantes
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(.02 * (attempt + 1))
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _public(comment: dict) -> dict:
        return {key: comment[key] for key in ("id", "production_id", "artifact_sha256", "at_seconds", "text", "author",
                                              "author_kind", "status", "created_at", "trail")}

    def _videos(self, production_id: str) -> set[str]:
        record = self.productions.get(production_id)   # KeyError → 404
        if record.get("local_data", {}).get("state") != "present":
            raise WorkflowConflict("Os dados locais desta produção foram excluídos; restaure da quarentena para comentar.")
        return {a["sha256"] for a in record["artifacts"] if a["kind"] == "video"}

    # --- operações -------------------------------------------------------------------------------------------------

    def create(self, production_id: str, request: CommentRequest, key: str) -> tuple[dict, bool]:
        if CONTROL.search(request.text):
            raise ValueError("Texto com caracteres de controle.")
        if request.artifact_sha256 not in self._videos(production_id):
            raise WorkflowConflict("O hash não corresponde a um vídeo desta produção.")
        body = request.model_dump()
        with self._lock(production_id):
            document = self._load(production_id)
            receipt = document["receipts"].get(f"create:{key}")
            if receipt:
                if receipt["digest"] != _digest(body):
                    raise WorkflowConflict("Chave de idempotência usada com outro comentário.")
                found = next((c for c in document["comments"] if c["id"] == receipt["comment_id"]), None)
                if found:
                    return self._public(found), False
            if len(document["comments"]) >= MAX_COMMENTS:
                raise WorkflowConflict(f"Limite de {MAX_COMMENTS} comentários por produção atingido.")
            moment = now()
            comment = {"id": uuid.uuid4().hex, "production_id": production_id, "artifact_sha256": request.artifact_sha256,
                       "at_seconds": request.at_seconds, "text": request.text, "author": request.author, "author_kind": "human",
                       "status": "open", "created_at": moment,
                       "trail": [{"action": "created", "author": request.author, "at": moment}]}
            document["comments"].append(comment)
            document["receipts"][f"create:{key}"] = {"digest": _digest(body), "comment_id": comment["id"]}
            self._save(production_id, document)
            return self._public(comment), True

    def decide(self, production_id: str, comment_id: str, action: str, request: DecisionRequest, key: str) -> dict:
        if not COMMENT_ID.fullmatch(comment_id):
            raise KeyError(comment_id)
        self.productions.get(production_id)
        body, scope = request.model_dump() | {"comment_id": comment_id}, f"{action}:{comment_id}:{key}"
        target = "resolved" if action == "resolve" else "open"
        if request.note and CONTROL.search(request.note):
            raise ValueError("Nota com caracteres de controle.")
        with self._lock(production_id):
            document = self._load(production_id)
            comment = next((c for c in document["comments"] if c["id"] == comment_id), None)
            if comment is None:
                raise KeyError(comment_id)
            receipt = document["receipts"].get(scope)
            if receipt:
                if receipt["digest"] != _digest(body):
                    raise WorkflowConflict("Chave de idempotência usada com outra decisão.")
                return self._public(comment)
            if comment["status"] == target:
                raise WorkflowConflict("O comentário já está " + ("resolvido." if target == "resolved" else "aberto."))
            if len(comment["trail"]) >= MAX_EVENTS:
                raise WorkflowConflict("A trilha deste comentário atingiu o limite de eventos.")
            event = {"action": "resolved" if target == "resolved" else "reopened", "author": request.author, "at": now()}
            if request.note:
                event["note"] = request.note
            comment["trail"].append(event)
            comment["status"] = target
            document["receipts"][scope] = {"digest": _digest(body), "comment_id": comment_id}
            self._save(production_id, document)
            return self._public(comment)

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
        self.productions.get(production_id)
        boundary = self._decode(after) if after else None
        limit = max(1, min(int(limit), MAX_PAGE))
        with self._lock(production_id):
            comments = self._load(production_id)["comments"]
        scoped = [c for c in comments if not artifact_sha256 or c["artifact_sha256"] == artifact_sha256]
        counts = {"open": sum(c["status"] == "open" for c in scoped), "resolved": sum(c["status"] == "resolved" for c in scoped)}
        items = sorted((c for c in scoped if not status or c["status"] == status), key=lambda c: (c["at_seconds"], c["id"]))
        if boundary:
            items = [c for c in items if (c["at_seconds"], c["id"]) > boundary]
        window = items[:limit]
        return {"items": [self._public(c) for c in window], "counts": counts,
                "next_after": self._cursor(window[-1]) if len(items) > limit and window else None}
