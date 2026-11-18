"""Importação offline de fixtures, sem pesos reais, rede ou diretório operacional."""
import errno
import json
from pathlib import Path

import pytest

from horus_backend.config import Settings
from support_g import client_for, pg_transport  # noqa: F401
from horus_backend.whisper_models import ImportModelRequest, MIN_WEIGHTS, ModelConflict, WhisperModels
from horus_backend.speech_alignment import resolve_model_path


@pytest.fixture
def source(tmp_path):
    folder = tmp_path / 'source'
    folder.mkdir()
    with (folder / 'model.bin').open('wb') as stream:
        stream.truncate(MIN_WEIGHTS)
    for name in ('config.json', 'tokenizer.json'):
        (folder / name).write_text('{}', encoding='utf-8')
    (folder / 'vocabulary.txt').write_text('fixture', encoding='utf-8')
    return folder


def request(source, model='small'):
    return ImportModelRequest(model=model, source_directory=str(source), confirm_license=True)


def test_atomic_import_provenance_listing_and_explicit_removal(tmp_path, source):
    service = WhisperModels(tmp_path / 'data')
    (source / 'README.md').write_text('---\nlicense: mit\n---\nLocal model card', encoding='utf-8')
    (source / 'untrusted.py').write_text('not copied')
    assert not any(m['installed'] for m in service.list()['models'])
    result = service.install(request(source))
    assert result['managed'] and result['removable'] and result['size_bytes'] >= MIN_WEIGHTS
    assert result['license'] == {'identifier': 'MIT', 'status': 'local_declaration', 'model_card_present': True}
    assert str(source) not in json.dumps(result)
    target = tmp_path / 'data/models/small'
    assert resolve_model_path(service.root, 'small') == target
    manifest = json.loads((target / 'horus-model.json').read_text(encoding='utf-8'))
    assert manifest['source']['directory'] == str(source)
    assert len(manifest['files']['model.bin']['sha256']) == 64
    assert not (target / 'untrusted.py').exists()
    with pytest.raises(ModelConflict):
        service.install(request(source))
    assert service.remove('small') == {'model': 'small', 'removed': True}
    assert (source / 'model.bin').is_file() and not target.exists()


def test_missing_model_card_is_declared_unverified(tmp_path, source):
    result = WhisperModels(tmp_path / 'data').install(request(source))
    assert result['license']['status'] == 'declared_unverified'
    assert result['license']['model_card_present'] is False


def test_maximum_weight_size_is_enforced_without_allocating_large_fixture(tmp_path, source, monkeypatch):
    from horus_backend import whisper_models
    monkeypatch.setattr(whisper_models, 'MAX_WEIGHTS', MIN_WEIGHTS - 1)
    service = WhisperModels(tmp_path / 'data')
    with pytest.raises(ValueError, match='16 GiB'):
        service.install(request(source))
    assert not list(service.root.iterdir())


@pytest.mark.parametrize('damage', ['missing', 'empty', 'tiny', 'json', 'large_metadata', 'license', 'license_list', 'license_file'])
def test_invalid_files_sizes_and_conflicting_license_leave_no_installation(tmp_path, source, damage):
    if damage == 'missing':
        (source / 'vocabulary.txt').unlink()
    elif damage == 'empty':
        (source / 'vocabulary.txt').write_bytes(b'')
    elif damage == 'tiny':
        (source / 'model.bin').write_bytes(b'not weights')
    elif damage == 'json':
        (source / 'tokenizer.json').write_text('broken')
    elif damage == 'large_metadata':
        with (source / 'config.json').open('wb') as stream:
            stream.truncate(17 * 1024**2)
    elif damage in {'license', 'license_list'}:
        value = 'apache-2.0' if damage == 'license' else '[mit, apache-2.0]'
        (source / 'README.md').write_text(f'---\nlicense: {value}\n---\n')
    else:
        (source / 'LICENSE').write_text('Apache License Version 2.0')
    service = WhisperModels(tmp_path / 'data')
    with pytest.raises(ValueError):
        service.install(request(source))
    assert not list(service.root.iterdir())
    assert source.exists()


def test_failed_copy_cleans_staging_and_does_not_publish(tmp_path, source, monkeypatch):
    original = Path.open

    def fail(path, *args, **kwargs):
        if path.name == 'tokenizer.json' and path.parent.name.startswith('.import-'):
            raise OSError(errno.ENOSPC, 'private-path-secret')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', fail)
    service = WhisperModels(tmp_path / 'data')
    with pytest.raises(OSError):
        service.install(request(source))
    assert not list(service.root.iterdir())
    assert (source / 'model.bin').stat().st_size == MIN_WEIGHTS


def test_publication_happens_only_after_all_bytes_are_staged(tmp_path, source, monkeypatch):
    original = Path.rename
    published = []

    def publish(stage, target):
        assert not target.exists()
        assert (stage / 'horus-model.json').is_file()
        assert (stage / 'model.bin').stat().st_size == MIN_WEIGHTS
        published.append(True)
        return original(stage, target)

    monkeypatch.setattr(Path, 'rename', publish)
    WhisperModels(tmp_path / 'data').install(request(source))
    assert published == [True]


def test_legacy_and_extra_files_are_never_removed(tmp_path, source):
    service = WhisperModels(tmp_path / 'data')
    service.install(request(source))
    extra = service.root / 'small/keep.txt'
    extra.write_text('third-party work')
    with pytest.raises(ModelConflict):
        service.remove('small')
    assert extra.read_text() == 'third-party work'
    (service.root / 'small/horus-model.json').unlink()
    assert next(m for m in service.list()['models'] if m['model'] == 'small')['managed'] is False
    with pytest.raises(ModelConflict):
        service.remove('small')


def test_source_symlink_is_not_copied_and_model_root_cannot_escape(tmp_path, source):
    file = source / 'vocabulary.txt'
    file.unlink()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'vocab').write_text('keep')
    try:
        file.symlink_to(outside / 'vocab')
    except OSError:
        pytest.skip('SO não permite criar link de fixture')
    service = WhisperModels(tmp_path / 'data')
    with pytest.raises(ValueError):
        service.install(request(source))
    service.root.rmdir()
    service.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ModelConflict):
        service.install(request(source))
    assert (outside / 'vocab').read_text() == 'keep'


@pytest.fixture
def client(tmp_path, client_for):
    settings = Settings(token='whisper-model-test-token-0123456789', data_dir=tmp_path / 'data',
        factory_root=None, hermes_executable=None, hermes_config=tmp_path / 'no-hermes.json')
    with client_for(settings) as value:
        yield value


def test_api_import_list_confirm_and_busy_remove(client, source):
    payload = {'model': 'small', 'source_directory': str(source), 'confirm_license': True}
    assert client.post('/v1/transcription/models/import', json={**payload, 'confirm_license': False}).status_code == 422
    assert client.post('/v1/transcription/models/import', json=payload).status_code == 201
    assert client.post('/v1/transcription/models/import', json=payload).status_code == 409
    assert any(m['managed'] for m in client.get('/v1/transcription/models').json()['models'])
    assert client.delete('/v1/transcription/models/small').status_code == 400
    manager = client.app.state.manager
    manager.active_id = 'fixture-running'
    assert client.delete('/v1/transcription/models/small?confirm=true').status_code == 409
    manager.active_id = None
    assert client.delete('/v1/transcription/models/small?confirm=true').status_code == 200
    assert (source / 'model.bin').is_file()


def test_imported_small_is_available_for_production_alignment_without_restart(client, source, monkeypatch):
    from horus_backend.diagnostics import WindowsSapi
    monkeypatch.setattr(WindowsSapi, 'voices', lambda self: [])
    def alignment():
        report = client.get('/v1/diagnostics').json()
        return next(c for c in report['production']['checks'] if c['id'] == 'alignment_model')
    assert alignment()['available'] is False
    response = client.post('/v1/transcription/models/import', json={
        'model': 'small', 'source_directory': str(source), 'confirm_license': True})
    assert response.status_code == 201
    assert alignment()['available'] is True


def test_explicit_alignment_model_still_overrides_import_directory(tmp_path):
    settings = Settings(token='whisper-model-test-token-0123456789', data_dir=tmp_path,
                        alignment_model=tmp_path / 'selected')
    assert settings.alignment_models_dir == tmp_path / 'selected'


@pytest.mark.parametrize('directory', ['relative/path', r'\\server\share', r'C:\model:stream'])
def test_api_rejects_unsafe_source_paths(client, directory):
    response = client.post('/v1/transcription/models/import',
        json={'model': 'small', 'source_directory': directory, 'confirm_license': True})
    assert response.status_code == 400
    assert directory not in response.text


def test_api_requires_authentication(client):
    assert client.get('/v1/transcription/models', headers={'Authorization': 'Bearer wrong'}).status_code == 401


def test_api_copy_failure_never_exposes_origin_or_os_diagnostics(client, source, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, f'private-secret {source}')
    monkeypatch.setattr(WhisperModels, 'install', fail)
    response = client.post('/v1/transcription/models/import',
        json={'model': 'small', 'source_directory': str(source), 'confirm_license': True})
    assert response.status_code == 400
    assert 'private-secret' not in response.text and str(source) not in response.text
    assert (source / 'model.bin').exists()
