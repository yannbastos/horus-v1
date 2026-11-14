"""HOR-117: sem telemetria (backend, frontend, Tauri, build) e exportação verificável dos dados locais. Offline."""
import asyncio
import json
import os
import re
import sqlite3
import tomllib
import zipfile
from pathlib import Path

import pytest

from horus_backend.local_data_export import export_local_data, verify_export
from horus_backend.migrations import migrate
from horus_backend.processes import TELEMETRY_OFF, OwnedProcess, run_sync
from horus_backend.storage import Store

ROOT = Path(__file__).resolve().parents[2]
PY = __import__("sys").executable

# Nomes de bibliotecas/serviços de telemetria, analytics, relatório de falhas ou atualização automática.
TELEMETRY_NAMES = re.compile(
    r"(?i)(sentry|posthog|(?<![a-z])segment(?![a-z])|mixpanel|amplitude|datadog|newrelic|new-relic|bugsnag|rollbar|logrocket|fullstory|hotjar|"
    r"heap-?analytics|rudder|plausible|matomo|appinsights|applicationinsights|opentelemetry|google-?analytics|gtag|"
    r"vercel/analytics|telemetry|analytics|tauri-plugin-updater|plugin-updater|electron-updater|crashpad-upload)")
TELEMETRY_HOSTS = re.compile(
    r"(?i)(google-analytics\.com|googletagmanager\.com|sentry\.io|posthog\.com|segment\.(io|com)|mixpanel\.com|amplitude\.com|"
    r"datadoghq\.|hotjar\.com|clarity\.ms|bugsnag\.com|rollbar\.com|logrocket\.|fullstory\.com|plausible\.io|stats\.g\.doubleclick)")
# Pacotes legítimos cujo nome casa a expressão: avaliados e aceitos explicitamente (nenhum envia dados do Hórus).
NAME_ALLOWLIST = {"opentelemetry-api"}  # vazio de fato; exemplo de como aceitar com justificativa


def lock_names():
    names = set()
    for item in tomllib.loads((ROOT / "backend/uv.lock").read_text(encoding="utf-8"))["package"]:
        names.add(("python", item["name"]))
    for key in json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))["packages"]:
        if key:
            names.add(("npm", key.split("node_modules/")[-1]))
    for item in tomllib.loads((ROOT / "frontend/src-tauri/Cargo.lock").read_text(encoding="utf-8"))["package"]:
        names.add(("cargo", item["name"]))
    return names


# --- (1) inventário de telemetria -----------------------------------------------------------------------------

def test_no_dependency_in_any_lockfile_is_a_telemetry_or_auto_update_library():
    found = sorted(f"{eco}:{name}" for eco, name in lock_names() if TELEMETRY_NAMES.search(name) and name not in NAME_ALLOWLIST)
    assert not found, f"Dependência de telemetria/atualização automática nos locks (exige decisão do usuário): {found}"


def test_tauri_has_no_updater_http_or_shell_plugin_and_only_loopback_connect_src():
    cargo = tomllib.loads((ROOT / "frontend/src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    assert set(cargo["dependencies"]) == {"tauri", "tauri-plugin-dialog", "serde", "serde_json", "uuid"}, "dependência Tauri nova"
    assert cargo["dependencies"]["tauri"]["features"] == []
    conf = json.loads((ROOT / "frontend/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    assert "updater" not in json.dumps(conf).lower() and "plugins" not in conf or "updater" not in conf.get("plugins", {})
    csp = conf["app"]["security"]["csp"]
    connect = re.search(r"connect-src ([^;]*)", csp).group(1).split()
    assert all(re.fullmatch(r"ipc:|http://ipc\.localhost|http://(127\.0\.0\.1|localhost)(:\*|:\d+)?", item) for item in connect), connect
    caps = json.loads((ROOT / "frontend/src-tauri/capabilities/main.json").read_text(encoding="utf-8"))
    assert set(caps["permissions"]) == {"core:default", "dialog:allow-open", "dialog:allow-save"}


def test_versioned_source_has_no_telemetry_hosts_or_sdk_imports():
    import subprocess
    files = subprocess.run(["git", "ls-files", "-z", "backend/src", "frontend/src", "frontend/index.html", "frontend/vite.config.ts",
                            "frontend/src-tauri/src", "frontend/src-tauri/tauri.conf.json", "frontend/package.json"],
                           cwd=ROOT, capture_output=True, check=True).stdout.decode().split("\0")
    hits = []
    for rel in filter(None, files):
        path = ROOT / rel
        if path.suffix in {".png", ".ico", ".ttf", ".woff2", ".svg"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if TELEMETRY_HOSTS.search(text):
            hits.append(f"{rel}: host de telemetria")
        if re.search(r"""(?m)^\s*(?:import|from)\s.*['"]?(?:@sentry|posthog|mixpanel|@segment|amplitude|@datadog)""", text):
            hits.append(f"{rel}: SDK de telemetria")
    assert not hits, hits


def test_every_subprocess_starts_with_telemetry_opt_outs(monkeypatch):
    monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "0")   # um ambiente do operador não os reabilita via padrão
    code = "import os,json;print(json.dumps({k: os.environ.get(k) for k in %r}))" % sorted(TELEMETRY_OFF)
    out = json.loads(run_sync([PY, "-I", "-c", code], timeout=20))
    assert out == TELEMETRY_OFF

    async def owned(env):
        process = await OwnedProcess.start([PY, "-I", "-c", code], **({} if env is None else {"env": env}))
        data, _ = await process.communicate(timeout=20)
        await process.stop()
        process.close()
        return json.loads(data)
    assert asyncio.run(owned(None)) == TELEMETRY_OFF
    assert asyncio.run(owned({"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")})) == TELEMETRY_OFF


def test_model_workers_also_force_offline_and_no_telemetry_in_their_own_env():
    for rel in ("backend/src/horus_backend/narration/kokoro.py", "backend/src/horus_backend/speech_alignment.py"):
        text = (ROOT / rel).read_text(encoding="utf-8-sig")
        assert "HF_HUB_OFFLINE" in text and "HF_HUB_DISABLE_TELEMETRY" in text, rel


def test_storybook_telemetry_is_disabled_when_the_sibling_repo_is_available():
    config = os.environ.get("HORUS_STORYBOOK_DIR")
    path = Path(config) / ".storybook/main.ts" if config else ROOT.parent / "horus-storybook/.storybook/main.ts"
    if not path.is_file():
        pytest.skip("horus-storybook indisponível (defina HORUS_STORYBOOK_DIR para conferir)")
    assert re.search(r"disableTelemetry\s*:\s*true", path.read_text(encoding="utf-8"))


# --- (2) exportação dos dados locais --------------------------------------------------------------------------

@pytest.fixture
def data_dir(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    store = Store(root / "horus.sqlite3")
    migrate(root / "horus.sqlite3")
    with store.connect() as db:
        db.execute("INSERT INTO media VALUES('m1','C:/x/a.mp4','a.mp4',1,1,'2026-11-14T00:00:00+00:00')")
    (root / "radar").mkdir()
    with sqlite3.connect(root / "radar" / "radar-youtube.sqlite3") as db:
        db.execute("CREATE TABLE t(x)")
        db.execute("INSERT INTO t VALUES(1)")
    (root / "narration-preference.json").write_text('{"provider":"kokoro","voice_id":"pf_dora"}', encoding="utf-8")
    production = root / "productions" / "p1"
    production.mkdir(parents=True)
    (production / "asset-provenance.json").write_text('{"schema_version":1,"assets":[]}', encoding="utf-8")
    (production / "video.mp4").write_bytes(b"x" * 1000)
    (production / "scene-1.png").write_bytes(b"png")
    (root / "integrations").mkdir()
    (root / "integrations" / "hermes.json").write_text('{"token":"TOKEN-SECRETO-DO-HERMES-0123456789abcdef"}', encoding="utf-8")
    (root / "models").mkdir()
    (root / "models" / "w.bin").write_bytes(b"m" * 100)
    return root


def names(package):
    with zipfile.ZipFile(package) as archive:
        return set(archive.namelist())


def test_export_contains_database_settings_and_manifests_but_no_secrets_models_or_media(data_dir, tmp_path):
    package = tmp_path / "saida" / "dados.zip"
    package.parent.mkdir()
    report = export_local_data(data_dir, package)
    assert report["ok"] and verify_export(package) == {"ok": True, "errors": []}
    assert names(package) == {"database/horus.sqlite3", "database/radar-youtube.sqlite3", "settings/narration-preference.json",
                              "productions/p1/asset-provenance.json", "manifest.json"}
    blob = package.read_bytes()
    assert b"TOKEN-SECRETO" not in blob and b"w.bin" not in blob
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["contains_private_data"] is True
        assert {e["path"] for e in manifest["excluded"]} == {"integrations/", "models/"}
        assert manifest["databases"]["horus.sqlite3"]["tables"]["media"] == 1


def test_export_is_consistent_with_a_live_wal_database_and_never_changes_the_source(data_dir, tmp_path):
    before = {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file() and p.suffix != ".sqlite3"}
    live = sqlite3.connect(data_dir / "horus.sqlite3")
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("INSERT INTO media VALUES('m2','C:/x/b.mp4','b.mp4',1,1,'2026-11-14T00:00:00+00:00')")
    live.commit()   # linha ainda só no WAL
    package = tmp_path / "dados.zip"
    try:
        export_local_data(data_dir, package)
    finally:
        live.close()
    with zipfile.ZipFile(package) as archive:
        copy = tmp_path / "copy.sqlite3"
        copy.write_bytes(archive.read("database/horus.sqlite3"))
    with sqlite3.connect(copy) as db:
        assert db.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 2
    assert {p: p.read_bytes() for p in before} == before


def test_export_refuses_existing_destination_destination_inside_data_dir_and_missing_database(data_dir, tmp_path):
    existing = tmp_path / "existe.zip"
    existing.write_bytes(b"nao-sobrescrever")
    with pytest.raises(ValueError):
        export_local_data(data_dir, existing)
    assert existing.read_bytes() == b"nao-sobrescrever"
    with pytest.raises(ValueError):
        export_local_data(data_dir, data_dir / "dentro.zip")
    with pytest.raises(ValueError):
        export_local_data(tmp_path / "vazia", tmp_path / "x.zip")


def test_export_refuses_a_corrupt_database_and_creates_nothing(data_dir, tmp_path):
    (data_dir / "horus.sqlite3").write_bytes(b"isto nao e sqlite" * 100)
    with pytest.raises((ValueError, sqlite3.Error)):
        export_local_data(data_dir, tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()


def test_credential_shaped_or_invalid_json_is_excluded_from_manifests(data_dir, tmp_path):
    production = data_dir / "productions" / "p1"
    (production / "vazou.json").write_text('{"api_key": "' + "A1b2" * 8 + '"}', encoding="utf-8")
    (production / "quebrado.json").write_text("{nao json", encoding="utf-8")
    package = tmp_path / "d.zip"
    report = export_local_data(data_dir, package)
    assert "productions/p1/vazou.json" not in names(package) and "productions/p1/quebrado.json" not in names(package)
    assert {"productions/p1/vazou.json", "productions/p1/quebrado.json"} <= {e["path"] for e in report["excluded"]}


def test_links_are_never_followed(data_dir, tmp_path):
    outside = tmp_path / "fora.json"
    outside.write_text('{"x":1}', encoding="utf-8")
    try:
        (data_dir / "productions" / "p1" / "atalho.json").symlink_to(outside)
        (data_dir / "productions" / "p2").symlink_to(tmp_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlink indisponível sem privilégio")
    package = tmp_path / "d.zip"
    export_local_data(data_dir, package)
    assert not any("atalho" in n or "p2" in n for n in names(package))


def rewrite(package, target, change):
    out = package.with_name("adulterado.zip")
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(out, "w") as dest:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == target:
                data = change(data)
            if data is not None:
                dest.writestr(item.filename, data)
    return out


@pytest.mark.parametrize("target,change,reason", [
    ("settings/narration-preference.json", lambda d: d + b" ", "hash"),
    ("database/horus.sqlite3", lambda d: d[:-100] + bytes(b ^ 0xFF for b in d[-100:]), "hash"),
    ("productions/p1/asset-provenance.json", lambda d: None, "manifesto"),
])
def test_verify_detects_tampering(data_dir, tmp_path, target, change, reason):
    package = tmp_path / "d.zip"
    export_local_data(data_dir, package)
    result = verify_export(rewrite(package, target, change))
    assert result["ok"] is False and result["errors"]


def test_verify_refuses_unsafe_names_and_non_packages(data_dir, tmp_path):
    bad = tmp_path / "zipslip.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../fora.txt", "x")
        archive.writestr("manifest.json", "{}")
    assert verify_export(bad)["ok"] is False
    junk = tmp_path / "lixo.zip"
    junk.write_bytes(b"nao e zip")
    assert verify_export(junk)["ok"] is False
    assert verify_export(tmp_path / "ausente.zip")["ok"] is False


def test_cli_export_and_verify(data_dir, tmp_path, capsys):
    from horus_backend.local_data_export import main
    package = tmp_path / "cli.zip"
    assert main(["export", str(data_dir), "--to", str(package)]) == 0
    assert main(["verify", str(package)]) == 0
    assert main(["export", str(data_dir), "--to", str(package)]) == 1   # não sobrescreve
    capsys.readouterr()
