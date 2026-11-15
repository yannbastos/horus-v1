"""Subprocesso de transcrição e download do YouTube.

Executado somente como tarefa; a API nunca importa Whisper ou yt-dlp. Mensagens
estruturadas saem no stdout como NDJSON ({"type": ..., "message": ...}).
"""
from __future__ import annotations

import argparse
import errno
import gc
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .dependencies import find_tool
from . import cuda_runtime
from .local_diagnostics import safe_error

MODELS = ("tiny", "base", "small", "medium", "large-v3-turbo")
LANGUAGES = ("auto", "pt", "en", "es")
FILE_MARKER = "HORUS_FILE:"
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


def is_youtube_url(value: str) -> bool:
    """Somente vídeo único, HTTPS e sem credenciais."""
    # urlsplit remove TAB/CR/LF silenciosamente. Valide o valor que será enviado
    # ao downloader, sem aceitar um endereço diferente após essa normalização.
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    try:
        url = urlsplit(value)
        if url.scheme != "https" or url.username is not None or url.password is not None or url.port is not None:
            return False
    except ValueError:
        # A propriedade port também lança ValueError para portas inválidas.
        return False
    if url.hostname == "youtu.be":
        return bool(_VIDEO_ID.fullmatch(url.path.strip("/")))
    if url.hostname not in _YOUTUBE_HOSTS:
        return False
    parts = [part for part in url.path.split("/") if part]
    if len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
        return bool(_VIDEO_ID.fullmatch(parts[1]))
    return url.path == "/watch" and any(_VIDEO_ID.fullmatch(v) for v in parse_qs(url.query).get("v", []))


def emit(kind: str, message: str):
    print(json.dumps({"type": kind, "message": message}, ensure_ascii=False), flush=True)


def choose_device(runtime, *, report=None, default='auto', cuda_ready=True) -> str:
    report = report or emit
    requested = os.environ.get('HORUS_WHISPER_DEVICE', default).strip().lower()
    if requested not in {'auto', 'cpu', 'cuda'}:
        raise ValueError('HORUS_WHISPER_DEVICE inválido. Use auto, cpu ou cuda.')
    if requested == 'cpu' or not cuda_ready:
        return 'cpu'
    try:
        available = runtime.get_cuda_device_count() > 0
    except (RuntimeError, OSError) as error:
        report('status', f'Não foi possível consultar CUDA ({type(error).__name__}). Usando CPU int8.')
        return 'cpu'
    if not available and requested == 'cuda':
        report('status', 'CUDA solicitada, mas nenhuma GPU utilizável foi detectada. Usando CPU int8.')
    return 'cuda' if available else 'cpu'


def is_gpu_failure(error: Exception) -> bool:
    detail = str(error).lower()
    return isinstance(error, (RuntimeError, OSError)) and any(marker in detail for marker in (
        'cuda', 'cudnn', 'cublas', 'nvidia', 'out of memory', 'dll load failed', 'float16 compute type'))


def transcribe(path: Path, model_name: str, language: str, models_dir: Path):
    with cuda_runtime.session(emit) as cuda_ready:
        return _transcribe(path, model_name, language, models_dir, cuda_ready)


def _transcribe(path: Path, model_name: str, language: str, models_dir: Path, cuda_ready: bool):
    import ctranslate2
    from faster_whisper import WhisperModel

    device = choose_device(ctranslate2, cuda_ready=cuda_ready)
    from .speech_alignment import resolve_model_path
    local_root = os.environ.get('HORUS_WHISPER_MODELS_DIR')
    if local_root:
        root = Path(local_root).expanduser()
        model_path = resolve_model_path(root, model_name, required_files=(
            'model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'))
        if model_path is None:
            raise ValueError(f'Modelo {model_name} não preparado em HORUS_WHISPER_MODELS_DIR; modo local não permite download.')
    else:
        model_path = resolve_model_path(models_dir, model_name)
        if model_path is None:
            raise FileNotFoundError(f'Modelo Whisper {model_name!r} não instalado na pasta de modelos selecionada. Instale-o explicitamente antes de transcrever.')
    emitted = False
    while True:
        model = segments = None
        try:
            compute = 'float16' if device == 'cuda' else 'int8'
            emit('status', f'Carregando modelo local {model_name} em {device.upper()} ({compute}).')
            model = WhisperModel(str(model_path.resolve()), device=device, compute_type=compute, local_files_only=True)
            emit('status', f'Transcrevendo em {device.upper()} ({compute}).')
            segments, info = model.transcribe(str(path), language=None if language == 'auto' else language)
            last = -1
            for segment in segments:
                text = segment.text.strip()
                if text:
                    emit('segment', text)
                    emitted = True
                if info.duration:
                    percent = min(99, int(segment.end * 100 / info.duration))
                    if percent > last:
                        last = percent
                        emit('progress', str(percent))
            emit('progress', '100')
            return
        except Exception as error:
            if device != 'cuda' or not is_gpu_failure(error):
                raise
            if emitted:
                raise RuntimeError('Falha CUDA/cuDNN após produzir texto. Texto parcial preservado; não houve repetição automática. '
                                   'Para tentar novamente em CPU, configure HORUS_WHISPER_DEVICE=cpu.') from error
            emit('status', f'GPU indisponível/incompatível ({type(error).__name__}). Alternando uma vez para CPU int8, sem repetir trechos.')
            emit('progress', '0')
            device = 'cpu'
        finally:
            if segments is not None and hasattr(segments, 'close'):
                segments.close()
            del segments, model
            gc.collect()


def download(url: str, directory: Path) -> int:
    if not is_youtube_url(url):
        raise ValueError("Informe um link válido de vídeo do YouTube.")
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_tool('ffmpeg')
    node = find_tool('node')
    # "no-certifi": valida TLS pelo repositório de certificados do sistema, que inclui CAs corporativas
    # (inspeção TLS). A verificação continua ativa; o certifi embutido recusaria essas redes.
    args = ["--ignore-config", "--compat-options", "no-certifi",
            "--js-runtimes", f"node:{node}" if node else "node", "--no-playlist", "--no-overwrites", "--continue", "--part", "--newline",
            "--progress", "--progress-delta", "1", "--no-colors", "--socket-timeout", "20", "--retries", "3",
            "--windows-filenames", "--no-write-info-json", "--trim-filenames", "150", "-P", str(directory),
            "-o", "%(title).150B [%(id)s].%(ext)s",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b" if ffmpeg else "best[ext=mp4]/best",
            "--merge-output-format", "mp4", "--print", f"after_move:{FILE_MARKER}%(filepath)s"]
    if ffmpeg:
        args += ["--ffmpeg-location", ffmpeg]
    args += ["--", url]
    try:
        import yt_dlp
    except ImportError:
        yt_dlp = None
    if yt_dlp is not None:
        try:
            return yt_dlp.main(args) or 0
        except SystemExit as exit_:
            return exit_.code if isinstance(exit_.code, int) else 1
    executable = find_tool('yt-dlp')
    if not executable:
        raise RuntimeError("yt-dlp não encontrado. Instale o extra transcription do backend ou o yt-dlp no PATH.")
    import subprocess
    return subprocess.call([executable, *args])


def download_error(error: object) -> str:
    """Classifica diagnóstico sem devolver caminhos, URLs ou credenciais do motor."""
    detail = str(error).lower()
    number = getattr(error, 'errno', None)
    if 'arquivo de vídeo válido' in detail:
        return 'O download não retornou um arquivo de vídeo válido e concluído no destino escolhido. Tente novamente para retomar o parcial compatível.'
    if 'pasta de destino não encontrada' in detail:
        return 'Pasta de destino não encontrada. Escolha uma pasta existente e tente novamente.'
    if 'yt-dlp não encontrado' in detail or 'ferramenta de download indisponível' in detail:
        return 'Ferramenta de download indisponível. Prepare o extra transcription do backend ou configure o yt-dlp antes de tentar novamente.'
    if number == errno.ENOSPC or any(s in detail for s in ('no space left', 'disk full', 'not enough space', 'errno 28', 'winerror 112', 'espaço em disco insuficiente')):
        return 'Espaço em disco insuficiente. Libere espaço no destino e tente novamente; o parcial foi mantido quando disponível.'
    if number in {errno.EACCES, errno.EPERM} or any(s in detail for s in ('permission denied', 'access is denied', 'errno 13', 'winerror 5', 'sem permissão para gravar')):
        return 'Sem permissão para gravar no destino. Escolha uma pasta gravável e tente novamente.'
    if any(s in detail for s in ('private video', 'video is private', 'vídeo é privado')):
        return 'Este vídeo é privado. Escolha um vídeo público; o Hórus não usa credenciais para liberar acesso.'
    if any(s in detail for s in ('age-restricted', 'confirm your age', 'age restricted', 'restrição de idade')):
        return 'Vídeo com restrição de idade. Escolha um vídeo público sem essa restrição.'
    if any(s in detail for s in ('available in your country', 'not available in your region', 'geo restricted', 'geo-restricted', 'geographic restriction', 'indisponível nesta região')):
        return 'Vídeo indisponível nesta região. Escolha outro vídeo disponível localmente.'
    if any(s in detail for s in ('removed', 'has been deleted', 'video unavailable', 'not available anymore', 'vídeo removido')):
        return 'Vídeo removido ou indisponível. Confira o link ou escolha outro vídeo.'
    if any(s in detail for s in ('timed out', 'timeout', 'connection', 'network', 'temporary failure', 'http error 5', 'http error 429', 'conexão falhou')):
        return 'A conexão falhou ou o servidor não respondeu. Verifique a rede e tente novamente; o mesmo destino permite retomar o parcial compatível.'
    return 'Não foi possível concluir o download. Confira o link, a conexão e a pasta de destino antes de tentar novamente.'


def main(argv: list[str]):
    parser = argparse.ArgumentParser(prog="horus-backend")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("transcribe")
    run.add_argument("--path", type=Path, required=True)
    run.add_argument("--model", choices=MODELS, required=True)
    run.add_argument("--language", choices=LANGUAGES, required=True)
    run.add_argument("--models-dir", type=Path, required=True)
    fetch = commands.add_parser("download")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    # O backend lê UTF-8; no Windows o console padrão (cp1252) quebra em texto transcrito.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    try:
        if args.command == "transcribe":
            transcribe(args.path, args.model, args.language, args.models_dir)
            return 0
        return download(args.url, args.directory)
    except Exception as error:  # noqa: BLE001 - o motivo segue para o log da tarefa
        print(download_error(error) if args.command == 'download' else safe_error(f"{type(error).__name__}: {error}"), file=sys.stderr, flush=True)
        return 1
