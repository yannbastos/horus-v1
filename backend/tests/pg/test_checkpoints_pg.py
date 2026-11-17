"""Testes PostgreSQL para human_checkpoints (HOR-164 / I4, emenda A1)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import threading
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from horus_backend.config import Settings
from horus_backend.human_checkpoints import (
    ACTIONS,
    CheckpointRefused,
    CheckpointService,
    require_publication_checkpoint,
)
from horus_backend.security import SecurityGate
from horus_backend.storage import DatabaseUnavailable, Store
from horus_backend.transmissions import TransmissionLog
from schema_support import schema_database
from test_storage_jobs import _make_pg_manager

SUBJECT = hashlib.sha256(b"objeto").hexdigest()
OTHER = hashlib.sha256(b"outro").hexdigest()


class Clock:
    def __init__(self, initial: datetime | None = None):
        self.now = initial or datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


@pytest.fixture(scope="module")
def _checkpoints_schema_db(pg_cluster):
    with schema_database(pg_cluster) as state:
        yield state


@pytest.fixture
def pg_pool(request, pg_cluster):
    if request.node.get_closest_marker("pg_strong"):
        with schema_database(pg_cluster) as (_, pool):
            yield pool
    else:
        admin, pool = request.getfixturevalue("_checkpoints_schema_db")
        try:
            yield pool
        finally:
            admin.execute("TRUNCATE horus.human_checkpoints, horus.transmissions RESTART IDENTITY CASCADE")


@pytest.fixture
def env(pg_pool):
    store = Store(pg_pool)
    clock = Clock()
    log = TransmissionLog(store)
    return SimpleNamespace(store=store, clock=clock, log=log, service=CheckpointService(store, log, clock))


@pytest.fixture
def settings(tmp_path):
    return Settings(
        token="checkpoints-pg-test-token-01234567890123456789",
        data_dir=tmp_path / "data",
        max_tokens_per_run=100000,
        max_tokens_per_day=200000,
        max_runs_per_day=5,
        budget_total_usd=Decimal("10"),
        budget_daily_usd=Decimal("5"),
        price_input_usd_per_mtok=Decimal("1"),
        price_output_usd_per_mtok=Decimal("2"),
    )


def rows(store: Store, table: str = "human_checkpoints") -> list[dict]:
    with store.uow(readonly=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return [dict(r) for r in cur.execute(f"SELECT * FROM horus.{table} ORDER BY seq").fetchall()]


def grant(service: CheckpointService, **over) -> dict:
    args = dict(
        action="paid_narration",
        subject=SUBJECT,
        description="Narração paga de 800 caracteres",
        limit_amount="1.50",
        currency="USD",
        ttl_seconds=600,
    )
    return service.grant(**(args | over))


def refusal(env, **kw) -> str:
    with pytest.raises(CheckpointRefused) as error:
        env.service.consume(
            **({"action": "paid_narration", "subject": SUBJECT, "amount": Decimal("1.00"), "currency": "USD", "ref": "r"} | kw)
        )
    assert isinstance(error.value, SecurityGate)
    return error.value.code


def test_grant_records_who_when_what_limit_and_expiry(env):
    t0 = env.clock.now
    record = grant(env.service, key="k1")
    assert (record["approved_by"], record["operator_id"], record["action"], record["subject"]) == (
        "human",
        "local-operator",
        "paid_narration",
        SUBJECT,
    )
    assert record["approved_at"] == t0.isoformat()
    assert record["expires_at"] == (t0 + timedelta(seconds=600)).isoformat()
    assert (record["limit_amount"], record["currency"], record["consumed_at"]) == ("1.50", "USD", None)
    assert len(rows(env.store)) == 1


@pytest.mark.parametrize(
    "override",
    [
        {"action": "deploy"},
        {"action": "PUBLISH"},
        {"subject": "abc"},
        {"subject": SUBJECT.upper()},
        {"subject": None},
        {"approved_by": "agent"},
        {"approved_by": "system"},
        {"description": ""},
        {"description": "x" * 201},
        {"description": "a\nb"},
        {"ttl_seconds": 29},
        {"ttl_seconds": 3601},
        {"ttl_seconds": "600"},
        {"ttl_seconds": True},
        {"limit_amount": None},
        {"currency": None},
        {"limit_amount": "0", "currency": "USD"},
        {"limit_amount": "-1", "currency": "USD"},
        {"limit_amount": "1e3", "currency": "USD"},
        {"limit_amount": "NaN", "currency": "USD"},
        {"limit_amount": 1.5, "currency": "USD"},
        {"limit_amount": "1", "currency": "usd"},
        {"limit_amount": "1", "currency": "US"},
    ],
)
def test_invalid_grants_are_refused_and_store_nothing(env, override):
    with pytest.raises(ValueError):
        grant(env.service, **override)
    assert rows(env.store) == []


def test_grant_is_idempotent_and_key_cannot_be_reused_for_another_request(env):
    first = grant(env.service, key="k")
    assert grant(env.service, key="k")["id"] == first["id"]
    assert len(rows(env.store)) == 1
    with pytest.raises(ValueError):
        grant(env.service, key="k", limit_amount="9.99")
    assert len(rows(env.store)) == 1


@pytest.mark.parametrize("action", ["hermes_run", "hermes_script"])
@pytest.mark.parametrize("transactional", [False, True])
def test_backend_ceiling_refuses_larger_grants_before_insert(env, settings, action, transactional):
    service = CheckpointService(env.store, env.log, env.clock, settings=settings)
    kwargs = dict(action=action, limit_amount="0.20000001")
    with pytest.raises(CheckpointRefused) as error:
        if transactional:
            with env.store.uow() as conn:
                grant(service, **kwargs, db=conn)
        else:
            grant(service, **kwargs)
    assert error.value.code == "checkpoint_limit_exceeds_backend_ceiling"
    assert rows(env.store) == []


@pytest.mark.parametrize("amount", ["0.01", "0.2", "0.20000000"])
def test_backend_ceiling_accepts_lower_or_equal_grants(env, settings, amount):
    service = CheckpointService(env.store, settings=settings)
    assert grant(service, action="hermes_run", limit_amount=amount)["limit_amount"] == amount


def test_backend_ceiling_cannot_be_bypassed_with_another_currency(env, settings):
    with pytest.raises(CheckpointRefused, match="teto"):
        grant(CheckpointService(env.store, settings=settings), action="hermes_run", currency="EUR", limit_amount="0.01")
    assert rows(env.store) == []


def test_monetary_hermes_grant_requires_backend_configuration(env, settings):
    for configured in (None, replace(settings, price_output_usd_per_mtok=None)):
        with pytest.raises(SecurityGate):
            grant(CheckpointService(env.store, settings=configured), action="hermes_run", limit_amount="0.01")
    assert rows(env.store) == []


@pytest.mark.parametrize("action", ["paid_narration", "publish"])
def test_actions_without_backend_ceiling_preserve_existing_grants_and_consumption(env, settings, action):
    service = CheckpointService(env.store, settings=settings)
    record = grant(service, action=action, limit_amount="9.99")
    assert record["limit_amount"] == "9.99"
    with pytest.raises(CheckpointRefused) as error:
        service.consume(action=action, subject=SUBJECT, amount=Decimal("10"), currency="USD", ref="blocked")
    assert error.value.code == "checkpoint_limit_exceeded"


def test_without_any_approval_nothing_is_allowed(env):
    assert refusal(env) == "checkpoint_missing"
    for action in ACTIONS:
        assert refusal(env, action=action) == "checkpoint_missing"
    with pytest.raises(ValueError):
        env.service.consume(action="deploy", subject=SUBJECT, ref="r")


def test_approval_for_another_object_or_action_is_useless(env):
    grant(env.service)
    assert refusal(env, subject=OTHER) == "checkpoint_missing"
    assert refusal(env, action="publish") == "checkpoint_missing"
    assert refusal(env, action="hermes_run") == "checkpoint_missing"
    assert rows(env.store)[0]["consumed_at"] is None


def test_expired_approval_and_boundary_are_refused(env):
    grant(env.service, ttl_seconds=60)
    env.clock.advance(seconds=59)
    env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1"), currency="USD", ref="ok")
    second = grant(env.service, ttl_seconds=60, description="segunda")
    env.clock.advance(seconds=60)  # exatamente no instante expires_at (fronteira)
    assert refusal(env) == "checkpoint_expired"
    assert [r["consumed_at"] is not None for r in rows(env.store)] == [True, False]
    assert second["consumed_at"] is None


def test_latest_valid_with_insufficient_limit_does_not_fallback_to_earlier_permissive(env):
    grant(env.service, limit_amount="5.00", description="mais permissivo anterior")
    grant(env.service, limit_amount="0.50", description="mais recente restrito")
    assert refusal(env, amount=Decimal("1.00"), currency="USD") == "checkpoint_limit_exceeded"
    assert [r["consumed_at"] for r in rows(env.store)] == [None, None]


def test_latest_expired_skips_to_earlier_valid_and_tie_breaks_by_seq_desc(env):
    first = grant(env.service, ttl_seconds=300, limit_amount="2.00", description="anterior mais longo")
    second = grant(env.service, ttl_seconds=30, limit_amount="1.00", description="segundo mais curto")
    env.clock.advance(seconds=31)
    # Segundo expirou; o primeiro ainda está válido e deve ser escolhido.
    consumed = env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1.50"), currency="USD", ref="ok1")
    assert consumed["id"] == first["id"]

    # Empate temporal exato: dois grants no mesmo instante -> escolhe o maior seq.
    a = grant(env.service, description="empate-1")
    b = grant(env.service, description="empate-2")
    assert a["approved_at"] == b["approved_at"] and b["seq"] > a["seq"]
    chosen = env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1.00"), currency="USD", ref="ok2")
    assert chosen["id"] == b["id"]
    assert second["id"] != chosen["id"]


@pytest.mark.parametrize(
    "amount,currency",
    [(Decimal("1.5000001"), "USD"), (Decimal("1000"), "USD"), (Decimal("0.01"), "EUR"), (Decimal("1"), None)],
)
def test_amount_above_limit_or_other_currency_is_refused_and_not_consumed(env, amount, currency):
    grant(env.service)
    assert refusal(env, amount=amount, currency=currency) == "checkpoint_limit_exceeded"
    assert rows(env.store)[0]["consumed_at"] is None
    env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1.50"), currency="USD", ref="ok")


def test_a_spending_action_with_an_unlimited_approval_is_refused(env):
    grant(env.service, limit_amount=None, currency=None)
    assert refusal(env) == "checkpoint_limit_exceeded"


def test_replay_is_refused_and_a_new_approval_is_needed(env):
    t0 = env.clock.now
    grant(env.service)
    done = env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1"), currency="USD", ref="geracao-1")
    assert done["consumed_ref"] == "geracao-1" and done["consumed_at"] == t0.isoformat()
    assert refusal(env) == "checkpoint_consumed"
    grant(env.service, description="nova aprovação")
    env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1"), currency="USD", ref="geracao-2")
    assert refusal(env) == "checkpoint_consumed"


@pytest.mark.pg_strong
def test_concurrent_attempts_consume_the_approval_only_once(pg_pool):
    store = Store(pg_pool)
    service = CheckpointService(store, TransmissionLog(store))
    grant(service, ttl_seconds=600)
    outcomes, barrier = [], threading.Barrier(8)
    lock = threading.Lock()

    def attempt(i: int):
        barrier.wait()
        try:
            service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1"), currency="USD", ref=f"t{i}")
            with lock:
                outcomes.append("ok")
        except CheckpointRefused as error:
            with lock:
                outcomes.append(error.code)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(attempt, range(8)))
    assert outcomes.count("ok") == 1
    assert set(outcomes) <= {"ok", "checkpoint_consumed"}
    assert sum(r["consumed_at"] is not None for r in rows(store)) == 1


def test_publication_barrier_requires_a_human_checkpoint_per_object(env):
    with pytest.raises(CheckpointRefused):
        require_publication_checkpoint(env.service, SUBJECT, ref="youtube")
    grant(env.service, action="publish", limit_amount=None, currency=None, description="Publicar vídeo aprovado")
    assert require_publication_checkpoint(env.service, SUBJECT, ref="youtube")["consumed_ref"] == "youtube"
    with pytest.raises(CheckpointRefused):
        require_publication_checkpoint(env.service, SUBJECT, ref="youtube")


def test_database_rejects_delete_edit_and_a_second_consumption(env):
    grant(env.service)
    env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1"), currency="USD", ref="r")
    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation)):
        with env.store.uow() as conn:
            conn.execute("DELETE FROM horus.human_checkpoints")
    for statement in (
        "UPDATE horus.human_checkpoints SET consumed_at = NULL, consumed_ref = NULL",
        "UPDATE horus.human_checkpoints SET consumed_ref = 'outro'",
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            with env.store.uow() as conn:
                conn.execute(statement)
    # Colunas protegidas por GRANT UPDATE(consumed_at, consumed_ref) para horus_app:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with env.store.uow() as conn:
            conn.execute("UPDATE horus.human_checkpoints SET limit_amount = 999")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with env.store.uow() as conn:
            conn.execute("UPDATE horus.human_checkpoints SET expires_at = '2099-01-01T00:00:00+00:00'")


def test_refusals_are_audited_without_content_and_success_does_not_duplicate_the_ledger(env):
    with pytest.raises(CheckpointRefused):
        env.service.consume(action="publish", subject=SUBJECT, ref="segredo-sk-" + "a" * 40)
    grant(env.service)
    refusal(env, amount=Decimal("99"))
    env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1"), currency="USD", ref="ok")
    ledger = rows(env.store, "transmissions")
    assert [(r["kind"], r["stage"], r["result"], r["detail"]) for r in ledger] == [
        ("gate_refusal", "checkpoint:publish", "refused_gate", "checkpoint_missing"),
        ("gate_refusal", "checkpoint:paid_narration", "refused_gate", "checkpoint_limit_exceeded"),
    ]
    assert "segredo" not in json.dumps(ledger, default=str)


def test_checkpoint_service_raises_503_when_database_unavailable(pg_cluster, tmp_path, monkeypatch):
    with schema_database(pg_cluster) as (_, pool):
        with pool.connection() as conn:
            db_name = conn.info.dbname
        unconfigured = _make_pg_manager(pg_cluster, db_name, tmp_path / "unconfigured", monkeypatch, configure=False)
        service = CheckpointService(Store(unconfigured))
        with pytest.raises(DatabaseUnavailable) as err:
            grant(service)
        assert err.value.http_status == 503
        assert err.value.code == "database_unavailable"
        with pytest.raises(DatabaseUnavailable):
            service.page()
