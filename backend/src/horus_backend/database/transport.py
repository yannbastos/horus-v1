"""F-22: libpq TCP, validação DNS em cada conexão e CA pública exclusiva."""
import ipaddress
import os
import socket
from pathlib import Path

import psycopg

CA_FILE = Path(__file__).with_name("supabase-ca.crt")


class ConnectFailure(psycopg.OperationalError):
    """Somente código seguro: o pool pode registrar esta exceção."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def failure_code(error):
    if isinstance(error, ConnectFailure):
        return error.code
    if getattr(error, "sqlstate", None) in {"28P01", "28000"}:
        return "database_auth_failed"
    # libpq nem sempre fornece SQLSTATE na fase de autenticação/TLS.
    message = str(error).lower()
    if "password authentication failed" in message:
        return "database_auth_failed"
    if any(word in message for word in ("certificate", "ssl error", "ssl is not enabled", "ssl off", "does not support ssl")):
        return "database_tls_invalid"
    return "database_unreachable"


def resolve_public(host, port):
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ConnectFailure("database_dns_failed") from None
    if not addresses:
        raise ConnectFailure("database_dns_failed")
    validated = []
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        address = getattr(address, "ipv4_mapped", None) or address
        if not address.is_global or address.is_multicast:
            raise ConnectFailure("database_endpoint_not_allowed")
        validated.append(str(address))
    return validated[0]


def connection_type(config):
    """Closure de geração: nenhuma senha em conninfo, repr ou erro do pool."""
    class VerifiedConnection(psycopg.Connection):
        last_error_code = None
        on_failure = None

        @classmethod
        def connect(cls, conninfo="", **kwargs):
            try:
                # libpq aplica PGSERVICE antes dos parâmetros explícitos;
                # service='' também tenta abrir um serviço vazio. Recusar
                # opções ambientais impede configurações concorrentes ocultas.
                if any(name.startswith("PG") for name in os.environ):
                    raise ConnectFailure("database_endpoint_not_allowed")
                address = resolve_public(config.host, config.port)
                return super().connect(host=config.host, hostaddr=address, port=config.port,
                    dbname=config.database, user=config.user, password=config.password.get_secret_value(),
                    sslmode="verify-full", sslrootcert=str(CA_FILE), sslcert="", sslkey="", sslcrl="",
                    options="-c statement_timeout=15000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=15000",
                    connect_timeout=5, keepalives=1, keepalives_idle=30, keepalives_interval=10,
                    keepalives_count=3, gssencmode="disable", autocommit=True, prepare_threshold=None)
            except Exception as error:
                code = failure_code(error)
                cls.last_error_code = code
                if cls.on_failure is not None:
                    cls.on_failure(code)
                raise ConnectFailure(code) from None
    return VerifiedConnection
