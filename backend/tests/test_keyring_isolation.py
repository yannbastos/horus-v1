"""Garante que a suíte nunca usa o cofre de credenciais real do sistema (ver conftest.py)."""
from pathlib import Path

import keyring
import pytest

from keyring_support import MemoryKeyring, _real_backend_classes


def test_active_backend_is_the_in_memory_one_and_credentials_stay_there(isolated_keyring):
    assert keyring.get_keyring() is isolated_keyring and isinstance(isolated_keyring, MemoryKeyring)
    keyring.set_password('horus.teste.isolamento', 'conta-sintetica', 'valor-sintetico')
    assert isolated_keyring.values == {('horus.teste.isolamento', 'conta-sintetica'): 'valor-sintetico'}
    assert keyring.get_password('horus.teste.isolamento', 'conta-sintetica') == 'valor-sintetico'


def test_real_system_backends_fail_the_test_if_used():
    classes = _real_backend_classes()
    for cls in classes:
        for method in ('get_password', 'set_password', 'delete_password'):
            with pytest.raises(AssertionError, match='cofre de credenciais real'):
                getattr(cls, method)(object(), 'horus.teste.isolamento', 'x', *(['y'] if method == 'set_password' else []))


def test_no_test_replaces_get_keyring_by_attribute_which_does_not_isolate():
    offenders = [p.name for p in Path(__file__).parent.glob('test_*.py')
                 if p.name != Path(__file__).name and "setattr(keyring, 'get_keyring'" in p.read_text(encoding='utf-8')]
    assert offenders == []
