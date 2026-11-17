"""PostgreSQL sintético descartável; nunca lê configuração do produto."""
from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool


DDL = """
CREATE SCHEMA horus AUTHORIZATION horus_owner;
SET ROLE horus_owner;
CREATE TABLE horus.parent(id bigint PRIMARY KEY);
INSERT INTO horus.parent VALUES (1);
CREATE TABLE horus.event(
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 key text UNIQUE NOT NULL, payload jsonb NOT NULL,
 parent_id bigint REFERENCES horus.parent(id) DEFERRABLE INITIALLY DEFERRED);
CREATE FUNCTION horus.append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'append-only'; END $$;
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON horus.event
 FOR EACH ROW EXECUTE FUNCTION horus.append_only();
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA horus TO horus_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA horus TO horus_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA horus TO horus_app;
RESET ROLE;
"""


class Cluster:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="horus-test-pg-")).resolve()
        self.data = self.root / "data"
        self.timings = {}
        self.databases = {"postgres", "horus_template"}
        self.active = False
        configured = os.environ.get("HORUS_TEST_PG_BIN")
        if configured:
            self.bin = Path(configured).resolve()
        else:
            from pgserver._commands import POSTGRES_BIN_PATH
            self.bin = Path(POSTGRES_BIN_PATH)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]

    def run(self, executable, *args):
        start = time.perf_counter()
        suffix = ".exe" if os.name == "nt" else ""
        with (self.root / "commands.log").open("ab") as output:
            subprocess.run([str(self.bin / (executable + suffix)), *args],
                           stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                           close_fds=True, timeout=90, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return time.perf_counter() - start

    def validate_dsn(self, dsn):
        """Allowlist exata, antes de qualquer conexão/pool; não resolve DNS."""
        options = conninfo_to_dict(dsn)
        allowed = {"host", "hostaddr", "port", "dbname", "user", "connect_timeout", "sslmode", "options"}
        if (not self.active or set(options) - allowed
                or options.get("host") != "127.0.0.1"
                or options.get("hostaddr") != "127.0.0.1"
                or options.get("options") != ""
                or options.get("port") != str(self.port)
                or options.get("dbname") not in self.databases
                or options.get("user") not in {"postgres", "horus_app"}
                or options.get("sslmode") != "disable"):
            raise ValueError("DSN recusado: use apenas o cluster descartável desta sessão")
        return dsn

    def dsn(self, database="postgres", user="postgres"):
        return self.validate_dsn(make_conninfo(host="127.0.0.1", hostaddr="127.0.0.1", options="", port=self.port,
            dbname=database, user=user, connect_timeout=5, sslmode="disable"))

    def connect(self, database="postgres", user="postgres", **kwargs):
        return psycopg.connect(self.dsn(database, user), **kwargs)

    def __enter__(self):
        try:
            self.timings["initdb_seconds"] = self.run("initdb", "-D", str(self.data),
                "-U", "postgres", "--auth=trust", "--encoding=UTF8", "--locale=C")
            # Trust restrito ao loopback e somente dados sintéticos; nenhum socket Unix.
            (self.data / "pg_hba.conf").write_text(
                "host all all 127.0.0.1/32 trust\n", encoding="ascii")
            with (self.data / "postgresql.conf").open("a", encoding="ascii") as config:
                config.write("\nunix_socket_directories = ''\n")
            self.timings["start_seconds"] = self.run("pg_ctl", "-D", str(self.data),
                "-l", str(self.root / "server.log"), "-o",
                f"-h 127.0.0.1 -p {self.port}", "-w", "start")
            self.active = True
            with self.connect(autocommit=True) as conn:
                self.version = conn.execute("SELECT version()").fetchone()[0]
                conn.execute("CREATE ROLE horus_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
                conn.execute("CREATE ROLE horus_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
                conn.execute("CREATE DATABASE horus_template OWNER horus_owner")
            start = time.perf_counter()
            with self.connect("horus_template") as conn:
                conn.execute(DDL)
                conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            self.timings["ddl_seconds"] = time.perf_counter() - start
            with self.connect(autocommit=True) as conn:
                conn.execute("ALTER DATABASE horus_template IS_TEMPLATE true")
                conn.execute("ALTER DATABASE horus_template ALLOW_CONNECTIONS false")
            return self
        except BaseException:
            self.close()
            raise

    @contextmanager
    def database(self):
        name = "horus_case_" + uuid.uuid4().hex
        pool = None
        start = time.perf_counter()
        with self.connect(autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE horus_template OWNER horus_owner").format(sql.Identifier(name)))
            conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO horus_app").format(sql.Identifier(name)))
        self.timings.setdefault("clone_seconds", []).append(time.perf_counter() - start)
        self.databases.add(name)
        try:
            pool = ConnectionPool(self.dsn(name, "horus_app"), min_size=1, max_size=4,
                                  kwargs={"autocommit": True}, open=True)
            pool.wait(timeout=10)
            yield pool
        finally:
            if pool is not None:
                pool.close()
            with self.connect(autocommit=True) as conn:
                conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            self.databases.remove(name)

    def close(self):
        if (self.data / "postmaster.pid").exists():
            self.run("pg_ctl", "-D", str(self.data), "-m", "immediate", "-w", "stop")
        self.active = False
        temp = Path(tempfile.gettempdir()).resolve()
        if self.root.parent != temp or not self.root.name.startswith("horus-test-pg-"):
            raise RuntimeError("Limpeza recusada: diretório fora do TEMP exclusivo")
        shutil.rmtree(self.root)

    def __exit__(self, *_):
        self.close()
