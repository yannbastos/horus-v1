"""Mapeamento explícito SQLite v9-v12 -> PostgreSQL horus e normalização canônica."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from typing import Any


MAPPING_VERSION = "v9-v12-pg-20261117160300"
EXPECTED_PG_SCHEMA_VERSION = "20261117160300"

V9_CORE_TABLES = (
    "media",
    "workflows",
    "jobs",
    "job_attempts",
    "artifacts",
    "approvals",
    "cognitive_runs",
    "authorizations",
    "scripts",
    "script_receipts",
    "productions",
    "production_receipts",
    "license_reviews",
    "events",
    "workflow_events",
)

RADAR_TABLES = ("radar_cache", "radar_usage", "radar_blocks", "radar_datasets")


def expected_core_tables(user_version: int) -> frozenset[str]:
    if user_version < 9 or user_version > 12:
        raise ValueError(f"user_version SQLite não suportada: {user_version} (esperado 9..12)")
    tables = set(V9_CORE_TABLES)
    if user_version >= 11:
        tables.add("transmissions")
    if user_version >= 12:
        tables.add("human_checkpoints")
    return frozenset(tables)


@dataclass(frozen=True)
class TableSpec:
    name: str
    pk_cols: tuple[str, ...]
    pg_cols: tuple[str, ...]
    identity_col: str | None = None
    json_cols: tuple[str, ...] = ()
    canonical_map: tuple[tuple[str, str], ...] = ()  # (json_col, canonical_col)
    ts_cols: tuple[str, ...] = ()
    epoch_ts_cols: tuple[str, ...] = ()
    numeric_cols: tuple[str, ...] = ()
    nullable_cols: tuple[str, ...] = ()
    id_range_col: str | None = None
    seq_range_col: str | None = None


TABLE_SPECS: dict[str, TableSpec] = {
    "media": TableSpec(
        name="media",
        pk_cols=("id",),
        pg_cols=("id", "path", "name", "size", "modified_ns", "created_at"),
        ts_cols=("created_at",),
        id_range_col="id",
    ),
    "workflows": TableSpec(
        name="workflows",
        pk_cols=("id",),
        pg_cols=(
            "id",
            "idempotency_key",
            "payload",
            "revision",
            "status",
            "feedback",
            "current_artifact_id",
            "created_at",
            "updated_at",
            "executor",
            "cognitive_client",
            "payload_canonical",
        ),
        json_cols=("payload",),
        canonical_map=(("payload", "payload_canonical"),),
        ts_cols=("created_at", "updated_at"),
        nullable_cols=("current_artifact_id",),
        id_range_col="id",
    ),
    "jobs": TableSpec(
        name="jobs",
        pk_cols=("id",),
        pg_cols=(
            "id",
            "idempotency_key",
            "payload",
            "status",
            "created_at",
            "updated_at",
            "attempt",
            "result",
            "error",
            "workflow_id",
            "workflow_revision",
            "step_key",
            "step_index",
            "depends_on",
            "payload_canonical",
        ),
        json_cols=("payload", "result"),
        canonical_map=(("payload", "payload_canonical"),),
        ts_cols=("created_at", "updated_at"),
        nullable_cols=(
            "result",
            "error",
            "workflow_id",
            "workflow_revision",
            "step_key",
            "step_index",
            "depends_on",
        ),
        id_range_col="id",
    ),
    "job_attempts": TableSpec(
        name="job_attempts",
        pk_cols=("id",),
        pg_cols=(
            "id",
            "job_id",
            "number",
            "status",
            "executor",
            "input_hash",
            "input_artifacts",
            "started_at",
            "finished_at",
            "result",
            "error",
            "input_artifacts_canonical",
            "result_canonical",
        ),
        json_cols=("input_artifacts", "result"),
        canonical_map=(
            ("input_artifacts", "input_artifacts_canonical"),
            ("result", "result_canonical"),
        ),
        ts_cols=("started_at", "finished_at"),
        nullable_cols=("finished_at", "result", "error", "result_canonical"),
        id_range_col="id",
    ),
    "artifacts": TableSpec(
        name="artifacts",
        pk_cols=("id",),
        pg_cols=(
            "id",
            "workflow_id",
            "revision",
            "stage",
            "job_id",
            "attempt_id",
            "schema_version",
            "content",
            "content_hash",
            "created_at",
            "content_canonical",
        ),
        json_cols=("content",),
        canonical_map=(("content", "content_canonical"),),
        ts_cols=("created_at",),
        id_range_col="id",
    ),
    "approvals": TableSpec(
        name="approvals",
        pk_cols=("id",),
        pg_cols=(
            "id",
            "workflow_id",
            "revision",
            "artifact_id",
            "decision",
            "feedback",
            "operator_id",
            "scope",
            "idempotency_key",
            "request",
            "created_at",
            "request_canonical",
        ),
        json_cols=("request",),
        canonical_map=(("request", "request_canonical"),),
        ts_cols=("created_at",),
        id_range_col="id",
    ),
    "cognitive_runs": TableSpec(
        name="cognitive_runs",
        pk_cols=("id",),
        pg_cols=(
            "id",
            "job_id",
            "attempt_id",
            "provider",
            "idempotency_key",
            "request",
            "request_hash",
            "external_ref",
            "status",
            "remote_status",
            "usage",
            "error",
            "created_at",
            "updated_at",
            "request_canonical",
        ),
        json_cols=("request", "usage"),
        canonical_map=(("request", "request_canonical"),),
        ts_cols=("created_at", "updated_at"),
        nullable_cols=("external_ref", "remote_status", "usage", "error"),
        id_range_col="id",
    ),
    "authorizations": TableSpec(
        name="authorizations",
        pk_cols=("id",),
        pg_cols=(
            "id",
            "workflow_id",
            "revision",
            "job_id",
            "input_artifact_id",
            "scope",
            "max_runs",
            "operator_id",
            "idempotency_key",
            "request",
            "consumed_attempt_id",
            "created_at",
            "request_canonical",
        ),
        json_cols=("request",),
        canonical_map=(("request", "request_canonical"),),
        ts_cols=("created_at",),
        nullable_cols=("consumed_attempt_id",),
        id_range_col="id",
    ),
    "scripts": TableSpec(
        name="scripts",
        pk_cols=("id",),
        pg_cols=(
            "id",
            "workflow_id",
            "workflow_revision",
            "proposal_artifact_id",
            "proposal_content_hash",
            "version",
            "status",
            "document",
            "created_at",
            "review",
            "origin",
            "generator",
            "document_canonical",
            "review_canonical",
        ),
        json_cols=("document", "review"),
        canonical_map=(
            ("document", "document_canonical"),
            ("review", "review_canonical"),
        ),
        ts_cols=("created_at",),
        nullable_cols=("review", "generator", "review_canonical"),
        id_range_col="id",
    ),
    "script_receipts": TableSpec(
        name="script_receipts",
        pk_cols=("scope", "idempotency_key"),
        pg_cols=(
            "scope",
            "idempotency_key",
            "request",
            "response",
            "request_canonical",
            "response_canonical",
        ),
        json_cols=("request", "response"),
        canonical_map=(
            ("request", "request_canonical"),
            ("response", "response_canonical"),
        ),
    ),
    "productions": TableSpec(
        name="productions",
        pk_cols=("id",),
        pg_cols=(
            "id",
            "script_id",
            "script_version",
            "script_content_hash",
            "script_review",
            "job_id",
            "mode",
            "plan",
            "artifacts",
            "review",
            "created_at",
            "script_review_canonical",
            "plan_canonical",
            "review_canonical",
        ),
        json_cols=("script_review", "plan", "artifacts", "review"),
        canonical_map=(
            ("script_review", "script_review_canonical"),
            ("plan", "plan_canonical"),
            ("review", "review_canonical"),
        ),
        ts_cols=("created_at",),
        nullable_cols=("review", "review_canonical"),
        id_range_col="id",
    ),
    "production_receipts": TableSpec(
        name="production_receipts",
        pk_cols=("scope", "idempotency_key"),
        pg_cols=(
            "scope",
            "idempotency_key",
            "request",
            "production_id",
            "request_canonical",
        ),
        json_cols=("request",),
        canonical_map=(("request", "request_canonical"),),
    ),
    "license_reviews": TableSpec(
        name="license_reviews",
        pk_cols=("seq",),
        pg_cols=(
            "seq",
            "id",
            "production_id",
            "asset_id",
            "asset_sha256",
            "decision",
            "license",
            "evidence",
            "reviewed_by",
            "reviewed_at",
            "supersedes",
            "idempotency_key",
            "request",
            "request_canonical",
        ),
        identity_col="seq",
        json_cols=("request",),
        canonical_map=(("request", "request_canonical"),),
        ts_cols=("reviewed_at",),
        nullable_cols=("supersedes",),
        id_range_col="id",
        seq_range_col="seq",
    ),
    "events": TableSpec(
        name="events",
        pk_cols=("sequence",),
        pg_cols=("sequence", "job_id", "kind", "message", "created_at"),
        identity_col="sequence",
        ts_cols=("created_at",),
        seq_range_col="sequence",
    ),
    "workflow_events": TableSpec(
        name="workflow_events",
        pk_cols=("sequence",),
        pg_cols=("sequence", "workflow_id", "revision", "kind", "data", "created_at"),
        identity_col="sequence",
        json_cols=("data",),
        ts_cols=("created_at",),
        seq_range_col="sequence",
    ),
    "transmissions": TableSpec(
        name="transmissions",
        pk_cols=("seq",),
        pg_cols=(
            "seq",
            "id",
            "at",
            "kind",
            "stage",
            "workflow_id",
            "job_id",
            "attempt_id",
            "run_id",
            "destination",
            "provider",
            "model",
            "envelope_bytes",
            "envelope_sha256",
            "authorization_id",
            "result",
            "detail",
            "tokens_total",
            "cost_usd",
        ),
        identity_col="seq",
        ts_cols=("at",),
        numeric_cols=("cost_usd",),
        nullable_cols=(
            "workflow_id",
            "job_id",
            "attempt_id",
            "run_id",
            "destination",
            "provider",
            "model",
            "envelope_bytes",
            "envelope_sha256",
            "authorization_id",
            "detail",
            "tokens_total",
            "cost_usd",
        ),
        id_range_col="id",
        seq_range_col="seq",
    ),
    "human_checkpoints": TableSpec(
        name="human_checkpoints",
        pk_cols=("seq",),
        pg_cols=(
            "seq",
            "id",
            "action",
            "subject",
            "description",
            "approved_by",
            "operator_id",
            "approved_at",
            "expires_at",
            "limit_amount",
            "currency",
            "consumed_at",
            "consumed_ref",
            "idempotency_key",
            "request",
            "request_canonical",
        ),
        identity_col="seq",
        json_cols=("request",),
        canonical_map=(("request", "request_canonical"),),
        ts_cols=("approved_at", "expires_at", "consumed_at"),
        numeric_cols=("limit_amount",),
        nullable_cols=(
            "limit_amount",
            "currency",
            "consumed_at",
            "consumed_ref",
            "idempotency_key",
        ),
        id_range_col="id",
        seq_range_col="seq",
    ),
    "radar_cache": TableSpec(
        name="radar_cache",
        pk_cols=("key",),
        pg_cols=("key", "fetched", "expires", "payload"),
        json_cols=("payload",),
        ts_cols=("fetched",),
        epoch_ts_cols=("expires",),
        id_range_col="key",
    ),
    "radar_usage": TableSpec(
        name="radar_usage",
        pk_cols=("id",),
        pg_cols=("id", "at", "bucket"),
        epoch_ts_cols=("at",),
        id_range_col="id",
    ),
    "radar_blocks": TableSpec(
        name="radar_blocks",
        pk_cols=("bucket",),
        pg_cols=("bucket", "until"),
        epoch_ts_cols=("until",),
        id_range_col="bucket",
    ),
    "radar_datasets": TableSpec(
        name="radar_datasets",
        pk_cols=("id", "revision"),
        pg_cols=("id", "revision", "expires", "collected_at", "payload"),
        json_cols=("payload",),
        epoch_ts_cols=("expires",),
        ts_cols=("collected_at",),
        id_range_col="id",
    ),
}


LOAD_ORDER = (
    "media",
    "workflows",
    "jobs",
    "job_attempts",
    "artifacts",
    "approvals",
    "cognitive_runs",
    "authorizations",
    "scripts",
    "script_receipts",
    "productions",
    "production_receipts",
    "license_reviews",
    "events",
    "workflow_events",
    "transmissions",
    "human_checkpoints",
    "radar_cache",
    "radar_usage",
    "radar_blocks",
    "radar_datasets",
)


def parse_utc_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Timestamp vazio")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    else:
        raise ValueError(f"Tipo inválido para timestamp: {type(value).__name__}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_finite_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Float não finito proibido para Decimal")
        dec = Decimal(str(value))
    elif isinstance(value, (int, str)) and not isinstance(value, bool):
        text = str(value).strip()
        if not text:
            raise ValueError("Decimal vazio")
        try:
            dec = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("Decimal inválido") from exc
    else:
        raise ValueError(f"Tipo inválido para Decimal: {type(value).__name__}")
    if not dec.is_finite() or dec < 0:
        raise ValueError("Decimal deve ser finito e não negativo")
    return dec


def _reject_nul_in_json(obj: Any) -> None:
    if isinstance(obj, str):
        if "\x00" in obj:
            raise ValueError("Caractere NUL (\\u0000) proibido em JSONB")
    elif isinstance(obj, list):
        for item in obj:
            _reject_nul_in_json(item)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and "\x00" in k:
                raise ValueError("Caractere NUL (\\u0000) proibido em chave JSONB")
            _reject_nul_in_json(v)


def parse_json_canonical(value: Any, *, allow_null: bool = False) -> tuple[Any, bytes | None]:
    if value is None:
        if allow_null:
            return None, None
        raise ValueError("JSON obrigatório ausente")
    if isinstance(value, bytes):
        raw_bytes = value
        text = value.decode("utf-8")
    elif isinstance(value, str):
        text = value
        raw_bytes = value.encode("utf-8")
    else:
        raise ValueError(f"Tipo inválido para JSON legado: {type(value).__name__}")

    if "\x00" in text:
        raise ValueError("Caractere NUL (\\x00) proibido em JSON")

    def _reject_nan(token: str) -> Any:
        raise ValueError(f"Constante JSON não finita proibida: {token}")

    parsed = json.loads(text, parse_constant=_reject_nan)
    if not isinstance(parsed, (dict, list)):
        raise ValueError("JSON operacional deve ser object ou array")
    _reject_nul_in_json(parsed)
    return parsed, raw_bytes


def validate_row_constraints(table: str, row: dict[str, Any]) -> None:
    """Valida NOT NULL, NUL em strings e CHECK constraints do PostgreSQL ainda no dry-run."""
    spec = TABLE_SPECS[table]
    nullable = set(spec.nullable_cols)
    for col in spec.pg_cols:
        val = row.get(col)
        if val is None:
            if col not in nullable:
                raise ValueError(f"Coluna NOT NULL '{table}.{col}' está nula")
            continue
        if isinstance(val, str) and "\x00" in val:
            raise ValueError(f"Caractere NUL (\\x00) proibido em '{table}.{col}'")

    if spec.identity_col:
        seq_val = row.get(spec.identity_col)
        if not isinstance(seq_val, int) or seq_val < 1:
            raise ValueError(f"Identidade '{table}.{spec.identity_col}' deve ser >= 1")

    if table == "workflows":
        if row["revision"] <= 0:
            raise ValueError("workflows.revision deve ser > 0")
        if row["status"] not in {
            "queued", "running", "awaiting_approval", "approved", "rejected",
            "cancel_requested", "cancelled", "interrupted", "failed",
        }:
            raise ValueError("workflows.status inválido")
        if row["executor"] not in {"simulated", "hermes"}:
            raise ValueError("workflows.executor inválido")
        if row["cognitive_client"] not in {"live", "simulated"}:
            raise ValueError("workflows.cognitive_client inválido")
    elif table == "job_attempts":
        if row["number"] <= 0:
            raise ValueError("job_attempts.number deve ser > 0")
        if row["status"] not in {"running", "succeeded", "failed", "interrupted", "cancelled"}:
            raise ValueError("job_attempts.status inválido")
    elif table == "approvals":
        if row["decision"] not in {"approve", "reject", "request_changes"}:
            raise ValueError("approvals.decision inválido")
    elif table == "cognitive_runs":
        if row["provider"] != "hermes":
            raise ValueError("cognitive_runs.provider inválido")
        if row["status"] not in {
            "submitting", "admitted", "completed", "failed", "cancelled",
            "interrupted", "unknown", "policy_violation", "invalid_output",
        }:
            raise ValueError("cognitive_runs.status inválido")
    elif table == "authorizations":
        if row["scope"] != "cognitive_run" or row["max_runs"] != 1:
            raise ValueError("authorizations.scope/max_runs inválido")
    elif table == "scripts":
        if row["version"] <= 0:
            raise ValueError("scripts.version deve ser > 0")
        if row["status"] not in {"awaiting_review", "approved", "rejected", "changes_requested"}:
            raise ValueError("scripts.status inválido")
        if row["origin"] not in {"simulated", "human"}:
            raise ValueError("scripts.origin inválido")
        if row["generator"] is not None and row["generator"] != "hermes":
            raise ValueError("scripts.generator inválido")
    elif table == "productions":
        if row["mode"] not in {"simulated", "local_demo", "local_pilot"}:
            raise ValueError("productions.mode inválido")
    elif table == "license_reviews":
        if row["decision"] not in {"verified", "rejected"}:
            raise ValueError("license_reviews.decision inválido")
        if not (1 <= len(str(row["evidence"])) <= 2000):
            raise ValueError("license_reviews.evidence fora do limite 1..2000")
        if row["reviewed_by"] != "human":
            raise ValueError("license_reviews.reviewed_by inválido")
    elif table == "transmissions":
        if row["kind"] not in {"submit", "outcome", "gate_refusal", "probe"}:
            raise ValueError("transmissions.kind inválido")
        if row["result"] not in {
            "attempted", "sent", "error", "unknown", "refused_gate", "completed",
            "failed", "cancelled", "interrupted", "invalid_output", "policy_violation",
        }:
            raise ValueError("transmissions.result inválido")
        if row["envelope_bytes"] is not None and row["envelope_bytes"] < 0:
            raise ValueError("transmissions.envelope_bytes negativo")
        if row["envelope_sha256"] is not None and len(str(row["envelope_sha256"])) != 64:
            raise ValueError("transmissions.envelope_sha256 deve ter 64 chars")
        if row["detail"] is not None and len(str(row["detail"])) > 120:
            raise ValueError("transmissions.detail acima de 120 chars")
        if row["tokens_total"] is not None and row["tokens_total"] < 0:
            raise ValueError("transmissions.tokens_total negativo")
    elif table == "human_checkpoints":
        if row["action"] not in {"hermes_run", "hermes_script", "paid_narration", "publish"}:
            raise ValueError("human_checkpoints.action inválido")
        if len(str(row["subject"])) != 64:
            raise ValueError("human_checkpoints.subject deve ter 64 chars")
        if not (1 <= len(str(row["description"])) <= 200):
            raise ValueError("human_checkpoints.description fora do limite 1..200")
        if row["approved_by"] != "human":
            raise ValueError("human_checkpoints.approved_by inválido")
        if row["currency"] is not None and len(str(row["currency"])) != 3:
            raise ValueError("human_checkpoints.currency deve ter 3 chars")
        if (row["limit_amount"] is None) != (row["currency"] is None):
            raise ValueError("human_checkpoints limit_amount/currency inconsistentes")
        if row["expires_at"] <= row["approved_at"]:
            raise ValueError("human_checkpoints expires_at <= approved_at")
        if row["consumed_ref"] is not None and not (1 <= len(str(row["consumed_ref"])) <= 120):
            raise ValueError("human_checkpoints.consumed_ref fora do limite 1..120")
        if (row["consumed_at"] is None) != (row["consumed_ref"] is None):
            raise ValueError("human_checkpoints consumed_at/consumed_ref inconsistentes")
    elif table == "radar_datasets":
        if row["revision"] <= 0:
            raise ValueError("radar_datasets.revision deve ser > 0")


def _canonical_value(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, datetime):
        dt = val.astimezone(timezone.utc) if val.tzinfo else val.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="microseconds")
    if isinstance(val, Decimal):
        norm = val.normalize()
        txt = format(norm, "f")
        if "." in txt:
            txt = txt.rstrip("0").rstrip(".") or "0"
        return txt
    if isinstance(val, (bytes, bytearray, memoryview)):
        return {"__bytes__": bytes(val).hex()}
    if isinstance(val, (dict, list)):
        return json.loads(json.dumps(val, sort_keys=True, ensure_ascii=False))
    return val


def canonical_row_digest(spec: TableSpec, row: dict[str, Any]) -> str:
    """Hash SHA-256 determinístico de uma linha usando o mesmo mapeamento canônico."""
    payload = [
        [col, _canonical_value(row[col])]
        for col in spec.pg_cols
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def table_digest(row_digests: list[str]) -> str:
    joined = "\n".join(row_digests).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()
