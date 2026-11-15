"""Despachos internos do backend instalado; desenvolvimento mantém seus comandos."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from .processes import run_sync

WORKERS = {
    'production-worker': 'horus_backend.production_worker',
    'narration-worker': 'horus_backend.narration.production_worker',
    'sapi-worker': 'horus_backend.narration.sapi_worker',
    'pilot-windows-worker': 'horus_backend.local_pilot_windows',
    'alignment-worker': 'horus_backend.speech_alignment',
}


def worker_command(name, *args, development):
    if name not in WORKERS:
        raise ValueError('Worker desconhecido')
    if getattr(sys, 'frozen', False):
        return [sys.executable, name, *map(str, args)]
    return [sys.executable, *development, *map(str, args)]


def check_resources():
    # O bridge é executado pelo Python independente da fábrica, fora do PYZ.
    for name in WORKERS.values():
        importlib.import_module(name)
    if getattr(sys, 'frozen', False):
        root = Path(__file__).parent
        for name in ('production_worker.py', 'processes.py', 'local_pilot.py',
                     'narration/kokoro_worker.py'):
            compile((root / name).read_bytes(), str(root / name), 'exec')
    return {'production_worker': True, 'protocol': 1}


def production_worker_available():
    if not getattr(sys, 'frozen', False):
        return True
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT'}}
    try:
        result = run_sync([sys.executable, 'production-check'], env=env, timeout=15, limit=4096)
        return json.loads(result) == {'production_worker': True, 'protocol': 1}
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def dispatch():
    """Chamado antes de iniciar API/autenticação, somente para uma lista fixa."""
    if len(sys.argv) < 2:
        return
    name = sys.argv[1]
    if name == 'production-check':
        print(json.dumps(check_resources()), flush=True)
        raise SystemExit(0)
    if name in WORKERS:
        import runpy
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        runpy.run_module(WORKERS[name], run_name='__main__', alter_sys=True)
        raise SystemExit(0)
