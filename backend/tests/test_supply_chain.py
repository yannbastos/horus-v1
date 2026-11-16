"""Supply chain e segredos (HOR-130). Offline: só lê arquivos versionados e lockfiles; nunca instala nem acessa a rede.

Falha quando: (1) um segredo aparece em arquivo versionado ou há `.env` versionado; (2) uma dependência entra em
`pyproject.toml`/`package.json` sem estar no lock, ou o lock perde hashes/origem confiável; (3) surge um script de
instalação novo (npm `hasInstallScript`, scripts de ciclo de vida, pacote Python só-sdist que executaria build).
Para aceitar de propósito um item novo, edite a allowlist abaixo na mesma mudança e justifique na revisão.
"""
import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT, UV_LOCK = ROOT / "backend/pyproject.toml", ROOT / "backend/uv.lock"
PACKAGE_JSON, PACKAGE_LOCK = ROOT / "frontend/package.json", ROOT / "frontend/package-lock.json"

# Allowlists (vazias = nenhum caso aceito). `fsevents` é opcional e só macOS; sem efeito no Windows.
NPM_INSTALL_SCRIPT_ALLOWLIST = {"node_modules/fsevents"}
PY_SDIST_ONLY_ALLOWLIST: set[str] = set()
LIFECYCLE_SCRIPTS = ("preinstall", "install", "postinstall", "prepare", "prepublish", "prepublishOnly", "preprepare", "postprepare")

SECRET_PATTERNS = {
    "minisign_private_text": re.compile(r"untrusted comment: [^\n]*(?:encrypted )?secret key", re.I),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "provider_key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}"),
    "private_key_block": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "bearer_literal": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{24,}=*"),
    "credential_literal": re.compile(
        r"""(?i)\b(?:api[_-]?key|secret|token|password|senha)\b["']?\s*[:=]\s*["'][A-Za-z0-9/+_=-]{20,}["']"""),
}
# Heurísticas de "literal de credencial" geram falso positivo em tokens fictícios de teste; nos arquivos de teste valem
# só os formatos reais de chave (acima), que também reprovam ali.
HEURISTIC = {"bearer_literal", "credential_literal"}
TEST_LIKE = re.compile(r"(^|/)(tests?|fixtures)/|(^|/)(test_|smoke_)[^/]*$|\.(test|spec)\.[cm]?[jt]sx?$|\.test\.mjs$")
BINARY = {".png", ".ico", ".ttf", ".woff", ".woff2", ".jpg", ".jpeg", ".svg", ".wav", ".mp4", ".gz", ".zip", ".pth", ".pt", ".whl"}
SELF = "backend/tests/test_supply_chain.py"


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


# --- segredos ------------------------------------------------------------------------------------------------

def scan(text: str, relaxed: bool = False) -> list[str]:
    return [name for name, pattern in SECRET_PATTERNS.items() if not (relaxed and name in HEURISTIC) and pattern.search(text)]


def test_no_secret_in_versioned_files():
    found = []
    for rel in tracked():
        path = ROOT / rel
        if rel == SELF or path.suffix.lower() in BINARY or not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found += [f"{rel}: {name}" for name in scan(text, relaxed=bool(TEST_LIKE.search(rel)))]
    assert not found, "Possível segredo versionado (remova e rotacione a credencial):\n" + "\n".join(found)


def test_no_dotenv_or_private_key_files_are_versioned():
    bad = [p for p in tracked() if re.search(r"(^|/)\.env(\..+)?$", p) and not p.endswith((".example", ".sample", ".template"))
           or p.lower().endswith((".pem", ".key", ".p12", ".pfx"))]
    assert not bad, f"Arquivos de segredo versionados: {bad}"


def test_updater_key_material_is_ignored_and_not_hidden_as_base64():
    import base64
    for name in ['horus-updater-test.key','horus-updater-test.key.pub','tauri-updater-test.generated.json']:
        result=subprocess.run(['git','check-ignore','--no-index',name],cwd=ROOT,capture_output=True)
        assert result.returncode==0, name
    for rel in tracked():
        path=ROOT/rel
        if path.suffix.lower() in BINARY or not path.is_file() or path.stat().st_size>2_000_000:
            continue
        text=path.read_text(encoding='utf-8',errors='replace')
        for candidate in re.findall(r'[A-Za-z0-9+/]{100,}={0,2}',text):
            try: decoded=base64.b64decode(candidate,validate=True).lower()
            except ValueError: continue
            assert not (b'untrusted comment:' in decoded and b'secret key' in decoded), rel


@pytest.mark.parametrize("sample,expected", [
    ("AKIAABCDEFGHIJKLMNOP", "aws_access_key"),
    ("ghp_" + "a" * 36, "github_token"),
    ("sk-ant-" + "a" * 40, "provider_key"),
    ("-----BEGIN RSA PRIVATE KEY-----", "private_key_block"),
    ("Authorization: Bearer " + "abcdEFGH0123" * 3, "bearer_literal"),
    ('api_key = "' + "A1b2" * 6 + '"', "credential_literal"),
])
def test_secret_scanner_detects_known_shapes(sample, expected):
    assert expected in scan(sample)


@pytest.mark.parametrize("sample", ["sk-curto", "token = os.environ['X']", "Bearer {token}", 'password: "<senha>"', "api_key=abc123"])
def test_secret_scanner_ignores_placeholders(sample):
    assert scan(sample) == []


# --- Python: pyproject × uv.lock -----------------------------------------------------------------------------

def py_requirements() -> list[Requirement]:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    specs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs += extra
    return [Requirement(s) for s in specs]


def locked() -> dict[str, dict]:
    return {canonicalize_name(p["name"]): p for p in tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))["package"]}


def test_every_python_dependency_is_locked_and_satisfied():
    lock = locked()
    for req in py_requirements():
        package = lock.get(canonicalize_name(req.name))
        assert package is not None, f"{req.name} está no pyproject mas não no uv.lock (rode `uv lock`)."
        assert req.specifier.contains(Version(package["version"]), prereleases=True), \
            f"uv.lock fixa {req.name}=={package['version']}, fora de '{req.specifier}'."


def test_python_lock_pins_exact_versions_with_hashes_from_pypi_only():
    for name, package in locked().items():
        if name == "horus-backend":
            continue
        assert package["source"] == {"registry": "https://pypi.org/simple"}, f"{name}: origem não confiável {package['source']}"
        files = ([package["sdist"]] if "sdist" in package else []) + package.get("wheels", [])
        assert files, f"{name}: sem artefatos no lock"
        for item in files:
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", item["hash"]), f"{name}: hash ausente/inválido"
            assert item["url"].startswith("https://files.pythonhosted.org/"), f"{name}: URL fora do PyPI"
        assert re.fullmatch(r"\d+(\.\d+)*([.-]?(a|b|rc|post|dev)\d*)?", package["version"]), f"{name}: versão não exata"


def test_no_new_python_package_would_build_from_source():
    sdist_only = {n for n, p in locked().items() if n != "horus-backend" and "sdist" in p and not p.get("wheels")}
    assert sdist_only <= PY_SDIST_ONLY_ALLOWLIST, f"Pacotes só-sdist executariam build/setup.py na instalação: {sorted(sdist_only - PY_SDIST_ONLY_ALLOWLIST)}"


def test_python_build_backend_is_unchanged():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["build-system"]["build-backend"] == "hatchling.build" and data["build-system"]["requires"] == ["hatchling"]
    assert not (ROOT / "backend/setup.py").exists()


# --- npm: package.json × package-lock.json -------------------------------------------------------------------

def package_json() -> dict:
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))


def package_lock() -> dict:
    return json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))


def test_every_npm_dependency_is_in_the_lock_with_the_same_range():
    manifest, lock = package_json(), package_lock()
    root = lock["packages"][""]
    for group in ("dependencies", "devDependencies", "optionalDependencies"):
        assert manifest.get(group, {}) == root.get(group, {}), f"{group} diverge do package-lock (rode `npm install --package-lock-only`)."
        for name in manifest.get(group, {}):
            assert f"node_modules/{name}" in lock["packages"], f"{name} sem entrada no lock"
    assert lock["lockfileVersion"] >= 3


def test_npm_lock_pins_every_package_with_integrity_from_the_registry():
    bad = []
    for key, item in package_lock()["packages"].items():
        if not key or item.get("link"):
            continue
        if not re.fullmatch(r"sha(?:256|384|512)-[A-Za-z0-9+/]+=*", item.get("integrity", "")):
            bad.append(f"{key}: sem integrity")
        if not item.get("resolved", "").startswith("https://registry.npmjs.org/"):
            bad.append(f"{key}: origem fora do registro npm")
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", item.get("version", "")):
            bad.append(f"{key}: versão não exata")
    assert not bad, bad


def test_npm_registry_is_https_npmjs_only():
    rc = (ROOT / "frontend/.npmrc").read_text(encoding="utf-8").split()
    assert rc == ["registry=https://registry.npmjs.org"], "Mudança de .npmrc: revise registro/escopos/tokens (nunca versione token)."


def test_no_new_npm_install_scripts():
    manifest = package_json()
    assert not [s for s in LIFECYCLE_SCRIPTS if s in manifest.get("scripts", {})], "Script de ciclo de vida no package.json"
    flagged = {k for k, v in package_lock()["packages"].items() if v.get("hasInstallScript")}
    assert flagged <= NPM_INSTALL_SCRIPT_ALLOWLIST, f"Dependência nova com script de instalação: {sorted(flagged - NPM_INSTALL_SCRIPT_ALLOWLIST)}"
    assert all(v.get("optional") for k, v in package_lock()["packages"].items() if k in NPM_INSTALL_SCRIPT_ALLOWLIST)


def test_dependency_checks_detect_violations():
    """Garante que as regras não são vácuas: um manifesto sem lock ou com versão solta seria reprovado."""
    req = Requirement("novo-pacote>=1")
    assert canonicalize_name(req.name) not in locked()
    assert not re.fullmatch(r"\d+\.\d+\.\d+", "^1.2.3")


def test_test_like_files_still_fail_on_real_key_shapes_but_not_on_fake_tokens():
    assert scan('TOKEN = "test-only-connection-token-0123456789"', relaxed=True) == []
    assert "provider_key" in scan("sk-ant-" + "a" * 40, relaxed=True)
    assert TEST_LIKE.search("backend/tests/test_api.py") and TEST_LIKE.search("frontend/tests/a.spec.ts")
    assert not TEST_LIKE.search("backend/src/horus_backend/config.py")
