"""Histórico de notificações internas, derivado das tabelas existentes (HOR-098). Sem tabela nova nem migração.

Os eventos são **calculados** a cada consulta a partir de `jobs`/`productions`/`scripts`/`workflows` (somente leitura); só o
estado lido/não lido é guardado, num JSON atômico em `<data_dir>/notifications-state.json`. O arquivo é tolerante: se
estiver corrompido, o aviso vai ao log, ele é recriado vazio na próxima escrita e a API nunca falha por causa dele.

Eventos (id estável → o estado lido sobrevive a reinícios):
- `production.succeeded|failed|interrupted` — `production:<id>:<status>`, na hora em que o job terminou (janela de 30 dias);
- `script.awaiting_review` — `script:<id>:v<n>`, `pending` enquanto o roteiro aguarda a revisão humana;
- `workflow.awaiting_approval` — `workflow:<id>:r<rev>`, só enquanto aguarda a aprovação (o banco não guarda o histórico);
- `workflow.awaiting_authorization` — `authorization:<job_id>`, só enquanto aguarda a autorização do gasto.
Mensagens trazem apenas rótulos e identificadores curtos: nunca caminhos, conteúdo de roteiro ou credenciais (de uma
falha só entra o código estável do diagnóstico, nunca texto livre de erro).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


LOG = logging.getLogger("horus.notifications")
WINDOW_DAYS = 30
READ_RETENTION_DAYS = 90
MAX_STATE_IDS = 5000
MAX_PAGE = 200
STATE_FILE = "notifications-state.json"
ID_PATTERN = re.compile(r"^[a-z]+:[A-Za-z0-9_.:-]{1,120}$")
KINDS = ("production.succeeded", "production.failed", "production.interrupted", "script.awaiting_review",
         "workflow.awaiting_approval", "workflow.awaiting_authorization")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class NotificationService:
    def __init__(self, store, data_dir: Path, clock=None):
        self.store, self.path = store, Path(data_dir) / STATE_FILE
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()   # leituras também esperam a troca atômica (no Windows replace falha com o arquivo aberto)

    # --- estado lido/não lido (JSON atômico, tolerante) -----------------------------------------------------------

    def _load(self) -> dict:
        with self.lock:
            return self._load_locked()

    def _load_locked(self) -> dict:
        empty = {"read": {}, "unread": [], "read_all_before": None}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("estrutura inválida")
            read = {k: v for k, v in (raw.get("read") or {}).items() if isinstance(k, str) and ID_PATTERN.fullmatch(k) and isinstance(v, str)}
            unread = [k for k in (raw.get("unread") or []) if isinstance(k, str) and ID_PATTERN.fullmatch(k)]
            before = raw.get("read_all_before")
            return {"read": read, "unread": unread, "read_all_before": before if _parse(before) else None}
        except FileNotFoundError:
            return empty
        except (OSError, ValueError, UnicodeError, AttributeError):
            LOG.warning("Estado de notificações ilegível; será recriado vazio (as notificações continuam disponíveis).")
            return empty

    def _save(self, state: dict) -> None:
        cutoff = self.clock() - timedelta(days=READ_RETENTION_DAYS)
        read = {k: v for k, v in state["read"].items() if (_parse(v) or cutoff) >= cutoff}
        if len(read) > MAX_STATE_IDS:
            read = dict(sorted(read.items(), key=lambda item: item[1])[-MAX_STATE_IDS:])
        payload = {"read": read, "unread": state["unread"][-MAX_STATE_IDS:], "read_all_before": state["read_all_before"]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".notifications-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False)
                for attempt in range(6):    # antivírus/indexador podem segurar o arquivo por instantes
                    try:
                        os.replace(temporary, self.path)
                        break
                    except PermissionError:
                        if attempt == 5:
                            raise
                        time.sleep(.02 * (attempt + 1))
            finally:
                Path(temporary).unlink(missing_ok=True)
        except OSError:
            LOG.warning("Não foi possível gravar o estado de notificações; a marcação de leitura não foi salva.")
            raise

    @staticmethod
    def _is_read(state: dict, item: dict) -> bool:
        if item["id"] in state["read"]:
            return True
        before = _parse(state["read_all_before"])
        at = _parse(item["at"])
        return bool(before and at and at <= before and item["id"] not in state["unread"])

    @staticmethod
    def _failure_code(result) -> str | None:
        try:
            code = (json.loads(result) or {}).get("failure", {}).get("code")
        except (TypeError, ValueError, AttributeError):
            return None
        return code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,60}", code) else None

    # --- eventos derivados ----------------------------------------------------------------------------------------

    def _events(self) -> list[dict]:
        since = _iso(self.clock() - timedelta(days=WINDOW_DAYS))
        events: list[dict] = []
        labels = {"succeeded": ("production.succeeded", "success", "Produção concluída", "A produção {n} terminou e aguarda sua revisão."),
                  "failed": ("production.failed", "error", "Produção falhou", "A produção {n} falhou."),
                  "blocked": ("production.failed", "error", "Produção bloqueada", "A produção {n} foi bloqueada antes de terminar."),
                  "interrupted": ("production.interrupted", "warning", "Produção interrompida", "A produção {n} foi interrompida; revise antes de repetir.")}
        with self.store.connect() as db:
            for row in db.execute("""SELECT p.id, j.status, j.updated_at, j.result FROM productions p JOIN jobs j ON j.id=p.job_id
                    WHERE j.status IN ('succeeded','failed','blocked','interrupted') AND j.updated_at>=? ORDER BY j.updated_at DESC LIMIT 500""", (since,)):
                kind, severity, title, template = labels[row["status"]]
                message = template.format(n=row["id"][:8])
                code = self._failure_code(row["result"]) if row["status"] != "succeeded" else None
                if code:   # só o código estável do diagnóstico; texto livre de erro nunca entra na notificação
                    message += f" Código do diagnóstico: {code}."
                events.append({"id": f"production:{row['id']}:{row['status']}", "kind": kind, "severity": severity, "title": title,
                               "message": message, "at": row["updated_at"], "ref": {"kind": "production", "id": row["id"]}, "pending": False})
            for row in db.execute("SELECT id, workflow_id, version, status, created_at FROM scripts WHERE created_at>=? OR status='awaiting_review' "
                                  "ORDER BY created_at DESC LIMIT 500", (since,)):
                events.append({"id": f"script:{row['id']}:v{row['version']}", "kind": "script.awaiting_review", "severity": "info",
                               "title": "Roteiro aguardando revisão" if row["status"] == "awaiting_review" else "Roteiro registrado",
                               "message": f"A versão {row['version']} do roteiro {row['id'][:8]} " + (
                                   "aguarda a sua revisão." if row["status"] == "awaiting_review" else "já foi decidida."),
                               "at": row["created_at"], "ref": {"kind": "script", "id": row["id"], "workflow_id": row["workflow_id"]},
                               "pending": row["status"] == "awaiting_review"})
            for row in db.execute("SELECT id, revision, updated_at FROM workflows WHERE status='awaiting_approval' LIMIT 200"):
                events.append({"id": f"workflow:{row['id']}:r{row['revision']}", "kind": "workflow.awaiting_approval", "severity": "info",
                               "title": "Proposta aguardando aprovação", "message": f"A análise {row['id'][:8]} (revisão {row['revision']}) aguarda a sua aprovação.",
                               "at": row["updated_at"], "ref": {"kind": "workflow", "id": row["id"]}, "pending": True})
            for row in db.execute("SELECT id, workflow_id, updated_at FROM jobs WHERE status='awaiting_authorization' AND workflow_id IS NOT NULL LIMIT 200"):
                events.append({"id": f"authorization:{row['id']}", "kind": "workflow.awaiting_authorization", "severity": "warning",
                               "title": "Execução paga aguardando autorização", "message": f"A análise {row['workflow_id'][:8]} só continua com a sua autorização explícita.",
                               "at": row["updated_at"], "ref": {"kind": "workflow", "id": row["workflow_id"]}, "pending": True})
        events.sort(key=lambda e: (e["at"], e["id"]), reverse=True)
        return events

    # --- API ------------------------------------------------------------------------------------------------------

    @staticmethod
    def _cursor(item) -> str:
        return base64.urlsafe_b64encode(json.dumps([item["at"], item["id"]]).encode()).decode().rstrip("=")

    @staticmethod
    def _decode(cursor: str):
        try:
            at, ident = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if isinstance(at, str) and isinstance(ident, str):
                return at, ident
        except (ValueError, TypeError, UnicodeError):
            pass
        raise ValueError("Cursor inválido.")

    def unread_count(self) -> int:
        events, state = self._events(), self._load()
        return sum(1 for e in events if not self._is_read(state, e))

    def page(self, limit: int = 50, after: str | None = None, unread: bool | None = None, kind: str | None = None) -> dict:
        if kind is not None and kind not in KINDS:
            raise ValueError("Tipo de notificação desconhecido.")
        limit = max(1, min(int(limit), MAX_PAGE))
        boundary = self._decode(after) if after else None
        events, state = self._events(), self._load()
        total_unread = sum(1 for e in events if not self._is_read(state, e))
        items = []
        for event in events:
            if boundary and (event["at"], event["id"]) >= boundary:
                continue
            if kind and event["kind"] != kind:
                continue
            read = self._is_read(state, event)
            if unread is True and read or unread is False and not read:
                continue
            items.append({**event, "read": read})
        window = items[:limit]
        more = len(items) > limit
        return {"items": window, "unread_count": total_unread, "next_after": self._cursor(window[-1]) if more and window else None}

    def set_read(self, *, ids: list[str] | None, all: bool, read: bool) -> dict:
        if (ids is None) == (not all):
            raise ValueError("Informe `ids` ou `all`.")
        if ids is not None:
            if len(ids) > 200 or len(set(ids)) != len(ids) or any(not isinstance(i, str) or not ID_PATTERN.fullmatch(i) for i in ids):
                raise ValueError("Lista de identificadores inválida.")
        with self.lock:
            state = self._load()
            moment = _iso(self.clock())
            if all:
                if read:
                    state.update(read={}, unread=[], read_all_before=moment)
                else:
                    state.update(read={}, unread=[], read_all_before=None)
            else:
                for ident in ids:
                    if read:
                        state["read"][ident] = moment
                        state["unread"] = [i for i in state["unread"] if i != ident]
                    else:
                        state["read"].pop(ident, None)
                        if ident not in state["unread"]:
                            state["unread"].append(ident)
            self._save(state)
        return {"unread_count": self.unread_count()}
