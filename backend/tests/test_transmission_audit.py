"""HOR-115 / C5: registro append-only de transmissões ao Hermes, sem conteúdo. Somente servidor falso."""
import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest

from test_workflow_hermes import (settings, hermes, client_for, create, pending, authorize, wait, Scenario, echo,
                                 REQUEST)
from test_scripts import generate
from test_scripts_hermes import approved, CONTRACTS
from horus_backend.migrations import migrate

SECRET = "SEGREDO-MARCADOR-8f3a1c-NAO-VAZAR"
VALID_SCRIPT = json.loads((CONTRACTS / "fixtures" / "script-saida-valida.json").read_text(encoding="utf-8"))


def events(client, **filters):
    items, after = [], 0
    while True:
        page = client.get("/v1/security/transmissions", params={"limit": 200, "after": after}).json()
        items += page["items"]
        if page["next_after"] is None:
            break
        after = page["next_after"]
    return [item for item in items if all(item[k] == v for k, v in filters.items())]


def run_analysis(client, hermes, objective=None, key="p1"):
    hermes.scenarios.append(Scenario(output=echo()))
    body = {**REQUEST, **({"objective": objective} if objective else {})}
    response = client.post("/v1/workflows", json=body, headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    workflow = pending(client, response.json()["id"])
    authorize(client, workflow)
    return wait(client, workflow["id"], lambda w: w["status"] == "awaiting_approval")


def test_analysis_submit_and_outcome_are_recorded_with_hash_and_reconciled_usage(settings, hermes):
    with client_for(settings) as client:
        workflow = run_analysis(client, hermes)
        with client.app.state.store.connect() as db:
            run = db.execute("SELECT * FROM cognitive_runs").fetchone()
            envelope = json.loads(run["request"])["input"]
            auth = db.execute("SELECT id FROM authorizations WHERE workflow_id=?", (workflow["id"],)).fetchone()[0]
        items = events(client)
        assert [(e["kind"], e["result"]) for e in items] == [("submit", "attempted"), ("submit", "sent"), ("outcome", "completed")]
        for item in items:
            assert item["stage"] == "analysis" and item["workflow_id"] == workflow["id"] and item["run_id"] == run["id"]
            assert item["destination"] == hermes.base_url and item["provider"] == "hermes" and item["model"] is None
            assert item["authorization_id"] == auth
        for item in items[:2]:
            assert item["envelope_bytes"] == len(envelope.encode())
            assert item["envelope_sha256"] == hashlib.sha256(envelope.encode()).hexdigest()
        outcome = items[-1]
        assert outcome["tokens_total"] == 2000 and float(outcome["cost_usd"]) > 0
        assert set(items[0]) == {"seq", "id", "at", "kind", "stage", "workflow_id", "job_id", "attempt_id", "run_id", "destination",
                                 "provider", "model", "envelope_bytes", "envelope_sha256", "authorization_id", "result", "detail",
                                 "tokens_total", "cost_usd"}


def test_no_content_reaches_the_transmission_log_api_or_logs(settings, hermes, caplog):
    caplog.set_level("DEBUG")
    with client_for(settings) as client:
        run_analysis(client, hermes, objective=f"Analisar {SECRET}")
        with client.app.state.store.connect() as db:
            assert SECRET in db.execute("SELECT request FROM cognitive_runs").fetchone()[0]  # já existia onde é necessário
            dump = json.dumps([dict(row) for row in db.execute("SELECT * FROM transmissions")])
            columns = {row[1] for row in db.execute("PRAGMA table_info(transmissions)")}
        assert SECRET not in dump
        page = client.get("/v1/security/transmissions").text
        assert SECRET not in page and "instructions" not in page and "objective" not in page
        assert not {"input", "body", "content", "request", "envelope", "output", "prompt"} & columns
    assert SECRET not in caplog.text


def test_log_is_append_only(settings, hermes):
    with client_for(settings) as client:
        run_analysis(client, hermes)
        with client.app.state.store.connect() as db:
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                db.execute("UPDATE transmissions SET result='completed'")
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                db.execute("DELETE FROM transmissions")
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT INTO transmissions(id,at,kind,stage,result,envelope_sha256) VALUES('x','t','submit','a','sent','curto')")
        assert len(events(client)) == 3


def test_pagination_is_bounded_and_authenticated(settings, hermes):
    with client_for(settings) as client:
        run_analysis(client, hermes)
        first = client.get("/v1/security/transmissions", params={"limit": 2}).json()
        assert len(first["items"]) == 2 and first["next_after"] == first["items"][-1]["seq"]
        second = client.get("/v1/security/transmissions", params={"limit": 2, "after": first["next_after"]}).json()
        assert len(second["items"]) == 1 and second["next_after"] is None
        assert client.get("/v1/security/transmissions", params={"limit": 100000}).status_code == 200
        assert client.get("/v1/security/transmissions", headers={"Authorization": "Bearer x"}).status_code == 401


def test_gate_refusals_are_recorded_for_authorize_and_script(settings, hermes, tmp_path):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        flow = pending(client, create(client, key="outro")["id"])
    blocked = replace(settings, hermes_profile=tmp_path / "ausente")
    with client_for(blocked) as client:
        before = len(hermes.requests)
        response = client.post(f"/v1/workflows/{flow['id']}/authorize", headers={"Idempotency-Key": "a"},
            json={"expected_revision": flow["revision"], "job_id": flow["pending_authorization"]["job_id"]})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "security_gate"
        assert generate(client, workflow, source="hermes", authorize=True).status_code == 409
        assert generate(client, workflow).status_code == 409
        refusals = events(client, kind="gate_refusal")
        assert [(e["stage"], e["result"], e["detail"]) for e in refusals] == [
            ("authorize", "refused_gate", "security_gate"), ("script", "refused_gate", "security_gate"),
            ("script", "refused_gate", "security_gate")]
        assert all(e["envelope_sha256"] is None and e["workflow_id"] for e in refusals)
        assert len(hermes.requests) == before
        assert not events(client, kind="submit", workflow_id=flow["id"])


def test_budget_refusal_before_send_is_recorded_as_gate_refusal(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
    limited = replace(settings, max_runs_per_day=1)
    with client_for(limited) as client:
        sent = len(events(client, kind="submit"))
        assert generate(client, workflow, source="hermes", authorize=True).status_code == 409
        assert len(events(client, kind="submit")) == sent
        assert events(client, kind="gate_refusal")


def test_script_stage_replay_does_not_duplicate_transmissions(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        hermes.scenarios.append(Scenario(output=echo(VALID_SCRIPT)))
        assert generate(client, workflow, source="hermes", authorize=True).status_code == 201
        recorded = events(client, stage="script")
        assert [(e["kind"], e["result"]) for e in recorded] == [("submit", "attempted"), ("submit", "sent"), ("outcome", "completed")]
        assert recorded[0]["authorization_id"]
        assert generate(client, workflow, source="hermes", authorize=True).status_code == 200  # replay
        assert events(client, stage="script") == recorded


def test_unknown_outcome_and_reconciliation_append_new_events_without_rewriting(settings, hermes, monkeypatch):
    from horus_backend.workflow_hermes import HermesRunClient, HermesHTTPError
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        hermes.scenarios.append(Scenario(output=echo(VALID_SCRIPT)))
        with monkeypatch.context() as patch:
            def down(self, run_id):
                raise HermesHTTPError(503, "x", "x")
            patch.setattr(HermesRunClient, "status", down)
            assert generate(client, workflow, source="hermes", authorize=True).status_code == 409
        first = events(client, stage="script")
        assert first[-1]["kind"] == "outcome" and first[-1]["result"] == "unknown"
        reconciled = client.post(f"/v1/workflows/{workflow['id']}/scripts/reconcile", json={"expected_revision": workflow["revision"]})
        assert reconciled.status_code == 200
        later = events(client, stage="script")
        assert later[:len(first)] == first  # nada anterior foi alterado
        assert later[-1]["result"] == "completed" and later[-1]["tokens_total"] == 2000
        assert [e["kind"] for e in later].count("submit") == 2  # só o envio original: sem reenvio


def test_failed_run_is_recorded_as_failed_outcome(settings, hermes):
    with client_for(settings) as client:
        hermes.scenarios.append(Scenario(output="", final_status="failed"))
        flow = pending(client, create(client, key="falha")["id"])
        authorize(client, flow)
        wait(client, flow["id"], lambda w: w["status"] == "failed")
        recorded = events(client)
        assert recorded[0]["result"] == "attempted"
        assert recorded[-1]["kind"] == "outcome" and recorded[-1]["result"] == "failed"


def test_audit_write_failure_blocks_the_send(settings, hermes, monkeypatch):
    from horus_backend.transmissions import TransmissionLog

    def broken(self, *args, **kwargs):
        raise sqlite3.OperationalError("disco cheio")
    with client_for(settings) as client:
        monkeypatch.setattr(TransmissionLog, "record", broken)
        flow = pending(client, create(client, key="auditoria")["id"])
        authorize(client, flow)
        wait(client, flow["id"], lambda w: w["status"] == "failed")
        assert len(hermes.runs) == 0


def test_simulated_executors_write_no_transmission_events(tmp_path):
    from test_workflows import settings as local_settings, client_for as local_client, create as local_create, wait as local_wait
    cfg = local_settings.__wrapped__(tmp_path)
    with local_client(cfg) as client:
        local_wait(client, local_create(client)["id"])
        assert client.get("/v1/security/transmissions").json() == {"items": [], "next_after": None}


def test_status_reports_audit_available_and_probe_is_logged(settings, hermes):
    with client_for(settings) as client:
        audit = client.get("/v1/security/status").json()["audit"]
        assert audit["available"] is True and audit["content_stored"] is False and audit["append_only"] is True
        assert client.post("/v1/hermes/probe").status_code == 200
        probes = events(client, kind="probe")
        assert len(probes) == 1 and probes[0]["envelope_sha256"] is None and probes[0]["envelope_bytes"] is None


@pytest.mark.parametrize("old", [6, 9, 10])
def test_v11_upgrade_is_additive_and_idempotent(tmp_path, monkeypatch, old):
    from test_workflows import settings as fixture_settings, client_for as local_client, create as local_create, wait as local_wait, review
    from horus_backend import migrations
    cfg = fixture_settings.__wrapped__(tmp_path)
    with monkeypatch.context() as legacy:
        legacy.setattr(migrations, "SCHEMA_VERSION", old)
        with local_client(cfg) as client:
            workflow = local_wait(client, local_create(client)["id"])
            review(client, workflow)
            created = generate(client, workflow, key="legacy").json()
    path = cfg.data_dir / "horus.sqlite3"
    with local_client(cfg) as client:
        assert client.get(f"/v1/scripts/{created['id']}").json()["id"] == created["id"]
        assert client.get("/v1/security/transmissions").json()["items"] == []
    migrate(path)
    migrate(path)
    assert len(list(cfg.data_dir.glob(f"horus.backup-v{old}-*.sqlite3"))) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='transmissions'")} == {
            "transmissions_no_update", "transmissions_no_delete"}
        assert db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] == 1
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
