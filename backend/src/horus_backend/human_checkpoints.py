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

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .security import SecurityGate

ACTIONS = ("hermes_run", "hermes_script", "paid_narration", "publish")
MAX_TTL_SECONDS = 3600
DEFAULT_TTL_SECONDS = 900
COLUMNS = ("seq", "id", "action", "subject", "description", "approved_by", "operator_id", "approved_at", "expires_at",
           "limit_amount", "currency", "consumed_at", "consumed_ref")
_SHA = re.compile(r"[a-f0-9]{64}")
_MONEY = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,8})?")


class CheckpointRefused(SecurityGate):
    """Recusa com código estável (`checkpoint_missing`, `checkpoint_expired`, `checkpoint_limit_exceeded`, ...)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"security_gate: {message}")
        self.code = code


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _money(value) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _MONEY.fullmatch(value):
        raise ValueError("Valor monetário inválido.")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError("Valor monetário inválido.") from None


class CheckpointService:
    def __init__(self, store, audit=None, clock=None):
        self.store, self.audit = store, audit
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # --- concessão -----------------------------------------------------------------------------------------------

    def grant(self, *, action, subject, description, limit_amount=None, currency=None, ttl_seconds=DEFAULT_TTL_SECONDS,
              approved_by="human", operator_id="local-operator", key=None, db=None) -> dict:
        """Registra a aprovação humana. `db` junta-se à transação de quem chama (ex.: a autorização do workflow)."""
        if action not in ACTIONS or not isinstance(subject, str) or not _SHA.fullmatch(subject):
            raise ValueError("Ação ou objeto do checkpoint inválidos.")
        if approved_by != "human":
            raise ValueError("Somente aprovação humana é aceita.")
        if not isinstance(description, str) or not 1 <= len(description) <= 200 or re.search(r"[\x00-\x1f\x7f]", description):
            raise ValueError("Descrição inválida.")
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError("Validade fora do intervalo permitido.")
        amount = _money(limit_amount)
        if (amount is None) != (currency is None) or (currency is not None and not re.fullmatch(r"[A-Z]{3}", str(currency))):
            raise ValueError("Limite e moeda devem vir juntos (moeda ISO de 3 letras).")
        if amount is not None and amount <= 0:
            raise ValueError("O limite deve ser maior que zero.")
        moment = self.clock()
        record = dict(id=uuid.uuid4().hex, action=action, subject=subject, description=description, approved_by="human",
                      operator_id=operator_id, approved_at=_iso(moment), expires_at=_iso(moment + timedelta(seconds=ttl_seconds)),
                      limit_amount=limit_amount, currency=currency)
        encoded = json.dumps({k: record[k] for k in ("action", "subject", "description", "limit_amount", "currency")}
                             | {"ttl_seconds": ttl_seconds}, sort_keys=True, separators=(",", ":"))

        def insert(conn):
            if key is not None:
                found = conn.execute("SELECT * FROM human_checkpoints WHERE idempotency_key=?", (key,)).fetchone()
                if found:
                    if found["request"] != encoded:
                        raise ValueError("Chave de idempotência usada com outro pedido.")
                    return {c: found[c] for c in COLUMNS}, False
            conn.execute("""INSERT INTO human_checkpoints(id,action,subject,description,approved_by,operator_id,approved_at,
                expires_at,limit_amount,currency,idempotency_key,request) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (record["id"], action, subject, description, "human", operator_id, record["approved_at"],
                          record["expires_at"], limit_amount, currency, key, encoded))
            row = conn.execute("SELECT * FROM human_checkpoints WHERE id=?", (record["id"],)).fetchone()
            return {c: row[c] for c in COLUMNS}, True

        if db is not None:
            return insert(db)[0]
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
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
        refusal = None

        def run(conn):
            nonlocal refusal
            rows = conn.execute("SELECT * FROM human_checkpoints WHERE action=? AND subject=? ORDER BY seq DESC",
                                (action, subject if isinstance(subject, str) else "")).fetchall()
            if not rows:
                refusal = ("checkpoint_missing", "Ação exige aprovação humana registrada (checkpoint) para este objeto.")
                return None
            usable = [r for r in rows if r["consumed_at"] is None]
            if not usable:
                refusal = ("checkpoint_consumed", "O checkpoint já foi usado; uma nova aprovação humana é necessária.")
                return None
            moment = self.clock()
            live = [r for r in usable if datetime.fromisoformat(r["expires_at"]) > moment]
            if not live:
                refusal = ("checkpoint_expired", "O checkpoint expirou; peça uma nova aprovação humana.")
                return None
            row = live[0]
            if amount is not None:
                limit = _money(row["limit_amount"])
                if limit is None or row["currency"] != currency or Decimal(str(amount)) > limit:
                    refusal = ("checkpoint_limit_exceeded", "O valor da ação excede o limite aprovado pelo humano.")
                    return None
            conn.execute("UPDATE human_checkpoints SET consumed_at=?, consumed_ref=? WHERE id=? AND consumed_at IS NULL",
                         (_iso(moment), str(ref)[:120], row["id"]))
            return {c: row[c] for c in COLUMNS} | {"consumed_at": _iso(moment), "consumed_ref": str(ref)[:120]}

        if db is not None:
            result = run(db)
        else:
            with self.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                result = run(conn)
        if refusal:
            self._refuse(action, *refusal, deferred=db is not None)
        return result

    # --- leitura -------------------------------------------------------------------------------------------------

    def page(self, limit=50, after=0):
        limit = max(1, min(int(limit), 200))
        with self.store.connect() as db:
            rows = db.execute(f"SELECT {','.join(COLUMNS)} FROM human_checkpoints WHERE seq>? ORDER BY seq LIMIT ?",
                              (max(int(after), 0), limit + 1)).fetchall()
        items = [dict(r) for r in rows[:limit]]
        return {"items": items, "next_after": items[-1]["seq"] if len(rows) > limit else None}


def require_publication_checkpoint(service: CheckpointService, subject: str, *, ref: str) -> dict:
    """Barreira que qualquer caminho futuro de publicação DEVE chamar antes de enviar algo para fora (hoje não há nenhum)."""
    return service.consume(action="publish", subject=subject, ref=ref)
