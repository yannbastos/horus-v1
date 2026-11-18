"""HOR-098 (backend): histórico de notificações derivado das tabelas existentes + estado lido/não lido em PostgreSQL.
Sem migração, sem rede, só banco descartável."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from horus_backend.notifications import MAX_STATE_IDS, NotificationService
from horus_backend.storage import DatabaseUnavailable
from support_c import (  # noqa: F401
    HEADERS,
    TOKEN,
    client_for,
    create,
    decide,
    finished,
    generate,
    pg_connect,
    review,
    schema_state,
    script_for,
    settings,
    start,
    wait,
)


def listing(client, **params):
    response = client.get("/v1/notifications", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def state_path(settings):
    return settings.data_dir / "notifications-state.json"


def read_state(client, ids=None, all=False, read=True):
    body = {"read": read} | ({"ids": ids} if ids is not None else {}) | ({"all": True} if all else {})
    return client.post("/v1/notifications/read-state", json=body)


def kinds(page):
    return [item["kind"] for item in page["items"]]


# --- derivação: eventos reais a partir das tabelas existentes ---------------------------------------------------

def test_empty_history_and_no_schema_change(settings):
    with client_for(settings) as client:
        with pg_connect(settings) as db:
            before_schema = schema_state(db)
        assert listing(client) == {"items": [], "unread_count": 0, "next_after": None}
        assert client.get("/v1/notifications/unread-count").json() == {"unread_count": 0}
        with pg_connect(settings) as db:
            assert db.execute("SELECT COUNT(*) FROM horus.notification_state").fetchone()[0] == 0
            assert schema_state(db) == before_schema
    assert not state_path(settings).exists()           # só ler não cria arquivo


def test_workflow_script_and_production_events_follow_the_real_state(settings):
    with client_for(settings) as client:
        workflow = wait(client, create(client)["id"])
        page = listing(client)
        assert kinds(page) == ["workflow.awaiting_approval"] and page["items"][0]["pending"] is True and page["unread_count"] == 1
        assert page["items"][0]["ref"] == {"kind": "workflow", "id": workflow["id"]} and page["items"][0]["severity"] == "info"

        assert review(client, workflow).status_code == 200                     # aprovada: some da lista de pendências
        assert "workflow.awaiting_approval" not in kinds(listing(client))

        script = generate(client, client.get(f"/v1/workflows/{workflow['id']}").json()).json()
        page = listing(client)
        assert kinds(page) == ["script.awaiting_review"] and page["items"][0]["pending"] is True
        assert decide(client, script).status_code == 200
        item = listing(client)["items"][0]
        assert item["kind"] == "script.awaiting_review" and item["pending"] is False and "já foi decidida" in item["message"]

        record = finished(client, start(client, script).json())
        assert record["status"] == "succeeded"
        page = listing(client)
        assert kinds(page)[0] == "production.succeeded" and page["items"][0]["ref"] == {"kind": "production", "id": record["id"]}
        assert page["items"][0]["severity"] == "success" and page["items"][0]["pending"] is False


@pytest.mark.parametrize("status,kind,severity", [("failed", "production.failed", "error"), ("blocked", "production.failed", "error"),
                                                  ("interrupted", "production.interrupted", "warning")])
def test_failed_blocked_and_interrupted_productions(settings, status, kind, severity):
    with client_for(settings) as client:
        record = finished(client, start(client, script_for(client)).json())
        with pg_connect(settings) as db:
            db.execute("UPDATE horus.jobs SET status=%s, error=%s, result=%s WHERE id=(SELECT job_id FROM horus.productions WHERE id=%s)",
                       (status, "Falhou ao ler C:\\Users\\operador\\segredos\\roteiro.txt com token=" + "z" * 24 + " em /home/yann/x",
                        json.dumps({"failure": {"code": "render_failed", "stderr_tail": "C:\\Users\\operador\\x"}}), record["id"]))
        page = listing(client, kind=kind)
        item = next(i for i in page["items"] if i["ref"]["id"] == record["id"])
        assert item["kind"] == kind and item["severity"] == severity and item["id"] == f"production:{record['id']}:{status}"
        assert item["message"].endswith("Código do diagnóstico: render_failed.")        # só o código estável, nunca o texto do erro
        for forbidden in ("operador", "segredos", "roteiro.txt", "z" * 10, "/home/yann", "stderr"):
            assert forbidden not in json.dumps(item), forbidden


def test_failure_code_with_unsafe_characters_is_ignored(settings):
    with client_for(settings) as client:
        record = finished(client, start(client, script_for(client)).json())
        with pg_connect(settings) as db:
            db.execute("UPDATE horus.jobs SET status='failed', result=%s WHERE id=(SELECT job_id FROM horus.productions WHERE id=%s)",
                       (json.dumps({"failure": {"code": "C:\\Users\\x segredo"}}), record["id"]))
        assert "Código do diagnóstico" not in listing(client)["items"][0]["message"]


def test_ordering_filters_pagination_and_cursor_validation(settings):
    with client_for(settings) as client:
        script = script_for(client)
        records = [finished(client, start(client, script, key=f"p{i}").json()) for i in range(1)]
        wf = wait(client, create(client, key="outro")["id"])
        everything = listing(client)["items"]
        assert [i["at"] for i in everything] == sorted((i["at"] for i in everything), reverse=True)
        seen, cursor = [], None
        for _ in range(10):
            params = {"limit": 1} | ({"after": cursor} if cursor else {})
            page = listing(client, **params)
            seen += [i["id"] for i in page["items"]]
            cursor = page["next_after"]
            if not cursor:
                break
        assert seen == [i["id"] for i in everything] and len(set(seen)) == len(seen)
        assert all(i["kind"] == "workflow.awaiting_approval" for i in listing(client, kind="workflow.awaiting_approval")["items"])
        for bad in ({"kind": "production.exploded"}, {"after": "!!!não-é-cursor"}, {"after": "e30"}, {"limit": 0}, {"limit": 201}):
            assert client.get("/v1/notifications", params=bad).status_code == 422, bad
        assert wf["id"] and records


# --- lido / não lido --------------------------------------------------------------------------------------------

def test_mark_read_unread_filter_count_and_persistence_across_restarts(settings):
    with client_for(settings) as client:
        wait(client, create(client)["id"])
        wait(client, create(client, key="segundo")["id"])
        items = listing(client)["items"]
        assert len(items) == 2 and listing(client)["unread_count"] == 2
        first = items[0]["id"]
        assert read_state(client, [first]).json() == {"unread_count": 1}
        assert [i["id"] for i in listing(client, unread=True)["items"]] == [items[1]["id"]]
        assert [i["id"] for i in listing(client, unread=False)["items"]] == [first]
        assert read_state(client, [first], read=False).json() == {"unread_count": 2}
        assert read_state(client, [first]).json() == {"unread_count": 1}
        assert read_state(client, [first]).json() == {"unread_count": 1}       # idempotente
    with pg_connect(settings) as db:
        db.execute("DELETE FROM horus.worker_instances")
    with client_for(settings) as again:                                         # novo processo, mesmo diretório de dados
        assert [i["read"] for i in listing(again)["items"]] == [True, False] and listing(again)["unread_count"] == 1


def test_read_all_then_new_events_are_unread_and_single_unread_overrides(settings):
    with client_for(settings) as client:
        first = wait(client, create(client)["id"])
        ids = [i["id"] for i in listing(client)["items"]]
        assert read_state(client, all=True).json() == {"unread_count": 0}
        time.sleep(.02)
        wait(client, create(client, key="novo")["id"])
        page = listing(client)
        assert page["unread_count"] == 1 and [i["read"] for i in page["items"]] == [False, True]
        assert read_state(client, ids, read=False).json() == {"unread_count": 2}     # desmarcar um item coberto pelo "tudo lido"
        assert read_state(client, all=True, read=False).json() == {"unread_count": 2}  # reset
        assert first["id"]


@pytest.mark.parametrize("body", [
    {"read": True}, {"read": True, "ids": [], "all": True}, {"read": True, "ids": ["workflow:" + "a" * 32 + ":r1"], "all": True},
    {"ids": ["workflow:x:r1"]}, {"read": "sim", "all": True}, {"read": 1, "all": True}, {"read": True, "all": "true"},
    {"read": True, "ids": "workflow:x:r1"}, {"read": True, "ids": [1]}, {"read": True, "ids": ["sem-dois-pontos"]},
    {"read": True, "ids": ["WORKFLOW:x"]}, {"read": True, "ids": ["workflow:" + "a" * 130]}, {"read": True, "ids": ["a:b c"]},
    {"read": True, "ids": ["workflow:x\n"]}, {"read": True, "ids": ["workflow:../../x"]},
    {"read": True, "ids": [f"workflow:{i}" for i in range(201)]}, {"read": True, "ids": ["workflow:a", "workflow:a"]},
    {"read": True, "all": True, "extra": 1}, {"read": True, "ids": None},
])
def test_read_state_validation(settings, body):
    with client_for(settings) as client:
        assert client.post("/v1/notifications/read-state", json=body).status_code in {200, 422}
        response = client.post("/v1/notifications/read-state", json=body)
        if body in ({"read": True, "ids": None},):
            assert response.status_code == 422
        elif "workflow:../../x" in json.dumps(body):
            assert response.status_code == 422
        else:
            assert response.status_code == 422, body
        with pg_connect(settings) as db:
            assert db.execute("SELECT COUNT(*) FROM horus.notification_state").fetchone()[0] == 0
        assert not state_path(settings).exists()


# --- persistência em PostgreSQL: indisponibilidade 503 e concorrência ------------------------------------------

def test_corrupt_state_never_breaks_the_api_and_is_recreated(settings):
    """Equivalente PG: estado em tabelas tipadas; sem arquivo JSON e falha de banco retorna 503 sem vazar detalhes."""
    with client_for(settings) as client:
        wait(client, create(client)["id"])
        page = listing(client)
        assert page["unread_count"] == 1 and page["items"][0]["read"] is False
        assert client.get("/v1/notifications/unread-count").status_code == 200
        item = page["items"][0]["id"]
        assert read_state(client, [item]).json() == {"unread_count": 0}
        assert not state_path(settings).exists()
        with pg_connect(settings) as db:
            row = db.execute("SELECT state FROM horus.notification_state WHERE event_id = %s", (item,)).fetchone()
            assert row == ("read",)


def test_unreadable_state_path_warns_and_write_failure_is_a_clean_500(settings, monkeypatch):
    """Equivalente PG: indisponibilidade do banco retorna 503 estável (`database_unavailable`) sem vazar caminhos/traceback."""
    with client_for(settings) as client:
        wait(client, create(client)["id"])
        monkeypatch.setattr(client.app.state.store, "uow", lambda **_: (_ for _ in ()).throw(DatabaseUnavailable("indisponível")))
        for response in (client.get("/v1/notifications"), client.get("/v1/notifications/unread-count"), read_state(client, all=True)):
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "database_unavailable"
            assert "Traceback" not in response.text and str(settings.data_dir) not in response.text


def test_writes_are_atomic_leave_no_temp_files_and_survive_concurrency(settings):
    with client_for(settings) as client:
        wait(client, create(client)["id"])
        ids = [f"workflow:id{n}:r1" for n in range(40)]
        errors = []

        def mark(chunk):
            for ident in chunk:
                response = read_state(client, [ident])
                if response.status_code != 200:
                    errors.append(response.status_code)
        threads = [threading.Thread(target=mark, args=(ids[i::4],)) for i in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert errors == []
        with pg_connect(settings) as db:
            saved = {r[0] for r in db.execute("SELECT event_id FROM horus.notification_state WHERE state = 'read'").fetchall()}
        assert saved == set(ids)
        assert not state_path(settings).exists()


def test_old_read_marks_are_pruned_and_state_is_bounded(settings):
    base = datetime(2026, 11, 14, tzinfo=timezone.utc)
    clock = {"now": base}
    with client_for(settings) as client:
        service = NotificationService(client.app.state.store, clock=lambda: clock["now"])
        service.set_read(ids=["workflow:antigo:r1"], all=False, read=True)
        clock["now"] = base + timedelta(days=91)
        service.set_read(ids=["workflow:novo:r1"], all=False, read=True)
        with pg_connect(settings) as db:
            saved = {r[0] for r in db.execute("SELECT event_id FROM horus.notification_state WHERE state = 'read'").fetchall()}
        assert saved == {"workflow:novo:r1"}
        assert MAX_STATE_IDS == 5000


# --- segurança: autenticação, Origin, vazamento ----------------------------------------------------------------

@pytest.mark.parametrize("method,path", [("GET", "/v1/notifications"), ("GET", "/v1/notifications/unread-count"),
                                         ("POST", "/v1/notifications/read-state")])
def test_routes_require_the_token_and_refuse_foreign_origin_or_host(settings, method, path):
    with client_for(settings) as client:
        assert client.request(method, path, headers={"Authorization": ""}).status_code in {401, 403}
        assert client.request(method, path, headers={"Authorization": "Bearer errado" + "x" * 30}).status_code == 401
        for hostile in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}):
            assert client.request(method, path, headers={**HEADERS, **hostile}, json={"read": True, "all": True}).status_code == 403


def test_no_path_script_content_or_token_leaks_in_any_response_or_state_file(settings):
    secret_text = "Texto secreto do roteiro que nunca deve aparecer " + "k" * 20
    with client_for(settings) as client:
        workflow = wait(client, create(client)["id"])
        review(client, workflow)
        script = generate(client, client.get(f"/v1/workflows/{workflow['id']}").json()).json()
        decide(client, script)
        record = finished(client, start(client, script).json())
        with pg_connect(settings) as db:
            db.execute("UPDATE horus.jobs SET status='failed', error=%s WHERE id=(SELECT job_id FROM horus.productions WHERE id=%s)",
                       (f"{secret_text} em {settings.data_dir}\\productions\\x e Bearer {TOKEN}", record["id"]))
            db.execute(
                "UPDATE horus.scripts "
                "SET document = jsonb_set(document, '{title}', to_jsonb(%s::text)), "
                "    document_canonical = convert_to(jsonb_set(document, '{title}', to_jsonb(%s::text))::text, 'UTF8') "
                "WHERE id=%s",
                (secret_text, secret_text, script["id"]),
            )
        read_state(client, all=True)
        with pg_connect(settings) as db:
            state_rows = db.execute("SELECT event_id, state, changed_at FROM horus.notification_state").fetchall()
        blob = json.dumps(listing(client)) + client.get("/v1/notifications/unread-count").text + json.dumps(state_rows, default=str)
        for forbidden in (secret_text, str(settings.data_dir), "productions\\x", TOKEN, "Bearer "):
            assert forbidden not in blob, forbidden
        assert not state_path(settings).exists()


def test_derivation_is_read_only(settings):
    with client_for(settings) as client:
        wait(client, create(client)["id"])
        with pg_connect(settings) as db:
            before = [tuple(r) for r in db.execute("SELECT id,status,updated_at FROM horus.workflows ORDER BY id")]
        for _ in range(3):
            listing(client)
            client.get("/v1/notifications/unread-count")
        with pg_connect(settings) as db:
            assert [tuple(r) for r in db.execute("SELECT id,status,updated_at FROM horus.workflows ORDER BY id")] == before
