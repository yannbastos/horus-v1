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


# --- Novas fixtures de aplicação em PostgreSQL (HOR-164 I-final A) -------------------------------------------

from pg_app_support import PG_TEST_CONFIG, PG_TEST_TOKEN, _patch_pg_transport  # noqa: E402,F401


@pytest.fixture(scope="session")
def pg_app_cluster():
    """Cluster PostgreSQL descartável em loopback para testes de API fora de `tests/pg/`."""
    import sys
    from pathlib import Path

    pg_dir = str(Path(__file__).resolve().parent / "pg")
    if pg_dir not in sys.path:
        sys.path.insert(0, pg_dir)
    from harness import Cluster

    with Cluster() as cluster:
        yield cluster


@pytest.fixture
def pg_app_database(pg_app_cluster):
    """Banco PostgreSQL descartável com schema `horus` completo e `database_identity`."""
    import sys
    from pathlib import Path

    pg_dir = str(Path(__file__).resolve().parent / "pg")
    if pg_dir not in sys.path:
        sys.path.insert(0, pg_dir)
    from schema_support import schema_database

    with schema_database(pg_app_cluster) as (admin, pool):
        with pool.connection() as conn:
            dbname = conn.info.dbname
        admin.execute(
            "INSERT INTO horus.database_identity(singleton, instance_id, created_at) "
            "VALUES (true, gen_random_uuid(), now()) ON CONFLICT (singleton) DO NOTHING"
        )
        yield dbname


@pytest.fixture
def pg_app_settings(tmp_path):
    from horus_backend.config import Settings

    return Settings(
        token=PG_TEST_TOKEN,
        data_dir=tmp_path / "data",
        factory_root=None,
        hermes_executable=None,
        hermes_config=tmp_path / "hermes.json",
    )


@pytest.fixture
def pg_app(pg_app_cluster, pg_app_database, pg_app_settings, monkeypatch):
    """Aplicação FastAPI (`create_app`) já conectada ao PostgreSQL descartável."""
    from horus_backend.api import create_app
    from horus_backend.database.models import ConnectionRequest
    from horus_backend.database.vault import ConnectionVault

    memory_keyring = MemoryKeyring()
    _patch_pg_transport(pg_app_cluster, pg_app_database, memory_keyring, monkeypatch)
    ConnectionVault(pg_app_settings.data_dir).write(ConnectionRequest.model_validate(PG_TEST_CONFIG))
    return create_app(pg_app_settings)


@pytest.fixture
def pg_client(pg_app):
    """`TestClient` sobre `pg_app` (loopback, sem exceções não tratadas)."""
    from fastapi.testclient import TestClient

    with TestClient(pg_app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def pg_diagnostic_app(pg_app_cluster, pg_app_database, pg_app_settings, monkeypatch):
    """Aplicação FastAPI (`create_app`) em modo diagnóstico (sem banco configurado), pronta para conectar."""
    from horus_backend.api import create_app

    memory_keyring = MemoryKeyring()
    _patch_pg_transport(pg_app_cluster, pg_app_database, memory_keyring, monkeypatch)
    return create_app(pg_app_settings)
