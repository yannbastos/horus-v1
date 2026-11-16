import hashlib
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from horus_backend.api import create_app
from horus_backend.config import Settings
from horus_backend.local_data_export import restore_local_data, verify_export
from horus_backend.storage import Store
from horus_backend.update_backup import create_update_backup


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


def test_backup_live_wal_radar_preferences_restore_and_unique_label(tmp_path):
    data = tmp_path / 'workspace'
    Store(data / 'horus.sqlite3')
    (data / 'radar').mkdir()
    with closing(sqlite3.connect(data / 'radar/radar-youtube.sqlite3')) as db:
        db.execute('CREATE TABLE fixture(value)')
        db.commit()
    (data / 'narration-preference.json').write_text('{"voice":"fixture"}')
    (data / 'video.mp4').write_bytes(b'large-media-excluded')
    (data / 'models').mkdir()
    (data / 'exports').mkdir()
    with closing(sqlite3.connect(data / 'horus.sqlite3')) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE update_fixture(value)')
        db.execute("INSERT INTO update_fixture VALUES('committed-in-wal')")
        db.commit()
        result = create_update_backup(data)
        again = create_update_backup(data)
    assert result['label'] != again['label']
    package = tmp_path / 'update-backups' / result['label']
    assert hashlib.sha256(package.read_bytes()).hexdigest() == result['sha256']
    assert verify_export(package)['ok']
    destination = tmp_path / 'restored'
    assert restore_local_data(package, destination)['ok']
    with closing(sqlite3.connect(destination / 'horus.sqlite3')) as db:
        assert db.execute('SELECT * FROM update_fixture').fetchall() == [('committed-in-wal',)]
    assert json.loads((destination / 'narration-preference.json').read_text()) == {'voice':'fixture'}
    with zipfile.ZipFile(package) as archive:
        assert not any('video' in n for n in archive.namelist())


def test_missing_database_refused(tmp_path):
    data = tmp_path / 'workspace'
    data.mkdir()
    with pytest.raises(ValueError):
        create_update_backup(data)


def test_concurrent_wal_writer_backup_restores_a_consistent_committed_snapshot(tmp_path):
    import threading
    data=tmp_path/'workspace'
    Store(data/'horus.sqlite3')
    ready,stop=threading.Event(),threading.Event()
    committed=[]
    def writer():
        with closing(sqlite3.connect(data/'horus.sqlite3')) as db:
            db.execute('PRAGMA journal_mode=WAL')
            while not stop.is_set():
                index=len(committed)
                db.execute('INSERT INTO media VALUES(?,?,?,?,?,?)',(f'writer-{index}',str(data/f'fixture-{index}.mp4'),'fixture',1,1,'2026-11-16'))
                db.commit();committed.append(index);ready.set()
    thread=threading.Thread(target=writer);thread.start()
    try:
        assert ready.wait(5)
        assert (data/'horus.sqlite3-wal').stat().st_size>0
        result=create_update_backup(data)
    finally:
        stop.set();thread.join(timeout=10)
    assert not thread.is_alive()
    target=tmp_path/'restored-concurrent'
    assert restore_local_data(tmp_path/'update-backups'/result['label'],target)['ok']
    with closing(sqlite3.connect(target/'horus.sqlite3')) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        copied=sorted(int(row[0].split('-')[1]) for row in db.execute('SELECT id FROM media'))
    assert copied and copied==list(range(len(copied)))
    assert len(copied)<=len(committed)


def test_invalid_preference_refuses_incomplete_backup(tmp_path):
    data = tmp_path / 'workspace'
    Store(data / 'horus.sqlite3')
    (data / 'narration-preference.json').write_text('invalid-json')
    with pytest.raises(ValueError, match='incompleto'):
        create_update_backup(data)


def test_activity_not_limited_to_recent_100_and_backup_guard(tmp_path):
    app = create_app(Settings(token='x'*32, data_dir=tmp_path/'workspace', factory_root=None, hermes_executable=None))
    store = app.state.store
    job, _ = store.create_job({'operation':'transcription.run'}, 'active')
    for i in range(101):
        done, _ = store.create_job({'operation':'transcription.run'}, str(i))
        store.update(done['id'], 'succeeded')
    headers = {'Authorization':'Bearer '+'x'*32}
    # Sem lifespan: o worker não pode consumir o job semeado.
    client = TestClient(app, base_url='http://127.0.0.1')
    for status in ['queued','running','cancel_requested']:
        store.update(job['id'], status)
        assert client.get('/v1/update/activity', headers=headers).json() == {'busy':True,'active_jobs':1,'active_previews':0}
        assert client.post('/v1/update/backup', headers=headers).status_code == 409
    store.update(job['id'], 'succeeded')
    assert client.get('/v1/update/activity', headers=headers).json() == {'busy':False,'active_jobs':0,'active_previews':0}
    assert client.post('/v1/update/backup', headers=headers).json()['ok']


def test_preview_is_busy_until_provider_returns_even_on_failure(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    app = create_app(Settings(token='x'*32, data_dir=tmp_path/'workspace', factory_root=None, hermes_executable=None))
    service = app.state.narration
    entered, finish = threading.Event(), threading.Event()
    def blocked(_):
        entered.set()
        assert finish.wait(10)
        raise ValueError('fixture')
    monkeypatch.setattr(service, '_preview', blocked)
    client = TestClient(app, base_url='http://127.0.0.1')
    headers = {'Authorization':'Bearer '+'x'*32}
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
