"""HOR-159: transporte e motores falsos; nenhuma conexão ou modelo real."""
import io
import os
import socket
import sys
from email.message import Message
from types import SimpleNamespace
from urllib.request import Request
from urllib.response import addinfourl

import pytest
from pydantic import ValidationError

from horus_backend import egress, whisper_download as download, transcriber, speech_alignment
from horus_backend.config import Settings
from horus_backend.jobs import TranscriptionRequest, job_environment
from horus_backend.transmissions import TransmissionLog
from support_b import pg_store, query_rows  # noqa: F401


@pytest.fixture
def gate(pg_store, monkeypatch):
    audit = TransmissionLog(pg_store)
    gateway = egress.EgressGateway(audit=audit, resolver=lambda h, p: ['93.184.216.34'])
    monkeypatch.setattr(egress, '_gateway', gateway)
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('Rede real proibida'))
    return gateway, audit


def rows(audit):
    return query_rows(audit.store, 'SELECT * FROM horus.transmissions ORDER BY seq')


def fake_http(monkeypatch, responses):
    calls = []
    def do_open(self, connection, req, **kwargs):
        calls.append((connection, req))
        status, location = responses.pop(0)
        headers = Message()
        if location:
            headers['Location'] = location
        response = addinfourl(io.BytesIO(b'fixture'), headers, req.full_url, status)
        response.msg = 'simulated'
        return response
    monkeypatch.setattr(download.PinnedHTTPSHandler, 'do_open', do_open)
    return calls


def test_allowed_download_is_audited_and_pins_the_resolved_ip(gate, monkeypatch):
    gateway, audit = gate
    resolutions = []
    gateway.resolver = lambda h, p: resolutions.append((h, p)) or ['93.184.216.34']
    calls = fake_http(monkeypatch, [(200, None)])
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    with download.open_download('https://huggingface.co/model?public=value') as response:
        assert response.read() == b'fixture'
    assert len(calls) == 1 and resolutions == [('huggingface.co', 443)]
    connected = []
    sock = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(download.socket, 'create_connection', lambda address, **kw: connected.append(address) or sock)
    tls = SimpleNamespace(wrap_socket=lambda value, **kw: connected.append(kw['server_hostname']) or value)
    monkeypatch.setattr(download.ssl, 'create_default_context', lambda: tls)
    calls[0][0]('huggingface.co', timeout=10).connect()
    assert connected == [('93.184.216.34', 443), 'huggingface.co']
    records = rows(audit)
    assert records[-1]['result'] == 'attempted'
    assert records[-1]['stage'] == 'egress:whisper_model_download'
    assert 'public=value' not in str(records)


@pytest.mark.parametrize('target,private', [
    ('https://evil.test/model', False), ('https://127.0.0.1/model', False),
    ('https://cdn-lfs.huggingface.co/model', True),
])
def test_redirect_is_reauthorized_and_private_dns_is_refused(gate, monkeypatch, target, private):
    gateway, audit = gate
    gateway.resolver = lambda host, port: ['10.0.0.2'] if private and host.startswith('cdn-') else ['93.184.216.34']
    calls = fake_http(monkeypatch, [(302, target)])
    with pytest.raises(egress.EgressBlocked):
        download.open_download('https://huggingface.co/model')
    assert len(calls) == 1
    assert rows(audit)[-1]['result'] == 'policy_violation'
    assert rows(audit)[-1]['detail'] == ('private_address' if private else 'destination_not_allowed')


def test_allowed_redirect_audits_each_hop(gate, monkeypatch):
    _, audit = gate
    calls = fake_http(monkeypatch, [(302, 'https://cas-bridge.xethub.hf.co/model'), (200, None)])
    with download.open_download('https://huggingface.co/model') as response:
        assert response.read() == b'fixture'
    assert len(calls) == 2
    assert len([r for r in rows(audit) if r['result'] == 'attempted']) == 2


def test_unknown_host_is_refused_and_audited_before_connection(gate):
    _, audit = gate
    with pytest.raises(egress.EgressBlocked):
        download.open_download('https://evil.test/model?secret=redacted')
    assert rows(audit)[-1]['result'] == 'policy_violation'
    assert 'redacted' not in str(rows(audit))


@pytest.mark.parametrize('purpose', ['hermes', 'publishing', 'youtube_download', 'radar_youtube'])
def test_whisper_rule_does_not_authorize_other_stages(gate, purpose):
    gateway, _ = gate
    with pytest.raises(egress.EgressBlocked):
        gateway.authorize(purpose, 'GET', 'https://huggingface.co/model')


@pytest.mark.parametrize('engine', ['transcription', 'alignment'])
def test_model_name_without_local_files_never_reaches_hub(gate, tmp_path, monkeypatch, engine):
    monkeypatch.delenv('HORUS_WHISPER_MODELS_DIR', raising=False)
    for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY'):
        monkeypatch.setenv(name, '0')
    monkeypatch.setenv('HF_ENDPOINT', 'https://evil.test')
    monkeypatch.setitem(sys.modules, 'ctranslate2', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(
        WhisperModel=lambda *a, **kw: pytest.fail('Modelo ausente não pode chegar ao Hub')))
    if engine == 'transcription':
        monkeypatch.setenv('HORUS_WHISPER_DEVICE', 'cpu')
        with pytest.raises(FileNotFoundError, match='Instale-o explicitamente'):
            transcriber._transcribe(tmp_path / 'audio.wav', 'tiny', 'pt', tmp_path, False)
    else:
        with pytest.raises(ValueError, match='Modelo local ausente'):
            speech_alignment._recognize_words(tmp_path / 'audio.wav', tmp_path / 'tiny', 'pt')
    assert os.environ['HF_HUB_OFFLINE'] == os.environ['TRANSFORMERS_OFFLINE'] == '1'


def test_payload_and_inherited_environment_cannot_enable_model_network(tmp_path, monkeypatch):
    for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY'):
        monkeypatch.setenv(name, '0')
    monkeypatch.setenv('HF_ENDPOINT', 'https://evil.test')
    request = dict(operation='transcription.run', path='audio.wav', model='tiny')
    for override in ({'local_files_only': False}, {'env': {'HF_HUB_OFFLINE': '0'}}, {'HF_HUB_OFFLINE': '0'}):
        with pytest.raises(ValidationError):
            TranscriptionRequest(**request, **override)
    env = job_environment(TranscriptionRequest(**request), Settings(token='test-whisper-offline-token-012345', data_dir=tmp_path))
    assert env['HF_HUB_OFFLINE'] == env['TRANSFORMERS_OFFLINE'] == env['HF_HUB_DISABLE_TELEMETRY'] == '1'
    assert 'HF_ENDPOINT' not in env

@pytest.mark.parametrize('address', ['127.0.0.1', '::1', '169.254.169.254', '::ffff:10.0.0.1'])
def test_initial_private_dns_is_refused_before_transport(gate, monkeypatch, address):
    gateway, audit = gate
    gateway.resolver = lambda *args: [address]
    fake_http(monkeypatch, [])
    with pytest.raises(egress.EgressBlocked, match='Endereço privado'):
        download.open_download('https://huggingface.co/model')
    assert rows(audit)[-1]['detail'] == 'private_address'


def test_environment_rule_cannot_widen_whisper_transport(gate, monkeypatch):
    gateway, audit = gate
    monkeypatch.setenv('HORUS_EGRESS_ALLOW', 'whisper_model_download=https://evil.test')
    gateway.rules += egress.parse_rules(os.environ['HORUS_EGRESS_ALLOW'])
    fake_http(monkeypatch, [])
    with pytest.raises(ValueError, match='Origem de download não permitida'):
        download.open_download('https://evil.test/model')
    assert rows(audit)[-1]['result'] == 'policy_violation'


def test_whisper_policy_matches_fixed_transport_hosts():
    rules = [rule for rule in egress.DEFAULT_RULES if rule.purpose == 'whisper_model_download']
    assert {rule.host for rule in rules} == download.HOSTS
    assert all(rule.scheme == 'https' and rule.methods == {'GET'} and not rule.allow_auth and not rule.allow_loopback
               for rule in rules)
