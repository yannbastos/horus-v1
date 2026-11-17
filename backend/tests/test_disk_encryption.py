"""HOR-119: estado da criptografia de disco no diagnóstico, somente leitura e sem administrador. Tudo com simulações."""
import subprocess
from pathlib import Path

import pytest

from horus_backend import disk_encryption as de
from horus_backend.diagnostics import diagnose
from support_d import auth_headers


def status(raw, data_dir='C:/dados'):
    return de.disk_encryption_status(data_dir, runner=(raw if callable(raw) else lambda letter: raw))


@pytest.mark.parametrize('raw,expected,protection', [('1', 'encrypted', 'on'), ('2', 'not_encrypted', 'off'),
                                                     ('0', 'unverified', 'unknown'), ('3', 'unverified', 'unknown'),
                                                     ('none', 'unverified', 'unknown'), ('', 'unverified', 'unknown')])
def test_value_mapping_only_trusts_on_and_off(raw, expected, protection):
    result = status(raw, 'C:/dados')
    assert (result['status'], result['protection'], result['method']) == (expected, protection, None)
    assert result['volume'] == 'C:' and result['guidance'] and 'C:/dados' not in str(result)


@pytest.mark.parametrize('error', [OSError('negado'), subprocess.TimeoutExpired('x', 8), subprocess.CalledProcessError(1, 'x')])
def test_failure_or_elevation_requirement_degrades_to_unverified_with_guidance(error):
    def boom(letter):
        raise error
    result = status(boom)
    assert result['status'] == 'unverified' and 'administrador' in result['detail'] and 'BitLocker' in result['guidance']


def test_guidance_explains_what_why_how_and_home_edition():
    text = de.GUIDANCE
    for needle in ('O que é', 'Por que importa', 'Como ativar', 'Criptografia de dispositivo', 'Windows Home não tem BitLocker',
                   'chave de recuperação', 'não altera a criptografia'):
        assert needle in text


def test_volume_without_drive_letter_is_unverified_and_never_runs_the_query(monkeypatch):
    monkeypatch.setattr(de, '_query', lambda letter: pytest.fail('não deve consultar'))
    assert de.disk_encryption_status('//servidor/compartilhamento/dados')['status'] == 'unverified'


def test_real_query_uses_run_sync_with_fixed_read_only_command_and_validated_letter(monkeypatch):
    calls = []

    def fake(command, *, timeout, **kwargs):
        calls.append((command, timeout, kwargs))
        return b'1\r\n'
    monkeypatch.setattr(de, 'run_sync', fake)
    monkeypatch.undo()  # restaura o `_query` simulado pelo conftest sem perder o patch abaixo
    monkeypatch.setattr(de, 'run_sync', fake)
    assert de._query('D:') == '1'
    command, timeout, kwargs = calls[0]
    script = ' '.join(command)
    assert command[0].lower().endswith('powershell.exe') and '-NoProfile' in command and timeout <= 10 and 'env' not in kwargs
    assert 'System.Volume.BitLockerProtection' in script and "NameSpace('D:" + chr(92) + "')" in script
    for forbidden in ('Get-BitLockerVolume', 'manage-bde', 'Win32_EncryptableVolume', 'Enable-', 'Disable-', 'Suspend-', 'Set-'):
        assert forbidden not in script


def test_result_is_cached_briefly_for_the_real_query(monkeypatch, tmp_path):
    monkeypatch.setattr(de.os, 'name', 'nt')
    monkeypatch.setattr(de, '_drive_letter', lambda d: 'Z:')
    calls = []
    monkeypatch.setattr(de, '_query', lambda letter: calls.append(letter) or '2')
    assert de.disk_encryption_status(tmp_path)['status'] == 'not_encrypted'
    assert de.disk_encryption_status(tmp_path)['status'] == 'not_encrypted'
    assert calls == ['Z:']


def test_diagnostics_exposes_disk_encryption_without_affecting_production_readiness(pg_app_settings, pg_client, monkeypatch):
    monkeypatch.setattr(de, '_query', lambda letter: '2')
    monkeypatch.setattr(de, '_drive_letter', lambda d: 'C:')
    result = diagnose(pg_app_settings)
    assert result['disk_encryption']['status'] == 'not_encrypted' and result['disk_encryption']['volume'] == 'C:'
    assert 'disk_encryption' not in {c['id'] for c in result['production']['checks']}
    assert 'disk_encryption' not in result['production']['missing']
    body = pg_client.get('/v1/diagnostics', headers=auth_headers()).json()
    assert set(body['disk_encryption']) == {'status', 'protection', 'method', 'volume', 'detail', 'guidance'}
