"""HOR-120 / 7.4: rotação do token local em duas fases (validação + reversão), em HORUS_DATA_DIR temporário e com tokens sintéticos."""
import secrets
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from horus_backend import local_token
from horus_backend.config import Settings
from horus_backend.local_token import TokenState
from support_e import pg_create_app, pg_transport  # noqa: F401

OLD = secrets.token_urlsafe(32)
NEW = secrets.token_urlsafe(32)
ROTATION = '/v1/security/token/rotation'


def auth(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def client(tmp_path, pg_transport):
    settings = Settings(token=OLD, data_dir=tmp_path / 'dados', factory_root=None, hermes_executable=None, hermes_config=tmp_path / 'h.json')
    assert Path(settings.data_dir).is_relative_to(tmp_path)
    with TestClient(pg_create_app(settings), base_url='http://127.0.0.1') as client:
        yield client


def status(client, token):
    return client.get('/v1/jobs', headers=auth(token)).status_code


def begin(client, token=OLD, new=NEW):
    return client.post(ROTATION, json={'new_token': new}, headers=auth(token))


def test_happy_path_both_tokens_during_the_window_then_only_the_new_one(client):
    started = begin(client)
    assert started.status_code == 202 and started.json()['state'] == 'pending'
    rid = started.json()['rotation_id']
    assert status(client, OLD) == 200 and status(client, NEW) == 200            # janela: os dois valem
    assert client.get(ROTATION, headers=auth(OLD)).json()['state'] == 'pending'
    committed = client.post(f'{ROTATION}/{rid}/commit', headers=auth(NEW))
    assert committed.status_code == 200 and committed.json() == {'state': 'committed'}
    assert status(client, NEW) == 200 and status(client, OLD) == 401           # o antigo deixou de valer
    assert client.get(ROTATION, headers=auth(NEW)).json() == {'state': 'idle', 'expires_in_seconds': None}


def test_commit_must_use_the_new_token_otherwise_nothing_changes(client):
    rid = begin(client).json()['rotation_id']
    refused = client.post(f'{ROTATION}/{rid}/commit', headers=auth(OLD))
    assert refused.status_code == 403
    assert status(client, OLD) == 200 and status(client, NEW) == 200            # ainda pendente
    assert client.post(f'{ROTATION}/{rid}/commit', headers=auth(NEW)).status_code == 200


def test_unauthenticated_and_unknown_requests(client):
    assert client.post(ROTATION, json={'new_token': NEW}).status_code == 401
    assert client.get(ROTATION).status_code == 401
    assert client.post(f'{ROTATION}/{"0" * 32}/commit', headers=auth(OLD)).status_code == 404
    assert client.post(f'{ROTATION}/{"0" * 32}/rollback', headers=auth(OLD)).status_code == 404
    rid = begin(client).json()['rotation_id']
    assert client.post(f'{ROTATION}/{"0" * 32}/commit', headers=auth(NEW)).status_code == 404
    assert client.post(f'{ROTATION}/{rid}/commit', headers={'Authorization': 'Bearer ' + 'x' * 40}).status_code == 401


def test_explicit_rollback_with_either_token_keeps_the_old_token(client):
    for who in (OLD, NEW):
        rid = begin(client).json()['rotation_id']
        assert client.post(f'{ROTATION}/{rid}/rollback', headers=auth(who)).json() == {'state': 'rolled_back'}
        assert status(client, OLD) == 200 and status(client, NEW) == 401
        assert client.post(f'{ROTATION}/{rid}/commit', headers=auth(OLD)).status_code == 404   # id encerrado


def test_unconfirmed_rotation_expires_by_itself_and_the_old_token_survives(client):
    manager = client.app.state.manager
    clock = {'now': 1000.0}
    manager.tokens = TokenState(OLD, clock=lambda: clock['now'], window=60)
    rid = begin(client).json()['rotation_id']
    clock['now'] += 59
    assert status(client, NEW) == 200
    clock['now'] += 2                                                           # passou da janela: reversão automática
    assert status(client, NEW) == 401 and status(client, OLD) == 200
    assert client.post(f'{ROTATION}/{rid}/commit', headers=auth(NEW)).status_code == 401
    assert client.get(ROTATION, headers=auth(OLD)).json()['state'] == 'idle'
    assert begin(client).status_code == 202                                       # pode tentar de novo


@pytest.mark.parametrize('new', ['curto', 'a' * 300, 'com espaço ' * 4, 'ação-com-acento-' + 'x' * 20, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaa', OLD, 123, None, ['x']])
def test_invalid_new_tokens_are_refused_without_echoing_them(client, new):
    response = client.post(ROTATION, json={'new_token': new}, headers=auth(OLD))
    assert response.status_code == 422
    assert isinstance(new, str) is False or new not in response.text
    assert status(client, OLD) == 200 and client.get(ROTATION, headers=auth(OLD)).json()['state'] == 'idle'


def test_extra_fields_are_refused(client):
    assert client.post(ROTATION, json={'new_token': NEW, 'force': True}, headers=auth(OLD)).status_code == 422


def test_second_rotation_while_pending_is_refused_and_concurrent_begins_create_one(client):
    assert begin(client).status_code == 202
    assert begin(client, new=secrets.token_urlsafe(32)).status_code == 409
    client.app.state.manager.tokens = TokenState(OLD)
    results = []
    lock = threading.Lock()

    def attempt():
        code = TokenState.begin.__get__(client.app.state.manager.tokens)
        try:
            code(secrets.token_urlsafe(32))
            outcome = 'ok'
        except Exception as error:      # noqa: BLE001
            outcome = type(error).__name__
        with lock:
            results.append(outcome)
    threads = [threading.Thread(target=attempt) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count('ok') == 1 and results.count('WorkflowConflict') == 19


def test_media_tickets_follow_the_rotation(client, tmp_path):
    video = tmp_path / 'v.mp4'
    video.write_bytes(bytes(range(100)))
    item = client.post('/v1/media', json={'path': str(video)}, headers=auth(OLD)).json()
    rid = begin(client).json()['rotation_id']
    assert client.get(item['stream_path']).status_code == 200                    # ticket antigo vale na janela
    client.post(f'{ROTATION}/{rid}/commit', headers=auth(NEW))
    assert client.get(item['stream_path']).status_code == 401                    # ticket do token antigo deixou de valer
    fresh = client.get('/v1/media', headers=auth(NEW)).json()[0]
    assert client.get(fresh['stream_path']).status_code == 200                   # o novo ticket é assinado com o token novo


def test_tokens_are_never_written_to_disk_or_responses(client, tmp_path, caplog):
    caplog.set_level('DEBUG')
    started = begin(client)
    rid = started.json()['rotation_id']
    replies = [started.text, client.get(ROTATION, headers=auth(OLD)).text,
               client.post(f'{ROTATION}/{rid}/commit', headers=auth(NEW)).text, client.get(ROTATION, headers=auth(NEW)).text]
    blob = '\n'.join(replies) + caplog.text
    assert OLD not in blob and NEW not in blob
    for path in (tmp_path / 'dados').rglob('*'):
        if path.is_file() and not path.name.endswith('.lock'):
            raw = path.read_bytes()
            assert OLD.encode() not in raw and NEW.encode() not in raw, path.name


def test_export_redaction_covers_the_new_token_during_the_window(client):
    from horus_backend.exports import ExportService
    service = ExportService(client.app.state.manager.productions)
    assert service._tokens() == [OLD]
    begin(client)
    assert set(service._tokens()) == {OLD, NEW}


def test_validation_rules_are_pure_and_message_never_contains_the_value():
    for bad in ('curto', 'x' * 300, 'tem espaço' * 5, 'a' * 40, OLD):
        with pytest.raises(local_token.InvalidToken) as caught:
            local_token.validate_new_token(bad, OLD)
        assert bad not in str(caught.value) or bad == ''
    assert local_token.validate_new_token(NEW, OLD) == NEW
