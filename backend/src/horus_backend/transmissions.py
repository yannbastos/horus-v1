"""Registro append-only de transmissões ao Hermes (C5): somente metadados, nunca conteúdo.

O envelope entra apenas como tamanho e SHA-256; `detail` é um código curto e fixo escolhido pelo chamador.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid

from .storage import now

LOG = logging.getLogger("horus.transmissions")
DETAIL = re.compile(r"[a-z0-9_:.\-]{1,120}")
COLUMNS = ("seq", "id", "at", "kind", "stage", "workflow_id", "job_id", "attempt_id", "run_id", "destination",
           "provider", "model", "envelope_bytes", "envelope_sha256", "authorization_id", "result", "detail",
           "tokens_total", "cost_usd")


class TransmissionLog:
    def __init__(self, store):
        self.store = store

    def record(self, kind, stage, result, *, workflow_id=None, job_id=None, attempt_id=None, run_id=None,
               destination=None, provider="hermes", model=None, envelope: str | None = None,
               authorization_id=None, detail=None, tokens_total=None, cost_usd=None):
        """Acrescenta um evento. `envelope` é reduzido a (bytes, sha256) aqui e nunca persistido."""
        size = digest = None
        if envelope is not None:
            raw = envelope.encode("utf-8")
            size, digest = len(raw), hashlib.sha256(raw).hexdigest()
        if detail is not None and not DETAIL.fullmatch(detail):
            detail = "detail_redacted"
        with self.store.connect() as db:
            if authorization_id is None and attempt_id:
                row = db.execute("SELECT id FROM authorizations WHERE consumed_attempt_id=?", (attempt_id,)).fetchone()
                authorization_id = row["id"] if row else None
            db.execute("""INSERT INTO transmissions(id,at,kind,stage,workflow_id,job_id,attempt_id,run_id,destination,provider,
                model,envelope_bytes,envelope_sha256,authorization_id,result,detail,tokens_total,cost_usd)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (uuid.uuid4().hex, now(), kind, stage, workflow_id, job_id, attempt_id, run_id, destination, provider,
                        model, size, digest, authorization_id, result, detail,
                        tokens_total if type(tokens_total) is int else None, None if cost_usd is None else str(cost_usd)))

    def best_effort(self, *args, **kwargs):
        """Recusas e desfechos não podem derrubar o fluxo; a falha vai ao log sem conteúdo."""
        try:
            self.record(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - auditoria nunca expõe dados nem interrompe a recusa
            LOG.warning("Falha ao registrar transmissão (%s).", type(error).__name__)

    def page(self, limit=50, after=0):
        limit = max(1, min(int(limit), 200))
        with self.store.connect() as db:
            rows = db.execute(f"SELECT {','.join(COLUMNS)} FROM transmissions WHERE seq>? ORDER BY seq LIMIT ?",
                              (max(int(after), 0), limit + 1)).fetchall()
        items = [dict(row) for row in rows[:limit]]
        return {"items": items, "next_after": items[-1]["seq"] if len(rows) > limit else None}
