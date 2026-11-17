"""HOR-107: checkpoints humanos para gastar e publicar. Barreira no backend; servidor Hermes falso; sem rede real."""
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest

from horus_backend.human_checkpoints import (ACTIONS, DEFAULT_TTL_SECONDS, MAX_TTL_SECONDS,
    CheckpointRefused, CheckpointService, require_publication_checkpoint)
from horus_backend.security import SecurityGate
from horus_backend.transmissions import TransmissionLog
from support_b import (
    HERMES_TOKEN as TOKEN,  # noqa: F401
    Scenario,
    VALID_SCRIPT_OUTPUT,
    approved_hermes_workflow as approved,
    authorize_workflow as authorize,
    client_for,  # noqa: F401
    create_hermes_workflow as create,
    echo,
    generate_script,
    hermes,  # noqa: F401
    hermes_settings as settings,  # noqa: F401
    pending_workflow as pending,
    pg_store,  # noqa: F401
    query_rows,
    wait_workflow as wait,
)

SUBJECT = hashlib.sha256(b"objeto").hexdigest()
OTHER = hashlib.sha256(b"outro").hexdigest()


class Clock:
    def __init__(self, initial=None):
        self.now = initial or datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def env(pg_store):
    clock = Clock()
    log = TransmissionLog(pg_store)
    return SimpleNamespace(store=pg_store, clock=clock, log=log, service=CheckpointService(pg_store, log, clock))


def rows(store, table="human_checkpoints"):
    return query_rows(store, f"SELECT * FROM horus.{table} ORDER BY seq")


def grant(service, **over):
    args = dict(action="paid_narration", subject=SUBJECT, description="Narração paga de 800 caracteres", limit_amount="1.50",
                currency="USD", ttl_seconds=600)
    return service.grant(**(args | over))


# --- concessão: quem, quando, o quê, limite -----------------------------------------------------------------

def test_grant_records_who_when_what_limit_and_expiry(env):
    t0 = env.clock.now
    record = grant(env.service, key="k1")
    assert (record["approved_by"], record["operator_id"], record["action"], record["subject"]) == ("human", "local-operator", "paid_narration", SUBJECT)
    assert record["approved_at"] == t0.isoformat() and record["expires_at"] == (t0 + timedelta(seconds=600)).isoformat()
    assert (record["limit_amount"], record["currency"], record["consumed_at"]) == ("1.50", "USD", None)
    assert len(rows(env.store)) == 1


@pytest.mark.parametrize("override", [
    {"action": "deploy"}, {"action": "PUBLISH"}, {"subject": "abc"}, {"subject": SUBJECT.upper()}, {"subject": None},
    {"approved_by": "agent"}, {"approved_by": "system"}, {"description": ""}, {"description": "x" * 201}, {"description": "a\nb"},
    {"ttl_seconds": 29}, {"ttl_seconds": 3601}, {"ttl_seconds": "600"}, {"ttl_seconds": True},
    {"limit_amount": None}, {"currency": None},                       # limite sem moeda / moeda sem limite
    {"limit_amount": "0", "currency": "USD"}, {"limit_amount": "-1", "currency": "USD"}, {"limit_amount": "1e3", "currency": "USD"},
    {"limit_amount": "NaN", "currency": "USD"}, {"limit_amount": 1.5, "currency": "USD"}, {"limit_amount": "1", "currency": "usd"},
    {"limit_amount": "1", "currency": "US"},
])
def test_invalid_grants_are_refused_and_store_nothing(env, override):
    with pytest.raises(ValueError):
        grant(env.service, **override)
    assert rows(env.store) == []


def test_grant_is_idempotent_and_key_cannot_be_reused_for_another_request(env):
    first = grant(env.service, key="k")
    assert grant(env.service, key="k")["id"] == first["id"] and len(rows(env.store)) == 1
    with pytest.raises(ValueError):
        grant(env.service, key="k", limit_amount="9.99")
    assert len(rows(env.store)) == 1


@pytest.mark.parametrize("action", ["hermes_run", "hermes_script"])
@pytest.mark.parametrize("transactional", [False, True])
def test_backend_ceiling_refuses_larger_grants_before_insert(env, settings, action, transactional):
    service = CheckpointService(env.store, env.log, env.clock, settings=settings)
    kwargs = dict(action=action, limit_amount="0.20000001")  # reserva configurada = 0.2 USD
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
    record = grant(service, action=action, limit_amount="9.99")  # não usa o orçamento exclusivo do Hermes
    assert record["limit_amount"] == "9.99"
    with pytest.raises(CheckpointRefused) as error:
        service.consume(action=action, subject=SUBJECT, amount=Decimal("10"), currency="USD", ref="blocked")
    assert error.value.code == "checkpoint_limit_exceeded"


# --- a barreira: sem aprovação, expirada, acima do limite, replay --------------------------------------------

def refusal(env, **kw):
    with pytest.raises(CheckpointRefused) as error:
        env.service.consume(**({"action": "paid_narration", "subject": SUBJECT, "amount": Decimal("1.00"), "currency": "USD", "ref": "r"} | kw))
    assert isinstance(error.value, SecurityGate)           # o HTTP já traduz SecurityGate em 409 security_gate
    return error.value.code


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


def test_expired_approval_is_refused(env):
    grant(env.service, ttl_seconds=60)
    env.clock.advance(seconds=59)
    env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1"), currency="USD", ref="ok")  # ainda vigente
    grant(env.service, ttl_seconds=60, description="segunda")
    env.clock.advance(seconds=61)
    assert refusal(env) == "checkpoint_expired"
    assert [r["consumed_at"] is not None for r in rows(env.store)] == [True, False]


@pytest.mark.parametrize("amount,currency", [(Decimal("1.5000001"), "USD"), (Decimal("1000"), "USD"), (Decimal("0.01"), "EUR"), (Decimal("1"), None)])
def test_amount_above_limit_or_other_currency_is_refused_and_not_consumed(env, amount, currency):
    grant(env.service)
    assert refusal(env, amount=amount, currency=currency) == "checkpoint_limit_exceeded"
    assert rows(env.store)[0]["consumed_at"] is None            # recusa não gasta a aprovação
    env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1.50"), currency="USD", ref="ok")   # no limite exato passa


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


def test_concurrent_attempts_consume_the_approval_only_once(env):
    grant(env.service)
    outcomes, barrier = [], threading.Barrier(8)

    def attempt(i):
        barrier.wait()
        try:
            env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1"), currency="USD", ref=f"t{i}")
            outcomes.append("ok")
        except CheckpointRefused as error:
            outcomes.append(error.code)
        except psycopg.OperationalError:
            outcomes.append("busy")
    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("ok") == 1 and set(outcomes) <= {"ok", "checkpoint_consumed", "busy"}
    assert sum(r["consumed_at"] is not None for r in rows(env.store)) == 1


def test_publication_barrier_requires_a_human_checkpoint_per_object(env):
    with pytest.raises(CheckpointRefused):
        require_publication_checkpoint(env.service, SUBJECT, ref="youtube")
    grant(env.service, action="publish", limit_amount=None, currency=None, description="Publicar vídeo aprovado")
    assert require_publication_checkpoint(env.service, SUBJECT, ref="youtube")["consumed_ref"] == "youtube"
    with pytest.raises(CheckpointRefused):
        require_publication_checkpoint(env.service, SUBJECT, ref="youtube")


# --- append-only no banco ---------------------------------------------------------------------------------------

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
    for statement in (
        "UPDATE horus.human_checkpoints SET limit_amount = 999",
        "UPDATE horus.human_checkpoints SET expires_at = '2099-01-01T00:00:00+00:00'",
        "UPDATE horus.human_checkpoints SET approved_by = 'human', operator_id = 'alguem'",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with env.store.uow() as conn:
                conn.execute(statement)
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation, psycopg.errors.IntegrityError)):
        with env.store.uow() as conn:
            conn.execute(
                """INSERT INTO horus.human_checkpoints(id, action, subject, description, approved_by, operator_id,
                   approved_at, expires_at, request, request_canonical)
                   VALUES ('x', 'publish', %s, 'd', 'agent', 'op', clock_timestamp(), clock_timestamp(), '{}'::jsonb, '{}'::bytea)""",
                (SUBJECT,),
            )
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation, psycopg.errors.IntegrityError)):
        with env.store.uow() as conn:
            conn.execute(
                """INSERT INTO horus.human_checkpoints(id, action, subject, description, approved_by, operator_id,
                   approved_at, expires_at, limit_amount, request, request_canonical)
                   VALUES ('y', 'publish', %s, 'd', 'human', 'op', clock_timestamp(), clock_timestamp(), 1, '{}'::jsonb, '{}'::bytea)""",
                (SUBJECT,),
            )


def test_cannot_edit_an_unconsumed_checkpoint_to_widen_it(env):
    grant(env.service)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with env.store.uow() as conn:
            conn.execute("UPDATE horus.human_checkpoints SET limit_amount = 1000000")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with env.store.uow() as conn:
            conn.execute("UPDATE horus.human_checkpoints SET subject = %s", (OTHER,))


# --- auditoria em transmissions -----------------------------------------------------------------------------------

def test_refusals_are_audited_without_content_and_success_does_not_duplicate_the_ledger(env):
    with pytest.raises(CheckpointRefused):
        env.service.consume(action="publish", subject=SUBJECT, ref="segredo-sk-" + "a" * 40)
    grant(env.service)
    refusal(env, amount=Decimal("99"))
    env.service.consume(action="paid_narration", subject=SUBJECT, amount=Decimal("1"), currency="USD", ref="ok")
    ledger = rows(env.store, "transmissions")
    assert [(r["kind"], r["stage"], r["result"], r["detail"]) for r in ledger] == [
        ("gate_refusal", "checkpoint:publish", "refused_gate", "checkpoint_missing"),
        ("gate_refusal", "checkpoint:paid_narration", "refused_gate", "checkpoint_limit_exceeded")]
    assert "segredo" not in json.dumps(ledger, default=str)


# --- API HTTP ---------------------------------------------------------------------------------------------------

def post(client, payload, key="k1"):
    return client.post("/v1/checkpoints", json=payload, headers={"Idempotency-Key": key})


BODY = {"action": "paid_narration", "subject": SUBJECT, "description": "Narração paga", "limit_amount": "1.50", "currency": "USD",
        "ttl_seconds": 600, "approved_by": "human"}


def test_api_creates_lists_and_is_idempotent(settings, client_for):
    with client_for(settings) as client:
        first = post(client, BODY)
        assert first.status_code == 201 and first.json()["approved_by"] == "human" and first.json()["consumed_at"] is None
        assert post(client, BODY).json()["id"] == first.json()["id"]
        assert post(client, BODY | {"limit_amount": "9"}).status_code == 422          # mesma chave, outro pedido
        listing = client.get("/v1/checkpoints").json()
        assert [i["id"] for i in listing["items"]] == [first.json()["id"]] and listing["next_after"] is None


def test_api_cannot_expand_ttl_or_grant_cognitive_spending(settings, client_for):
    with client_for(settings) as client:
        assert post(client, BODY | {"ttl_seconds": MAX_TTL_SECONDS + 1}).status_code == 422
        assert post(client, BODY | {"action": "hermes_run", "limit_amount": "999"}).status_code == 422
        assert client.get("/v1/checkpoints").json()["items"] == []
        record = post(client, {k: v for k, v in BODY.items() if k != "ttl_seconds"}).json()
        assert (datetime.fromisoformat(record["expires_at"]) - datetime.fromisoformat(record["approved_at"])).total_seconds() == DEFAULT_TTL_SECONDS


@pytest.mark.parametrize("change", [
    {"action": "hermes_run"}, {"action": "hermes_script"}, {"action": "deploy"},        # gasto cognitivo só nasce em /authorize
    {"approved_by": "agent"}, {"approved_by": None}, {"subject": "curto"}, {"ttl_seconds": 5}, {"ttl_seconds": 99999},
    {"limit_amount": "1e3"}, {"currency": "usd"}, {"extra": "campo"}, {"description": ""},
])
def test_api_rejects_invalid_or_non_human_approvals(settings, client_for, change):
    with client_for(settings) as client:
        assert post(client, BODY | change).status_code == 422
        assert client.get("/v1/checkpoints").json()["items"] == []


def test_api_requires_idempotency_key_and_authentication(settings, client_for):
    with client_for(settings) as client:
        assert client.post("/v1/checkpoints", json=BODY).status_code == 422
        client.headers.pop("Authorization")
        assert client.post("/v1/checkpoints", json=BODY, headers={"Idempotency-Key": "k"}).status_code == 401
        assert client.get("/v1/checkpoints").status_code == 401


# --- integração: execução cognitiva paga (Hermes) ----------------------------------------------------------------

def checkpoints(client):
    return query_rows(client.app.state.store, "SELECT * FROM horus.human_checkpoints ORDER BY seq")


def test_authorizing_a_hermes_run_registers_and_consumes_a_bounded_checkpoint(settings, hermes, client_for):
    hermes.scenarios.append(Scenario(output=echo()))
    with client_for(settings) as client:
        workflow = authorize(client, pending(client, create(client)["id"]))
        workflow = wait(client, workflow["id"], lambda w: w["status"] == "awaiting_approval")
        [record] = checkpoints(client)
        attempt = workflow["attempts"][2]["id"]
        assert (record["action"], record["approved_by"], record["operator_id"]) == ("hermes_run", "human", "local-operator")
        assert str(record["limit_amount"]).rstrip("0").rstrip(".") == "0.2" and record["currency"] == "USD"          # 100000 tokens × US$ 2/Mtok
        assert record["consumed_ref"] == attempt and record["consumed_at"] and record["expires_at"] > record["approved_at"]
        assert len(hermes.requests) == 1


def test_expired_hermes_checkpoint_blocks_the_run_before_any_submission(settings, hermes, client_for, monkeypatch):
    hermes.scenarios.append(Scenario(output=echo()))
    with client_for(settings) as client:
        service = client.app.state.manager.workflows.checkpoints
        calls = {"n": 0}

        t0 = datetime.now(timezone.utc)
        def clock():
            calls["n"] += 1
            return t0 if calls["n"] == 1 else t0 + timedelta(hours=2)          # 1ª chamada = concessão; depois, 2 h adiante
        service.clock = clock
        workflow = authorize(client, pending(client, create(client)["id"]))
        time_to_settle = wait(client, workflow["id"], lambda w: w["status"] in {"failed", "blocked", "awaiting_approval", "interrupted"})
        assert time_to_settle["status"] != "awaiting_approval"
        assert hermes.requests == []
        assert [r["consumed_at"] for r in checkpoints(client)] == [None]
        with client.app.state.store.uow(readonly=True) as conn:
            details = [r[0] for r in conn.execute("SELECT detail FROM horus.transmissions WHERE stage = 'checkpoint:hermes_run'").fetchall()]
        assert details == ["checkpoint_expired"]


def test_price_increase_after_approval_exceeds_the_approved_limit(settings, hermes, client_for):
    hermes.scenarios.append(Scenario(output=echo()))
    with client_for(settings) as client:
        service = client.app.state.manager.workflows.checkpoints
        state = {"n": 0}

        t0 = datetime.now(timezone.utc)
        def clock():
            state["n"] += 1
            if state["n"] == 1:                                              # concessão feita; depois o preço sobe 100×
                object.__setattr__(settings, "price_output_usd_per_mtok", Decimal("200"))
            return t0
        service.clock = clock
        workflow = authorize(client, pending(client, create(client)["id"]))
        settled = wait(client, workflow["id"], lambda w: w["status"] in {"failed", "blocked", "awaiting_approval", "interrupted"})
        assert settled["status"] != "awaiting_approval" and hermes.requests == []
        with client.app.state.store.uow(readonly=True) as conn:
            assert [r[0] for r in conn.execute("SELECT detail FROM horus.transmissions WHERE stage = 'checkpoint:hermes_run'").fetchall()] == ["checkpoint_limit_exceeded"]


def test_attempt_without_a_checkpoint_is_refused_even_with_an_authorization_row(settings, hermes, client_for):
    """Barreira independente da UI: autorização antiga sem checkpoint (ex.: criada por outro caminho) não gasta."""
    hermes.scenarios.append(Scenario(output=echo()))
    with client_for(settings) as client:
        manager = client.app.state.manager
        manager.workflows.checkpoints.grant = lambda **kw: {}                     # nenhuma concessão é gravada
        workflow = authorize(client, pending(client, create(client)["id"]))
        settled = wait(client, workflow["id"], lambda w: w["status"] in {"failed", "blocked", "awaiting_approval", "interrupted"})
        assert settled["status"] != "awaiting_approval" and hermes.requests == []
        assert checkpoints(client) == []


# --- integração: roteiro via Hermes e narração paga ----------------------------------------------------------------

def test_hermes_script_stage_registers_a_consumed_checkpoint(settings, hermes, client_for):
    with client_for(settings) as client:
        workflow = approved(client, hermes)
        hermes.scenarios.append(Scenario(output=echo(VALID_SCRIPT_OUTPUT)))
        assert generate_script(client, workflow, key="k1", source="hermes", authorize=True).status_code == 201
        records = [c for c in checkpoints(client) if c["action"] == "hermes_script"]
        assert len(records) == 1 and records[0]["consumed_at"] and records[0]["approved_by"] == "human"


def paid_provider(tmp_path, checkpoints_service, enabled=True):
    from horus_backend.config import Settings
    from horus_backend.narration.providers import ElevenLabs

    class Transport:
        calls = 0

        def synthesize(self, **kwargs):
            Transport.calls += 1
            kwargs["output"].write_bytes(b"RIFFfake")
            return {"credits": 1}
    settings = Settings(token="x", narration_paid_enabled=enabled, elevenlabs_price_per_1k_chars=Decimal("0.30"), elevenlabs_currency="usd")
    provider = ElevenLabs(settings, SimpleNamespace(get=lambda: "synthetic-credential"), Transport(), checkpoints=checkpoints_service)
    text = "hello" * 100                                                      # 500 caracteres × US$ 0,30/1k = US$ 0,15
    authorization = {"text_sha256": hashlib.sha256(text.encode()).hexdigest(), "voice_id": "mock",
                     "estimated_cost": provider.estimate(text)["cost"], "confirmed": True}
    return provider, Transport, text, authorization


def test_paid_narration_needs_a_matching_human_checkpoint_each_time(env, tmp_path):
    provider, transport, text, authorization = paid_provider(tmp_path, env.service)
    out = tmp_path / "a.wav"
    from horus_backend.narration.providers import NarrationError
    with pytest.raises(NarrationError) as error:                              # sem checkpoint
        provider.synthesize(text, "mock", out, authorization)
    assert error.value.code == "paid_provider_not_authorized" and transport.calls == 0
    grant(env.service, subject=authorization["text_sha256"], limit_amount="0.10", currency="USD")      # limite abaixo do custo
    with pytest.raises(NarrationError):
        provider.synthesize(text, "mock", out, authorization)
    grant(env.service, subject=authorization["text_sha256"], limit_amount="0.15", currency="USD", description="segunda aprovação")
    assert provider.synthesize(text, "mock", out, authorization)["publishable"] is False and transport.calls == 1
    with pytest.raises(NarrationError):                                       # replay: uso único
        provider.synthesize(text, "mock", out, authorization)
    assert transport.calls == 1


def test_paid_narration_without_a_checkpoint_service_or_with_unknown_cost_never_reaches_the_provider(env, tmp_path):
    from horus_backend.narration.providers import NarrationError
    provider, transport, text, authorization = paid_provider(tmp_path, None)
    with pytest.raises(NarrationError):
        provider.synthesize(text, "mock", tmp_path / "a.wav", authorization)
    provider, transport, text, authorization = paid_provider(tmp_path, env.service)
    authorization = authorization | {"estimated_cost": {"amount": None, "currency": None, "basis": "characters", "source": "unknown"}}
    grant(env.service, subject=authorization["text_sha256"])
    with pytest.raises(NarrationError):
        provider.synthesize(text, "mock", tmp_path / "a.wav", authorization)
    assert transport.calls == 0


def test_paid_narration_disabled_provider_ignores_even_a_valid_checkpoint(env, tmp_path):
    from horus_backend.narration.providers import NarrationError
    provider, transport, text, authorization = paid_provider(tmp_path, env.service, enabled=False)
    grant(env.service, subject=authorization["text_sha256"], limit_amount="5", currency="USD")
    with pytest.raises(NarrationError):
        provider.synthesize(text, "mock", tmp_path / "a.wav", authorization)
    assert transport.calls == 0 and rows(env.store)[0]["consumed_at"] is None
