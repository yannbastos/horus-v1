"""HOR-088: comentários de revisão e estado de notificações no pacote de dados locais v2 (ida e volta e pacote adulterado)."""
from dataclasses import replace
import hashlib
import json
import sys
from pathlib import Path
import zipfile

import pytest

from pg_app_support import PG_TEST_CONFIG, _patch_pg_transport
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from horus_backend.local_data_export import export_local_data, restore_local_data, verify_export
from keyring_support import MemoryKeyring
from support_c import client_for, pg_connect, settings  # noqa: F401
from test_review_comments import body, env, post  # noqa: F401


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


def populated(env, tmp_path):
    client, pid, sha, settings = env
    comment = post(client, pid, body(sha)).json()
    client.post(
        f"/v1/productions/{pid}/comments/{comment['id']}/resolve",
        json={"author": "Yann", "note": "ok"},
        headers={"Idempotency-Key": "r1"},
    )
    assert client.post("/v1/notifications/read-state", json={"read": True, "ids": ["job:abc"]}).status_code == 200
    assert client.post("/v1/notifications/read-state", json={"read": False, "ids": ["job:def"]}).status_code == 200
    package = tmp_path / "pacote.zip"
    report = export_local_data(settings.data_dir, package, store=client.app.state.store)
    assert report["ok"] and not report["excluded"]
    return client, pid, sha, settings, package


def amend(package, tmp_path, name, mutate, target="adulterado.zip"):
    """Troca o conteúdo de um arquivo mantendo manifesto (tamanho e hash) coerente."""
    def change(entries):
        data = mutate(entries[name])
        manifest = json.loads(entries["manifest.json"])
        for item in manifest.get("files", []):
            if item["name"] == name:
                item.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        for item in manifest.get("pg_tables", []):
            if item["name"] == name:
                item.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        entries[name], entries["manifest.json"] = data, json.dumps(manifest).encode()
        return entries
    return rewrite(package, tmp_path / target, change)


def test_round_trip_restores_comments_trail_receipts_and_notification_state(env, tmp_path, pg_app_cluster, monkeypatch):
    client, pid, sha, settings, package = populated(env, tmp_path)
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = set(archive.namelist())
    assert manifest["format"] == "horus-pg-export-v2"
    assert {
        "pg_dump/review_comments.jsonl",
        "pg_dump/review_comment_events.jsonl",
        "pg_dump/review_comment_receipts.jsonl",
        "pg_dump/notification_state.jsonl",
    } <= names
    assert verify_export(package) == {"ok": True, "errors": []}

    pg_dir = str(Path(__file__).resolve().parent / "pg")
    if pg_dir not in sys.path:
        sys.path.insert(0, pg_dir)
    from schema_support import schema_database

    before = client.get(f"/v1/productions/{pid}/comments").json()
    destination = tmp_path / "restaurado"
    with schema_database(pg_app_cluster) as (admin, pool):
        with pool.connection() as conn:
            restored_dbname = conn.info.dbname
        result = restore_local_data(package, destination, target_conn=admin)
        assert result["ok"]
        admin.execute("DELETE FROM horus.worker_instances")
        _patch_pg_transport(pg_app_cluster, restored_dbname, MemoryKeyring(), monkeypatch)
        restored_settings = replace(settings, data_dir=destination)
        ConnectionVault(restored_settings.data_dir).write(ConnectionRequest.model_validate(PG_TEST_CONFIG))
        with client_for(restored_settings) as restored:
            assert restored.get(f"/v1/productions/{pid}/comments").json() == before
            replay = post(restored, pid, body(sha))      # recibo de idempotência restaurado: mesma chave, sem duplicar
            assert replay.status_code == 200 and replay.json()["id"] == before["items"][0]["id"]
            assert restored.get(f"/v1/productions/{pid}/comments").json()["counts"] == before["counts"]
            with pg_app_cluster.connect(restored_dbname, autocommit=True) as rdb:
                notif_ids = {r[0] for r in rdb.execute("SELECT event_id FROM horus.notification_state").fetchall()}
                assert {"job:abc", "job:def"} <= notif_ids


def test_package_without_comments_or_notifications_still_verifies_and_restores(env, tmp_path):
    client, pid, sha, settings = env
    package = tmp_path / "sem.zip"
    export_local_data(settings.data_dir, package, store=client.app.state.store)
    assert verify_export(package)["ok"] and restore_local_data(package, tmp_path / "d")["ok"]
    assert not (tmp_path / "d" / "production-comments").exists()


def test_tampered_comment_and_state_packages_are_refused_before_writing(env, tmp_path):
    client, pid, sha, settings, package = populated(env, tmp_path)
    name = "pg_dump/review_comments.jsonl"

    # hash divergente (conteúdo trocado sem atualizar o manifesto)
    refuse(rewrite(package, tmp_path / "t1.zip", lambda e: e | {name: e[name].replace(b"Cortar", b"Apagar")}), tmp_path / "d1", "não verificado")

    # hash coerente, mas conteúdo JSONL corrompido
    tampered = amend(package, tmp_path, name, lambda raw: b"{nao e jsonl valido\n", "c0.zip")
    report = verify_export(tampered)
    assert not report["ok"]
    refuse(tampered, tmp_path / "dc0", "não verificado")

    # nome fora do formato permitido, mesmo com manifesto coerente
    def foreign(entries):
        manifest = json.loads(entries["manifest.json"])
        entries["local_files/../x.json"] = b"{}"
        manifest["files"].append({"name": "local_files/../x.json", "size": 2, "sha256": hashlib.sha256(b"{}").hexdigest()})
        entries["manifest.json"] = json.dumps(manifest).encode()
        return entries
    refuse(rewrite(package, tmp_path / "t3.zip", foreign), tmp_path / "d3", "não verificado")


def test_export_excludes_unreadable_invalid_or_credential_shaped_comment_files(env, tmp_path):
    client, pid, sha, settings = env
    post(client, pid, body(sha))
    prod_dir = settings.data_dir / "productions" / pid
    (prod_dir / "quebrado.json").write_text("{nao e json", encoding="utf-8")
    (prod_dir / "segredo.json").write_text(
        json.dumps({"nota": "TOKEN: 'TOKEN-SECRETO-DO-HERMES-0123456789abcdef'"}),
        encoding="utf-8",
    )
    package = tmp_path / "p.zip"
    report = export_local_data(settings.data_dir, package, store=client.app.state.store)
    reasons = {e["path"]: e["reason"] for e in report["excluded"]}
    assert f"local_files/productions/{pid}/segredo.json" in reasons
    with zipfile.ZipFile(package) as archive:
        assert f"local_files/productions/{pid}/segredo.json" not in archive.namelist()
    assert verify_export(package)["ok"]


def test_human_author_kind_cannot_be_forged_through_the_api(env):
    client, pid, sha, settings = env
    for extra in ({"author_kind": "agent"}, {"author_kind": "human"}, {"status": "resolved"}, {"id": "a" * 32}, {"trail": []}):
        assert post(client, pid, body(sha) | extra, key="forja").status_code == 422
    created = post(client, pid, body(sha, author="Hermes (agente)"), key="rotulo").json()
    assert created["author_kind"] == "human"   # o rótulo é texto livre do operador; o tipo é fixado pelo servidor
    forged = client.post(f"/v1/productions/{pid}/comments/{created['id']}/resolve", json={"author": "x", "author_kind": "agent"},
                         headers={"Idempotency-Key": "d1"})
    assert forged.status_code == 422
    with pg_connect(settings) as db:
        rows = db.execute("SELECT author_kind, status FROM horus.review_comments WHERE production_id = %s", (pid,)).fetchall()
        assert rows == [("human", "open")]
