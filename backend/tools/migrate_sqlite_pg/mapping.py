"""Mapeamento explícito SQLite v9-v12 -> PostgreSQL horus e normalização canônica."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
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
    elif isinstance(value, (int, str)):
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

    def _reject_nan(token: str) -> Any:
        raise ValueError(f"Constante JSON não finita proibida: {token}")

    parsed = json.loads(text, parse_constant=_reject_nan)
    if not isinstance(parsed, (dict, list)):
        raise ValueError("JSON operacional deve ser object ou array")
    return parsed, raw_bytes


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
