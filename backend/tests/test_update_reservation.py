"""Reserva em memória: somente dados temporários e chamadas locais autenticadas."""
from fastapi.testclient import TestClient
from horus_backend.api import create_app
from horus_backend.config import Settings


def test_reservation_blocks_mutations_but_keeps_reads_backup_and_release(tmp_path):
    app=create_app(Settings(token='x'*32,data_dir=tmp_path/'workspace',factory_root=None,hermes_executable=None))
    client=TestClient(app,base_url='http://127.0.0.1')
    headers={'Authorization':'Bearer '+'x'*32}
    assert client.post('/v1/update/reservation').status_code==401
    assert client.post('/v1/update/reservation',headers=headers).json()=={'reserved':True}
    assert client.post('/v1/update/reservation',headers=headers).status_code==409
    assert client.post('/v1/jobs',headers=headers,json={}).status_code==409
    assert client.put('/v1/narration/preference',headers=headers,json={}).status_code==409
    assert client.get('/v1/update/activity',headers=headers).status_code==200
    assert client.post('/v1/update/backup',headers=headers).json()['ok']
    assert client.delete('/v1/update/reservation',headers=headers).json()=={'reserved':False}
    assert client.post('/v1/jobs',headers=headers,json={}).status_code!=409


def test_inflight_mutation_cannot_be_reserved(tmp_path,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    app=create_app(Settings(token='x'*32,data_dir=tmp_path/'workspace',factory_root=None,hermes_executable=None))
    entered,finish=threading.Event(),threading.Event()
    def preview(_):
        entered.set()
        assert finish.wait(10)
        return {'id':'fixture'}
    monkeypatch.setattr(app.state.narration,'preview',preview)
    client=TestClient(app,base_url='http://127.0.0.1')
    headers={'Authorization':'Bearer '+'x'*32}
    with ThreadPoolExecutor() as pool:
        pending=pool.submit(client.post,'/v1/narration/preview',headers=headers,json={'provider':'sapi','voice_id':'fixture','text':'fixture'})
        try:
            assert entered.wait(5)
            assert client.post('/v1/update/reservation',headers=headers).status_code==409
        finally: finish.set()
        pending.result()
    assert client.post('/v1/update/reservation',headers=headers).status_code==200


def test_worker_terminal_cleanup_still_blocks_update(tmp_path):
    app=create_app(Settings(token='x'*32,data_dir=tmp_path/'workspace',factory_root=None,hermes_executable=None))
    client=TestClient(app,base_url='http://127.0.0.1')
    headers={'Authorization':'Bearer '+'x'*32}
    app.state.manager.active_id='finishing-terminal-job'
    state=client.get('/v1/update/activity',headers=headers).json()
    assert state['busy'] and state['active_jobs']==0
    assert client.post('/v1/update/backup',headers=headers).status_code==409
    app.state.manager.active_id=None
    assert not client.get('/v1/update/activity',headers=headers).json()['busy']


def test_post_restart_health_requires_authentication_and_checks_real_database(tmp_path):
    app=create_app(Settings(token='x'*32,data_dir=tmp_path/'workspace',factory_root=None,hermes_executable=None))
    client=TestClient(app,base_url='http://127.0.0.1')
    assert client.get('/v1/update/health').status_code==401
    result=client.get('/v1/update/health',headers={'Authorization':'Bearer '+'x'*32})
    assert result.status_code==200
    assert result.json()['ok'] and result.json()['integrity_check']=='ok'
