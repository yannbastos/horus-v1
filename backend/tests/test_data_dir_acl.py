"""HOR-120 / 7.3: ACL restritiva da pasta de dados, testada SOMENTE em pasta isolada temporária (tmp_path).

Nunca toca a pasta de dados real: o script recusa essa pasta sem --allow-real e os testes só usam diretórios criados aqui.
Windows apenas (icacls)."""
import importlib.util
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='icacls: somente Windows')

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'data-dir-acl.py'
spec = importlib.util.spec_from_file_location('data_dir_acl', SCRIPT)
acl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acl)


@pytest.fixture
def tmp_path():
    """Pasta temporária ISOLADA com a ACL herdada padrão do Windows. O `tmp_path` do pytest nasce com ACL protegida
    (explícita, só dono/SYSTEM/administradores) e não representa a pasta de dados real, que herda da pasta do usuário."""
    base = Path(tempfile.gettempdir()) / f'horus-acl-teste-{uuid.uuid4().hex}'
    base.mkdir()
    yield base
    for _ in range(5):
        shutil.rmtree(base, ignore_errors=True)
        if not base.exists():
            break
        time.sleep(.2)


@pytest.fixture
def folder(tmp_path):
    data = tmp_path / 'dados'
    (data / 'productions' / 'p1').mkdir(parents=True)
    (data / 'productions' / 'p1' / 'plan.json').write_text('{}', encoding='utf-8')
    (data / 'nota.txt').write_text('antes', encoding='utf-8')
    return data


def principals(path):
    return sorted((e['principal'], tuple(e['flags'])) for e in acl.read_acl(path))


def run(function, *args, answers=(), **kwargs):
    lines, replies = [], iter(answers)
    code = function(*args, ask=lambda _: next(replies), out=lines.append, **kwargs) if function is acl.apply else function(*args, out=lines.append, **kwargs)
    return code, '\n'.join(lines)


def test_default_plan_shows_acl_and_exact_commands_and_changes_nothing(folder):
    before = principals(folder)
    code, text = run(acl.plan, folder)
    assert code == 0 and 'Simulação' in text and '/inheritance:r' in text and '/grant:r' in text and 'Real: não' in text
    assert principals(folder) == before and any(e['inherited'] for e in acl.read_acl(folder))


def test_apply_without_flag_is_a_plan_and_wrong_confirmation_changes_nothing(folder):
    before = principals(folder)
    code, text = run(acl.apply, folder, confirm=False)
    assert code == 0 and 'Simulação' in text and principals(folder) == before
    code, text = run(acl.apply, folder, confirm=True, answers=['aplicar acl'])
    assert code == 1 and principals(folder) == before and not (folder.parent / 'dados-acl-backup').exists()


def test_apply_leaves_only_current_user_and_system_keeps_working_access_and_restores(folder):
    original = principals(folder)
    code, text = run(acl.apply, folder, confirm=True, answers=['APLICAR ACL'])
    assert code == 0, text
    now = acl.read_acl(folder)
    assert len(now) == 2 and all(not e['inherited'] for e in now)                  # sem herança, só duas ACEs
    sid = acl.current_user_sid()
    assert acl.has_sid(folder, sid) and acl.has_sid(folder, acl.SYSTEM_SID)
    # o usuário continua trabalhando: cria, lê, altera e apaga arquivos e subpastas; filhos herdam só as duas ACEs
    (folder / 'novo.txt').write_text('depois', encoding='utf-8')
    (folder / 'sub').mkdir()
    (folder / 'sub' / 'a.txt').write_text('a', encoding='utf-8')
    assert (folder / 'nota.txt').read_text(encoding='utf-8') == 'antes'
    for child in (folder / 'nota.txt', folder / 'novo.txt', folder / 'productions' / 'p1' / 'plan.json'):
        entries = acl.read_acl(child)
        assert len(entries) == 2 and all(e['inherited'] for e in entries), child
    (folder / 'sub' / 'a.txt').unlink()
    (folder / 'sub').rmdir()
    # a ACL anterior foi guardada FORA da pasta de dados
    backups = list((folder.parent / 'dados-acl-backup').glob('acl-*.json'))
    assert len(backups) == 1 and not str(backups[0]).startswith(str(folder) + os.sep)
    record = json.loads(backups[0].read_text(encoding='utf-8'))
    assert sorted((e['principal'], tuple(e['flags'])) for e in record['original']) == original
    # restauração: a ACL volta exatamente à anterior (tudo herdado) e os filhos acompanham
    code, text = run(acl.restore, folder, backups[0])
    assert code == 0, text
    assert principals(folder) == original
    assert all(e['inherited'] for e in acl.read_acl(folder / 'nota.txt'))
    (folder / 'depois-da-restauracao.txt').write_text('ok', encoding='utf-8')


def test_apply_twice_is_refused_as_already_customized_and_nothing_breaks(folder):
    assert run(acl.apply, folder, confirm=True, answers=['APLICAR ACL'])[0] == 0
    code, text = run(acl.apply, folder, confirm=True, answers=['APLICAR ACL'])
    assert code == 1 and 'personalizada' in text and len(acl.read_acl(folder)) == 2


def test_backups_keep_working_with_the_restricted_acl(folder, tmp_path, pg_app_cluster, pg_app_database, monkeypatch):
    """Pacote de pré-exclusão/exportação: lê a pasta restrita e grava na pasta irmã de backups; restauração em pasta nova.
    O dump agora é lógico PostgreSQL (sem arquivo `.db` na pasta de dados), com um registro sintético semeado."""
    import zipfile
    from horus_backend.local_data_export import export_local_data, restore_local_data, verify_export
    from horus_backend.storage import Store
    from support_d import make_pg_manager
    manager = make_pg_manager(pg_app_cluster, pg_app_database, tmp_path / 'vault', monkeypatch)
    try:
        store = Store(manager, data_dir=folder)
        job, _ = store.create_job({'operation': 'transcription.run', 'title': 'sintetico'}, 'acl-sintetico')
        assert run(acl.apply, folder, confirm=True, answers=['APLICAR ACL'])[0] == 0
        backups = tmp_path / 'dados-backups'
        backups.mkdir()
        package = backups / 'pre-exclusao-teste.zip'
        assert export_local_data(folder, package, store=store)['ok'] and verify_export(package)['ok']
        with zipfile.ZipFile(package) as archive:
            assert 'pg_dump/jobs.jsonl' in archive.namelist() and job['id'] in archive.read('pg_dump/jobs.jsonl').decode()
            assert not any('.sqlite' in n for n in archive.namelist())
        assert restore_local_data(package, tmp_path / 'restaurado')['ok']
        assert (tmp_path / 'restaurado' / 'productions' / 'p1' / 'plan.json').is_file()
        assert len(acl.read_acl(backups)) > 2                                      # a pasta irmã não foi alterada
    finally:
        manager.close()


def test_real_data_dir_is_refused_without_allow_real(folder, monkeypatch):
    monkeypatch.setattr(acl, 'real_data_dirs', lambda: [folder.resolve()])
    before = principals(folder)
    code, text = run(acl.apply, folder, confirm=True, answers=['APLICAR ACL'])
    assert code == 2 and 'REAL' in text and principals(folder) == before
    code, text = run(acl.restore, folder, folder.parent / 'qualquer.json')
    assert code == 2 and 'REAL' in text
    code, text = run(acl.plan, folder)                                              # plano (somente leitura) é permitido e avisa
    assert code == 0 and 'Real: SIM' in text


def test_customized_acl_is_refused_for_manual_review(folder):
    assert acl.icacls(str(folder), '/inheritance:d')[0] == 0                        # herança copiada vira ACE explícita
    before = principals(folder)
    code, text = run(acl.apply, folder, confirm=True, answers=['APLICAR ACL'])
    assert code == 1 and 'personalizada' in text and principals(folder) == before


@pytest.mark.parametrize('kind', ['missing', 'file'])
def test_missing_or_file_targets_are_refused(tmp_path, kind):
    target = tmp_path / 'x'
    if kind == 'file':
        target.write_text('x', encoding='utf-8')
    code, text = run(acl.apply, target, confirm=True, answers=['APLICAR ACL'])
    assert code == 2


def test_restore_with_a_corrupt_backup_file_changes_nothing(folder, tmp_path):
    before = principals(folder)
    broken = tmp_path / 'acl.json'
    broken.write_text('{', encoding='utf-8')
    assert run(acl.restore, folder, broken)[0] == 2 and principals(folder) == before


def test_script_never_targets_credentials_or_data_files():
    text = SCRIPT.read_text(encoding='utf-8')
    assert 'hermes' not in text.lower() and 'keyring' not in text and 'sqlite' not in text
