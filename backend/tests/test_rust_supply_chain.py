"""Guardas offline do Rust/Tauri; execute com unittest ou pytest, sem instalar crates."""
import copy
import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAURI = ROOT / "frontend/src-tauri"
REGISTRY = "registry+https://github.com/rust-lang/crates.io-index"
# Alterações exigem revisão explícita, inclusive features e novos plugins.
DEPENDENCIES = {
    "tauri-build": {"version": "2", "features": []},
    "tauri": {"version": "2", "features": []},
    "tauri-plugin-dialog": "2",
    "serde": {"version": "1", "features": ["derive"]},
    "serde_json": "1",
    "uuid": {"version": "1", "features": ["v4"]},
    "windows-sys": {"version": "0.61", "features": [
        "Win32_Foundation", "Win32_Security", "Win32_System_JobObjects", "Win32_System_Threading"]},
}


def validate_lock(manifest, lock):
    assert lock["version"] in (3, 4), "Formato do Cargo.lock não revisado"
    direct = dict(manifest["dependencies"])
    direct.update(manifest["build-dependencies"])
    assert set(manifest.get("target", {})) == {"cfg(windows)"}
    direct.update(manifest["target"]["cfg(windows)"]["dependencies"])
    assert direct == DEPENDENCIES, "Dependências/features Rust mudaram: revisar allowlist"
    assert not manifest.get("dev-dependencies")
    assert set(manifest["target"]["cfg(windows)"]) == {"dependencies"}
    assert not manifest.get("patch") and not manifest.get("replace") and not manifest.get("workspace")
    assert not manifest.get("features"), "Features locais precisam de revisão"
    packages = lock["package"]
    root = [p for p in packages if p["name"] == manifest["package"]["name"]]
    assert len(root) == 1 and root[0]["version"] == manifest["package"]["version"]
    assert {d.split()[0] for d in root[0]["dependencies"]} == set(direct), "Manifesto e lock divergem"
    for p in packages:
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?", p["version"])
        if p is not root[0]:
            assert p.get("source") == REGISTRY, f"{p['name']}: origem não revisada"
            assert re.fullmatch(r"[0-9a-f]{64}", p.get("checksum", "")), f"{p['name']}: checksum ausente"
        for dep in p.get("dependencies", []):
            parts = dep.split()
            candidates = [q for q in packages if q["name"] == parts[0]
                          and (len(parts) == 1 or q["version"] == parts[1])]
            assert len(candidates) == 1, f"Referência ausente/ambígua no lock: {dep}"
    for dep in root[0]["dependencies"]:
        parts = dep.split()
        p = next(p for p in packages if p["name"] == parts[0]
                 and (len(parts) == 1 or p["version"] == parts[1]))
        spec = direct[parts[0]]
        version = spec if isinstance(spec, str) else spec["version"]
        # Os ranges aprovados são caret numéricos simples (2, 1, 0.61).
        prefix = tuple(map(int, version.split(".")))
        actual = tuple(map(int, p["version"].split(".")))
        assert actual[:len(prefix)] == prefix, f"{dep}: fora do range {version}"


def validate_capability(capability):
    assert set(capability) <= {"$schema", "identifier", "description", "windows", "permissions", "local"}
    assert capability["identifier"] == "main"
    assert capability["windows"] == ["main"] and capability.get("local", True) is True
    assert capability["permissions"] == ["core:default", "dialog:allow-open", "dialog:allow-save"], \
        "Permissão nova/ampla ou escopo customizado exige revisão"


class RustSupplyChainTests(unittest.TestCase):
    def test_lock_is_tracked_and_matches_manifest(self):
        import subprocess
        subprocess.run(["git", "ls-files", "--error-unmatch", "frontend/src-tauri/Cargo.lock"],
                       cwd=ROOT, check=True, capture_output=True)
        validate_lock(tomllib.loads((TAURI / "Cargo.toml").read_text(encoding="utf-8")),
                      tomllib.loads((TAURI / "Cargo.lock").read_text(encoding="utf-8")))

    def test_only_reviewed_local_capability_exists(self):
        files = sorted(p.relative_to(TAURI).as_posix() for p in (TAURI / "capabilities").rglob("*") if p.is_file())
        self.assertEqual(files, ["capabilities/main.json"])
        self.assertFalse((TAURI / "permissions").exists(), "Permissões customizadas exigem revisão")
        validate_capability(json.loads((TAURI / files[0]).read_text(encoding="utf-8")))
        config = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
        self.assertNotIn("capabilities", config["app"]["security"], "Capabilities inline exigem revisão")

    def test_rejects_lock_drift_and_untrusted_packages(self):
        manifest = tomllib.loads((TAURI / "Cargo.toml").read_text(encoding="utf-8"))
        lock = tomllib.loads((TAURI / "Cargo.lock").read_text(encoding="utf-8"))
        for mutation in ("missing", "range", "checksum", "source", "root"):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(lock)
                package = next(p for p in bad["package"] if p["name"] == "tauri")
                if mutation == "missing":
                    bad["package"].remove(package)
                elif mutation == "range":
                    package["version"] = "3.0.0"
                elif mutation == "root":
                    next(p for p in bad["package"] if p["name"] == "horus-desktop")["version"] = "99.0.0"
                else:
                    package[mutation] = "untrusted"
                with self.assertRaises(AssertionError):
                    validate_lock(manifest, bad)

    def test_rejects_broad_permissions_and_remote_access(self):
        original = json.loads((TAURI / "capabilities/main.json").read_text(encoding="utf-8"))
        for change in ({"permissions": ["shell:default"]}, {"permissions": ["fs:default"]},
                       {"permissions": original["permissions"] + [{"identifier": "fs:allow-read-file", "allow": [{"path": "**"}]}]},
                       {"windows": ["*"]}, {"remote": {"urls": ["https://*"]}}, {"local": False}):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                validate_capability(original | change)

    def test_rejects_extra_tauri_features_or_plugins(self):
        manifest = tomllib.loads((TAURI / "Cargo.toml").read_text(encoding="utf-8"))
        lock = tomllib.loads((TAURI / "Cargo.lock").read_text(encoding="utf-8"))
        for mutation in ("feature", "plugin", "target-build"):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(manifest)
                if mutation == "feature":
                    bad["dependencies"]["tauri"]["features"].append("protocol-asset")
                elif mutation == "plugin":
                    bad["dependencies"]["tauri-plugin-shell"] = "2"
                else:
                    bad["target"]["cfg(windows)"]["build-dependencies"] = {"extra": "1"}
                with self.assertRaises(AssertionError):
                    validate_lock(bad, lock)


if __name__ == "__main__":
    unittest.main()
