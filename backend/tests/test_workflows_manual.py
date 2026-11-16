"""Criação humana sem executor: persistência, auditoria e gates reais do roteiro."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from test_workflows import settings, client_for
from test_scripts import decide
from test_scripts_manual import manual, version


def create(client, payload=None, key="manual-workflow"):
    return client.post('/v1/workflows/manual', json=payload or {'title': 'Meu vídeo'},
                       headers={'Idempotency-Key': key})


def test_human_workflow_scripts_versions_and_restart(settings):
    # Até configuração Hermes não muda esta ação humana para inferência.
    settings = replace(settings, workflow_executor='hermes')
    with client_for(settings) as client:
        response = create(client)
        assert response.status_code == 201, response.text
        record = response.json()
        workflow = client.get('/v1/workflows/' + record['workflow_id']).json()
        assert workflow['status'] == record['status'] == 'approved'
        assert workflow['executor'] == 'human' and workflow['simulated'] is False
        assert workflow['current_artifact_id'] == record['proposal_artifact_id']
        assert workflow['artifacts'][0]['content']['origin'] == 'human'
        assert workflow['approvals'][0]['operator_id'] == 'local-operator'
        assert workflow['attempts'][0]['executor'] == 'human'
        assert workflow['cognitive_runs'] == [] and workflow['authorizations'] == []
        assert all(j['status'] == 'succeeded' for j in workflow['steps'])
        first = manual(client, workflow).json()
        assert first['status'] == 'awaiting_review' and first['origin'] == 'human'
        assert decide(client, first, 'request_changes', feedback='Revisar').status_code == 200
        second = version(client, first).json()
        assert second['version'] == 2
        assert decide(client, second, key='approve-v2').status_code == 200
        assert create(client).status_code == 200 and create(client).json() == record
        assert create(client, {'title': 'Outro'}).status_code == 409
        assert client.get('/v1/workflows/' + record['workflow_id'] + '/scripts').json()['items'][0]['status'] == 'approved'
        with client.app.state.store.connect() as db:
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    with client_for(settings) as client:
        assert create(client).json() == record
        assert client.get('/v1/scripts/' + second['id']).json()['status'] == 'approved'


@pytest.mark.parametrize('payload', [{'title': ''}, {'title': '  '}, {'title': 'x' * 201},
    {'title': 'ok', 'objective': 'x' * 2001}, {'title': 'ok', 'executor': 'hermes'}, {'title': 12}])
def test_invalid(settings, payload):
    with client_for(settings) as client:
        assert create(client, payload).status_code == 422
        assert client.get('/v1/workflows').json() == []


def test_auth_key_and_concurrency(settings):
    with client_for(settings) as client:
        assert client.post('/v1/workflows/manual', json={'title': 'ok'}).status_code == 422
        client.headers.pop('Authorization')
        assert create(client).status_code == 401
    with client_for(settings) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: create(client), range(4)))
        assert sorted(r.status_code for r in responses) == [200, 200, 200, 201]
        assert len({r.json()['workflow_id'] for r in responses}) == 1
