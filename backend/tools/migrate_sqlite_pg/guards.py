"""Guardas de segurança: recusa origem operacional/quente, links, destino remoto/não vazio e segredos."""
from __future__ import annotations

import ctypes
import ipaddress
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse

from psycopg.conninfo import conninfo_to_dict


class MigrationGuardError(ValueError):
    """Erro de pré-condição ou segurança que impede a migração."""


def _strip_win_extended_prefix(raw: str) -> str:
    """Remove prefixos estendidos do Windows (\\\\?\\ e \\\\?\\UNC\\) para comparação canônica."""
    if raw.startswith("\\\\?\\UNC\\") or raw.startswith("//?/UNC/"):
        return "\\\\" + raw[8:]
    if raw.startswith("\\\\?\\") or raw.startswith("//?/"):
        return raw[4:]
    return raw


def _reject_device_namespace(raw: str) -> None:
    if raw.startswith("\\\\.\\") or raw.startswith("//./"):
        raise MigrationGuardError("Caminho de dispositivo Windows (\\\\.\\) recusado.")


def _win_long_path(raw: str) -> str:
    """Expande nomes curtos 8.3 no Windows para o segmento existente mais longo."""
    if os.name != "nt":
        return raw
    try:
        get_long = ctypes.windll.kernel32.GetLongPathNameW
    except Exception:
        return raw

    candidate = Path(raw)
    tail: list[str] = []
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            return raw
        tail.append(candidate.name)
        candidate = parent

    buf = ctypes.create_unicode_buffer(32768)
    length = get_long(str(candidate), buf, len(buf))
    resolved_base = buf.value if 0 < length < len(buf) else str(candidate)
    result = Path(resolved_base)
    for part in reversed(tail):
        result = result / part
    return str(result)


def _canonical_path_str(path: Path) -> str:
    raw = str(path)
    _reject_device_namespace(raw)
    stripped = _strip_win_extended_prefix(raw)
    resolved = _strip_win_extended_prefix(str(Path(stripped).resolve()))
    expanded = _win_long_path(resolved)
    return _strip_win_extended_prefix(expanded)


def _norm_key(path: Path) -> str:
    return _canonical_path_str(path).replace("\\", "/").casefold().rstrip("/")


def _is_inside(path: Path, parent: Path) -> bool:
    p_key = _norm_key(path)
    parent_key = _norm_key(parent)
    return p_key == parent_key or p_key.startswith(parent_key + "/")


def _operational_roots() -> list[Path]:
    roots: list[Path] = []
    candidates = [Path.home() / "AppData" / "Local"]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(_strip_win_extended_prefix(local_app_data)))
    for base in candidates:
        for name in ("com.horus.audiovisual", "Horus", "Programs/Horus"):
            roots.append(base / name)
    return roots


def validate_no_links(path: Path) -> None:
    """Recusa symlinks e junctions em qualquer segmento do caminho."""
    raw = str(path)
    _reject_device_namespace(raw)
    clean = Path(_strip_win_extended_prefix(raw))
    for candidate in (clean, *clean.parents):
        try:
            is_link = candidate.is_symlink() or candidate.is_junction()
        except OSError:
            is_link = False
        if is_link:
            raise MigrationGuardError("Caminho com symlink ou junction recusado.")


def _check_not_operational(path: Path) -> None:
    for op_root in _operational_roots():
        if _is_inside(path, op_root):
            raise MigrationGuardError(
                "Pasta operacional ou de instalação recusada; use apenas caminhos isolados."
            )


def _temp_root() -> Path:
    raw_temp = os.environ.get("TEMP") or tempfile.gettempdir()
    temp = Path(_strip_win_extended_prefix(raw_temp))
    if not temp.is_absolute():
        raise MigrationGuardError("TEMP relativo proibido.")
    validate_no_links(temp)
    return Path(_canonical_path_str(temp))


def _allowed_output_roots(allowlist_roots: tuple[Path, ...] | list[Path] | None = None) -> list[Path]:
    roots = [_temp_root()]
    env_allow = os.environ.get("HORUS_MIGRATION_ALLOWLIST_DIR")
    if env_allow:
        for item in env_allow.split(os.pathsep):
            if item.strip():
                roots.append(Path(_canonical_path_str(Path(item.strip()))))
    if allowlist_roots:
        for item in allowlist_roots:
            roots.append(Path(_canonical_path_str(Path(item))))
    return roots


def validate_source_path(raw_path: str | Path, *, must_exist: bool = True) -> Path:
    """Garante que a origem é uma cópia explícita absoluta fora das pastas operacionais."""
    raw_str = str(raw_path)
    _reject_device_namespace(raw_str)
    stripped = _strip_win_extended_prefix(raw_str)
    if stripped.startswith("\\\\") or stripped.startswith("//"):
        raise MigrationGuardError("Caminho UNC remoto recusado para origem; use cópia local.")
    path = Path(stripped)
    if not path.is_absolute():
        raise MigrationGuardError("Caminho de origem deve ser absoluto.")
    validate_no_links(path)
    _check_not_operational(path)
    resolved = Path(_canonical_path_str(path))
    _check_not_operational(resolved)
    if must_exist and not resolved.exists():
        raise MigrationGuardError("Caminho de origem não encontrado.")
    return resolved


def validate_output_dir(
    raw_path: str | Path,
    *,
    allowlist_roots: tuple[Path, ...] | list[Path] | None = None,
) -> Path:
    """Valida --backup-dir: absoluto, sem links, fora de pastas operacionais, em TEMP/allowlist e novo/vazio."""
    raw_str = str(raw_path)
    _reject_device_namespace(raw_str)
    stripped = _strip_win_extended_prefix(raw_str)
    if stripped.startswith("\\\\") or stripped.startswith("//"):
        raise MigrationGuardError("Caminho UNC recusado para diretório de saída.")
    path = Path(stripped)
    if not path.is_absolute():
        raise MigrationGuardError("Diretório de saída deve ser absoluto.")
    validate_no_links(path)
    _check_not_operational(path)
    resolved = Path(_canonical_path_str(path))
    _check_not_operational(resolved)

    temp = _temp_root()
    if _norm_key(resolved) == _norm_key(temp):
        raise MigrationGuardError("Exige uma subpasta exclusiva dentro de TEMP, não a raiz de TEMP.")
    allowed = _allowed_output_roots(allowlist_roots)
    if not any(_is_inside(resolved, root) for root in allowed):
        raise MigrationGuardError("Diretório de saída deve estar dentro de TEMP ou pasta allowlisted.")
    if resolved.exists():
        if not resolved.is_dir():
            raise MigrationGuardError("Diretório de saída aponta para um arquivo existente.")
        if any(resolved.iterdir()):
            raise MigrationGuardError("Diretório de backup já existe e não está vazio; recusa sobrescrita.")
    return resolved


def validate_report_out(
    raw_path: str | Path,
    *,
    allowlist_roots: tuple[Path, ...] | list[Path] | None = None,
) -> Path:
    """Valida --report-out: absoluto, sem links, fora de pastas operacionais, em TEMP/allowlist e sem sobrescrever."""
    raw_str = str(raw_path)
    _reject_device_namespace(raw_str)
    stripped = _strip_win_extended_prefix(raw_str)
    if stripped.startswith("\\\\") or stripped.startswith("//"):
        raise MigrationGuardError("Caminho UNC recusado para relatório.")
    path = Path(stripped)
    if not path.is_absolute():
        raise MigrationGuardError("Caminho do relatório deve ser absoluto.")
    validate_no_links(path)
    _check_not_operational(path)
    resolved = Path(_canonical_path_str(path))
    _check_not_operational(resolved)

    temp = _temp_root()
    if _norm_key(resolved) == _norm_key(temp) or _norm_key(resolved.parent) == _norm_key(temp):
        raise MigrationGuardError("Relatório exige uma subpasta exclusiva dentro de TEMP.")
    allowed = _allowed_output_roots(allowlist_roots)
    if not any(_is_inside(resolved, root) for root in allowed):
        raise MigrationGuardError("Relatório deve estar dentro de TEMP ou pasta allowlisted.")
    if resolved.exists():
        raise MigrationGuardError("Arquivo de relatório já existe; recusa sobrescrita.")
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
    if "password=" in low or ("://" in low and "@" in urlparse(dsn).netloc):
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
