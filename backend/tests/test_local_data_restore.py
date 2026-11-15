"""HOR-093: restauração do pacote de dados locais numa pasta nova, com prova ponta a ponta. Sem rede nem dados reais."""
import hashlib
import json
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from test_workflows import settings, client_for  # noqa: F401
from test_production import script_for, start, finished
from horus_backend.local_data_export import export_local_data, main, restore_local_data, verify_export
from horus_backend.migrations import SCHEMA_VERSION


def tree(root: Path):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*"))


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source(settings):
    """Ambiente sintético: produção simulada concluída, preferência de narração, segredo do Hermes e modelos."""
    with client_for(settings) as client:
        record = finished(client, start(client, script_for(client)).json())
    assert record["status"] == "succeeded"
    root = settings.data_dir
    (root / "narration-preference.json").write_text('{"provider":"kokoro","voice_id":"pf_dora"}', encoding="utf-8")
    (root / "integrations").mkdir(exist_ok=True)
    (root / "integrations" / "hermes.json").write_text('{"token":"TOKEN-SECRETO-DO-HERMES-0123456789abcdef"}', encoding="utf-8")
    return settings, record


def package_of(source_settings, tmp_path):
    package = tmp_path / "pacote.zip"
    export_local_data(source_settings.data_dir, package)
    return package


def test_end_to_end_restore_boots_backend_with_production_preferences_and_files(source, tmp_path):
    settings, record = source
    package = package_of(settings, tmp_path)
    destination = tmp_path / "restaurado"
    report = restore_local_data(package, destination)
    assert report["ok"] and report["database_version"] == SCHEMA_VERSION
    # credenciais nunca vêm no pacote nem aparecem no destino
    assert not (destination / "integrations").exists() and "TOKEN-SECRETO" not in b"".join(p.read_bytes() for p in destination.rglob("*") if p.is_file()).decode("latin-1")
    # arquivos de produção e preferências idênticos aos da origem
    for relative in tree(destination):
        restored = destination / relative
        if restored.suffix == ".json":
            assert sha(restored) == sha(settings.data_dir / relative), relative
    assert (destination / "narration-preference.json").read_text(encoding="utf-8") == '{"provider":"kokoro","voice_id":"pf_dora"}'
    assert not [p for p in tmp_path.iterdir() if ".restore-" in p.name]   # pasta temporária removida
    # o backend sobe apontando para a pasta restaurada e enxerga a produção íntegra
    with client_for(replace(settings, data_dir=destination)) as client:
        restored = client.get("/v1/productions/" + record["id"])
        assert restored.status_code == 200
        assert restored.json() == record
        assert client.get("/v1/narration/preference").json()["voice_id"] == "pf_dora"


def test_restores_into_an_existing_empty_folder_and_cli_works(source, tmp_path, capsys):
    settings, _ = source
    package = package_of(settings, tmp_path)
    empty = tmp_path / "vazia"
    empty.mkdir()
    assert main(["restore", str(package), "--to", str(empty)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] and (empty / "horus.sqlite3").is_file()
    assert main(["restore", str(package), "--to", str(tmp_path / "x")]) == 0
    with pytest.raises(SystemExit):
        main(["restore", str(package)])


def test_refuses_non_empty_existing_file_link_and_missing_parent_without_touching_anything(source, tmp_path):
    settings, _ = source
    package = package_of(settings, tmp_path)
    busy = tmp_path / "ocupada"
    busy.mkdir()
    (busy / "meu-dado.txt").write_text("importante", encoding="utf-8")
    with pytest.raises(ValueError, match="nova ou vazia"):
        restore_local_data(package, busy)
    assert tree(busy) == ["meu-dado.txt"] and (busy / "meu-dado.txt").read_text(encoding="utf-8") == "importante"
    afile = tmp_path / "arquivo"
    afile.write_text("x")
    with pytest.raises(ValueError, match="nova ou vazia"):
        restore_local_data(package, afile)
    with pytest.raises(ValueError, match="não existe"):
        restore_local_data(package, tmp_path / "inexistente" / "destino")
    # o destino do banco operacional atual também é recusado
    with pytest.raises(ValueError, match="nova ou vazia"):
        restore_local_data(package, settings.data_dir)


def rewrite(package, target, change):
    with zipfile.ZipFile(package) as archive:
        entries = {i.filename: archive.read(i.filename) for i in archive.infolist()}
    entries = change(entries)
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w") as out:
        for name, data in entries.items():
            out.writestr(name, data)
    return target


def refuse(package, destination, match=None):
    with pytest.raises(ValueError, match=match):
        restore_local_data(package, destination)
    assert not destination.exists() and not [p for p in destination.parent.iterdir() if ".restore-" in p.name]


def test_hostile_or_tampered_packages_are_refused_before_writing(source, tmp_path):
    settings, _ = source
    package = package_of(settings, tmp_path)

    def tamper(entries):
        entries["database/horus.sqlite3"] = entries["database/horus.sqlite3"][:-10] + b"0123456789"
        return entries
    refuse(rewrite(package, tmp_path / "t1.zip", tamper), tmp_path / "d1", "não verificado")

    def traversal(entries):
        entries["../fora.json"] = b"{}"
        return entries
    refuse(rewrite(package, tmp_path / "t2.zip", traversal), tmp_path / "d2", "não verificado")

    def foreign(entries):   # arquivo fora do formato, mesmo com manifesto coerente
        manifest = json.loads(entries["manifest.json"])
        entries["settings/integrations.json"] = b"{}"
        manifest["files"].append({"name": "settings/integrations.json", "size": 2, "sha256": hashlib.sha256(b"{}").hexdigest()})
        entries["manifest.json"] = json.dumps(manifest).encode()
        return entries
    refuse(rewrite(package, tmp_path / "t3.zip", foreign), tmp_path / "d3", "fora do formato")

    def secret(entries):   # credencial plantada num manifesto de produção, com hash coerente
        manifest = json.loads(entries["manifest.json"])
        data = b'{"token":"TOKEN-SECRETO-DO-HERMES-0123456789abcdef"}'
        entries["productions/p1/leak.json"] = data
        manifest["files"].append({"name": "productions/p1/leak.json", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        entries["manifest.json"] = json.dumps(manifest).encode()
        return entries
    refuse(rewrite(package, tmp_path / "t4.zip", secret), tmp_path / "d4", "credencial")
    notzip = tmp_path / "nao.zip"
    notzip.write_bytes(b"isto nao e um zip")
    refuse(notzip, tmp_path / "d5", "não verificado")


def test_newer_database_schema_is_refused_with_clear_message(source, tmp_path):
    settings, _ = source
    package = package_of(settings, tmp_path)

    def newer(entries):
        path = tmp_path / "novo.sqlite3"
        path.write_bytes(entries["database/horus.sqlite3"])
        with sqlite3.connect(path) as db:
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        data = path.read_bytes()
        manifest = json.loads(entries["manifest.json"])
        for item in manifest["files"]:
            if item["name"] == "database/horus.sqlite3":
                item.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        manifest["databases"]["horus.sqlite3"]["user_version"] = SCHEMA_VERSION + 1
        entries["database/horus.sqlite3"], entries["manifest.json"] = data, json.dumps(manifest).encode()
        return entries
    newer_package = rewrite(package, tmp_path / "novo.zip", newer)
    assert verify_export(newer_package)["ok"]
    refuse(newer_package, tmp_path / "dn", "versão mais nova")


def test_failure_midway_leaves_no_partial_destination(source, tmp_path, monkeypatch):
    settings, _ = source
    package = package_of(settings, tmp_path)
    import horus_backend.local_data_export as module
    monkeypatch.setattr(module.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("falha simulada")))
    with pytest.raises(OSError):
        restore_local_data(package, tmp_path / "meio")
    assert not (tmp_path / "meio").exists() and not [p for p in tmp_path.iterdir() if ".restore-" in p.name]
