"""Isolamento global dos testes do backend: nenhum teste pode tocar o cofre de credenciais real do sistema.

`keyring.set_password` usa o `get_keyring` do módulo `keyring.core`; `monkeypatch.setattr(keyring, 'get_keyring', ...)` troca
apenas o atributo do pacote e NÃO isola (os testes chegaram a gravar valores sintéticos no Credential Manager real). Esta fixture
autouse instala um backend em memória com `keyring.set_keyring`, restaura o anterior ao final e falha o teste se, durante ele,
o backend ativo deixar de ser o de memória ou se qualquer método dos backends reais do sistema for chamado.
"""
import keyring
import pytest

from keyring_support import MemoryKeyring, _real_backend_classes


@pytest.fixture(autouse=True)
def isolated_keyring(monkeypatch):
    previous = keyring.get_keyring()           # só resolve o backend; não lê nem grava credenciais
    memory = MemoryKeyring()

    def forbidden(*args, **kwargs):
        raise AssertionError('Teste tentou usar o cofre de credenciais real do sistema; use o backend em memória.')
    for cls in _real_backend_classes():
        for method in ('get_password', 'set_password', 'delete_password', 'get_credential'):
            if method in cls.__dict__ or hasattr(cls, method):
                monkeypatch.setattr(cls, method, forbidden)
    keyring.set_keyring(memory)
    try:
        yield memory
        assert keyring.get_keyring() is memory, 'O backend de credenciais foi trocado durante o teste.'
    finally:
        keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def no_disk_encryption_query(monkeypatch):
    """O diagnóstico não deve abrir PowerShell nos testes; a consulta real é exercitada com `runner` simulado."""
    from horus_backend import disk_encryption
    monkeypatch.setattr(disk_encryption, '_query', lambda letter: 'none')
    disk_encryption._cache.clear()
