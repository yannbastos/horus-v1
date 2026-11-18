import hashlib
import json
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from horus_backend.config import Settings
from horus_backend.local_data_export import restore_local_data, verify_export
from horus_backend.storage import DatabaseUnavailable, Store
from horus_backend.update_backup import UpdateBackupBlocked, create_update_backup
from support_d import make_pg_manager
from schema_support import schema_database  # noqa: E402 (support_d adiciona tests/pg ao sys.path)
from support_e import pg_client_for, pg_create_app, pg_transport  # noqa: F401


@pytest.fixture
def pg_store(pg_app_cluster, pg_app_database, tmp_path, monkeypatch):
    manager = make_pg_manager(pg_app_cluster, pg_app_database, tmp_path / 'vault', monkeypatch)
    try:
        yield Store(manager, data_dir=tmp_path / 'workspace')
    finally:
        manager.close()


def test_owner_eof_uses_unbuffered_reads(monkeypatch):
    from horus_backend import __main__ as entry
    chunks = iter([b'fixture', b''])
    stopped = []
    monkeypatch.setattr(entry.os, 'read', lambda fd, size: next(chunks))
    class Input:
        def fileno(self): return 10
    monkeypatch.setattr(entry.sys, 'stdin', Input())
    entry.watch_owner_input(lambda: stopped.append(True))
    assert stopped == [True]


def test_backup_live_pg_preferences_restore_and_unique_label(pg_store, pg_app_cluster, tmp_path):
    """Porte de `..._live_wal_radar_preferences_restore_and_unique_label`: o dump lógico PG (REPEATABLE READ) substitui o
    WAL do SQLite; a preferência de narração é linha PG e a mídia grande continua excluída."""
    data = tmp_path / 'workspace'
    (data / 'models').mkdir(parents=True)
    (data / 'exports').mkdir()
    (data / 'video.mp4').write_bytes(b'large-media-excluded')
    with pg_store.uow() as conn:
        conn.execute(
            "INSERT INTO horus.preferences(device_id, key, value, updated_at) "
            "VALUES ('local', 'narration.preference', %s, clock_timestamp())", (json.dumps({'voice': 'fixture'}),))
    committed, _ = pg_store.create_job({'operation': 'transcription.run'}, 'committed-before-backup')
    result = create_update_backup(data, store=pg_store)
    again = create_update_backup(data, store=pg_store)
    assert result['label'] != again['label']
    package = tmp_path / 'update-backups' / result['label']
    assert hashlib.sha256(package.read_bytes()).hexdigest() == result['sha256']
    assert verify_export(package)['ok']
    assert result['remote_proof']['tables_count'] and result['remote_proof']['schema_version']
    with zipfile.ZipFile(package) as archive:
        assert not any('video' in n or '.sqlite' in n for n in archive.namelist())
    with schema_database(pg_app_cluster) as (target_admin, _pool):
        target_admin.autocommit = False
        restored = restore_local_data(package, tmp_path / 'restored', target_conn=target_admin)
        target_admin.commit()
        assert restored['ok'] and restored['pg_restored']
        assert target_admin.execute('SELECT status FROM horus.jobs WHERE id = %s', (committed['id'],)).fetchone() == ('queued',)
        value = target_admin.execute("SELECT value FROM horus.preferences WHERE key = 'narration.preference'").fetchone()[0]
        assert (json.loads(value) if isinstance(value, str) else value) == {'voice': 'fixture'}


def test_missing_database_refused(tmp_path):
    """Sem conexão PG não há fallback: o backup falha fechado (503 estável) e nada é gravado."""
    data = tmp_path / 'workspace'
    data.mkdir()
    with pytest.raises(DatabaseUnavailable) as refused:
        create_update_backup(data, store=None)
    assert refused.value.http_status == 503
    with pytest.raises(DatabaseUnavailable):
        create_update_backup(data, store=Store(None, data_dir=data))
    assert not list((tmp_path / 'update-backups').glob('*.zip'))


def test_missing_data_dir_refused(pg_store, tmp_path):
    with pytest.raises(ValueError):
        create_update_backup(tmp_path / 'inexistente', store=pg_store)


def test_concurrent_pg_writer_backup_restores_a_consistent_committed_snapshot(pg_store, pg_app_cluster, tmp_path):
    """Porte do teste de escritor WAL concorrente: o snapshot REPEATABLE READ só contém um prefixo contíguo das linhas
    confirmadas pelo escritor, nunca linhas parciais."""
    data = tmp_path / 'workspace'
    data.mkdir()
    ready, stop = threading.Event(), threading.Event()
    committed = []

    def writer():
        index = 0
        while not stop.is_set():
            with pg_store.uow() as conn:
                conn.execute(
                    'INSERT INTO horus.media(id, path, name, size, modified_ns, created_at) '
                    "VALUES (%s, %s, 'fixture', 1, 1, clock_timestamp())",
                    (f'writer-{index:06d}', str(data / f'fixture-{index}.mp4')))
            committed.append(index)
            index += 1
            ready.set()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert ready.wait(5)
        result = create_update_backup(data, store=pg_store)
    finally:
        stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    with schema_database(pg_app_cluster) as (target_admin, _pool):
        target_admin.autocommit = False
        assert restore_local_data(tmp_path / 'update-backups' / result['label'], tmp_path / 'restored-concurrent',
                                  target_conn=target_admin)['ok']
        target_admin.commit()
        copied = sorted(int(row[0].split('-')[1]) for row in target_admin.execute(
            "SELECT id FROM horus.media WHERE id LIKE 'writer-%'").fetchall())
    assert copied and copied == list(range(len(copied)))
    assert len(copied) <= len(committed)


def test_invalid_local_json_refuses_incomplete_backup(pg_store, tmp_path):
    """Porte de `invalid_preference_refuses_incomplete_backup`: a preferência agora é linha PG; o JSON local que restou
    (planos de produção) inválido continua tornando o pacote incompleto e bloqueando a atualização."""
    data = tmp_path / 'workspace'
    production = data / 'productions' / uuid.uuid4().hex
    production.mkdir(parents=True)
    (production / 'plan.json').write_text('invalid-json')
    with pytest.raises(UpdateBackupBlocked, match='incompleto') as blocked:
        create_update_backup(data, store=pg_store)
    assert blocked.value.code == 'update_backup_incomplete'


def test_credential_shaped_local_json_refuses_backup(pg_store, tmp_path):
    data = tmp_path / 'workspace'
    production = data / 'productions' / uuid.uuid4().hex
    production.mkdir(parents=True)
    (production / 'plan.json').write_text('{"token": "' + 'sk-' + 'secret-123456789012345678901234567890' + '"}')
    with pytest.raises(UpdateBackupBlocked, match='incompleto'):
        create_update_backup(data, store=pg_store)


def test_activity_not_limited_to_recent_100_and_backup_guard(tmp_path, pg_transport):
    app = pg_create_app(Settings(token='x'*32, data_dir=tmp_path/'workspace', factory_root=None, hermes_executable=None))
    # Sem lifespan o worker não inicia e não pode consumir o job semeado; conectamos o banco explicitamente.
    (tmp_path / 'workspace').mkdir(exist_ok=True)  # o lifespan real garante a pasta de dados; aqui não há lifespan
    assert app.state.database.test().state == 'connected'
    try:
        store = app.state.store
        job, _ = store.create_job({'operation':'transcription.run'}, 'active')
        for i in range(101):
            done, _ = store.create_job({'operation':'transcription.run'}, str(i))
            store.update(done['id'], 'succeeded')
        headers = {'Authorization':'Bearer '+'x'*32}
        client = TestClient(app, base_url='http://127.0.0.1')
        for status in ['queued','running','cancel_requested']:
            store.update(job['id'], status)
            assert client.get('/v1/update/activity', headers=headers).json() == {'busy':True,'active_jobs':1,'active_previews':0}
            assert client.post('/v1/update/backup', headers=headers).status_code == 409
        store.update(job['id'], 'succeeded')
        assert client.get('/v1/update/activity', headers=headers).json() == {'busy':False,'active_jobs':0,'active_previews':0}
        response = client.post('/v1/update/backup', headers=headers)
        result = response.json()
        assert result['ok'] and result['remote_proof']['tables_count']
    finally:
        app.state.database.close()


def test_preview_is_busy_until_provider_returns_even_on_failure(tmp_path, monkeypatch, pg_transport):
    settings = Settings(token='x'*32, data_dir=tmp_path/'workspace', factory_root=None, hermes_executable=None)
    headers = {'Authorization':'Bearer '+'x'*32}
    with pg_client_for(settings) as client:
        service = client.app.state.narration
        entered, finish = threading.Event(), threading.Event()
        def blocked(_):
            entered.set()
            assert finish.wait(10)
            raise ValueError('fixture')
        monkeypatch.setattr(service, '_preview', blocked)
        with ThreadPoolExecutor() as pool:
            task = pool.submit(service.preview, None)
            try:
                assert entered.wait(10)
                assert client.get('/v1/update/activity', headers=headers).json()['busy']
                assert client.post('/v1/update/backup', headers=headers).status_code == 409
            finally:
                finish.set()
            with pytest.raises(ValueError): task.result()
        assert not client.get('/v1/update/activity', headers=headers).json()['busy']
