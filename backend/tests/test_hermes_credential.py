"""HOR-120 / 7.2: token do Hermes preferindo o cofre do SO, com fallback ao arquivo, e migração explícita (cofre em memória)."""
import json
import secrets
from dataclasses import replace

import keyring
import pytest

from horus_backend import hermes_credential as hc
from horus_backend.hermes import read_connection
from test_workflows import settings  # noqa: F401

FILE_TOKEN = 'arquivo-' + secrets.token_hex(12)
VAULT_TOKEN = 'cofre-' + secrets.token_hex(12)
URL = 'http://127.0.0.1:8643'


@pytest.fixture
def config(settings, tmp_path):
    path = tmp_path / 'integrations' / 'hermes.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'base_url': URL, 'token': FILE_TOKEN}), encoding='utf-8')
    return replace(settings, hermes_config=path), path


def vault_put(path, token):
    keyring.set_password(hc.SERVICE, hc.account_for(path), token)


def vault_get(path):
    return keyring.get_password(hc.SERVICE, hc.account_for(path))


def test_without_vault_the_file_token_is_used_unchanged(config):
    settings, path = config
    assert read_connection(settings) == (URL, FILE_TOKEN)


def test_vault_token_is_preferred_over_the_file(config):
    settings, path = config
    vault_put(path, VAULT_TOKEN)
    assert read_connection(settings) == (URL, VAULT_TOKEN)


def test_file_without_token_works_when_the_vault_has_it_but_fails_without(config):
    settings, path = config
    path.write_text(json.dumps({'base_url': URL}), encoding='utf-8')
    with pytest.raises(ValueError):
        read_connection(settings)
    vault_put(path, VAULT_TOKEN)
    assert read_connection(settings) == (URL, VAULT_TOKEN)


@pytest.mark.parametrize('bad', ['curto', 'com espaço e acentuação ' * 3, 'x' * 5000])
def test_invalid_vault_value_is_ignored_and_the_file_is_used(config, bad):
    settings, path = config
    vault_put(path, bad)
    assert read_connection(settings) == (URL, FILE_TOKEN)


def test_unavailable_vault_falls_back_to_the_file(config, monkeypatch):
    settings, path = config

    def boom(*args):
        raise RuntimeError('cofre indisponível ' + FILE_TOKEN)
    monkeypatch.setattr(keyring, 'get_password', boom)
    assert read_connection(settings) == (URL, FILE_TOKEN)


def test_url_is_still_validated_and_missing_file_still_raises(config, tmp_path):
    settings, path = config
    path.write_text(json.dumps({'base_url': 'http://example.com:80', 'token': FILE_TOKEN}), encoding='utf-8')
    vault_put(path, VAULT_TOKEN)
    with pytest.raises(ValueError):
        read_connection(settings)
    with pytest.raises(FileNotFoundError):
        read_connection(replace(settings, hermes_config=tmp_path / 'nao-existe.json'))


def run(path, *flags, answers=()):
    lines, replies = [], iter(answers)
    code = hc.migrate(path, apply='--apply' in flags, replace='--replace' in flags, ask=lambda _: next(replies), out=lines.append)
    return code, '\n'.join(lines)


def test_migration_defaults_to_dry_run_and_changes_nothing(config):
    settings, path = config
    before = path.read_bytes()
    code, text = run(path)
    assert code == 0 and 'Simulação' in text and 'ausente' in text
    assert vault_get(path) is None and path.read_bytes() == before
    assert FILE_TOKEN not in text and hc.account_for(path) not in text            # usuário truncado, valor nunca exibido


def test_apply_requires_the_typed_confirmation(config):
    settings, path = config
    code, text = run(path, '--apply', answers=['migrar'])
    assert code == 1 and vault_get(path) is None


def test_apply_copies_verifies_and_leaves_the_file_and_credential_untouched(config):
    settings, path = config
    before = path.read_bytes()
    code, text = run(path, '--apply', answers=['MIGRAR'])
    assert code == 0 and 'verificado' in text and FILE_TOKEN not in text
    assert vault_get(path) == FILE_TOKEN
    assert path.read_bytes() == before                                             # arquivo intacto; nada revogado
    assert read_connection(settings) == (URL, FILE_TOKEN)
    code, text = run(path, '--apply', answers=[])
    assert code == 0 and 'já tem o mesmo token' in text                            # idempotente, sem pedir confirmação


def test_existing_different_vault_token_is_never_overwritten_without_replace(config):
    settings, path = config
    vault_put(path, VAULT_TOKEN)
    code, text = run(path, '--apply', answers=['MIGRAR'])
    assert code == 1 and vault_get(path) == VAULT_TOKEN and VAULT_TOKEN not in text
    code, text = run(path, '--apply', '--replace', answers=['MIGRAR'])
    assert code == 0 and vault_get(path) == FILE_TOKEN


@pytest.mark.parametrize('content', [None, '{', '[]', json.dumps({'base_url': URL}), json.dumps({'base_url': URL, 'token': 'curto'})])
def test_invalid_or_missing_config_is_refused_without_touching_the_vault(config, content):
    settings, path = config
    if content is None:
        path.unlink()
    else:
        path.write_text(content, encoding='utf-8')
    code, text = run(path, '--apply', answers=['MIGRAR'])
    assert code == 2 and not any(service == hc.SERVICE for service, _ in keyring.get_keyring().values)


def test_vault_failure_during_apply_reports_without_changing_the_file(config, monkeypatch):
    settings, path = config
    before = path.read_bytes()

    def boom(*args):
        raise RuntimeError('falha ' + FILE_TOKEN)
    monkeypatch.setattr(keyring, 'set_password', boom)
    code, text = run(path, '--apply', answers=['MIGRAR'])
    assert code == 3 and FILE_TOKEN not in text and path.read_bytes() == before


def test_verification_mismatch_is_reported(config, monkeypatch):
    settings, path = config
    replies = iter([None, 'outro-valor-qualquer-xxxx'])          # cofre vazio antes; releitura diferente depois
    monkeypatch.setattr(keyring, 'get_password', lambda *a: next(replies))
    code, text = run(path, '--apply', answers=['MIGRAR'])
    assert code == 3 and 'não conferiu' in text


def test_cli_entrypoint_uses_dry_run_by_default(config, capsys):
    settings, path = config
    assert hc.main(['migrate', '--config', str(path)]) == 0
    assert 'Simulação' in capsys.readouterr().out
