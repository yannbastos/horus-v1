"""ACL restritiva da pasta de dados do Hórus (HOR-120, item 7.3). SIMULAÇÃO por padrão; Windows (`icacls`).

Remove a herança da pasta e concede controle total **somente ao usuário atual e ao SYSTEM** (por SID, sem depender do idioma);
administradores, grupos locais (inclusive o grupo de sandbox de ferramentas de IA) e demais principais deixam de ter acesso.
Arquivos e subpastas que herdam da pasta acompanham. A pasta de backups (`<pasta>-backups`, irmã) tem ACL própria e não é
alterada: o usuário continua gravando e lendo os pacotes de pré-exclusão e de exportação.

  plan    DATA_DIR                      mostra a ACL atual e os comandos exatos (padrão; nada é alterado)
  apply   DATA_DIR --apply              aplica, com a confirmação digitada `APLICAR ACL`; antes guarda a ACL atual em
                                        `<pasta>-acl-backup/acl-<data>.json` (fora da pasta de dados)
  restore DATA_DIR --from ARQUIVO       devolve a herança e remove as concessões feitas por este script, conferindo o resultado

Segurança: recusa a pasta de dados REAL (padrão do Hórus ou HORUS_DATA_DIR) sem `--allow-real`; essa aplicação depende de
autorização do usuário (docs/seguranca-dados.md). Só trata pastas cuja ACL é 100% herdada (caso padrão); ACL já personalizada
é recusada para revisão manual. Nunca toca o banco, os arquivos nem as credenciais, apenas a DACL da pasta.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SYSTEM_SID = "S-1-5-18"
CONFIRMATION = "APLICAR ACL"
ACE = re.compile(r"(?P<principal>\S.*?):(?P<flags>(?:\([^)]*\))+)\s*$")


def system32(name: str) -> str:
    return str(Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32" / name)


def run(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    text = result.stdout.decode("cp850" if os.name == "nt" else "utf-8", "replace")
    return result.returncode, text


def icacls(*args: str) -> tuple[int, str]:
    return run([system32("icacls.exe"), *args])


def current_user_sid() -> str:
    code, text = run([system32("whoami.exe"), "/user", "/fo", "csv", "/nh"])
    match = re.search(r'"(S-1-[0-9-]+)"', text)
    if code or not match:
        raise OSError("Não foi possível obter o SID do usuário atual.")
    return match.group(1)


def parse_acl(text: str, path: Path) -> list[dict]:
    """ACEs de `icacls <pasta>`: [{principal, flags:[...], inherited}], sem o prefixo do caminho."""
    entries = []
    for line in text.splitlines():
        if line.startswith(str(path)):
            line = line[len(str(path)):]
        match = ACE.search(line.strip())
        if match:
            flags = re.findall(r"\(([^)]*)\)", match.group("flags"))
            entries.append({"principal": match.group("principal").strip(), "flags": flags, "inherited": "I" in flags})
    return entries


def read_acl(path: Path) -> list[dict]:
    code, text = icacls(str(path))
    if code:
        raise OSError("Não foi possível ler a ACL da pasta.")
    return parse_acl(text, path)


def has_sid(path: Path, sid: str) -> bool:
    code, text = icacls(str(path), "/findsid", f"*{sid}")
    return code == 0 and str(path) in text


def real_data_dirs() -> list[Path]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))
    try:
        from horus_backend.config import default_data_dir
        found = [default_data_dir()]
    finally:
        sys.path.pop(0)
    if os.environ.get("HORUS_DATA_DIR"):
        found.append(Path(os.environ["HORUS_DATA_DIR"]))
    return [p.resolve() for p in found]


def check_target(data_dir: Path, *, allow_real: bool, will_change: bool) -> Path:
    if os.name != "nt":
        raise ValueError("Este script usa icacls e só funciona no Windows.")
    target = Path(data_dir)
    if not target.is_dir() or target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
        raise ValueError("A pasta não existe, não é uma pasta ou é um link.")
    target = target.resolve()
    if will_change and not allow_real and target in real_data_dirs():
        raise ValueError("Esta é a pasta de dados REAL; a aplicação depende de autorização do usuário (use --allow-real somente com ela).")
    return target


def commands(target: Path, sid: str) -> list[list[str]]:
    return [["icacls", str(target), "/inheritance:r"],
            ["icacls", str(target), "/grant:r", f"*{sid}:(OI)(CI)F", f"*{SYSTEM_SID}:(OI)(CI)F"]]


def plan(data_dir: Path, *, allow_real: bool = False, out=print) -> int:
    try:
        target = check_target(data_dir, allow_real=True, will_change=False)
        sid = current_user_sid()
        acl = read_acl(target)
    except (OSError, ValueError) as error:
        out(f"Erro: {error}")
        return 2
    out("ACL atual (principal: permissões; (I) = herdada):")
    for entry in acl:
        out(f"  {entry['principal']}: " + "".join(f"({f})" for f in entry["flags"]))
    explicit = [e for e in acl if not e["inherited"]]
    if explicit or not acl:
        out("Há ACEs explícitas ou herança desativada: a ACL já foi personalizada; revise manualmente (nada será aplicado).")
    out("Comandos que seriam executados (--apply):")
    for command in commands(target, sid):
        out("  " + " ".join(command))
    out(f"Depois: somente o usuário atual e o SYSTEM, sem herança. Simulação: nada foi alterado. Real: {'SIM' if target in real_data_dirs() else 'não'}.")
    return 0


def apply(data_dir: Path, *, confirm: bool = False, allow_real: bool = False, ask=input, out=print) -> int:
    try:
        target = check_target(data_dir, allow_real=allow_real, will_change=True)
        sid = current_user_sid()
        acl = read_acl(target)
    except (OSError, ValueError) as error:
        out(f"Erro: {error}")
        return 2
    if not acl or any(not e["inherited"] for e in acl):
        out("A ACL já foi personalizada (ACE explícita ou herança desativada); recusado para revisão manual.")
        return 1
    if not confirm:
        return plan(target, allow_real=True, out=out)
    if ask(f"Digite exatamente '{CONFIRMATION}' para restringir a ACL de uma pasta: ").strip() != CONFIRMATION:
        out("Confirmação não conferiu; nada foi alterado.")
        return 1
    backup_dir = target.parent / f"{target.name}-acl-backup"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    code, listing = icacls(str(target))
    saved = backup_dir / f"acl-{stamp}.json"
    saved.write_text(json.dumps({"schema_version": 1, "created_at": stamp, "data_dir_name": target.name, "user_sid": sid,
                                 "original": acl, "listing": listing}, ensure_ascii=False, indent=2), encoding="utf-8")
    for command in commands(target, sid):
        code, text = icacls(*command[1:])
        if code:
            out("O icacls recusou a alteração; tentando restaurar a ACL anterior.")
            restore(target, saved, allow_real=allow_real, out=out)
            return 3
    after = read_acl(target)
    ok = (len(after) == 2 and all(not e["inherited"] for e in after) and has_sid(target, sid) and has_sid(target, SYSTEM_SID))
    if not ok:
        out("A ACL resultante não é a esperada; restaurando a anterior.")
        restore(target, saved, allow_real=allow_real, out=out)
        return 3
    probe = target / ".acl-probe"
    try:
        probe.write_text("x", encoding="utf-8")
        probe.read_text(encoding="utf-8")
        probe.unlink()
    except OSError:
        out("O usuário atual perdeu acesso de escrita; restaurando a ACL anterior.")
        restore(target, saved, allow_real=allow_real, out=out)
        return 3
    out(f"ACL restrita aplicada (usuário atual + SYSTEM). ACL anterior guardada em {backup_dir.name}/{saved.name}.")
    out(f"Para desfazer: python scripts/data-dir-acl.py restore \"{target}\" --from \"{saved}\"")
    return 0



def restore(data_dir: Path, saved: Path, *, allow_real: bool = False, out=print) -> int:
    try:
        target = check_target(data_dir, allow_real=allow_real, will_change=True)
        record = json.loads(Path(saved).read_text(encoding="utf-8"))
        sid = record["user_sid"]
        original = record["original"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        out(f"Erro: {error}")
        return 2
    if any(not e["inherited"] for e in original):
        out("A ACL original tinha ACEs explícitas; a restauração automática não as recria. Revise manualmente.")
        return 1
    steps = [("/inheritance:e",), ("/remove:g", f"*{sid}", f"*{SYSTEM_SID}")]
    for step in steps:
        code, _ = icacls(str(target), *step)
        if code:
            out("O icacls recusou a restauração; nada mais foi alterado.")
            return 3
    now = [(e["principal"], tuple(e["flags"])) for e in read_acl(target)]
    expected = [(e["principal"], tuple(e["flags"])) for e in original]
    if sorted(now) != sorted(expected):
        out("A ACL restaurada difere da original; confira manualmente (o arquivo de backup foi preservado).")
        return 3
    out("ACL original restaurada e conferida (herança reativada).")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="ACL restritiva da pasta de dados (simulação por padrão).")
    parser.add_argument("command", choices=("plan", "apply", "restore"))
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("--apply", action="store_true", help="aplica de verdade (com confirmação digitada)")
    parser.add_argument("--from", dest="saved", type=Path, help="arquivo de backup da ACL (restore)")
    parser.add_argument("--allow-real", action="store_true", help="permite a pasta de dados REAL (somente com autorização do usuário)")
    args = parser.parse_args(argv)
    if args.command == "plan":
        return plan(args.data_dir)
    if args.command == "apply":
        return apply(args.data_dir, confirm=args.apply, allow_real=args.allow_real)
    if not args.saved:
        parser.error("restore exige --from ARQUIVO")
    return restore(args.data_dir, args.saved, allow_real=args.allow_real)


if __name__ == "__main__":
    sys.exit(main())
