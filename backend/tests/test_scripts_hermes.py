"""Etapa script: somente servidor falso e bancos temporários."""
import json
import sqlite3
from dataclasses import replace

import pytest

from test_workflow_hermes import (settings, hermes, client_for, create, pending, authorize,
                                 wait, Scenario, echo, CONTRACTS)
from test_scripts import generate
from horus_backend.migrations import migrate


def approved(client, server):
    server.scenarios.append(Scenario(output=echo()))
    workflow = pending(client, create(client)["id"])
    authorize(client, workflow)
    workflow = wait(client, workflow["id"], lambda w: w["status"] == "awaiting_approval")
    response = client.post(f"/v1/workflows/{workflow['id']}/review", headers={"Idempotency-Key": "approve"},
        json={"expected_revision": workflow["revision"], "artifact_id": workflow["current_artifact_id"], "decision": "approve"})
    assert response.status_code == 200, response.text
    return workflow


@pytest.mark.parametrize("valid", [True, False])
def test_script_authorization_reservation_validation_and_replay(settings, hermes, valid):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        assert generate(client, workflow).status_code == 409
        assert generate(client, workflow, source="hermes", authorize=1).status_code == 422
        output = json.loads((CONTRACTS / "fixtures" / "script-saida-valida.json").read_text(encoding="utf-8"))
        if not valid:
            output["sections"][1]["source_ids"] = ["unapproved"]
        hermes.scenarios.append(Scenario(output=echo(output)))
        response = generate(client, workflow, source="hermes", authorize=True)
        assert response.status_code == (201 if valid else 409), response.text
        with client.app.state.store.connect() as db:
            run = db.execute("SELECT * FROM cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            request = json.loads(run["request"])
            envelope = json.loads(request["input"])
            assert set(envelope) == {"contract_version", "stage", "task_id", "attempt_id", "revision", "proposal", "allowed_sources", "constraints"}
            assert envelope["stage"] == "script"
            assert set(envelope["proposal"]) == {"proposal_id", "title", "premise", "angle", "format"}
            assert all(set(source) == {"source_id", "title"} for source in envelope["allowed_sources"])
            assert "horus_cost_reservation_usd" in request["metadata"]
            assert run["status"] == ("completed" if valid else "invalid_output")
            assert db.execute("SELECT COUNT(*) FROM authorizations WHERE consumed_attempt_id=?", (run["attempt_id"],)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] == int(valid)
        replay = generate(client, workflow, source="hermes", authorize=True)
        assert replay.status_code == (200 if valid else 409)
        assert len(hermes.runs) == 2
        if valid:
            assert replay.json() == response.json()
            assert response.json()["origin"] == response.json()["authored_by"] == "hermes"
            assert response.json()["document"]["simulated"] is False
        else:
            hermes.scenarios.append(Scenario(output=echo(json.loads(
                (CONTRACTS / "fixtures" / "script-saida-valida.json").read_text(encoding="utf-8")))))
            assert generate(client, workflow, key="new-authorization", source="hermes", authorize=True).status_code == 201


def test_unknown_outcome_blocks_new_authorization(settings, hermes, monkeypatch):
    from horus_backend.workflow_hermes import HermesRunClient, HermesHTTPError
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        def unavailable(self, run_id):
            raise HermesHTTPError(404, "missing", "unavailable")
        monkeypatch.setattr(HermesRunClient, "status", unavailable)
        assert generate(client, workflow, source="hermes", authorize=True).status_code == 409
        assert generate(client, workflow, key="different", source="hermes", authorize=True).status_code == 409
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM cognitive_runs").fetchone()[0] == 2
            assert db.execute("SELECT status FROM cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0] == "unknown"
            assert db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] == 0


def test_budget_exhaustion_never_submits_script(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
    limited = replace(settings, max_runs_per_day=1)
    before = len(hermes.runs)
    with client_for(limited) as client:
        assert generate(client, workflow, source="hermes", authorize=True).status_code == 409
        assert len(hermes.runs) == before
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] == 0


def test_script_gate_blocks_before_any_intent(settings, hermes, tmp_path):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
    blocked = replace(settings, hermes_profile=tmp_path / "absent")
    with client_for(blocked) as client:
        for extra in ({}, {"source": "hermes", "authorize": True}):
            response = generate(client, workflow, **extra)
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "security_gate"
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM cognitive_runs").fetchone()[0] == 1


def test_v9_upgrade_preserves_legacy_records_and_reading(tmp_path, monkeypatch):
    from horus_backend import migrations
    path = tmp_path / "v9.sqlite3"
    with monkeypatch.context() as old:
        old.setattr(migrations, "SCHEMA_VERSION", 9)
        migrate(path)
    # Existing rows are supplied using the real legacy service in the API regression below;
    # the SQL projection used by v9 remains valid after adding the nullable column.
    migrate(path)
    migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == __import__("horus_backend.migrations", fromlist=["SCHEMA_VERSION"]).SCHEMA_VERSION
        assert "generator" in {row[1] for row in db.execute("PRAGMA table_info(scripts)")}
        assert db.execute("SELECT origin,document,review FROM scripts").fetchall() == []


def test_simulated_and_manual_bytes_survive_v9_upgrade(tmp_path, monkeypatch):
    from test_workflows import settings as fixture_settings, client_for as local_client
    from horus_backend import migrations
    cfg = fixture_settings.__wrapped__(tmp_path)
    records = []
    with monkeypatch.context() as old:
        old.setattr(migrations, "SCHEMA_VERSION", 9)
        with local_client(cfg) as client:
            for index, manual in enumerate((False, True)):
                from test_workflows import create as local_create, wait as local_wait, review
                workflow = local_wait(client, local_create(client, key=f"workflow-{index}")["id"])
                review(client, workflow)
                extra = {}
                if manual:
                    from horus_backend.scripts import simulate_script
                    document = simulate_script({"title": "Manual"}, "", set())
                    document["simulated"] = False
                    extra = {"source": "manual", "document": document}
                response = generate(client, workflow, key=f"script-{index}", **extra)
                assert response.status_code == 201
                replay = generate(client, workflow, key=f"script-{index}", **extra)
                records.append((workflow, extra, response.content, replay.content))
    with local_client(cfg) as client:
        for index, (workflow, extra, expected, replay) in enumerate(records):
            assert generate(client, workflow, key=f"script-{index}", **extra).content == replay
            record = json.loads(expected)
            assert client.get(f"/v1/scripts/{record['id']}").content == expected
        with client.app.state.store.connect() as db:
            assert [row[0] for row in db.execute("SELECT origin FROM scripts ORDER BY created_at")] == ["simulated", "human"]
