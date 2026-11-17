"""Revisão adversarial da etapa `script` (HOR-134): somente servidor falso e bancos temporários."""
import json
from dataclasses import replace

import psycopg
import pytest

from support_a import client_for, hermes_settings as settings, pg_admin_connect  # noqa: F401
from test_workflow_hermes import (hermes, create, pending, authorize,
                                  wait, Scenario, echo, CONTRACTS)  # noqa: F401
from test_scripts import generate
from test_scripts_hermes import approved
from horus_backend.security import budget_usage, daily_runs, daily_tokens

VALID = json.loads((CONTRACTS / "fixtures" / "script-saida-valida.json").read_text(encoding="utf-8"))


def valid_output(**changes):
    return echo({**VALID, **changes})


def counts(pg_app_cluster, pg_app_database):
    with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
        return {name: db.execute(f"SELECT COUNT(*) FROM horus.{name}").fetchone()[0]
                for name in ("scripts", "cognitive_runs", "authorizations", "script_receipts")}


def run_script(client, hermes, workflow, output, key="k1"):
    hermes.scenarios.append(Scenario(output=output))
    return generate(client, workflow, key=key, source="hermes", authorize=True)


# --- 1. Nenhum caminho chama o Hermes sem autorização, gate, reserva e cognitive_runs --------------

def test_script_run_counts_against_budget_runs_and_tokens(settings, hermes):
    """O gasto do roteiro precisa entrar em execuções/dia, tokens/dia e orçamento (reserva e gasto)."""
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        store = client.app.state.store
        before = (daily_runs(store), daily_tokens(settings, store), budget_usage(store)["estimated_spent_total_usd"])
        assert run_script(client, hermes, workflow, valid_output()).status_code == 201
        assert daily_runs(store) == before[0] + 1
        assert daily_tokens(settings, store) > before[1]
        assert budget_usage(store)["estimated_spent_total_usd"] > before[2]


def test_script_reservation_is_visible_while_remote_run_is_unknown(settings, hermes, monkeypatch):
    from horus_backend.workflow_hermes import HermesRunClient, HermesHTTPError
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        store = client.app.state.store
        before = daily_tokens(settings, store), budget_usage(store)["reserved_usd"]
        def unavailable(self, run_id):
            raise HermesHTTPError(404, "missing", "unavailable")
        monkeypatch.setattr(HermesRunClient, "status", unavailable)
        assert run_script(client, hermes, workflow, valid_output()).status_code == 409
        assert daily_tokens(settings, store) == before[0] + settings.max_tokens_per_run
        assert budget_usage(store)["reserved_usd"] > before[1]


def test_daily_run_cap_includes_script_runs(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
    capped = replace(settings, max_runs_per_day=2)  # análise + roteiro = 2
    with client_for(capped) as client:
        assert run_script(client, hermes, workflow, valid_output()).status_code == 201
        report = client.get("/v1/security/status").json()
        assert "daily_run_limit" in {item["code"] for item in report["blockers"]}


def test_simulated_cognitive_client_workflow_never_reaches_hermes(settings, hermes, pg_app_cluster, pg_app_database):
    """Workflow de cliente simulado fica fora do orçamento: não pode virar chamada real escondida."""
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            db.execute("UPDATE horus.workflows SET cognitive_client='simulated' WHERE id=%s", (workflow["id"],))
        before = len(hermes.runs)
        response = run_script(client, hermes, workflow, valid_output())
        assert response.status_code == 409, response.text
        assert len(hermes.runs) == before
        assert counts(pg_app_cluster, pg_app_database)["scripts"] == 0


@pytest.mark.parametrize("extra", [
    {}, {"source": "hermes"}, {"source": "hermes", "authorize": False}, {"source": "hermes", "authorize": "true"},
    {"source": "hermes", "authorize": 1}, {"source": "hermes", "authorize": None}, {"authorize": True}, {"source": "manual"},
    {"source": "hermes", "authorize": True, "extra": 1},
])
def test_no_authorization_variant_reaches_hermes(settings, hermes, pg_app_cluster, pg_app_database, extra):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        before, snapshot = len(hermes.runs), counts(pg_app_cluster, pg_app_database)
        hermes.scenarios.append(Scenario(output=valid_output()))
        response = generate(client, workflow, **extra)
        assert response.status_code in {409, 422}
        assert len(hermes.runs) == before and counts(pg_app_cluster, pg_app_database) == snapshot


def test_replay_with_different_body_is_rejected_without_new_run(settings, hermes, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        assert run_script(client, hermes, workflow, valid_output()).status_code == 201
        runs, snapshot = len(hermes.runs), counts(pg_app_cluster, pg_app_database)
        for body in ({"expected_revision": workflow["revision"] + 1},
                     {"proposal_artifact_id": "f" * 32}):
            hermes.scenarios.append(Scenario(output=valid_output()))
            response = client.post(f"/v1/workflows/{workflow['id']}/scripts", headers={"Idempotency-Key": "k1"},
                json={"expected_revision": workflow["revision"], "proposal_artifact_id": workflow["current_artifact_id"],
                      "source": "hermes", "authorize": True, **body})
            assert response.status_code == 409
        # Replay do mesmo corpo como roteiro simulado/manual com a mesma chave também não chama ninguém.
        assert generate(client, workflow, key="k1").status_code == 409
        assert len(hermes.runs) == runs and counts(pg_app_cluster, pg_app_database) == snapshot


def test_authorization_of_other_workflow_or_unapproved_workflow_is_useless(settings, hermes, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        first = approved(client, hermes)
        # Segundo workflow ainda aguardando aprovação humana.
        hermes.scenarios.append(Scenario(output=echo()))
        second = pending(client, create(client, key="pedido-2")["id"])
        authorize(client, second, key="auth-2")
        second = wait(client, second["id"], lambda w: w["status"] == "awaiting_approval")
        before, snapshot = len(hermes.runs), counts(pg_app_cluster, pg_app_database)
        hermes.scenarios.append(Scenario(output=valid_output()))
        assert generate(client, second, key="x", source="hermes", authorize=True).status_code == 409
        # Revisão e artefato de outro workflow.
        crossed = {**second, "revision": first["revision"], "current_artifact_id": first["current_artifact_id"]}
        assert generate(client, crossed, key="y", source="hermes", authorize=True).status_code == 409
        assert len(hermes.runs) == before and counts(pg_app_cluster, pg_app_database) == snapshot


def test_stale_revision_never_reaches_hermes(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        before = len(hermes.runs)
        stale = {**workflow, "revision": workflow["revision"] + 1}
        assert run_script(client, hermes, stale, valid_output()).status_code == 409
        assert len(hermes.runs) == before


def test_each_execution_creates_and_consumes_its_own_authorization(settings, hermes, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        hermes.scenarios.append(Scenario(output="", final_status="failed"))
        assert generate(client, workflow, key="a", source="hermes", authorize=True).status_code == 409
        # Mesma chave: nada de reaproveitar a autorização consumida.
        runs = len(hermes.runs)
        assert generate(client, workflow, key="a", source="hermes", authorize=True).status_code == 409
        assert len(hermes.runs) == runs
        assert run_script(client, hermes, workflow, valid_output(), key="b").status_code == 201
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            rows = db.execute("SELECT consumed_attempt_id,max_runs,scope FROM horus.authorizations WHERE idempotency_key LIKE 'script:%'").fetchall()
            assert len(rows) == 2 and all(row[0] and row[1] == 1 and row[2] == "cognitive_run" for row in rows)
            assert db.execute("SELECT COUNT(*) FROM horus.cognitive_runs").fetchone()[0] == 3  # análise + 2 roteiros


def test_executor_swapped_to_simulated_refuses_hermes_requests(settings, hermes, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
    simulated = replace(settings, workflow_executor="simulated")
    with client_for(simulated) as client:
        before = len(hermes.runs)
        hermes.scenarios.append(Scenario(output=valid_output()))
        response = generate(client, workflow, key="swap", source="hermes", authorize=True)
        assert response.status_code == 409
        assert len(hermes.runs) == before
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert db.execute("SELECT COUNT(*) FROM horus.scripts").fetchone()[0] == 0


def test_gate_closing_after_prepare_blocks_submission(settings, hermes, monkeypatch, tmp_path):
    """O gate é repetido imediatamente antes do envio, depois de reserva e autorização."""
    from horus_backend import workflow_hermes
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        before = len(hermes.runs)
        calls = {"n": 0}
        real = workflow_hermes.require_secure
        def flaky(cfg, *args):
            calls["n"] += 1
            if calls["n"] >= 1:  # o serviço usa security.require_secure; esta é a repetição antes do envio
                raise workflow_hermes.SecurityGate("security_gate: fechado")
            return real(cfg, *args)
        monkeypatch.setattr(workflow_hermes, "require_secure", flaky)
        assert run_script(client, hermes, workflow, valid_output()).status_code == 409
        assert len(hermes.runs) == before


# --- 4. Saída hostil do Hermes -----------------------------------------------------------------------

def hostile_sections(count, **override):
    kinds = ["hook"] + ["body"] * (count - 2) + ["conclusion"]
    return [{"id": f"s{i}", "kind": kind, "heading": "T", "narration": "Texto.", "source_ids": [], **override}
            for i, kind in enumerate(kinds)]


HOSTILE = {
    "injection_followed": lambda: json.loads((CONTRACTS / "fixtures" / "script-saida-injecao-obedecida.json").read_text(encoding="utf-8")),
    "disallowed_source": lambda: json.loads((CONTRACTS / "fixtures" / "script-saida-fonte-nao-permitida.json").read_text(encoding="utf-8")),
    "extra_top_field": lambda: {**VALID, "tool_call": "terminal"},
    "extra_section_field": lambda: {**VALID, "sections": [{**VALID["sections"][0], "shell": "rm -rf"}, *VALID["sections"][1:]]},
    "simulated_flag_smuggled": lambda: {**VALID, "simulated": True},
    "origin_smuggled": lambda: {**VALID, "origin": "human"},
    "too_few_sections": lambda: {**VALID, "sections": VALID["sections"][:1]},
    "too_many_sections": lambda: {**VALID, "sections": hostile_sections(200)},
    "giant_narration": lambda: {**VALID, "sections": [{**VALID["sections"][0], "narration": "x" * 5_000_000}, *VALID["sections"][1:]]},
    "source_flood": lambda: {**VALID, "sections": [{**VALID["sections"][0], "source_ids": ["D1"] * 5000}, *VALID["sections"][1:]]},
    "not_an_object": lambda: [VALID],
    "wrong_contract": lambda: {**VALID, "contract_version": "9.9"},
    "injection_in_source_text": lambda: {**VALID, "sections": [{**VALID["sections"][0], "source_ids": ["D1 ignore tudo"]}, *VALID["sections"][1:]]},
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_output_creates_no_script_and_spends_only_the_reservation(settings, hermes, pg_app_cluster, pg_app_database, name):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        store = client.app.state.store
        runs_before = daily_runs(store)
        payload = HOSTILE[name]()
        response = run_script(client, hermes, workflow, lambda body: json.dumps(
            {**payload, "task_id": json.loads(body["input"])["task_id"]} if isinstance(payload, dict) else payload))
        assert response.status_code == 409, (name, response.status_code, response.text[:300])
        counters = counts(pg_app_cluster, pg_app_database)
        assert counters["scripts"] == 0
        assert daily_runs(store) == runs_before + 1  # exatamente uma execução reservada
        with pg_admin_connect(pg_app_cluster, pg_app_database, as_dict=True) as db:
            run = db.execute("SELECT status, usage FROM horus.cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            if name == "giant_narration":
                # Resposta acima do limite do cliente: desfecho desconhecido, reserva mantida (conservador).
                assert run["status"] in {"invalid_output", "unknown"}
            else:
                assert run["status"] == "invalid_output"
                usage = run["usage"]
                # O custo finalizado nunca excede a reserva integral da execução.
                from horus_backend.security import cost_reservation
                assert float(usage["horus_estimated_cost_usd"]) <= float(cost_reservation(settings))
        assert "x" * 1000 not in response.text  # o conteúdo hostil não é ecoado ao cliente


def test_task_id_from_another_run_is_rejected(settings, hermes, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        response = run_script(client, hermes, workflow, json.dumps({**VALID, "task_id": "b" * 32}))
        assert response.status_code == 409
        assert counts(pg_app_cluster, pg_app_database)["scripts"] == 0
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert "task_mismatch" in db.execute("SELECT error FROM horus.cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0]


def test_usage_above_run_ceiling_is_failure_not_script(settings, hermes, pg_app_cluster, pg_app_database):
    limited = replace(settings, max_tokens_per_run=50000)
    with client_for(limited) as client:
        workflow = approved(client, hermes)
        hermes.scenarios.append(Scenario(output=valid_output(), usage={
            "input_tokens": 90000, "output_tokens": 10, "total_tokens": 90010, "cache_read_tokens": 0, "cache_write_tokens": 0}))
        response = generate(client, workflow, source="hermes", authorize=True)
        assert response.status_code == 409
        assert counts(pg_app_cluster, pg_app_database)["scripts"] == 0


def test_valid_hermes_script_is_never_human_nor_simulated_in_api(settings, hermes, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        record = run_script(client, hermes, workflow, valid_output()).json()
        listed = client.get(f"/v1/workflows/{workflow['id']}/scripts").json()["items"][0]
        for item in (record, listed, client.get(f"/v1/scripts/{record['id']}").json()):
            assert item["origin"] == item["authored_by"] == "hermes"
            assert item["document"]["simulated"] is False
            assert "generator" not in item
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            row = db.execute("SELECT origin,generator FROM horus.scripts").fetchone()
            assert tuple(row) == ("simulated", "hermes")  # CHECK legado intacto; projeção decide
            with pytest.raises(psycopg.IntegrityError):
                db.execute("UPDATE horus.scripts SET generator='human'")
            with pytest.raises(psycopg.IntegrityError):
                db.execute("UPDATE horus.scripts SET generator='simulated'")
            with pytest.raises(psycopg.IntegrityError):
                db.execute("UPDATE horus.scripts SET origin='hermes'")


def test_hermes_script_review_and_new_version_keep_provenance(settings, hermes):
    from test_scripts import decide
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        record = run_script(client, hermes, workflow, valid_output()).json()
        reviewed = decide(client, record, "approve").json()
        assert reviewed["origin"] == "hermes" and reviewed["status"] == "approved"


# --- 5. Desfecho remoto desconhecido e reinício ---------------------------------------------------------

def test_restart_after_unknown_outcome_never_resubmits_or_double_spends(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch):
    from horus_backend.workflow_hermes import HermesRunClient, HermesHTTPError
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        with monkeypatch.context() as patch:
            def unavailable(self, run_id):
                raise HermesHTTPError(404, "missing", "unavailable")
            patch.setattr(HermesRunClient, "status", unavailable)
            assert run_script(client, hermes, workflow, valid_output()).status_code == 409
    submitted = len(hermes.runs)
    with client_for(settings) as client:  # reinício
        store = client.app.state.store
        spent = (daily_runs(store), daily_tokens(settings, store))
        for key in ("k1", "novo"):
            hermes.scenarios.append(Scenario(output=valid_output()))
            assert generate(client, workflow, key=key, source="hermes", authorize=True).status_code == 409
        assert len(hermes.runs) == submitted
        assert (daily_runs(store), daily_tokens(settings, store)) == spent
        assert counts(pg_app_cluster, pg_app_database)["scripts"] == 0
        # A reserva do desfecho desconhecido continua contada após o reinício.
        assert spent[1] >= settings.max_tokens_per_run
        # O workflow aprovado não foi corrompido pela recuperação.
        assert client.get(f"/v1/workflows/{workflow['id']}").json()["status"] == "approved"


def test_crash_mid_run_leaves_reservation_blocks_resubmission_and_keeps_workflow_approved(settings, hermes, pg_app_cluster, pg_app_database):
    """Simula processo morto com execução em voo (`admitted`), validando recuperação na inicialização."""
    import threading
    import time
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        hermes.scenarios.append(Scenario(output=valid_output(), hold=True))
        thread = threading.Thread(target=lambda: generate(client, workflow, source="hermes", authorize=True))
        thread.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
                row = db.execute("SELECT id, attempt_id, status FROM horus.cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            if row and row[2] == "admitted" and len(hermes.runs) == 2:
                run_id_db, attempt_id = row[0], row[1]
                break
            time.sleep(.05)
        else:
            pytest.fail("execução não admitida")
        for run_id in list(hermes.runs):
            hermes.release(run_id) if hermes.runs[run_id].status not in {"completed"} else None
        thread.join(15)
    with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
        from horus_backend.workflows import encode

        pending_resp = encode({"pending": True})
        db.execute(
            "UPDATE horus.script_receipts SET response=%s::jsonb, response_canonical=%s WHERE scope=%s",
            (pending_resp, pending_resp.encode("utf-8"), f"generate:{workflow['id']}"),
        )
        db.execute("DELETE FROM horus.scripts")
        db.execute("UPDATE horus.cognitive_runs SET status='admitted', remote_status=NULL, usage=NULL, error=NULL WHERE id=%s", (run_id_db,))
        db.execute("UPDATE horus.job_attempts SET status='interrupted', finished_at=NULL, error=NULL WHERE id=%s", (attempt_id,))
    submitted = len(hermes.runs)
    with client_for(settings) as client:
        store = client.app.state.store
        assert client.get(f"/v1/workflows/{workflow['id']}").json()["status"] == "approved"
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert db.execute("SELECT status FROM horus.cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0] == "unknown"
            assert db.execute("SELECT COUNT(*) FROM horus.scripts").fetchone()[0] == 0
        assert daily_tokens(settings, store) >= settings.max_tokens_per_run
        for key in ("k1", "outra"):
            assert generate(client, workflow, key=key, source="hermes", authorize=True).status_code == 409
        assert len(hermes.runs) == submitted


def test_scripts_check_constraints_pg(pg_app_cluster, pg_app_database):
    with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
        defs = " ".join(
            row[0] for row in db.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'horus.scripts'::regclass AND contype = 'c'"
            ).fetchall()
        )
        assert "origin" in defs and "simulated" in defs and "human" in defs
        assert "generator" in defs and "hermes" in defs


# --- 5b. Conciliação de desfecho desconhecido ----------------------------------------------------------

def reconcile(client, workflow, revision=None):
    return client.post(f"/v1/workflows/{workflow['id']}/scripts/reconcile",
                       json={"expected_revision": revision or workflow["revision"]})


def unknown_run(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch, output):
    """Deixa uma execução de roteiro com desfecho desconhecido e devolve o workflow; o cliente é encerrado."""
    from horus_backend.workflow_hermes import HermesRunClient, HermesHTTPError
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        with monkeypatch.context() as patch:
            def unavailable(self, run_id):
                raise HermesHTTPError(503, "down", "unavailable")
            patch.setattr(HermesRunClient, "status", unavailable)
            assert run_script(client, hermes, workflow, output).status_code == 409
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert db.execute("SELECT status FROM horus.cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0] == "unknown"
    return workflow


def test_reconcile_applies_late_valid_result_once_without_new_spend(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch):
    workflow = unknown_run(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch, valid_output())
    with client_for(settings) as client:  # reinício com pendente
        store = client.app.state.store
        spent = (daily_runs(store), daily_tokens(settings, store))
        response = reconcile(client, workflow)
        assert response.status_code == 200, response.text
        script = response.json()
        assert script["origin"] == script["authored_by"] == "hermes" and script["document"]["simulated"] is False
        assert len(hermes.runs) == 2  # nenhum envio novo
        assert daily_runs(store) == spent[0]
        assert daily_tokens(settings, store) < spent[1]  # reserva substituída pelo uso conhecido, nunca somada
        repeated = reconcile(client, workflow)  # idempotente
        assert repeated.status_code == 200 and repeated.json()["id"] == script["id"]
        assert counts(pg_app_cluster, pg_app_database)["scripts"] == 1 and len(hermes.runs) == 2
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert db.execute("SELECT status FROM horus.cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0] == "completed"
            assert db.execute("SELECT status FROM horus.job_attempts WHERE executor='script.hermes'").fetchone()[0] == "succeeded"
        # A chave original agora responde com o roteiro (recibo normal).
        assert generate(client, workflow, source="hermes", authorize=True, key="k1").json()["id"] == script["id"]


def test_reconcile_late_invalid_result_closes_without_script_and_needs_new_authorization(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch):
    workflow = unknown_run(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch, json.dumps({**VALID, "task_id": "b" * 32}))
    with client_for(settings) as client:
        store = client.app.state.store
        spent = (daily_runs(store), daily_tokens(settings, store))
        assert reconcile(client, workflow).status_code == 409
        assert counts(pg_app_cluster, pg_app_database)["scripts"] == 0 and len(hermes.runs) == 2
        assert (daily_runs(store), daily_tokens(settings, store)) <= (spent[0], spent[1])
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            run = db.execute("SELECT status FROM horus.cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            assert run[0] == "invalid_output"
        assert reconcile(client, workflow).status_code == 409  # nada pendente; sem novo envio
        assert len(hermes.runs) == 2
        # A chave antiga continua fechada; só nova autorização (nova chave) tenta de novo.
        assert generate(client, workflow, source="hermes", authorize=True, key="k1").status_code == 409
        assert len(hermes.runs) == 2
        assert run_script(client, hermes, workflow, valid_output(), key="nova").status_code == 201
        assert len(hermes.runs) == 3


def test_reconcile_missing_remote_run_stays_pending_and_never_resubmits(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch):
    workflow = unknown_run(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch, valid_output())
    hermes.runs.clear()  # o gateway perdeu a execução
    posted = len(hermes.requests)
    with client_for(settings) as client:
        store = client.app.state.store
        tokens = daily_tokens(settings, store)
        for _ in range(2):
            assert reconcile(client, workflow).status_code == 409
        assert not any(r.get("method") == "POST" for r in hermes.requests[posted:])
        assert len(hermes.runs) == 0 and counts(pg_app_cluster, pg_app_database)["scripts"] == 0
        assert daily_tokens(settings, store) == tokens  # reserva mantida, sem gasto repetido
        assert generate(client, workflow, key="outra", source="hermes", authorize=True).status_code == 409


def test_reconcile_requires_pending_and_hermes_executor(settings, hermes, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        assert reconcile(client, workflow).status_code == 409  # nada pendente
    simulated = replace(settings, workflow_executor="simulated")
    with client_for(simulated) as client:
        assert reconcile(client, workflow).status_code == 409


def test_reconcile_gate_closed_and_stale_revision(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch, tmp_path):
    workflow = unknown_run(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch, valid_output())
    blocked = replace(settings, hermes_profile=tmp_path / "absent")
    with client_for(blocked) as client:
        response = reconcile(client, workflow)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "security_gate"
    with client_for(settings) as client:
        assert reconcile(client, workflow, revision=workflow["revision"] + 1).status_code == 409
        assert len(hermes.runs) == 2 and counts(pg_app_cluster, pg_app_database)["scripts"] == 0


def test_reconcile_valid_result_not_applicable_closes_definitively(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch):
    workflow = unknown_run(settings, hermes, pg_app_cluster, pg_app_database, monkeypatch, valid_output())
    with client_for(settings) as client:
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            db.execute("UPDATE horus.workflows SET status='rejected' WHERE id=%s", (workflow["id"],))
        assert reconcile(client, workflow).status_code == 409
        assert counts(pg_app_cluster, pg_app_database)["scripts"] == 0 and len(hermes.runs) == 2
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert db.execute("SELECT response FROM horus.script_receipts").fetchone()[0] == {"failed": True}
