"""Exportação dos dados do usuário como pacote verificável PostgreSQL v2 (HOR-164 / I8).

Substitui o legado SQLite: não importa `sqlite3` nem cria `.sqlite3`.
Conteúdo do pacote (`schema_version=2`, `format="horus-pg-export-v2"`):
1. Dump lógico PostgreSQL (`pg_dump/<tabela>.jsonl`) lido em transação `REPEATABLE READ`
   somente leitura com o papel `horus_app` (respeitando RLS, sem `postgres`/`service_role`),
   com contagem de linhas e SHA-256 determinístico por tabela em `manifest.json`.
2. Arquivos locais permitidos separados em `local_files/productions/<id>/*.json`.
   Nunca entram: `integrations/`, `models/`, `exports/`, `.quarentena/`, `update-backups/`,
   credenciais (`SECRET_SHAPE`), links simbólicos ou junções.

Restauração:
- `restore_local_data` devolve `local_files/` a uma pasta de dados **nova ou vazia**
  (staging irmão + `os.rename`), nunca sobrescrevendo nada.
- Se `target_conn` for fornecida, restaura o dump lógico exclusivamente em um banco
  PostgreSQL **isolado e vazio** (via `restore_to_isolated_pg`), nunca sobrepondo o
  banco operacional remoto pela UI.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile

from psycopg import sql
from psycopg.rows import dict_row

from .backups import FORMAT_TAG, FORMAT_VERSION, restore_to_isolated_pg, serialize_row
from .database.expected_schema import EXPECTED_SCHEMA_VERSION
from .storage import DatabaseUnavailable, Store

SCHEMA = FORMAT_VERSION
MAX_MANIFEST_FILE = 5 * 1024 * 1024
SECRET_SHAPE = re.compile(
    r"\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36,}|\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}|-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"
    r"|\bBearer\s+[A-Za-z0-9._~+/-]{24,}|(?i:\b(?:api[_-]?key|secret|token|password|senha)\b[\"']?\s*[:=]\s*[\"'][A-Za-z0-9/+_=-]{20,}[\"'])"
)
SAFE_NAME = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,100}/)*[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
MAX_ENTRIES = 20000
ALLOWED_NAME = re.compile(
    r"^(?:pg_dump/[a-z][a-z0-9_]{0,62}\.jsonl"
    r"|local_files/productions/[A-Za-z0-9_-]{1,64}/[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.json)$"
)
EXCLUDED_AREAS = ("integrations/", "models/", "exports/", ".quarentena/", "update-backups/")


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _restore_relative(name: str) -> str | None:
    if name.startswith("local_files/"):
        return name[len("local_files/"):]
    return None


def _candidates(data_dir: Path):
    """(nome no pacote, caminho de origem) dos arquivos locais permitidos, separados do dump PG."""
    productions = data_dir / "productions"
    if productions.is_dir() and not _is_link(productions):
        for folder in sorted(p for p in productions.iterdir() if p.is_dir() and not _is_link(p)):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", folder.name):
                continue
            for item in sorted(folder.glob("*.json")):
                if _is_link(item) or not item.is_file():
                    continue
                yield f"local_files/productions/{folder.name}/{item.name}", item


def _primary_key_columns(conn, table: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
        WHERE n.nspname = 'horus' AND c.relname = %s AND i.indisprimary
        ORDER BY k.ord
        """,
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def _dump_pg_tables(store: Store, scratch: Path) -> tuple[dict, list[tuple[str, Path, str]]]:
    """Exporta todas as tabelas de `horus` em snapshot consistente `REPEATABLE READ` via `horus_app`."""
    tables_meta: dict[str, dict] = {}
    staged_pg: list[tuple[str, Path, str]] = []

    with store.uow(readonly=True) as conn:
        conn.execute("SET LOCAL TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        identity_row = conn.execute(
            "SELECT instance_id::text FROM horus.database_identity WHERE singleton = true"
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

        for (table,) in table_rows:
            pk_cols = _primary_key_columns(conn, table)
            if pk_cols:
                order_clause = sql.SQL(" ORDER BY ") + sql.SQL(", ").join(sql.Identifier(c) for c in pk_cols)
            else:
                order_clause = sql.SQL("")
            query = sql.SQL("SELECT * FROM {}{}").format(sql.Identifier("horus", table), order_clause)

            out_path = scratch / f"pg-{table}.jsonl"
            digest = hashlib.sha256()
            row_count = 0
            with conn.cursor(row_factory=dict_row) as cur, out_path.open("wb") as stream:
                cur.execute(query)
                for row in cur:
                    line = serialize_row(row)
                    digest.update(line)
                    stream.write(line)
                    row_count += 1

            table_sha = digest.hexdigest()
            tables_meta[table] = {"rows": row_count, "sha256": table_sha}
            staged_pg.append((f"pg_dump/{table}.jsonl", out_path, "pg_table"))

    db_info = {
        "instance_id": instance_id,
        "schema_version": schema_version,
        "schema_versions": schema_versions,
        "tables": tables_meta,
    }
    return db_info, staged_pg


def export_local_data(
    data_dir: Path,
    destination: Path,
    *,
    store: Store | None = None,
    now: datetime | None = None,
) -> dict:
    """Exporta dump lógico PostgreSQL + arquivos locais permitidos para pacote ZIP verificável."""
    if store is None:
        raise DatabaseUnavailable("database_unavailable")

    data_dir, destination = Path(data_dir), Path(destination)
    if not data_dir.is_dir() or _is_link(data_dir):
        raise ValueError("Pasta de dados não encontrada ou inválida.")
    if destination.exists() or destination.is_symlink():
        raise ValueError("O destino já existe; escolha um arquivo novo (nada é sobrescrito).")
    try:
        destination.resolve().relative_to(data_dir.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("O pacote não pode ser gravado dentro da pasta de dados do Hórus.")

    files: list[dict] = []
    excluded: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="horus-export-") as scratch_dir:
        scratch = Path(scratch_dir)
        db_info, staged = _dump_pg_tables(store, scratch)

        for name, source in _candidates(data_dir):
            if source.stat().st_size > MAX_MANIFEST_FILE:
                excluded.append({"path": name, "reason": "grande demais para manifesto"})
                continue
            raw = source.read_bytes()
            copy = scratch / f"manifest-{len(staged)}.json"
            copy.write_bytes(raw)
            try:
                json.loads(raw)
            except ValueError:
                excluded.append({"path": name, "reason": "JSON inválido"})
                continue
            if SECRET_SHAPE.search(raw.decode("utf-8", "replace")):
                excluded.append({"path": name, "reason": "formato de credencial"})
                continue
            staged.append((name, copy, "local_file"))

        created = (now or datetime.now(timezone.utc)).isoformat()
        for area in EXCLUDED_AREAS:
            if (data_dir / area.rstrip("/")).exists():
                excluded.append({"path": area, "reason": "área nunca exportada (segredos, modelos, mídia ou pacotes próprios)"})

        for name, source, category in staged:
            files.append({"name": name, "size": source.stat().st_size, "sha256": _sha(source), "category": category})

        manifest = {
            "schema_version": SCHEMA,
            "format": FORMAT_TAG,
            "created_at": created,
            "database": db_info,
            "files": files,
            "excluded": excluded,
            "contains_private_data": True,
        }
        try:
            with destination.open("xb") as handle, zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as package:
                for name, source, _category in staged:
                    package.write(source, name)
                package.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    return {
        "ok": True,
        "schema_version": SCHEMA,
        "format": FORMAT_TAG,
        "files": len(files),
        "excluded": excluded,
        "sha256": _sha(destination),
        "database": {
            "instance_id": db_info["instance_id"],
            "schema_version": db_info["schema_version"],
            "tables_count": len(db_info["tables"]),
        },
    }


def verify_export(package: Path) -> dict:
    """Verifica integridade estrutural, hashes por tabela/arquivo e ausência de segredos no pacote."""
    errors: list[str] = []
    try:
        with zipfile.ZipFile(package) as archive:
            infolist = archive.infolist()
            if len(infolist) > MAX_ENTRIES:
                return {"ok": False, "errors": ["pacote com entradas demais"]}
            for info in infolist:
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    return {"ok": False, "errors": [f"link simbólico recusado: {info.filename}"]}
            names = [i.filename for i in infolist]
            if len(names) != len(set(names)):
                errors.append("nomes duplicados no pacote")
            bad = [n for n in names if not SAFE_NAME.fullmatch(n) or ".." in n.split("/")]
            if bad:
                return {"ok": False, "errors": [f"nome inseguro: {n}" for n in bad[:5]]}
            if "manifest.json" not in names:
                return {"ok": False, "errors": ["manifest.json ausente"]}

            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("schema_version") != SCHEMA or manifest.get("format") != FORMAT_TAG:
                errors.append("versão ou formato de manifesto desconhecido")

            declared = {f["name"]: f for f in manifest.get("files", [])}
            if set(declared) != set(names) - {"manifest.json"}:
                errors.append("arquivos do pacote diferem do manifesto")

            db_meta = manifest.get("database") or {}
            declared_tables = db_meta.get("tables") or {}
            if not declared_tables:
                errors.append("dump lógico PostgreSQL ausente")
            for table in declared_tables:
                if f"pg_dump/{table}.jsonl" not in declared:
                    errors.append(f"tabela declarada ausente do pacote: {table}")

            for name, item in declared.items():
                if name not in names:
                    continue
                if not ALLOWED_NAME.fullmatch(name):
                    errors.append(f"entrada fora da allowlist: {name}")
                    continue
                declared_size = item.get("size")
                if type(declared_size) is not int or declared_size < 0 or archive.getinfo(name).file_size != declared_size:
                    errors.append(f"tamanho divergente: {name}")
                    continue

                digest, total = hashlib.sha256(), 0
                is_pg = name.startswith("pg_dump/")
                is_local = name.startswith("local_files/")
                line_count = 0
                raw_buf = bytearray() if is_local else None
                with archive.open(name) as stream:
                    if is_pg:
                        for line in stream:
                            total += len(line)
                            if total > declared_size:
                                break
                            digest.update(line)
                            if line.strip():
                                json.loads(line.decode("utf-8"))
                                line_count += 1
                    else:
                        while chunk := stream.read(1024 * 1024):
                            total += len(chunk)
                            if total > declared_size:
                                break
                            digest.update(chunk)
                            if raw_buf is not None:
                                raw_buf += chunk

                if total != declared_size or digest.hexdigest() != item.get("sha256"):
                    errors.append(f"hash divergente: {name}")
                    continue
                if is_pg:
                    table = name[len("pg_dump/") : -len(".jsonl")]
                    table_meta = declared_tables.get(table)
                    if not isinstance(table_meta, dict) or table_meta.get("rows") != line_count or table_meta.get("sha256") != item.get("sha256"):
                        errors.append(f"metadados de tabela divergentes: {table}")
                if is_local and raw_buf is not None:
                    text = raw_buf.decode("utf-8", errors="replace")
                    json.loads(text)
                    if SECRET_SHAPE.search(text):
                        errors.append(f"formato de credencial recusado: {name}")
    except (OSError, zipfile.BadZipFile, ValueError, KeyError, UnicodeError) as error:
        errors.append(f"pacote ilegível ({type(error).__name__})")
    return {"ok": not errors, "errors": errors}


def restore_local_data(
    package: Path,
    destination: Path,
    *,
    target_conn=None,
    expected_schema_version: str | None = EXPECTED_SCHEMA_VERSION,
) -> dict:
    """Restaura `local_files/` em pasta nova/vazia e, se `target_conn` for informada, o dump PG em banco isolado e vazio."""
    package, destination = Path(package), Path(destination)
    if _is_link(destination):
        raise ValueError("O destino não pode ser um link; escolha uma pasta nova ou vazia.")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("O destino precisa ser uma pasta nova ou vazia; nada existente é sobrescrito.")
    if not destination.parent.is_dir():
        raise ValueError("A pasta que conterá o destino não existe.")

    report = verify_export(package)
    if not report["ok"]:
        raise ValueError("Pacote não verificado; nada foi escrito: " + "; ".join(report["errors"][:3]))

    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        declared = {f["name"]: f for f in manifest["files"]}
        db_meta = manifest.get("database") or {}
        backup_schema = db_meta.get("schema_version")
        if expected_schema_version and backup_schema and str(backup_schema) > str(expected_schema_version):
            raise ValueError(
                f"O pacote vem de uma versão mais nova do Hórus (schema {backup_schema}; aplicativo {expected_schema_version}). "
                "Atualize o Hórus antes de restaurar; nada foi escrito."
            )

        pg_result = None
        if target_conn is not None:
            pg_result = restore_to_isolated_pg(
                package, target_conn, expected_schema_version=expected_schema_version
            )

        stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent))
        try:
            restored = []
            for name, item in declared.items():
                relative = _restore_relative(name)
                if relative is None:
                    continue
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                digest, total = hashlib.sha256(), 0
                with archive.open(name) as stream, target.open("xb") as out:
                    while chunk := stream.read(1024 * 1024):
                        total += len(chunk)
                        if total > item["size"]:
                            raise ValueError(f"Conteúdo maior que o declarado: {name}")
                        digest.update(chunk)
                        out.write(chunk)
                if total != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise ValueError(f"Arquivo alterado durante a restauração: {name}")
                if relative.endswith(".json") and SECRET_SHAPE.search(target.read_text(encoding="utf-8", errors="replace")):
                    raise ValueError(f"Arquivo com formato de credencial recusado: {name}")
                restored.append(relative)
            if destination.exists():
                destination.rmdir()
            os.rename(stage, destination)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    return {
        "ok": True,
        "destination": str(destination),
        "files": len(restored),
        "schema_version": backup_schema,
        "pg_restored": pg_result is not None,
        "pg_tables": pg_result["restored_tables"] if pg_result else {},
        "instruction": (
            "Arquivos locais restaurados em pasta isolada. A restauração de dados PostgreSQL exige banco isolado e vazio "
            "(nunca sobrepor o banco remoto pela interface). Credenciais não fazem parte do pacote: reconfigure-as."
        ),
    }


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Verifica ou restaura arquivos locais de um pacote v2 do Hórus.")
    parser.add_argument("command", choices=("verify", "restore"))
    parser.add_argument("path", type=Path, help="pacote .zip")
    parser.add_argument("--to", type=Path)
    args = parser.parse_args(argv)
    if (args.command != "verify") != (args.to is not None):
        parser.error("restore exige --to; verify não aceita --to")
    try:
        report = verify_export(args.path) if args.command == "verify" else restore_local_data(args.path, args.to)
    except (OSError, ValueError) as error:
        report = {"ok": False, "error": str(error) if isinstance(error, ValueError) else "Operação recusada; nada foi sobrescrito."}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
