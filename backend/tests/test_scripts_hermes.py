"""Etapa script: somente servidor falso e bancos temporários."""
import json
from dataclasses import replace

import pytest

from support_a import (client_for, create as local_create, hermes_settings as settings,
                       pg_admin_connect, review as local_review, settings as local_settings,
                       wait as local_wait)  # noqa: F401
from test_workflow_hermes import (hermes, create, pending, authorize,
                                  wait, Scenario, echo, CONTRACTS)  # noqa: F401
from test_scripts import generate


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
def test_script_authorization_reservation_validation_and_replay(settings, hermes, pg_app_cluster, pg_app_database, valid):
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
        with pg_admin_connect(pg_app_cluster, pg_app_database, as_dict=True) as db:
            run = db.execute("SELECT * FROM horus.cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            request = run["request"]
            envelope = json.loads(request["input"])
            assert set(envelope) == {"contract_version", "stage", "task_id", "attempt_id", "revision", "proposal", "allowed_sources", "constraints"}
            assert envelope["stage"] == "script"
            assert set(envelope["proposal"]) == {"proposal_id", "title", "premise", "angle", "format"}
            assert all(set(source) == {"source_id", "title"} for source in envelope["allowed_sources"])
            assert "horus_cost_reservation_usd" in request["metadata"]
            assert run["status"] == ("completed" if valid else "invalid_output")
            assert db.execute("SELECT COUNT(*) FROM horus.authorizations WHERE consumed_attempt_id=%s", (run["attempt_id"],)).fetchone()["count"] == 1
            assert db.execute("SELECT COUNT(*) FROM horus.scripts").fetchone()["count"] == int(valid)
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


def test_unknown_outcome_blocks_new_authorization(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch):
    from horus_backend.workflow_hermes import HermesRunClient, HermesHTTPError
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        def unavailable(self, run_id):
            raise HermesHTTPError(404, "missing", "unavailable")
        monkeypatch.setattr(HermesRunClient, "status", unavailable)
        assert generate(client, workflow, source="hermes", authorize=True).status_code == 409
        assert generate(client, workflow, key="different", source="hermes", authorize=True).status_code == 409
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert db.execute("SELECT COUNT(*) FROM horus.cognitive_runs").fetchone()[0] == 2
            assert db.execute("SELECT status FROM horus.cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0] == "unknown"
            assert db.execute("SELECT COUNT(*) FROM horus.scripts").fetchone()[0] == 0


def test_budget_exhaustion_never_submits_script(settings, hermes, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
    limited = replace(settings, max_runs_per_day=1)
    before = len(hermes.runs)
    with client_for(limited) as client:
        assert generate(client, workflow, source="hermes", authorize=True).status_code == 409
        assert len(hermes.runs) == before
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert db.execute("SELECT COUNT(*) FROM horus.scripts").fetchone()[0] == 0


def test_script_gate_blocks_before_any_intent(settings, hermes, pg_app_cluster, pg_app_database, tmp_path):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
    blocked = replace(settings, hermes_profile=tmp_path / "absent")
    with client_for(blocked) as client:
        for extra in ({}, {"source": "hermes", "authorize": True}):
            response = generate(client, workflow, **extra)
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "security_gate"
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert db.execute("SELECT COUNT(*) FROM horus.cognitive_runs").fetchone()[0] == 1


def test_simulated_and_manual_bytes_survive_restart_and_keep_origin(local_settings, pg_app_cluster, pg_app_database):
    records = []
    with client_for(local_settings) as client:
        for index, manual in enumerate((False, True)):
            workflow = local_wait(client, local_create(client, key=f"workflow-{index}")["id"])
            local_review(client, workflow)
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
    with client_for(local_settings) as client:
        for index, (workflow, extra, expected, replay) in enumerate(records):
            assert generate(client, workflow, key=f"script-{index}", **extra).content == replay
            record = json.loads(expected)
            assert client.get(f"/v1/scripts/{record['id']}").content == expected
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert [row[0] for row in db.execute("SELECT origin FROM horus.scripts ORDER BY created_at")] == ["simulated", "human"]
