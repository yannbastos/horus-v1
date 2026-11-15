"""Exportação dos dados locais do usuário como pacote verificável (HOR-117). Somente leitura da origem; offline.

Conteúdo: cópia consistente (API de *backup* do SQLite, banco aberto em modo leitura) do banco operacional e do estado do
Radar, as preferências não secretas (`narration-preference.json`) e os manifestos JSON das produções
(`productions/<id>/*.json`). Nunca entram: `integrations/` (conexão e token do Hermes), `models/`, mídia, `exports/`,
arquivos com formato de credencial, links simbólicos/junções. Cada arquivo tem SHA-256 em `manifest.json`;
`verify_export` confere hashes, nomes seguros e a integridade dos bancos. O pacote contém dados privados do usuário:
guarde-o como o próprio banco (BitLocker; fora de pastas sincronizadas).

CLI: `python -m horus_backend.local_data_export export <pasta-de-dados> --to <pacote.zip>` e `... verify <pacote.zip>`.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .backups import _inspect

SCHEMA = 1
MAX_MANIFEST_FILE = 5 * 1024 * 1024
SECRET_SHAPE = re.compile(
    r"\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36,}|\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}|-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"
    r"|\bBearer\s+[A-Za-z0-9._~+/-]{24,}|(?i:\b(?:api[_-]?key|secret|token|password|senha)\b[\"']?\s*[:=]\s*[\"'][A-Za-z0-9/+_=-]{20,}[\"'])")
SAFE_NAME = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,100}/)*[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
EXCLUDED_AREAS = ("integrations/", "models/", "exports/")


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _snapshot(source: Path, target: Path) -> dict:
    """Cópia consistente do banco vivo (WAL incluído) e verificação de integridade da cópia."""
    with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True, timeout=10)) as db, \
            closing(sqlite3.connect(target)) as out:
        db.backup(out)
        report = _inspect(out)
    if not report["ok"]:
        raise ValueError(f"Banco {source.name} sem integridade; exportação recusada (nada foi criado).")
    return report


def _candidates(data_dir: Path):
    """(nome no pacote, caminho de origem) das cópias de arquivo; os bancos são tratados à parte."""
    preference = data_dir / "narration-preference.json"
    if preference.is_file() and not _is_link(preference):
        yield "settings/narration-preference.json", preference
    productions = data_dir / "productions"
    if productions.is_dir() and not _is_link(productions):
        for folder in sorted(p for p in productions.iterdir() if p.is_dir() and not _is_link(p)):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", folder.name):
                continue
            for item in sorted(folder.glob("*.json")):
                if _is_link(item) or not item.is_file():   # link para fora da produção nunca é exportado
                    continue
                yield f"productions/{folder.name}/{item.name}", item


def export_local_data(data_dir: Path, destination: Path, *, now: datetime | None = None) -> dict:
    data_dir, destination = Path(data_dir), Path(destination)
    database = data_dir / "horus.sqlite3"
    if not database.is_file() or _is_link(database):
        raise ValueError("Banco operacional não encontrado na pasta de dados.")
    if destination.exists() or destination.is_symlink():
        raise ValueError("O destino já existe; escolha um arquivo novo (nada é sobrescrito).")
    try:
        destination.resolve().relative_to(data_dir.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("O pacote não pode ser gravado dentro da pasta de dados do Hórus.")
    files, excluded = [], []
    with tempfile.TemporaryDirectory(prefix="horus-export-") as scratch:
        scratch = Path(scratch)
        staged: list[tuple[str, Path]] = []
        info = {}
        info["horus.sqlite3"] = _snapshot(database, scratch / "horus.sqlite3")
        staged.append(("database/horus.sqlite3", scratch / "horus.sqlite3"))
        radar = data_dir / "radar" / "radar-youtube.sqlite3"
        if radar.is_file() and not _is_link(radar):
            info["radar-youtube.sqlite3"] = _snapshot(radar, scratch / "radar.sqlite3")
            staged.append(("database/radar-youtube.sqlite3", scratch / "radar.sqlite3"))
        for name, source in _candidates(data_dir):
            if source.stat().st_size > MAX_MANIFEST_FILE:
                excluded.append({"path": name, "reason": "grande demais para manifesto"})
                continue
            raw = source.read_bytes()
            copy = scratch / f"manifest-{len(staged)}.json"
            copy.write_bytes(raw)   # hash e pacote usam esta cópia: o arquivo vivo pode mudar depois
            try:
                json.loads(raw)
            except ValueError:
                excluded.append({"path": name, "reason": "JSON inválido"})
                continue
            if SECRET_SHAPE.search(raw.decode("utf-8", "replace")):
                excluded.append({"path": name, "reason": "formato de credencial"})
                continue
            staged.append((name, copy))
        created = (now or datetime.now(timezone.utc)).isoformat()
        for area in EXCLUDED_AREAS:
            if (data_dir / area.rstrip("/")).exists():
                excluded.append({"path": area, "reason": "área nunca exportada (segredos, modelos ou pacotes próprios)"})
        for name, source in staged:
            files.append({"name": name, "size": source.stat().st_size, "sha256": _sha(source)})
        manifest = {"schema_version": SCHEMA, "created_at": created, "files": files, "excluded": excluded,
                    "databases": {k: {"user_version": v["user_version"], "tables": v["tables"]} for k, v in info.items()},
                    "contains_private_data": True}
        try:
            with destination.open("xb") as handle, zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as package:
                for name, source in staged:
                    package.write(source, name)
                package.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        except BaseException:
            destination.unlink(missing_ok=True)   # nunca deixa um pacote incompleto que pareça válido
            raise
    return {"ok": True, "files": len(files), "excluded": excluded, "sha256": _sha(destination)}


def verify_export(package: Path) -> dict:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(package) as archive:
            names = [i.filename for i in archive.infolist()]
            if len(names) != len(set(names)):
                errors.append("nomes duplicados no pacote")
            bad = [n for n in names if not SAFE_NAME.fullmatch(n) or ".." in n.split("/")]
            if bad:
                return {"ok": False, "errors": [f"nome inseguro: {n}" for n in bad[:5]]}
            if "manifest.json" not in names:
                return {"ok": False, "errors": ["manifest.json ausente"]}
            manifest = json.loads(archive.read("manifest.json"))
            declared = {f["name"]: f for f in manifest.get("files", [])}
            if manifest.get("schema_version") != SCHEMA:
                errors.append("versão de manifesto desconhecida")
            if set(declared) != set(names) - {"manifest.json"}:
                errors.append("arquivos do pacote diferem do manifesto")
            if "database/horus.sqlite3" not in declared:
                errors.append("banco operacional ausente")
            with tempfile.TemporaryDirectory(prefix="horus-verify-") as scratch:
                for name, item in declared.items():
                    if name not in names:
                        continue
                    declared_size = item["size"]
                    if type(declared_size) is not int or declared_size < 0 or archive.getinfo(name).file_size != declared_size:
                        errors.append(f"tamanho divergente: {name}")
                        continue
                    digest, total = hashlib.sha256(), 0
                    copy = Path(scratch) / name.replace("/", "_") if name.startswith("database/") else None
                    sink = copy.open("wb") if copy else None
                    try:
                        with archive.open(name) as stream:
                            while chunk := stream.read(1024 * 1024):
                                total += len(chunk)
                                if total > declared_size:      # o conteúdo real excede o declarado: para de ler
                                    break
                                digest.update(chunk)
                                if sink:
                                    sink.write(chunk)
                    finally:
                        if sink:
                            sink.close()
                    if total != declared_size or digest.hexdigest() != item["sha256"]:
                        errors.append(f"hash divergente: {name}")
                        continue
                    if copy:
                        with closing(sqlite3.connect(copy)) as db:
                            if not _inspect(db)["ok"]:
                                errors.append(f"banco sem integridade: {name}")
    except (OSError, zipfile.BadZipFile, ValueError, KeyError, sqlite3.Error) as error:
        errors.append(f"pacote ilegível ({type(error).__name__})")
    return {"ok": not errors, "errors": errors}


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Exporta/verifica os dados locais do Hórus (somente leitura da origem).")
    parser.add_argument("command", choices=("export", "verify"))
    parser.add_argument("path", type=Path, help="pasta de dados (export) ou pacote .zip (verify)")
    parser.add_argument("--to", type=Path)
    args = parser.parse_args(argv)
    if (args.command == "export") != (args.to is not None):
        parser.error("export exige --to; verify não aceita --to")
    try:
        report = export_local_data(args.path, args.to) if args.command == "export" else verify_export(args.path)
    except (OSError, ValueError, sqlite3.Error) as error:
        report = {"ok": False, "error": str(error) if isinstance(error, ValueError) else "Operação recusada; nada foi sobrescrito."}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
