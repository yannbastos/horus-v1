"""Exportação dos dados locais do usuário como pacote verificável (HOR-117). Somente leitura da origem; offline.

Conteúdo: cópia consistente (API de *backup* do SQLite, banco aberto em modo leitura) do banco operacional e do estado do
Radar, as preferências não secretas (`narration-preference.json`) e os manifestos JSON das produções
(`productions/<id>/*.json`), os comentários de revisão (`production-comments/<id>.json`, HOR-088) e o estado de leitura das
notificações (`notifications-state.json`). Nunca entram: `integrations/` (conexão e token do Hermes), `models/`, mídia, `exports/`,
arquivos com formato de credencial, links simbólicos/junções. Cada arquivo tem SHA-256 em `manifest.json`;
`verify_export` confere hashes, nomes seguros e a integridade dos bancos. O pacote contém dados privados do usuário:
guarde-o como o próprio banco (BitLocker; fora de pastas sincronizadas).

Restauração (HOR-093): `restore_local_data` devolve o pacote a uma pasta de dados **nova ou vazia** escolhida pelo usuário,
nunca sobrescrevendo nada. Verifica o pacote por completo antes de escrever, recusa schema de banco mais novo que o do
aplicativo, grava numa pasta temporária irmã (hash conferido durante a gravação) e só então troca para o destino. Segredos
não fazem parte do pacote: o usuário reconfigura Hermes e demais credenciais. A mídia e os arquivos finais das produções
(`exports/`) também não entram no pacote e, portanto, não são restaurados. Não migra o banco.

CLI: `python -m horus_backend.local_data_export export <pasta-de-dados> --to <pacote.zip>`, `... verify <pacote.zip>` e
`... restore <pacote.zip> --to <pasta-nova-ou-vazia>`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .backups import _inspect
from .migrations import SCHEMA_VERSION
from .notifications import ID_PATTERN as NOTIFICATION_ID

SCHEMA = 1
MAX_MANIFEST_FILE = 5 * 1024 * 1024
SECRET_SHAPE = re.compile(
    r"\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36,}|\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}|-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"
    r"|\bBearer\s+[A-Za-z0-9._~+/-]{24,}|(?i:\b(?:api[_-]?key|secret|token|password|senha)\b[\"']?\s*[:=]\s*[\"'][A-Za-z0-9/+_=-]{20,}[\"'])")
SAFE_NAME = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,100}/)*[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
MAX_ENTRIES = 20000
ALLOWED_NAME = re.compile(r"^(?:database/(?:horus|radar-youtube)\.sqlite3|settings/narration-preference\.json"
                          r"|settings/notifications-state\.json|comments/[A-Za-z0-9_-]{1,64}\.json"
                          r"|productions/[A-Za-z0-9_-]{1,64}/[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.json)$")
RESTORE_TARGET = {"database/horus.sqlite3": "horus.sqlite3", "database/radar-youtube.sqlite3": "radar/radar-youtube.sqlite3",
                  "settings/narration-preference.json": "narration-preference.json",
                  "settings/notifications-state.json": "notifications-state.json"}
COMMENT_NAME = re.compile(r"^comments/([A-Za-z0-9_-]{1,64})\.json$")
EXCLUDED_AREAS = ("integrations/", "models/", "exports/")


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _restore_relative(name: str) -> str:
    comment = COMMENT_NAME.fullmatch(name)
    return f"production-comments/{comment.group(1)}.json" if comment else RESTORE_TARGET.get(name, name)


def _invalid_content(name: str, raw: bytes) -> str | None:
    """Estrutura mínima dos JSON de comentários e de notificações (None = válido). Aplicada na exportação e na verificação."""
    comment = COMMENT_NAME.fullmatch(name)
    if not comment and name != "settings/notifications-state.json":
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return "JSON inválido"
    if not isinstance(doc, dict):
        return "estrutura inválida"
    if name == "settings/notifications-state.json":
        read, unread, before = doc.get("read", {}), doc.get("unread", []), doc.get("read_all_before")
        ok = (isinstance(read, dict) and len(read) <= 5000 and all(isinstance(k, str) and NOTIFICATION_ID.fullmatch(k) and isinstance(v, str) for k, v in read.items())
              and isinstance(unread, list) and len(unread) <= 5000 and all(isinstance(k, str) and NOTIFICATION_ID.fullmatch(k) for k in unread)
              and (before is None or isinstance(before, str)))
        return None if ok else "estado de notificações inválido"
    pid, comments = comment.group(1), doc.get("comments")
    if doc.get("schema_version") != 1 or not isinstance(comments, list) or len(comments) > 500 or not isinstance(doc.get("receipts"), dict)             or len(doc["receipts"]) > 1000:
        return "estrutura de comentários inválida"
    seen = set()
    for item in comments:
        if not isinstance(item, dict):
            return "comentário inválido"
        at, trail = item.get("at_seconds"), item.get("trail")
        if (not isinstance(item.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", item["id"]) or item["id"] in seen
                or item.get("production_id") != pid or not isinstance(item.get("artifact_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["artifact_sha256"]) or isinstance(at, bool)
                or not isinstance(at, (int, float)) or not 0 <= at <= 86400 or not isinstance(item.get("text"), str)
                or not isinstance(item.get("author"), str) or item.get("author_kind") != "human"
                or item.get("status") not in ("open", "resolved") or not isinstance(item.get("created_at"), str)
                or not isinstance(trail, list) or not 1 <= len(trail) <= 50
                or not all(isinstance(e, dict) and e.get("action") in ("created", "resolved", "reopened") and isinstance(e.get("author"), str)
                           for e in trail)):
            return "comentário inválido (o autor deve ser humano)"
        seen.add(item["id"])
    return None


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
    notifications = data_dir / "notifications-state.json"
    if notifications.is_file() and not _is_link(notifications):
        yield "settings/notifications-state.json", notifications
    comments = data_dir / "production-comments"
    if comments.is_dir() and not _is_link(comments):
        for item in sorted(comments.glob("*.json")):
            if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", item.stem) and item.is_file() and not _is_link(item):
                yield f"comments/{item.name}", item
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
            reason = _invalid_content(name, raw)
            if reason:
                excluded.append({"path": name, "reason": reason})
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
                    digest, total, content = hashlib.sha256(), 0, bytearray()
                    checked = COMMENT_NAME.fullmatch(name) or name == "settings/notifications-state.json"
                    copy = Path(scratch) / name.replace("/", "_") if name.startswith("database/") else None
                    sink = copy.open("wb") if copy else None
                    try:
                        with archive.open(name) as stream:
                            while chunk := stream.read(1024 * 1024):
                                total += len(chunk)
                                if total > declared_size:      # o conteúdo real excede o declarado: para de ler
                                    break
                                digest.update(chunk)
                                if checked:
                                    content += chunk
                                if sink:
                                    sink.write(chunk)
                    finally:
                        if sink:
                            sink.close()
                    if total != declared_size or digest.hexdigest() != item["sha256"]:
                        errors.append(f"hash divergente: {name}")
                        continue
                    if checked and (reason := _invalid_content(name, bytes(content))):
                        errors.append(f"conteúdo inválido ({reason}): {name}")
                        continue
                    if copy:
                        with closing(sqlite3.connect(copy)) as db:
                            if not _inspect(db)["ok"]:
                                errors.append(f"banco sem integridade: {name}")
    except (OSError, zipfile.BadZipFile, ValueError, KeyError, sqlite3.Error) as error:
        errors.append(f"pacote ilegível ({type(error).__name__})")
    return {"ok": not errors, "errors": errors}


def restore_local_data(package: Path, destination: Path) -> dict:
    """Restaura o pacote verificado numa pasta de dados nova/vazia, de forma atômica. Nunca sobrescreve nem migra."""
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
        if len(archive.infolist()) > MAX_ENTRIES:
            raise ValueError("Pacote com entradas demais; nada foi escrito.")
        manifest = json.loads(archive.read("manifest.json"))
        declared = {f["name"]: f for f in manifest["files"]}
        refused = [n for n in declared if not ALLOWED_NAME.fullmatch(n)]
        if refused:
            raise ValueError(f"Pacote contém arquivo fora do formato do Hórus ({refused[0]}); nada foi escrito.")
        for info in archive.infolist():
            if (info.external_attr >> 16) & 0o170000 == 0o120000:   # entrada de link simbólico
                raise ValueError("Pacote contém link simbólico; nada foi escrito.")
        operational = (manifest.get("databases") or {}).get("horus.sqlite3", {}).get("user_version")
        if type(operational) is not int or operational < 0:
            raise ValueError("Versão do banco ausente no manifesto; nada foi escrito.")
        if operational > SCHEMA_VERSION:
            raise ValueError(f"O pacote vem de uma versão mais nova do Hórus (banco v{operational}; este aplicativo entende até "
                             f"v{SCHEMA_VERSION}). Atualize o Hórus antes de restaurar; nada foi escrito.")
        stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent))
        try:
            restored = []
            for name, item in declared.items():
                relative = _restore_relative(name)   # produções mantêm o caminho relativo da pasta de dados
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
                if relative.endswith(".sqlite3"):
                    with closing(sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)) as db:
                        if not _inspect(db)["ok"]:
                            raise ValueError(f"Banco restaurado sem integridade: {name}")
                restored.append(relative)
            if destination.exists():
                destination.rmdir()   # só existe vazia (conferido acima); falha se alguém escreveu nela nesse intervalo
            os.rename(stage, destination)   # atômico no mesmo volume; nunca substitui uma pasta existente
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)   # nada parcial no destino
            raise
    return {"ok": True, "destination": str(destination), "files": len(restored), "database_version": operational,
            "instruction": "Restaurado sem migração: aponte HORUS_DATA_DIR para a nova pasta; o Hórus migra o banco ao abrir. "
                           "Credenciais (Hermes, tokens) não fazem parte do pacote: reconfigure-as. Mídia e exports finais não são restaurados."}


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Exporta/verifica os dados locais do Hórus (somente leitura da origem).")
    parser.add_argument("command", choices=("export", "verify", "restore"))
    parser.add_argument("path", type=Path, help="pasta de dados (export) ou pacote .zip (verify, restore)")
    parser.add_argument("--to", type=Path)
    args = parser.parse_args(argv)
    if (args.command != "verify") != (args.to is not None):
        parser.error("export e restore exigem --to; verify não aceita --to")
    try:
        report = {"export": lambda: export_local_data(args.path, args.to), "verify": lambda: verify_export(args.path),
                  "restore": lambda: restore_local_data(args.path, args.to)}[args.command]()
    except (OSError, ValueError, sqlite3.Error) as error:
        report = {"ok": False, "error": str(error) if isinstance(error, ValueError) else "Operação recusada; nada foi sobrescrito."}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
