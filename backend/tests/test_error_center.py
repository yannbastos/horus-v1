"""HOR-095 (backend): centro de erros agregado, derivado das tabelas existentes. Sem migração, rede ou dados reais."""
from contextlib import contextmanager
import json

import pytest

from support_c import TOKEN, client_for, create, pg_connect, schema_state, settings  # noqa: F401
from horus_backend.error_center import CATALOG

SECRET = "C:\\Users\\operador\\segredo\\roteiro.txt token=abc123SECRET"


def insert_job(db, ident, status, *, at, operation="factory.render.preview", result=None, error=None, workflow=None, step=None):
    payload_str = json.dumps({"operation": operation, "texto": SECRET}, sort_keys=True, ensure_ascii=False)
    db.execute(
        """INSERT INTO horus.jobs(id,idempotency_key,payload,payload_canonical,status,created_at,updated_at,result,error,workflow_id,step_key)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            ident,
            "k-" + ident,
            payload_str,
            payload_str.encode("utf-8"),
            status,
            at,
            at,
            json.dumps(result) if result is not None else None,
            error,
            workflow,
            step,
        ),
    )


@contextmanager
def seeded(settings):
    with client_for(settings) as client:
        client.get("/health")
        with pg_connect(settings) as db:
            insert_job(db, "a" * 32, "failed", at="2026-11-10T10:00:00+00:00",
                       result={"failure": {"code": "render_failed", "stage": "render", "exit_code": 3, "duration_seconds": 1.5,
                                           "stderr_tail": SECRET}}, error=SECRET)
            insert_job(db, "b" * 32, "interrupted", at="2026-11-11T10:00:00+00:00", operation="transcription.run")
            insert_job(db, "c" * 32, "failed", at="2026-11-12T10:00:00+00:00",
                       result={"failure": {"code": "codigo_inventado_" + "x" * 80, "stage": "../../etc"}})
            insert_job(db, "d" * 32, "succeeded", at="2026-11-12T11:00:00+00:00")
        yield client


def test_lists_failures_with_filters_counts_and_portuguese_text(settings):
    with seeded(settings) as client:
        page = client.get("/v1/errors").json()
        assert [i["id"] for i in page["items"]] == ["job:" + "c" * 32, "job:" + "b" * 32, "job:" + "a" * 32]
        by_id = {i["id"]: i for i in page["items"]}
        render = by_id["job:" + "a" * 32]
        assert (render["code"], render["stage"], render["kind"], render["exit_code"]) == ("render_failed", "render", "job", 3)
        assert render["suggested_action_pt"] and render["ref"] == {"kind": "job", "id": "a" * 32, "workflow_id": None}
        assert by_id["job:" + "b" * 32]["code"] == "job_interrupted" and by_id["job:" + "b" * 32]["stage"] == "transcription"
        odd = by_id["job:" + "c" * 32]
        assert (odd["code"], odd["stage"]) == ("unknown_failure", "render")  # código fora do catálogo e etapa suspeita são neutralizados (etapa vem da operação)
        assert [i["id"] for i in client.get("/v1/errors", params={"code": "render_failed"}).json()["items"]] == ["job:" + "a" * 32]
        assert len(client.get("/v1/errors", params={"stage": "transcription"}).json()["items"]) == 1
        assert client.get("/v1/errors", params={"kind": "production"}).json()["items"] == []
        window = client.get("/v1/errors", params={"since": "2026-11-11T00:00:00Z", "until": "2026-11-11T23:59:59Z"}).json()
        assert [i["id"] for i in window["items"]] == ["job:" + "b" * 32]
        summary = client.get("/v1/errors/summary").json()
        assert summary["total"] == 3
        assert {c["code"]: c["count"] for c in summary["by_code"]} == {"render_failed": 1, "job_interrupted": 1, "unknown_failure": 1}
        assert all(c["title"] and c["suggested_action_pt"] for c in summary["by_code"])
        assert {k["kind"]: k["count"] for k in summary["by_kind"]} == {"job": 3}


def test_production_and_workflow_items_link_to_their_origin(settings):
    with client_for(settings) as client:
        workflow = create(client)
        with pg_connect(settings) as db:
            step = db.execute("SELECT id, step_key FROM horus.jobs WHERE workflow_id=%s ORDER BY step_index LIMIT 1", (workflow["id"],)).fetchone()
            db.execute("UPDATE horus.jobs SET status='blocked', error=%s, updated_at='2026-11-12T10:00:00+00:00' WHERE id=%s", (SECRET, step[0]))
            db.execute("UPDATE horus.workflows SET status='failed' WHERE id=%s", (workflow["id"],))
        items = client.get("/v1/errors").json()["items"]
        assert len(items) == 1  # a análise e a etapa que a derrubou contam uma vez só
        assert items[0]["kind"] == "workflow_step" and items[0]["code"] == "workflow_step_blocked" and items[0]["stage"] == step[1]
        assert items[0]["ref"]["id"] == workflow["id"]
        assert client.get("/v1/errors", params={"kind": "workflow_step"}).json()["items"][0]["id"] == items[0]["id"]


def test_no_leak_of_paths_secrets_or_free_text(settings):
    with seeded(settings) as client:
        raw = client.get("/v1/errors").text + client.get("/v1/errors/summary").text
        for forbidden in ("segredo", "abc123SECRET", "roteiro.txt", "operador", "stderr_tail", "../"):
            assert forbidden not in raw
        for item in json.loads(client.get("/v1/errors").text)["items"]:
            assert set(item) == {"id", "kind", "status", "code", "stage", "at", "title", "message_pt", "suggested_action_pt", "ref",
                                 "duration_seconds", "exit_code"}
            assert item["code"] in CATALOG


def test_pagination_cursor_and_limits(settings):
    with seeded(settings) as client:
        first = client.get("/v1/errors", params={"limit": 2}).json()
        assert len(first["items"]) == 2 and first["next_after"]
        second = client.get("/v1/errors", params={"limit": 2, "after": first["next_after"]}).json()
        assert [i["id"] for i in second["items"]] == ["job:" + "a" * 32] and second["next_after"] is None
        assert client.get("/v1/errors", params={"limit": 0}).status_code == 422
        assert client.get("/v1/errors", params={"limit": 201}).status_code == 422
        assert len(client.get("/v1/errors", params={"limit": 1}).json()["items"]) == 1


@pytest.mark.parametrize("params", [{"after": "!!não-é-cursor"}, {"kind": "outro"}, {"code": "Código Ruim"}, {"stage": "../x"},
                                    {"since": "ontem"}, {"until": "2026-13-45"}, {"after": "x" * 301}])
def test_invalid_parameters_are_422_without_echo(settings, params):
    with seeded(settings) as client:
        response = client.get("/v1/errors", params=params)
        assert response.status_code == 422
        assert client.get("/v1/errors/summary", params={"since": "ontem"}).status_code == 422


@pytest.mark.parametrize("path", ["/v1/errors", "/v1/errors/summary"])
def test_requires_authentication(settings, path):
    with seeded(settings) as client:
        assert client.get(path, headers={"Authorization": "Bearer errado"}).status_code == 401
        assert client.get(path, headers={"Authorization": ""}).status_code == 401


def test_read_only_and_no_schema_change(settings):
    with seeded(settings) as client:
        with pg_connect(settings) as db:
            before = db.execute("SELECT COUNT(*), MAX(updated_at) FROM horus.jobs").fetchone()
            before_schema = schema_state(db)
        client.get("/v1/errors")
        client.get("/v1/errors/summary")
        assert client.post("/v1/errors").status_code == 405
        with pg_connect(settings) as db:
            assert db.execute("SELECT COUNT(*), MAX(updated_at) FROM horus.jobs").fetchone() == before
            assert schema_state(db) == before_schema
