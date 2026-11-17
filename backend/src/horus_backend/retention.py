"""Retenção das cópias de dados públicos do YouTube no PostgreSQL operacional (política de 28 dias do Radar).

Os dados do dataset são *redigidos* logicamente (substituídos por um marcador estável com o hash do original,
atualizando também as colunas `*_canonical` na mesma transação), sem apagar linhas, sem alterar estados e sem
tocar em conteúdo editorial, decisões ou hashes históricos (`content_hash`/`request_hash`).

Nota sobre PostgreSQL: devido a MVCC, WAL e backups externos, a redação lógica NÃO realiza nem promete
`secure_delete`, `wal_checkpoint(TRUNCATE)` ou zeragem física imediata em disco.
Idempotente: workflow com `radar.redacted_at` é ignorado.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Callable

from psycopg.rows import dict_row

from .database.manager import DatabaseRefused

REASON = "radar_retention"
ERROR_MARKER = "[redigido: retenção do Radar]"
# Workflows com execução em andamento são adiados: a etapa em curso ainda gravaria o dataset em um artefato.
ACTIVE = {"running", "cancel_requested"}
log = logging.getLogger(__name__)

# Contrato pendente de I4/I5: inventário de literais em `horus.scripts` e `horus.script_receipts`.
ScriptLiteralMatcher = Callable[[Any, str, list[str]], tuple[int, int]]


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def _as_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


@dataclass
class RetentionReport:
    dry_run: bool = False
    evaluated: int = 0
    redacted: int = 0
    already_redacted: int = 0
    not_expired: int = 0
    deferred_active: int = 0
    failed: int = 0
    workflow_ids: list[str] = field(default_factory=list)  # redigidos (ou que seriam, em dry-run)
    errors: list[dict] = field(default_factory=list)
    # Mantido como None em PostgreSQL (sem wal_checkpoint SQLite); MVCC/WAL/backups não têm zeragem imediata.
    checkpoint: dict | None = None

    @property
    def checkpoint_pending(self) -> bool:
        return False

    @property
    def ignored(self) -> int:
        return self.already_redacted + self.not_expired + self.deferred_active


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_time(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def is_marker(value) -> bool:
    return isinstance(value, dict) and value.get("redacted") is True and value.get("reason") == REASON


def marker(original, expired_at: str) -> dict:
    return {"redacted": True, "reason": REASON, "expired_at": expired_at, "sha256": digest(original)}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _literals(dataset) -> list[str]:
    """Textos do YouTube cuja presença literal em campos preservados é registrada ou removida de erros."""
    found = set()
    for item in (dataset.get("items") or []) if isinstance(dataset, dict) else []:
        for key in ("title", "url", "channel"):
            text = item.get(key)
            if isinstance(text, str) and len(text) >= 4:
                found.add(text.lower())
    return sorted(found)


def _hits(value, literals) -> int:
    return sum(any(literal in text.lower() for literal in literals) for text in _strings(value))


@contextmanager
def _uow(db):
    if db is None or not hasattr(db, "connection"):
        raise DatabaseRefused("database_unavailable", 503)
    with db.connection() as conn:
        with conn.transaction():
            yield conn


def _redact_artifacts(db, workflow_id: str, expired_at: str, literals: list[str]) -> tuple[int, int]:
    """research.dataset e analysis.result.sources/facts. Proposta e demais campos editoriais ficam intactos."""
    changed = literal_hits = 0
    with db.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT id, stage, content FROM horus.artifacts WHERE workflow_id = %s ORDER BY created_at, id",
            (workflow_id,),
        ).fetchall()
    for row in rows:
        content = _as_json(row["content"])
        before = encode(content)
        if row["stage"] == "research" and "dataset" in content and not is_marker(content["dataset"]):
            content["dataset"] = marker(content["dataset"], expired_at)
        elif row["stage"] == "analysis" and isinstance(content.get("result"), dict):
            for key in ("sources", "facts"):
                if key in content["result"] and not is_marker(content["result"][key]):
                    content["result"][key] = marker(content["result"][key], expired_at)
        if row["stage"] == "analysis" and isinstance(content.get("result"), dict):
            literal_hits += _hits({k: v for k, v in content["result"].items() if k not in {"sources", "facts"}}, literals)
        elif row["stage"] == "proposal":
            literal_hits += _hits(content, literals)
        after = encode(content)
        if after != before:
            db.execute(
                "UPDATE horus.artifacts SET content = %s::jsonb, content_canonical = %s WHERE id = %s",
                (after, after.encode("utf-8"), row["id"]),  # content_hash permanece
            )
            changed += 1
    return changed, literal_hits


def _redact_runs(db, workflow_id: str, expired_at: str) -> int:
    changed = 0
    with db.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            """SELECT r.id, r.request
               FROM horus.cognitive_runs r
               JOIN horus.jobs j ON j.id = r.job_id
               WHERE j.workflow_id = %s""",
            (workflow_id,),
        ).fetchall()
    for row in rows:
        body = _as_json(row["request"])
        if "input" in body and not (isinstance(body["input"], str) and body["input"].startswith('{"redacted"')):
            body["input"] = encode(marker(body["input"], expired_at))  # request_hash permanece
            after = encode(body)
            db.execute(
                "UPDATE horus.cognitive_runs SET request = %s::jsonb, request_canonical = %s WHERE id = %s",
                (after, after.encode("utf-8"), row["id"]),
            )
            changed += 1
    return changed


def _script_literal_matches(
    db,
    workflow_id: str,
    literals: list[str],
    *,
    script_matcher: ScriptLiteralMatcher | None = None,
) -> tuple[int, int]:
    """Contrato/stub para inventário das cópias editoriais de scripts/recibos (I4/I5).

    Implementação efetiva depende dos contratos/dados de `scripts` e `script_receipts`
    em I4/I5; enquanto não integrados, retorna `(0, 0)` ou delega a `script_matcher`.
    """
    if script_matcher is not None:
        return script_matcher(db, workflow_id, literals)
    return 0, 0


def _scrub_errors(db, workflow_id: str, literals: list[str]) -> int:
    """Mensagens de erro só são trocadas quando reproduzem literalmente texto do dataset."""
    if not literals:
        return 0
    changed = 0
    targets = (
        (
            "SELECT id, error FROM horus.jobs WHERE workflow_id = %s AND error IS NOT NULL",
            "UPDATE horus.jobs SET error = %s WHERE id = %s",
        ),
        (
            """SELECT a.id, a.error
               FROM horus.job_attempts a
               JOIN horus.jobs j ON j.id = a.job_id
               WHERE j.workflow_id = %s AND a.error IS NOT NULL""",
            "UPDATE horus.job_attempts SET error = %s WHERE id = %s",
        ),
        (
            """SELECT r.id, r.error
               FROM horus.cognitive_runs r
               JOIN horus.jobs j ON j.id = r.job_id
               WHERE j.workflow_id = %s AND r.error IS NOT NULL""",
            "UPDATE horus.cognitive_runs SET error = %s WHERE id = %s",
        ),
    )
    for select_sql, update_sql in targets:
        with db.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(select_sql, (workflow_id,)).fetchall()
        for row in rows:
            if _hits(row["error"], literals):
                db.execute(update_sql, (ERROR_MARKER, row["id"]))
                changed += 1

    with db.cursor(row_factory=dict_row) as cur:
        event_rows = cur.execute(
            "SELECT sequence, data FROM horus.workflow_events WHERE workflow_id = %s",
            (workflow_id,),
        ).fetchall()
    for row in event_rows:
        data = _as_json(row["data"])
        if isinstance(data.get("error"), str) and _hits(data["error"], literals):
            data["error"] = ERROR_MARKER
            db.execute(
                "UPDATE horus.workflow_events SET data = %s::jsonb WHERE sequence = %s",
                (encode(data), row["sequence"]),
            )
            changed += 1
    return changed


def _redact_workflow(
    db,
    row: dict,
    payload: dict,
    expired_at: str,
    now_dt: datetime,
    *,
    script_matcher: ScriptLiteralMatcher | None = None,
) -> None:
    dataset = payload.get("dataset")
    literals = _literals(dataset)
    if dataset is not None and not is_marker(dataset):
        payload["dataset"] = marker(dataset, expired_at)
    payload["radar"]["redacted_at"] = now_dt.isoformat()
    encoded_payload = encode(payload)
    # Primeira escrita: qualquer falha adiante desfaz a transação do workflow inteiro.
    db.execute(
        "UPDATE horus.workflows SET payload = %s::jsonb, payload_canonical = %s WHERE id = %s",
        (encoded_payload, encoded_payload.encode("utf-8"), row["id"]),
    )
    artifacts, editorial_hits = _redact_artifacts(db, row["id"], expired_at, literals)
    runs = _redact_runs(db, row["id"], expired_at)
    errors = _scrub_errors(db, row["id"], literals)
    script_hits, receipt_hits = _script_literal_matches(
        db, row["id"], literals, script_matcher=script_matcher
    )
    event_data = encode(
        {
            "reason": REASON,
            "expired_at": expired_at,
            "snapshot_id": payload["radar"].get("snapshot_id"),
            "artifacts": artifacts,
            "cognitive_runs": runs,
            "error_fields": errors,
            "editorial_literal_matches": editorial_hits,
            "script_editorial_literal_matches": script_hits,
            "script_receipt_editorial_literal_matches": receipt_hits,
        }
    )
    db.execute(
        """INSERT INTO horus.workflow_events (workflow_id, revision, kind, data, created_at)
           VALUES (%s, %s, %s, %s::jsonb, %s)""",
        (row["id"], row["revision"], "workflow.radar_redacted", event_data, now_dt),
    )


def run_retention(
    db,
    *,
    clock=utcnow,
    dry_run: bool = False,
    retry_checkpoint: bool = False,
    checkpoint_timeout: float = 2.0,
    script_matcher: ScriptLiteralMatcher | None = None,
) -> RetentionReport:
    """Redige logicamente as cópias do Radar de workflows com `radar.expires_at <= agora`.

    Usa exclusivamente PostgreSQL via UoW de I1. Parâmetros `retry_checkpoint` e
    `checkpoint_timeout` são mantidos apenas para compatibilidade de assinatura durante a
    transição; em PostgreSQL não há `wal_checkpoint` nem promessa de zeragem física imediata.
    """
    del retry_checkpoint, checkpoint_timeout
    report = RetentionReport(dry_run=dry_run)
    with _uow(db) as conn:
        ids = [
            row[0]
            for row in conn.execute(
                """SELECT id FROM horus.workflows
                   WHERE payload->'radar' IS NOT NULL
                     AND jsonb_typeof(payload->'radar') <> 'null'
                   ORDER BY created_at, id"""
            ).fetchall()
        ]
    for workflow_id in ids:
        report.evaluated += 1
        try:
            with _uow(db) as conn:
                lock_clause = " FOR UPDATE" if not dry_run else ""
                with conn.cursor(row_factory=dict_row) as cur:
                    row = cur.execute(
                        f"SELECT id, status, revision, payload FROM horus.workflows WHERE id = %s{lock_clause}",
                        (workflow_id,),
                    ).fetchone()
                if not row:
                    continue
                payload = _as_json(row["payload"])
                radar = payload["radar"]
                now_dt = _as_utc(clock())
                if radar.get("redacted_at"):
                    report.already_redacted += 1
                elif parse_time(radar["expires_at"]) > now_dt:
                    report.not_expired += 1
                elif row["status"] in ACTIVE:
                    report.deferred_active += 1
                else:
                    if not dry_run:
                        _redact_workflow(
                            conn,
                            row,
                            payload,
                            radar["expires_at"],
                            now_dt,
                            script_matcher=script_matcher,
                        )
                    report.redacted += 1
                    report.workflow_ids.append(workflow_id)
        except DatabaseRefused:
            raise
        except Exception as error:  # a transação do workflow foi desfeita; os demais seguem
            report.failed += 1
            report.errors.append({"workflow_id": workflow_id, "error": type(error).__name__})
            log.warning("Retenção do Radar falhou para %s: %s", workflow_id, type(error).__name__)
    return report
