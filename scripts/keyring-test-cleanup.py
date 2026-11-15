"""Limpeza das entradas sintéticas que testes antigos deixaram no Credential Manager (HOR-114). SIMULAÇÃO por padrão.

Serviço: `horus.narration.elevenlabs`. O código de produção (`narration/service.py`, classe `Credentials`) usa como usuário
`sha256(str(Path(settings.data_dir).resolve()))` (64 hex). Os testes antigos geravam o mesmo formato a partir de pastas
temporárias; portanto o padrão "64 hex" NÃO distingue produção de teste. O script separa as entradas cujo usuário coincide
com o de uma pasta de dados de produção CONHECIDA (padrão do Hórus, padrão do Tauri `com.horus.audiovisual`,
`HORUS_DATA_DIR` e `--protect-dir`) e nunca as toca. Entradas de outras pastas de dados reais (por exemplo, worktrees de
desenvolvimento) só podem ser protegidas se você as informar com `--protect-dir`.

Nunca lê valores: apenas os nomes de destino (`usuario@servico`). Só apaga com `--apply` E a confirmação digitada
`APAGAR <n>`. A exclusão depende de autorização do usuário (docs/seguranca-dados.md); não execute `--apply` sem ela.

  python scripts/keyring-test-cleanup.py [--protect-dir PASTA ...]
  python scripts/keyring-test-cleanup.py --apply [--protect-dir PASTA ...]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

SERVICE = "horus.narration.elevenlabs"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TAURI_IDENTIFIER = "com.horus.audiovisual"


def account_for(data_dir) -> str:
    """Mesma fórmula de `Credentials.__init__` em narration/service.py."""
    return hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()


def default_data_dir() -> Path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))
    try:
        from horus_backend.config import default_data_dir as official
        return official()
    finally:
        sys.path.pop(0)


def production_data_dirs(extra=()) -> list[Path]:
    dirs = [default_data_dir()]
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        dirs.append(base / TAURI_IDENTIFIER / "workspace")        # app_local_data_dir() do Tauri + "workspace"
    if os.environ.get("HORUS_DATA_DIR"):
        dirs.append(Path(os.environ["HORUS_DATA_DIR"]))
    return dirs + [Path(p) for p in extra]


def list_windows_users() -> list[str]:
    """Usuários das entradas do serviço, lendo SÓ o nome de destino (nunca o blob da credencial)."""
    import ctypes
    from ctypes import wintypes
    if os.name != "nt":
        raise OSError("A enumeração do Credential Manager só existe no Windows.")

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.c_void_p), ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    count, array = wintypes.DWORD(), ctypes.POINTER(ctypes.POINTER(CREDENTIAL))()
    advapi.CredEnumerateW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                      ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(CREDENTIAL)))]
    if not advapi.CredEnumerateW(f"*@{SERVICE}", 0, ctypes.byref(count), ctypes.byref(array)):
        error = ctypes.get_last_error()
        if error == 1168:   # ERROR_NOT_FOUND: nenhuma entrada
            return []
        raise OSError(f"CredEnumerateW falhou (código {error}).")
    try:
        targets = [array[i].contents.TargetName for i in range(count.value)]
    finally:
        advapi.CredFree(array)
    suffix = "@" + SERVICE
    return [t[:-len(suffix)] for t in targets if t and t.endswith(suffix)]


def classify(users, protected_dirs):
    protected_accounts = {account_for(d) for d in protected_dirs} | {hashlib.sha256(str(d).encode()).hexdigest() for d in protected_dirs}
    synthetic = sorted({u for u in users if HEX64.fullmatch(u)})
    other = sorted({u for u in users if not HEX64.fullmatch(u)})
    protected = [u for u in synthetic if u in protected_accounts]
    return {"total": len(set(users)), "protected": protected, "deletable": [u for u in synthetic if u not in protected_accounts],
            "other_format": other}


def short(user: str) -> str:
    return user[:8] + "…"


def run(args, lister=list_windows_users, delete=None, ask=input, out=print) -> int:
    try:
        users = lister()
    except OSError as error:
        out(f"Erro: {error}")
        return 2
    report = classify(users, production_data_dirs(args.protect_dir))
    out(f"Serviço: {SERVICE}")
    out(f"Entradas do serviço: {report['total']}")
    out(f"  usuário de produção conhecido (protegido, nunca tocado): {len(report['protected'])}"
        + (f" -> {', '.join(short(u) for u in report['protected'])}" if report["protected"] else ""))
    out(f"  usuário com 64 hex fora das pastas de produção conhecidas (candidatas a limpeza): {len(report['deletable'])}")
    out(f"  usuário em outro formato (ignoradas): {len(report['other_format'])}")
    for user in report["deletable"][:20]:
        out(f"    {short(user)}")
    if len(report["deletable"]) > 20:
        out(f"    ... e mais {len(report['deletable']) - 20}")
    if not args.apply:
        out("Simulação: nada foi alterado. A exclusão exige --apply, confirmação digitada e autorização do usuário.")
        return 0
    if not report["deletable"]:
        out("Nada a apagar.")
        return 0
    phrase = f"APAGAR {len(report['deletable'])}"
    if ask(f"Digite exatamente '{phrase}' para apagar {len(report['deletable'])} entradas: ").strip() != phrase:
        out("Confirmação não conferiu; nada foi apagado.")
        return 1
    if delete is None:
        import keyring
        delete = keyring.delete_password
    protected = set(report["protected"])
    done = 0
    for user in report["deletable"]:
        if user in protected:      # defesa em profundidade
            continue
        delete(SERVICE, user)
        done += 1
    out(f"Apagadas {done} entradas.")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="apaga (com confirmação digitada); sem isto é só simulação")
    parser.add_argument("--protect-dir", action="append", default=[], metavar="PASTA",
                        help="pasta de dados real cuja entrada nunca deve ser tocada (repetível)")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
