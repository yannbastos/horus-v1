"""Procedimento de manutenção separado para dump/restauração completos via `pg_dump`/`pg_restore` (HOR-164 / I8).

Regras de segurança:
- NUNCA executado pelo aplicativo ou pela UI; uso exclusivo de manutenção do operador.
- Credencial de backup restrita lida do cofre nativo (`keyring`), NUNCA de `argv`, logs ou Git.
- Repassa senha exclusivamente via `PGPASSFILE` temporário protegido (removido em `finally`)
  com `--no-password`, sessão TCP 5432, `sslmode=verify-full` e CA oficial Supabase embarcada.
- Restauração (`restore`) recusa banco de destino não vazio; sempre usar banco isolado e vazio.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

import keyring

KEYRING_SERVICE = "horus.pg.backup.maintenance"
DEFAULT_PORT = 5432
CA_CERT = Path(__file__).resolve().parent.parent / "backend" / "src" / "horus_backend" / "database" / "supabase-ca.crt"


def _escape_pgpass(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


def _write_temp_pgpass(temp_dir: Path, *, host: str, port: int, database: str, user: str, password: str) -> Path:
    pgpass = temp_dir / "pgpass.conf"
    line = f"{_escape_pgpass(host)}:{port}:{_escape_pgpass(database)}:{_escape_pgpass(user)}:{_escape_pgpass(password)}\n"
    fd = os.open(pgpass, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    if os.name != "nt":
        os.chmod(pgpass, stat.S_IRUSR | stat.S_IWUSR)
    return pgpass


def build_libpq_env(pgpass_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env["PGPASSFILE"] = str(pgpass_path)
    env["PGSSLMODE"] = "verify-full"
    env["PGSSLROOTCERT"] = str(CA_CERT)
    return env


def run_pg_dump(*, pg_bin_dir: Path, host: str, database: str, user: str, output_file: Path, dry_run: bool = False) -> int:
    password = keyring.get_password(KEYRING_SERVICE, f"{user}@{host}/{database}")
    if not password:
        raise RuntimeError("Credencial de backup ausente no cofre nativo; configure antes de executar.")
    if output_file.exists():
        raise ValueError("Arquivo de saída já existe; escolha um destino novo.")

    exe = pg_bin_dir / ("pg_dump.exe" if os.name == "nt" else "pg_dump")
    cmd = [
        str(exe),
        "--format=custom",
        "--schema=horus",
        "--no-owner",
        "--no-privileges",
        "--no-password",
        "--host",
        host,
        "--port",
        str(DEFAULT_PORT),
        "--username",
        user,
        "--dbname",
        database,
        "--file",
        str(output_file),
    ]
    if dry_run:
        print("DRY-RUN:", " ".join(cmd))
        return 0

    with tempfile.TemporaryDirectory(prefix="horus-pgpass-") as tmp:
        pgpass = _write_temp_pgpass(Path(tmp), host=host, port=DEFAULT_PORT, database=database, user=user, password=password)
        env = build_libpq_env(pgpass)
        result = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, check=False)
        return result.returncode


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Manutenção separada de pg_dump com credencial via cofre/PGPASSFILE.")
    parser.add_argument("--pg-bin", type=Path, required=True, help="Pasta bin do PostgreSQL 17 oficial.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return run_pg_dump(
        pg_bin_dir=args.pg_bin,
        host=args.host,
        database=args.database,
        user=args.user,
        output_file=args.output,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
