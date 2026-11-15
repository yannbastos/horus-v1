"""scripts/keyring-test-cleanup.py: simulação por padrão; exclusão só com --apply + confirmação; nunca toca o usuário de produção.

Usa apenas o cofre em memória do conftest e listas fictícias: nunca o Credential Manager real.
"""
import hashlib
import importlib.util
from argparse import Namespace
from pathlib import Path

import keyring
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'keyring-test-cleanup.py'
spec = importlib.util.spec_from_file_location('keyring_test_cleanup', SCRIPT)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


def user(label):
    return hashlib.sha256(label.encode()).hexdigest()


@pytest.fixture
def world(isolated_keyring, tmp_path):
    production = tmp_path / 'producao' / 'workspace'
    production.mkdir(parents=True)
    prod_user = cleanup.account_for(production)
    tests = [user(f'teste-{i}') for i in range(3)]
    other = 'nao-hex'
    for name in [prod_user, *tests, other]:
        keyring.set_password(cleanup.SERVICE, name, 'valor-sintetico')
    return production, prod_user, tests, other


def args(production, apply=False):
    return Namespace(apply=apply, protect_dir=[str(production)])


def lister(isolated_keyring):
    return lambda: [u for (s, u) in isolated_keyring.values if s == cleanup.SERVICE]


def test_production_user_formula_matches_the_production_code(tmp_path):
    from horus_backend.narration.service import Credentials
    assert Credentials(tmp_path).account == cleanup.account_for(tmp_path)


def test_dry_run_reports_counts_and_changes_nothing(world, isolated_keyring):
    production, prod_user, tests, other = world
    lines = []
    assert cleanup.run(args(production), lister=lister(isolated_keyring), out=lines.append) == 0
    text = '\n'.join(lines)
    assert 'Entradas do serviço: 5' in text and 'protegido, nunca tocado): 1' in text and 'candidatas a limpeza): 3' in text
    assert 'outro formato (ignoradas): 1' in text and 'Simulação: nada foi alterado' in text
    assert all(u not in text for u in [prod_user, *tests])            # só usuários truncados
    assert prod_user[:8] in text and tests[0][:8] in text
    assert len(isolated_keyring.values) == 5


def test_apply_without_exact_confirmation_deletes_nothing(world, isolated_keyring):
    production, *_ = world
    answers = iter(['sim'])
    assert cleanup.run(args(production, True), lister=lister(isolated_keyring), ask=lambda _: next(answers), out=lambda _: None) == 1
    assert len(isolated_keyring.values) == 5


def test_apply_with_confirmation_deletes_only_synthetic_entries_never_production(world, isolated_keyring):
    production, prod_user, tests, other = world
    assert cleanup.run(args(production, True), lister=lister(isolated_keyring), ask=lambda _: 'APAGAR 3', out=lambda _: None) == 0
    assert set(u for _, u in isolated_keyring.values) == {prod_user, other}


def test_unknown_production_dir_is_a_known_limit_the_operator_must_protect_explicitly(world, isolated_keyring):
    production, prod_user, *_ = world
    report = cleanup.classify(lister(isolated_keyring)(), [])          # sem --protect-dir: a pasta fictícia não é conhecida
    assert prod_user in report['deletable']


def test_enumeration_error_is_reported_without_touching_anything(world, isolated_keyring):
    def broken():
        raise OSError('indisponível')
    lines = []
    assert cleanup.run(args(world[0], True), lister=broken, out=lines.append) == 2
    assert len(isolated_keyring.values) == 5


def test_script_never_reads_values():
    source = SCRIPT.read_text(encoding='utf-8')
    assert 'get_password' not in source and 'CredentialBlob' in source   # a estrutura declara o campo, mas ele nunca é lido
    assert 'CredentialBlob)' not in source and '.CredentialBlob' not in source
