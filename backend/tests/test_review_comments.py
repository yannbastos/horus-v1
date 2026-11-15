"""HOR-088 (backend): comentários de revisão por timecode e versão, com trilha de decisão. Sem migração, rede ou dados reais."""
import json
import sqlite3

import pytest

from test_workflows import settings, client_for  # noqa: F401
from test_production import script_for, start
from horus_backend.production import inspect_artifact
from horus_backend.migrations import SCHEMA_VERSION
from horus_backend.review_comments import DIRECTORY, MAX_COMMENTS

H = {"Idempotency-Key": "k1"}


@pytest.fixture
def env(settings):
    """Produção local_pilot sintética com um vídeo registrado (fixture de armazenamento; não afirma render real)."""
    with client_for(settings) as client:
        script = script_for(client)
        client.portal.call(client.app.state.manager.worker.cancel)
        item = start(client, script, mode="local_pilot").json()
        root = settings.data_dir / "productions" / item["id"]
        root.mkdir(parents=True)
        (root / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypunit-fixture")
        video = inspect_artifact(root, "video.mp4", "video")
        with sqlite3.connect(settings.data_dir / "horus.sqlite3") as db:
            db.execute("UPDATE productions SET artifacts=? WHERE id=?", (json.dumps([video]), item["id"]))
        yield client, item["id"], video["sha256"], settings


def body(sha, **extra):
    return {"artifact_sha256": sha, "at_seconds": 12.5, "text": "Cortar aqui", "author": "Yann"} | extra


def post(client, pid, payload, key="k1"):
    return client.post(f"/v1/productions/{pid}/comments", json=payload, headers={"Idempotency-Key": key})


def test_create_list_resolve_reopen_with_decision_trail(env):
    client, pid, sha, _ = env
    created = post(client, pid, body(sha))
    assert created.status_code == 201
    comment = created.json()
    assert (comment["status"], comment["author_kind"], comment["artifact_sha256"], comment["at_seconds"]) == ("open", "human", sha, 12.5)
    assert [e["action"] for e in comment["trail"]] == ["created"]
    cid = comment["id"]
    resolved = client.post(f"/v1/productions/{pid}/comments/{cid}/resolve", json={"author": "Yann", "note": "Cortado na v2"}, headers={"Idempotency-Key": "r1"})
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    assert [e["action"] for e in resolved.json()["trail"]] == ["created", "resolved"] and resolved.json()["trail"][1]["note"] == "Cortado na v2"
    assert client.post(f"/v1/productions/{pid}/comments/{cid}/resolve", json={"author": "Yann"}, headers={"Idempotency-Key": "r2"}).status_code == 409
    reopened = client.post(f"/v1/productions/{pid}/comments/{cid}/reopen", json={"author": "Yann"}, headers={"Idempotency-Key": "o1"})
    assert reopened.json()["status"] == "open" and [e["action"] for e in reopened.json()["trail"]] == ["created", "resolved", "reopened"]
    page = client.get(f"/v1/productions/{pid}/comments").json()
    assert page["counts"] == {"open": 1, "resolved": 0} and page["items"][0]["trail"] == reopened.json()["trail"]


def test_filters_ordering_and_pagination(env):
    client, pid, sha, _ = env
    ids = []
    for index, at in enumerate((30, 5, 5, 10)):
        ids.append(post(client, pid, body(sha, at_seconds=at, text=f"c{index}"), key=f"k{index}").json()["id"])
    client.post(f"/v1/productions/{pid}/comments/{ids[3]}/resolve", json={"author": "Yann"}, headers={"Idempotency-Key": "r"})
    listing = client.get(f"/v1/productions/{pid}/comments").json()
    assert [i["at_seconds"] for i in listing["items"]] == [5, 5, 10, 30] and listing["counts"] == {"open": 3, "resolved": 1}
    assert [i["id"] for i in client.get(f"/v1/productions/{pid}/comments", params={"status": "resolved"}).json()["items"]] == [ids[3]]
    assert len(client.get(f"/v1/productions/{pid}/comments", params={"artifact_sha256": sha}).json()["items"]) == 4
    assert client.get(f"/v1/productions/{pid}/comments", params={"artifact_sha256": "0" * 64}).json()["items"] == []
    seen, cursor = [], None
    while True:
        page = client.get(f"/v1/productions/{pid}/comments", params={"limit": 3} | ({"after": cursor} if cursor else {})).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_after"]
        if not cursor:
            break
    assert sorted(seen) == sorted(ids) and len(seen) == 4


def test_idempotency_and_replay(env):
    client, pid, sha, _ = env
    first = post(client, pid, body(sha), key="same")
    again = post(client, pid, body(sha), key="same")
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert post(client, pid, body(sha, text="Outro"), key="same").status_code == 409
    assert client.get(f"/v1/productions/{pid}/comments").json()["counts"]["open"] == 1
    cid = first.json()["id"]
    r = lambda: client.post(f"/v1/productions/{pid}/comments/{cid}/resolve", json={"author": "Yann"}, headers={"Idempotency-Key": "rk"})
    assert r().status_code == 200 and r().status_code == 200
    assert len(r().json()["trail"]) == 2   # a repetição não duplica o evento
    assert client.post(f"/v1/productions/{pid}/comments/{cid}/resolve", json={"author": "Outro"}, headers={"Idempotency-Key": "rk"}).status_code == 409


@pytest.mark.parametrize("change", [
    {"artifact_sha256": "x" * 64}, {"artifact_sha256": "A" * 64}, {"at_seconds": -1}, {"at_seconds": 100000}, {"at_seconds": "5"},
    {"at_seconds": True}, {"text": ""}, {"text": "x" * 2001}, {"text": 5}, {"author": ""}, {"author": "a" * 81}, {"author": "a\x00b"},
    {"author": "  "}, {"extra": 1}])
def test_invalid_bodies_are_422_without_echo(env, change):
    client, pid, sha, _ = env
    response = post(client, pid, body(sha) | change)
    assert response.status_code == 422 and "Cortar" not in response.text


def test_nan_and_control_characters_are_refused(env):
    client, pid, sha, _ = env
    raw = '{"artifact_sha256":"%s","at_seconds":NaN,"text":"x","author":"Y"}' % sha
    assert client.post(f"/v1/productions/{pid}/comments", content=raw, headers=H | {"Content-Type": "application/json"}).status_code == 422
    assert post(client, pid, body(sha, text="a\x07b")).status_code == 422
    assert post(client, pid, body(sha)).status_code == 201


def test_foreign_hash_unknown_production_and_comment_and_missing_key(env):
    client, pid, sha, _ = env
    assert post(client, pid, body("0" * 64)).status_code == 409           # hash que não é de vídeo desta produção
    assert post(client, "inexistente", body(sha)).status_code == 404
    assert client.get("/v1/productions/inexistente/comments").status_code == 404
    assert client.post(f"/v1/productions/{pid}/comments/{'0' * 32}/resolve", json={"author": "Y"}, headers=H).status_code == 404
    assert client.post(f"/v1/productions/{pid}/comments/..%2f..%2fx/resolve", json={"author": "Y"}, headers=H).status_code in {404, 422}
    assert client.post(f"/v1/productions/{pid}/comments", json=body(sha)).status_code == 422   # sem Idempotency-Key
    assert client.get(f"/v1/productions/..%2f..%2fx/comments").status_code == 404


@pytest.mark.parametrize("params", [{"status": "outro"}, {"after": "!!"}, {"artifact_sha256": "zz"}, {"limit": 0}, {"limit": 201}])
def test_invalid_query_is_422(env, params):
    client, pid, *_ = env
    assert client.get(f"/v1/productions/{pid}/comments", params=params).status_code == 422


def test_requires_authentication(env):
    client, pid, sha, _ = env
    for method, path in (("get", f"/v1/productions/{pid}/comments"), ("post", f"/v1/productions/{pid}/comments"),
                         ("post", f"/v1/productions/{pid}/comments/{'0' * 32}/resolve")):
        for headers in ({"Authorization": "Bearer errado"}, {"Authorization": ""}):
            assert getattr(client, method)(path, headers=headers | H).status_code == 401


def test_limit_of_comments_per_production(env, monkeypatch):
    client, pid, sha, _ = env
    import horus_backend.review_comments as module
    monkeypatch.setattr(module, "MAX_COMMENTS", 2)
    assert [post(client, pid, body(sha), key=f"k{i}").status_code for i in range(3)] == [201, 201, 409]


def test_trail_limit(env, monkeypatch):
    client, pid, sha, _ = env
    import horus_backend.review_comments as module
    monkeypatch.setattr(module, "MAX_EVENTS", 3)
    cid = post(client, pid, body(sha)).json()["id"]
    codes = [client.post(f"/v1/productions/{pid}/comments/{cid}/{a}", json={"author": "Y"}, headers={"Idempotency-Key": f"x{i}"}).status_code
             for i, a in enumerate(("resolve", "reopen", "resolve"))]
    assert codes == [200, 200, 409]


def test_corrupt_file_is_never_overwritten(env):
    client, pid, sha, settings = env
    post(client, pid, body(sha))
    path = settings.data_dir / DIRECTORY / f"{pid}.json"
    path.write_text("{corrompido", encoding="utf-8")
    for response in (post(client, pid, body(sha), key="k2"), client.get(f"/v1/productions/{pid}/comments")):
        assert response.status_code == 409 and "ilegível" in response.json()["detail"]
    assert path.read_text(encoding="utf-8") == "{corrompido"


def test_storage_is_atomic_json_without_secrets_or_paths_and_no_schema_change(env):
    client, pid, sha, settings = env
    post(client, pid, body(sha))
    folder = settings.data_dir / DIRECTORY
    assert [p.name for p in folder.iterdir()] == [f"{pid}.json"]      # sem temporários residuais
    document = json.loads((folder / f"{pid}.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == 1 and document["comments"][0]["author_kind"] == "human"
    assert "Bearer" not in json.dumps(document) and str(settings.data_dir) not in json.dumps(document)
    with sqlite3.connect(settings.data_dir / "horus.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_comments_do_not_change_the_existing_review_endpoint(env):
    client, pid, sha, _ = env
    before = client.get(f"/v1/productions/{pid}").json()
    post(client, pid, body(sha))
    assert client.get(f"/v1/productions/{pid}").json() == before
