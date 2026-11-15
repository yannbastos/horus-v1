"""Diagnósticos locais sem revelar diretórios absolutos do operador."""
import re
from pathlib import PurePosixPath, PureWindowsPath


def _name(value: str) -> str:
    path = PureWindowsPath(value) if '\\' in value or re.match(r'^[A-Za-z]:', value) else PurePosixPath(value)
    return path.name or 'pasta selecionada'


def safe_error(value: object) -> str:
    text = str(value)
    # Caminhos entre aspas podem conter espaços; preserve somente o nome final.
    text = re.sub(r'''(["'])([A-Za-z]:[\\/][^"'\r\n]*|\\\\[^"'\r\n]*|/[^"'\r\n]*)\1''',
                  lambda m: m[1] + _name(m[2]) + m[1], text)
    text = re.sub(r'''[A-Za-z]:[\\/][^"'\r\n;,<>|]*''', lambda m: _name(m[0]).rstrip(), text)
    text = re.sub(r'''\\\\[^\s"';,<>|]+''', lambda m: _name(m[0]), text)
    text = re.sub(r'''(?<![\w:/])/(?:[^\s"';,<>|]+)''', lambda m: _name(m[0]), text)
    return text
