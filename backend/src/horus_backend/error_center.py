"""Centro de erros (HOR-095, backend): falhas agregadas derivadas das tabelas existentes, somente leitura.

Sem tabela nova nem migração. Cada item expõe apenas: código estável (`result.failure.code`, validado e restrito ao catálogo),
etapa, tipo, horário, referência ao item de origem e textos em português vindos de um catálogo fixo. Nunca devolve
`stderr_tail`, `jobs.error`, payload, caminhos nem conteúdo de roteiro.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone

MAX_PAGE = 200
SCAN_LIMIT = 5000
KINDS = ("production", "workflow_step", "job", "workflow")
CODE = re.compile(r"^[a-z0-9_]{1,60}$")
STAGE = re.compile(r"^[a-z0-9_]{1,40}$")

_PROD = "Abra o Diagnóstico técnico da produção e crie nova produção após corrigir a causa."
_LOCAL = "Confira os componentes locais em Diagnósticos e crie nova produção."
_NARR = "Gere a narração novamente; se persistir, troque a voz."
_NET = "Crie nova produção; se persistir, reinicie o app."
CATALOG = {
    "render_failed": ("Falha no render", _PROD),
    "render_timeout": ("Render excedeu o tempo limite", "Tente novamente; se repetir, confira o desempenho da máquina."),
    "render_dependencies_missing": ("Dependências de render ausentes", "Instale as dependências locais de render (veja Diagnósticos) e crie nova produção."),
    "local_tool_failed": ("Ferramenta local falhou", _LOCAL),
    "local_narration_failed": ("Falha na narração local", "Confira a voz e os modelos locais de narração e gere de novo."),
    "narration_empty_wav": ("Narração vazia", _NARR),
    "narration_invalid_wav": ("Narração inválida", _NARR),
    "narration_truncated_wav": ("Narração truncada", _NARR),
    "local_pilot_blocked": ("Piloto local bloqueado", "Revise os insumos do piloto antes de repetir."),
    "local_pilot_unavailable": ("Piloto local indisponível", _LOCAL),
    "production_stage_failed": ("Etapa da produção falhou", _PROD),
    "production_worker_failed": ("Processo da produção falhou", _PROD),
    "production_precondition_failed": ("Pré-condição da produção não atendida", "Revise o roteiro e as licenças e crie nova produção."),
    "production_interrupted": ("Produção interrompida", "A execução foi interrompida (por exemplo, ao fechar o app); crie nova produção."),
    "production_timeout": ("Produção excedeu o tempo limite", "Tente novamente; se repetir, confira o desempenho da máquina."),
    "production_failed": ("Produção falhou", _PROD),
    "factory_command_failed": ("Comando da fábrica falhou", "Confira a configuração da fábrica em Diagnósticos e repita a operação."),
    "factory_failed": ("Operação da fábrica falhou", "Confira a configuração da fábrica em Diagnósticos e repita a operação."),
    "factory_blocked": ("Operação da fábrica bloqueada", "Confira a configuração da fábrica em Diagnósticos e repita a operação."),
    "factory_interrupted": ("Operação da fábrica interrompida", "Repita a operação."),
    "browser_guard_missing": ("Proteção de rede do render ausente", "Reinstale os componentes de render e crie nova produção."),
    "browser_not_closed": ("Navegador do render não encerrou", _NET),
    "external_tcp_detected": ("Tráfego de rede externo detectado no render", "Revise os recursos externos do vídeo; o render offline foi bloqueado de propósito."),
    "audit_encoding_invalid": ("Auditoria de rede ilegível", _NET),
    "audit_file_unavailable": ("Auditoria de rede indisponível", _NET),
    "audit_json_incomplete": ("Auditoria de rede incompleta", _NET),
    "netlog_reference_invalid": ("Registro de rede inválido", _NET),
    "netlog_schema_invalid": ("Registro de rede inválido", _NET),
    "workflow_step_failed": ("Etapa da análise falhou", "Abra a análise e use «tentar de novo»."),
    "workflow_step_blocked": ("Etapa da análise bloqueada", "Abra a análise, confira a causa mostrada e use «tentar de novo»."),
    "workflow_step_interrupted": ("Etapa da análise interrompida", "Abra a análise e use «tentar de novo»."),
    "workflow_failed": ("Análise falhou", "Abra a análise e use «tentar de novo»."),
    "workflow_interrupted": ("Análise interrompida", "Abra a análise e use «tentar de novo»."),
    "job_failed": ("Tarefa falhou", "Abra a tarefa, confira os registros e tente de novo."),
    "job_blocked": ("Tarefa bloqueada", "Abra a tarefa, confira a causa e tente de novo."),
    "job_interrupted": ("Tarefa interrompida", "Tente de novo."),
    "unknown_failure": ("Falha sem código conhecido", "Abra o item de origem e consulte o Diagnóstico técnico."),
}
STATUS_PT = {"failed": "falhou", "blocked": "foi bloqueado", "interrupted": "foi interrompido"}
KIND_PT = {"production": "A produção", "workflow_step": "A etapa da análise", "job": "A tarefa", "workflow": "A análise"}
OPERATION_STAGE = {"factory.profile": "prepare", "factory.render.preview": "render", "transcription.run": "transcription",
                   "youtube.download": "download"}


def _parse(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _failure(result):
    try:
        failure = (json.loads(result) or {}).get("failure") if result else None
    except (TypeError, ValueError, AttributeError):
        return {}
    return failure if isinstance(failure, dict) else {}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _item(*, kind, row_id, status, at, failure, derived, stage_fallback, ref):
    code = failure.get("code")
    code = ("unknown_failure" if code not in CATALOG else code) if isinstance(code, str) else derived
    stage = failure.get("stage")
    if not (isinstance(stage, str) and STAGE.fullmatch(stage)):
        stage = stage_fallback if isinstance(stage_fallback, str) and STAGE.fullmatch(stage_fallback) else "unknown"
    title, action = CATALOG[code]
    return {"id": f"{kind}:{row_id}", "kind": kind, "status": status, "code": code, "stage": stage, "at": at, "title": title,
            "message_pt": f"{KIND_PT[kind]} {row_id[:8]} {STATUS_PT[status]} na etapa «{stage}». {title}.",
            "suggested_action_pt": action, "ref": ref,
            "duration_seconds": _number(failure.get("duration_seconds")), "exit_code": _number(failure.get("exit_code"))}


class ErrorCenter:
    def __init__(self, store):
        self.store = store

    def _all(self, since, until) -> list[dict]:
        items, since, until = [], since or "", until or "9999"
        with self.store.connect() as db:
            rows = db.execute("""SELECT j.id, j.status, j.updated_at, j.result, j.step_key, j.workflow_id,
                    json_extract(j.payload,'$.operation') AS operation, p.id AS production_id
                FROM jobs j LEFT JOIN productions p ON p.job_id=j.id
                WHERE j.status IN ('failed','blocked','interrupted') AND j.updated_at>=? AND j.updated_at<=?
                ORDER BY j.updated_at DESC, j.id DESC LIMIT ?""", (since, until, SCAN_LIMIT)).fetchall()
            failed_workflows = {row["workflow_id"] for row in rows if row["workflow_id"]}
            for row in rows:
                failure = _failure(row["result"])
                common = {"status": row["status"], "at": row["updated_at"], "failure": failure}
                if row["production_id"]:
                    items.append(_item(kind="production", row_id=row["production_id"], derived="production_failed", stage_fallback="prepare",
                                       ref={"kind": "production", "id": row["production_id"], "workflow_id": None}, **common))
                elif row["workflow_id"]:
                    items.append(_item(kind="workflow_step", row_id=row["id"], derived=f"workflow_step_{row['status']}",
                                       stage_fallback=row["step_key"],
                                       ref={"kind": "workflow", "id": row["workflow_id"], "workflow_id": row["workflow_id"]}, **common))
                else:
                    items.append(_item(kind="job", row_id=row["id"], derived=f"job_{row['status']}",
                                       stage_fallback=OPERATION_STAGE.get(row["operation"]),
                                       ref={"kind": "job", "id": row["id"], "workflow_id": None}, **common))
            for row in db.execute("""SELECT id, status, updated_at FROM workflows WHERE status IN ('failed','interrupted')
                    AND updated_at>=? AND updated_at<=? ORDER BY updated_at DESC LIMIT ?""", (since, until, SCAN_LIMIT)):
                if row["id"] not in failed_workflows:   # evita contar duas vezes a análise e a etapa que a derrubou
                    items.append(_item(kind="workflow", row_id=row["id"], status=row["status"], at=row["updated_at"], failure={},
                                       derived=f"workflow_{row['status']}", stage_fallback="workflow",
                                       ref={"kind": "workflow", "id": row["id"], "workflow_id": row["id"]}))
        items.sort(key=lambda e: (e["at"], e["id"]), reverse=True)
        return items

    @staticmethod
    def _bound(value, name):
        if value is None:
            return None
        moment = _parse(value)
        if moment is None:
            raise ValueError(f"Parâmetro `{name}` inválido; use data e hora ISO-8601.")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _cursor(item) -> str:
        return base64.urlsafe_b64encode(json.dumps([item["at"], item["id"]]).encode()).decode().rstrip("=")

    @staticmethod
    def _decode(cursor):
        try:
            at, ident = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if isinstance(at, str) and isinstance(ident, str):
                return at, ident
        except (ValueError, TypeError, UnicodeError):
            pass
        raise ValueError("Cursor inválido.")

    def _filtered(self, *, stage, code, kind, since, until):
        if kind is not None and kind not in KINDS:
            raise ValueError("Tipo de erro desconhecido.")
        if stage is not None and not STAGE.fullmatch(stage):
            raise ValueError("Parâmetro `stage` inválido.")
        if code is not None and not CODE.fullmatch(code):
            raise ValueError("Parâmetro `code` inválido.")
        since, until = self._bound(since, "since"), self._bound(until, "until")
        return [i for i in self._all(since, until) if (not stage or i["stage"] == stage) and (not code or i["code"] == code)
                and (not kind or i["kind"] == kind)]

    def page(self, limit=50, after=None, *, stage=None, code=None, kind=None, since=None, until=None) -> dict:
        limit = max(1, min(int(limit), MAX_PAGE))
        boundary = self._decode(after) if after else None
        items = [i for i in self._filtered(stage=stage, code=code, kind=kind, since=since, until=until)
                 if not boundary or (i["at"], i["id"]) < boundary]
        window = items[:limit]
        return {"items": window, "next_after": self._cursor(window[-1]) if len(items) > limit and window else None}

    def summary(self, *, since=None, until=None) -> dict:
        items = self._filtered(stage=None, code=None, kind=None, since=since, until=until)

        def count(key):
            totals: dict[str, int] = {}
            for item in items:
                totals[item[key]] = totals.get(item[key], 0) + 1
            return sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
        return {"total": len(items),
                "by_code": [{"code": c, "count": n, "title": CATALOG[c][0], "suggested_action_pt": CATALOG[c][1]} for c, n in count("code")],
                "by_stage": [{"stage": s, "count": n} for s, n in count("stage")],
                "by_kind": [{"kind": k, "count": n} for k, n in count("kind")]}
