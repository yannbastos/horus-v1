"""HOR-018: fixtures locais de yt-dlp; nunca acessa YouTube nem proxies reais."""
from contextlib import contextmanager
import asyncio
import errno
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from horus_backend.config import Settings
from horus_backend import jobs, transcriber
from support_g import client_for, pg_transport  # noqa: F401

URL = 'https://youtu.be/dQw4w9WgXcQ'
TOKEN = 'download-fixture-token-0123456789'
SECRET = 'secret-marker'


@contextmanager
def fixture_client(client_for, tmp_path, script):
    worker = tmp_path / 'worker.py'
    worker.write_text('import pathlib, sys, time\n'
        'root = pathlib.Path(sys.argv[sys.argv.index("--directory") + 1])\n' + script, encoding='utf-8')
    settings = Settings(token=TOKEN, data_dir=tmp_path / 'data', factory_root=None,
        hermes_executable=None, hermes_config=tmp_path / 'no-hermes.json',
        worker_command=(sys.executable, str(worker)))
    with client_for(settings) as client:
        yield client


def submit(client, folder):
    result = client.post('/v1/jobs', json={'operation': 'youtube.download', 'url': URL, 'directory': str(folder)})
    assert result.status_code == 202, result.text
    return result.json()['id']


def wait(client, job_id, *, terminal=True):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(f'/v1/jobs/{job_id}').json()
        if (terminal and job['status'] in jobs.TERMINAL) or (not terminal and job['status'] == 'running'):
            return job
        time.sleep(.02)
    pytest.fail(f'job não terminou: {job["status"]}')


@pytest.mark.parametrize('raw,expected', [
    ('Private video. Sign in', 'vídeo é privado'),
    ('Video removed by uploader', 'removido'),
    ('Video unavailable', 'indisponível'),
    ('Sign in to confirm your age', 'restrição de idade'),
    ('Video unavailable. Not available in your country', 'nesta região'),
    ('The uploader has not made this video available in your country', 'nesta região'),
    ('Connection reset by peer', 'conexão falhou'),
    ('HTTP Error 503', 'conexão falhou'),
    ('[Errno 28] No space left on device', 'Espaço em disco'),
    ('[WinError 5] Access is denied', 'Sem permissão'),
    ('unknown error', 'Não foi possível'),
])
def test_failures_are_actionable_and_do_not_persist_raw_diagnostics(client_for, tmp_path, raw, expected):
    diagnostic = f'ERROR: {raw} https://user:{SECRET}@proxy.invalid C:\\private\\{SECRET} \x1b[31m'
    script = f'print({diagnostic!r}, file=sys.stderr, flush=True)\nsys.exit(1)\n'
    with fixture_client(client_for, tmp_path, script) as client:
        job_id = submit(client, tmp_path)
        job = wait(client, job_id)
        assert job['status'] == 'failed'
        assert expected in job['error']
        events = client.app.state.store.events(job_id, 0)
        exposed = json.dumps(events) + job['error']
        assert SECRET not in exposed and 'proxy.invalid' not in exposed and 'C:\\private' not in exposed
        assert len(events) < 10


@pytest.mark.parametrize('error,expected', [(OSError(errno.ENOSPC, SECRET), 'Espaço'),
                                         (PermissionError(errno.EACCES, SECRET), 'permissão')])
def test_native_filesystem_errors_are_sanitized_and_classification_is_idempotent(error, expected):
    message = transcriber.download_error(error)
    assert expected in message and SECRET not in message
    assert transcriber.download_error(message) == message


def test_partial_survives_failure_and_explicit_retry_resumes(client_for, tmp_path):
    script = '''partial = root / 'safe.mp4.part'
target = root / 'safe.mp4'
if not partial.exists():
    partial.write_bytes(b'first-')
    print('ERROR: Connection reset', file=sys.stderr, flush=True)
    sys.exit(1)
assert partial.read_bytes() == b'first-'
with partial.open('ab') as stream:
    stream.write(b'second')
partial.rename(target)
print('HORUS_FILE:' + str(target), flush=True)
'''
    with fixture_client(client_for, tmp_path, script) as client:
        job_id = submit(client, tmp_path)
        assert wait(client, job_id)['status'] == 'failed'
        assert (tmp_path / 'safe.mp4.part').read_bytes() == b'first-'
        response = client.post(f'/v1/jobs/{job_id}/retry')
        assert response.status_code == 200, response.text
        assert wait(client, job_id)['status'] == 'succeeded'
        assert (tmp_path / 'safe.mp4').read_bytes() == b'first-second'


@pytest.mark.parametrize('mode', ['cancel', 'timeout'])
def test_cancel_and_timeout_stop_worker_and_preserve_partial(client_for, tmp_path, monkeypatch, mode):
    if mode == 'timeout':
        monkeypatch.setitem(jobs.TIMEOUTS, 'youtube.download', .3)
    script = "(root / 'safe.mp4.part').write_bytes(b'partial')\nprint('[download] 12.0%', flush=True)\ntime.sleep(30)\n"
    with fixture_client(client_for, tmp_path, script) as client:
        job_id = submit(client, tmp_path)
        deadline = time.monotonic() + 5
        while not (tmp_path / 'safe.mp4.part').exists():
            assert time.monotonic() < deadline
            time.sleep(.01)
        process = client.app.state.manager.active_process.process
        if mode == 'cancel':
            assert client.post(f'/v1/jobs/{job_id}/cancel').status_code == 200
        job = wait(client, job_id)
        assert job['status'] == ('cancelled' if mode == 'cancel' else 'failed')
        if mode == 'timeout':
            assert 'Tempo limite do download' in job['error'] and 'retomar' in job['error']
        assert process.returncode is not None
        assert (tmp_path / 'safe.mp4.part').read_bytes() == b'partial'
        assert job['result'] is None


def test_timeout_also_bounds_pipe_draining_after_worker_exit(client_for, tmp_path, monkeypatch):
    stopped = []

    async def start(*args, **kwargs):
        # Simula pipe herdado ainda aberto depois que o processo principal terminou.
        out, err = asyncio.StreamReader(), asyncio.StreamReader()
        err.feed_eof()

        async def exited():
            return 0

        async def stop():
            stopped.append(True)
            out.feed_eof()

        return SimpleNamespace(process=SimpleNamespace(stdout=out, stderr=err, wait=exited, returncode=0),
                               stop=stop, close=lambda: None)

    monkeypatch.setattr(jobs.OwnedProcess, 'start', start)
    monkeypatch.setitem(jobs.TIMEOUTS, 'youtube.download', .1)
    with fixture_client(client_for, tmp_path, '') as client:
        job = wait(client, submit(client, tmp_path))
        assert job['status'] == 'failed' and 'Tempo limite do download' in job['error']
        assert stopped


def test_giant_output_is_drained_without_persisting_hostile_title_or_paths(client_for, tmp_path):
    script = f'''print({SECRET!r} * 250000, flush=True)
print('[download] ' + '9' * 350 + '%', flush=True)
for _ in range(3000):
    print('[download] 20.0% https://user:{SECRET}@proxy.invalid', flush=True)
    print('WARNING: C:/private/{SECRET}', file=sys.stderr, flush=True)
target = root / 'safe [dQw4w9WgXcQ].mp4'
target.write_bytes(b'video')
print('HORUS_FILE:' + str(target), flush=True)
'''
    with fixture_client(client_for, tmp_path, script) as client:
        job_id = submit(client, tmp_path)
        job = wait(client, job_id)
        assert job['status'] == 'succeeded', job
        events = client.app.state.store.events(job_id, 0)
        assert len(events) < 10
        assert SECRET not in json.dumps(events)
        assert all(str(tmp_path) not in e['message'] for e in events)
        assert not any('Não foi possível' in e['message'] for e in events)


@pytest.mark.parametrize('target', ['../outside.mp4', 'unfinished.mp4.part'])
def test_worker_cannot_claim_outside_or_unfinished_file(client_for, tmp_path, target):
    script = f"target = root / {target!r}\ntarget.write_bytes(b'video')\nprint('HORUS_FILE:' + str(target), flush=True)\n"
    with fixture_client(client_for, tmp_path, script) as client:
        job = wait(client, submit(client, tmp_path))
        assert job['status'] == 'failed'
        assert job['result'] is None
        assert str(tmp_path) not in job['error']


def test_download_arguments_preserve_partial_and_sanitize_hostile_names(tmp_path, monkeypatch):
    utils = pytest.importorskip('yt_dlp.utils')
    captured = []
    monkeypatch.setattr(transcriber, 'find_tool', lambda name: None)
    monkeypatch.setitem(sys.modules, 'yt_dlp', SimpleNamespace(main=lambda args: captured.append(args)))
    assert transcriber.download(URL, tmp_path) == 0
    args = captured[0]
    assert {'--continue', '--part', '--no-overwrites', '--windows-filenames', '--trim-filenames'} <= set(args)
    assert args[-2:] == ['--', URL]
    name = utils.sanitize_filename('../CON:<hostile>|?*\\bad\r\nname', restricted=False, is_id=False)
    assert not any(c in name for c in '\r\n/\\:*?"<>|')
