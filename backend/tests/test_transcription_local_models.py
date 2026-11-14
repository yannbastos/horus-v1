"""Seleção local do modelo nunca consulta cache remoto nem baixa pesos."""
from types import SimpleNamespace
import sys

import pytest

from horus_backend import transcriber
from horus_backend.config import Settings
from horus_backend.jobs import TranscriptionRequest, job_environment


def test_prepared_local_model_is_read_only_and_offline(tmp_path, monkeypatch):
    root = tmp_path / 'whisper'
    model = root / 'faster-whisper-small'
    model.mkdir(parents=True)
    for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'):
        (model / name).write_text('fixture')
    monkeypatch.setenv('HORUS_WHISPER_MODELS_DIR', str(root))
    monkeypatch.setenv('HORUS_WHISPER_DEVICE', 'cpu')
    calls = []

    def load(source, **kwargs):
        calls.append((source, kwargs))
        return SimpleNamespace(transcribe=lambda *a, **k: (iter([]), SimpleNamespace(duration=1)))

    monkeypatch.setitem(sys.modules, 'ctranslate2', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=load))
    cache = tmp_path / 'unused-cache'
    transcriber.transcribe(tmp_path / 'audio.wav', 'small', 'pt', cache)
    assert calls[0][0] == str(model.resolve())
    assert calls[0][1]['local_files_only'] is True
    assert not cache.exists()


def test_missing_explicit_local_model_never_reaches_loader(tmp_path, monkeypatch):
    monkeypatch.setenv('HORUS_WHISPER_MODELS_DIR', str(tmp_path))
    monkeypatch.setenv('HORUS_WHISPER_DEVICE', 'cpu')
    monkeypatch.setitem(sys.modules, 'ctranslate2', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(
        WhisperModel=lambda *a, **k: pytest.fail('Não pode baixar modelo ausente')))
    with pytest.raises(ValueError, match='modo local não permite download'):
        transcriber.transcribe(tmp_path / 'audio.wav', 'small', 'pt', tmp_path / 'cache')


def test_offline_configuration_only_reaches_transcription(tmp_path, monkeypatch):
    from horus_backend.jobs import FactoryRequest
    values = {'HORUS_WHISPER_MODELS_DIR': str(tmp_path), 'HF_HUB_OFFLINE': '1',
              'HF_HUB_DISABLE_TELEMETRY': '1', 'TRANSFORMERS_OFFLINE': '1'}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = Settings(token='local-test-token-0123456789', data_dir=tmp_path)
    env = job_environment(TranscriptionRequest(operation='transcription.run', path='a.wav'), settings)
    assert all(env[k] == v for k, v in values.items())
    other = job_environment(FactoryRequest(operation='factory.profile'), settings)
    assert not set(values) & other.keys()
