"""Checkpoints humanos para gastar e publicar (HOR-107): barreira técnica no backend, não só na interface.

Toda ação paga (execução cognitiva do Hermes, narração por API paga) ou de publicação exige um checkpoint registrado
**antes**: quem aprovou (`human`/operador local), quando, o quê (SHA-256 do objeto aprovado + descrição curta), até que
valor (`limit_amount`/`currency`) e por quanto tempo (`expires_at`). O código que executa a ação chama `consume`, que, numa
transação do SQLite, confere ação, objeto, validade, limite e uso único, e só então marca o checkpoint como consumido
(replay recusado). Falta, expiração, valor acima do limite, objeto diferente ou reuso levantam `CheckpointRefused`
(subclasse de `SecurityGate`: o HTTP já o traduz em 409 `security_gate`) e a recusa vai para `transmissions`
(kind `gate_refusal`, stage `checkpoint:<ação>`, só o código). O consumo bem-sucedido fica na própria linha do checkpoint
(`consumed_at`/`consumed_ref`), sem duplicar o ledger de transmissões do Hermes.
A tabela é append-only: só a transição única `consumed_at NULL → valor` é permitida (triggers no banco).
Nada aqui ativa provedor, publica ou acessa rede.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .security import SecurityGate, cost_reservation, pg_clock
from .storage import _iso, canonical_json_bytes

ACTIONS = ("hermes_run", "hermes_script", "paid_narration", "publish")
MAX_TTL_SECONDS = 3600
DEFAULT_TTL_SECONDS = 900
CHECKPOINT_LOCK_NAMESPACE = 16411
COLUMNS = ("seq", "id", "action", "subject", "description", "approved_by", "operator_id", "approved_at", "expires_at",
           "limit_amount", "currency", "consumed_at", "consumed_ref")
_SHA = re.compile(r"[a-f0-9]{64}")
_MONEY = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,8})?")
# A descrição é exibida ao humano que aprova: controles, separadores de linha/parágrafo, marcas de direção (bidi) e
# caracteres invisíveis poderiam reordenar ou esconder texto e tornar a aprovação enganosa.
_UNSAFE_DESCRIPTION = re.compile("[\x00-\x1f\x7f-\x9f­؜᠎​-‏ -‮⁠-⁯﻿￹-￻]")


class CheckpointRefused(SecurityGate):
    """Recusa com código estável (`checkpoint_missing`, `checkpoint_expired`, `checkpoint_limit_exceeded`, ...)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"security_gate: {message}")
        self.code = code


def _money(value) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _MONEY.fullmatch(value):
        raise ValueError("Valor monetário inválido.")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError("Valor monetário inválido.") from None


def _consume_amount(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, (int, str)) and not isinstance(value, bool):
        try:
            dec = Decimal(str(value))
        except InvalidOperation:
            raise ValueError("Valor monetário inválido.") from None
    else:
        raise ValueError("Valor monetário inválido.")
    if not dec.is_finite() or dec < 0:
        raise ValueError("Valor monetário inválido.")
    return dec


def _format_row(row: dict) -> dict:
    item = {c: row[c] for c in COLUMNS}
    for col in ("approved_at", "expires_at", "consumed_at"):
        item[col] = _iso(item[col])
    if item["limit_amount"] is not None:
        item["limit_amount"] = str(item["limit_amount"])
    return item


def _acquire_checkpoint_lock(conn, action: str, subject: str) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (CHECKPOINT_LOCK_NAMESPACE, f"{action}:{subject}"))


class CheckpointService:
    def __init__(self, store, audit=None, clock=None, *, settings=None):
        self.store, self.audit = store, audit
        self.settings = settings
        self.clock = clock

    def _now(self, conn) -> datetime:
        return pg_clock(conn, clock=self.clock)

    # --- concessão -----------------------------------------------------------------------------------------------

    def grant(self, *, action, subject, description, limit_amount=None, currency=None, ttl_seconds=DEFAULT_TTL_SECONDS,
              approved_by="human", operator_id="local-operator", key=None, db=None) -> dict:
        """Registra a aprovação humana. `db` junta-se à transação de quem chama (ex.: a autorização do workflow)."""
        if action not in ACTIONS or not isinstance(subject, str) or not _SHA.fullmatch(subject):
            raise ValueError("Ação ou objeto do checkpoint inválidos.")
        if approved_by != "human":
            raise ValueError("Somente aprovação humana é aceita.")
        if not isinstance(description, str) or not 1 <= len(description) <= 200 or _UNSAFE_DESCRIPTION.search(description):
            raise ValueError("Descrição inválida.")
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError("Validade fora do intervalo permitido.")
        amount = _money(limit_amount)
        if (amount is None) != (currency is None) or (currency is not None and not re.fullmatch(r"[A-Z]{3}", str(currency))):
            raise ValueError("Limite e moeda devem vir juntos (moeda ISO de 3 letras).")
        if amount is not None and amount <= 0:
            raise ValueError("O limite deve ser maior que zero.")
        if amount is not None and action in ("hermes_run", "hermes_script"):
            if self.settings is None:
                raise SecurityGate("security_gate: Configuração de teto do checkpoint indisponível.")
            ceiling = cost_reservation(self.settings)
            if currency != "USD" or amount > ceiling:
                self._refuse(action, "checkpoint_limit_exceeds_backend_ceiling",
                             "O limite solicitado excede o teto em USD definido pelo backend.", deferred=db is not None)
        # Narração/publicação ainda não têm teto monetário configurável; consume mantém a barreira final.
        request_obj = {
            "action": action,
            "subject": subject,
            "description": description,
            "limit_amount": limit_amount,
            "currency": currency,
            "ttl_seconds": ttl_seconds,
        }
        request_canonical = canonical_json_bytes(request_obj)

        def insert(conn):
            _acquire_checkpoint_lock(conn, action, subject)
            with conn.cursor(row_factory=dict_row) as cur:
                if key is not None:
                    found = cur.execute(
                        "SELECT * FROM horus.human_checkpoints WHERE idempotency_key = %s",
                        (key,),
                    ).fetchone()
                    if found:
                        if bytes(found["request_canonical"]) != request_canonical:
                            raise ValueError("Chave de idempotência usada com outro pedido.")
                        return _format_row(found), False
                moment = self._now(conn)
                expires_at = moment + timedelta(seconds=ttl_seconds)
                row = cur.execute(
                    """INSERT INTO horus.human_checkpoints(
                        id, action, subject, description, approved_by, operator_id,
                        approved_at, expires_at, limit_amount, currency, idempotency_key,
                        request, request_canonical
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *""",
                    (
                        uuid.uuid4().hex,
                        action,
                        subject,
                        description,
                        "human",
                        operator_id,
                        moment,
                        expires_at,
                        limit_amount,
                        currency,
                        key,
                        Jsonb(request_obj),
                        request_canonical,
                    ),
                ).fetchone()
                return _format_row(row), True

        if db is not None:
            return insert(db)[0]
        with self.store.uow() as conn:
            return insert(conn)[0]

    # --- consumo (a barreira) ------------------------------------------------------------------------------------

    def audit_refusal(self, error: "CheckpointRefused") -> None:
        """Registra a recusa em `transmissions`. Quem consome dentro de uma transação própria (`db=`) chama isto DEPOIS dela
        terminar: a escrita numa segunda conexão ficaria bloqueada pela transação aberta e a recusa reverte a primeira."""
        if self.audit is not None:
            self.audit.best_effort("gate_refusal", f"checkpoint:{error.action}", "refused_gate", provider="human", detail=error.code)

    def _refuse(self, action, code, message, deferred):
        error = CheckpointRefused(code, message)
        error.action = action
        if not deferred:
            self.audit_refusal(error)
        raise error

    def consume(self, *, action, subject, amount=None, currency=None, ref, db=None) -> dict:
        """Confere e consome UM checkpoint; levanta `CheckpointRefused`. Atômico: duas chamadas nunca consomem o mesmo."""
        if action not in ACTIONS:
            raise ValueError("Ação inválida.")
        amount_dec = _consume_amount(amount)
        ref_str = str(ref)[:120]
        if not ref_str:
            raise ValueError("Referência de consumo obrigatória.")
        subject_str = subject if isinstance(subject, str) else ""
        refusal = None

        def run(conn):
            nonlocal refusal
            _acquire_checkpoint_lock(conn, action, subject_str)
            moment = self._now(conn)
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute(
                    """SELECT * FROM horus.human_checkpoints
                       WHERE action = %s AND subject = %s
                         AND consumed_at IS NULL AND expires_at > %s
                       ORDER BY seq DESC LIMIT 1 FOR UPDATE""",
                    (action, subject_str, moment),
                ).fetchone()
                if not row:
                    counts = cur.execute(
                        """SELECT
                             COUNT(*) AS total,
                             COUNT(*) FILTER (WHERE consumed_at IS NULL) AS unconsumed
                           FROM horus.human_checkpoints
                           WHERE action = %s AND subject = %s""",
                        (action, subject_str),
                    ).fetchone()
                    if counts["total"] == 0:
                        refusal = ("checkpoint_missing", "Ação exige aprovação humana registrada (checkpoint) para este objeto.")
                    elif counts["unconsumed"] == 0:
                        refusal = ("checkpoint_consumed", "O checkpoint já foi usado; uma nova aprovação humana é necessária.")
                    else:
                        refusal = ("checkpoint_expired", "O checkpoint expirou; peça uma nova aprovação humana.")
                    return None
                if amount_dec is not None:
                    limit = row["limit_amount"]
                    if limit is None or row["currency"] != currency or amount_dec > limit:
                        refusal = ("checkpoint_limit_exceeded", "O valor da ação excede o limite aprovado pelo humano.")
                        return None
                t_final = self._now(conn)
                try:
                    updated = cur.execute(
                        """UPDATE horus.human_checkpoints
                           SET consumed_at = %s, consumed_ref = %s
                           WHERE id = %s AND consumed_at IS NULL AND expires_at > %s
                             AND (%s::numeric IS NULL OR (
                                 limit_amount IS NOT NULL AND currency = %s AND %s::numeric <= limit_amount
                             ))
                           RETURNING *""",
                        (t_final, ref_str, row["id"], t_final, amount_dec, currency, amount_dec),
                    ).fetchone()
                except psycopg.errors.CheckViolation:
                    refusal = ("checkpoint_expired", "O checkpoint expirou; peça uma nova aprovação humana.")
                    return None
                if updated is None:
                    refusal = ("checkpoint_expired", "O checkpoint expirou; peça uma nova aprovação humana.")
                    return None
                return _format_row(updated)

        if db is not None:
            result = run(db)
        else:
            with self.store.uow() as conn:
                result = run(conn)
        if refusal:
            self._refuse(action, *refusal, deferred=db is not None)
        return result

    # --- leitura -------------------------------------------------------------------------------------------------

    def page(self, limit=50, after=0):
        limit = max(1, min(int(limit), 200))
        with self.store.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    f"SELECT {','.join(COLUMNS)} FROM horus.human_checkpoints WHERE seq > %s ORDER BY seq LIMIT %s",
                    (max(int(after), 0), limit + 1),
                ).fetchall()
        items = [_format_row(r) for r in rows[:limit]]
        return {"items": items, "next_after": items[-1]["seq"] if len(rows) > limit else None}


def require_publication_checkpoint(service: CheckpointService, subject: str, *, ref: str) -> dict:
    """Barreira que qualquer caminho futuro de publicação DEVE chamar antes de enviar algo para fora (hoje não há nenhum)."""
    return service.consume(action="publish", subject=subject, ref=ref)
