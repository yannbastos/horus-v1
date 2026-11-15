"""Rotação do token local do backend, em duas fases com validação e reversão automática (HOR-120, item 7.4).

O token local nasce a cada execução (Tauri) e vive só em memória. Para trocá-lo sem reiniciar o backend:

1. `begin`: o cliente autenticado com o token ATUAL propõe um token novo (gerado por ele). O backend valida o formato e abre uma
   janela de `WINDOW_SECONDS` em que o atual e o novo valem.
2. `commit`: o cliente confirma **usando o token novo** (prova de que o guarda e de que o servidor o aceita). Só então o novo
   passa a ser o atual e o antigo deixa de valer.
3. `rollback`: descarta o novo a qualquer momento; se o `commit` não chegar na janela, o novo expira sozinho e o atual
   continua valendo. O backend nunca fica sem um token válido.

O token nunca é gravado em disco, log ou resposta. Tickets de mídia assinados com o token antigo deixam de valer após o
`commit` (o cliente os recarrega em `GET /v1/media`). Uma rotação real exige a autorização do usuário; os testes usam tokens
sintéticos e um `HORUS_DATA_DIR` temporário.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
import uuid

from .workflows import WorkflowConflict

WINDOW_SECONDS = 60.0
MIN_LENGTH, MAX_LENGTH, MIN_DISTINCT = 24, 256, 12
CHARSET = re.compile(r"^[A-Za-z0-9._~-]+$")


class InvalidToken(ValueError):
    """Token proposto inválido; a mensagem nunca contém o valor."""


def validate_new_token(value, current: str) -> str:
    if not isinstance(value, str) or not MIN_LENGTH <= len(value) <= MAX_LENGTH:
        raise InvalidToken(f"O token novo deve ter de {MIN_LENGTH} a {MAX_LENGTH} caracteres.")
    if not CHARSET.fullmatch(value):
        raise InvalidToken("O token novo só pode ter letras, números e . _ ~ -.")
    if len(set(value)) < MIN_DISTINCT:
        raise InvalidToken("O token novo é previsível demais; gere-o com um gerador aleatório seguro.")
    if secrets.compare_digest(value.encode(), current.encode()):
        raise InvalidToken("O token novo deve ser diferente do atual.")
    return value


class TokenState:
    def __init__(self, token: str, *, clock=time.monotonic, window: float = WINDOW_SECONDS):
        self.current, self._clock, self._window = token, clock, window
        self._pending: dict | None = None
        self._lock = threading.RLock()

    def _expire(self) -> None:
        if self._pending and self._clock() >= self._pending["deadline"]:
            self._pending = None            # reversão automática: o atual continua valendo

    def valid_tokens(self) -> list[str]:
        with self._lock:
            self._expire()
            return [self.current] + ([self._pending["token"]] if self._pending else [])

    def match(self, header: str) -> str | None:
        """'current', 'pending' ou None. Compara com todos os candidatos, sem interromper no primeiro acerto."""
        with self._lock:
            self._expire()
            data = header.encode("utf-8", "replace")
            kind = None
            if secrets.compare_digest(data, f"Bearer {self.current}".encode()):
                kind = "current"
            if self._pending and secrets.compare_digest(data, f"Bearer {self._pending['token']}".encode()):
                kind = kind or "pending"
            return kind

    def begin(self, new_token) -> dict:
        with self._lock:
            self._expire()
            if self._pending:
                raise WorkflowConflict("Já há uma rotação em andamento; confirme ou reverta antes de iniciar outra.")
            validate_new_token(new_token, self.current)
            self._pending = {"id": uuid.uuid4().hex, "token": new_token, "deadline": self._clock() + self._window}
            return {"rotation_id": self._pending["id"], "state": "pending", "expires_in_seconds": int(self._window)}

    def commit(self, rotation_id: str, presented: str | None) -> dict:
        with self._lock:
            self._expire()
            if not self._pending or self._pending["id"] != rotation_id:
                raise KeyError(rotation_id)
            if presented != "pending":
                raise PermissionError("A confirmação deve ser feita com o token novo.")
            self.current, self._pending = self._pending["token"], None
            return {"state": "committed"}

    def rollback(self, rotation_id: str) -> dict:
        with self._lock:
            self._expire()
            if not self._pending or self._pending["id"] != rotation_id:
                raise KeyError(rotation_id)
            self._pending = None
            return {"state": "rolled_back"}

    def status(self) -> dict:
        with self._lock:
            self._expire()
            if not self._pending:
                return {"state": "idle", "expires_in_seconds": None}
            return {"state": "pending", "expires_in_seconds": max(0, int(self._pending["deadline"] - self._clock()))}
