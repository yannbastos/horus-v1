"""Contratos locais isolados: nenhum banco operacional, rede ou token real."""
from dataclasses import replace
from uuid import uuid4

import pytest

from horus_backend import persistence_contracts as contracts
from horus_backend.persistence_contracts import Change, Conflict, LocalJobRepository, LocalUnitOfWork, validate_verified_identity
from horus_backend.storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/'synthetic.sqlite3')


@pytest.fixture
def change():
    return Change(uuid4(),uuid4(),uuid4(),uuid4(),0,'workflow',{'text':'Synthetic editorial text'})


def test_disabled_default_does_not_touch_store(store):
    with pytest.raises(ValueError, match='disabled'):
        with LocalUnitOfWork(store).transaction():
            pass
    with store.connect() as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sync_%'").fetchall()


def test_local_job_adapter_preserves_current_contract(store):
    repo = LocalJobRepository(store)
    job, created = repo.submit({'operation':'factory.profile'},'synthetic')
    assert created and repo.get(job['id']) == store.get_job(job['id'])
    assert repo.list() == store.list_jobs()
    assert repo.submit({'operation':'factory.profile'},'synthetic')[1] is False


def test_outbox_atomic_with_local_write_and_rollback(store, change):
    uow = LocalUnitOfWork(store,enabled=True)
    with pytest.raises(RuntimeError):
        with uow.transaction() as journal:
            journal.db.execute("INSERT INTO media(id,path,name,size,modified_ns,created_at) VALUES('synthetic','synthetic','x',0,0,'x')")
            journal.enqueue(change)
            raise RuntimeError('crash before commit')
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM media WHERE id='synthetic'").fetchall()
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='sync_outbox'").fetchall()
    with uow.transaction() as journal:
        journal.enqueue(change)
    with uow.transaction() as journal:
        assert len(journal.pending(change.owner_id)) == 1
        assert journal.pending(uuid4()) == []
        journal.enqueue(change)
        with pytest.raises(Conflict):
            journal.enqueue(replace(change,payload={'text':'Changed'}))
        journal.ack(change.owner_id,change.operation_id,1)
        journal.ack(change.owner_id,change.operation_id,1)
        with pytest.raises(Conflict):
            journal.ack(change.owner_id,change.operation_id,2)
        assert journal.pending(change.owner_id) == []


def test_inbox_cursor_and_logout_are_scoped_and_atomic(store, change):
    uow = LocalUnitOfWork(store,enabled=True)
    other = replace(change,owner_id=uuid4(),operation_id=uuid4())
    with uow.transaction() as journal:
        journal.enqueue(change)
        journal.enqueue(other)
        journal.apply_inbox(change.owner_id,change.workspace_id,1,[change])
    with pytest.raises(RuntimeError):
        with uow.transaction() as journal:
            journal.apply_inbox(change.owner_id,change.workspace_id,2,[change])
            raise RuntimeError('crash')
    with uow.transaction() as journal:
        assert journal.db.execute('SELECT cursor FROM sync_cursors').fetchone()[0] == 1
        with pytest.raises(ValueError):
            journal.apply_inbox(change.owner_id,change.workspace_id,2,[other])
        with pytest.raises(Conflict):
            journal.apply_inbox(change.owner_id,change.workspace_id,1,[change])
        journal.logout(change.owner_id)
        assert journal.pending(change.owner_id) == []
        assert len(journal.pending(other.owner_id)) == 1
        assert journal.db.execute('SELECT count(*) FROM sync_inbox').fetchone()[0] == 0


@pytest.mark.parametrize('payload', [{'access_token':'synthetic'}, {'text':'Bearer synthetic-session'},
    {'text':'postgresql://synthetic.invalid'}, {'text':'sb_secret_synthetic'}, {'title':123},
    {'text':'x' * 33000}, {'approved':True}, {'historical_only':False}])
def test_secrets_oversized_or_unauthorized_payload_never_queued(store, change, payload):
    with LocalUnitOfWork(store,enabled=True).transaction() as journal:
        with pytest.raises(ValueError):
            journal.enqueue(replace(change,payload=payload))
        assert journal.pending(change.owner_id) == []


def test_queue_limit_preserves_existing_pending(store, change, monkeypatch):
    monkeypatch.setattr(contracts,'MAX_PENDING',1)
    with LocalUnitOfWork(store,enabled=True).transaction() as journal:
        journal.enqueue(change)
        with pytest.raises(ValueError,match='quota'):
            journal.enqueue(replace(change,operation_id=uuid4()))
        assert len(journal.pending(change.owner_id)) == 1


def test_remote_review_is_only_historical(change):
    with pytest.raises(ValueError):
        replace(change,kind='review_history').encoded()
    assert replace(change,kind='review_history',payload={'historical_only':True}).encoded()


def test_verified_identity_semantics_are_not_signature_validation():
    subject = uuid4()
    claims = {'sub':str(subject),'aud':'authenticated','role':'authenticated','exp':200,'iss':'synthetic-issuer'}
    assert validate_verified_identity(claims,algorithm='ES256',issuer='synthetic-issuer',now=100) == subject
    for field,value in [('sub','invalid'),('aud','wrong'),('role','service_role'),('exp',99),('iss','wrong')]:
        with pytest.raises(ValueError):
            validate_verified_identity({**claims,field:value},algorithm='ES256',issuer='synthetic-issuer',now=100)
    for algorithm in ('none','HS256','unknown'):
        with pytest.raises(ValueError):
            validate_verified_identity(claims,algorithm=algorithm,issuer='synthetic-issuer',now=100)
    with pytest.raises(ValueError):
        validate_verified_identity({},algorithm='ES256',issuer='synthetic-issuer',now=100)
