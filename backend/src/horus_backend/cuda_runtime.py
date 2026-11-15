"""Runtime NVIDIA opt-in no Windows; inspeção estática, sem downloads ou PATH global."""
from contextlib import contextmanager, ExitStack
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import re
import struct

from .file_access import client_path

# ABI usada por CTranslate2 4.5+ / faster-whisper; não certifica driver/GPU.
REQUIRED = {
    'cublas64_12.dll': 12, 'cublasLt64_12.dll': 12,
    'cudnn64_9.dll': 9, 'cudnn_ops64_9.dll': 9,
    'cudnn_cnn64_9.dll': 9, 'cudnn_adv64_9.dll': 9,
    'cudnn_graph64_9.dll': 9, 'cudnn_engines_precompiled64_9.dll': 9,
    'cudnn_engines_runtime_compiled64_9.dll': 9,
    'cudnn_heuristic64_9.dll': 9,
}
WINDOWS = os.name == 'nt'


def dll_major(path):
    """Lê PE x64/DLL e FileVersion do recurso, sem LoadLibrary ou dependência extra."""
    with path.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('arquivo não é PE')
        stream.seek(0x3c)
        offset = struct.unpack('<I', stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b'PE\0\0':
            raise ValueError('cabeçalho PE inválido')
        header = stream.read(20)
        machine, count = struct.unpack_from('<HH', header)
        size, flags = struct.unpack_from('<HH', header, 16)
        if machine != 0x8664 or not flags & 0x2000 or not 0 < count <= 96:
            raise ValueError('DLL precisa ser Windows x64')
        optional = stream.read(size)
        if len(optional) < 136 or struct.unpack_from('<H', optional)[0] != 0x20b:
            raise ValueError('PE32+ inválido')
        resource_rva = struct.unpack_from('<I', optional, 128)[0]
        sections = [stream.read(40) for _ in range(count)]
        for section in sections:
            virtual_size, rva, raw_size, raw_offset = struct.unpack_from('<IIII', section, 8)
            if rva <= resource_rva < rva + max(virtual_size, raw_size) and raw_size <= 16 * 1024 * 1024:
                stream.seek(raw_offset)
                resource = stream.read(raw_size)
                # VS_FIXEDFILEINFO: assinatura, versão da estrutura, FileVersionMS.
                signature = b'\xbd\x04\xef\xfe\x00\x00\x01\x00'
                index = resource.find(signature)
                if index >= 0 and index + 16 <= len(resource):
                    return struct.unpack_from('<I', resource, index + 8)[0] >> 16
        return None  # Alguns componentes NVIDIA não incluem recurso FileVersion.


def _folders(selected):
    if selected.lower() != 'system':
        roots = [client_path(selected, strict=True)]
    else:
        roots = []
        for key, value in sorted(os.environ.items()):
            if key.upper() == 'CUDA_PATH' or re.fullmatch(r'CUDA_PATH_V\d+_\d+', key.upper()):
                roots.append(client_path(value, strict=True))
        # Instalações padrão cuDNN podem ficar separadas do Toolkit CUDA.
        program_files = os.environ.get('ProgramFiles') or os.environ.get('PROGRAMFILES')
        if program_files:
            base = client_path(str(client_path(program_files, strict=True) / 'NVIDIA' / 'CUDNN'))
            if base.is_dir():
                roots.extend(client_path(str(p), strict=True) for p in sorted(base.glob('v9*')))
    folders = []
    for root in roots:
        if not root.is_dir():
            raise ValueError('Selecione uma pasta local de runtime CUDA.')
        candidates = [root, root / 'bin', root / 'bin/x64', root / 'cudnn/bin', root / 'cublas/bin', root / 'cuda_nvrtc/bin']
        bin_path = client_path(str(root / 'bin'))
        if bin_path.is_dir():
            candidates.extend(sorted(bin_path.glob('12.*')))
        for candidate in candidates:
            path = client_path(str(candidate))
            if path.is_dir() and path not in folders:
                folders.append(path)
    return folders


def inspect_runtime():
    selected = os.environ.get('HORUS_CUDA_RUNTIME_DIR', '')
    report = dict(configured=bool(selected), available=False,
                  source='system' if selected.lower() == 'system' else 'directory' if selected else 'none',
                  cuda_major=12, cudnn_major=9, missing=[], directories=[],
                  detail='CUDA desabilitada: configure HORUS_CUDA_RUNTIME_DIR com pasta local ou system; CPU int8 disponível se o motor estiver instalado.')
    if not WINDOWS:
        report['detail'] = 'Seleção de runtime DLL disponível somente no Windows; não altera bibliotecas do sistema.'
        return report
    if not selected:
        return report
    try:
        folders = _folders(selected)
        found = {}
        for name, major in REQUIRED.items():
            for folder in folders:
                candidate = client_path(str(folder / name))
                if candidate.is_file():
                    try:
                        actual = dll_major(candidate)
                    except (ValueError, struct.error, OSError) as error:
                        raise ValueError(f'{name}: DLL x64/FileVersion inválido.') from error
                    # cuBLAS usa FileVersion de driver (6.14...), não a versão da ABI.
                    if name.startswith('cudnn') and actual is not None and actual != major:
                        raise ValueError(f'{name}: FileVersion major {actual}; esperado {major}.')
                    found[name] = candidate.parent
                    break
        report['missing'] = sorted(set(REQUIRED) - found.keys())
        report['directories'] = sorted({str(p) for p in folders})
        if report['missing']:
            report['detail'] = 'Runtime incompleto: faltam ' + ', '.join(report['missing']) + '; usando CPU int8.'
            return report
        try:
            engine_version = version('ctranslate2')
        except PackageNotFoundError:
            engine_version = None
        if engine_version and not re.match(r'^4\.(?:[5-9]|[1-9]\d)\.', engine_version):
            report['detail'] = f'CTranslate2 {engine_version} fora da faixa validada (>=4.5,<5); usando CPU int8.'
            return report
        report['available'] = True
        report['detail'] = ('DLLs PE x64 com nomes ABI CUDA 12/cuDNN 9 verificadas; FileVersion cuDNN quando presente; versão nativa, driver/GPU e carga só ao transcrever. '
                            + ('Motor CTranslate2 ausente; runtime sozinho não habilita transcrição.' if not engine_version else 'Fallback CPU antes do primeiro trecho.'))
    except (ValueError, OSError) as error:
        from .local_diagnostics import safe_error
        report['detail'] = f'Runtime CUDA recusado: {safe_error(error)}; usando CPU int8.'
    return report


@contextmanager
def session(report):
    """Somente o subprocesso de transcrição registra pastas de DLL; handles vivem até o fim."""
    if not WINDOWS:
        yield True  # Não mudar a seleção existente em outras plataformas.
        return
    if os.environ.get('HORUS_WHISPER_DEVICE', 'auto').strip().lower() == 'cpu':
        yield False
        return
    result = inspect_runtime()
    if not result['available']:
        report('status', result['detail'])
        yield False
        return
    with ExitStack() as stack:
        try:
            for directory in result['directories']:
                stack.enter_context(os.add_dll_directory(directory))
        except OSError:
            stack.close()
            report('status', 'Não foi possível registrar as DLLs CUDA no subprocesso; usando CPU int8.')
            yield False
            return
        # CTranslate2/cuDNN também usam LoadLibrary por nome. A pré-carga absoluta
        # garante a seleção explícita mesmo nesses carregadores nativos.
        libraries = []
        try:
            import ctypes
            for name in ('cublasLt64_12.dll', 'cublas64_12.dll', *[n for n in REQUIRED if n.startswith('cudnn')]):
                candidates = [client_path(str(Path(p) / name)) for p in result['directories']]
                path = next(p for p in candidates if p.is_file())
                libraries.append(ctypes.WinDLL(str(path), winmode=0x1100))
            actual = ctypes.c_int()
            libraries[1].cublasGetProperty.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
            if libraries[1].cublasGetProperty(0, ctypes.byref(actual)) != 0 or actual.value != 12:
                raise OSError('ABI cuBLAS incompatível')
            libraries[2].cudnnGetVersion.restype = ctypes.c_size_t
            if libraries[2].cudnnGetVersion() // 10000 != 9:
                raise OSError('ABI cuDNN incompatível')
        except (OSError, ValueError, AttributeError, StopIteration):
            report('status', 'Falha ao carregar runtime CUDA validado; usando CPU int8.')
            yield False
            return
        yield True
