"""Histórico de notificações internas, derivado das tabelas existentes no PostgreSQL (HOR-098 / HOR-164 I7).

Os eventos são **calculados** a cada consulta a partir de `horus.productions`/`jobs`/`scripts`/`workflows` (somente leitura);
o estado lido/não lido e o watermark `read_all_before` ficam em `horus.notification_state` e `horus.preferences`.

Eventos (id estável → o estado lido sobrevive a reinícios):
- `production.succeeded|failed|interrupted` — `production:<id>:<status>`, na hora em que o job terminou (janela de 30 dias);
- `script.awaiting_review` — `script:<id>:v<n>`, `pending` enquanto o roteiro aguarda a revisão humana;
- `workflow.awaiting_approval` — `workflow:<id>:r<rev>`, só enquanto aguarda a aprovação;
- `workflow.awaiting_authorization` — `authorization:<job_id>`, só enquanto aguarda a autorização do gasto.
Mensagens trazem apenas rótulos e identificadores curtos: nunca caminhos, conteúdo de roteiro ou credenciais (de uma
falha só entra o código estável do diagnóstico, nunca texto livre de erro).
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .storage import DatabaseUnavailable

WINDOW_DAYS = 30
READ_RETENTION_DAYS = 90
MAX_STATE_IDS = 5000
MAX_PAGE = 200
PREF_KEY_READ_ALL = "notifications.read_all_before"
ID_PATTERN = re.compile(r"^[a-z]+:[A-Za-z0-9_.:-]{1,120}$")
KINDS = (
    "production.succeeded",
    "production.failed",
    "production.interrupted",
    "script.awaiting_review",
    "workflow.awaiting_approval",
    "workflow.awaiting_authorization",
)


def _iso(moment: datetime | str) -> str:
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    return str(moment)


def _parse(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class NotificationService:
    def __init__(self, store, data_dir: Path | None = None, clock=None, *, device_id: str = "default"):
        self.store = store
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.device_id = device_id

    def _uow(self, *, readonly: bool = False):
        if self.store is None or not hasattr(self.store, "uow"):
            raise DatabaseUnavailable("database_unavailable")
        return self.store.uow(readonly=readonly)

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
            payload = json.loads(result) if isinstance(result, str) else result
            code = (payload or {}).get("failure", {}).get("code")
        except (TypeError, ValueError, AttributeError):
            return None
        return code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,60}", code) else None

    def _load_state(self, conn) -> dict:
        read: dict[str, str] = {}
        unread: list[str] = []
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT event_id, state, changed_at FROM horus.notification_state WHERE device_id = %s",
                (self.device_id,),
            )
            for row in cur.fetchall():
                ident = row["event_id"]
                if not isinstance(ident, str) or not ID_PATTERN.fullmatch(ident):
                    continue
                if row["state"] == "read":
                    read[ident] = _iso(row["changed_at"])
                elif row["state"] == "unread":
                    unread.append(ident)
            cur.execute(
                "SELECT value FROM horus.preferences WHERE device_id = %s AND key = %s",
                (self.device_id, PREF_KEY_READ_ALL),
            )
            pref = cur.fetchone()
        before = None
        if pref is not None:
            raw = pref["value"]
            candidate = raw.get("at") if isinstance(raw, dict) else raw
            if _parse(candidate) is not None:
                before = _iso(_parse(candidate))
        return {"read": read, "unread": unread, "read_all_before": before}

    def _events(self, conn) -> list[dict]:
        since = self.clock() - timedelta(days=WINDOW_DAYS)
        events: list[dict] = []
        labels = {
            "succeeded": ("production.succeeded", "success", "Produção concluída", "A produção {n} terminou e aguarda sua revisão."),
            "failed": ("production.failed", "error", "Produção falhou", "A produção {n} falhou."),
            "blocked": ("production.failed", "error", "Produção bloqueada", "A produção {n} foi bloqueada antes de terminar."),
            "interrupted": ("production.interrupted", "warning", "Produção interrompida", "A produção {n} foi interrompida; revise antes de repetir."),
        }
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT p.id, j.status, j.updated_at, j.result
                FROM horus.productions p
                JOIN horus.jobs j ON j.id = p.job_id
                WHERE j.status IN ('succeeded', 'failed', 'blocked', 'interrupted')
                  AND j.updated_at >= %s
                ORDER BY j.updated_at DESC
                LIMIT 500
                """,
                (since,),
            )
            for row in cur.fetchall():
                kind, severity, title, template = labels[row["status"]]
                message = template.format(n=row["id"][:8])
                code = self._failure_code(row["result"]) if row["status"] != "succeeded" else None
                if code:  # só o código estável do diagnóstico; texto livre de erro nunca entra na notificação
                    message += f" Código do diagnóstico: {code}."
                events.append(
                    {
                        "id": f"production:{row['id']}:{row['status']}",
                        "kind": kind,
                        "severity": severity,
                        "title": title,
                        "message": message,
                        "at": _iso(row["updated_at"]),
                        "ref": {"kind": "production", "id": row["id"]},
                        "pending": False,
                    }
                )

            cur.execute(
                """
                SELECT id, workflow_id, version, status, created_at
                FROM horus.scripts
                WHERE created_at >= %s OR status = 'awaiting_review'
                ORDER BY created_at DESC
                LIMIT 500
                """,
                (since,),
            )
            for row in cur.fetchall():
                events.append(
                    {
                        "id": f"script:{row['id']}:v{row['version']}",
                        "kind": "script.awaiting_review",
                        "severity": "info",
                        "title": "Roteiro aguardando revisão" if row["status"] == "awaiting_review" else "Roteiro registrado",
                        "message": f"A versão {row['version']} do roteiro {row['id'][:8]} "
                        + ("aguarda a sua revisão." if row["status"] == "awaiting_review" else "já foi decidida."),
                        "at": _iso(row["created_at"]),
                        "ref": {"kind": "script", "id": row["id"], "workflow_id": row["workflow_id"]},
                        "pending": row["status"] == "awaiting_review",
                    }
                )

            cur.execute(
                "SELECT id, revision, updated_at FROM horus.workflows WHERE status = 'awaiting_approval' LIMIT 200"
            )
            for row in cur.fetchall():
                events.append(
                    {
                        "id": f"workflow:{row['id']}:r{row['revision']}",
                        "kind": "workflow.awaiting_approval",
                        "severity": "info",
                        "title": "Proposta aguardando aprovação",
                        "message": f"A análise {row['id'][:8]} (revisão {row['revision']}) aguarda a sua aprovação.",
                        "at": _iso(row["updated_at"]),
                        "ref": {"kind": "workflow", "id": row["id"]},
                        "pending": True,
                    }
                )

            cur.execute(
                """
                SELECT id, workflow_id, updated_at
                FROM horus.jobs
                WHERE status = 'awaiting_authorization' AND workflow_id IS NOT NULL
                LIMIT 200
                """
            )
            for row in cur.fetchall():
                events.append(
                    {
                        "id": f"authorization:{row['id']}",
                        "kind": "workflow.awaiting_authorization",
                        "severity": "warning",
                        "title": "Execução paga aguardando autorização",
                        "message": f"A análise {row['workflow_id'][:8]} só continua com a sua autorização explícita.",
                        "at": _iso(row["updated_at"]),
                        "ref": {"kind": "workflow", "id": row["workflow_id"]},
                        "pending": True,
                    }
                )
        events.sort(key=lambda e: (e["at"], e["id"]), reverse=True)
        return events

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
        with self._uow(readonly=True) as conn:
            events = self._events(conn)
            state = self._load_state(conn)
        return sum(1 for e in events if not self._is_read(state, e))

    def page(self, limit: int = 50, after: str | None = None, unread: bool | None = None, kind: str | None = None) -> dict:
        if kind is not None and kind not in KINDS:
            raise ValueError("Tipo de notificação desconhecido.")
        limit = max(1, min(int(limit), MAX_PAGE))
        boundary = self._decode(after) if after else None
        with self._uow(readonly=True) as conn:
            events = self._events(conn)
            state = self._load_state(conn)
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
        return {
            "items": window,
            "unread_count": total_unread,
            "next_after": self._cursor(window[-1]) if more and window else None,
        }

    def set_read(self, *, ids: list[str] | None, all: bool, read: bool) -> dict:
        if (ids is None) == (not all):
            raise ValueError("Informe `ids` ou `all`.")
        if ids is not None:
            if len(ids) > 200 or len(set(ids)) != len(ids) or any(not isinstance(i, str) or not ID_PATTERN.fullmatch(i) for i in ids):
                raise ValueError("Lista de identificadores inválida.")
        moment = self.clock()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moment_iso = _iso(moment)
        cutoff = moment - timedelta(days=READ_RETENTION_DAYS)
        with self._uow() as conn:
            with conn.cursor() as cur:
                if all:
                    cur.execute("DELETE FROM horus.notification_state WHERE device_id = %s", (self.device_id,))
                    if read:
                        cur.execute(
                            """
                            INSERT INTO horus.preferences (device_id, key, value, updated_at)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (device_id, key)
                            DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                            """,
                            (self.device_id, PREF_KEY_READ_ALL, Jsonb({"at": moment_iso}), moment),
                        )
                    else:
                        cur.execute(
                            "DELETE FROM horus.preferences WHERE device_id = %s AND key = %s",
                            (self.device_id, PREF_KEY_READ_ALL),
                        )
                elif ids:
                    target_state = "read" if read else "unread"
                    cur.executemany(
                        """
                        INSERT INTO horus.notification_state (device_id, event_id, state, changed_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (device_id, event_id)
                        DO UPDATE SET state = EXCLUDED.state, changed_at = EXCLUDED.changed_at
                        """,
                        [(self.device_id, ident, target_state, moment) for ident in ids],
                    )
                    cur.execute(
                        "DELETE FROM horus.notification_state WHERE device_id = %s AND state = 'read' AND changed_at < %s",
                        (self.device_id, cutoff),
                    )
                    cur.execute(
                        """
                        DELETE FROM horus.notification_state
                        WHERE device_id = %s
                          AND event_id IN (
                              SELECT event_id FROM horus.notification_state
                              WHERE device_id = %s AND state = 'read'
                              ORDER BY changed_at DESC, event_id DESC
                              OFFSET %s
                          )
                        """,
                        (self.device_id, self.device_id, MAX_STATE_IDS),
                    )
            events = self._events(conn)
            state = self._load_state(conn)
        return {"unread_count": sum(1 for e in events if not self._is_read(state, e))}
