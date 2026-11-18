"""Testes PostgreSQL para WorkerLock, claim/lease/fencing, recovery concorrente e JobManager (HOR-164 / I3)."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from horus_backend.config import Settings
from horus_backend.jobs import JobManager
from horus_backend.storage import LeaseLostError, Store
from horus_backend.worker_lock import WorkerLock
from test_storage_jobs import _make_pg_manager, _storage_schema_db, pg_store  # noqa: F401


def test_worker_lock_contender_fails_immediately_and_can_retry(pg_store):
    store, _, _ = pg_store
    owner = WorkerLock(store, device_id="pc-01", ttl_seconds=30)
    contender = WorkerLock(store, device_id="pc-01", ttl_seconds=30)

    token1 = owner.acquire()
    assert token1 >= 1
    try:
        for _ in range(3):
            with pytest.raises(RuntimeError, match="Outro backend"):
                contender.acquire()
            assert contender.active is False
            assert contender.fencing_token is None
    finally:
        owner.close()

    token2 = contender.acquire()
    assert token2 > token1
    contender.close()


def test_worker_lock_recovers_after_expired_lease(pg_store):
    store, admin, _ = pg_store
    crashed = WorkerLock(store, device_id="pc-crash", ttl_seconds=10)
    token1 = crashed.acquire()
    # Simula queda sem close(): expira o lease no banco.
    admin.execute(
        "UPDATE horus.worker_instances SET lease_until = clock_timestamp() - interval '1 second' WHERE id = %s",
        (crashed.instance_id,),
    )
    successor = WorkerLock(store, device_id="pc-crash", ttl_seconds=10)
    token2 = successor.acquire()
    assert token2 > token1
    assert crashed.renew() is False
    successor.close()


def test_lost_lease_cannot_write_events_update_or_renew(pg_store):
    store, admin, _ = pg_store
    lock_a = WorkerLock(store, device_id="pc-a", ttl_seconds=30)
    token_a = lock_a.acquire()
    job, _ = store.create_job({"operation": "factory.profile", "channel_id": "canal01"}, "lease-loss-1")
    claimed = store.claim_job(job["id"], lock_a.instance_id, token_a, ttl_seconds=30)
    assert claimed is not None
    assert claimed["status"] == "running"
    assert claimed["attempt"] == 1
    assert claimed["fencing_token"] == token_a

    # Outro executor não consegue reclamar um job já reclamado.
    lock_b = WorkerLock(store, device_id="pc-b", ttl_seconds=30)
    token_b = lock_b.acquire()
    assert store.claim_job(job["id"], lock_b.instance_id, token_b, ttl_seconds=30) is None

    # Expira o lease do job A e recupera pelo executor B.
    admin.execute(
        "UPDATE horus.jobs SET lease_until = clock_timestamp() - interval '1 second' WHERE id = %s",
        (job["id"],),
    )
    recovered = store.recover()
    assert recovered == [job["id"]]

    # Instância A que perdeu o lease NÃO consegue gravar atualização, evento nem renovar.
    with pytest.raises(LeaseLostError):
        store.update(job["id"], "succeeded", worker_instance_id=lock_a.instance_id, fencing_token=token_a)
    with pytest.raises(LeaseLostError):
        store.event(job["id"], "stdout", "tardio", worker_instance_id=lock_a.instance_id, fencing_token=token_a)
    with pytest.raises(LeaseLostError):
        store.renew_job_lease(job["id"], lock_a.instance_id, token_a, ttl_seconds=30)

    final = store.get_job(job["id"])
    assert final["status"] == "interrupted"
    assert final["worker_instance_id"] is None
    assert final["fencing_token"] is None
    lock_a.close()
    lock_b.close()


@pytest.mark.pg_strong
def test_two_sessions_do_not_recover_live_job_and_replay_is_idempotent(pg_cluster, _storage_schema_db, tmp_path, monkeypatch):
    database, admin = _storage_schema_db
    mgr_a = _make_pg_manager(pg_cluster, database, tmp_path / "a", monkeypatch)
    mgr_b = _make_pg_manager(pg_cluster, database, tmp_path / "b", monkeypatch)
    store_a = Store(mgr_a)
    store_b = Store(mgr_b)
    lock_a = WorkerLock(store_a, device_id="pc-a", ttl_seconds=30)
    lock_b = WorkerLock(store_b, device_id="pc-b", ttl_seconds=30)
    try:
        token_a = lock_a.acquire()
        lock_b.acquire()
        live_job, _ = store_a.create_job({"operation": "factory.profile", "channel_id": "canal01"}, "live-job")
        assert store_a.claim_job(live_job["id"], lock_a.instance_id, token_a, ttl_seconds=30) is not None

        # Duas threads concorrentes chamando recover() NÃO recuperam o job vivo.
        barrier = threading.Barrier(2)

        def run_recover(store):
            barrier.wait(timeout=5)
            return store.recover()

        with ThreadPoolExecutor(max_workers=2) as executor:
            rec_a, rec_b = [f.result(timeout=10) for f in (executor.submit(run_recover, store_a), executor.submit(run_recover, store_b))]
        assert rec_a == []
        assert rec_b == []
        assert store_b.get_job(live_job["id"])["status"] == "running"

        # Após expirar o lease, apenas UMA sessão recupera e replay de recover() não duplica evento.
        admin.execute(
            "UPDATE horus.jobs SET lease_until = clock_timestamp() - interval '1 second' WHERE id = %s",
            (live_job["id"],),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            rec_a2, rec_b2 = [f.result(timeout=10) for f in (executor.submit(run_recover, store_a), executor.submit(run_recover, store_b))]
        assert sorted([len(rec_a2), len(rec_b2)]) == [0, 1]
        assert store_a.recover() == []

        evts = store_a.events(live_job["id"], 0)
        assert [e["message"] for e in evts] == ["interrupted"]
    finally:
        lock_a.close()
        lock_b.close()
        mgr_a.close()
        mgr_b.close()
        admin.execute(
            "TRUNCATE horus.events, horus.jobs, horus.worker_instances, horus.media RESTART IDENTITY CASCADE"
        )


def test_recover_production_run_sets_diagnostic_once(pg_store):
    store, _, _ = pg_store
    job, _ = store.create_job({"operation": "production.run", "production_id": "p1"}, "prod-1")
    store.update(job["id"], "running", new_attempt=True)
    assert store.recover() == [job["id"]]
    restored = store.get_job(job["id"])
    assert restored["status"] == "interrupted"
    assert restored["result"]["failure"]["code"] == "production_interrupted"
    assert store.recover() == []


def test_job_manager_lifecycle_with_pg_store(pg_store, tmp_path, monkeypatch):
    store, _, _ = pg_store
    monkeypatch.setenv("PATH", "")
    settings = Settings(token="tok-pg", data_dir=tmp_path, factory_root=None)

    async def run():
        jm = JobManager(store, settings, device_id="pc-jm", lease_ttl_seconds=10, renew_interval_seconds=2)
        # Workflows e retenção são escopos I4/I6b (ainda SQLite); isolados neste teste de I3.
        jm.workflows.recover = lambda: None
        async def noop_retention():
            return None
        jm.run_retention = noop_retention

        interrupted, _ = store.create_job({"operation": "factory.profile", "channel_id": "canal01"}, "jm-interrupted")
        store.update(interrupted["id"], "running", new_attempt=True)
        queued, _ = store.create_job({"operation": "factory.profile", "channel_id": "canal01"}, "jm-queued")

        await jm.start()
        try:
            assert jm.worker_lock.active is True
            assert store.get_job(interrupted["id"])["status"] == "interrupted"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current = store.get_job(queued["id"])
                if current["status"] in {"blocked", "failed", "succeeded"}:
                    break
                await asyncio.sleep(0.05)
            assert store.get_job(queued["id"])["status"] == "blocked"
            assert store.get_job(queued["id"])["worker_instance_id"] is None
        finally:
            await jm.close()
        assert jm.worker_lock.active is False

    asyncio.run(run())
