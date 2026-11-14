"""Política de acesso a caminhos vindos do cliente (HOR-123).

Os caminhos de mídia, transcrição e destino de salvamento/download vêm do diálogo nativo do aplicativo; o backend
não conhece as pastas escolhidas, então não impõe uma raiz única. Impõe, antes de qualquer E/S, que o texto seja um
caminho local absoluto e comum: sem UNC/dispositivo (`\\\\srv\\x`, `\\\\?\\`, `\\\\.\\`), sem fluxo alternativo NTFS
(`arquivo:stream`), sem `..`, sem nomes reservados do Windows (CON, NUL, COM1…), sem ponto/espaço final e sem
caracteres de controle. Destinos de escrita também não podem cair dentro do diretório de dados do Hórus.
Artefatos de produção/exportação usam `production.confined` (raiz + links + ids), não este módulo.
"""
from __future__ import annotations

import re
from pathlib import Path

_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³])$", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class PathNotAllowed(ValueError):
    """Caminho recusado pela política; a mensagem é segura para devolver ao cliente."""


def _text_problem(raw: str) -> str | None:
    if _CONTROL.search(raw):
        return "O caminho contém caracteres de controle."
    normalized = raw.replace("/", "\\")
    if normalized.startswith("\\\\"):
        return "Caminhos de rede (UNC) e de dispositivo (\\\\?\\, \\\\.\\) não são permitidos."
    rest = normalized[2:] if re.match(r"^[A-Za-z]:", normalized) else normalized
    if ":" in rest:
        return "Fluxos alternativos (arquivo:stream) não são permitidos."
    for part in normalized.split("\\"):
        if part == "..":
            return "O caminho não pode conter '..'."
        if part in {"", "."}:
            continue
        if part != part.rstrip(" .") or part.endswith((" ", ".")):
            return "Nomes terminados em ponto ou espaço não são permitidos."
        if _RESERVED.match(part.split(".", 1)[0].rstrip(" ")):
            return "Nomes reservados do Windows (CON, NUL, COM1…) não são permitidos."
    return None


def _resolved_problem(path: Path) -> str | None:
    text = str(path)
    if text.startswith("\\\\?\\") and re.match(r"^[A-Za-z]:", text[4:]):
        return None  # prefixo estendido de uma unidade local, produzido pela própria resolução
    if text.startswith("\\\\"):
        return "O destino resolve para um caminho de rede ou de dispositivo."
    return None


def client_path(raw: str, *, strict: bool = False, protected: tuple[Path, ...] = ()) -> Path:
    """Valida e devolve o caminho absoluto resolvido; `PathNotAllowed` se recusado.

    `strict=True` exige que exista (como `resolve(strict=True)`). `protected`: pastas onde o caminho não pode cair.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise PathNotAllowed("Informe um caminho.")
    if problem := _text_problem(raw):
        raise PathNotAllowed(problem)
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise PathNotAllowed("Informe um caminho absoluto (use o seletor do aplicativo).")
    try:
        resolved = expanded.resolve(strict=strict)
    except OSError as error:
        raise ValueError(error.strerror or str(error)) from error
    if problem := _resolved_problem(resolved):
        raise PathNotAllowed(problem)
    for root in protected:
        try:
            inside = resolved.is_relative_to(Path(root).resolve())
        except OSError:
            inside = False
        if inside:
            raise PathNotAllowed("Esta pasta é reservada ao Hórus; escolha outro local.")
    return resolved
