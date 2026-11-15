"""Estado da criptografia de disco do volume da pasta de dados (HOR-119). Somente leitura, sem administrador.

Consulta a propriedade de shell `System.Volume.BitLockerProtection` (a mesma que o Explorador usa para o cadeado do
volume), que um usuário comum pode ler. `Get-BitLockerVolume`, `manage-bde` e o WMI `Win32_EncryptableVolume` exigem
elevação e NÃO são usados. Nada é alterado no sistema; o comando é fixo (só a letra do volume, validada, entra nele), roda
por `processes.run_sync` (ambiente mínimo, sem shell do usuário, prazo curto) e a resposta usa a letra do volume, nunca caminhos.

Valores da propriedade (observação e documentação da comunidade; apenas `1` e `2` são tratados como definitivos):
1 = proteção ligada; 2 = proteção desligada (volume sem criptografia ou com a proteção suspensa); qualquer outro valor,
erro, tempo esgotado ou plataforma sem essa consulta = `unverified`. A propriedade não diferencia BitLocker de criptografia
de dispositivo, então `method` é `None`. Este resultado nunca bloqueia a produção.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from .processes import run_sync

CACHE_SECONDS = 300
TIMEOUT_SECONDS = 8
_cache: dict[str, tuple[float, dict]] = {}

GUIDANCE = (
    "O que é: a criptografia de disco (BitLocker no Windows Pro/Enterprise/Education; \"criptografia de dispositivo\" "
    "no Windows Home e em alguns computadores) embaralha todo o volume, de modo que quem pegar o disco ou o computador "
    "não consegue ler os arquivos sem a chave.\n"
    "Por que importa: o Hórus guarda roteiros, mídia, comentários, o banco de dados e o arquivo de conexão do Hermes na "
    "pasta de dados, sem criptografia própria. A criptografia do volume é o que protege esses dados se o computador for "
    "perdido ou roubado, e também cobre páginas livres antigas, backups e arquivos temporários.\n"
    "Como ativar: Windows Pro/Enterprise/Education: Configurações > Privacidade e segurança > Criptografia de "
    "dispositivo, ou Painel de Controle > Sistema e Segurança > Criptografia de Unidade de Disco BitLocker > Ativar o "
    "BitLocker. Windows Home não tem BitLocker: use Configurações > Privacidade e segurança > Criptografia de dispositivo "
    "(disponível em computadores compatíveis, com conta Microsoft). Guarde a chave de recuperação fora do computador. "
    "Esta verificação apenas informa; o Hórus não altera a criptografia do disco."
)


def _drive_letter(data_dir) -> str | None:
    drive = Path(data_dir).resolve().drive
    return drive.upper() if re.fullmatch(r"[A-Za-z]:", drive or "") else None


def _query(letter: str) -> str:
    system = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR") or "C:/Windows"
    shell = str(Path(system) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    script = (f"$v=(New-Object -ComObject Shell.Application).NameSpace('{letter}\\').Self"
              ".ExtendedProperty('System.Volume.BitLockerProtection'); if($null -eq $v){'none'}else{[string]$v}")
    return run_sync([shell, "-NoProfile", "-NonInteractive", "-Command", script], timeout=TIMEOUT_SECONDS).decode("utf-8", "replace").strip()


def _result(status, protection, volume, detail):
    return {"status": status, "protection": protection, "method": None, "volume": volume, "detail": detail, "guidance": GUIDANCE}


def disk_encryption_status(data_dir, *, runner=None, use_cache=True) -> dict:
    letter = _drive_letter(data_dir) if os.name == "nt" or runner else None
    if letter is None:
        return _result("unverified", "unknown", None, "Não verificado: a consulta só existe para volumes com letra no Windows.")
    cached = _cache.get(letter)
    if use_cache and runner is None and cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        raw = (runner or _query)(letter)
    except (OSError, subprocess.SubprocessError):
        result = _result("unverified", "unknown", letter, f"Não verificado no volume {letter}: a consulta não pôde ser concluída sem permissões de administrador ou falhou.")
    else:
        if raw == "1":
            result = _result("encrypted", "on", letter, f"Volume {letter} com criptografia de disco ligada.")
        elif raw == "2":
            result = _result("not_encrypted", "off", letter, f"Volume {letter} com proteção de criptografia desligada (sem criptografia ou suspensa).")
        else:
            result = _result("unverified", "unknown", letter, f"Não verificado no volume {letter}: o sistema não informou o estado de forma conclusiva.")
    if runner is None:
        _cache[letter] = (time.monotonic(), result)
    return result
