"""HOR-117: exclusão segura de dados LOCAIS em PostgreSQL (prévia, frase, backup verificado, quarentena, auditoria PG, restauração).

Somente HORUS_DATA_DIR temporário (tmp_path) com dados sintéticos: o fixture recusa qualquer pasta fora do diretório temporário.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import uuid

from fastapi.testclient import TestClient
import psycopg
import pytest

from horus_backend import data_deletion as dd
from horus_backend.local_data_export import verify_export
from support_d import auth_headers, comment_body, create_renderable_production, post_comment

WATCHED = {'productions', 'exports', 'production-comments', 'imports', '.quarentena'}


def tree(root: Path):
    """Arquivos de dados do usuário (exclui diretórios internos que não pertencem ao escopo observado)."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob('*'))
        if p.is_file() and p.relative_to(root).parts[0] in WATCHED
    }


@pytest.fixture
def world(pg_client, pg_app, pg_app_settings, tmp_path):
    assert Path(pg_app_settings.data_dir).resolve().is_relative_to(tmp_path.resolve())  # nunca dados operacionais
    record, sha = create_renderable_production(pg_client, pg_app, pg_app_settings.data_dir, approve=True)
    pid = record['id']
    assert post_comment(pg_client, pid, comment_body(sha)).status_code == 201
    data = Path(pg_app_settings.data_dir)
    (data / 'productions' / pid / 'extra').mkdir(parents=True, exist_ok=True)
    (data / 'productions' / pid / 'extra' / 'cena.png').write_bytes(b'png-sintetico' * 100)
    (data / 'exports' / pid / 'e1').mkdir(parents=True, exist_ok=True)
    (data / 'exports' / pid / 'e1' / 'package.zip').write_bytes(b'zip-sintetico' * 50)
    return pg_client, pid, sha, data, record


def preview(client, pid, scope='production', target=None):
    return client.post('/v1/data-deletion/preview', headers=auth_headers(), json={'scope': scope, 'id': target or pid})


def execute(client, plan, **override):
    payload = {'plan_id': plan['plan_id'], 'intent_token': plan['intent_token'], 'phrase': plan['confirmation_phrase']} | override
    return client.post('/v1/data-deletion/execute', headers=auth_headers(), json=payload)


def snapshot(data):
    return {k: v for k, v in tree(data).items() if not k.startswith('.quarentena')}


def test_preview_is_exact_read_only_and_lists_db_rows_kept_and_dependencies(world):
    client, pid, sha, data, record = world
    before = tree(data)
    result = preview(client, pid).json()
    assert tree(data) == before  # prévia não altera nada
    by_kind = {i['kind']: i for i in result['items']}
    for kind, relative in (('production_folder', f'productions/{pid}'), ('exports', f'exports/{pid}')):
        files = [p for p in (data / relative).rglob('*') if p.is_file()] if (data / relative).is_dir() else [data / relative]
        assert (by_kind[kind]['files'], by_kind[kind]['bytes']) == (len(files), sum(f.stat().st_size for f in files)), kind
        assert by_kind[kind]['path'] == relative and str(data) not in json.dumps(result)
    assert result['totals']['files'] == sum(i['files'] for i in result['items']) and result['can_execute'] and result['blockers'] == []
    assert result['confirmation_phrase'] == f'APAGAR producao {pid[:8]}'
    assert {k['table'] for k in result['db_kept']} >= {'productions', 'jobs', 'production_receipts'}
    assert any(d['kind'] == 'script' for d in result['dependencies'])


def test_wrong_phrase_wrong_token_unknown_plan_and_reuse_delete_nothing(world):
    client, pid, sha, data, record = world
    before = tree(data)
    plan = preview(client, pid).json()
    assert execute(client, plan, phrase='apagar producao ' + pid[:8]).status_code == 409
    assert execute(client, plan, intent_token='x' * 24).status_code == 409
    assert execute(client, plan, plan_id='0' * 32).status_code == 409
    assert tree(data) == before and not (data / '.quarentena').exists()
    assert execute(client, plan).status_code == 200
    assert execute(client, plan).status_code == 409  # uso único


def test_execute_makes_verified_backup_quarantines_files_audits_and_marks_production_deleted(world):
    client, pid, sha, data, record = world
    plan = preview(client, pid).json()
    original = {k: v for k, v in tree(data).items() if k.startswith((f'productions/{pid}/', f'exports/{pid}/', f'production-comments/{pid}'))}
    done = execute(client, plan)
    assert done.status_code == 200, done.text
    result = done.json()
    aid = result['audit_id']
    for relative in original:
        assert not (data / relative).exists() and (data / '.quarentena' / aid / relative).is_file()
    assert {k[len(f'.quarentena/{aid}/'):]: v for k, v in tree(data).items() if k.startswith(f'.quarentena/{aid}/')} == original
    package = data.parent / result['backup']['location'] / result['backup']['name']
    assert package.is_file() and verify_export(package)['ok'] and result['backup']['verified']
    assert hashlib.sha256(package.read_bytes()).hexdigest() == result['backup']['sha256']
    page = client.get('/v1/data-deletion/audit', headers=auth_headers()).json()
    assert page['chain_ok'] and [e['action'] for e in reversed(page['items'])] == ['started', 'completed']
    assert '"text"' not in json.dumps(page) and 'Cortar aqui' not in json.dumps(page)  # auditoria sem conteúdo
    # estado derivado da auditoria; nada oferecido para arquivos inexistentes; banco intacto
    shown = client.get(f'/v1/productions/{pid}', headers=auth_headers()).json()
    assert shown['local_data']['state'] == 'deleted' and shown['local_data']['audit_id'] == aid
    assert client.post(f'/v1/productions/{pid}/export', headers=auth_headers({'Idempotency-Key': 'x'})).status_code == 409
    assert client.get(f'/v1/productions/{pid}/exports/{"a" * 64}/file', headers=auth_headers()).status_code == 409
    assert post_comment(client, pid, comment_body(sha), key='novo').status_code == 409
    assert preview(client, pid).json()['blockers'][0]['code'] in {'already_deleted', 'nothing_to_delete'}
    with client.app.state.store.uow(readonly=True) as db:
        assert db.execute('SELECT COUNT(*) FROM horus.productions WHERE id = %s', (pid,)).fetchone()[0] == 1


def test_active_jobs_block_preview_execute_and_a_plan_made_before_the_job_started(world):
    client, pid, sha, data, record = world
    plan = preview(client, pid).json()
    client.app.state.store.update(record['job_id'], 'running')
    before = snapshot(data)
    assert execute(client, plan).status_code == 409
    blocked = preview(client, pid).json()
    assert [b['code'] for b in blocked['blockers']] == ['active_jobs'] and not blocked['can_execute']
    assert snapshot(data) == before and not (data / '.quarentena').exists()
    assert not (data.parent / f'{data.name}-backups').exists()  # recusou antes de qualquer backup


def test_files_changed_after_preview_invalidate_the_plan(world):
    client, pid, sha, data, record = world
    plan = preview(client, pid).json()
    (data / 'productions' / pid / 'novo.txt').write_text('mudou depois da prévia', encoding='utf-8')
    assert execute(client, plan).status_code == 409 and (data / 'productions' / pid / 'novo.txt').exists()


def test_backup_failure_aborts_before_any_move_and_without_audit_start(world, monkeypatch):
    client, pid, sha, data, record = world
    plan = preview(client, pid).json()
    monkeypatch.setattr(dd, 'export_local_data', lambda *a, **k: (_ for _ in ()).throw(ValueError('falha simulada')))
    before = snapshot(data)
    assert execute(client, plan).status_code == 409
    assert snapshot(data) == before and not (data / '.quarentena').exists()
    assert client.get('/v1/data-deletion/audit', headers=auth_headers()).json()['items'] == []


def test_unverified_backup_aborts(world, monkeypatch):
    client, pid, sha, data, record = world
    plan = preview(client, pid).json()
    monkeypatch.setattr(dd, 'verify_export', lambda package: {'ok': False, 'errors': ['simulado']})
    before = snapshot(data)
    response = execute(client, plan)
    assert response.status_code == 409 and 'verificação' in response.json()['detail']
    assert snapshot(data) == before
    assert client.get('/v1/data-deletion/audit', headers=auth_headers()).json()['items'] == []


def test_move_failure_rolls_back_every_item_and_audits_the_failure(world, monkeypatch):
    client, pid, sha, data, record = world
    plan = preview(client, pid).json()
    real, calls = dd.os.rename, []

    def flaky(source, destination):
        calls.append(source)
        if len(calls) == 2:
            raise OSError('falha simulada no segundo item')
        return real(source, destination)
    monkeypatch.setattr(dd.os, 'rename', flaky)
    before = snapshot(data)
    assert execute(client, plan).status_code == 409
    monkeypatch.setattr(dd.os, 'rename', real)
    assert snapshot(data) == before  # primeiro item voltou ao lugar
    actions = [e['action'] for e in reversed(client.get('/v1/data-deletion/audit', headers=auth_headers()).json()['items'])]
    assert actions == ['started', 'failed']
    assert client.get(f'/v1/productions/{pid}', headers=auth_headers()).json()['local_data']['state'] == 'present'


def test_restore_brings_files_back_verified_once_and_refuses_conflicts_or_tampering(world):
    client, pid, sha, data, record = world
    before = snapshot(data)
    done = execute(client, preview(client, pid).json()).json()
    aid = done['audit_id']
    # conflito no destino: recusa e não altera
    (data / 'exports' / pid).mkdir(parents=True)
    assert client.post(f'/v1/data-deletion/{aid}/restore', headers=auth_headers()).status_code == 409
    shutil.rmtree(data / 'exports' / pid)
    # quarentena alterada: recusa
    victim = data / '.quarentena' / aid / f'productions/{pid}/extra/cena.png'
    original = victim.read_bytes()
    victim.write_bytes(original + b'adulterado')
    assert client.post(f'/v1/data-deletion/{aid}/restore', headers=auth_headers()).status_code == 409
    victim.write_bytes(original)
    restored = client.post(f'/v1/data-deletion/{aid}/restore', headers=auth_headers())
    assert restored.status_code == 200, restored.text
    assert snapshot(data) == before and client.get(f'/v1/productions/{pid}', headers=auth_headers()).json()['local_data']['state'] == 'present'
    assert client.post(f'/v1/data-deletion/{aid}/restore', headers=auth_headers()).status_code == 409  # só uma vez
    assert client.post('/v1/data-deletion/' + '0' * 32 + '/restore', headers=auth_headers()).status_code == 404
    assert client.post('/v1/data-deletion/xyz/restore', headers=auth_headers()).status_code == 404
    again = preview(client, pid).json()
    assert again['can_execute']  # pode excluir de novo
    assert [e['action'] for e in reversed(client.get('/v1/data-deletion/audit', headers=auth_headers()).json()['items'])] == ['started', 'completed', 'restored']


def test_audit_is_hash_chained_and_tampering_blocks_new_deletions(world, pg_app_cluster, pg_app_database):
    client, pid, sha, data, record = world
    execute(client, preview(client, pid).json())
    # 1. Papel operacional `horus_app` não consegue alterar nem apagar `horus.deletion_events`
    with pytest.raises(psycopg.Error):
        with client.app.state.store.uow() as conn:
            conn.execute("UPDATE horus.deletion_events SET target = %s", ('production:' + 'f' * 32,))

    # 2. Adulteração simulada via conexão administrativa (desabilitando trigger temporariamente)
    with pg_app_cluster.connect(pg_app_database, autocommit=True) as admin:
        admin.execute("ALTER TABLE horus.deletion_events DISABLE TRIGGER USER")
        try:
            admin.execute("UPDATE horus.deletion_events SET hash = repeat('0', 64) WHERE seq = 1")
        finally:
            admin.execute("ALTER TABLE horus.deletion_events ENABLE TRIGGER USER")

    assert client.get('/v1/data-deletion/audit', headers=auth_headers()).json()['chain_ok'] is False
    other = preview(client, pid).json()
    assert any(b['code'] == 'audit_unreadable' for b in other['blockers'])
    assert execute(client, other).status_code == 409


def test_backups_are_never_deleted_automatically_across_cycles(world):
    client, pid, sha, data, record = world
    first = execute(client, preview(client, pid).json()).json()
    client.post(f"/v1/data-deletion/{first['audit_id']}/restore", headers=auth_headers())
    second = execute(client, preview(client, pid).json()).json()
    folder = data.parent / f'{data.name}-backups'
    assert {p.name for p in folder.iterdir()} == {first['backup']['name'], second['backup']['name']}


def test_invalid_requests_and_authentication(world):
    client, pid, sha, data, record = world
    assert client.post('/v1/data-deletion/preview', headers=auth_headers(), json={'scope': 'tudo', 'id': pid}).status_code == 422
    assert client.post('/v1/data-deletion/preview', headers=auth_headers(), json={'scope': 'production', 'id': '../x'}).status_code == 422
    assert client.post('/v1/data-deletion/preview', headers=auth_headers(), json={'scope': 'production', 'id': pid, 'force': True}).status_code == 422
    assert preview(client, pid, target='1' * 32).status_code == 404
    assert preview(client, pid, scope='import', target='1' * 32).status_code == 404
    anonymous = TestClient(client.app, base_url='http://127.0.0.1')
    assert anonymous.post('/v1/data-deletion/preview', json={'scope': 'production', 'id': pid}).status_code == 401
    assert anonymous.get('/v1/data-deletion/audit').status_code == 401


@pytest.fixture
def imported(tmp_path, pg_client, pg_app, pg_app_settings):
    record, _ = create_renderable_production(pg_client, pg_app, pg_app_settings.data_dir, approve=True)
    pid = record['id']
    created = pg_client.post(
        f'/v1/productions/{pid}/export',
        headers=auth_headers({'Idempotency-Key': f'exp-{uuid.uuid4().hex}'}),
    )
    assert created.status_code == 201, created.text
    data = pg_client.get(
        f"/v1/productions/{pid}/exports/{created.json()['id']}/file",
        headers=auth_headers(),
    ).content
    package = tmp_path / 'pacote' / 'horus.zip'
    package.parent.mkdir()
    package.write_bytes(data)
    imp = pg_client.post(
        '/v1/imports',
        headers=auth_headers({'Idempotency-Key': f'imp-{uuid.uuid4().hex}'}),
        json={'path': str(package)},
    )
    assert imp.status_code == 201, imp.text
    return pg_client, imp.json()['id'], Path(pg_app_settings.data_dir), record


def test_imports_can_be_deleted_hidden_and_restored(imported):
    client, import_id, data, record = imported
    plan = preview(client, import_id, scope='import').json()
    assert plan['can_execute'] and plan['db_kept'] == [] and plan['confirmation_phrase'] == f'APAGAR importacao {import_id[:8]}'
    assert plan['items'][0]['path'] == f'imports/{import_id}' and plan['items'][0]['files'] >= 1
    done = execute(client, plan)
    assert done.status_code == 200, done.text
    assert not (data / 'imports' / import_id).exists()
    assert client.get(f'/v1/imports/{import_id}', headers=auth_headers()).status_code == 404
    assert import_id not in json.dumps(client.get('/v1/imports', headers=auth_headers()).json())
    assert client.post(f"/v1/data-deletion/{done.json()['audit_id']}/restore", headers=auth_headers()).status_code == 200
    assert client.get(f'/v1/imports/{import_id}', headers=auth_headers()).status_code == 200
