"""Pool síncrono, geração de credencial e snapshots imutáveis de diagnóstico."""
from contextlib import contextmanager
from datetime import datetime, timezone
import random
import threading
import time

from psycopg_pool import ConnectionPool

from .expected_schema import EXPECTED_SCHEMA_VERSION
from .models import DatabaseStatus
from .transport import ConnectFailure, connection_type, failure_code, resolve_public
from .vault import VaultUnavailable

# Migrador futuro toma o lock EXCLUSIVO da mesma chave; app só mantém SHARED.
MIGRATION_LOCK = (1213157973, 164)

MESSAGES = {
    "database_busy": "Banco ocupado; aguarde a parada das operações locais.",
    "database_invalid_connection": "Configuração de conexão inválida.",
    "database_endpoint_not_allowed": "Destino de banco não permitido.",
    "database_vault_unavailable": "Cofre do sistema indisponível; conexão bloqueada.",
    "database_internal_error": "Não foi possível verificar a conexão com segurança.",
}


class DatabaseRefused(Exception):
    def __init__(self, code, http_status=503):
        super().__init__(code)
        self.code, self.http_status = code, http_status


class DatabaseManager:
    def __init__(self, vault, *, busy=lambda: False):
        self.vault, self.busy = vault, busy
        self._operation = threading.Lock()
        self._units = threading.Condition()
        self._active = 0
        self.changing = False
        self._pool = self._coordination = None
        self._generation = 0
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._monitor = None
        self._snapshot = DatabaseStatus.initial()
        self._load()

    @property
    def snapshot(self):
        return self._snapshot

    def _set(self, state, code=None, *, configured=True, version=None, latency=None, checked=True):
        self._snapshot = DatabaseStatus(state=state, latency_ms=latency if state == "connected" else None,
            schema_version=version, expected_schema_version=EXPECTED_SCHEMA_VERSION, last_error_code=code,
            host_label="Supabase (sessão 5432)" if configured else None,
            checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z") if checked else None)
        return self.snapshot

    def _load(self):
        try:
            configured = self.vault.read() is not None
        except VaultUnavailable:
            self._close()
            return self._set("vault_unavailable", "database_vault_unavailable", configured=False)
        return self._set("connecting" if configured else "not_configured", configured=configured, checked=False)

    def _close(self):
        self._generation += 1  # invalida UoW/coordenação da geração anterior
        pool, coordination = self._pool, self._coordination
        self._pool = self._coordination = None
        if coordination is not None:
            coordination.close()
        if pool is not None:
            pool.close(timeout=3)

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._monitor is not None and self._monitor is not threading.current_thread():
            self._monitor.join(timeout=1)
        with self._operation:
            self._close()

    def _heartbeat(self):
        while not self._stop.is_set():
            self._wake.wait(10)
            self._wake.clear()
            if self._stop.is_set():
                return
            if not self._operation.acquire(blocking=False):
                continue
            try:
                if self.snapshot.state != "connected" and self._pool is not None:
                    self._close()
                if self._coordination is not None:
                    try:
                        self._coordination.execute("SELECT 1")
                    except Exception as error:
                        code = failure_code(error)
                        self._close()
                        self._set("auth_failed" if code == "database_auth_failed" else "unreachable", code)
            finally:
                self._operation.release()

    @contextmanager
    def _mutation(self):
        if not self._operation.acquire(blocking=False):
            raise DatabaseRefused("database_busy", 409)
        try:
            self.changing = True
            if self.busy() or self.snapshot.state == "migrating":
                raise DatabaseRefused("database_busy", 409)
            with self._units:
                self.changing = True
                deadline = time.monotonic() + 3
                while self._active:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DatabaseRefused("database_busy", 409)
                    self._units.wait(remaining)
            yield
        finally:
            self.changing = False
            self._operation.release()

    def configure(self, config):
        # Resolve ANTES de tocar o cofre ou a geração. DNS indisponível permite
        # configurar offline; resposta DNS proibida é recusa definitiva.
        try:
            resolve_public(config.host, config.port)
        except ConnectFailure as error:
            if error.code == "database_endpoint_not_allowed":
                raise DatabaseRefused(error.code, 422) from None
        with self._mutation():
            self._close()
            try:
                self.vault.write(config)
            except VaultUnavailable:
                self._set("vault_unavailable", "database_vault_unavailable")
                raise DatabaseRefused("database_vault_unavailable") from None
            return self._test_saved()

    def delete(self):
        with self._mutation():
            self._close()
            try:
                self.vault.delete()
            except VaultUnavailable:
                self._set("vault_unavailable", "database_vault_unavailable")
                raise DatabaseRefused("database_vault_unavailable") from None
            return self._set("not_configured", configured=False)

    def test(self):
        if not self._operation.acquire(blocking=False):
            return self.snapshot
        try:
            with self._units:
                if self._active:
                    return self.snapshot
                self.changing = True
            return self._test_saved()
        finally:
            self.changing = False
            self._operation.release()

    def _make_connection_type(self, config):
        return connection_type(config)

    def _test_saved(self):
        self._close()
        try:
            config = self.vault.read()
        except VaultUnavailable:
            return self._set("vault_unavailable", "database_vault_unavailable")
        if config is None:
            return self._set("not_configured", configured=False)
        self._set("connecting", checked=False)
        started = time.perf_counter()
        kind = self._make_connection_type(config)
        # Leitura idempotente somente; nunca repete COMMIT/efeitos de domínio.
        for attempt in range(3):
            generation = self._generation
            kind.on_failure = staticmethod(lambda code, g=generation: self._connection_failed(code, g))
            try:
                self._coordination = kind.connect()
                state, code, version = self._validate(self._coordination)
                if state != "connected":
                    self._close()
                    return self._set(state, code, version=version)
                self._pool = ConnectionPool("", connection_class=kind, min_size=1, max_size=4,
                    max_waiting=4, timeout=3, max_idle=60, max_lifetime=900, reconnect_timeout=5,
                    check=ConnectionPool.check_connection, open=False, name="horus-database",
                    reconnect_failed=lambda pool: self._reconnect_failed(pool, kind))
                self._pool.open(wait=True, timeout=3)
                if self._monitor is None:
                    self._monitor = threading.Thread(target=self._heartbeat, daemon=True, name="horus-database-heartbeat")
                    self._monitor.start()
                return self._set("connected", version=version, latency=(time.perf_counter()-started)*1000)
            except Exception as error:
                code = getattr(kind, "last_error_code", None) or failure_code(error)
                self._close()
                if code != "database_unreachable" or attempt == 2:
                    state = "auth_failed" if code == "database_auth_failed" else "unreachable"
                    return self._set(state, code)
                time.sleep((2 ** attempt) + random.uniform(0, 0.25))

    def _reconnect_failed(self, pool, kind):
        # Callback do pool: nunca reutiliza a geração após perda de conexão.
        if self._pool is pool:
            code = getattr(kind, "last_error_code", None) or "database_unreachable"
            self._set("auth_failed" if code == "database_auth_failed" else "unreachable", code)
            self._wake.set()

    def _connection_failed(self, code, generation):
        # Executado também por threads do pool. Fecha admissão imediatamente;
        # o monitor fecha a geração fora do próprio worker de reconexão.
        if code == "database_auth_failed" and generation == self._generation:
            self._generation += 1
            self._set("auth_failed", code)
            self._wake.set()

    def _validate(self, conn):
        try:
            return self._validate_catalog(conn)
        except Exception as error:
            if getattr(error, "sqlstate", None) in {"42P01", "42703", "3F000"}:
                return "schema_mismatch", "database_schema_mismatch", None
            if getattr(error, "sqlstate", None) == "42501":
                return "schema_mismatch", "database_role_invalid", None
            raise

    def _validate_catalog(self, conn):
        role = conn.execute("""SELECT r.rolname, r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolbypassrls,
            EXISTS (SELECT 1 FROM pg_roles p WHERE pg_has_role(current_user,p.oid,'MEMBER')
                AND (p.rolsuper OR p.rolcreatedb OR p.rolcreaterole OR p.rolbypassrls)),
            has_schema_privilege(current_user,'public','CREATE') OR
            has_database_privilege(current_user,current_database(),'CREATE')
            FROM pg_roles r WHERE r.rolname=current_user""").fetchone()
        if not role or role[0] != "horus_app" or any(role[1:]):
            return "schema_mismatch", "database_role_invalid", None
        schema = conn.execute("SELECT oid,pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='horus'").fetchone()
        if schema is None:
            return "schema_mismatch", "database_schema_mismatch", None
        invalid = conn.execute("""SELECT has_schema_privilege(current_user,'horus','CREATE') OR
            pg_has_role(current_user,(SELECT nspowner FROM pg_namespace WHERE nspname='horus'),'MEMBER') OR
            EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='horus' AND pg_has_role(current_user,c.relowner,'MEMBER'))""").fetchone()[0]
        if invalid:
            return "schema_mismatch", "database_role_invalid", None
        for table in ("horus.schema_migrations", "horus.database_identity"):
            present = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]
            if present is None:
                return "schema_mismatch", "database_schema_mismatch", None
            if not conn.execute("SELECT has_table_privilege(current_user,%s,'SELECT')", (table,)).fetchone()[0]:
                return "schema_mismatch", "database_role_invalid", None
        row = conn.execute("SELECT version,checksum FROM horus.schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        version = row[0] if row else None
        import re
        if version != EXPECTED_SCHEMA_VERSION or not isinstance(row[1], str) or not re.fullmatch(r"[a-f0-9]{64}", row[1]):
            return "schema_mismatch", "database_schema_mismatch", version
        identity = conn.execute("SELECT instance_id FROM horus.database_identity WHERE singleton=true").fetchone()
        if not identity or identity[0] is None:
            return "schema_mismatch", "database_schema_mismatch", version
        locked = conn.execute("SELECT pg_try_advisory_lock_shared(%s,%s)", MIGRATION_LOCK).fetchone()[0]
        if not locked:
            return "migrating", None, version
        return "connected", None, version

    @contextmanager
    def connection(self):
        """Uma conexão por UoW; admissão fechada quando diagnóstico não é conectado.

        Não faz retry de transação. Consumidores futuros controlam recibos/CAS.
        """
        with self._units:
            if self.changing or self.snapshot.state != "connected" or self._pool is None:
                raise DatabaseRefused(self.snapshot.last_error_code or "database_unreachable")
            self._active += 1
            pool, generation = self._pool, self._generation
        try:
            with pool.connection(timeout=3) as conn:
                if generation != self._generation:
                    raise DatabaseRefused("database_unreachable")
                yield conn
        except DatabaseRefused:
            raise
        except Exception as error:
            import psycopg
            if isinstance(error, psycopg.OperationalError):
                code = failure_code(error)
                self._close()
                self._set("auth_failed" if code == "database_auth_failed" else "unreachable", code)
                raise DatabaseRefused(code) from None
            raise
        finally:
            with self._units:
                self._active -= 1
                self._units.notify_all()
