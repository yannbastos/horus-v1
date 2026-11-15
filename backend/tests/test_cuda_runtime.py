"""Seleção explícita de DLLs: sem rede, carga nativa ou mudanças globais nos testes."""
from contextlib import contextmanager
import ctypes
import os
import struct
import sys
from types import SimpleNamespace as NS

import pytest
from horus_backend import cuda_runtime as cr, transcriber as tr


def pe(path, major, machine=0x8664, with_version=True):
    data = bytearray(1024)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HH', data, 0x84, machine, 1)
    struct.pack_into('<HH', data, 0x94, 240, 0x2000)
    struct.pack_into('<H', data, 0x98, 0x20b)
    struct.pack_into('<I', data, 0x98 + 128, 0x1000)
    struct.pack_into('<IIII', data, 0x98 + 240 + 8, 512, 0x1000, 512, 512)
    if with_version:
        struct.pack_into('<IIII', data, 512, 0xFEEF04BD, 0x10000, major << 16, 0)
    path.write_bytes(data)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(cr, 'WINDOWS', True)
    monkeypatch.setattr(cr, 'version', lambda name: '4.8.0')
    monkeypatch.setenv('HORUS_WHISPER_DEVICE', 'cuda')
    monkeypatch.setenv('HORUS_CUDA_RUNTIME_DIR', str(tmp_path))
    for name, major in cr.REQUIRED.items():
        pe(tmp_path / name, major)
    return tmp_path


def test_opt_in_and_no_dll_load_in_inspection(runtime, monkeypatch):
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: pytest.fail('carga nativa no diagnóstico'), raising=False)
    monkeypatch.delenv('HORUS_CUDA_RUNTIME_DIR')
    assert not cr.inspect_runtime()['available']
    monkeypatch.setenv('HORUS_CUDA_RUNTIME_DIR', str(runtime))
    assert cr.inspect_runtime()['available']


@pytest.mark.parametrize('path', ['relative/runtime', r'\\server\share', r'\\?\C:\runtime', 'C:/missing/runtime'])
def test_invalid_or_unc_refused(runtime, monkeypatch, path):
    monkeypatch.setenv('HORUS_CUDA_RUNTIME_DIR', path)
    result = cr.inspect_runtime()
    assert not result['available']
    assert 'recusado' in result['detail']


@pytest.mark.parametrize('problem', ['missing', 'corrupt', 'x86', 'old_cudnn', 'engine'])
def test_incomplete_or_incompatible_runtime(runtime, monkeypatch, problem):
    target = runtime / 'cudnn64_9.dll'
    if problem == 'missing':
        target.unlink()
    elif problem == 'corrupt':
        target.write_bytes(b'not a DLL')
    elif problem == 'x86':
        pe(target, 9, machine=0x14c)
    elif problem == 'old_cudnn':
        pe(target, 8)
    else:
        monkeypatch.setattr(cr, 'version', lambda name: '4.4.0')
    assert not cr.inspect_runtime()['available']


def test_vendor_fileversion_is_not_cublas_abi(runtime):
    pe(runtime / 'cublas64_12.dll', 6)
    pe(runtime / 'cudnn_graph64_9.dll', 9, with_version=False)
    assert cr.inspect_runtime()['available']


def test_system_discovery_is_explicit_and_combines_folders(runtime, monkeypatch):
    monkeypatch.setenv('CUDA_PATH', str(runtime))
    monkeypatch.setenv('HORUS_CUDA_RUNTIME_DIR', 'system')
    # Não depender de instalações do operador.
    monkeypatch.delenv('ProgramFiles', raising=False)
    monkeypatch.delenv('PROGRAMFILES', raising=False)
    assert cr.inspect_runtime()['available']
    monkeypatch.delenv('HORUS_CUDA_RUNTIME_DIR')
    assert not cr.inspect_runtime()['available']


@pytest.mark.parametrize('failure', ['none', 'register', 'load', 'version'])
def test_handles_lifetime_absolute_load_and_cpu_fallback(runtime, monkeypatch, failure):
    events = []
    original_path = os.environ.get('PATH')

    @contextmanager
    def register(directory):
        if failure == 'register':
            raise OSError('refused')
        events.append('registered')
        try:
            yield
        finally:
            events.append('closed')

    class Function:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)

    def load(path, **kw):
        assert str(runtime) in path and kw['winmode'] == 0x1100
        if failure == 'load':
            raise OSError('load failed')
        events.append('loaded')
        def cublas(_, pointer):
            pointer._obj.value = 11 if failure == 'version' else 12
            return 0
        return NS(cublasGetProperty=Function(cublas), cudnnGetVersion=Function(lambda: 90000))

    monkeypatch.setattr(cr.os, 'add_dll_directory', register, raising=False)
    monkeypatch.setattr(ctypes, 'WinDLL', load, raising=False)
    messages = []
    with cr.session(lambda *message: messages.append(message)) as enabled:
        assert enabled == (failure == 'none')
        if enabled:
            assert 'closed' not in events
    if failure != 'register':
        assert events[-1] == 'closed'
    assert os.environ.get('PATH') == original_path
    assert bool(messages) == (failure != 'none')


def test_invalid_runtime_forces_cpu_before_engine_import_and_gpu_probe(runtime, monkeypatch):
    monkeypatch.setenv('HORUS_CUDA_RUNTIME_DIR', r'\\server\share')
    monkeypatch.setattr(tr, 'emit', lambda *args: None)
    monkeypatch.setattr(tr, '_transcribe', lambda *args: args[-1])
    assert tr.transcribe(runtime / 'a.wav', 'small', 'pt', runtime) is False
    assert tr.choose_device(NS(get_cuda_device_count=lambda: pytest.fail('GPU probe')), cuda_ready=False) == 'cpu'
