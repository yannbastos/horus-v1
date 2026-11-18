"""Testes PostgreSQL para Store, jobs, eventos, idempotência, mídia e 503 sem fallback (HOR-164 / I3)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import uuid

import psycopg
import pytest

from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.database.manager import DatabaseManager
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from horus_backend.storage import DatabaseUnavailable, MAX_EVENTS_PER_JOB, Store
from schema_support import apply_chain
from test_database_contract import CONFIG, MemoryKeyring


def _make_pg_manager(pg_cluster, database: str, tmp_path: Path, monkeypatch, *, configure: bool = True) -> DatabaseManager:
    vault = ConnectionVault(tmp_path, MemoryKeyring())
    if configure:
        vault.write(ConnectionRequest.model_validate(CONFIG))
    manager = DatabaseManager(vault)

    def local_type(config):
        class LocalConnection(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo="", **kwargs):
                return super().connect(
                    pg_cluster.dsn(database, "horus_app"),
                    autocommit=True,
                    password=config.password.get_secret_value(),
                    prepare_threshold=None,
                )

        return LocalConnection

    monkeypatch.setattr(manager, "_make_connection_type", local_type)
    monkeypatch.setattr("horus_backend.database.manager.resolve_public", lambda *_: "8.8.8.8")
    if configure:
        assert manager.test().state == "connected"
    return manager


@pytest.fixture(scope="module")
def _storage_schema_db(pg_cluster):
    with pg_cluster.connect(autocommit=True) as bootstrap:
        for name in ("anon", "authenticated"):
            bootstrap.execute(
                f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{name}') "
                f"THEN CREATE ROLE {name} NOLOGIN; END IF; END $$"
            )
    with pg_cluster.database() as pool:
        with pool.connection() as app:
            database = app.info.dbname
        with pg_cluster.connect(database, autocommit=True) as admin:
            admin.execute("DROP SCHEMA horus CASCADE")
            admin.execute("CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY)")
            apply_chain(admin)
            admin.execute(
                "INSERT INTO horus.schema_migrations(version, checksum, applied_at) "
                "VALUES (%s, %s, clock_timestamp()) ON CONFLICT (version) DO NOTHING",
                (EXPECTED_SCHEMA_VERSION, "a" * 64),
            )
            admin.execute(
                "INSERT INTO horus.database_identity(singleton, instance_id, created_at) "
                "VALUES (true, gen_random_uuid(), clock_timestamp()) ON CONFLICT (singleton) DO NOTHING"
            )
            yield database, admin


@pytest.fixture
def pg_store(pg_cluster, _storage_schema_db, tmp_path, monkeypatch):
    database, admin = _storage_schema_db
    manager = _make_pg_manager(pg_cluster, database, tmp_path, monkeypatch)
    store = Store(manager, data_dir=tmp_path)
    try:
        yield store, admin, manager
    finally:
        manager.close()
        admin.execute(
            "TRUNCATE horus.events, horus.jobs, horus.worker_instances, horus.media RESTART IDENTITY CASCADE"
        )


def test_unavailable_database_raises_503_without_sqlite_fallback(pg_cluster, _storage_schema_db, tmp_path, monkeypatch):
    database, _ = _storage_schema_db
    unconfigured = _make_pg_manager(pg_cluster, database, tmp_path / "u", monkeypatch, configure=False)
    try:
        for store in (Store(None, data_dir=tmp_path), Store(unconfigured, data_dir=tmp_path)):
            with pytest.raises(DatabaseUnavailable) as exc:
                _ = store.database_id
            assert exc.value.http_status == 503
            assert exc.value.code == "database_unavailable"

            for call in (
                lambda: store.create_job({"operation": "factory.profile", "channel_id": "canal01"}, "k1"),
                lambda: store.get_job("missing"),
                lambda: store.list_jobs(),
                lambda: store.queued_ids(),
                lambda: store.claim_job("missing", uuid.uuid4(), 1),
                lambda: store.update("missing", "failed"),
                lambda: store.event("missing", "state", "failed"),
                lambda: store.events("missing", 0),
                lambda: store.recover(),
                lambda: store.media("missing"),
                lambda: store.list_media(),
            ):
                with pytest.raises(DatabaseUnavailable) as err:
                    call()
                assert err.value.http_status == 503
                assert err.value.code == "database_unavailable"

            media_file = tmp_path / "sample.mp4"
            media_file.write_bytes(b"mp4")
            with pytest.raises(DatabaseUnavailable) as err:
                store.register_media(media_file)
            assert err.value.http_status == 503
            assert err.value.code == "database_unavailable"
    finally:
        unconfigured.close()
    assert not list(tmp_path.rglob("*.sqlite*"))
    assert not list(tmp_path.rglob("*.db"))


def test_database_id_reads_singleton_identity(pg_store):
    store, admin, _ = pg_store
    expected = str(admin.execute("SELECT instance_id FROM horus.database_identity WHERE singleton = true").fetchone()[0])
    assert store.database_id == expected


def test_create_job_idempotency_and_canonical_json_comparison(pg_store):
    store, _, _ = pg_store
    payload = {"operation": "factory.profile", "channel_id": "canal01", "items": [1, 2], "ratio": 1}
    first, created_first = store.create_job(payload, "idem-1")
    second, created_second = store.create_job(dict(reversed(list(payload.items()))), "idem-1")
    assert created_first is True
    assert created_second is False
    assert first["id"] == second["id"]
    assert "idempotency_key" not in first
    assert "payload_canonical" not in first

    # Divergência 1 vs 1.0 preservada por payload_canonical (emenda A4).
    with pytest.raises(ValueError, match="idempotência"):
        store.create_job({"operation": "factory.profile", "channel_id": "canal01", "items": [1, 2], "ratio": 1.0}, "idem-1")

    # Divergência de ordem em lista preservada.
    with pytest.raises(ValueError, match="idempotência"):
        store.create_job({"operation": "factory.profile", "channel_id": "canal01", "items": [2, 1], "ratio": 1}, "idem-1")


@pytest.mark.pg_strong
def test_concurrent_create_job_same_key_inserts_once(pg_cluster, tmp_path, monkeypatch):
    with pg_cluster.connect(autocommit=True) as bootstrap:
        for name in ("anon", "authenticated"):
            bootstrap.execute(
                f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{name}') "
                f"THEN CREATE ROLE {name} NOLOGIN; END IF; END $$"
            )
    with pg_cluster.database() as pool:
        with pool.connection() as app:
            database = app.info.dbname
        with pg_cluster.connect(database, autocommit=True) as admin:
            admin.execute("DROP SCHEMA horus CASCADE")
            admin.execute("CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY)")
            apply_chain(admin)
            admin.execute(
                "INSERT INTO horus.schema_migrations(version, checksum, applied_at) "
                "VALUES (%s, %s, clock_timestamp()) ON CONFLICT (version) DO NOTHING",
                (EXPECTED_SCHEMA_VERSION, "a" * 64),
            )
            admin.execute(
                "INSERT INTO horus.database_identity(singleton, instance_id, created_at) "
                "VALUES (true, gen_random_uuid(), clock_timestamp()) ON CONFLICT (singleton) DO NOTHING"
            )
        manager = _make_pg_manager(pg_cluster, database, tmp_path, monkeypatch)
        store = Store(manager)
        try:
            barrier = threading.Barrier(2)
            payload = {"operation": "factory.profile", "channel_id": "canal01"}

            def worker():
                barrier.wait(timeout=5)
                return store.create_job(payload, "concurrent-key")

            with ThreadPoolExecutor(max_workers=2) as executor:
                r1, r2 = [f.result(timeout=10) for f in (executor.submit(worker), executor.submit(worker))]
            assert r1[0]["id"] == r2[0]["id"]
            assert sorted([r1[1], r2[1]]) == [False, True]
            assert len(store.list_jobs()) == 1
        finally:
            manager.close()


def test_events_segment_checkpoint_and_pruning(pg_store):
    store, _, _ = pg_store
    job, _ = store.create_job({"operation": "transcription.run", "path": "a.mp4", "model": "small", "language": "pt"}, "tx-1")
    seq1 = store.event(job["id"], "segment", "Olá mundo")
    seq2 = store.event(job["id"], "segment", "Segunda linha")
    assert seq2 > seq1
    current = store.get_job(job["id"])
    assert current["result"] == {"text": "Olá mundo\nSegunda linha\n", "partial": True, "sequence": seq2}

    # Poda mantém no máximo 2000 eventos preservando o texto acumulado.
    for idx in range(MAX_EVENTS_PER_JOB + 15):
        store.event(job["id"], "stdout", f"linha-{idx}")
    all_recent = []
    cursor = 0
    while True:
        batch = store.events(job["id"], cursor)
        if not batch:
            break
        all_recent.extend(batch)
        cursor = batch[-1]["sequence"]
    assert len(all_recent) == MAX_EVENTS_PER_JOB
    assert store.get_job(job["id"])["result"]["text"] == "Olá mundo\nSegunda linha\n"

    # Novo attempt reseta result com o maior sequence atual.
    store.update(job["id"], "running", new_attempt=True)
    restarted = store.get_job(job["id"])
    assert restarted["attempt"] == 1
    assert restarted["result"] == {"text": "", "partial": True, "sequence": cursor}


def test_old_queued_job_is_recovered_beyond_recent_history_and_media_filter(pg_store):
    store, _, _ = pg_store
    pending, _ = store.create_job({"operation": "factory.profile", "channel_id": "canal01"}, "older-pending")
    for idx in range(101):
        historical, _ = store.create_job({"operation": "factory.profile", "channel_id": "canal01"}, f"hist-{idx}")
        store.update(historical["id"], "succeeded")
    media_job, _ = store.create_job({"operation": "youtube.download", "url": "https://youtu.be/dQw4w9WgXcQ", "directory": "d"}, "media-1")

    assert pending["id"] in store.queued_ids()
    assert media_job["id"] in store.queued_ids()
    assert len(store.list_jobs()) == 100
    assert [j["id"] for j in store.list_jobs(media_only=True)] == [media_job["id"]]


def test_register_media_upsert_and_lookup(pg_store, tmp_path):
    store, _, _ = pg_store
    video = tmp_path / "video.mp4"
    video.write_bytes(b"12345")
    first = store.register_media(video)
    assert first["name"] == "video.mp4"
    assert first["size"] == 5

    video.write_bytes(b"123456789")
    second = store.register_media(video)
    assert second["id"] == first["id"]
    assert second["size"] == 9
    assert store.media(first["id"])["size"] == 9
    assert [item["id"] for item in store.list_media()] == [first["id"]]
