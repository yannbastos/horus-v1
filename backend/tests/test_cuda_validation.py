"""HOR-008: falhas simuladas e ensaio de hardware explicitamente opt-in, offline."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS

import pytest

from horus_backend import speech_alignment as sa, transcriber as tr


@pytest.mark.parametrize('layout', ['small', 'faster-whisper-small', 'whisper/faster-whisper-small',
                                  'models--Systran--faster-whisper-small/snapshots/' + 'a' * 40])
def test_resolve_preinstalled_and_existing_hub_cache(tmp_path, layout):
    folder = tmp_path / layout
    folder.mkdir(parents=True)
    (folder / 'model.bin').touch()
    if layout.startswith('models--'):
        refs = tmp_path / 'models--Systran--faster-whisper-small' / 'refs'
        refs.mkdir()
        (refs / 'main').write_text('a' * 40)
    assert sa.resolve_model_path(tmp_path, 'small') == folder


def test_cache_revision_cannot_escape_snapshots(tmp_path):
    refs = tmp_path / 'models--Systran--faster-whisper-small' / 'refs'
    refs.mkdir(parents=True)
    (refs / 'main').write_text('../../outside')
    assert sa.resolve_model_path(tmp_path, 'small') is None


def test_missing_model_never_invokes_downloader(monkeypatch, tmp_path):
    monkeypatch.setenv('HORUS_WHISPER_DEVICE', 'cpu')
    monkeypatch.setitem(sys.modules, 'ctranslate2', NS())
    monkeypatch.setitem(sys.modules, 'faster_whisper', NS(WhisperModel=lambda *a, **k: pytest.fail('download')))
    with pytest.raises(FileNotFoundError, match='Instale-o explicitamente'):
        tr.transcribe(tmp_path / 'audio.wav', 'small', 'pt', tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('failure', ['driver', 'oom', 'lazy', 'partial', 'media', 'cpu'])
def test_alignment_fallback_is_bounded_and_atomic(monkeypatch, tmp_path, failure):
    (tmp_path / 'model.bin').touch()
    monkeypatch.setenv('HORUS_WHISPER_DEVICE', 'cuda')
    calls = []

    def generator(device):
        if device == 'cuda' and failure in {'lazy', 'partial', 'media'}:
            if failure == 'partial':
                yield NS(words=[NS(word='parcial', start=0, end=1)])
            raise RuntimeError('invalid media' if failure == 'media' else 'CUDA out of memory')
        yield NS(words=[NS(word='teste', start=0, end=1)])

    def factory(path, **kw):
        calls.append(kw)
        assert kw['local_files_only'] is True
        if kw['device'] == 'cuda' and failure in {'driver', 'oom', 'cpu'}:
            raise RuntimeError('CUDA driver unavailable' if failure == 'driver' else 'CUDA out of memory')
        if kw['device'] == 'cpu' and failure == 'cpu':
            raise RuntimeError('CPU failure')
        return NS(transcribe=lambda *a, **k: (generator(kw['device']), NS(duration=2, language='pt')))

    monkeypatch.setitem(sys.modules, 'ctranslate2', NS(get_cuda_device_count=lambda: 1))
    monkeypatch.setitem(sys.modules, 'faster_whisper', NS(WhisperModel=factory))
    if failure in {'partial', 'media', 'cpu'}:
        with pytest.raises(RuntimeError):
            sa._recognize_words(tmp_path / 'a.wav', tmp_path, 'pt')
    else:
        payload = sa._recognize_words(tmp_path / 'a.wav', tmp_path, 'pt')
        assert payload['device'] == 'cpu'
        assert len(payload['words']) == 1
    assert [c['device'] for c in calls] == (['cuda'] if failure in {'partial', 'media'} else ['cuda', 'cpu'])
    assert [c['compute_type'] for c in calls] == (['float16'] if failure in {'partial', 'media'} else ['float16', 'int8'])


@pytest.mark.skipif(os.environ.get('HORUS_TEST_CUDA') != '1', reason='hardware CUDA opt-in')
@pytest.mark.parametrize('device,compute', [('cuda', 'float16'), ('cuda', 'int8'), ('cpu', 'int8')])
def test_real_cuda_offline(monkeypatch, tmp_path, device, compute):
    """Modelo e WAV sintético explicitamente fornecidos; nenhum download ou dado operacional."""
    if os.environ.get('HORUS_TEST_CUDA_CHILD') != '1':
        # Limite externo cobre também travamentos nativos que não lançam exceção Python.
        modes = ['raw'] if device == 'cuda' and compute == 'int8' else ['raw', 'alignment', 'transcription']
        for mode in modes:
            result = subprocess.run([sys.executable, '-m', 'pytest',
                f'{__file__}::test_real_cuda_offline[{device}-{compute}]', '-q', '-s'],
                env={**os.environ, 'HORUS_TEST_CUDA_CHILD': '1', 'HORUS_TEST_CUDA_MODE': mode},
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
            print(result.stdout, flush=True)
            assert result.returncode == 0, result.stdout + result.stderr
        return
    from faster_whisper import WhisperModel
    import ctranslate2

    model_path = Path(os.environ['HORUS_TEST_MODEL_PATH'])
    wav = Path(os.environ['HORUS_TEST_WAV'])
    script = os.environ['HORUS_TEST_SCRIPT']
    assert (model_path / 'model.bin').is_file() and wav.is_file()
    monkeypatch.setenv('HORUS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    monkeypatch.setenv('TRANSFORMERS_OFFLINE', '1')
    monkeypatch.setenv('HORUS_WHISPER_DEVICE', device)
    assert tr.choose_device(ctranslate2) == device
    samples = []
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            value = subprocess.check_output(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'], text=True)
            samples.append(int(value.splitlines()[0]))
            stop.wait(.1)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    started = time.perf_counter()
    mode = os.environ.get('HORUS_TEST_CUDA_MODE', 'raw')
    expect_fallback = os.environ.get('HORUS_TEST_EXPECT_FALLBACK') == '1'
    expected_device = 'cpu' if expect_fallback else device
    try:
        if mode == 'alignment':
            payload = sa._recognize_words(wav, model_path, 'pt')
            assert payload['device'] == expected_device
            assert len(payload['words']) >= 10
            return
        if mode == 'transcription':
            frames = []
            monkeypatch.setattr(tr, 'emit', lambda kind, message: frames.append((kind, message)))
            tr.transcribe(wav, 'small', 'pt', model_path.parent)
            assert any(k == 'segment' for k, _ in frames)
            assert any(f'Transcrevendo em {expected_device.upper()}' in m for k, m in frames)
            assert any('Alternando' in m for _, m in frames) is expect_fallback
            return
        print(f'HARDWARE loading {device}/{compute}', flush=True)
        model = WhisperModel(str(model_path), device=device, compute_type=compute, local_files_only=True)
        print(f'HARDWARE effective_compute_type={model.model.compute_type}', flush=True)
        print('HARDWARE decoding', flush=True)
        segments, info = model.transcribe(str(wav), language='pt', word_timestamps=True)
        words = [dict(word=w.word, start=float(w.start), end=float(w.end)) for s in segments for w in s.words or []]
        assert len(words) >= 10
        sha, duration = sa._wav_facts(wav)
        document, report = sa.align_with_report(script, words, sections=[dict(section=1, start=0, end=duration)],
            narration_sha256=sha, duration_seconds=duration, engine={'name': 'windows_sapi', 'voice': 'synthetic'},
            asr_engine=f'faster-whisper small ({device} {compute}, offline)')
        assert report['speech_aligned'] is True, report
    finally:
        elapsed = time.perf_counter() - started
        stop.set()
        monitor.join(timeout=5)
        assert samples, 'nvidia-smi não produziu amostras de VRAM'
        print(f'HARDWARE mode={mode} requested={device}/{compute} expected_device={expected_device} seconds={elapsed:.3f} baseline_MiB={samples[0]} peak_MiB={max(samples)}', flush=True)
