"""Testes PostgreSQL para ProductionService (HOR-164 / I5)."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import threading
import uuid

from psycopg.types.json import Jsonb
import pytest

from horus_backend.config import Settings
from horus_backend.jobs import JobManager
from horus_backend.production import (
    NarrationSelection,
    ProductionRequest,
    ProductionReviewNote,
    ProductionReviewRequest,
    ProductionService,
    inspect_artifact,
)
from horus_backend.scripts import ScriptRequest, ScriptReviewRequest, ScriptService
import horus_backend.security as security
from horus_backend.storage import DatabaseUnavailable, LeaseLostError, Store
from horus_backend.workflows import WorkflowConflict, encode
from schema_support import schema_database
from test_scripts_pg import _approved_workflow


@pytest.fixture(scope="module")
def _prod_schema_db(pg_cluster):
    with schema_database(pg_cluster) as state:
        yield state


@pytest.fixture
def pg_pool(request, pg_cluster):
    if request.node.get_closest_marker("pg_strong"):
        with schema_database(pg_cluster) as (_, pool):
            yield pool
    else:
        admin, pool = request.getfixturevalue("_prod_schema_db")
        try:
            yield pool
        finally:
            admin.execute(
                """TRUNCATE horus.workflows, horus.jobs, horus.job_attempts, horus.artifacts,
                            horus.approvals, horus.workflow_events, horus.cognitive_runs,
                            horus.authorizations, horus.human_checkpoints, horus.transmissions,
                            horus.events, horus.scripts, horus.script_receipts,
                            horus.productions, horus.production_receipts, horus.license_reviews,
                            horus.preferences, horus.worker_instances
                   RESTART IDENTITY CASCADE"""
            )


@pytest.fixture
def settings(tmp_path):
    profile = tmp_path / "profile"
    security.harden_profile(profile)
    connection = tmp_path / "connection.json"
    connection.write_text(json.dumps({"base_url": "http://127.0.0.1:8643", "token": "connection-ficticia-01234567890123"}))
    return Settings(
        token="prod-pg-test-token-0123456789012345",
        data_dir=tmp_path / "data",
        factory_root=None,
        hermes_executable=None,
        hermes_profile=profile,
        hermes_config=connection,
        workflow_executor="simulated",
        max_tokens_per_run=10000,
        max_tokens_per_day=50000,
        max_runs_per_day=5,
        budget_total_usd=Decimal("10"),
        budget_daily_usd=Decimal("5"),
        price_input_usd_per_mtok=Decimal("10"),
        price_output_usd_per_mtok=Decimal("20"),
    )


def _approved_script(store: Store, settings: Settings, *, key: str = "wf-prod") -> dict:
    wf = _approved_workflow(store, settings, key=key)
    scripts = ScriptService(store, settings)
    s1, _ = scripts.create(wf["id"], ScriptRequest(expected_revision=1, proposal_artifact_id=wf["current_artifact_id"]), f"{key}-gen")
    return scripts.review(s1["id"], ScriptReviewRequest(expected_version=1, decision="approve"), f"{key}-rev")


def test_production_simulated_run_artifacts_idempotency_and_list(pg_pool, settings):
    store = Store(pg_pool, data_dir=settings.data_dir)
    script = _approved_script(store, settings)
    manager = JobManager(store, settings, device_id="dev-prod")

    async def scenario():
        await manager.start()
        try:
            req = ProductionRequest(expected_version=1, mode="simulated")
            p1, created1 = await manager.productions.create(script["id"], req, "prod-1")
            assert created1 is True
            assert p1["status"] in {"queued", "running", "succeeded"}

            # Replay idempotente
            p1_replay, created_replay = await manager.productions.create(script["id"], req, "prod-1")
            assert created_replay is False
            assert p1_replay["id"] == p1["id"]

            # Conflito de chave com outro modo
            with pytest.raises(WorkflowConflict):
                await manager.productions.create(script["id"], ProductionRequest(expected_version=1, mode="local_demo"), "prod-1")

            # Aguardar conclusão do subprocesso production_worker.py (simulado)
            for _ in range(100):
                current = manager.productions.get(p1["id"])
                if current["status"] in {"succeeded", "failed", "blocked"}:
                    break
                await asyncio.sleep(0.05)
            assert current["status"] == "succeeded", current.get("error")
            assert any(a["kind"] == "plan" for a in current["artifacts"])

            # Segunda produção para validar list() em lote
            p2, created2 = await manager.productions.create(script["id"], req, "prod-2")
            assert created2 is True
            for _ in range(100):
                if manager.productions.get(p2["id"])["status"] in {"succeeded", "failed", "blocked"}:
                    break
                await asyncio.sleep(0.05)
            listed = manager.productions.list(script["id"])["items"]
            assert len(listed) == 2
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_production_stale_approval_and_canonical_integrity(pg_pool, settings):
    store = Store(pg_pool, data_dir=settings.data_dir)
    script = _approved_script(store, settings, key="wf-stale")
    manager = JobManager(store, settings, device_id="dev-stale")

    async def scenario():
        p1, _ = await manager.productions.create(script["id"], ProductionRequest(expected_version=1, mode="simulated"), "p-stale")
        # Verificar que plan_canonical e script_review_canonical estão gravados
        with pg_pool.connection() as conn:
            row = conn.execute(
                "SELECT script_review_canonical, plan_canonical FROM horus.productions WHERE id = %s",
                (p1["id"],),
            ).fetchone()
            assert bytes(row[0]) == encode(script["review"]).encode("utf-8")
            assert json.loads(bytes(row[1]).decode("utf-8"))["script_id"] == script["id"]

            # Simular nova versão de roteiro (torna v1 obsoleto -> stale approval)
            with conn.transaction():
                doc2 = encode({**script["document"], "title": "Versão 2"})
                conn.execute(
                    """INSERT INTO horus.scripts(
                        id, workflow_id, workflow_revision, proposal_artifact_id, proposal_content_hash,
                        version, status, document, created_at, review, origin, generator, document_canonical, review_canonical
                    ) VALUES(%s, %s, %s, %s, %s, 2, 'awaiting_review', %s::jsonb, clock_timestamp(), NULL, 'human', NULL, %s, NULL)""",
                    (
                        uuid.uuid4().hex,
                        script["workflow_id"],
                        script["workflow_revision"],
                        script["proposal_artifact_id"],
                        script["proposal_content_hash"],
                        doc2,
                        doc2.encode("utf-8"),
                    ),
                )
        job = store.get_job(p1["job_id"])
        await manager.productions.run(job)
        blocked = manager.productions.get(p1["id"])
        assert blocked["status"] == "blocked"
        assert "aprovação" in (blocked["error"] or "").lower()

    asyncio.run(scenario())


def test_production_plan_canonical_divergence_blocks_execution(pg_pool, settings):
    store = Store(pg_pool, data_dir=settings.data_dir)
    script = _approved_script(store, settings, key="wf-can")
    manager = JobManager(store, settings, device_id="dev-can")

    async def scenario():
        p1, _ = await manager.productions.create(script["id"], ProductionRequest(expected_version=1, mode="simulated"), "p-can")
        # Adulterar plan_canonical mantendo jsonb consistente
        with pg_pool.connection() as conn:
            with conn.transaction():
                plan = conn.execute("SELECT plan FROM horus.productions WHERE id = %s", (p1["id"],)).fetchone()[0]
                plan["scenes"][0]["heading"] = "Cena adulterada"
                tampered = encode(plan)
                conn.execute(
                    "UPDATE horus.productions SET plan = %s::jsonb, plan_canonical = %s WHERE id = %s",
                    (tampered, tampered.encode("utf-8"), p1["id"]),
                )
        job = store.get_job(p1["job_id"])
        await manager.productions.run(job)
        blocked = manager.productions.get(p1["id"])
        assert blocked["status"] == "blocked"
        assert "plano" in (blocked["error"] or "").lower()

    asyncio.run(scenario())


def test_production_narration_preference_from_pg(pg_pool, settings):
    store = Store(pg_pool, data_dir=settings.data_dir)
    script = _approved_script(store, settings, key="wf-pref")
    manager = JobManager(store, settings, device_id="dev-pref")

    with pg_pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                """INSERT INTO horus.preferences(device_id, key, value, updated_at)
                   VALUES('default', 'narration.preference', %s, clock_timestamp())""",
                (Jsonb({"provider": "piper", "voice_id": "pt_BR-faber-medium"}),),
            )

    async def scenario():
        p1, _ = await manager.productions.create(script["id"], ProductionRequest(expected_version=1, mode="local_pilot"), "p-pref")
        assert p1["narration"] == {"provider": "piper", "voice_id": "pt_BR-faber-medium"}

        # Preferência inválida em horus.preferences -> recusa
        with pg_pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "UPDATE horus.preferences SET value = %s WHERE device_id = 'default' AND key = 'narration.preference'",
                    (Jsonb({"provider": ""}),),
                )
        with pytest.raises(WorkflowConflict, match="Preferência"):
            await manager.productions.create(script["id"], ProductionRequest(expected_version=1, mode="local_pilot"), "p-pref-bad")

    asyncio.run(scenario())


def test_production_review_hash_notes_and_artifact_confinement(pg_pool, settings):
    store = Store(pg_pool, data_dir=settings.data_dir)
    script = _approved_script(store, settings, key="wf-rev")
    manager = JobManager(store, settings, device_id="dev-rev")

    async def scenario():
        p1, _ = await manager.productions.create(script["id"], ProductionRequest(expected_version=1, mode="local_demo"), "p-rev")
        root = manager.productions.root(p1["id"])
        root.mkdir(parents=True, exist_ok=True)
        video_path = root / "video.mp4"
        video_bytes = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64
        video_path.write_bytes(video_bytes)
        art = inspect_artifact(root, "video.mp4", "video")
        video_sha = art["sha256"]

        with pg_pool.connection() as conn:
            with conn.transaction():
                conn.execute("UPDATE horus.productions SET artifacts = %s::jsonb WHERE id = %s", (encode([art]), p1["id"]))
                conn.execute("UPDATE horus.jobs SET status = 'succeeded' WHERE id = %s", (p1["job_id"],))

        # Confinamento e leitura de artefato válido
        assert manager.productions.artifact(p1["id"], art["id"]) == video_path

        # Revisão com hash errado -> recusa
        with pytest.raises(WorkflowConflict):
            manager.productions.review(
                p1["id"],
                ProductionReviewRequest(decision="approve", artifact_sha256="0" * 64),
                "rev-bad-sha",
            )

        # Revisão válida com notas
        reviewed = manager.productions.review(
            p1["id"],
            ProductionReviewRequest(
                decision="approve",
                artifact_sha256=video_sha,
                notes=[ProductionReviewNote(at_seconds=1.5, text="Corte limpo")],
            ),
            "rev-ok",
        )
        assert reviewed["review"]["decision"] == "approve"
        assert reviewed["review"]["notes"][0]["text"] == "Corte limpo"

        # Replay idempotente
        assert manager.productions.review(
            p1["id"],
            ProductionReviewRequest(
                decision="approve",
                artifact_sha256=video_sha,
                notes=[ProductionReviewNote(at_seconds=1.5, text="Corte limpo")],
            ),
            "rev-ok",
        ) == reviewed

        # Conflito de chave
        with pytest.raises(WorkflowConflict):
            manager.productions.review(
                p1["id"],
                ProductionReviewRequest(decision="reject", artifact_sha256=video_sha),
                "rev-ok",
            )

    asyncio.run(scenario())


@pytest.mark.pg_strong
def test_production_live_lease_blocks_other_instance_recovery(pg_pool, settings):
    store_a = Store(pg_pool, data_dir=settings.data_dir)
    store_b = Store(pg_pool, data_dir=settings.data_dir)
    script = _approved_script(store_a, settings, key="wf-lease")
    mgr_a = JobManager(store_a, settings, device_id="dev-a")
    mgr_b = JobManager(store_b, settings, device_id="dev-b")

    async def scenario():
        p1, _ = await mgr_a.productions.create(script["id"], ProductionRequest(expected_version=1, mode="simulated"), "p-lease")
        mgr_a.worker_lock.acquire()
        mgr_b.worker_lock.acquire()
        try:
            claimed = store_a.claim_job(p1["job_id"], mgr_a.worker_lock.instance_id, mgr_a.worker_lock.fencing_token, ttl_seconds=30)
            assert claimed is not None

            # Outra instância tentando recover() não recupera job com lease vivo
            assert store_b.recover() == []
            assert store_b.claim_job(p1["job_id"], mgr_b.worker_lock.instance_id, mgr_b.worker_lock.fencing_token, ttl_seconds=30) is None

            # Escrita com fencing_token antigo/errado falha com LeaseLostError
            with pytest.raises(LeaseLostError):
                store_a.update(p1["job_id"], "failed", worker_instance_id=mgr_a.worker_lock.instance_id, fencing_token=999999)
        finally:
            mgr_b.worker_lock.close()
            mgr_a.worker_lock.close()

    asyncio.run(scenario())


def test_production_unavailable_raises_503(settings):
    manager = JobManager(Store(None, data_dir=settings.data_dir), settings, device_id="dev-503")
    with pytest.raises(DatabaseUnavailable) as caught:
        manager.productions.get("a" * 32)
    assert caught.value.http_status == 503
