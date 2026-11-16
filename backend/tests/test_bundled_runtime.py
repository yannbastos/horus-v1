"""Descoberta relativa e isolada do perfil opcional, sem depender de ferramentas reais."""
import json
import os
import sys

from horus_backend import bundled_runtime
from horus_backend.config import Settings
from horus_backend.dependencies import find_tool
from horus_backend.jobs import FactoryRequest, job_environment


def test_minimum_and_development_never_discover_arbitrary_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'horus-backend.exe'))
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    folder = tmp_path / 'first-video'
    folder.mkdir()
    (folder / 'runtime.json').write_text('{"profile":"first-video","schema_version":1}')
    assert bundled_runtime.runtime_root() is None
    monkeypatch.setattr(sys, 'frozen', True)
    assert bundled_runtime.runtime_root() == folder
    for text in ('{}', '[]', '{bad', '{"profile":"other","schema_version":1}'):
        (folder / 'runtime.json').write_text(text)
        assert bundled_runtime.runtime_root() is None


def test_relative_resources_overrides_and_worker_tools(tmp_path, monkeypatch):
    for name in ('FABRICA_HOME', 'HORUS_PILOT_FACTORY', 'HORUS_FFMPEG', 'HORUS_TOOLS_DIR',
                 'HORUS_KOKORO_PYTHON', 'HORUS_KOKORO_MODEL_DIR', 'HORUS_SUBTITLE_FONT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'app with spaces/horus-backend.exe'))
    root = tmp_path / 'app with spaces/first-video'
    root.mkdir(parents=True)
    (root / 'runtime.json').write_text(json.dumps({'profile': 'first-video', 'schema_version': 1}))
    for relative in ('tools/ffmpeg.exe', 'tools/node.exe', 'factory/scripts/.venv/Scripts/python.exe',
                     'kokoro/config.json', 'fonts/ArchivoExpanded-SemiBold.ttf'):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    settings = Settings(token='test', data_dir=tmp_path / 'data')
    assert settings.factory_root == root / 'factory'
    assert settings.ffmpeg == root / 'tools/ffmpeg.exe'
    assert settings.kokoro_python == root / 'factory/scripts/.venv/Scripts/python.exe'
    assert settings.kokoro_model_dir == root / 'kokoro'
    assert settings.subtitle_font == root / 'fonts/ArchivoExpanded-SemiBold.ttf'
    assert settings.command()[0] == str(settings.kokoro_python)
    assert find_tool('ffmpeg') == str(settings.ffmpeg)
    env = job_environment(FactoryRequest(operation='factory.profile'), settings)
    assert env['PATH'].split(os.pathsep)[0] == str(root / 'tools')
    assert 'HORUS_API_TOKEN' not in env
    assert bundled_runtime.resource('../outside') is None
    monkeypatch.setenv('HORUS_FFMPEG', str(tmp_path / 'explicit-override.exe'))
    assert Settings(token='test').ffmpeg == tmp_path / 'explicit-override.exe'
    assert find_tool('ffmpeg') is None  # Não substituir configuração explícita inválida silenciosamente.
