"""Fixtures sintéticas servidas localmente: não baixa modelos reais."""
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import urllib.request

import pytest
from fastapi.testclient import TestClient

from horus_backend import whisper_download as download
from horus_backend.api import create_app
from horus_backend.config import Settings
from horus_backend.whisper_models import WhisperModels, MIN_WEIGHTS


@pytest.fixture
def local_model(monkeypatch):
    files = {'model.bin': b'x' * MIN_WEIGHTS, 'config.json': b'{}', 'tokenizer.json': b'{}',
             'vocabulary.txt': b'fixture', 'README.md': b'---\nlicense: mit\n---\nSynthetic model'}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = files[self.path.rsplit('/', 1)[-1]]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real = urllib.request.urlopen
    monkeypatch.setattr(download, 'open_download', lambda url: real(
        f'http://127.0.0.1:{server.server_port}/' + url.rsplit('/', 1)[-1], timeout=2))
    manifest = {name: {'size_bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()} for name, body in files.items()}
    monkeypatch.setattr(download, 'CATALOG', {'tiny': {'repo': 'synthetic/model', 'revision': 'a' * 40, 'files': manifest}})
    yield files, manifest
    server.shutdown()
    server.server_close()
    thread.join()


def test_integrity_atomic_install_and_removal(tmp_path, local_model):
    service = WhisperModels(tmp_path)
    progress = []
    installed = download.transfer(service, 'tiny', threading.Event(), progress.append)
    assert installed['installed'] and installed['removable']
    assert progress == sorted(progress) and max(progress) <= 99
    assert not list(service.root.glob('.download-*'))
    assert service.remove('tiny')['removed']


def test_hash_mismatch_discards_partial(tmp_path, local_model):
    files, manifest = local_model
    manifest['model.bin']['sha256'] = '0' * 64
    service = WhisperModels(tmp_path)
    with pytest.raises(ValueError, match='Integridade'):
        download.transfer(service, 'tiny', threading.Event(), lambda _: None)
    assert not list(service.root.iterdir())


def test_cancellation_discards_partial(tmp_path, local_model):
    service = WhisperModels(tmp_path)
    cancel = threading.Event()
    with pytest.raises(download.DownloadCancelled):
        download.transfer(service, 'tiny', cancel, lambda _: cancel.set())
    assert not list(service.root.iterdir())


@pytest.mark.parametrize('url', ['http://huggingface.co/file', 'https://evil.test/file',
    'https://huggingface.co.evil.test/file', 'https://user:pass@huggingface.co/file',
    'https://huggingface.co:444/file', 'file:///tmp/model'])
def test_redirect_allowlist_rejects_untrusted_urls(url):
    with pytest.raises(ValueError):
        download.checked_url(url)


def test_catalog_is_offline_and_pinned():
    for model, item in download.CATALOG.items():
        assert len(item['revision']) == 40
        assert set(('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')) <= item['files'].keys()
        assert all(len(f['sha256']) == 64 and f['size_bytes'] > 0 for f in item['files'].values())
    assert all(item['destination_label'].startswith('models/') for item in download.catalog()['models'])


def test_api_requires_confirmation_and_idempotency(tmp_path, local_model, monkeypatch):
    # Empilha jobs sem execução para testar a fronteira da API sem rede.
    from horus_backend.jobs import JobManager
    async def no_start(self):
        pass
    monkeypatch.setattr(JobManager, 'start', no_start)
    settings = Settings(token='test-token-long-enough-123456', data_dir=tmp_path / 'data',
                        factory_root=None, hermes_executable=None, hermes_config=tmp_path/'hermes.json')
    download.CATALOG['base'] = download.CATALOG['tiny']
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        client.headers['Authorization'] = f'Bearer {settings.token}'
        assert client.get('/v1/transcription/models/catalog').status_code == 200
        path = '/v1/transcription/models/tiny/download'
        headers = {'Idempotency-Key': 'synthetic-request'}
        assert client.post(path, headers=headers, json={'confirm': False}).status_code == 422
        first = client.post(path, headers=headers, json={'confirm': True})
        assert first.status_code == 202, first.text
        assert client.post(path, headers=headers, json={'confirm': True}).json() == first.json()
        assert client.post(path, headers={'Idempotency-Key': 'other'}, json={'confirm': True}).status_code == 409
        assert client.post('/v1/transcription/models/base/download', headers=headers, json={'confirm': True}).status_code == 409
        assert client.get('/v1/jobs/' + first.json()['job_id']).json()['status'] == 'queued'

def test_worker_download_and_sanitized_failure(tmp_path, local_model):
    import time
    settings = Settings(token='test-token-long-enough-123456', data_dir=tmp_path / 'data',
                        factory_root=None, hermes_executable=None, hermes_config=tmp_path/'hermes.json')
    with TestClient(create_app(settings), base_url='http://127.0.0.1',
                    headers={'Authorization': f'Bearer {settings.token}'}) as client:
        response = client.post('/v1/transcription/models/tiny/download',
                               headers={'Idempotency-Key': 'run'}, json={'confirm': True})
        assert response.status_code == 202, response.text
        job_id = response.json()['job_id']
        for _ in range(100):
            job = client.get('/v1/jobs/' + job_id).json()
            if job['status'] in ('succeeded', 'failed'):
                break
            time.sleep(0.02)
        assert job['status'] == 'succeeded', job
        assert job['result']['installed']
        assert client.post('/v1/jobs/' + job_id + '/retry').status_code == 409


def test_http_errors_do_not_persist_signed_urls(tmp_path, local_model, monkeypatch):
    import asyncio
    from horus_backend.jobs import JobManager
    from horus_backend.storage import Store
    def fail(url):
        raise OSError('https://evil.test/path?secret=never-persist-this')
    monkeypatch.setattr(download, 'open_download', fail)
    settings = Settings(token='test-token-long-enough-123456', data_dir=tmp_path,
                        factory_root=None, hermes_executable=None, hermes_config=tmp_path/'hermes.json')
    store = Store(tmp_path/'state.sqlite3')
    manager = JobManager(store, settings)
    job, _ = store.create_job({'operation': 'whisper.model.download', 'model': 'tiny'}, 'failure')
    asyncio.run(download.run(manager, job))
    saved = store.get_job(job['id'])
    assert saved['status'] == 'failed'
    assert 'secret' not in json.dumps(saved)
    assert not list((tmp_path/'models').iterdir())

def test_explicit_download_cleans_only_owned_stale_partials(tmp_path, local_model):
    service = WhisperModels(tmp_path)
    service._root()
    stale = service.root/'.download-stale'
    stale.mkdir()
    (stale/'model.bin').write_bytes(b'incomplete')
    unexpected = service.root/'.download-unknown'
    unexpected.mkdir()
    (unexpected/'operator.txt').write_text('preserve')
    download.transfer(service, 'tiny', threading.Event(), lambda _: None)
    assert not stale.exists()
    assert (unexpected/'operator.txt').read_text() == 'preserve'


def test_manager_cancel_reaches_thread(tmp_path, local_model, monkeypatch):
    import asyncio
    from horus_backend.jobs import JobManager
    from horus_backend.storage import Store
    def wait_for_cancel(service, model, cancel, progress):
        assert cancel.wait(timeout=3)
        raise download.DownloadCancelled()
    monkeypatch.setattr(download, 'transfer', wait_for_cancel)
    settings = Settings(token='test-token-long-enough-123456', data_dir=tmp_path,
                        factory_root=None, hermes_executable=None, hermes_config=tmp_path/'hermes.json')
    store = Store(tmp_path/'state.sqlite3')
    manager = JobManager(store, settings)
    job, _ = store.create_job({'operation': 'whisper.model.download', 'model': 'tiny'}, 'cancel')
    async def scenario():
        task = asyncio.create_task(download.run(manager, job))
        await asyncio.sleep(0.02)
        await manager.cancel(job['id'])
        await task
    asyncio.run(scenario())
    assert store.get_job(job['id'])['status'] == 'cancelled'
