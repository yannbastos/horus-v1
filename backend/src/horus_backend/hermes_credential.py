"""Token do Hermes no cofre do SO (HOR-120, item 7.2): leitura com preferência pelo cofre e migração explícita e reversível.

Leitura (`vault_token`, usada por `hermes.read_connection`): o token do cofre (`keyring`, serviço `horus.hermes.connection`,
usuário = SHA-256 do caminho resolvido do arquivo de conexão) tem preferência; se o cofre estiver indisponível, vazio ou
guardar um valor inválido, vale o token do arquivo atual (`integrations/hermes.json`). O arquivo continua sendo obrigatório
para a URL (`base_url`); o campo `token` dele passa a ser opcional quando o cofre tem o token.

Migração (`python -m horus_backend.hermes_credential migrate [--config ARQUIVO] [--apply] [--replace]`): **simulação por
padrão**. Com `--apply` e a confirmação digitada, copia o token do arquivo para o cofre e **relê e compara** (comparação em
tempo constante). Nunca apaga nem altera o arquivo, nunca revoga nem rotaciona o token, nunca imprime o valor. Remover o
campo `token` do arquivo é um passo manual posterior, com autorização do usuário (docs/seguranca-dados.md). Sem rede.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from pathlib import Path

SERVICE = "horus.hermes.connection"
CONFIRMATION = "MIGRAR"
MAX_CONFIG = 16 * 1024


def account_for(config: Path) -> str:
    return hashlib.sha256(str(Path(config).resolve()).encode()).hexdigest()


def valid_token(value) -> bool:
    return isinstance(value, str) and 16 <= len(value) <= 4096 and all(33 <= ord(c) <= 126 for c in value)


def vault_token(config: Path) -> str | None:
    """Token válido do cofre ou None (cofre indisponível, vazio ou valor inválido: o chamador usa o arquivo)."""
    try:
        import keyring
        value = keyring.get_password(SERVICE, account_for(config))
    except Exception:       # noqa: BLE001 - qualquer falha do cofre cai no arquivo; o motivo nunca vai para a resposta
        return None
    return value if valid_token(value) else None


def _read_file_config(config: Path) -> dict:
    raw = Path(config).read_bytes()[:MAX_CONFIG + 1]
    if len(raw) > MAX_CONFIG:
        raise ValueError("Configuração excessiva.")
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("Configuração inválida.")
    return document


def migrate(config: Path, *, apply: bool = False, replace: bool = False, ask=input, out=print) -> int:
    config = Path(config)
    try:
        document = _read_file_config(config)
    except FileNotFoundError:
        out("Arquivo de conexão não encontrado; nada a migrar.")
        return 2
    except (OSError, ValueError, UnicodeError):
        out("Arquivo de conexão ilegível ou inválido; nada foi alterado.")
        return 2
    token = document.get("token")
    if not valid_token(token):
        out("O arquivo não contém um token válido para migrar (ausente, curto ou com caracteres inválidos).")
        return 2
    existing = vault_token(config)
    same = existing is not None and secrets.compare_digest(existing.encode(), token.encode())
    out(f"Serviço do cofre: {SERVICE}; usuário: {account_for(config)[:8]}… (derivado do caminho do arquivo)")
    out("Token no arquivo: presente (valor não exibido).")
    out("Token no cofre: " + ("igual ao do arquivo" if same else "diferente do arquivo" if existing else "ausente"))
    if same:
        out("Nada a fazer: o cofre já tem o mesmo token. O arquivo não foi alterado.")
        return 0
    if existing and not replace:
        out("O cofre já tem outro token; recusado sem --replace (nada foi sobrescrito).")
        return 1
    if not apply:
        out("Simulação: nada foi alterado. Com --apply e a confirmação digitada, o token é copiado ao cofre e verificado; "
            "o arquivo NÃO é apagado e nada é revogado.")
        return 0
    if ask(f"Digite exatamente '{CONFIRMATION}' para copiar o token ao cofre do sistema: ").strip() != CONFIRMATION:
        out("Confirmação não conferiu; nada foi alterado.")
        return 1
    try:
        import keyring
        keyring.set_password(SERVICE, account_for(config), token)
        verified = keyring.get_password(SERVICE, account_for(config))
    except Exception:       # noqa: BLE001
        out("O cofre do sistema não está disponível ou recusou a gravação; nada foi alterado no arquivo.")
        return 3
    if verified is None or not secrets.compare_digest(verified.encode(), token.encode()):
        out("A releitura do cofre não conferiu com o arquivo; o arquivo permanece como estava.")
        return 3
    out("Token copiado ao cofre e verificado. O arquivo e a credencial real não foram alterados nem revogados.")
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Migra o token do Hermes para o cofre do sistema (simulação por padrão).")
    parser.add_argument("command", choices=("migrate",))
    parser.add_argument("--config", type=Path, help="arquivo de conexão (padrão: o configurado para o Hórus)")
    parser.add_argument("--apply", action="store_true", help="grava no cofre (com confirmação digitada)")
    parser.add_argument("--replace", action="store_true", help="permite substituir um token diferente já existente no cofre")
    args = parser.parse_args(argv)
    from .config import hermes_connection_file
    return migrate(args.config or hermes_connection_file(), apply=args.apply, replace=args.replace)


if __name__ == "__main__":
    sys.exit(main())
