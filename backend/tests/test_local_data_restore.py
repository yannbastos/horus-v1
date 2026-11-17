"""HOR-093: restauração do pacote de dados locais v2 numa pasta nova e banco PG isolado. Sem rede nem dados reais."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.local_data_export import export_local_data, main, restore_local_data, verify_export
from support_d import auth_headers, create_simulated_production, isolated_empty_pg_db


def tree(root: Path):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*"))


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source(pg_client, pg_app, pg_app_settings):
    """Ambiente sintético: produção simulada concluída em PG, manifesto local e segredo do Hermes."""
    record, _ = create_simulated_production(pg_client)
    assert record["status"] == "succeeded"
    root = pg_app_settings.data_dir
    prod_dir = root / "productions" / record["id"]
    prod_dir.mkdir(parents=True, exist_ok=True)
    (prod_dir / "asset-provenance.json").write_text('{"schema_version":1,"assets":[]}', encoding="utf-8")
    (root / "integrations").mkdir(exist_ok=True)
    (root / "integrations" / "hermes.json").write_text('{"token":"TOKEN-SECRETO-DO-HERMES-0123456789abcdef"}', encoding="utf-8")
    return pg_app_settings, pg_app, pg_client, record


def package_of(source_settings, app, tmp_path):
    package = tmp_path / "pacote.zip"
    export_local_data(source_settings.data_dir, package, store=app.state.store)
    return package


def test_end_to_end_restore_boots_backend_with_production_preferences_and_files(source, pg_app_cluster, tmp_path):
    settings, app, client, record = source
    package = package_of(settings, app, tmp_path)
    destination = tmp_path / "restaurado"

    with isolated_empty_pg_db(pg_app_cluster) as (admin, _):
        report = restore_local_data(package, destination, target_conn=admin)
        assert report["ok"] and report["schema_version"] == EXPECTED_SCHEMA_VERSION
        assert report["pg_restored"] is True and report["pg_tables"]["productions"] >= 1
        row = admin.execute(
            "SELECT id, script_id, mode FROM horus.productions WHERE id = %s",
            (record["id"],),
        ).fetchone()
        assert row == (record["id"], record["script_id"], "simulated")

    # credenciais nunca vêm no pacote nem aparecem no destino
    assert not (destination / "integrations").exists()
    assert "TOKEN-SECRETO" not in b"".join(p.read_bytes() for p in destination.rglob("*") if p.is_file()).decode("latin-1")
    # arquivos de produção e preferências idênticos aos da origem
    for relative in tree(destination):
        restored = destination / relative
        if restored.suffix == ".json":
            assert sha(restored) == sha(settings.data_dir / relative), relative
    assert (destination / "productions" / record["id"] / "asset-provenance.json").is_file()
    assert not [p for p in tmp_path.iterdir() if ".restore-" in p.name]


def test_restores_into_an_existing_empty_folder_and_cli_works(source, tmp_path, capsys):
    settings, app, _, record = source
    package = package_of(settings, app, tmp_path)
    empty = tmp_path / "vazia"
    empty.mkdir()
    assert main(["restore", str(package), "--to", str(empty)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] and (empty / "productions" / record["id"] / "asset-provenance.json").is_file()
    assert main(["restore", str(package), "--to", str(tmp_path / "x")]) == 0
    with pytest.raises(SystemExit):
        main(["restore", str(package)])


def test_refuses_non_empty_existing_file_link_and_missing_parent_without_touching_anything(source, tmp_path):
    settings, app, _, _ = source
    package = package_of(settings, app, tmp_path)
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
    # o destino operacional atual também é recusado
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


def refuse(package, destination, match=None, *, target_conn=None):
    with pytest.raises(ValueError, match=match):
        restore_local_data(package, destination, target_conn=target_conn)
    assert not destination.exists() and not [p for p in destination.parent.iterdir() if ".restore-" in p.name]


def test_hostile_or_tampered_packages_are_refused_before_writing(source, tmp_path):
    settings, app, _, _ = source
    package = package_of(settings, app, tmp_path)

    def tamper(entries):
        entries["pg_dump/productions.jsonl"] = entries["pg_dump/productions.jsonl"] + b" "
        return entries
    refuse(rewrite(package, tmp_path / "t1.zip", tamper), tmp_path / "d1", "não verificado")

    def traversal(entries):
        entries["../fora.json"] = b"{}"
        return entries
    refuse(rewrite(package, tmp_path / "t2.zip", traversal), tmp_path / "d2", "não verificado")

    def foreign(entries):   # arquivo fora do formato, mesmo com manifesto coerente
        manifest = json.loads(entries["manifest.json"])
        entries["local_files/settings/integrations.json"] = b"{}"
        manifest["files"].append({"name": "local_files/settings/integrations.json", "size": 2, "sha256": hashlib.sha256(b"{}").hexdigest()})
        entries["manifest.json"] = json.dumps(manifest).encode()
        return entries
    refuse(rewrite(package, tmp_path / "t3.zip", foreign), tmp_path / "d3", "entrada fora da allowlist")

    def secret(entries):   # credencial plantada num manifesto de produção, com hash coerente
        manifest = json.loads(entries["manifest.json"])
        data = b'{"token":"TOKEN-SECRETO-DO-HERMES-0123456789abcdef"}'
        entries["local_files/productions/p1/leak.json"] = data
        manifest["files"].append({"name": "local_files/productions/p1/leak.json", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        entries["manifest.json"] = json.dumps(manifest).encode()
        return entries
    refuse(rewrite(package, tmp_path / "t4.zip", secret), tmp_path / "d4", "credencial")

    notzip = tmp_path / "nao.zip"
    notzip.write_bytes(b"isto nao e um zip")
    refuse(notzip, tmp_path / "d5", "não verificado")


def test_newer_database_schema_is_refused_with_clear_message(source, pg_app_cluster, tmp_path):
    settings, app, _, _ = source
    package = package_of(settings, app, tmp_path)

    def newer(entries):
        manifest = json.loads(entries["manifest.json"])
        manifest["database"]["schema_version"] = "99999999999999"
        entries["manifest.json"] = json.dumps(manifest).encode()
        return entries

    newer_package = rewrite(package, tmp_path / "novo.zip", newer)
    assert verify_export(newer_package)["ok"]
    with isolated_empty_pg_db(pg_app_cluster) as (admin, _):
        refuse(newer_package, tmp_path / "dn", "versão mais nova", target_conn=admin)


def test_failure_midway_leaves_no_partial_destination(source, tmp_path, monkeypatch):
    settings, app, _, _ = source
    package = package_of(settings, app, tmp_path)
    import horus_backend.local_data_export as module
    monkeypatch.setattr(module.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("falha simulada")))
    with pytest.raises(OSError):
        restore_local_data(package, tmp_path / "meio")
    assert not (tmp_path / "meio").exists() and not [p for p in tmp_path.iterdir() if ".restore-" in p.name]
