"""Constantes e adaptador de transporte PostgreSQL compartilhados pelos testes de API (HOR-164).

Ficam em módulo próprio (e não em `conftest.py`) porque `backend/tests/conftest.py` e `backend/tests/pg/conftest.py`
têm o mesmo nome de módulo: `from conftest import ...` é ambíguo na coleta conjunta de `backend/tests` + `backend/tests/pg`.
"""

PG_TEST_TOKEN = "pg-app-tests-token-0123456789abcdef"
PG_TEST_CONFIG = {
    "host": "aws-0-sa-east-1.pooler.supabase.com",
    "port": 5432,
    "database": "postgres",
    "user": "horus_app.abcdefghijklmnopqrst",
    "password": "sintética:'\\@?#%$\"= 空白 !",
}


def _patch_pg_transport(pg_app_cluster, pg_app_database, memory_keyring, monkeypatch):
    import psycopg
    from horus_backend.database.manager import DatabaseManager
    from horus_backend.database.vault import ConnectionVault

    monkeypatch.setattr(ConnectionVault, "backend", lambda self: memory_keyring)

    def local_type(self, config):
        class LocalConnection(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo="", **kwargs):
                return super().connect(
                    pg_app_cluster.dsn(pg_app_database, "horus_app"),
                    autocommit=True,
                    password=config.password.get_secret_value(),
                    prepare_threshold=None,
                )

        return LocalConnection

    monkeypatch.setattr(DatabaseManager, "_make_connection_type", local_type)
    monkeypatch.setattr("horus_backend.database.manager.resolve_public", lambda *_: "8.8.8.8")
