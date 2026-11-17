"""Leitura, backup imutável e validação de origem SQLite (v9-v12 e Radar)."""
from __future__ import annotations

from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from .guards import validate_cold_sqlite, validate_source_path
from .mapping import (
    RADAR_TABLES,
    TABLE_SPECS,
    canonical_row_digest,
    expected_core_tables,
    parse_finite_decimal,
    parse_json_canonical,
    parse_utc_timestamp,
    table_digest,
)


class SqliteSourceError(ValueError):
    """Erro de validação, mapeamento ou consistência na cópia SQLite."""

    def __init__(self, message: str, *, rejected: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.rejected = rejected or []


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def backup_and_inspect_sqlite(
    source_db: Path,
    backup_dir: Path,
    *,
    label: str = "horus",
) -> tuple[Path, str, int]:
    """Cria backup imutável via SQLite backup API e valida integridade/FK/versão."""
    source_db = validate_source_path(source_db)
    validate_cold_sqlite(source_db)
    source_hash = sha256_file(source_db)

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{label}.immutable-{uuid.uuid4().hex}.sqlite3"

    with closing(sqlite3.connect(f"{source_db.as_uri()}?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(backup_path)) as dst:
            src.backup(dst)

    with closing(sqlite3.connect(f"{backup_path.as_uri()}?mode=ro", uri=True)) as chk:
        integrity = chk.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise SqliteSourceError(f"integrity_check falhou em {label}")
        fk_errors = chk.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise SqliteSourceError(
                f"foreign_key_check falhou em {label} ({len(fk_errors)} violação(ões))"
            )
        user_version = int(chk.execute("PRAGMA user_version").fetchone()[0])

    return backup_path, source_hash, user_version


def _topological_sort(rows: list[dict[str, Any]], id_key: str, parent_key: str) -> list[dict[str, Any]]:
    by_id = {row[id_key]: row for row in rows}
    visited: dict[str, int] = {}  # 0=unvisited, 1=visiting, 2=done
    ordered: list[dict[str, Any]] = []

    def visit(item_id: str) -> None:
        state = visited.get(item_id, 0)
        if state == 2:
            return
        if state == 1:
            raise SqliteSourceError(f"Ciclo detectado em {parent_key} (id={item_id})")
        visited[item_id] = 1
        row = by_id[item_id]
        parent_id = row.get(parent_key)
        if parent_id is not None:
            if parent_id not in by_id:
                raise SqliteSourceError(f"Órfão detectado em {parent_key} (id={item_id})")
            visit(parent_id)
        visited[item_id] = 2
        ordered.append(row)

    for row in rows:
        visit(row[id_key])
    return ordered


def _convert_row(table: str, raw: dict[str, Any], user_version: int) -> dict[str, Any]:
    spec = TABLE_SPECS[table]
    out: dict[str, Any] = {}
    canonical_pairs = dict(spec.canonical_map)

    if table == "scripts" and user_version < 10 and "generator" not in raw:
        raw = {**raw, "generator": None}

    for col in spec.pg_cols:
        # Colunas *_canonical são preenchidas junto da coluna JSON correspondente.
        if col in canonical_pairs.values():
            continue
        if table == "radar_datasets" and col == "collected_at" and "collected_at" not in raw:
            continue

        val = raw.get(col)
        if col in spec.json_cols:
            allow_null = col in spec.nullable_cols
            parsed, raw_bytes = parse_json_canonical(val, allow_null=allow_null)
            out[col] = parsed
            if col in canonical_pairs:
                out[canonical_pairs[col]] = raw_bytes
        elif col in spec.ts_cols:
            if val is None and col in spec.nullable_cols:
                out[col] = None
            else:
                out[col] = parse_utc_timestamp(val)
        elif col in spec.epoch_ts_cols:
            if val is None and col in spec.nullable_cols:
                out[col] = None
            else:
                out[col] = parse_utc_timestamp(float(val))
        elif col in spec.numeric_cols:
            if val is None and col in spec.nullable_cols:
                out[col] = None
            else:
                out[col] = parse_finite_decimal(val)
        else:
            out[col] = val

    if table == "radar_datasets" and "collected_at" not in out:
        payload = out["payload"]
        collected_raw = payload.get("collected_at") if isinstance(payload, dict) else None
        out["collected_at"] = (
            parse_utc_timestamp(collected_raw) if collected_raw is not None else out["expires"]
        )

    return out


def compute_table_stats(table: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    spec = TABLE_SPECS[table]
    ordered = sorted(rows, key=lambda r: tuple(r[pk] for pk in spec.pk_cols))
    row_digests = [canonical_row_digest(spec, r) for r in ordered]
    null_counts = {
        col: sum(1 for r in ordered if r.get(col) is None)
        for col in spec.pg_cols
    }
    stats: dict[str, Any] = {
        "count": len(ordered),
        "sha256": table_digest(row_digests),
        "null_counts": null_counts,
    }
    if spec.id_range_col and ordered:
        ids = [str(r[spec.id_range_col]) for r in ordered]
        stats["min_id"] = min(ids)
        stats["max_id"] = max(ids)
    else:
        stats["min_id"] = None
        stats["max_id"] = None
    if spec.seq_range_col and ordered:
        seqs = [int(r[spec.seq_range_col]) for r in ordered]
        stats["min_seq"] = min(seqs)
        stats["max_seq"] = max(seqs)
    else:
        stats["min_seq"] = None
        stats["max_seq"] = None
    return stats


def read_and_validate_sqlite(
    core_backup: Path,
    user_version: int,
    *,
    radar_backup: Path | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Lê, mapeia e valida todas as tabelas esperadas; rejeita tabelas inesperadas ou linhas inválidas."""
    expected_tables = expected_core_tables(user_version)
    rejected: list[dict[str, Any]] = []
    data: dict[str, list[dict[str, Any]]] = {}

    with closing(sqlite3.connect(f"{core_backup.as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        found_tables = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        unexpected = sorted(found_tables - expected_tables)
        missing = sorted(expected_tables - found_tables)
        if unexpected:
            for tbl in unexpected:
                rejected.append({"table": tbl, "reason": "unexpected_table"})
            raise SqliteSourceError(
                f"Tabela(s) inesperada(s) no SQLite: {', '.join(unexpected)}",
                rejected=rejected,
            )
        if missing:
            for tbl in missing:
                rejected.append({"table": tbl, "reason": "missing_expected_table"})
            raise SqliteSourceError(
                f"Tabela(s) esperada(s) ausente(s) no SQLite: {', '.join(missing)}",
                rejected=rejected,
            )

        for tbl in sorted(expected_tables):
            spec = TABLE_SPECS[tbl]
            pk_order = ", ".join(_quote_ident(c) for c in spec.pk_cols)
            raw_rows = db.execute(
                f"SELECT * FROM {_quote_ident(tbl)} ORDER BY {pk_order}"
            ).fetchall()
            converted: list[dict[str, Any]] = []
            for idx, raw_row in enumerate(raw_rows):
                raw_dict = dict(raw_row)
                pk_ref = {pk: raw_dict.get(pk) for pk in spec.pk_cols}
                try:
                    converted.append(_convert_row(tbl, raw_dict, user_version))
                except Exception as exc:
                    rejected.append(
                        {
                            "table": tbl,
                            "row_index": idx,
                            "pk": pk_ref,
                            "reason": type(exc).__name__,
                        }
                    )
            if tbl == "jobs" and not rejected:
                converted = _topological_sort(converted, "id", "depends_on")
            elif tbl == "license_reviews" and not rejected:
                converted = _topological_sort(converted, "id", "supersedes")
            data[tbl] = converted

    # Garante que tabelas de versões posteriores existam vazias no dicionário unificado.
    for tbl in ("transmissions", "human_checkpoints"):
        data.setdefault(tbl, [])

    if radar_backup is not None:
        with closing(sqlite3.connect(f"{radar_backup.as_uri()}?mode=ro", uri=True)) as rdb:
            rdb.row_factory = sqlite3.Row
            found_radar = {
                r[0]
                for r in rdb.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            unexpected_radar = sorted(found_radar - set(RADAR_TABLES))
            if unexpected_radar:
                for tbl in unexpected_radar:
                    rejected.append({"table": tbl, "reason": "unexpected_radar_table"})
                raise SqliteSourceError(
                    f"Tabela(s) inesperada(s) no Radar SQLite: {', '.join(unexpected_radar)}",
                    rejected=rejected,
                )
            for tbl in RADAR_TABLES:
                if tbl not in found_radar:
                    data[tbl] = []
                    continue
                spec = TABLE_SPECS[tbl]
                pk_order = ", ".join(_quote_ident(c) for c in spec.pk_cols)
                raw_rows = rdb.execute(
                    f"SELECT * FROM {_quote_ident(tbl)} ORDER BY {pk_order}"
                ).fetchall()
                converted_radar: list[dict[str, Any]] = []
                for idx, raw_row in enumerate(raw_rows):
                    raw_dict = dict(raw_row)
                    pk_ref = {pk: raw_dict.get(pk) for pk in spec.pk_cols}
                    try:
                        converted_radar.append(_convert_row(tbl, raw_dict, user_version))
                    except Exception as exc:
                        rejected.append(
                            {
                                "table": tbl,
                                "row_index": idx,
                                "pk": pk_ref,
                                "reason": type(exc).__name__,
                            }
                        )
                data[tbl] = converted_radar
    else:
        for tbl in RADAR_TABLES:
            data[tbl] = []

    if rejected:
        raise SqliteSourceError(
            f"Linhas inconsistentes/rejeitadas na origem ({len(rejected)}).",
            rejected=rejected,
        )

    stats = {tbl: compute_table_stats(tbl, data[tbl]) for tbl in TABLE_SPECS}
    return data, stats, rejected
