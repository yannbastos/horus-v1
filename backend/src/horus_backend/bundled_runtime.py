"""Recursos opcionais do perfil offline, sempre relativos ao backend instalado."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def runtime_root() -> Path | None:
    if not getattr(sys, 'frozen', False):
        return None
    root = Path(sys.executable).resolve().parent / 'first-video'
    try:
        manifest = json.loads((root / 'runtime.json').read_text(encoding='utf-8'))
        if manifest.get('profile') == 'first-video' and manifest.get('schema_version') == 1:
            return root
    except (OSError, ValueError, AttributeError):
        pass
    return None


def resource(relative: str) -> Path | None:
    root = runtime_root()
    if root is None:
        return None
    path = root / relative
    # Não seguir substituições por junction/symlink para fora dos recursos instalados.
    return path.resolve() if path.exists() and path.resolve().is_relative_to(root.resolve()) else None


def configured_resource(variable: str, relative: str) -> Path | None:
    value = os.environ.get(variable)
    return Path(value).expanduser() if value else resource(relative)


def tool_path(path: str) -> str:
    tools = resource('tools')
    return str(tools) + os.pathsep + path if tools else path
