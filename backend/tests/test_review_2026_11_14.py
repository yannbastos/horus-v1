"""Revisão adversarial 14/11/2026: exportação de dados locais, run_sync e política de caminhos. Só diretórios temporários."""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from horus_backend import local_data_export as lde
from horus_backend.file_access import PathNotAllowed, client_path
from horus_backend.local_data_export import export_local_data, verify_export
from horus_backend.processes import run_sync
from support_a import client_for, settings  # noqa: F401


@pytest.fixture
def pg_workspace(settings):
    with client_for(settings) as client:
        root = settings.data_dir
        production = root / "productions" / "p1"
        production.mkdir(parents=True, exist_ok=True)
        (production / "asset-provenance.json").write_text('{"schema_version":1,"assets":[]}', encoding="utf-8")
        yield root, client.app.state.store


def names(package):
    with zipfile.ZipFile(package) as archive:
        return set(archive.namelist())


def test_json_that_is_a_link_is_never_exported(pg_workspace, tmp_path, monkeypatch):
    data_dir, store = pg_workspace
    outside = tmp_path / "particular.json"
    outside.write_text('{"nota": "conteudo-particular-do-usuario"}', encoding="utf-8")
    link = data_dir / "productions" / "p1" / "atalho.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        link.write_bytes(outside.read_bytes())      # sem privilégio de symlink: simula o link pelo detector
        real = lde._is_link
        monkeypatch.setattr(lde, "_is_link", lambda p: p.name == "atalho.json" or real(p))
    package = tmp_path / "d.zip"
    export_local_data(data_dir, package, store=store)
    assert "local_files/productions/p1/atalho.json" not in names(package) and b"conteudo-particular" not in package.read_bytes()
    assert "local_files/productions/p1/asset-provenance.json" in names(package)


def test_exported_manifest_is_exactly_what_was_read_even_if_the_file_changes_meanwhile(pg_workspace, tmp_path, monkeypatch):
    data_dir, store = pg_workspace
    target = data_dir / "productions" / "p1" / "asset-provenance.json"
    original = target.read_bytes()
    real_loads = lde.json.loads
    state = {"done": False}

    def loads(raw, *a, **k):
        if not state["done"] and raw == original:
            state["done"] = True
            target.write_text('{"trocado": "depois da leitura"}', encoding="utf-8")   # alteração concorrente
        return real_loads(raw, *a, **k)
    monkeypatch.setattr(lde.json, "loads", loads)
    package = tmp_path / "d.zip"
    export_local_data(data_dir, package, store=store)
    with zipfile.ZipFile(package) as archive:
        assert archive.read("local_files/productions/p1/asset-provenance.json") == original
    assert verify_export(package)["ok"] is True


def test_failed_export_leaves_no_partial_package(pg_workspace, tmp_path, monkeypatch):
    data_dir, store = pg_workspace
    def boom(self, *a, **k):
        raise OSError("disco cheio")
    monkeypatch.setattr(zipfile.ZipFile, "writestr", boom)
    package = tmp_path / "d.zip"
    with pytest.raises(OSError):
        export_local_data(data_dir, package, store=store)
    assert not package.exists()


def test_verify_streams_members_and_rejects_lying_sizes(pg_workspace, tmp_path, monkeypatch):
    data_dir, store = pg_workspace
    package = tmp_path / "d.zip"
    export_local_data(data_dir, package, store=store)
    real_read = zipfile.ZipFile.read

    def only_manifest(self, name, *a, **k):
        assert name == "manifest.json", f"verify leu {name} inteiro em memória"
        return real_read(self, name, *a, **k)
    monkeypatch.setattr(zipfile.ZipFile, "read", only_manifest)
    assert verify_export(package)["ok"] is True
    monkeypatch.undo()
    # manifesto declara tamanho menor que o membro real
    liar = tmp_path / "liar.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(liar, "w") as dest:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "manifest.json":
                manifest = json.loads(data)
                for f in manifest["files"]:
                    if f["name"] == "local_files/productions/p1/asset-provenance.json":
                        f["size"] = 10
                data = json.dumps(manifest).encode()
            dest.writestr(item.filename, data)
    result = verify_export(liar)
    assert result["ok"] is False and any("tamanho" in e for e in result["errors"])


def test_run_sync_timeout_closes_the_input_pipe(monkeypatch):
    seen = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            seen.append(self)
    monkeypatch.setattr("horus_backend.processes.subprocess.Popen", Spy)
    with pytest.raises(subprocess.TimeoutExpired):
        run_sync([sys.executable, "-I", "-c", "import time; time.sleep(60)"], timeout=1, input_text="x")
    assert seen[0].stdin.closed and seen[0].poll() is not None


# --- política de caminhos: bypass por nome curto 8.3, caixa e junção (Windows) ------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="8.3/junção são do Windows")
def test_protected_directory_cannot_be_reached_by_case_short_name_or_junction(tmp_path):
    import ctypes
    protected = tmp_path / "Pasta de Dados Longa"
    (protected / "sub").mkdir(parents=True)
    variants = [protected / "x.txt", Path(str(protected).upper()) / "x.txt", Path(str(protected).lower()) / "sub" / "novo" / "x.txt"]
    buffer = ctypes.create_unicode_buffer(500)
    if ctypes.windll.kernel32.GetShortPathNameW(str(protected), buffer, 500):
        variants.append(Path(buffer.value) / "sub" / "x.txt")
    junction = tmp_path / "atalho"
    if subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(protected)], capture_output=True).returncode == 0:
        variants += [junction / "x.txt", junction / "sub" / "novo" / "x.txt"]
    for candidate in variants:
        with pytest.raises(PathNotAllowed):
            client_path(str(candidate), protected=(protected,))
    assert client_path(str(tmp_path / "fora.txt"), protected=(protected,)).name == "fora.txt"
