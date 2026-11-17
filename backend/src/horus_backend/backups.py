"""Inventário, verificação e restauração isolada de backups PostgreSQL (HOR-164 / I8).

Substitui o legado SQLite: não importa `sqlite3`, não abre arquivos `.db`/`.sqlite3`
e não faz fallback local. A inspeção operacional usa `Store.uow(readonly=True)`
com o papel `horus_app` (respeitando RLS). A restauração lógica é permitida
apenas em banco/instância PostgreSQL isolado e vazio, nunca sobrepondo o banco
operacional remoto pela UI.

Política de retenção de pacotes locais de backup (a ordem importa):
1. versão do backup maior que o schema atual: mantido;
2. backup mais recente (entre versões conhecidas) ou único: mantido sempre;
3. sem prazo configurado (`HORUS_BACKUP_RETENTION_DAYS`): mantido (remoção desligada);
4. mais novo que o prazo: mantido; do contrário: candidato à remoção.
Backups contendo registros não redigidos do Radar exigem decisão do operador.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
from uuid import UUID
import zipfile

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database.expected_schema import EXPECTED_SCHEMA_VERSION
from .storage import DatabaseUnavailable, Store

ENV_RETENTION_DAYS = "HORUS_BACKUP_RETENTION_DAYS"
FORMAT_VERSION = 2
FORMAT_TAG = "horus-pg-export-v2"
BACKUP_NAME_PATTERN = re.compile(
    r"^(?:horus-pg-backup-v(\d+)-[0-9a-f]{32}|(?:pre-update|pre-exclusao)-.+)\.zip$"
)
log = logging.getLogger(__name__)


@dataclass
class BackupEntry:
    name: str
    version: int
    size: int
    age_days: float
    radar_workflows: int | None
    radar_unredacted: int | None
    action: str = "keep"  # keep | remove
    reason: str = ""
    path: Path = field(default=None, repr=False)

    def as_dict(self) -> dict:
        return {
            key: getattr(self, key)
            for key in (
                "name",
                "version",
                "size",
                "age_days",
                "radar_workflows",
                "radar_unredacted",
                "action",
                "reason",
            )
        }


@dataclass
class BackupReport:
    retention_days: int | None
    dry_run: bool
    entries: list[BackupEntry] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    @property
    def candidates(self) -> list[BackupEntry]:
        return [entry for entry in self.entries if entry.action == "remove"]

    @property
    def needs_operator_decision(self) -> list[str]:
        """Mantidos, mas com Radar não redigido: só o operador decide se e quando removê-los."""
        return [e.name for e in self.entries if e.action == "keep" and e.radar_unredacted]


def retention_days_from_env(environ=None) -> int | None:
    """Inteiro >= 1 ou None (desligado). Valor inválido também desliga: na dúvida, nada é removido."""
    value = (environ if environ is not None else os.environ).get(ENV_RETENTION_DAYS, "").strip()
    if not value:
        return None
    if not value.isdecimal() or int(value) < 1:
        log.warning("%s inválida; remoção de backups permanece desligada.", ENV_RETENTION_DAYS)
        return None
    return int(value)


def _encode_pg_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return {"__pg_type__": "timestamptz", "value": dt.astimezone(timezone.utc).isoformat()}
    if isinstance(value, UUID):
        return {"__pg_type__": "uuid", "value": str(value)}
    if isinstance(value, Decimal):
        return {"__pg_type__": "numeric", "value": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__pg_type__": "bytea", "value": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (dict, list)):
        return value
    return str(value)


def _decode_pg_value(value, data_type: str):
    if isinstance(value, dict) and "__pg_type__" in value:
        kind = value["__pg_type__"]
        raw = value["value"]
        if kind == "timestamptz":
            return datetime.fromisoformat(raw)
        if kind == "uuid":
            return UUID(raw)
        if kind == "numeric":
            return Decimal(raw)
        if kind == "bytea":
            return base64.b64decode(raw.encode("ascii"))
        return raw
    if data_type == "jsonb" and value is not None:
        return Jsonb(value)
    return value


def serialize_row(row: dict) -> bytes:
    """Serialização JSONL canônica UTF-8 para cálculo determinístico de hash por tabela."""
    encoded = {k: _encode_pg_value(v) for k, v in row.items()}
    return (json.dumps(encoded, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _schema_version_int(versions: list[str]) -> int:
    if not versions:
        return 0
    latest = versions[-1]
    digits = "".join(ch for ch in latest if ch.isdigit())
    return int(digits) if digits else len(versions)


def inspect_database(store: Store | None) -> dict:
    """Inspeciona o banco PostgreSQL operacional em modo somente leitura (papel `horus_app`)."""
    if store is None:
        raise DatabaseUnavailable("database_unavailable")
    with store.uow(readonly=True) as conn:
        identity_row = conn.execute(
            "SELECT instance_id::text AS instance_id FROM horus.database_identity WHERE singleton = true"
        ).fetchone()
        instance_id = identity_row[0] if identity_row else None
        migration_rows = conn.execute(
            "SELECT version FROM horus.schema_migrations ORDER BY version ASC"
        ).fetchall()
        schema_versions = [row[0] for row in migration_rows]
        schema_version = schema_versions[-1] if schema_versions else None
        table_rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'horus' ORDER BY tablename"
        ).fetchall()
        tables: dict[str, int] = {}
        for (table,) in table_rows:
            count = conn.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier("horus", table))).fetchone()[0]
            tables[table] = int(count)
    return {
        "ok": True,
        "instance_id": instance_id,
        "schema_version": schema_version,
        "schema_versions": schema_versions,
        "tables": tables,
    }


def _radar_counts_from_zip(path: Path) -> tuple[int | None, int | None, int]:
    """Lê contagens do Radar e versão de schema a partir de pacote lógico v2."""
    try:
        with zipfile.ZipFile(path) as archive:
            if "manifest.json" not in archive.namelist():
                return None, None, 0
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("schema_version") != FORMAT_VERSION:
                return None, None, 0
            db_meta = manifest.get("database") or {}
            ver_str = str(db_meta.get("schema_version") or "")
            ver_num = int("".join(c for c in ver_str if c.isdigit()) or 0)
            if "pg_dump/workflows.jsonl" not in archive.namelist():
                return 0, 0, ver_num
            total = 0
            unredacted = 0
            with archive.open("pg_dump/workflows.jsonl") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    row = json.loads(line.decode("utf-8"))
                    payload = row.get("payload") or {}
                    radar = payload.get("radar") if isinstance(payload, dict) else None
                    if radar is not None:
                        total += 1
                        if not isinstance(radar, dict) or radar.get("redacted_at") is None:
                            unredacted += 1
            return total, unredacted, ver_num
    except (OSError, zipfile.BadZipFile, ValueError, KeyError, UnicodeError):
        return None, None, 0


def inventory(backup_dir: Path, *, clock=None) -> list[BackupEntry]:
    """Lista pacotes de backup lógico na pasta informada, do mais novo ao mais antigo. Nunca escreve."""
    backup_dir = Path(backup_dir)
    now = (clock or (lambda: datetime.now(timezone.utc)))().timestamp()
    entries: list[BackupEntry] = []
    if not backup_dir.is_dir():
        return entries
    for path in backup_dir.iterdir():
        match = BACKUP_NAME_PATTERN.match(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        total, unredacted, ver_num = _radar_counts_from_zip(path)
        version = int(match.group(1)) if match.group(1) else ver_num
        age = round(max(now - stat.st_mtime, 0) / 86400, 2)
        entries.append(BackupEntry(path.name, version, stat.st_size, age, total, unredacted, path=path))
    return sorted(entries, key=lambda e: (e.path.stat().st_mtime_ns, e.name), reverse=True)


def plan(
    backup_dir: Path,
    *,
    retention_days: int | None,
    clock=None,
    current_version: int | None = None,
) -> BackupReport:
    if current_version is None:
        digits = "".join(c for c in EXPECTED_SCHEMA_VERSION if c.isdigit())
        current_version = int(digits) if digits else 0
    report = BackupReport(retention_days, dry_run=True, entries=inventory(backup_dir, clock=clock))
    newest = next((e for e in report.entries if e.version <= current_version), None)
    for entry in report.entries:
        if entry.version > current_version:
            entry.reason = f"versão v{entry.version} maior que o schema atual (v{current_version}); nunca removido"
        elif entry is newest:
            entry.reason = "único backup existente" if len(report.entries) == 1 else "backup mais recente"
        elif retention_days is None:
            entry.reason = f"remoção desligada ({ENV_RETENTION_DAYS} não definida)"
        elif entry.age_days < retention_days:
            entry.reason = f"dentro do prazo de {retention_days} dias"
        else:
            entry.action, entry.reason = "remove", f"mais antigo que {retention_days} dias e não é o backup mais recente"
    return report


def remove_backups(
    backup_dir: Path,
    *,
    retention_days: int | None,
    dry_run: bool = True,
    clock=None,
    current_version: int | None = None,
) -> BackupReport:
    """Aplica a política de retenção aos pacotes locais. Sem `retention_days` ou com `dry_run=True`, nada é apagado."""
    report = plan(backup_dir, retention_days=retention_days, clock=clock, current_version=current_version)
    report.dry_run = dry_run or retention_days is None
    if report.dry_run:
        return report
    for entry in report.candidates:
        try:
            if entry.path.exists() or entry.path.is_symlink():
                entry.path.unlink()
            report.removed.append(entry.name)
        except OSError as error:
            report.errors.append({"backup": entry.name, "error": type(error).__name__})
    return report


def restore_to_isolated_pg(
    package: Path,
    target_conn,
    *,
    expected_schema_version: str | None = EXPECTED_SCHEMA_VERSION,
) -> dict:
    """Restaura o dump lógico PG de um pacote verificado para um banco PostgreSQL ISOLADO e vazio.

    Nunca sobrescreve um banco operacional não vazio. Exige conexão com permissão de escrita/owner
    no banco isolado de destino (ex.: `horus_owner` ou migrador externo em banco vazio).
    """
    from .local_data_export import verify_export

    package = Path(package)
    checked = verify_export(package)
    if not checked["ok"]:
        raise ValueError("Pacote não verificado; restauração recusada: " + "; ".join(checked["errors"][:3]))

    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        db_meta = manifest.get("database") or {}
        backup_schema = db_meta.get("schema_version")
        if expected_schema_version and backup_schema and str(backup_schema) > str(expected_schema_version):
            raise ValueError(
                f"O pacote vem de versão de schema mais nova ({backup_schema} > {expected_schema_version}); restauração recusada."
            )
        declared_tables = db_meta.get("tables") or {}

        with target_conn.transaction():
            target_tables = [
                row[0]
                for row in target_conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'horus' ORDER BY tablename"
                ).fetchall()
            ]
            # Recusa se qualquer tabela operacional já contiver linhas.
            protected_empty = [t for t in target_tables if t not in {"schema_migrations", "database_identity"}]
            for table in protected_empty:
                count = target_conn.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier("horus", table))
                ).fetchone()[0]
                if count > 0:
                    raise ValueError(
                        f"Banco de destino não está vazio (tabela horus.{table} contém {count} linha(s)); "
                        "restauração permitida somente em banco isolado e vazio."
                    )

            target_conn.execute("SET CONSTRAINTS ALL DEFERRED")

            # Verifica compatibilidade de schema_migrations no destino, se existir.
            if target_tables:
                cols_info = target_conn.execute(
                    """
                    SELECT table_name, column_name, data_type, is_identity
                    FROM information_schema.columns
                    WHERE table_schema = 'horus' AND is_generated = 'NEVER'
                    ORDER BY table_name, ordinal_position
                    """
                ).fetchall()
            else:
                cols_info = []

            table_columns: dict[str, list[tuple[str, str, bool]]] = {}
            for tname, cname, dtype, is_ident in cols_info:
                table_columns.setdefault(tname, []).append((cname, dtype, is_ident == "YES"))

            # Desabilita triggers de usuário temporariamente no banco isolado vazio para restaurar
            # histórico append-only/encadeado exatamente como exportado.
            for table in target_tables:
                if table in {"schema_migrations", "database_identity"}:
                    continue
                target_conn.execute(
                    sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(sql.Identifier("horus", table))
                )

            restored_counts: dict[str, int] = {}
            for table, meta in declared_tables.items():
                if table in {"schema_migrations", "database_identity"}:
                    continue
                entry_name = f"pg_dump/{table}.jsonl"
                if entry_name not in archive.namelist():
                    raise ValueError(f"Tabela ausente no pacote: {table}")
                cols = table_columns.get(table, [])
                if not cols:
                    raise ValueError(f"Tabela inexistente no banco de destino: horus.{table}")
                col_names = [c[0] for c in cols]
                col_types = {c[0]: c[1] for c in cols}
                has_identity = any(c[2] for c in cols)
                col_idents = sql.SQL(", ").join(sql.Identifier(c) for c in col_names)
                placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in col_names)
                override = sql.SQL(" OVERRIDING SYSTEM VALUE") if has_identity else sql.SQL("")
                stmt = sql.SQL("INSERT INTO {} ({}){} VALUES ({})").format(
                    sql.Identifier("horus", table),
                    col_idents,
                    override,
                    placeholders,
                )
                count = 0
                digest = hashlib.sha256()
                with archive.open(entry_name) as stream:
                    for raw_line in stream:
                        digest.update(raw_line)
                        if not raw_line.strip():
                            continue
                        row = json.loads(raw_line.decode("utf-8"))
                        values = [_decode_pg_value(row.get(c), col_types[c]) for c in col_names]
                        target_conn.execute(stmt, values)
                        count += 1
                if count != meta["rows"] or digest.hexdigest() != meta["sha256"]:
                    raise ValueError(f"Divergência de contagem/hash ao restaurar horus.{table}")
                restored_counts[table] = count

            # Sincroniza sequences após restauração com identity.
            for (seq_name,) in target_conn.execute(
                "SELECT sequencename FROM pg_sequences WHERE schemaname = 'horus'"
            ).fetchall():
                owner_row = target_conn.execute(
                    """
                    SELECT c.relname, a.attname
                    FROM pg_class s
                    JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'
                    JOIN pg_class c ON c.oid = d.refobjid
                    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.refobjsubid
                    JOIN pg_namespace n ON n.oid = s.relnamespace
                    WHERE n.nspname = 'horus' AND s.relname = %s
                    """,
                    (seq_name,),
                ).fetchone()
                if owner_row:
                    max_val = target_conn.execute(
                        sql.SQL("SELECT MAX({}) FROM {}").format(
                            sql.Identifier(owner_row[1]),
                            sql.Identifier("horus", owner_row[0]),
                        )
                    ).fetchone()[0]
                    if max_val is not None:
                        target_conn.execute(
                            "SELECT setval(%s::regclass, %s, true)",
                            (f"horus.{seq_name}", int(max_val)),
                        )

            target_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            for table in target_tables:
                if table in {"schema_migrations", "database_identity"}:
                    continue
                target_conn.execute(
                    sql.SQL("ALTER TABLE {} ENABLE TRIGGER USER").format(sql.Identifier("horus", table))
                )

    return {
        "ok": True,
        "restored_tables": restored_counts,
        "schema_version": backup_schema,
        "instruction": (
            "Restauração concluída em banco PostgreSQL isolado e vazio. "
            "Nunca sobreponha o banco operacional remoto pela interface; confira contagens e hashes antes de qualquer corte."
        ),
    }


def main(argv: list[str]) -> int:
    """`python -m horus_backend.backups <pasta-backups> [--apply]`: lista (dry-run); remove só com `--apply` e `HORUS_BACKUP_RETENTION_DAYS`."""
    args = [arg for arg in argv if arg != "--apply"]
    if len(args) != 1:
        print("uso: python -m horus_backend.backups <pasta-de-backups> [--apply]", file=sys.stderr)
        return 2
    days = retention_days_from_env()
    apply = "--apply" in argv
    if apply and days is None:
        print(f"--apply ignorado: defina {ENV_RETENTION_DAYS} (inteiro >= 1).", file=sys.stderr)
    report = remove_backups(Path(args[0]), retention_days=days, dry_run=not apply)
    print(f"retention_days={days} dry_run={report.dry_run}")
    for entry in report.entries:
        radar = "?" if entry.radar_workflows is None else f"{entry.radar_workflows}/{entry.radar_unredacted}"
        print(
            f"{entry.action:6} {entry.name} v{entry.version} {entry.size}B {entry.age_days}d "
            f"radar(total/não redigido)={radar} - {entry.reason}"
        )
    for name in report.removed:
        print(f"removido {name}")
    for error in report.errors:
        print(f"erro {error['backup']}: {error['error']}", file=sys.stderr)
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
