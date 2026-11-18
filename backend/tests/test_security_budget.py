"""Orçamento local offline: relógio UTC controlado, banco isolado e Hermes em memória."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import socket
import threading
import time
import uuid

from fastapi.testclient import TestClient
import psycopg
from psycopg.rows import dict_row
import pytest

from horus_backend.api import create_app
from horus_backend.config import Settings
from horus_backend.hermes_contract.models import RunEnvelope
from horus_backend.hermes_simulator import editorial_result
import horus_backend.security as security
import horus_backend.workflow_hermes as adapter
from support_h1 import FakeClock, admin, make_context, store_for, wire_app_database

TOKEN = 'security-budget-offline-session-01234567890123'
HEADERS = {'Authorization': 'Bearer ' + TOKEN}
FIXTURES = Path(__file__).resolve().parents[2] / 'contracts/hermes/fixtures'
ENVELOPE = json.loads((FIXTURES / 'envelope-ficticio.json').read_text(encoding='utf-8'))
REQUEST = {name: ENVELOPE[name] for name in ('objective', 'dataset', 'channel_profile')}
VARIABLES = {
    'HORUS_MAX_TOKENS_PER_RUN': ('max_tokens_per_run', 16384),
    'HORUS_MAX_TOKENS_PER_DAY': ('max_tokens_per_day', 32768),
    'HORUS_MAX_RUNS_PER_DAY': ('max_runs_per_day', 2),
    'HORUS_BUDGET_TOTAL_USD': ('budget_total_usd', Decimal('5')),
    'HORUS_BUDGET_DAILY_USD': ('budget_daily_usd', Decimal('1')),
    'HORUS_PRICE_INPUT_USD_PER_MTOK': ('price_input_usd_per_mtok', None),
    'HORUS_PRICE_OUTPUT_USD_PER_MTOK': ('price_output_usd_per_mtok', None),
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    original_connect = socket.socket.connect
    def loopback_only(sock, address):
        # Windows pode usar loopback internamente para o socketpair do asyncio.
        if isinstance(address, tuple):
            assert address[0] in {'127.0.0.1', '::1'}, 'socket externo proibido'
        return original_connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', loopback_only)
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('conexão HTTP não esperada'))


@pytest.fixture
def clock():
    """Relógio UTC controlado, injetado via `clock=` (o PG usa `pg_clock`, não `datetime.now`)."""
    return FakeClock()


@pytest.fixture
def store(pg_app_cluster, pg_app_database, tmp_path, monkeypatch):
    return store_for(pg_app_cluster, pg_app_database, tmp_path, monkeypatch)


@pytest.fixture
def pg_settings(settings, pg_app_cluster, pg_app_database, monkeypatch):
    return wire_app_database(pg_app_cluster, pg_app_database, settings, monkeypatch)


@pytest.fixture
def settings(tmp_path):
    profile = tmp_path / 'profile'
    security.harden_profile(profile)
    connection = tmp_path / 'connection.json'
    connection.write_text(json.dumps({'base_url': 'http://127.0.0.1:8643',
                                      'token': 'connection-ficticia-01234567890123'}))
    return Settings(token=TOKEN, data_dir=tmp_path / 'data', factory_root=None, hermes_executable=None,
                    hermes_profile=profile, hermes_config=connection, workflow_executor='hermes',
                    price_input_usd_per_mtok=Decimal('10'), price_output_usd_per_mtok=Decimal('20'),
                    hermes_poll_seconds=.01, hermes_run_timeout=5)


class FakeHermes:
    """Nenhum transporte: permite manter execução aberta e escolher usage."""
    def __init__(self, usage=None, hold=False):
        self.usage = usage if usage is not None else {'input_tokens': 1000, 'output_tokens': 500, 'total_tokens': 1500}
        self.hold, self.requests = hold, []
        self.lock = threading.Lock()

    def submit(self, body, key):
        with self.lock:
            self.requests.append((body, key))
            return {'run_id': 'fake_' + str(len(self.requests)), 'status': 'queued'}

    def status(self, ref):
        if self.hold:
            return {'status': 'running'}
        body = self.requests[int(ref.split('_')[1]) - 1][0]
        result = editorial_result(RunEnvelope.model_validate_json(body['input']))
        return {'status': 'completed', 'usage': self.usage, 'output': json.dumps(result)}

    def stop(self, ref):
        return {'status': 'cancelled'}


def context(store, suffix, timestamp, simulated=False):
    """Preparar uma tentativa local válida; não criar contratos/schema adicionais."""
    return make_context(store, suffix, timestamp, REQUEST['objective'], REQUEST, simulated)


def execute(stages, ctx):
    return asyncio.run(stages.analysis(ctx[0], ctx[1], lambda: False))


def report(settings, store, clock=None):
    return security.security_status(settings, store, clock=clock)['hermes']['cost_limits']


@pytest.mark.parametrize('missing', [None, 'price_input_usd_per_mtok', 'price_output_usd_per_mtok',
    'budget_total_usd', 'budget_daily_usd', 'max_tokens_per_run', 'max_tokens_per_day'])
def test_status_exposes_backend_checkpoint_limits_and_exact_reservation(settings, missing):
    from horus_backend.human_checkpoints import DEFAULT_TTL_SECONDS, MAX_TTL_SECONDS
    actual = replace(settings, **{missing: None}) if missing else settings
    with TestClient(create_app(actual), base_url='http://127.0.0.1', headers=HEADERS) as client:
        response = client.get('/v1/security/status')
        assert response.status_code == 200
        hermes = response.json()['hermes']
        assert hermes['checkpoint_limits'] == {'default_ttl_seconds': DEFAULT_TTL_SECONDS,
                                               'max_ttl_seconds': MAX_TTL_SECONDS}
        reservation = hermes['cost_limits']['reservation_per_run_usd']
        if missing:
            assert reservation is None
            assert not response.json()['real_execution_allowed']
        else:
            assert isinstance(reservation, str)
            assert reservation == format(security.cost_reservation(actual), 'f') == '0.32768'


def test_approved_defaults_apply_without_environment(settings, monkeypatch):
    actual = Settings(token=TOKEN, data_dir=settings.data_dir, hermes_executable=None, factory_root=None)
    for name, (field, default) in VARIABLES.items():
        assert getattr(actual, field) == default
    assert security.cost_configured(actual) and security.budget_configured(actual)
    assert not security.pricing_configured(actual)


@pytest.mark.parametrize('name', VARIABLES)
def test_operator_override_and_invalid_never_fall_back(settings, monkeypatch, name):
    field, _ = VARIABLES[name]
    value = '123' if 'TOKENS' in name or 'RUNS' in name else '0.125'
    monkeypatch.setenv(name, value)
    actual = Settings(token=TOKEN, data_dir=settings.data_dir, hermes_executable=None, factory_root=None)
    assert getattr(actual, field) == (123 if value == '123' else Decimal('0.125'))
    for invalid in ('', '0', '-1', 'NaN', 'Infinity', '1e2', 'abc', ' 2 ', '１２', '0.0000000000001', '9' * 100):
        monkeypatch.setenv(name, invalid)
        actual = Settings(token=TOKEN, data_dir=settings.data_dir, hermes_executable=None, factory_root=None)
        assert getattr(actual, field) is None, invalid
        candidate = replace(settings, **{field: getattr(actual, field)})
        assert not security.security_status(candidate)['real_execution_allowed']


@pytest.mark.parametrize('total,daily', [(None, Decimal('1')), (Decimal('5'), None),
    (Decimal('1'), Decimal('2')), (Decimal('0'), Decimal('1')), (True, Decimal('1'))])
def test_invalid_budget_fails_closed(settings, total, daily):
    actual = replace(settings, budget_total_usd=total, budget_daily_usd=daily)
    assert 'budget_limits_invalid' in {b['code'] for b in security.security_status(actual)['blockers']}
    with pytest.raises(security.SecurityGate):
        security.cost_reservation(actual)


@pytest.mark.parametrize('field', ['price_input_usd_per_mtok', 'price_output_usd_per_mtok'])
def test_missing_pricing_blocks_authorization_before_fake_client(pg_settings, field):
    actual = replace(pg_settings, **{field: None})
    app, fake = create_app(actual), FakeHermes()
    app.state.manager.workflows.hermes.client_factory = lambda: fake
    with TestClient(app, base_url='http://127.0.0.1', headers=HEADERS) as client:
        created = client.post('/v1/workflows', json=REQUEST, headers={'Idempotency-Key': 'create'}).json()
        workflow = wait_workflow(client, created['id'], lambda w: w['pending_authorization'] is not None)
        response = authorize(client, workflow, 'denied')
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'security_gate'
        assert fake.requests == []
        status = client.get('/v1/security/status').json()
        blocker = next(b for b in status['blockers'] if b['code'] == 'pricing_missing')
        assert blocker['severity'] == 'critical'
        assert status['hermes']['cost_limits']['pricing_configured'] is False


def stored(store, sql):
    """Primeira linha como dict; colunas jsonb já chegam desserializadas do PG."""
    with store.uow(readonly=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(sql).fetchone()


def test_cost_is_recorded_with_frozen_prices_and_cache_conservatively(settings, store, clock):
    fake = FakeHermes({'input_tokens': 1000, 'output_tokens': 500, 'total_tokens': 1550,
                       'cache_read_tokens': 20, 'cache_write_tokens': 10})
    stages = adapter.HermesStages(store, settings, lambda: fake, clock=clock)
    execute(stages, context(store, 'first', clock.current.isoformat()))
    row = stored(store, 'SELECT request, usage FROM horus.cognitive_runs')
    metadata, usage = row['request']['metadata'], row['usage']
    assert metadata['horus_cost_reservation_usd'] == '0.32768'
    # 1000×10 + 500×20 + (30 cache + 20 não classificados)×20, por milhão.
    assert Decimal(usage['horus_estimated_cost_usd']) == Decimal('0.021')
    assert usage['horus_cost_finalized'] is True
    changed = replace(settings, price_input_usd_per_mtok=Decimal('100'), price_output_usd_per_mtok=Decimal('200'))
    limits = report(changed, store, clock)
    assert Decimal(limits['estimated_spent_total_usd']) == Decimal('0.021')
    assert Decimal(limits['estimated_spent_today_usd']) == Decimal('0.021')
    assert limits['reserved_usd'] == '0' and limits['runs_today'] == 1


@pytest.mark.parametrize('usage', [{}, {'total_tokens': 20}, {'input_tokens': 10, 'output_tokens': 10},
    {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 19},
    {'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20, 'cache_read_tokens': -1},
    {'input_tokens': True, 'output_tokens': 10, 'total_tokens': 20}])
def test_unknown_or_invalid_usage_retains_full_reservation(settings, store, clock, usage):
    execute(adapter.HermesStages(store, settings, lambda: FakeHermes(usage), clock=clock),
            context(store, 'unknown', clock.current.isoformat()))
    saved = stored(store, 'SELECT usage FROM horus.cognitive_runs')['usage']
    assert saved['horus_estimated_cost_usd'] == '0.32768' and saved['horus_cost_finalized'] is False
    assert report(settings, store, clock)['reserved_usd'] == '0.32768'
    assert report(settings, store, clock)['estimated_spent_total_usd'] == '0'


@pytest.mark.parametrize('scope', ['total', 'daily'])
def test_reservation_blocks_exceeding_budget_and_allows_equality(settings, store, clock, scope):
    # Cada reserva custa exatamente 0.32768 USD; o teto comporta exatamente uma.
    settings = replace(settings, budget_total_usd=Decimal('0.32768') if scope == 'total' else Decimal('5'),
                       budget_daily_usd=Decimal('0.32768'))
    fake = FakeHermes({})
    stages = adapter.HermesStages(store, settings, lambda: fake, clock=clock)
    execute(stages, context(store, 'one', clock.current.isoformat()))
    with pytest.raises(adapter.CognitiveFailure, match='Saldo total' if scope == 'total' else 'Saldo diário'):
        execute(stages, context(store, 'two', clock.current.isoformat()))
    assert len(fake.requests) == 1
    status = security.security_status(settings, store, clock=clock)
    assert ('total_budget_limit' if scope == 'total' else 'daily_budget_limit') in {b['code'] for b in status['blockers']}


def test_utc_rollover_resets_daily_spend_and_runs_but_keeps_total(settings, store, clock):
    fake = FakeHermes()
    execute(adapter.HermesStages(store, settings, lambda: fake, clock=clock), context(store, 'one', clock.current.isoformat()))
    before = report(settings, store, clock)
    assert before['runs_today'] == 1 and Decimal(before['estimated_spent_today_usd']) == Decimal('.02')
    clock.current = datetime(2026, 11, 13, tzinfo=timezone.utc)
    after = report(settings, store, clock)
    assert after['runs_today'] == 0 and after['estimated_spent_today_usd'] == '0'
    assert Decimal(after['estimated_spent_total_usd']) == Decimal('.02')
    assert security.daily_tokens(settings, store, clock=clock) == 0


@pytest.mark.parametrize('scope', ['total', 'daily'])
def test_settled_spend_plus_new_reservation_cannot_exceed_limit(settings, store, clock, scope):
    settings = replace(settings, budget_total_usd=Decimal('.34') if scope == 'total' else Decimal('5'),
                       budget_daily_usd=Decimal('.34'))
    fake = FakeHermes()
    stages = adapter.HermesStages(store, settings, lambda: fake, clock=clock)
    execute(stages, context(store, 'settled', clock.current.isoformat()))
    limits = report(settings, store, clock)
    assert limits['reserved_usd'] == '0' and Decimal(limits['estimated_spent_total_usd']) == Decimal('.02')
    with pytest.raises(adapter.CognitiveFailure, match='Saldo total' if scope == 'total' else 'Saldo diário'):
        execute(stages, context(store, 'over', clock.current.isoformat()))
    assert len(fake.requests) == 1


def test_open_and_unknown_reservations_cross_utc_midnight(settings, store, clock, pg_app_cluster, pg_app_database):
    fake = FakeHermes({})
    execute(adapter.HermesStages(store, settings, lambda: fake, clock=clock), context(store, 'old', clock.current.isoformat()))
    with admin(pg_app_cluster, pg_app_database) as conn:
        conn.execute("UPDATE horus.cognitive_runs SET status='unknown'")
    clock.current = datetime(2026, 11, 13, tzinfo=timezone.utc)
    limits = report(settings, store, clock)
    assert limits['reserved_usd'] == '0.32768' and limits['runs_today'] == 0
    assert security.daily_tokens(settings, store, clock=clock) == settings.max_tokens_per_run
    settings = replace(settings, budget_daily_usd=Decimal('.5'))
    with pytest.raises(adapter.CognitiveFailure, match='Saldo diário'):
        execute(adapter.HermesStages(store, settings, lambda: fake, clock=clock),
                context(store, 'new', clock.current.isoformat()))


def test_midnight_during_admission_keeps_same_utc_day_for_all_checks(settings, store, clock, monkeypatch):
    settings = replace(settings, budget_daily_usd=Decimal('.34'))
    fake = FakeHermes()
    stages = adapter.HermesStages(store, settings, lambda: fake, clock=clock)
    execute(stages, context(store, 'old', clock.current.isoformat()))
    second = context(store, 'new', clock.current.isoformat())

    def midnight_after_lock(store, db, *, day=None, clock=None):
        # Sob a transação de admissão, o dia UTC já foi fixado uma única vez.
        assert db.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
        assert day.date().isoformat() == '2026-11-12'
        FAKE_CLOCK.current = datetime(2026, 11, 13, tzinfo=timezone.utc)
        return security.daily_runs(store, db, day=day, clock=clock)
    FAKE_CLOCK = clock
    monkeypatch.setattr(adapter, 'daily_runs', midnight_after_lock)
    with pytest.raises(adapter.CognitiveFailure, match='Saldo diário'):
        execute(stages, second)
    assert len(fake.requests) == 1


def test_simulation_ignores_all_limits_prices_and_accounting(settings, store, clock):
    fake = FakeHermes()
    invalid = replace(settings, max_tokens_per_run=None, max_tokens_per_day=None, max_runs_per_day=None,
                      budget_total_usd=None, budget_daily_usd=None,
                      price_input_usd_per_mtok=None, price_output_usd_per_mtok=None)
    output = execute(adapter.HermesStages(store, invalid, lambda: fake, clock=clock),
                     context(store, 'simulation', clock.current.isoformat(), simulated=True))
    assert output['provider'] == 'hermes_simulated'
    limits = report(settings, store, clock)
    assert all(limits[name] == '0' for name in ('reserved_usd', 'estimated_spent_total_usd', 'estimated_spent_today_usd'))
    assert limits['runs_today'] == 0 and security.daily_tokens(settings, store, clock=clock) == 0
    assert 'horus_cost_reservation_usd' not in fake.requests[0][0]['metadata']


@pytest.mark.parametrize('scope', ['total', 'daily'])
def test_concurrent_admissions_reserve_under_global_budget_lock(settings, store, clock, monkeypatch, scope):
    # Antes: lock do SQLite; agora: advisory lock global de orçamento (acquire_budget_lock).
    settings = replace(settings, budget_total_usd=Decimal('.5') if scope == 'total' else Decimal('5'),
                       budget_daily_usd=Decimal('.5'))
    fake = FakeHermes({})
    contexts = [context(store, str(i), clock.current.isoformat()) for i in range(2)]
    stages = adapter.HermesStages(store, settings, lambda: fake, clock=clock)
    barrier = threading.Barrier(2)
    original_secure = adapter.require_secure

    def both_pass_preflight(*args, **kwargs):
        original_secure(*args, **kwargs)
        barrier.wait(timeout=5)
    monkeypatch.setattr(adapter, 'require_secure', both_pass_preflight)

    def run(ctx):
        try:
            execute(stages, ctx)
            return 'allowed'
        except adapter.CognitiveFailure as error:
            assert 'Saldo total' in str(error) if scope == 'total' else 'Saldo diário' in str(error)
            return 'denied'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, contexts))
    assert sorted(results) == ['allowed', 'denied'] and len(fake.requests) == 1
    assert report(settings, store, clock)['reserved_usd'] == '0.32768'


def wait_workflow(client, workflow_id, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        workflow = client.get('/v1/workflows/' + workflow_id).json()
        if predicate(workflow):
            return workflow
        time.sleep(.01)
    pytest.fail('workflow não atingiu condição no prazo')


def authorize(client, workflow, key):
    return client.post('/v1/workflows/' + workflow['id'] + '/authorize', headers={'Idempotency-Key': key},
                       json={'expected_revision': workflow['revision'], 'job_id': workflow['pending_authorization']['job_id']})


def test_concurrent_human_authorizations_and_status_endpoint(pg_settings):
    settings = replace(pg_settings, budget_daily_usd=Decimal('.5'))
    app, fake = create_app(settings), FakeHermes({}, hold=True)
    app.state.manager.workflows.hermes.client_factory = lambda: fake
    with TestClient(app, base_url='http://127.0.0.1', headers=HEADERS) as client:
        workflows = []
        for i in range(2):
            created = client.post('/v1/workflows', json=REQUEST, headers={'Idempotency-Key': 'create-' + str(i)}).json()
            workflows.append(wait_workflow(client, created['id'], lambda w: w['pending_authorization'] is not None))
        barrier = threading.Barrier(2)

        def grant(i):
            barrier.wait(timeout=5)
            return authorize(client, workflows[i], 'grant-' + str(i))
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(grant, range(2)))
        assert all(r.status_code in {200, 409} for r in responses)
        assert any(r.status_code == 200 for r in responses)
        # O worker é sequencial: liberar o primeiro permite verificar uma segunda autorização já aceita.
        deadline = time.monotonic() + 5
        while not fake.requests and time.monotonic() < deadline:
            time.sleep(.01)
        fake.hold = False
        for workflow, response in zip(workflows, responses):
            if response.status_code == 200:
                wait_workflow(client, workflow['id'], lambda w: w['status'] in {'awaiting_approval', 'failed'})
            else:
                assert response.json()['detail']['code'] == 'security_gate'
        assert len(fake.requests) == 1
        status = client.get('/v1/security/status').json()
        limits = status['hermes']['cost_limits']
        assert limits == {'configured': True, 'max_tokens_per_run': 16384, 'max_tokens_per_day': 32768,
            'max_runs_per_day': 2, 'runs_configured': True, 'budget_total_usd': '5', 'budget_daily_usd': '0.5',
            'budget_configured': True, 'pricing_configured': True, 'estimated_spent_total_usd': '0',
            'estimated_spent_today_usd': '0', 'reserved_usd': '0.32768', 'runs_today': 1,
            'reservation_per_run_usd': '0.32768'}
        assert 'daily_budget_limit' in {b['code'] for b in status['blockers']}


def test_legacy_unverifiable_cost_blocks_without_schema_migration(settings, store, clock, pg_app_cluster, pg_app_database):
    fake = FakeHermes()
    execute(adapter.HermesStages(store, settings, lambda: fake, clock=clock), context(store, 'old', clock.current.isoformat()))
    with admin(pg_app_cluster, pg_app_database) as conn:
        # request e request_canonical têm de permanecer coerentes (CHECK do schema): só a reserva some.
        conn.execute("UPDATE horus.cognitive_runs SET request='{}'::jsonb, request_canonical='{}'::bytea")
    status = security.security_status(settings, store, clock=clock)
    assert 'cost_usage_unverifiable' in {b['code'] for b in status['blockers']}
    assert status['hermes']['cost_limits']['reserved_usd'] is None
    with pytest.raises(adapter.CognitiveFailure, match='não verificável'):
        execute(adapter.HermesStages(store, settings, lambda: fake, clock=clock),
                context(store, 'new', clock.current.isoformat()))
    assert len(fake.requests) == 1

def test_job_request_with_nul_is_refused_without_echo():
    # D5: NUL não pode chegar ao jsonb; recusa na borda do modelo, sem eco do valor.
    from pydantic import ValidationError
    from horus_backend.jobs import DownloadRequest, TranscriptionRequest
    with pytest.raises(ValidationError) as caught:
        TranscriptionRequest(operation='transcription.run', path='/x/a' + chr(0) + 'segredo.mp4')
    assert 'segredo' not in str(caught.value.errors(include_input=False))
    with pytest.raises(ValidationError):
        DownloadRequest(operation='youtube.download', url='https://youtu.be/abcdefghijk', directory='/x' + chr(0))
