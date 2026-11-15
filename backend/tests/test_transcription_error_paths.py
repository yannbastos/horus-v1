"""Diagnósticos e caminhos de modelos: fixtures negativas, sem rede ou ASR real."""
import asyncio
from pathlib import Path
import os
import subprocess
import stat
import sys
import time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from horus_backend import cuda_runtime as cr, speech_alignment as sa, transcriber as tr
from horus_backend.jobs import JobManager
from horus_backend.local_diagnostics import safe_error
from horus_backend.whisper_models import ImportModelRequest, WhisperModels
from horus_backend.api import create_app
from horus_backend.config import Settings


@pytest.mark.parametrize('path', [r'C:\private\operator\audio.wav', 'C:/private/operator/audio.wav',
    '/private/operator/audio.wav', r'\\server\private\audio.wav', r'C:\private folder\operator\audio.wav'])
def test_diagnostic_keeps_basename_without_absolute_path(path):
    value = safe_error(f"Não foi possível abrir '{path}': acesso negado")
    assert path not in value and 'audio.wav' in value and 'acesso negado' in value
    assert safe_error(value) == value


def test_cuda_inspection_hides_os_error_path(monkeypatch):
    monkeypatch.setattr(cr, 'WINDOWS', True)
    monkeypatch.setenv('HORUS_CUDA_RUNTIME_DIR', 'fixture')
    def fail(value):
        raise OSError(13, 'denied', r'C:\private\runtime\cudnn.dll')
    monkeypatch.setattr(cr, '_folders', fail)
    report = cr.inspect_runtime()
    assert 'C:\\private' not in report['detail'] and 'cudnn.dll' in report['detail']


def test_missing_transcription_model_does_not_expose_models_directory(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'ctranslate2', NS())
    monkeypatch.setitem(sys.modules, 'faster_whisper', NS(WhisperModel=lambda *a, **k: pytest.fail('Modelo ausente')))
    monkeypatch.setenv('HORUS_WHISPER_DEVICE', 'cpu')
    monkeypatch.delenv('HORUS_WHISPER_MODELS_DIR', raising=False)
    with pytest.raises(FileNotFoundError) as captured:
        tr._transcribe(tmp_path / 'audio.wav', 'small', 'pt', tmp_path / 'private-models', False)
    assert str(tmp_path) not in str(captured.value) and 'small' in str(captured.value)


def test_job_events_and_failure_tail_hide_paths_but_keep_transcript():
    async def check():
        events = []
        async def emit(job_id, kind, message):
            events.append((kind, message))
        manager = object.__new__(JobManager)
        manager.emit = emit
        notes, failures = {'segments': []}, []
        path = r'C:\private\operator\audio.wav'
        await manager._line('job', 'stderr', f"Error opening '{path}'", notes, failures)
        await manager._line('job', 'stdout', '{"type":"status","message":"Falha em C:/private/operator/audio.wav"}', notes, failures)
        await manager._line('job', 'stdout', '{"type":"segment","message":"A pessoa disse C:/work/file.wav"}', notes, failures)
        assert path not in str(failures)
        assert all('private' not in message for kind, message in events if kind != 'segment')
        assert notes['segments'] == ['A pessoa disse C:/work/file.wav']
    asyncio.run(check())


def test_alignment_failure_reason_redacts_subprocess_stderr(tmp_path, monkeypatch):
    model = tmp_path / 'small'
    model.mkdir()
    (model / 'model.bin').touch()
    monkeypatch.setattr(sa, '_wav_facts', lambda path: ('a' * 64, 1.0))
    from horus_backend.processes import OwnedProcess
    async def communicate(**kwargs):
        return b'', b"RuntimeError: cannot open 'C:/private/operator/audio.wav'"
    async def stop():
        pass
    async def start(*args, **kwargs):
        return NS(process=NS(returncode=1), communicate=communicate, stop=stop, close=lambda: None)
    monkeypatch.setattr(OwnedProcess, 'start', start)
    result = sa.run_alignment(tmp_path / 'audio.wav', 'teste', sections=[{'section': 1, 'start': 0, 'end': 1}],
        engine={'name': 'fixture', 'voice': 'fixture'}, model_dir=tmp_path)
    assert 'private' not in result['reason'] and 'audio.wav' in result['reason']


def link_folder(link, target):
    if os.name == 'nt':
        # Junction de fixture não exige privilégio de symlink; nenhum comando de exclusão.
        result = subprocess.run(['powershell', '-NoProfile', '-Command',
            '$ErrorActionPreference="Stop"; New-Item -ItemType Junction -Path $env:HORUS_TEST_LINK -Target $env:HORUS_TEST_TARGET | Out-Null'],
            env={**os.environ, 'HORUS_TEST_LINK': str(link), 'HORUS_TEST_TARGET': str(target)}, capture_output=True)
        if result.returncode:
            pytest.skip('SO não permitiu criar junction de fixture')
    else:
        link.symlink_to(target, target_is_directory=True)


def test_model_folder_link_is_refused_by_resolution_and_import(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'):
        (source / name).write_text('{}')
    linked = tmp_path / 'small'
    link_folder(linked, source)
    assert sa.resolve_model_path(tmp_path, 'small') is None
    service = WhisperModels(tmp_path / 'data')
    with pytest.raises(ValueError, match='link'):
        service.install(ImportModelRequest(model='small', source_directory=str(linked), confirm_license=True))
    assert (source / 'model.bin').read_text() == '{}'


@pytest.mark.parametrize('kind', ['reparse', 'symlink'])
def test_required_file_link_never_reaches_is_file(tmp_path, monkeypatch, kind):
    model = tmp_path / 'small'
    model.mkdir()
    file = model / 'model.bin'
    file.write_bytes(b'fixture')
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == file:
            return NS(st_mode=stat.S_IFLNK if kind == 'symlink' else info.st_mode,
                      st_file_attributes=0x400 if kind == 'reparse' else 0)
        return info
    monkeypatch.setattr(Path, 'lstat', reparse)
    monkeypatch.setattr(Path, 'is_file', lambda *args: pytest.fail('Não pode seguir links via is_file'))
    assert sa.resolve_model_path(tmp_path, 'small') is None


def test_job_history_sanitizes_os_exception_before_persisting(tmp_path):
    token = 'path-history-fixture-token-0123456789'
    app = create_app(Settings(token=token, data_dir=tmp_path / 'data', factory_root=None,
        hermes_executable=None, hermes_config=tmp_path / 'no-hermes.json'))
    def fail(request):
        raise OSError(13, 'access denied', r'C:\private\operator\audio.wav')
    app.state.manager.command = fail
    with TestClient(app, base_url='http://127.0.0.1', headers={'Authorization': f'Bearer {token}'}) as client:
        response = client.post('/v1/jobs', json={'operation': 'transcription.run', 'path': str(tmp_path / 'audio.wav')})
        assert response.status_code == 202
        job_id = response.json()['id']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get(f'/v1/jobs/{job_id}').json()
            if job['status'] == 'failed':
                break
            time.sleep(.01)
        assert job['status'] == 'failed' and 'audio.wav' in job['error']
        assert 'private' not in job['error'] and 'C:' not in job['error']
