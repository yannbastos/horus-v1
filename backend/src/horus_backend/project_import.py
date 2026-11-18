"""Importação de projeto a partir do pacote de exportação (HOR-099). Arquivo morto verificado; sem tabelas nem migração.

Fluxo: (1) o pacote é verificado POR COMPLETO, em fluxo e sem escrever nada; (2) copiado para uma pasta de preparação em
`<data_dir>/imports/` conferindo os hashes de novo durante a cópia; (3) a pasta é promovida com uma troca atômica para
`<data_dir>/imports/<id novo>/` e o registro `import.json` é gravado antes da promoção. Qualquer falha apaga a preparação.
O importado NUNCA vira produção, não herda aprovação, não é reexportável nem publicável: revisão, licenças e QC originais
ficam só como histórico (`publishable=false` sempre). Ids novos sempre; reimportar o mesmo manifesto é 409.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import threading
import uuid
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from pydantic import ValidationError

from datetime import datetime, timezone

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .media_contracts import validate_quality_report
from .script_contract import ScriptDocument
from .storage import DatabaseUnavailable, now

REQUIRED = {"video.mp4", "subtitles.srt", "script.json", "quality-report.json", "licenses.json", "review.json"}
OPTIONAL = {"script.txt", "metadata.json", "thumbnail.png", "thumbnail.jpg"}
ALLOWED = REQUIRED | OPTIONAL | {"manifest.json"}
MAX_TOTAL_BYTES = 8 * 1024 ** 3
MAX_VIDEO_BYTES = 8 * 1024 ** 3
MAX_TEXT_BYTES = 10 * 1024 ** 2
MAX_IMAGE_BYTES = 20 * 1024 ** 2
MAX_MANIFEST_BYTES = 1024 ** 2
MAX_MEMBERS = len(ALLOWED)
MAX_RATIO = 200
CHUNK = 1024 * 1024
ID = re.compile(r"[a-f0-9]{32}")


class ImportRefused(ValueError):
    """Pacote ou pedido recusado. `code` estável; a mensagem nunca traz caminhos."""

    def __init__(self, code: str, message: str, status: int = 422, **extra):
        super().__init__(message)
        self.code, self.status, self.extra = code, status, extra


def _limit(name: str) -> int:
    if name == "video.mp4":
        return MAX_VIDEO_BYTES
    if name.startswith("thumbnail."):
        return MAX_IMAGE_BYTES
    return MAX_MANIFEST_BYTES if name == "manifest.json" else MAX_TEXT_BYTES


def _verify_structure(archive: ZipFile, package_size: int) -> list:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS or not infos:
        raise ImportRefused("package_structure", "Pacote com quantidade inválida de arquivos.")
    seen, total, compressed = set(), 0, 0
    for info in infos:
        name = info.filename
        if name not in ALLOWED:
            raise ImportRefused("member_not_allowed", "O pacote contém um arquivo que não faz parte da exportação do Hórus.")
        if name.lower() in seen:
            raise ImportRefused("member_duplicate", "O pacote repete um arquivo.")
        seen.add(name.lower())
        mode = info.external_attr >> 16
        if info.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG):     # só arquivo comum (sem link, pipe, dispositivo)
            raise ImportRefused("member_link", "O pacote contém link ou item que não é arquivo comum.")
        if info.flag_bits & 0x1:
            raise ImportRefused("member_encrypted", "O pacote contém arquivo criptografado.")
        if info.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
            raise ImportRefused("member_compression", "Método de compressão não aceito.")
        if info.file_size > _limit(name):
            raise ImportRefused("member_too_large", "Um arquivo do pacote excede o tamanho permitido.")
        if name != "video.mp4" and info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise ImportRefused("compression_ratio", "Razão de compressão suspeita (possível zip bomb).")
        total += info.file_size
        compressed += info.compress_size
    if total > MAX_TOTAL_BYTES:
        raise ImportRefused("package_too_large", "O pacote excede o tamanho total permitido.")
    if compressed > package_size:           # membros sobrepostos no mesmo trecho do arquivo
        raise ImportRefused("package_structure", "Estrutura do pacote inconsistente (membros sobrepostos).")
    return infos


def _read_small(archive: ZipFile, name: str, limit: int) -> bytes:
    with archive.open(name) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ImportRefused("member_too_large", "Um arquivo do pacote excede o tamanho permitido.")
    return data


def _manifest(archive: ZipFile, infos: list) -> dict:
    names = {i.filename for i in infos}
    if "manifest.json" not in names:
        raise ImportRefused("manifest_missing", "O pacote não tem manifest.json.")
    try:
        manifest = json.loads(_read_small(archive, "manifest.json", MAX_MANIFEST_BYTES))
    except (ValueError, UnicodeError, BadZipFile):
        raise ImportRefused("manifest_invalid", "manifest.json ilegível.") from None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or type(manifest.get("schema_version")) is not int:
        raise ImportRefused("manifest_invalid", "Versão de manifesto não suportada.")
    if manifest.get("publishable") is not False:
        raise ImportRefused("manifest_invalid", "O manifesto precisa declarar publishable=false.")
    for field, pattern in (("id", r"[a-f0-9]{64}"), ("production_id", r"[a-f0-9]{32}")):
        if not isinstance(manifest.get(field), str) or not re.fullmatch(pattern, manifest[field]):
            raise ImportRefused("manifest_invalid", f"Campo {field} do manifesto inválido.")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_MEMBERS:
        raise ImportRefused("manifest_invalid", "Lista de arquivos do manifesto inválida.")
    declared = {}
    for item in files:
        if (not isinstance(item, dict) or set(item) != {"name", "sha256", "size_bytes"} or item["name"] not in ALLOWED - {"manifest.json"}
                or not isinstance(item["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
                or type(item["size_bytes"]) is not int or item["size_bytes"] < 0 or item["name"] in declared):
            raise ImportRefused("manifest_invalid", "Entrada inválida na lista de arquivos do manifesto.")
        declared[item["name"]] = item
    if set(declared) != names - {"manifest.json"}:
        raise ImportRefused("manifest_mismatch", "Os arquivos do pacote não coincidem com o manifesto.")
    if not REQUIRED <= set(declared):
        raise ImportRefused("manifest_incomplete", "O pacote não traz todos os arquivos obrigatórios.")
    manifest["_declared"] = declared
    return manifest


def _stream(archive: ZipFile, name: str, declared: dict, sink=None) -> bytes | None:
    """Lê em fluxo conferindo tamanho e SHA-256 do manifesto; grava em `sink` se houver. Devolve o conteúdo se for pequeno."""
    info = archive.getinfo(name)
    if info.file_size != declared["size_bytes"]:
        raise ImportRefused("size_mismatch", "Tamanho de um arquivo difere do manifesto.")
    digest, total, head, keep = hashlib.sha256(), 0, b"", bytearray() if name != "video.mp4" else None
    with archive.open(name) as stream:
        while block := stream.read(CHUNK):
            total += len(block)
            if total > declared["size_bytes"]:          # conteúdo real maior que o declarado: para de ler
                raise ImportRefused("size_mismatch", "Um arquivo tem mais bytes do que o manifesto declara.")
            digest.update(block)
            if len(head) < 16:
                head += block[:16 - len(head)]
            if keep is not None:
                keep += block
            if sink is not None:
                sink.write(block)
    if total != declared["size_bytes"] or digest.hexdigest() != declared["sha256"]:
        raise ImportRefused("hash_mismatch", "O conteúdo de um arquivo não confere com o manifesto (SHA-256).")
    if name == "video.mp4" and head[4:8] != b"ftyp":
        raise ImportRefused("video_invalid", "video.mp4 não é um MP4 válido.")
    if name == "thumbnail.png" and not head.startswith(b"\x89PNG"):
        raise ImportRefused("image_invalid", "thumbnail.png inválida.")
    if name == "thumbnail.jpg" and not head.startswith(b"\xff\xd8\xff"):
        raise ImportRefused("image_invalid", "thumbnail.jpg inválida.")
    return bytes(keep) if keep is not None else None


def _validate_content(contents: dict) -> dict:
    parsed = {}
    for name in ("script.json", "quality-report.json", "licenses.json", "review.json", "metadata.json"):
        if name in contents:
            try:
                parsed[name] = json.loads(contents[name].decode("utf-8"))
            except (ValueError, UnicodeError):
                raise ImportRefused("content_invalid", f"{name} não é JSON válido.") from None
    for name in ("script.txt", "subtitles.srt"):
        if name in contents:
            try:
                contents[name].decode("utf-8")
            except UnicodeError:
                raise ImportRefused("content_invalid", f"{name} não é UTF-8.") from None
    try:
        script = ScriptDocument.model_validate(parsed["script.json"])
    except (ValidationError, ValueError):
        raise ImportRefused("script_invalid", "script.json não segue o contrato de roteiro.") from None
    report = validate_quality_report(parsed["quality-report.json"])
    if not report["ok"] and parsed["quality-report.json"].get("status") != "not_run":
        raise ImportRefused("quality_report_invalid", "quality-report.json não segue o contrato de QC.")
    for name in ("licenses.json", "review.json"):
        if not isinstance(parsed[name], dict):
            raise ImportRefused("content_invalid", f"{name} deve ser um objeto.")
    parsed["_title"] = script.title
    return parsed


def verify_package(path: Path) -> dict:
    """Verificação completa, sem escrever nada. Devolve o manifesto e o conteúdo pequeno já validado."""
    try:
        size = path.stat().st_size
        with ZipFile(path) as archive:
            infos = _verify_structure(archive, size)
            manifest = _manifest(archive, infos)
            contents = {}
            for name, declared in manifest["_declared"].items():
                data = _stream(archive, name, declared)
                if data is not None:
                    contents[name] = data
            parsed = _validate_content(contents)
    except (BadZipFile, OSError, EOFError, RuntimeError, NotImplementedError):
        raise ImportRefused("package_unreadable", "O arquivo não é um pacote de exportação legível.") from None
    return {"manifest": manifest, "contents": contents, "parsed": parsed}


class ImportService:
    def __init__(self, data_dir: Path, store=None, *, device_id: str = "default"):
        self.root = Path(data_dir) / "imports"
        self.store = store
        self.device_id = device_id
        self.lock = threading.Lock()

    def _uow(self, *, readonly: bool = False):
        if self.store is None or not hasattr(self.store, "uow"):
            raise DatabaseUnavailable("database_unavailable")
        return self.store.uow(readonly=readonly)

    # --- leitura -------------------------------------------------------------------------------------------------

    @staticmethod
    def _valid_record(record) -> bool:
        return (
            isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and bool(ID.fullmatch(record["id"]))
            and isinstance(record.get("files"), list)
        )

    def _folder_present(self, import_id: str) -> bool:
        """A linha em `horus.imports` permanece após a exclusão local (trilha); a importação só existe para leitura
        enquanto a pasta existir (paridade com o legado, onde o registro vivia na pasta). Link/junção conta como presente
        para que `_folder_file` recuse como adulterada."""
        return os.path.lexists(self.root / import_id)

    def _records(self) -> list[dict]:
        with self._uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT manifest FROM horus.imports ORDER BY created_at DESC, id DESC LIMIT 200"
                )
                return [row["manifest"] for row in cur.fetchall()
                        if self._valid_record(row["manifest"]) and self._folder_present(row["manifest"]["id"])]

    def _read_record(self, import_id: str) -> dict | None:
        with self._uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT manifest FROM horus.imports WHERE id = %s", (import_id,))
                row = cur.fetchone()
        if row is None or not self._valid_record(row["manifest"]) or not self._folder_present(import_id):
            return None
        return row["manifest"]

    @staticmethod
    def _summary(record: dict) -> dict:
        return {k: record[k] for k in ("id", "imported_at", "title", "export_id", "source_production_id", "source_created_at",
                                       "files", "imported", "publishable")}

    def list(self) -> dict:
        return {"items": [self._summary(r) for r in self._records()]}

    def _folder_file(self, import_id: str, name: str) -> Path:
        folder = self.root / import_id
        path = folder / name
        for part in (self.root, folder, path):
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise ImportRefused("import_tampered", "Importação com link; recusada.", 409)
        if not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
            raise ImportRefused("import_missing_file", "Arquivo da importação ausente.", 409)
        return path

    def _build_detail(self, record: dict) -> dict:
        import_id = record["id"]
        history = {}
        for name, key in (("script.json", "script"), ("quality-report.json", "quality_report"), ("licenses.json", "licenses"),
                          ("review.json", "original_review"), ("metadata.json", "metadata")):
            item = next((f for f in record["files"] if f["name"] == name), None)
            if item is None:
                continue
            path = self._folder_file(import_id, name)
            data = path.read_bytes() if path.stat().st_size <= MAX_TEXT_BYTES else b""
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ImportRefused("import_tampered", "Arquivo da importação alterado depois de importado.", 409)
            history[key] = json.loads(data.decode("utf-8"))
        return {**self._summary(record), "manifest": {k: record[k] for k in ("export_id", "source_production_id", "source_created_at",
                                                                             "horus_version", "database_schema_version")},
                **history, "historical_only": True, "approved": False,
                "notice": "Histórico de um pacote importado: revisão, licenças e QC originais não valem como aprovação neste computador."}

    def get(self, import_id: str) -> dict:
        if not ID.fullmatch(import_id):
            raise KeyError(import_id)
        record = self._read_record(import_id)
        if record is None:
            raise KeyError(import_id)
        return self._build_detail(record)

    def file(self, import_id: str, name: str) -> tuple[Path, int]:
        if not ID.fullmatch(import_id) or name not in {"video.mp4", "subtitles.srt"}:
            raise KeyError(import_id)
        record = self._read_record(import_id)
        item = next((f for f in record["files"] if f["name"] == name), None) if record else None
        if item is None:
            raise KeyError(import_id)
        path = self._folder_file(import_id, name)
        if path.stat().st_size != item["size_bytes"]:
            raise ImportRefused("import_tampered", "Arquivo da importação alterado depois de importado.", 409)
        return path, item["size_bytes"]

    # --- importação ---------------------------------------------------------------------------------------------

    def _find_by_export_id(self, conn, export_id: str) -> dict | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT manifest FROM horus.imports WHERE manifest->>'export_id' = %s LIMIT 1",
                (export_id,),
            )
            row = cur.fetchone()
        return row["manifest"] if row and self._valid_record(row["manifest"]) else None

    def import_package(self, path: Path, key: str) -> tuple[dict, bool]:
        request_hash = hashlib.sha256(f"{key}".encode()).hexdigest()
        verified = verify_package(path)                       # fase 1: nada foi escrito até aqui
        manifest, parsed = verified["manifest"], verified["parsed"]
        fingerprint = hashlib.sha256(json.dumps({"files": sorted((n, d["sha256"]) for n, d in manifest["_declared"].items()),
                                                 "id": manifest["id"]}, sort_keys=True).encode()).hexdigest()
        with self._uow(readonly=True) as conn:
            existing = self._find_by_export_id(conn, manifest["id"])
        if existing is not None:
            if existing.get("idempotency_key_hash") == request_hash and existing.get("fingerprint") == fingerprint:
                return self._build_detail(existing), False
            raise ImportRefused("already_imported", "Este pacote já foi importado.", 409, existing_id=existing["id"])

        with self.lock:
            import_id = uuid.uuid4().hex
            staging = self.root / f".staging-{uuid.uuid4().hex}"
            target = self.root / import_id
            self.root.mkdir(parents=True, exist_ok=True)
            if self.root.is_symlink():
                raise ImportRefused("imports_dir_invalid", "Pasta de importações inválida.", 409)
            try:
                staging.mkdir()
                with ZipFile(path) as archive:            # fase 2: cópia conferindo os hashes de novo
                    for name, declared in manifest["_declared"].items():
                        with (staging / name).open("xb") as sink:
                            _stream(archive, name, declared, sink)
                moment_dt = datetime.now(timezone.utc)
                record = {"id": import_id, "imported": True, "imported_at": moment_dt.isoformat(), "title": parsed["_title"],
                          "export_id": manifest["id"], "source_production_id": manifest["production_id"],
                          "source_created_at": manifest.get("created_at") if isinstance(manifest.get("created_at"), str) else None,
                          "horus_version": str(manifest.get("horus_version"))[:40],
                          "database_schema_version": manifest.get("database_schema_version") if type(manifest.get("database_schema_version")) is int else None,
                          "files": [{"name": n, "sha256": d["sha256"], "size_bytes": d["size_bytes"]} for n, d in sorted(manifest["_declared"].items())],
                          "publishable": False, "idempotency_key_hash": request_hash, "fingerprint": fingerprint}
                os.replace(staging, target)               # promoção atômica dos artefatos em disco
            except ImportRefused:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            except (OSError, BadZipFile):
                shutil.rmtree(staging, ignore_errors=True)
                raise ImportRefused("import_failed", "Não foi possível gravar a importação; nada foi mantido.", 409) from None

            try:
                with self._uow() as conn:
                    conn.execute("SELECT pg_advisory_xact_lock(16408, hashtext(%s))", (manifest["id"],))
                    existing = self._find_by_export_id(conn, manifest["id"])
                    if existing is not None:
                        shutil.rmtree(target, ignore_errors=True)
                        if existing.get("idempotency_key_hash") == request_hash and existing.get("fingerprint") == fingerprint:
                            return self._build_detail(existing), False
                        raise ImportRefused("already_imported", "Este pacote já foi importado.", 409, existing_id=existing["id"])
                    conn.execute(
                        """
                        INSERT INTO horus.imports (id, device_id, path, hash, manifest, created_at, approved, publishable)
                        VALUES (%s, %s, %s, %s, %s, %s, false, false)
                        """,
                        (import_id, self.device_id, f"imports/{import_id}", fingerprint, Jsonb(record), moment_dt),
                    )
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
        return self._build_detail(record), True
