"""Gate "nenhum SQLite escondido" (HOR-164 / I-final A).

Garante que o produto (backend/src e workers) não importa nem referencia SQLite,
que o modo diagnóstico sobe sem banco e devolve 503 `database_unavailable` nas
rotas de domínio sem criar arquivos locais, e que o próprio gate detecta violações.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from horus_backend.api import create_app
from horus_backend.config import Settings
from horus_backend.database.vault import ConnectionVault
from keyring_support import MemoryKeyring

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "backend" / "src"
SCRIPTS_DIR = ROOT / "scripts"

FORBIDDEN_SQLITE_MODULES = {"sqlite3", "_sqlite3", "aiosqlite"}

# Scripts legados fora do escopo do corte I-final A (mantidos até remoção em HOR-163/pós-corte).
LEGACY_SCRIPTS_ALLOWLIST = {
    "scripts/check-installer-data.py",
    "scripts/ensaio_atualizacao_banco.py",
    "scripts/measure-local-pilot.py",
    "scripts/updater-e2e-support.py",
}

FORBIDDEN_TEXT_PATTERN = re.compile(
    r"sqlite|PRAGMA|json_extract|julianday|executescript|rowid|BEGIN IMMEDIATE|"
    r"INSERT OR|\.sqlite|\.db[\"']|-wal|secure_delete|winsqlite3|sqlite_master|last_insert_rowid",
    re.IGNORECASE,
)

# Linhas explícitas de documentação/sanitização (nunca código executando SQLite).
ALLOWED_TEXT_LINES = {
    "backend/src/horus_backend/backups.py": {3},
    "backend/src/horus_backend/data_deletion.py": {18},
    "backend/src/horus_backend/database/__init__.py": {1},
    "backend/src/horus_backend/local_data_export.py": {3},
    "backend/src/horus_backend/retention.py": {8, 58},
    # Sanitizador de perfil do Hermes externo: bloqueia arquivos .db/.sqlite no perfil.
    "backend/src/horus_backend/security.py": {694, 695},
}


def scan_ast_sqlite_imports(source: str) -> list[str]:
    """Retorna ocorrências de importação direta ou dinâmica de SQLite via AST."""
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_SQLITE_MODULES:
                    hits.append(f"L{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in FORBIDDEN_SQLITE_MODULES:
                hits.append(f"L{node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else None
            )
            if name in {"import_module", "__import__"} and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    if arg0.value.split(".")[0] in FORBIDDEN_SQLITE_MODULES:
                        hits.append(f"L{node.lineno}: {name}({arg0.value!r})")
    return hits


def scan_text_sqlite_tokens(source: str, *, allowed_lines: set[int] | None = None) -> list[str]:
    """Varredura textual linha a linha por tokens/SQL SQLite fora das linhas documentadas."""
    allowed = allowed_lines or set()
    hits: list[str] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        if lineno in allowed:
            continue
        if FORBIDDEN_TEXT_PATTERN.search(line):
            hits.append(f"L{lineno}: {line.strip()}")
    return hits


def test_legacy_migrations_moved_out_of_runtime():
    assert not (SRC_DIR / "horus_backend" / "migrations.py").exists()
    assert (ROOT / "backend" / "tools" / "migrate_sqlite_pg" / "legacy_schema.py").is_file()


def test_no_sqlite_imports_in_backend_src_or_scripts():
    violations: list[str] = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        hits = scan_ast_sqlite_imports(path.read_text(encoding="utf-8-sig"))
        violations.extend(f"{rel}: {hit}" for hit in hits)
    for path in sorted(SCRIPTS_DIR.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in LEGACY_SCRIPTS_ALLOWLIST:
            continue
        hits = scan_ast_sqlite_imports(path.read_text(encoding="utf-8-sig"))
        violations.extend(f"{rel}: {hit}" for hit in hits)
    assert not violations, "Importação SQLite proibida encontrada:\n" + "\n".join(violations)


def test_no_sqlite_textual_tokens_in_backend_src_and_workers():
    violations: list[str] = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        hits = scan_text_sqlite_tokens(
            path.read_text(encoding="utf-8-sig"),
            allowed_lines=ALLOWED_TEXT_LINES.get(rel),
        )
        violations.extend(f"{rel}: {hit}" for hit in hits)
    assert not violations, "Referência textual SQLite proibida em backend/src:\n" + "\n".join(violations)

    # Workers/subprocessos não podem ter sequer menção documentada.
    worker_files = [
        SRC_DIR / "horus_backend" / "api.py",
        SRC_DIR / "horus_backend" / "__main__.py",
        SRC_DIR / "horus_backend" / "config.py",
        SRC_DIR / "horus_backend" / "production.py",
        SRC_DIR / "horus_backend" / "production_worker.py",
        SRC_DIR / "horus_backend" / "transcriber.py",
        *sorted((SRC_DIR / "horus_backend" / "narration").glob("*_worker.py")),
    ]
    for path in worker_files:
        assert path.is_file(), f"Arquivo esperado ausente: {path}"
        hits = scan_text_sqlite_tokens(path.read_text(encoding="utf-8-sig"))
        assert not hits, f"Token SQLite em {path.relative_to(ROOT)}: {hits}"


def test_gate_scanners_detect_hidden_sqlite():
    for sample in (
        "import sqlite3\n",
        "from sqlite3 import connect\n",
        "import _sqlite3\n",
        "import aiosqlite\n",
        "import importlib\nimportlib.import_module('sqlite3')\n",
        "__import__('sqlite3')\n",
    ):
        assert scan_ast_sqlite_imports(sample), f"AST scanner não detectou: {sample!r}"

    for token in (
        "PRAGMA journal_mode=WAL",
        "SELECT json_extract(payload, '$.a')",
        "SELECT julianday('now')",
        "db.executescript('SELECT 1')",
        "SELECT rowid FROM jobs",
        "BEGIN IMMEDIATE",
        "INSERT OR REPLACE INTO t VALUES (1)",
        "path = 'horus.sqlite3'",
        'path = "data.db"',
        "wal = 'horus.sqlite3-wal'",
        "PRAGMA secure_delete=ON",
        "winsqlite3.dll",
        "SELECT * FROM sqlite_master",
        "SELECT last_insert_rowid()",
    ):
        assert scan_text_sqlite_tokens(token), f"Scanner textual não detectou: {token!r}"


def test_diagnostic_mode_starts_without_db_returns_503_and_creates_no_local_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ConnectionVault, "backend", lambda self: MemoryKeyring())
    token = "diagnostic-mode-test-token-0123456789"
    headers = {"Authorization": f"Bearer {token}"}
    data_dir = tmp_path / "clean-data"
    settings = Settings(
        token=token,
        data_dir=data_dir,
        factory_root=None,
        hermes_executable=None,
        hermes_config=tmp_path / "hermes.json",
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        health = client.get("/health", headers=headers)
        assert health.status_code == 200
        assert health.json()["database_id"] is None
        assert health.json()["database_state"] == "not_configured"

        db_status = client.get("/v1/database/status", headers=headers)
        assert db_status.status_code == 200
        assert db_status.json()["state"] == "not_configured"

        assert client.get("/v1/security/status", headers=headers).status_code == 200
        assert client.get("/v1/update/activity", headers=headers).status_code == 200
        assert client.get("/v1/update/health", headers=headers).status_code == 200

        media_file = tmp_path / "x.mp4"
        media_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 16)
        for method, path, body in (
            ("GET", "/v1/jobs", None),
            ("GET", "/v1/workflows", None),
            ("GET", "/v1/radar/datasets", None),
            ("GET", "/v1/notifications", None),
            ("GET", "/v1/errors", None),
            ("GET", "/v1/imports", None),
            ("POST", "/v1/media", {"path": str(media_file)}),
        ):
            resp = client.request(method, path, headers=headers, json=body)
            assert resp.status_code == 503, f"{method} {path}: {resp.status_code} {resp.text}"
            payload = resp.json()
            assert payload["detail"]["code"] == "database_unavailable"
            assert payload["detail"]["status"]["state"] == "not_configured"
            assert "message" in payload["detail"]

    if data_dir.exists():
        created_db_files = [
            p for p in data_dir.rglob("*")
            if p.is_file() and any(s in p.name.lower() for s in (".db", ".sqlite", "-wal", "-shm"))
        ]
        assert not created_db_files, f"Arquivos SQLite criados em modo diagnóstico: {created_db_files}"
