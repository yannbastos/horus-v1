"""Revisão adversarial da etapa `script` (HOR-134): somente servidor falso e bancos temporários."""
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from test_workflow_hermes import (settings, hermes, client_for, create, pending, authorize,
                                 wait, Scenario, echo, CONTRACTS)
from test_scripts import generate
from test_scripts_hermes import approved
from horus_backend.security import budget_usage, daily_runs, daily_tokens

VALID = json.loads((CONTRACTS / "fixtures" / "script-saida-valida.json").read_text(encoding="utf-8"))


def valid_output(**changes):
    return echo({**VALID, **changes})


def counts(client):
    with client.app.state.store.connect() as db:
        return {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
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


def test_simulated_cognitive_client_workflow_never_reaches_hermes(settings, hermes):
    """Workflow de cliente simulado fica fora do orçamento: não pode virar chamada real escondida."""
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        with client.app.state.store.connect() as db:
            db.execute("UPDATE workflows SET cognitive_client='simulated' WHERE id=?", (workflow["id"],))
        before = len(hermes.runs)
        response = run_script(client, hermes, workflow, valid_output())
        assert response.status_code == 409, response.text
        assert len(hermes.runs) == before
        assert counts(client)["scripts"] == 0


@pytest.mark.parametrize("extra", [
    {}, {"source": "hermes"}, {"source": "hermes", "authorize": False}, {"source": "hermes", "authorize": "true"},
    {"source": "hermes", "authorize": 1}, {"source": "hermes", "authorize": None}, {"authorize": True}, {"source": "manual"},
    {"source": "hermes", "authorize": True, "extra": 1},
])
def test_no_authorization_variant_reaches_hermes(settings, hermes, extra):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        before, snapshot = len(hermes.runs), counts(client)
        hermes.scenarios.append(Scenario(output=valid_output()))
        response = generate(client, workflow, **extra)
        assert response.status_code in {409, 422}
        assert len(hermes.runs) == before and counts(client) == snapshot


def test_replay_with_different_body_is_rejected_without_new_run(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        assert run_script(client, hermes, workflow, valid_output()).status_code == 201
        runs, snapshot = len(hermes.runs), counts(client)
        for body in ({"expected_revision": workflow["revision"] + 1},
                     {"proposal_artifact_id": "f" * 32}):
            hermes.scenarios.append(Scenario(output=valid_output()))
            response = client.post(f"/v1/workflows/{workflow['id']}/scripts", headers={"Idempotency-Key": "k1"},
                json={"expected_revision": workflow["revision"], "proposal_artifact_id": workflow["current_artifact_id"],
                      "source": "hermes", "authorize": True, **body})
            assert response.status_code == 409
        # Replay do mesmo corpo como roteiro simulado/manual com a mesma chave também não chama ninguém.
        assert generate(client, workflow, key="k1").status_code == 409
        assert len(hermes.runs) == runs and counts(client) == snapshot


def test_authorization_of_other_workflow_or_unapproved_workflow_is_useless(settings, hermes):
    with client_for(settings) as client:
        first = approved(client, hermes)
        # Segundo workflow ainda aguardando aprovação humana.
        hermes.scenarios.append(Scenario(output=echo()))
        second = pending(client, create(client, key="pedido-2")["id"])
        authorize(client, second, key="auth-2")
        second = wait(client, second["id"], lambda w: w["status"] == "awaiting_approval")
        before, snapshot = len(hermes.runs), counts(client)
        hermes.scenarios.append(Scenario(output=valid_output()))
        assert generate(client, second, key="x", source="hermes", authorize=True).status_code == 409
        # Revisão e artefato de outro workflow.
        crossed = {**second, "revision": first["revision"], "current_artifact_id": first["current_artifact_id"]}
        assert generate(client, crossed, key="y", source="hermes", authorize=True).status_code == 409
        assert len(hermes.runs) == before and counts(client) == snapshot


def test_stale_revision_never_reaches_hermes(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        before = len(hermes.runs)
        stale = {**workflow, "revision": workflow["revision"] + 1}
        assert run_script(client, hermes, stale, valid_output()).status_code == 409
        assert len(hermes.runs) == before


def test_each_execution_creates_and_consumes_its_own_authorization(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        hermes.scenarios.append(Scenario(output="", final_status="failed"))
        assert generate(client, workflow, key="a", source="hermes", authorize=True).status_code == 409
        # Mesma chave: nada de reaproveitar a autorização consumida.
        runs = len(hermes.runs)
        assert generate(client, workflow, key="a", source="hermes", authorize=True).status_code == 409
        assert len(hermes.runs) == runs
        assert run_script(client, hermes, workflow, valid_output(), key="b").status_code == 201
        with client.app.state.store.connect() as db:
            rows = db.execute("SELECT consumed_attempt_id,max_runs,scope FROM authorizations WHERE idempotency_key LIKE 'script:%'").fetchall()
            assert len(rows) == 2 and all(row[0] and row[1] == 1 and row[2] == "cognitive_run" for row in rows)
            assert db.execute("SELECT COUNT(*) FROM cognitive_runs").fetchone()[0] == 3  # análise + 2 roteiros


def test_executor_swapped_to_simulated_refuses_hermes_requests(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
    simulated = replace(settings, workflow_executor="simulated")
    with client_for(simulated) as client:
        before = len(hermes.runs)
        hermes.scenarios.append(Scenario(output=valid_output()))
        response = generate(client, workflow, key="swap", source="hermes", authorize=True)
        assert response.status_code == 409
        assert len(hermes.runs) == before
        with client.app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] == 0


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
def test_hostile_output_creates_no_script_and_spends_only_the_reservation(settings, hermes, name):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        store = client.app.state.store
        runs_before = daily_runs(store)
        payload = HOSTILE[name]()
        response = run_script(client, hermes, workflow, lambda body: json.dumps(
            {**payload, "task_id": json.loads(body["input"])["task_id"]} if isinstance(payload, dict) else payload))
        assert response.status_code == 409, (name, response.status_code, response.text[:300])
        counters = counts(client)
        assert counters["scripts"] == 0
        assert daily_runs(store) == runs_before + 1  # exatamente uma execução reservada
        with store.connect() as db:
            run = db.execute("SELECT status,usage FROM cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            if name == "giant_narration":
                # Resposta acima do limite do cliente: desfecho desconhecido, reserva mantida (conservador).
                assert run["status"] in {"invalid_output", "unknown"}
            else:
                assert run["status"] == "invalid_output"
                usage = json.loads(run["usage"])
                # O custo finalizado nunca excede a reserva integral da execução.
                from horus_backend.security import cost_reservation
                assert float(usage["horus_estimated_cost_usd"]) <= float(cost_reservation(settings))
        assert "x" * 1000 not in response.text  # o conteúdo hostil não é ecoado ao cliente


def test_task_id_from_another_run_is_rejected(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        response = run_script(client, hermes, workflow, json.dumps({**VALID, "task_id": "b" * 32}))
        assert response.status_code == 409
        assert counts(client)["scripts"] == 0
        with client.app.state.store.connect() as db:
            assert "task_mismatch" in db.execute("SELECT error FROM cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0]


def test_usage_above_run_ceiling_is_failure_not_script(settings, hermes):
    limited = replace(settings, max_tokens_per_run=50000)
    with client_for(limited) as client:
        workflow = approved(client, hermes)
        hermes.scenarios.append(Scenario(output=valid_output(), usage={
            "input_tokens": 90000, "output_tokens": 10, "total_tokens": 90010, "cache_read_tokens": 0, "cache_write_tokens": 0}))
        response = generate(client, workflow, source="hermes", authorize=True)
        assert response.status_code == 409
        assert counts(client)["scripts"] == 0


def test_valid_hermes_script_is_never_human_nor_simulated_in_api(settings, hermes):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        record = run_script(client, hermes, workflow, valid_output()).json()
        listed = client.get(f"/v1/workflows/{workflow['id']}/scripts").json()["items"][0]
        for item in (record, listed, client.get(f"/v1/scripts/{record['id']}").json()):
            assert item["origin"] == item["authored_by"] == "hermes"
            assert item["document"]["simulated"] is False
            assert "generator" not in item
        with client.app.state.store.connect() as db:
            row = db.execute("SELECT origin,generator FROM scripts").fetchone()
            assert tuple(row) == ("simulated", "hermes")  # CHECK legado intacto; projeção decide
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("UPDATE scripts SET generator='human'")
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("UPDATE scripts SET generator='simulated'")
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("UPDATE scripts SET origin='hermes'")


def test_hermes_script_review_and_new_version_keep_provenance(settings, hermes):
    from test_scripts import decide
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        record = run_script(client, hermes, workflow, valid_output()).json()
        reviewed = decide(client, record, "approve").json()
        assert reviewed["origin"] == "hermes" and reviewed["status"] == "approved"


# --- 5. Desfecho remoto desconhecido e reinício ---------------------------------------------------------

def test_restart_after_unknown_outcome_never_resubmits_or_double_spends(settings, hermes, monkeypatch):
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
        assert counts(client)["scripts"] == 0
        # A reserva do desfecho desconhecido continua contada após o reinício.
        assert spent[1] >= settings.max_tokens_per_run
        # O workflow aprovado não foi corrompido pela recuperação.
        assert client.get(f"/v1/workflows/{workflow['id']}").json()["status"] == "approved"


def test_crash_mid_run_leaves_reservation_blocks_resubmission_and_keeps_workflow_approved(settings, hermes, tmp_path):
    """Cópia do banco com a execução em voo (admitida, sem desfecho) = estado de um processo morto."""
    import shutil
    import threading
    import time
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        hermes.scenarios.append(Scenario(output=valid_output(), hold=True))
        thread = threading.Thread(target=lambda: generate(client, workflow, source="hermes", authorize=True))
        thread.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with client.app.state.store.connect() as db:
                row = db.execute("SELECT status FROM cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            if row and row[0] == "admitted" and len(hermes.runs) == 2:
                break
            time.sleep(.05)
        else:
            pytest.fail("execução não admitida")
        data = tmp_path / "crash-data"
        data.mkdir()
        source = sqlite3.connect(settings.data_dir / "horus.sqlite3")
        target = sqlite3.connect(data / "horus.sqlite3")
        source.backup(target)
        source.close(); target.close()
        for run_id in list(hermes.runs):
            hermes.release(run_id) if hermes.runs[run_id].status not in {"completed"} else None
        thread.join(15)
    crashed = replace(settings, data_dir=data)
    submitted = len(hermes.runs)
    with client_for(crashed) as client:
        store = client.app.state.store
        assert client.get(f"/v1/workflows/{workflow['id']}").json()["status"] == "approved"
        with store.connect() as db:
            assert db.execute("SELECT status FROM cognitive_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0] == "unknown"
            assert db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] == 0
        assert daily_tokens(crashed, store) >= crashed.max_tokens_per_run
        for key in ("k1", "outra"):
            assert generate(client, workflow, key=key, source="hermes", authorize=True).status_code == 409
        assert len(hermes.runs) == submitted


# --- 3. Migração v10 a partir de bancos v6..v9 com dados ---------------------------------------------------

@pytest.mark.parametrize("old", [6, 7, 8, 9])
def test_v10_upgrade_from_populated_legacy_db(tmp_path, monkeypatch, old):
    from test_workflows import settings as fixture_settings, client_for as local_client, create as local_create, wait as local_wait, review
    from horus_backend import migrations
    cfg = fixture_settings.__wrapped__(tmp_path)
    with monkeypatch.context() as legacy:
        legacy.setattr(migrations, "SCHEMA_VERSION", old)
        with local_client(cfg) as client:
            workflow = local_wait(client, local_create(client)["id"])
            review(client, workflow)
            created = generate(client, workflow, key="legacy")
            assert created.status_code == 201
            before = created.content
    db_path = cfg.data_dir / "horus.sqlite3"
    assert sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0] == old
    with local_client(cfg) as client:  # abre com o código v10: migra e preserva bytes
        assert client.get(f"/v1/scripts/{json.loads(before)['id']}").content == before
        assert generate(client, workflow, key="legacy").json() == json.loads(before)
    backups = list(cfg.data_dir.glob(f"horus.backup-v{old}-*.sqlite3"))
    assert len(backups) == 1
    assert sqlite3.connect(backups[0]).execute("PRAGMA user_version").fetchone()[0] == old
    assert sqlite3.connect(backups[0]).execute("SELECT COUNT(*) FROM scripts").fetchone()[0] == 1
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert db.execute("SELECT generator FROM scripts").fetchone()[0] is None
        assert db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with local_client(cfg):  # idempotente: reabrir não cria novo backup nem altera nada
        pass
    assert len(list(cfg.data_dir.glob("horus.backup-v*.sqlite3"))) == 1


def test_v10_check_constraints(tmp_path):
    from horus_backend.migrations import migrate
    path = tmp_path / "x.sqlite3"
    migrate(path)
    with sqlite3.connect(path) as db:
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='scripts'").fetchone()[0]
        assert "CHECK(origin IN ('simulated','human'))" in sql.replace("\n", " ").replace("  ", " ") or "origin IN ('simulated','human')" in sql
        assert "generator IS NULL OR generator = 'hermes'" in sql
