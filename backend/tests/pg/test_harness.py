"""Contratos de isolamento e recursos necessários à migração, sem runtime."""
import pytest
import psycopg
from psycopg.conninfo import make_conninfo
from harness import Cluster


@pytest.mark.parametrize("changes", [
    {"host": "example.com"}, {"host": "localhost"}, {"hostaddr": "192.0.2.1"},
    {"port": "5432"}, {"dbname": "operacional"}, {"service": "production"},
    {"options": "-c search_path=public"}, {"user": "horus_owner"},
])
def test_reject_external_dsn(pg_cluster, changes):
    options = dict(host="127.0.0.1", hostaddr="127.0.0.1", options="", port=pg_cluster.port, dbname="postgres",
                   user="postgres", sslmode="disable")
    options.update(changes)
    with pytest.raises(ValueError, match="DSN recusado"):
        pg_cluster.validate_dsn(make_conninfo(**options))


def test_minimal_role(pg_pool):
    with pg_pool.connection() as conn:
        assert conn.execute("SELECT current_user").fetchone() == ("horus_app",)
        assert conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone() == (False,) * 4
        for statement in ("CREATE TABLE horus.forbidden(id int)",
                          "CREATE TABLE public.forbidden(id int)",
                          "CREATE TEMP TABLE forbidden(id int)",
                          "CREATE SCHEMA forbidden", "SET ROLE horus_owner"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_postgresql_semantics(pg_pool):
    with pg_pool.connection() as conn:
        row = conn.execute("INSERT INTO horus.event(key,payload,parent_id) VALUES ('one','{\"ok\":true}',1) RETURNING id,payload->>'ok'").fetchone()
        assert row == (1, "true")
        assert conn.execute("INSERT INTO horus.event(id,key,payload) OVERRIDING SYSTEM VALUE VALUES (100,'explicit','{}') RETURNING id").fetchone() == (100,)
        assert conn.execute("INSERT INTO horus.event(key,payload) VALUES ('one','{}') ON CONFLICT(key) DO NOTHING RETURNING id").fetchone() is None
        assert conn.execute("INSERT INTO horus.event(key,payload) VALUES ('new','{}') ON CONFLICT(key) DO NOTHING RETURNING id").fetchone() is not None
        for statement in ("UPDATE horus.event SET payload='{}'", "DELETE FROM horus.event"):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute(statement)
        with conn.transaction():
            conn.execute("INSERT INTO horus.event(key,payload,parent_id) VALUES ('deferred','{}',2)")
            conn.execute("INSERT INTO horus.parent VALUES (2)")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute("INSERT INTO horus.event(key,payload,parent_id) VALUES ('invalid','{}',999)")


def test_clone_isolation_and_drop(pg_cluster):
    with pg_cluster.database() as first, pg_cluster.database() as second:
        with first.connection() as conn:
            name = conn.info.dbname
            conn.execute("INSERT INTO horus.event(key,payload) VALUES ('isolated','{}')")
        with second.connection() as conn:
            assert conn.execute("SELECT count(*) FROM horus.event").fetchone() == (0,)
    with pg_cluster.connect() as conn:
        assert conn.execute("SELECT count(*) FROM pg_database WHERE datname=%s", (name,)).fetchone() == (0,)
    assert first.closed and second.closed


def test_cleanup_after_exception():
    cluster = Cluster()
    with pytest.raises(RuntimeError, match="synthetic"):
        with cluster:
            raise RuntimeError("synthetic")
    assert not cluster.root.exists()
    assert not cluster.active
    with pytest.raises(ValueError):
        cluster.dsn()
