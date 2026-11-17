"""Guardas de segurança: recusa origem operacional/quente, links, destino remoto/não vazio e segredos."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse

from psycopg.conninfo import conninfo_to_dict


class MigrationGuardError(ValueError):
    """Erro de pré-condição ou segurança que impede a migração."""


def _norm_key(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").casefold().rstrip("/")


def _is_inside(path: Path, parent: Path) -> bool:
    p_key = _norm_key(path)
    parent_key = _norm_key(parent)
    return p_key == parent_key or p_key.startswith(parent_key + "/")


def _operational_roots() -> list[Path]:
    roots: list[Path] = []
    candidates = [Path.home() / "AppData" / "Local"]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data))
    for base in candidates:
        for name in ("com.horus.audiovisual", "Horus", "Programs/Horus"):
            roots.append(base / name)
    return roots


def validate_no_links(path: Path) -> None:
    """Recusa symlinks e junctions em qualquer segmento do caminho."""
    for candidate in (path, *path.parents):
        try:
            is_link = candidate.is_symlink() or candidate.is_junction()
        except OSError:
            is_link = False
        if is_link:
            raise MigrationGuardError("Caminho com symlink ou junction recusado.")


def validate_source_path(raw_path: str | Path, *, must_exist: bool = True) -> Path:
    """Garante que a origem é uma cópia explícita absoluta fora das pastas operacionais."""
    path = Path(raw_path)
    if not path.is_absolute():
        raise MigrationGuardError("Caminho de origem deve ser absoluto.")
    validate_no_links(path)
    for op_root in _operational_roots():
        if _is_inside(path, op_root):
            raise MigrationGuardError(
                "Origem operacional ou de instalação recusada; use apenas uma cópia explícita."
            )
    resolved = path.resolve()
    if must_exist and not resolved.exists():
        raise MigrationGuardError("Caminho de origem não encontrado.")
    return resolved


def validate_cold_sqlite(db_path: Path) -> None:
    """Recusa banco SQLite com sidecars -wal/-shm/-journal (cópia não fria)."""
    if not db_path.is_file():
        raise MigrationGuardError("Arquivo SQLite de origem não encontrado.")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            raise MigrationGuardError(
                f"Cópia não é fria: sidecar {suffix} presente; faça snapshot/checkpoint antes de copiar."
            )


def validate_target_dsn(dsn: str) -> dict[str, str]:
    """Allowlist estrita para destino PostgreSQL descartável em loopback, sem segredos em argv."""
    if not dsn or not isinstance(dsn, str):
        raise MigrationGuardError("DSN de destino obrigatório.")
    low = dsn.lower()
    if "password=" in low or "://" in low and "@" in urlparse(dsn).netloc:
        raise MigrationGuardError("Segredos/credenciais em DSN/argv são proibidos.")
    try:
        opts = conninfo_to_dict(dsn)
    except Exception as exc:
        raise MigrationGuardError("DSN de destino inválido.") from exc

    allowed_keys = {
        "host",
        "hostaddr",
        "port",
        "dbname",
        "user",
        "connect_timeout",
        "sslmode",
        "options",
    }
    extra = set(opts) - allowed_keys
    if extra:
        raise MigrationGuardError("Opções não permitidas no DSN de destino.")
    if "password" in opts:
        raise MigrationGuardError("Senha em DSN/argv é proibida.")
    if opts.get("options", "") != "":
        raise MigrationGuardError("Parâmetro options no DSN é proibido.")

    host = opts.get("host", "")
    hostaddr = opts.get("hostaddr", "")
    if host not in {"127.0.0.1", "localhost"}:
        raise MigrationGuardError(
            "Destino remoto recusado: apenas PostgreSQL descartável em loopback (127.0.0.1) é permitido."
        )
    if hostaddr:
        try:
            ip = ipaddress.ip_address(hostaddr)
        except ValueError as exc:
            raise MigrationGuardError("hostaddr inválido no DSN.") from exc
        if not ip.is_loopback:
            raise MigrationGuardError("hostaddr não loopback recusado.")
    if opts.get("sslmode") not in {None, "disable"}:
        raise MigrationGuardError("Destino de ensaio local exige sslmode=disable.")
    if not opts.get("dbname") or not opts.get("user"):
        raise MigrationGuardError("DSN deve informar dbname e user.")
    if opts.get("port"):
        try:
            port_num = int(opts["port"])
            if not (1 <= port_num <= 65535):
                raise ValueError
        except ValueError as exc:
            raise MigrationGuardError("Porta inválida no DSN.") from exc
    return opts
