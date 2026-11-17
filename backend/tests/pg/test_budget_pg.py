"""Testes PostgreSQL de orçamento global Hermes (HOR-164 / I4, emenda A3)."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import threading
import time
import uuid

import pytest
from psycopg.rows import dict_row

from horus_backend.config import Settings
from horus_backend.hermes_contract.models import RunEnvelope
from horus_backend.hermes_simulator import editorial_result
import horus_backend.security as security
from horus_backend.storage import DatabaseUnavailable, Store, canonical_json_bytes
from horus_backend.workflow_hermes import CognitiveFailure, HermesStages
from schema_support import schema_database
from test_storage_jobs import _make_pg_manager

TOKEN = "security-budget-pg-session-01234567890123"
FIXTURES = Path(__file__).resolve().parents[3] / "contracts/hermes/fixtures"
ENVELOPE = json.loads((FIXTURES / "envelope-ficticio.json").read_text(encoding="utf-8"))
REQUEST = {name: ENVELOPE[name] for name in ("objective", "dataset", "channel_profile")}


class FakeClock:
    def __init__(self, initial: datetime | None = None):
        self.current = initial or datetime(2026, 11, 12, 23, 59, 30, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kw) -> None:
        self.current += timedelta(**kw)


class FakeHermes:
    def __init__(self, usage=None, hold=False):
        self.usage = usage if usage is not None else {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}
        self.hold, self.requests = hold, []
        self.lock = threading.Lock()

    def submit(self, body, key):
        with self.lock:
            self.requests.append((body, key))
            return {"run_id": "fake_" + str(len(self.requests)), "status": "queued"}

    def status(self, ref):
        if self.hold:
            return {"status": "running"}
        body = self.requests[int(ref.split("_")[1]) - 1][0]
        result = editorial_result(RunEnvelope.model_validate_json(body["input"]))
        return {"status": "completed", "usage": self.usage, "output": json.dumps(result)}

    def stop(self, ref):
        return {"status": "cancelled"}


@pytest.fixture(scope="module")
def _budget_schema_db(pg_cluster):
    with schema_database(pg_cluster) as state:
        yield state


@pytest.fixture
def pg_pool(request, pg_cluster):
    if request.node.get_closest_marker("pg_strong"):
        with schema_database(pg_cluster) as (_, pool):
            yield pool
    else:
        admin, pool = request.getfixturevalue("_budget_schema_db")
        try:
            yield pool
        finally:
            admin.execute(
                "TRUNCATE horus.cognitive_runs, horus.job_attempts, horus.jobs, horus.workflows, horus.transmissions RESTART IDENTITY CASCADE"
            )


@pytest.fixture
def settings(tmp_path):
    profile = tmp_path / "profile"
    security.harden_profile(profile)
    connection = tmp_path / "connection.json"
    connection.write_text(json.dumps({"base_url": "http://127.0.0.1:8643", "token": "connection-ficticia-01234567890123"}))
    return Settings(
        token=TOKEN,
        data_dir=tmp_path / "data",
        factory_root=None,
        hermes_executable=None,
        hermes_profile=profile,
        hermes_config=connection,
        workflow_executor="hermes",
        price_input_usd_per_mtok=Decimal("10"),
        price_output_usd_per_mtok=Decimal("20"),
        hermes_poll_seconds=0.01,
        hermes_run_timeout=5,
    )


def _context(store: Store, suffix: str, timestamp: datetime, *, simulated: bool = False):
    workflow, job, attempt = (uuid.uuid5(uuid.NAMESPACE_OID, prefix + suffix).hex for prefix in ("w-", "j-", "a-"))
    with store.uow() as conn:
        conn.execute(
            """INSERT INTO horus.workflows(
                id, idempotency_key, payload, payload_canonical, revision, status,
                created_at, updated_at, executor, cognitive_client
            ) VALUES (%s, %s, '{}'::jsonb, %s, 1, 'running', %s, %s, 'hermes', %s)""",
            (workflow, workflow, canonical_json_bytes({}), timestamp, timestamp, "simulated" if simulated else "live"),
        )
        conn.execute(
            """INSERT INTO horus.jobs(
                id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, workflow_id
            ) VALUES (%s, %s, '{}'::jsonb, %s, 'running', %s, %s, %s)""",
            (job, job, canonical_json_bytes({}), timestamp, timestamp, workflow),
        )
        conn.execute(
            """INSERT INTO horus.job_attempts(
                id, job_id, number, status, executor, input_hash, input_artifacts, input_artifacts_canonical, started_at
            ) VALUES (%s, %s, 1, 'running', 'hermes-v1', 'fixture', '[]'::jsonb, %s, %s)""",
            (attempt, job, canonical_json_bytes([]), timestamp),
        )
    return {
        "workflow_id": workflow,
        "job_id": job,
        "revision": 1,
        "objective": REQUEST["objective"],
        "cognitive_client": "simulated" if simulated else "live",
        "sources": [{"id": "snapshot", "content": {name: REQUEST[name] for name in ("dataset", "channel_profile")}}],
    }, attempt


def _execute(stages: HermesStages, ctx):
    return asyncio.run(stages.analysis(ctx[0], ctx[1], lambda: False))


def test_cost_is_recorded_with_frozen_prices_and_cache_conservatively(pg_pool, settings):
    store = Store(pg_pool)
    clock = FakeClock()
    fake = FakeHermes({"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1700, "cache_read_tokens": 100})
    stages = HermesStages(store, settings, client_factory=lambda: fake, clock=clock)
    ctx = _context(store, "cache", clock())
    _execute(stages, ctx)
    with store.uow(readonly=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute("SELECT usage FROM horus.cognitive_runs").fetchone()
    usage = row["usage"]
    # 1000×10/1M + 500×20/1M + 100×20/1M + 100×20/1M = 0.024 USD
    assert Decimal(usage["horus_estimated_cost_usd"]) == Decimal("0.024")
    assert usage["horus_cost_finalized"] is True


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"total_tokens": 100},
        {"input_tokens": 100, "output_tokens": 100, "total_tokens": 100},
        {"input_tokens": True, "output_tokens": 10, "total_tokens": 20},
    ],
)
def test_unknown_or_invalid_usage_retains_full_reservation(pg_pool, settings, usage):
    store = Store(pg_pool)
    clock = FakeClock()
    fake = FakeHermes(usage)
    stages = HermesStages(store, settings, client_factory=lambda: fake, clock=clock)
    _execute(stages, _context(store, "inv", clock()))
    usage_report = security.budget_usage(store, day=clock())
    assert usage_report["reserved_usd"] == security.cost_reservation(settings)
    assert usage_report["estimated_spent_total_usd"] == Decimal(0)


@pytest.mark.parametrize("scope", ["daily", "total"])
def test_reservation_blocks_exceeding_budget_and_allows_equality(pg_pool, settings, scope):
    store = Store(pg_pool)
    clock = FakeClock()
    cap = replace(
        settings,
        max_tokens_per_run=10000,
        max_tokens_per_day=30000,
        max_runs_per_day=5,
        budget_total_usd=Decimal("0.40") if scope == "total" else Decimal("5"),
        budget_daily_usd=Decimal("0.40"),
    )
    fake = FakeHermes({"input_tokens": 100, "output_tokens": 100, "total_tokens": 200})
    stages = HermesStages(store, cap, client_factory=lambda: fake, clock=clock)
    _execute(stages, _context(store, "1", clock()))
    assert fake.requests
    # Reserva por execução = 0.20 USD; após gastar 0.003, sobram 0.397 -> permite 1 execução, bloqueia a 2ª.
    _execute(stages, _context(store, "2", clock()))
    assert len(fake.requests) == 2
    cap2 = replace(cap, budget_daily_usd=Decimal("0.205"), budget_total_usd=Decimal("0.205") if scope == "total" else Decimal("5"))
    stages2 = HermesStages(store, cap2, client_factory=lambda: fake, clock=clock)
    with pytest.raises(CognitiveFailure, match="security_gate"):
        _execute(stages2, _context(store, "3", clock()))
    assert len(fake.requests) == 2


def test_utc_rollover_resets_daily_spend_and_runs_but_keeps_total(pg_pool, settings):
    store = Store(pg_pool)
    clock = FakeClock(datetime(2026, 11, 12, 23, 59, 30, tzinfo=timezone.utc))
    cap = replace(settings, max_tokens_per_run=10000, max_tokens_per_day=20000, max_runs_per_day=1,
                  budget_daily_usd=Decimal("0.20"), budget_total_usd=Decimal("0.30"))
    fake = FakeHermes()
    stages = HermesStages(store, cap, client_factory=lambda: fake, clock=clock)
    _execute(stages, _context(store, "d1", clock()))
    assert security.daily_runs(store, day=clock()) == 1
    clock.advance(seconds=35)  # 00:00:05 UTC do dia seguinte
    assert security.daily_runs(store, day=clock()) == 0
    assert security.budget_usage(store, day=clock())["estimated_spent_today_usd"] == Decimal(0)
    assert security.budget_usage(store, day=clock())["estimated_spent_total_usd"] > Decimal(0)
    _execute(stages, _context(store, "d2", clock()))
    assert len(fake.requests) == 2
    # Total ~ 0.04; se budget_total_usd=0.22, próxima reserva de 0.20 excede o total mesmo em dia novo.
    cap_total = replace(cap, budget_total_usd=Decimal("0.22"))
    with pytest.raises(CognitiveFailure, match="security_gate"):
        _execute(HermesStages(store, cap_total, client_factory=lambda: fake, clock=clock), _context(store, "d3", clock()))


def test_iso_offset_timestamps_respect_utc_day_boundary(pg_pool, settings):
    store = Store(pg_pool)
    # 2026-11-12T22:30:00-03:00 == 2026-11-13T01:30:00Z (já é dia 13 em UTC!)
    t_offset = datetime.fromisoformat("2026-11-12T22:30:00-03:00")
    fake = FakeHermes()
    stages = HermesStages(store, settings, client_factory=lambda: fake, clock=lambda: t_offset)
    _execute(stages, _context(store, "offset", t_offset))
    assert security.daily_runs(store, day="2026-11-12") == 0
    assert security.daily_runs(store, day="2026-11-13") == 1
    assert security.budget_usage(store, day="2026-11-12")["estimated_spent_today_usd"] == Decimal(0)
    assert security.budget_usage(store, day="2026-11-13")["estimated_spent_today_usd"] > Decimal(0)


def test_open_and_unknown_reservations_cross_utc_midnight(pg_pool, settings):
    store = Store(pg_pool)
    clock = FakeClock(datetime(2026, 11, 12, 23, 59, 30, tzinfo=timezone.utc))
    cap = replace(settings, max_tokens_per_run=10000, max_tokens_per_day=20000, max_runs_per_day=5,
                  budget_daily_usd=Decimal("0.25"), budget_total_usd=Decimal("5"))
    stages = HermesStages(store, cap, client_factory=lambda: FakeHermes(hold=True), clock=clock)
    ctx = _context(store, "open", clock())
    # Simula execução presa em 'admitted' / 'unknown'
    with store.uow() as conn:
        body = {"input": "{}", "instructions": "x", "metadata": {
            "horus_token_reservation": "10000",
            "horus_cost_reservation_usd": "0.20",
        }}
        conn.execute(
            """INSERT INTO horus.cognitive_runs(
                id, job_id, attempt_id, provider, idempotency_key, request, request_hash,
                request_canonical, status, created_at, updated_at
            ) VALUES (%s, %s, %s, 'hermes', 'k-open', %s::jsonb, 'h', %s, 'unknown', %s, %s)""",
            (uuid.uuid4().hex, ctx[0]["job_id"], ctx[1], json.dumps(body), canonical_json_bytes(body), clock(), clock()),
        )
    clock.advance(days=1)  # meia-noite UTC passou, mas reserva 'unknown' continua aberta
    usage = security.budget_usage(store, day=clock())
    assert usage["reserved_usd"] == Decimal("0.20")
    assert security.daily_tokens(cap, store, day=clock()) == 10000
    with pytest.raises(CognitiveFailure, match="security_gate"):
        _execute(stages, _context(store, "next", clock()))


@pytest.mark.pg_strong
def test_concurrent_admissions_across_workflows_never_overspend(pg_pool, settings):
    store = Store(pg_pool)
    cap = replace(settings, max_tokens_per_run=10000, max_tokens_per_day=15000, max_runs_per_day=1,
                  budget_daily_usd=Decimal("0.25"), budget_total_usd=Decimal("0.25"))
    clock = FakeClock()
    fake = FakeHermes()

    class SlowFake(FakeHermes):
        def submit(self, body, key):
            time.sleep(0.02)
            return super().submit(body, key)

    slow = SlowFake()
    barrier = threading.Barrier(4)
    results = []
    lock = threading.Lock()

    def worker(i: int):
        stages = HermesStages(store, cap, client_factory=lambda: slow, clock=clock)
        ctx = _context(store, f"conc-{i}", clock())
        barrier.wait()
        try:
            _execute(stages, ctx)
            with lock:
                results.append("ok")
        except CognitiveFailure:
            with lock:
                results.append("refused")

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(worker, range(4)))
    assert results.count("ok") == 1
    assert results.count("refused") == 3
    assert len(slow.requests) == 1
    assert security.daily_runs(store, day=clock()) == 1


def test_simulation_ignores_all_limits_prices_and_accounting(pg_pool, settings):
    store = Store(pg_pool)
    clock = FakeClock()
    broken = replace(settings, max_runs_per_day=0, max_tokens_per_run=0, budget_total_usd=None, price_input_usd_per_mtok=None)
    fake = FakeHermes()
    stages = HermesStages(store, broken, client_factory=lambda: fake, clock=clock)
    _execute(stages, _context(store, "sim", clock(), simulated=True))
    assert len(fake.requests) == 1
    assert security.daily_runs(store, day=clock()) == 0
    assert security.budget_usage(store, day=clock())["estimated_spent_total_usd"] == Decimal(0)


def test_budget_raises_503_when_database_unavailable(pg_cluster, tmp_path, monkeypatch, settings):
    with schema_database(pg_cluster) as (_, pool):
        with pool.connection() as conn:
            db_name = conn.info.dbname
        unconfigured = _make_pg_manager(pg_cluster, db_name, tmp_path / "unconfigured", monkeypatch, configure=False)
        store = Store(unconfigured)
        with pytest.raises(DatabaseUnavailable) as err:
            security.budget_usage(store)
        assert err.value.http_status == 503
        with pytest.raises(DatabaseUnavailable):
            security.daily_runs(store)
        with pytest.raises(DatabaseUnavailable):
            security.daily_tokens(settings, store)
