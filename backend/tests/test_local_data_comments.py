"""HOR-088: comentários de revisão e estado de notificações no pacote de dados locais (ida e volta e pacote adulterado)."""
import hashlib
import json
import zipfile
from dataclasses import replace

import pytest

from test_review_comments import env, post, body  # noqa: F401
from test_workflows import client_for, settings  # noqa: F401
from test_local_data_restore import rewrite, refuse
from horus_backend.local_data_export import export_local_data, restore_local_data, verify_export

STATE = {"read": {"job:abc": "2026-11-01T10:00:00+00:00"}, "unread": ["job:def"], "read_all_before": None}


def populated(env, tmp_path):
    client, pid, sha, settings = env
    comment = post(client, pid, body(sha)).json()
    client.post(f"/v1/productions/{pid}/comments/{comment['id']}/resolve", json={"author": "Yann", "note": "ok"}, headers={"Idempotency-Key": "r1"})
    (settings.data_dir / "notifications-state.json").write_text(json.dumps(STATE), encoding="utf-8")
    package = tmp_path / "pacote.zip"
    report = export_local_data(settings.data_dir, package)
    assert report["ok"] and not report["excluded"]
    return client, pid, sha, settings, package


def amend(package, tmp_path, name, mutate, target="adulterado.zip"):
    """Troca o conteúdo de um arquivo mantendo manifesto (tamanho e hash) coerente: só a validação de conteúdo pode barrar."""
    def change(entries):
        data = mutate(entries[name])
        manifest = json.loads(entries["manifest.json"])
        for item in manifest["files"]:
            if item["name"] == name:
                item.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        entries[name], entries["manifest.json"] = data, json.dumps(manifest).encode()
        return entries
    return rewrite(package, tmp_path / target, change)


def test_round_trip_restores_comments_trail_receipts_and_notification_state(env, tmp_path):
    client, pid, sha, settings, package = populated(env, tmp_path)
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    names = {f["name"] for f in manifest["files"]}
    assert {f"comments/{pid}.json", "settings/notifications-state.json"} <= names
    assert verify_export(package) == {"ok": True, "errors": []}
    destination = tmp_path / "restaurado"
    assert restore_local_data(package, destination)["ok"]
    for relative in (f"production-comments/{pid}.json", "notifications-state.json"):
        assert (destination / relative).read_bytes() == (settings.data_dir / relative).read_bytes()
    before = client.get(f"/v1/productions/{pid}/comments").json()
    with client_for(replace(settings, data_dir=destination)) as restored:
        assert restored.get(f"/v1/productions/{pid}/comments").json() == before
        replay = post(restored, pid, body(sha))      # recibo de idempotência restaurado: mesma chave, sem duplicar
        assert replay.status_code == 200 and replay.json()["id"] == before["items"][0]["id"]
        assert restored.get(f"/v1/productions/{pid}/comments").json()["counts"] == before["counts"]


def test_package_without_comments_or_notifications_still_verifies_and_restores(env, tmp_path):
    client, pid, sha, settings = env
    package = tmp_path / "sem.zip"
    export_local_data(settings.data_dir, package)
    assert verify_export(package)["ok"] and restore_local_data(package, tmp_path / "d")["ok"]
    assert not (tmp_path / "d" / "production-comments").exists()


def test_tampered_comment_and_state_packages_are_refused_before_writing(env, tmp_path):
    client, pid, sha, settings, package = populated(env, tmp_path)
    name = f"comments/{pid}.json"

    def doc(entries_bytes, change):
        data = json.loads(entries_bytes)
        change(data)
        return json.dumps(data).encode()
    # hash divergente (conteúdo trocado sem atualizar o manifesto)
    refuse(rewrite(package, tmp_path / "t1.zip", lambda e: e | {name: e[name].replace(b"Cortar", b"Apagar")}), tmp_path / "d1", "não verificado")
    # hash coerente, mas conteúdo inválido: autor 'agente', produção trocada, estrutura, estado de notificações
    cases = [(name, lambda d: d["comments"][0].update(author_kind="agent"), "humano"),
             (name, lambda d: d["comments"][0].update(production_id="outra"), "inválido"),
             (name, lambda d: d["comments"][0].update(at_seconds=-1), "inválido"),
             (name, lambda d: d.update(comments="x"), "estrutura"),
             (name, lambda d: d["comments"][0]["trail"].append({"action": "deleted", "author": "x"}), "inválido"),
             ("settings/notifications-state.json", lambda d: d.update(unread=["../x"]), "notificações")]
    for index, (target, change, reason) in enumerate(cases):
        tampered = amend(package, tmp_path, target, lambda raw, c=change: doc(raw, c), f"c{index}.zip")
        report = verify_export(tampered)
        assert not report["ok"] and reason in " ".join(report["errors"]), (index, report)
        refuse(tampered, tmp_path / f"dc{index}", "não verificado")
    # nome de comentário fora do formato, mesmo com manifesto coerente
    def foreign(entries):
        manifest = json.loads(entries["manifest.json"])
        entries["comments/../x.json"] = b"{}"
        manifest["files"].append({"name": "comments/../x.json", "size": 2, "sha256": hashlib.sha256(b"{}").hexdigest()})
        entries["manifest.json"] = json.dumps(manifest).encode()
        return entries
    refuse(rewrite(package, tmp_path / "t3.zip", foreign), tmp_path / "d3", "não verificado")


def test_export_excludes_unreadable_invalid_or_credential_shaped_comment_files(env, tmp_path):
    client, pid, sha, settings = env
    post(client, pid, body(sha))
    folder = settings.data_dir / "production-comments"
    (folder / "quebrado.json").write_text("{nao e json", encoding="utf-8")
    (folder / "falso.json").write_text(json.dumps({"schema_version": 1, "receipts": {}, "comments": [{"id": "x", "author_kind": "agent"}]}), encoding="utf-8")
    (folder / "segredo.json").write_text(json.dumps({"schema_version": 1, "receipts": {}, "comments": [],
                                                      "nota": "TOKEN: 'TOKEN-SECRETO-DO-HERMES-0123456789abcdef'"}), encoding="utf-8")
    (settings.data_dir / "notifications-state.json").write_text('{"read": []}', encoding="utf-8")
    package = tmp_path / "p.zip"
    report = export_local_data(settings.data_dir, package)
    reasons = {e["path"]: e["reason"] for e in report["excluded"]}
    assert set(reasons) == {"comments/quebrado.json", "comments/falso.json", "comments/segredo.json", "settings/notifications-state.json"}
    with zipfile.ZipFile(package) as archive:
        assert f"comments/{pid}.json" in archive.namelist() and "comments/segredo.json" not in archive.namelist()
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
    stored = json.loads((settings.data_dir / "production-comments" / f"{pid}.json").read_text(encoding="utf-8"))
    assert [c["author_kind"] for c in stored["comments"]] == ["human"] and stored["comments"][0]["status"] == "open"
