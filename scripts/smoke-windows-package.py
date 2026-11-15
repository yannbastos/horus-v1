"""Smoke local do binário congelado/desktop instalado; usa apenas dados descartáveis.

Requer psutil somente no Python que executa o teste, nunca no aplicativo.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.request

import psutil


def request(port, token, path, method="GET", payload=None, raw=False):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=30) as response:
        return response.read().decode('utf-8') if raw else json.load(response)


def close_window(pid):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE, fluxo normal do Tauri
        return True

    user32.EnumWindows(visit, 0)


def smoke(executable, desktop=False, crash=False, data_dir=None, with_transcription=False,
          cuda_runtime_dir=None, model_dir=None, audio=None, expected_device=None):
    started = time.perf_counter()
    with (nullcontext(str(data_dir)) if data_dir else tempfile.TemporaryDirectory(prefix="horus-package-smoke-")) as folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("HORUS_", "FABRICA_", "PYTHON", "UV_", "VIRTUAL_ENV"))}
        # Sem node, uv, Python, ffmpeg ou modelos descobertos pelo ambiente do operador.
        env["PATH"] = os.path.join(os.environ["SystemRoot"], "System32")
        env["HORUS_DATA_DIR"] = folder
        env["HORUS_HERMES_CONFIG"] = str(Path(folder) / "missing-hermes.json")
        env["HORUS_HERMES_PROFILE"] = str(Path(folder) / "hermes-profile")
        token = secrets.token_hex(32)
        env.update(HORUS_API_TOKEN=token, HORUS_MANAGED="1")
        if cuda_runtime_dir is not None:
            env['HORUS_CUDA_RUNTIME_DIR'] = cuda_runtime_dir
        if model_dir:
            env.update(HORUS_WHISPER_MODELS_DIR=str(model_dir), HORUS_WHISPER_DEVICE='cuda',
                       HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
        proc = subprocess.Popen([str(executable)] + ([] if desktop else ["--port", "0"]),
                                env=env, cwd=folder, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        backend = None
        descendants = []
        try:
            if desktop:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    descendants = psutil.Process(proc.pid).children(recursive=True)
                    backend = next((p for p in descendants if p.name() == "horus-backend.exe"), None)
                    if backend:
                        connections = backend.net_connections(kind="tcp")
                        port = next((c.laddr.port for c in connections if c.status == "LISTEN"), None)
                        if port:
                            backend_env = backend.environ()
                            assert backend_env["HORUS_DATA_DIR"] == folder
                            token = backend_env["HORUS_API_TOKEN"]
                            break
                    time.sleep(.1)
                else:
                    raise AssertionError("Desktop não iniciou backend/listener em 45 s")
            else:
                # Leitura com timeout para que falha de inicialização não trave o smoke.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(proc.stdout.readline)
                    try:
                        ready = json.loads(future.result(timeout=45))
                    except Exception:
                        proc.kill()
                        raise
                assert ready["event"] == "ready"
                port = ready["port"]
                backend = psutil.Process(ready["pid"])
            ready_seconds = time.perf_counter() - started
            health = request(port, token, "/health")
            assert health["status"] == "ready"
            diagnostics = request(port, token, "/v1/diagnostics")
            missing = [c["id"] for c in diagnostics["checks"] if not c["available"]]
            assert {'ffmpeg', 'node'} <= set(missing), missing
            engines = {'faster_whisper', 'ctranslate2', 'av'}
            assert (not engines.intersection(missing)) if with_transcription else engines <= set(missing), missing
            if expected_device:
                assert with_transcription and model_dir and audio
                cuda = diagnostics['cuda_runtime']
                assert cuda['available'] == (expected_device == 'cuda'), cuda
                transcription_started = time.perf_counter()
                job = request(port, token, '/v1/jobs', 'POST',
                              dict(operation='transcription.run', path=str(audio), model='small', language='pt'))
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    job = request(port, token, f"/v1/jobs/{job['id']}")
                    if job['status'] in {'succeeded', 'failed', 'blocked', 'interrupted', 'cancelled'}:
                        break
                    time.sleep(.2)
                assert job['status'] == 'succeeded', job
                assert job['result']['text'].strip(), job
                events = request(port, token, f"/v1/jobs/{job['id']}/events", raw=True)
                assert f'Transcrevendo em {expected_device.upper()}' in events, events
                print(json.dumps(dict(transcription_device=expected_device, cuda_runtime=cuda['source'],
                                      cuda_available=cuda['available'], transcription_seconds=round(time.perf_counter()-transcription_started, 3))))
            assert not diagnostics["production"]["ready"]
            assert (Path(folder) / "horus.sqlite3").is_file()
            descendants = psutil.Process(proc.pid).children(recursive=True)
            if desktop:
                if crash:
                    proc.kill()
                else:
                    close_window(proc.pid)
            else:
                request(port, token, "/v1/shutdown", "POST")
            proc.wait(timeout=15)
            deadline = time.monotonic() + 10
            while backend.is_running() and time.monotonic() < deadline:
                time.sleep(.1)
            assert not backend.is_running(), "Backend órfão"
            _, alive = psutil.wait_procs(descendants, timeout=10)
            assert not alive, f"Processos descendentes órfãos: {[p.pid for p in alive]}"
            print(json.dumps(dict(mode="desktop-crash" if crash else "desktop" if desktop else "backend",
                                  ready_seconds=round(ready_seconds, 3), total_seconds=round(time.perf_counter()-started, 3),
                                  missing=missing, orphan_backend=False), ensure_ascii=False))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for child in descendants:
                if child.is_running():
                    child.kill()
            if backend and backend.is_running():
                backend.kill()
                backend.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--crash", action="store_true")
    parser.add_argument("--data-dir", type=Path, help="Diretório descartável compartilhado entre versões")
    parser.add_argument('--with-transcription', action='store_true')
    parser.add_argument('--cuda-runtime-dir')
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--audio', type=Path, help='Somente áudio sintético descartável')
    parser.add_argument('--expected-device', choices=['cpu', 'cuda'])
    args = parser.parse_args()
    smoke(args.executable.resolve(), args.desktop, args.crash, args.data_dir, args.with_transcription,
          args.cuda_runtime_dir, args.model_dir, args.audio, args.expected_device)
