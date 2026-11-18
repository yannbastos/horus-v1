"""Carga transacional, reseed, verificação e ledger horus.migration_runs no PostgreSQL."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from horus_backend.database.transport import ConnectFailure, connection_type

from .guards import MigrationGuardError, supabase_target, validate_target_dsn
from .mapping import (
    EXPECTED_PG_SCHEMA_VERSION,
    LOAD_ORDER,
    MAPPING_VERSION,
    TABLE_SPECS,
)
from .sqlite_reader import compute_table_stats


ADVISORY_NS = 1213157973
ADVISORY_KEY = 164


class MigrationVerificationError(RuntimeError):
    """Erro de verificação pós-carga ou integridade; exige rollback total."""

    def __init__(self, message: str, *, inconsistencies: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.inconsistencies = inconsistencies or []


def _connect_target(dsn: str, *, target_mode: str, user_authorized: bool):
    if target_mode == "loopback":
        validate_target_dsn(dsn)
        return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    if target_mode != "supabase":
        raise MigrationGuardError("Modo de destino inválido.")
    config = supabase_target(dsn, user_authorized=user_authorized)
    try:
        conn = connection_type(config).connect()
    except ConnectFailure as exc:
        raise MigrationGuardError(f"Conexão Supabase recusada ({exc.code}).") from None
    conn.row_factory = dict_row
    return conn


def _ensure_supabase_role(conn: psycopg.Connection[Any], *, apply: bool = False) -> None:
    role = conn.execute("SELECT current_user AS role").fetchone()["role"]
    if role != "horus_app":
        raise MigrationGuardError("O destino Supabase exige uma sessão como horus_app.")
    if apply and not conn.execute(
        "SELECT has_table_privilege('horus.migration_runs', 'INSERT') AS can_record"
    ).fetchone()["can_record"]:
        raise MigrationGuardError("Carga Supabase recusada: horus_app não pode gravar migration_runs.")


def check_migration_run(
    dsn: str, run_id: str, *, target_mode: str = "loopback", user_authorized: bool = False
) -> dict[str, Any] | None:
    """Consulta horus.migration_runs para recuperação após COMMIT ambíguo."""
    parsed_id = str(uuid.UUID(run_id))
    with _connect_target(dsn, target_mode=target_mode, user_authorized=user_authorized) as conn:
        if target_mode == "supabase":
            _ensure_supabase_role(conn)
        row = conn.execute(
            "SELECT run_id::text AS run_id, source_sha256, mapping_version, "
            "source_schema_version, table_checks, applied_at "
            "FROM horus.migration_runs WHERE run_id = %s::uuid",
            (parsed_id,),
        ).fetchone()
        return dict(row) if row else None


def _ensure_target_ready(
    conn: psycopg.Connection[Any],
    run_id: uuid.UUID,
    source_sha256: str,
) -> dict[str, Any] | None:
    schema_row = conn.execute(
        "SELECT 1 FROM horus.schema_migrations WHERE version = %s",
        (EXPECTED_PG_SCHEMA_VERSION,),
    ).fetchone()
    if not schema_row:
        raise MigrationGuardError(
            f"Schema PostgreSQL incompatível: migration {EXPECTED_PG_SCHEMA_VERSION} ausente."
        )

    existing_same = conn.execute(
        "SELECT run_id::text AS run_id, source_sha256, mapping_version, source_schema_version, applied_at "
        "FROM horus.migration_runs WHERE run_id = %s::uuid",
        (str(run_id),),
    ).fetchone()
    if existing_same:
        if existing_same["source_sha256"] != source_sha256:
            raise MigrationGuardError(
                "run_id já registrado no destino com source_sha256 diferente."
            )
        return dict(existing_same)

    any_run = conn.execute("SELECT 1 FROM horus.migration_runs LIMIT 1").fetchone()
    if any_run:
        raise MigrationGuardError(
            "Destino já possui migração anterior registrada; use um banco descartável novo."
        )

    coordination_tables = (
        "worker_instances",
        "review_comments",
        "review_comment_events",
        "review_comment_receipts",
        "notification_state",
        "preferences",
        "deletion_events",
        "imports",
        "publishing_receipts",
    )
    for tbl in (*LOAD_ORDER, *coordination_tables):
        count = conn.execute(
            sql.SQL("SELECT COUNT(*) AS c FROM horus.{}").format(sql.Identifier(tbl))
        ).fetchone()["c"]
        if count > 0:
            raise MigrationGuardError(
                f"Destino não está vazio (tabela horus.{tbl} contém {count} linha(s))."
            )
    return None


def _insert_table_rows(
    conn: psycopg.Connection[Any],
    table: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    spec = TABLE_SPECS[table]
    cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in spec.pg_cols)
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in spec.pg_cols)
    override = sql.SQL("OVERRIDING SYSTEM VALUE ") if spec.identity_col else sql.SQL("")
    stmt = sql.SQL("INSERT INTO horus.{} ({}) {}VALUES ({})").format(
        sql.Identifier(table), cols_sql, override, placeholders
    )

    json_set = set(spec.json_cols)
    canonical_set = {c for _, c in spec.canonical_map}
    prepared_batch: list[tuple[Any, ...]] = []
    for row in rows:
        vals: list[Any] = []
        for col in spec.pg_cols:
            v = row[col]
            if table == "workflows" and col == "current_artifact_id":
                v = None
            elif col in json_set and v is not None:
                v = Jsonb(v)
            elif col in canonical_set and v is not None:
                v = bytes(v)
            vals.append(v)
        prepared_batch.append(tuple(vals))

    with conn.cursor() as cur:
        cur.executemany(stmt, prepared_batch)


def _reseed_identity(conn: psycopg.Connection[Any], table: str, identity_col: str) -> None:
    row = conn.execute(
        sql.SQL("SELECT MAX({}) AS m, COUNT(*) AS c FROM horus.{}").format(
            sql.Identifier(identity_col), sql.Identifier(table)
        )
    ).fetchone()
    if row["c"] > 0 and row["m"] is not None:
        conn.execute(
            "SELECT setval(pg_get_serial_sequence(%s, %s), %s, true)",
            (f"horus.{table}", identity_col, int(row["m"])),
        )


def _verify_pg_against_sqlite(
    conn: psycopg.Connection[Any],
    sqlite_stats: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    pg_stats: dict[str, dict[str, Any]] = {}
    inconsistencies: list[dict[str, Any]] = []

    for tbl in LOAD_ORDER:
        spec = TABLE_SPECS[tbl]
        cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in spec.pg_cols)
        order_sql = sql.SQL(", ").join(sql.Identifier(pk) for pk in spec.pk_cols)
        pg_rows = conn.execute(
            sql.SQL("SELECT {} FROM horus.{} ORDER BY {}").format(
                cols_sql, sql.Identifier(tbl), order_sql
            )
        ).fetchall()
        stats = compute_table_stats(tbl, [dict(r) for r in pg_rows])
        pg_stats[tbl] = stats

        expected = sqlite_stats[tbl]
        for key in ("count", "sha256", "null_counts", "min_id", "max_id", "min_seq", "max_seq"):
            if stats[key] != expected[key]:
                inconsistencies.append(
                    {
                        "table": tbl,
                        "check": key,
                        "expected": expected[key],
                        "actual": stats[key],
                    }
                )

    orphan_queries = (
        ("jobs_workflow", "SELECT COUNT(*) AS c FROM horus.jobs j WHERE j.workflow_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM horus.workflows w WHERE w.id = j.workflow_id)"),
        ("jobs_depends_on", "SELECT COUNT(*) AS c FROM horus.jobs j WHERE j.depends_on IS NOT NULL AND NOT EXISTS (SELECT 1 FROM horus.jobs p WHERE p.id = j.depends_on)"),
        ("workflows_current_artifact", "SELECT COUNT(*) AS c FROM horus.workflows w WHERE w.current_artifact_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM horus.artifacts a WHERE a.id = w.current_artifact_id)"),
        ("events_job", "SELECT COUNT(*) AS c FROM horus.events e WHERE NOT EXISTS (SELECT 1 FROM horus.jobs j WHERE j.id = e.job_id)"),
        ("license_reviews_supersedes", "SELECT COUNT(*) AS c FROM horus.license_reviews lr WHERE lr.supersedes IS NOT NULL AND NOT EXISTS (SELECT 1 FROM horus.license_reviews p WHERE p.id = lr.supersedes)"),
    )
    for check_name, query in orphan_queries:
        orphan_count = conn.execute(query).fetchone()["c"]
        if orphan_count:
            inconsistencies.append({"check": f"orphan_{check_name}", "count": orphan_count})

    return pg_stats, inconsistencies


def execute_pg_load(
    dsn: str,
    data: dict[str, list[dict[str, Any]]],
    sqlite_stats: dict[str, dict[str, Any]],
    *,
    source_sha256: str,
    source_schema_version: int,
    run_id: uuid.UUID,
    apply: bool = False,
    inject_failure_after: str | None = None,
    target_mode: str = "loopback",
    user_authorized: bool = False,
) -> dict[str, Any]:
    """Valida destino e executa dry-run ou carga transacional única no PostgreSQL."""
    with _connect_target(dsn, target_mode=target_mode, user_authorized=user_authorized) as conn:
        with conn.transaction():
            if target_mode == "supabase":
                _ensure_supabase_role(conn, apply=apply)
            conn.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                (ADVISORY_NS, ADVISORY_KEY),
            )
            already = _ensure_target_ready(conn, run_id, source_sha256)
            if already is not None:
                return {
                    "status": "already_applied",
                    "run_id": already["run_id"],
                    "source_sha256": already["source_sha256"],
                    "mapping_version": already["mapping_version"],
                    "source_schema_version": already["source_schema_version"],
                    "applied_at": already["applied_at"].astimezone(timezone.utc).isoformat(),
                    "tables": sqlite_stats,
                    "inconsistencies": [],
                }

            if not apply:
                return {
                    "status": "dry_run_ok",
                    "run_id": str(run_id),
                    "source_sha256": source_sha256,
                    "mapping_version": MAPPING_VERSION,
                    "source_schema_version": source_schema_version,
                    "tables": sqlite_stats,
                    "inconsistencies": [],
                }

            conn.execute("SET CONSTRAINTS ALL DEFERRED")

            for tbl in LOAD_ORDER:
                _insert_table_rows(conn, tbl, data.get(tbl, []))
                spec = TABLE_SPECS[tbl]
                if spec.identity_col and data.get(tbl):
                    _reseed_identity(conn, tbl, spec.identity_col)
                if inject_failure_after and inject_failure_after == tbl:
                    raise RuntimeError(f"Falha injetada após tabela {tbl} para ensaio de rollback.")

            # Atualiza current_artifact_id após artifacts carregados.
            wf_updates = [
                (w["current_artifact_id"], w["id"])
                for w in data.get("workflows", [])
                if w.get("current_artifact_id") is not None
            ]
            if wf_updates:
                with conn.cursor() as cur:
                    cur.executemany(
                        "UPDATE horus.workflows SET current_artifact_id = %s WHERE id = %s",
                        wf_updates,
                    )

            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

            pg_stats, inconsistencies = _verify_pg_against_sqlite(conn, sqlite_stats)
            if inconsistencies:
                raise MigrationVerificationError(
                    f"Verificação pós-carga falhou ({len(inconsistencies)} inconsistência(s)).",
                    inconsistencies=inconsistencies,
                )

            applied_at = datetime.now(timezone.utc)
            conn.execute(
                "INSERT INTO horus.migration_runs "
                "(run_id, source_sha256, mapping_version, source_schema_version, table_checks, applied_at) "
                "VALUES (%s::uuid, %s, %s, %s, %s, %s)",
                (
                    str(run_id),
                    source_sha256,
                    MAPPING_VERSION,
                    source_schema_version,
                    Jsonb(pg_stats),
                    applied_at,
                ),
            )

        return {
            "status": "succeeded",
            "run_id": str(run_id),
            "source_sha256": source_sha256,
            "mapping_version": MAPPING_VERSION,
            "source_schema_version": source_schema_version,
            "applied_at": applied_at.isoformat(),
            "tables": pg_stats,
            "inconsistencies": [],
        }
