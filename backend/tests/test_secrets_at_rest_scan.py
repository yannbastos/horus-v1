"""HOR-114: varredura de segredos em texto puro (diretório de dados, banco, logs, respostas, exports e pacotes).

Os segredos são gerados no teste: token local aleatório (como o `uuid4` duplo do Tauri), chave ElevenLabs e token do Hermes
sintéticos. A varredura procura o valor exato (UTF-8 e UTF-16LE) e os padrões de credencial de `local_data_export`. Se algum
fluxo passar a gravar um segredo fora do lugar esperado (cofre do SO em memória aqui; `integrations/` para o Hermes), o teste
falha. Sem rede, sem Hermes real, sem chaves reais.
"""
import hashlib
import json
import secrets
import tempfile
import time
import zipfile
from dataclasses import replace
from pathlib import Path

import keyring
import pytest

from horus_backend.local_data_export import SECRET_SHAPE, export_local_data, restore_local_data
from horus_backend.processes import sanitized_tail
from horus_backend.production import inspect_artifact
from support_b import (
    approved_simulated_workflow,
    client_for,  # noqa: F401
    decide_script,
    generate_script,
    pg_admin,  # noqa: F401
    pg_store,  # noqa: F401
    settings,  # noqa: F401
)

ELEVEN = 'xi-sintetica-' + secrets.token_hex(16)
HERMES = 'hermes-sintetico-' + secrets.token_hex(16)


def needles(*values):
    return [v.encode(enc) for v in values for enc in ('utf-8', 'utf-16-le')]


def hits_in(name, raw: bytes, values, *, shape=True):
    found = [f'{name}: segredo exato' for n in needles(*values) if n in raw]
    if shape and SECRET_SHAPE.search(raw.decode('utf-8', 'replace')):
        found.append(f'{name}: formato de credencial')
    return found


def scan_tree(root: Path, values, *, skip=('integrations',)):
    found = []
    for path in sorted(p for p in root.rglob('*') if p.is_file()):
        relative = path.relative_to(root)
        if relative.parts[0] in skip or path.name.endswith('.lock'):   # a trava do executor fica bloqueada e só contém '0'
            continue
        found += hits_in(relative.as_posix(), path.read_bytes(), values, shape=path.suffix in {'.json', '.txt', '.log', '.md', '.srt'})
    return found


def scan_package(package: Path, values):
    found = []
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            found += hits_in(f'{package.name}:{info.filename}', archive.read(info), values, shape=info.filename.endswith('.json'))
    return found


def secrets_of(settings):
    return [settings.token, ELEVEN, HERMES]


@pytest.fixture
def production(settings, client_for, pg_admin):
    with client_for(settings) as client:
        workflow = approved_simulated_workflow(client)
        script = generate_script(client, workflow).json()
        assert decide_script(client, script).status_code == 200
        started = client.post(
            f"/v1/scripts/{script['id']}/productions",
            headers={'Idempotency-Key': 'production'},
            json={'expected_version': script['version'], 'mode': 'simulated'},
        )
        assert started.status_code in (201, 202), started.text
        pid = started.json()['id']
        record = None
        for _ in range(200):
            item = client.get(f'/v1/productions/{pid}').json()
            if item['status'] not in {'queued', 'running', 'cancel_requested'}:
                record = item
                break
            time.sleep(0.025)
        assert record is not None and record['status'] == 'succeeded', record
        root = settings.data_dir / 'productions' / record['id']
        (root / 'video.mp4').write_bytes(b'\x00\x00\x00\x18ftyp' + b'fixture-video')
        artifacts = record['artifacts'] + [inspect_artifact(root, 'video.mp4', 'video')]
        scene = next(a for a in artifacts if a['kind'] == 'scene')
        provenance = {
            'schema_version': 1,
            'assets': [
                {
                    'asset_id': ('scene-' if a['kind'] == 'scene' else 'audio-') + str(index),
                    'relative_path': a['relative_path'],
                    'sha256': a['sha256'],
                    'kind': 'image' if a['kind'] == 'scene' else 'audio',
                    'origin': 'original_local',
                    'source_url': None,
                    'author': None,
                    'license': {
                        'id': None,
                        'name': None,
                        'url': None,
                        'commercial_use': 'unknown',
                        'attribution_required': None,
                        'verified_by': None,
                        'verified_at': None,
                    },
                    'status': 'unverified',
                    'notes': '',
                }
                for index, a in enumerate([a for a in artifacts if a['kind'] in {'scene', 'audio'}], start=1)
            ],
        }
        (root / 'asset-provenance.json').write_text(json.dumps(provenance), encoding='utf-8')
        artifacts.append(inspect_artifact(root, 'asset-provenance.json', 'manifest'))
        pg_admin.execute(
            "UPDATE horus.productions SET mode = 'local_pilot', artifacts = %s::jsonb WHERE id = %s",
            (json.dumps(artifacts), record['id']),
        )
        asset = next(a for a in provenance['assets'] if a['sha256'] == scene['sha256'])
        license_payload = {
            'asset_id': asset['asset_id'],
            'asset_sha256': scene['sha256'],
            'decision': 'verified',
            'license': {
                'id': 'own',
                'name': 'Obra própria',
                'url': 'https://example.org/termos',
                'commercial_use': 'allowed',
                'attribution_required': False,
            },
            'evidence': 'Termos conferidos pelo operador.',
            'reviewed_by': 'human',
        }
        response = client.post(
            f"/v1/productions/{record['id']}/license-reviews",
            headers={'Idempotency-Key': 'license'},
            json=license_payload,
        )
        assert response.status_code == 201, response.text
        (root / 'private.env').write_text('TOKEN=' + settings.token, encoding='utf-8')
        yield client, record, root, settings


def approve(client, record):
    response = client.post(
        f"/v1/productions/{record['id']}/review",
        headers={'Idempotency-Key': 'approval'},
        json={
            'decision': 'approve',
            'artifact_sha256': hashlib.sha256(b'\x00\x00\x00\x18ftypfixture-video').hexdigest(),
            'acknowledge_quality_errors': True,
        },
    )
    assert response.status_code == 200, response.text


def export(client, pid, key='export'):
    return client.post(f'/v1/productions/{pid}/export', headers={'Idempotency-Key': key})


def test_scanner_detects_planted_secrets_in_every_surface(tmp_path):
    """Controle positivo: sem ele, 'nenhum achado' não provaria nada."""
    values = [secrets.token_hex(24)]
    tree = tmp_path / 'dados'
    tree.mkdir()
    (tree / 'texto.json').write_text(json.dumps({'k': values[0]}), encoding='utf-8')
    (tree / 'utf16.txt').write_bytes(values[0].encode('utf-16-le'))
    (tree / 'b.bin').write_bytes(b'\x00\x01' + values[0].encode('utf-8') + b'\x02\x03')
    (tree / 'forma.json').write_text('{"api_key": "' + 'A' * 24 + '"}', encoding='utf-8')
    found = scan_tree(tree, values)
    assert {f.split(':')[0] for f in found} == {'texto.json', 'utf16.txt', 'b.bin', 'forma.json'}
    package = tmp_path / 'p.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('x.json', json.dumps({'t': values[0]}))
    assert scan_package(package, values)
    (tree / 'integrations').mkdir()
    (tree / 'integrations' / 'hermes.json').write_text(values[0], encoding='utf-8')
    assert not [f for f in scan_tree(tree, values) if f.startswith('integrations')]   # o local esperado é ignorado só nele


def test_no_plaintext_secret_in_data_dir_database_logs_responses_exports_and_packages(production, pg_admin, pg_store, tmp_path, isolated_keyring, caplog, capfd):
    client, record, root, settings = production
    (root / 'private.env').unlink()                     # a fixture planta um .env só para provar a exclusão do export
    keyring_store = isolated_keyring                 # conftest.py: cofre em memória (o real do SO é proibido nos testes)
    assert keyring.get_keyring() is keyring_store
    caplog.set_level('DEBUG')
    hermes_file = settings.hermes_config            # fora do diretório de dados (caminho do fixture)
    hermes_file.write_text(json.dumps({'base_url': 'http://127.0.0.1:8643', 'token': HERMES}), encoding='utf-8')
    values = secrets_of(settings)
    responses = []
    live = client
    if True:
        responses.append(live.put('/v1/narration/providers/elevenlabs/credential', json={'key': ELEVEN}).text)
        responses.append(live.put('/v1/narration/providers/elevenlabs/credential', json={'key': ELEVEN, 'extra': 1}).text)  # 422
        responses.append(live.get('/v1/narration/providers').text)
        responses.append(live.get('/v1/hermes').text)
        responses.append(live.get('/v1/security/status').text)
        responses.append(live.get('/v1/media').text)
        responses.append(live.get('/v1/productions/' + record['id']).text)
        responses.append(live.get('/v1/jobs').text)
        bad = live.get('/v1/jobs', headers={'Authorization': 'Bearer ' + ELEVEN})    # 401 com chave de provedor como "token"
        assert bad.status_code == 401
        responses.append(bad.text)
        approve(live, record)
        exported = export(live, record['id'])
        assert exported.status_code in (200, 201), exported.text
        responses.append(exported.text)
    assert keyring_store.values and ELEVEN in keyring_store.values.values()      # o cofre (aqui, memória) é o único lugar da chave
    found = []
    # 1) diretório de dados, banco PostgreSQL e exports de produção gravados nele
    assert list(settings.data_dir.rglob('*.sqlite*')) == []
    assert list(settings.data_dir.rglob('*.db')) == []
    found += scan_tree(settings.data_dir, values)
    tables = [r[0] for r in pg_admin.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'horus'").fetchall()]
    for table in tables:
        dump = json.dumps(pg_admin.execute(f'SELECT * FROM horus."{table}"').fetchall(), default=str).encode()
        found += hits_in(f'pg:{table}', dump, values)
    for package in settings.data_dir.rglob('*.zip'):
        found += scan_package(package, values)
    # 2) respostas da API, logs do Python e saída do processo
    found += hits_in('respostas', '\n'.join(responses).encode(), values, shape=False)
    found += hits_in('caplog', caplog.text.encode(), values)
    out = capfd.readouterr()
    found += hits_in('stdout/stderr', (out.out + out.err).encode(), values)
    # 3) pacote de dados locais e restauração em pasta nova
    package = tmp_path / 'dados.zip'
    assert export_local_data(settings.data_dir, package, store=pg_store)['ok']
    found += scan_package(package, values)
    restored = tmp_path / 'restaurado'
    restore_local_data(package, restored)
    found += scan_tree(restored, values)
    assert found == []
    # 4) nenhuma pasta temporária de exportação/verificação ficou para trás
    leftovers = [p for p in Path(tempfile.gettempdir()).glob('horus-*') if p.name.startswith(('horus-export-', 'horus-verify-'))]
    assert leftovers == []


def test_hermes_connection_file_is_the_only_plaintext_location_and_is_never_exported(tmp_path, settings, client_for, pg_store):
    """O token do Hermes fica em texto puro em `integrations/hermes.json` (lacuna documentada); nada mais o contém."""
    (settings.data_dir / 'integrations').mkdir(parents=True)
    private = settings.data_dir / 'integrations' / 'hermes.json'
    private.write_text(json.dumps({'base_url': 'http://127.0.0.1:8643', 'token': HERMES}), encoding='utf-8')
    with client_for(replace(settings, hermes_config=private)) as client:
        assert client.get('/v1/hermes').status_code == 200
        client.get('/v1/security/status')
    assert HERMES in private.read_text(encoding='utf-8')
    assert scan_tree(settings.data_dir, [HERMES, settings.token]) == []
    package = tmp_path / 'dados.zip'
    report = export_local_data(settings.data_dir, package, store=pg_store)
    assert 'integrations/' in {e['path'] for e in report['excluded']}
    assert scan_package(package, [HERMES]) == []


@pytest.mark.parametrize('leak', ['Authorization: Bearer {t}', 'token={t}', '{{"api_key": "{t}"}}', 'ELEVENLABS_API_KEY={t}'])
def test_diagnostic_tail_redacts_tokens_before_they_can_be_persisted(leak):
    token = secrets.token_hex(24)
    assert token not in sanitized_tail(leak.format(t=token))
