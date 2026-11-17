import asyncio
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from horus_backend.workflows import STAGES, WorkflowRequest, WorkflowService, simulate
from support_b import (
    WORKFLOW_REQUEST as REQUEST,
    WORKFLOW_TOKEN as TOKEN,  # noqa: F401
    client_for,  # noqa: F401
    create_workflow as create,
    pg_admin,  # noqa: F401
    pg_store,  # noqa: F401
    review_workflow as review,
    settings,  # noqa: F401
    wait_workflow as wait,
)

HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class FakeOwnedProcess:
    """Processo possuído determinístico: stdout pronto, saída 0, nada é criado no sistema operacional."""

    def __init__(self, stdout: bytes):
        from types import SimpleNamespace
        out, err = asyncio.StreamReader(), asyncio.StreamReader()
        out.feed_data(stdout)
        out.feed_eof()
        err.feed_eof()

        async def wait():
            return 0
        self.process = SimpleNamespace(stdout=out, stderr=err, returncode=0, pid=0, wait=wait)

    async def stop(self):
        pass

    def close(self):
        pass


def test_complete_workflow_is_simulated_and_keeps_worker_free(settings, client_for, monkeypatch):
    from horus_backend.hermes import HermesConnector
    from horus_backend.processes import OwnedProcess

    async def forbidden(*args, **kwargs):
        pytest.fail("O workflow simulado não deve usar processos ou Hermes.")

    with client_for(settings) as client:
        with monkeypatch.context() as guard:
            guard.setattr(HermesConnector, "probe", forbidden)
            guard.setattr(OwnedProcess, "start", forbidden)
            workflow = wait(client, create(client)["id"])
        assert [step["step_key"] for step in workflow["steps"]] == list(STAGES)
        assert all(step["status"] == "succeeded" for step in workflow["steps"])
        assert len(workflow["attempts"]) == len(workflow["artifacts"]) == 4
        assert all(item["content"]["simulated"] is True for item in workflow["artifacts"])
        assert all(item["executor"] == "simulated-v1" for item in workflow["attempts"])
        assert workflow["approvals"] == []
        assert client.app.state.manager.active_id is None
        # Aguardar revisão humana não ocupa o único worker: operação antiga continua funcionando. O que se prova aqui é a
        # fila/worker livre, não o sistema operacional: o processo da fábrica é substituído por um determinístico (a criação
        # real de processos é coberta por test_api/test_job_environment/test_process_sandbox). Antes, `failed` esporádico em
        # suíte longa com a máquina sem memória vinha do spawn real; agora a espera tem prazo generoso e o erro é exibido.
        started = []

        async def fake_factory_process(command, **kwargs):
            started.append((list(command), kwargs.get("cwd")))
            return FakeOwnedProcess(json.dumps({"id": "canal01"}).encode() + chr(10).encode())
        monkeypatch.setattr(OwnedProcess, "start", fake_factory_process)
        job = client.post("/v1/jobs", json={"operation": "factory.profile"}).json()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = client.get(f"/v1/jobs/{job['id']}").json()
            if job["status"] in {"succeeded", "failed", "blocked", "interrupted", "cancelled"}:
                break
            time.sleep(.02)
        assert job["status"] == "succeeded", job
        assert len(started) == 1 and started[0][0][0] == sys.executable and started[0][0][1].endswith("fake_factory.py")
        assert started[0][1] == settings.factory_root
        response = review(client, workflow)
        assert response.status_code == 200, response.text
        assert response.json()["workflow"]["status"] == "approved"
        assert response.json()["approval"]["operator_id"] == "local-operator"
        assert response.json()["approval"]["scope"] == "editorial_proposal"
        assert len(client.get("/v1/jobs").json()) == 5  # Aprovação não produz vídeo.


def test_create_and_review_are_idempotent_and_conflicts_are_rejected(settings, client_for):
    with client_for(settings) as client:
        first = create(client)
        assert create(client)["id"] == first["id"]
        assert client.post("/v1/workflows", json={"objective": "Outra entrada"}, headers={"Idempotency-Key": "request-1"}).status_code == 409
        workflow = wait(client, first["id"])
        a = review(client, workflow)
        b = review(client, workflow)
        assert a.status_code == b.status_code == 200
        assert a.json()["approval"]["id"] == b.json()["approval"]["id"]
        assert len(b.json()["workflow"]["approvals"]) == 1
        assert review(client, workflow, decision="reject").status_code == 409
        assert review(client, workflow, key="other-review").status_code == 409
        assert len(b.json()["workflow"]["attempts"]) == 4


def test_changes_create_versioned_artifacts_and_reject_obsolete_approval(settings, client_for):
    with client_for(settings) as client:
        old = wait(client, create(client)["id"])
        response = review(client, old, "request_changes", feedback="Mudar a abordagem para transporte", key="change-1")
        assert response.status_code == 200, response.text
        current = wait(client, old["id"])
        assert current["revision"] == 2
        assert len(current["steps"]) == len(current["artifacts"]) == len(current["attempts"]) == 8
        assert {a["id"] for a in old["artifacts"]}.issubset({a["id"] for a in current["artifacts"]})
        assert current["current_artifact_id"] != old["current_artifact_id"]
        assert review(client, old, key="obsolete").status_code == 409
        assert review(client, old, "request_changes", feedback="Mudar a abordagem para transporte", key="change-1").status_code == 200
        assert client.get(f"/v1/workflows/{old['id']}").json()["revision"] == 2
        assert review(client, current, key="approve-2").json()["workflow"]["status"] == "approved"


def test_rejection_is_terminal_and_persists(settings, client_for):
    with client_for(settings) as client:
        workflow = wait(client, create(client)["id"])
        assert review(client, workflow, decision="reject").json()["workflow"]["status"] == "rejected"
    with client_for(settings) as restarted:
        restored = restarted.get(f"/v1/workflows/{workflow['id']}").json()
        assert restored["status"] == "rejected"
        assert len(restored["approvals"]) == 1
        assert len(restored["attempts"]) == 4


def test_awaiting_approval_and_journal_survive_restart(settings, client_for):
    with client_for(settings) as client:
        workflow = wait(client, create(client)["id"])
        events = client.get(f"/v1/workflows/{workflow['id']}/events").json()
        assert events[0]["kind"] == "workflow.created"
        assert any(e["kind"] == "workflow.awaiting_approval" for e in events)
        cursor = events[-1]["sequence"]
    with client_for(settings) as restarted:
        restored = restarted.get(f"/v1/workflows/{workflow['id']}").json()
        assert restored["status"] == "awaiting_approval"
        assert restored["attempts"] == workflow["attempts"]
        assert restarted.get(f"/v1/workflows/{workflow['id']}/events?after={cursor}").json() == []
        assert review(restarted, restored).status_code == 200
        assert restarted.get(f"/v1/workflows/{workflow['id']}/events?after={cursor}").json()[0]["kind"] == "review.recorded"


def test_failed_attempt_is_preserved_and_retry_does_not_repeat_completed_step(settings, client_for):
    with client_for(settings) as client:
        service = client.app.state.manager.workflows

        async def fail_research(context):
            if context["stage"] == "research":
                raise RuntimeError("falha simulada")
            return await simulate(context)

        service.executor = fail_research
        failed = wait(client, create(client)["id"], ("failed",))
        assert [a["status"] for a in failed["attempts"]] == ["succeeded", "failed"]
        assert "falha simulada" in failed["attempts"][1]["error"]
        failed_job = next(j for j in failed["steps"] if j["status"] == "failed")
        assert client.post(f"/v1/jobs/{failed_job['id']}/retry").status_code == 409
        service.executor = simulate
        assert client.post(f"/v1/workflows/{failed['id']}/retry", json={"expected_revision": 1}).status_code == 200
        completed = wait(client, failed["id"])
        assert len(completed["attempts"]) == 5
        assert len(completed["artifacts"]) == 4
        assert completed["steps"][0]["attempt"] == 1
        assert completed["steps"][1]["attempt"] == 2
        assert completed["attempts"][1]["status"] == "failed"


def test_cancellation_stops_simulated_step_and_prevents_dependents(settings, client_for):
    with client_for(settings) as client:
        async def slow(context):
            await asyncio.sleep(30)
            return await simulate(context)

        client.app.state.manager.workflows.executor = slow
        workflow = wait(client, create(client)["id"], ("running",))
        queued = create(client, key="second")
        assert client.post(f"/v1/workflows/{queued['id']}/cancel").json()["status"] == "cancelled"
        # Compatibilidade: cancelar um job editorial cancela seu workflow inteiro.
        active = next(j for j in workflow["steps"] if j["status"] == "running")
        assert client.post(f"/v1/jobs/{active['id']}/cancel").status_code == 200
        cancelled = wait(client, workflow["id"], ("cancelled",))
        assert all(step["status"] == "cancelled" for step in cancelled["steps"])
        assert len(cancelled["attempts"]) == 1
        assert cancelled["attempts"][0]["status"] == "cancelled"
        assert cancelled["artifacts"] == []
        assert client.get(f"/v1/workflows/{queued['id']}").json()["attempts"] == []
        assert client.post(f"/v1/workflows/{workflow['id']}/cancel").json()["status"] == "cancelled"


def seed_without_worker(pg_store):
    async def notify():
        pass

    service = WorkflowService(pg_store, asyncio.Queue(), notify)
    workflow = asyncio.run(service.create(WorkflowRequest(**REQUEST), "seed"))
    return pg_store, service, workflow


@pytest.mark.parametrize("completed_before_crash", [False, True])
def test_abrupt_process_exit_recovers_without_duplicate_artifact(
    settings, client_for, pg_store, pg_admin, pg_app_cluster, pg_app_database, completed_before_crash
):
    _, _, workflow = seed_without_worker(pg_store)
    job_id = workflow["steps"][0]["id"]
    dsn = pg_app_cluster.dsn(pg_app_database, "horus_app")
    script = """
import asyncio, os, sys
from psycopg_pool import ConnectionPool
from horus_backend.storage import Store
from horus_backend.worker_lock import WorkerLock
from horus_backend.workflows import WorkflowService, simulate
with ConnectionPool(sys.argv[1], min_size=1, max_size=2, open=True) as pool:
    store = Store(pool)
    lock = WorkerLock(store)
    lock.acquire()
    service = WorkflowService(store, asyncio.Queue(), None)
    attempt, context = service._claim(sys.argv[2])
    if sys.argv[3] == 'True':
        service._finish(sys.argv[2], attempt, 'succeeded', content=asyncio.run(simulate(context)))
    os._exit(0)
"""
    process = subprocess.run([sys.executable, "-c", script, dsn, job_id, str(completed_before_crash)], capture_output=True, timeout=15)
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    # Simula expiração do lease do processo encerrado abruptamente antes do reinício.
    pg_admin.execute("UPDATE horus.worker_instances SET lease_until = clock_timestamp() - interval '1 second'")
    with client_for(settings) as restarted:
        if completed_before_crash:
            recovered = wait(restarted, workflow["id"])
            assert len(recovered["attempts"]) == 4
            assert recovered["steps"][0]["attempt"] == 1
        else:
            interrupted = restarted.get(f"/v1/workflows/{workflow['id']}").json()
            assert interrupted["status"] == "interrupted"
            assert interrupted["attempts"][0]["status"] == "interrupted"
            assert interrupted["artifacts"] == []
            assert restarted.post(f"/v1/workflows/{workflow['id']}/retry", json={"expected_revision": 1}).status_code == 200
            recovered = wait(restarted, workflow["id"])
            assert len(recovered["attempts"]) == 5
        assert len(recovered["artifacts"]) == 4
        assert len({a["job_id"] for a in recovered["artifacts"]}) == 4


def test_dependency_and_artifact_integrity_are_checked_before_execution(pg_store, pg_admin):
    store, service, workflow = seed_without_worker(pg_store)
    research_id = workflow["steps"][1]["id"]
    pg_admin.execute("UPDATE horus.jobs SET status = 'queued' WHERE id = %s", (research_id,))
    asyncio.run(service.run_step(store.get_job(research_id)))
    blocked = service.get(workflow["id"])
    assert blocked["status"] == "failed"
    assert blocked["steps"][1]["status"] == "blocked"
    assert blocked["attempts"] == []


def test_tampered_dependency_artifact_is_not_consumed(pg_store, pg_admin):
    store, service, workflow = seed_without_worker(pg_store)
    first = workflow["steps"][0]["id"]
    attempt, context = service._claim(first)
    research = service._finish(first, attempt, "succeeded", content=asyncio.run(simulate(context)))
    pg_admin.execute("UPDATE horus.artifacts SET content = '{}'::jsonb, content_canonical = '{}'::bytea WHERE job_id = %s", (first,))
    asyncio.run(service.run_step(store.get_job(research)))
    blocked = service.get(workflow["id"])
    assert blocked["status"] == "failed"
    assert "integridade" in blocked["steps"][1]["error"]
    assert len(blocked["attempts"]) == 1


def test_step_result_and_next_release_commit_atomically(settings, client_for, pg_store, pg_admin):
    _, _, workflow = seed_without_worker(pg_store)
    pg_admin.execute("""
        CREATE OR REPLACE FUNCTION horus.fail_release_fn() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.step_key = 'research' AND NEW.status = 'queued' THEN
                RAISE EXCEPTION 'falha de teste na transação';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER fail_release BEFORE UPDATE OF status ON horus.jobs
            FOR EACH ROW EXECUTE FUNCTION horus.fail_release_fn();
    """)
    with client_for(settings) as client:
        failed = wait(client, workflow["id"], ("failed",))
        assert failed["artifacts"] == []
        assert failed["steps"][0]["status"] == "failed"
        assert failed["steps"][1]["status"] == "waiting"
        assert failed["attempts"][0]["status"] == "failed"
        pg_admin.execute("DROP TRIGGER fail_release ON horus.jobs; DROP FUNCTION horus.fail_release_fn();")
        assert client.post(f"/v1/workflows/{workflow['id']}/retry", json={"expected_revision": 1}).status_code == 200
        assert len(wait(client, workflow["id"])["artifacts"]) == 4


def test_late_result_from_old_attempt_cannot_change_recovered_workflow(settings, client_for, pg_store):
    _, service, workflow = seed_without_worker(pg_store)
    job_id = workflow["steps"][0]["id"]
    old_attempt, context = service._claim(job_id)
    service.recover()
    with client_for(settings) as client:
        client.post(f"/v1/workflows/{workflow['id']}/retry", json={"expected_revision": 1})
        completed = wait(client, workflow["id"])
        assert service._finish(job_id, old_attempt, "succeeded", content=asyncio.run(simulate(context))) is None
        after = client.get(f"/v1/workflows/{workflow['id']}").json()
        assert after["artifacts"] == completed["artifacts"]
        assert after["current_artifact_id"] == completed["current_artifact_id"]


def test_technical_log_retention_does_not_erase_editorial_history(settings, client_for):
    with client_for(settings) as client:
        workflow = wait(client, create(client)["id"])
        review(client, workflow)
        store = client.app.state.store
        job_id = workflow["steps"][0]["id"]
        with store.uow() as conn:
            conn.execute(
                "INSERT INTO horus.events(job_id, kind, message, created_at) "
                "SELECT %s, 'stdout', 'log', clock_timestamp() FROM generate_series(1, 2001)",
                (job_id,),
            )
        store.event(job_id, "stdout", "última linha")
        assert len(client.get(f"/v1/workflows/{workflow['id']}").json()["approvals"]) == 1
        assert client.get(f"/v1/workflows/{workflow['id']}/events").json()[0]["kind"] == "workflow.created"


def test_cancelling_completed_step_does_not_cancel_workflow_waiting_for_review(settings, client_for):
    with client_for(settings) as client:
        workflow = wait(client, create(client)["id"])
        assert client.post(f"/v1/jobs/{workflow['steps'][0]['id']}/cancel").json()["status"] == "succeeded"
        assert client.get(f"/v1/workflows/{workflow['id']}").json()["status"] == "awaiting_approval"


def test_invalid_executor_output_does_not_advance_or_approve(settings, client_for):
    with client_for(settings) as client:
        async def invalid(context):
            return {**await simulate(context), "official_status": "approved"}

        client.app.state.manager.workflows.executor = invalid
        failed = wait(client, create(client)["id"], ("failed",))
        assert failed["artifacts"] == failed["approvals"] == []
        assert failed["steps"][1]["status"] == "waiting"
        assert len(failed["attempts"]) == 1


def test_competing_reviews_have_one_winner(settings, client_for):
    with client_for(settings) as client:
        workflow = wait(client, create(client)["id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            approve = pool.submit(review, client, workflow, "approve", "approve")
            reject = pool.submit(review, client, workflow, "reject", "reject")
            assert sorted([approve.result().status_code, reject.result().status_code]) == [200, 409]
        assert len(client.get(f"/v1/workflows/{workflow['id']}").json()["approvals"]) == 1


@pytest.mark.xfail(strict=True, reason="Defeito PG: database_router fecha db_manager antes de manager.close() no shutdown da API (api.py/database/router.py)")
def test_second_backend_cannot_recover_another_workers_running_step(settings, client_for):
    with client_for(settings) as first:
        async def slow(context):
            await asyncio.sleep(30)
            return await simulate(context)

        first.app.state.manager.workflows.executor = slow
        workflow = wait(first, create(first)["id"], ("running",))
        with pytest.raises(RuntimeError, match="Outro backend"):
            with client_for(settings):
                pass
        assert first.get(f"/v1/workflows/{workflow['id']}").json()["status"] == "running"
    with client_for(settings) as next_instance:
        assert next_instance.get(f"/v1/workflows/{workflow['id']}").json()["status"] == "interrupted"


def test_contract_rejects_unapproved_operations_and_requires_authentication(settings, client_for):
    with client_for(settings) as client:
        assert client.post("/v1/workflows", json=REQUEST).status_code == 422
        assert client.post("/v1/workflows", json={**REQUEST, "executor": "hermes"}, headers={"Idempotency-Key": "bad"}).status_code == 422
        assert client.post("/v1/jobs", json={"operation": "editorial.simulated", "stage": "planning"}).status_code == 422
        assert client.post("/v1/workflows", json=REQUEST, headers={"Authorization": "wrong", "Idempotency-Key": "bad"}).status_code == 401
        assert client.get("/v1/workflows/missing").status_code == 404
        workflow = wait(client, create(client)["id"])
        assert review(client, workflow, operator_id="agent").status_code == 422
        assert review(client, workflow, decision="request_changes").status_code == 422
        assert client.get(f"/v1/workflows/{workflow['id']}/events?after=-1").status_code == 422


def test_pg_schema_migrations_preserve_rows_and_rollback_on_failure(pg_store, pg_admin):
    import psycopg
    from schema_support import apply_chain

    _, _, workflow = seed_without_worker(pg_store)
    before_versions = pg_admin.execute("SELECT version FROM horus.schema_migrations ORDER BY version").fetchall()
    apply_chain(pg_admin)
    assert pg_admin.execute("SELECT version FROM horus.schema_migrations ORDER BY version").fetchall() == before_versions
    assert pg_admin.execute("SELECT id FROM horus.workflows WHERE id = %s", (workflow["id"],)).fetchone()[0] == workflow["id"]
    with pytest.raises(psycopg.errors.SyntaxError):
        with pg_admin.transaction():
            pg_admin.execute(
                "INSERT INTO horus.schema_migrations(version, checksum, applied_at) VALUES ('99999999999999', %s, clock_timestamp())",
                ("0" * 64,),
            )
            pg_admin.execute("INVALID SQL")
    assert pg_admin.execute("SELECT version FROM horus.schema_migrations WHERE version = '99999999999999'").fetchone() is None


def test_pg_schema_checksum_mismatch_is_refused(pg_admin):
    from schema_support import apply_chain

    version, orig_checksum = pg_admin.execute("SELECT version, checksum FROM horus.schema_migrations ORDER BY version LIMIT 1").fetchone()
    pg_admin.execute("UPDATE horus.schema_migrations SET checksum = %s WHERE version = %s", ("f" * 64, version))
    with pytest.raises(ValueError, match="divergente"):
        apply_chain(pg_admin)
    pg_admin.execute("UPDATE horus.schema_migrations SET checksum = %s WHERE version = %s", (orig_checksum, version))
