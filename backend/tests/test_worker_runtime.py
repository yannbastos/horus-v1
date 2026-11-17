import json
import subprocess
import sys

import pytest

from horus_backend import worker_runtime as runtime
from support_b import settings  # noqa: F401


@pytest.mark.parametrize('name', runtime.WORKERS)
def test_frozen_workers_use_executable_entrypoint(monkeypatch, name):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert runtime.worker_command(name, 'input with spaces', development=['-m', 'legacy']) == [
        sys.executable, name, 'input with spaces']


def test_development_command_preserved(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert runtime.worker_command('alignment-worker', '--wav', 'audio.wav',
        development=['-I', '-m', 'horus_backend.speech_alignment']) == [
            sys.executable, '-I', '-m', 'horus_backend.speech_alignment', '--wav', 'audio.wav']


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'invalid', 'wrong_protocol'])
def test_packaged_readiness_fails_closed(monkeypatch, failure):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    def run(command, **kwargs):
        assert command == [sys.executable, 'production-check']
        assert 'HORUS_API_TOKEN' not in kwargs['env']
        if failure == 'exit':
            raise subprocess.CalledProcessError(1, command)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, 15)
        return b'garbage' if failure == 'invalid' else b'{}'
    monkeypatch.setenv('HORUS_API_TOKEN', 'private-token')
    monkeypatch.setattr(runtime, 'run_sync', run)
    assert not runtime.production_worker_available()


def test_packaged_readiness_requires_worker_handshake(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(runtime, 'run_sync', lambda *a, **k:
        json.dumps({'production_worker': True, 'protocol': 1}).encode())
    assert runtime.production_worker_available()


def test_diagnostics_not_ready_when_packaged_worker_cannot_start(monkeypatch, settings):
    from horus_backend.diagnostics import production_diagnostics
    monkeypatch.setattr(runtime, 'production_worker_available', lambda: False)
    result = production_diagnostics(settings)
    assert result['ready'] is False
    assert 'production_worker' in result['missing']


def test_dispatch_strips_internal_argument(monkeypatch):
    import runpy
    observed = []
    monkeypatch.setattr(sys, 'argv', ['horus-backend', 'production-worker', 'output', '--factory-source', 'factory'])
    monkeypatch.setattr(runpy, 'run_module', lambda name, **kw: observed.append((name, sys.argv.copy())))
    with pytest.raises(SystemExit) as exit:
        runtime.dispatch()
    assert exit.value.code == 0
    assert observed == [('horus_backend.production_worker', ['horus-backend', 'output', '--factory-source', 'factory'])]


def test_packaged_check_rejects_missing_bridge_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(runtime, '__file__', str(tmp_path / 'worker_runtime.py'))
    imported = []
    monkeypatch.setattr(runtime.importlib, 'import_module', imported.append)
    with pytest.raises(FileNotFoundError):
        runtime.check_resources()
    assert imported == list(runtime.WORKERS.values())


def test_dispatch_does_not_accept_arbitrary_module(monkeypatch):
    import runpy
    monkeypatch.setattr(sys, 'argv', ['horus-backend', 'operator.module'])
    monkeypatch.setattr(runpy, 'run_module', lambda *a, **k: pytest.fail('Módulo fora da lista fixa'))
    assert runtime.dispatch() is None
