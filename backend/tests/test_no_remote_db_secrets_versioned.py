"""HOR-137 Fase A: nenhum segredo de Postgres/Supabase em arquivo versionado nem em pacotes de interface já gerados.

Rede de segurança para a persistência remota (docs/seguranca-persistencia-remota.md, requisito R1): string de conexão com
senha, chave `sb_secret_`, JWT de papel privilegiado (`service_role`/`supabase_admin`/`postgres`) e `SUPABASE_SERVICE_ROLE_KEY`
com valor. Chaves `anon`/`sb_publishable_` são públicas por desenho e não são tratadas aqui. Os valores abaixo são fictícios.
"""
import base64
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONNECTION = re.compile(rb"postgres(?:ql)?://[^\s:/@'\"]+:[^\s@'\"]{3,}@[^\s'\"]+", re.I)
SECRET_KEY = re.compile(rb"\bsb_secret_[A-Za-z0-9_-]{10,}")
SERVICE_VAR = re.compile(rb"(?i)SUPABASE_SERVICE_ROLE_KEY[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9._-]{20,}")
JWT = re.compile(rb"\beyJ[A-Za-z0-9_-]{8,}\.(eyJ[A-Za-z0-9_-]{8,})\.[A-Za-z0-9_-]{8,}")
PRIVILEGED = {"service_role", "supabase_admin", "postgres", "supabase_auth_admin"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".wav", ".onnx", ".zip", ".lock"}


def privileged_jwt(raw: bytes) -> bool:
    for match in JWT.finditer(raw):
        payload = match.group(1) + b"=" * (-len(match.group(1)) % 4)
        try:
            claims = json.loads(base64.urlsafe_b64decode(payload))
        except ValueError:
            continue
        if isinstance(claims, dict) and claims.get("role") in PRIVILEGED:
            return True
    return False


def findings(name: str, raw: bytes) -> list[str]:
    found = []
    if CONNECTION.search(raw):
        found.append(f"{name}: string de conexão Postgres com senha")
    if SECRET_KEY.search(raw):
        found.append(f"{name}: chave sb_secret_")
    if SERVICE_VAR.search(raw):
        found.append(f"{name}: SUPABASE_SERVICE_ROLE_KEY com valor")
    if privileged_jwt(raw):
        found.append(f"{name}: JWT de papel privilegiado")
    return found


def b64(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def test_scanner_detects_each_secret_shape_and_ignores_public_values():
    header, tail = b64({"alg": "HS256"}), "assinatura-ficticia"
    jwt = lambda role: f"{header}.{b64({'role': role})}.{tail}"
    assert findings("a", b"DATABASE_URL=postgresql://postgres:senha-ficticia@db.exemplo.invalid:5432/postgres")
    assert findings("b", b"key = sb_secret_ficticia1234567890")
    assert findings("c", b'SUPABASE_SERVICE_ROLE_KEY="' + b"x" * 30 + b'"')
    assert findings("d", jwt("service_role").encode())
    # públicos por desenho / sem segredo
    assert not findings("e", jwt("anon").encode())
    assert not findings("f", b"postgresql://localhost/horus")                       # sem senha
    assert not findings("g", b"SUPABASE_SERVICE_ROLE_KEY=  # preencher fora do repo")
    assert not findings("h", b"sb_publishable_ficticia1234567890")


def test_no_versioned_file_or_built_ui_bundle_contains_remote_database_secrets():
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    paths = [ROOT / name.decode() for name in tracked if name]
    for built in ("frontend/dist", "frontend/src-tauri/target/release/bundle"):
        folder = ROOT / built
        if folder.is_dir():
            paths += [p for p in folder.rglob("*") if p.is_file()]
    found = []
    for path in paths:
        if path.suffix.lower() in SKIP_SUFFIX or not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
            continue
        if path.name == Path(__file__).name:        # este arquivo contém exemplos fictícios dos formatos
            continue
        found += findings(path.relative_to(ROOT).as_posix(), path.read_bytes())
    assert found == []
