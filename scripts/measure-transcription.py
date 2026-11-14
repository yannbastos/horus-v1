"""HOR-017: mídia sintética e inferência local opt-in, sem baixar modelos.

HORUS_E2E_TRANSCRIPTION=1, HORUS_FFMPEG e HORUS_WHISPER_MODELS_DIR obrigatórios.
Mede subprocessos do JobManager real em banco descartável; não usa dados pessoais.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

import psutil
from fastapi.testclient import TestClient
from horus_backend.api import create_app
from horus_backend.config import Settings


def run(command):
    subprocess.run(command, check=True, capture_output=True, timeout=300)


def generate(folder, ffmpeg, duration):
    speech = folder / 'speech.wav'
    # Texto criado exclusivamente para o ensaio, sem conteúdo do operador.
    code = ("Add-Type -AssemblyName System.Speech;"
            "$voice=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$voice.SetOutputToWaveFile('{str(speech).replace(chr(39), chr(39)*2)}');"
            "$voice.Speak('Local transcription test. The blue bird flies over the quiet river. "
            "We are measuring progress and keeping the partial text safe.');$voice.Dispose()")
    run(['powershell', '-NoProfile', '-Command', code])
    cycle = folder / 'cycle.wav'
    run([ffmpeg, '-v', 'error', '-y', '-i', str(speech), '-af', 'apad', '-t', '30', '-ar', '16000', '-ac', '1', str(cycle)])
    short = folder / 'sample.wav'
    run([ffmpeg, '-v', 'error', '-y', '-stream_loop', '-1', '-i', str(cycle), '-t', '20', str(short)])
    long = folder / 'long.wav'
    run([ffmpeg, '-v', 'error', '-y', '-stream_loop', '-1', '-i', str(cycle), '-t', str(duration), str(long)])
    media = {'wav': short}
    for ext, codec in [('mp3', 'libmp3lame'), ('m4a', 'aac'), ('ogg', 'libopus'), ('opus', 'libopus'), ('flac', 'flac')]:
        target = folder / f'sample.{ext}'
        run([ffmpeg, '-v', 'error', '-y', '-i', str(short), '-c:a', codec, str(target)])
        media[ext] = target
    for ext, video, audio in [('mp4', 'libx264', 'aac'), ('mkv', 'libx264', 'flac'), ('webm', 'libvpx-vp9', 'libopus')]:
        target = folder / f'sample.{ext}'
        run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=1',
             '-i', str(short), '-c:v', video, '-c:a', audio, '-shortest', str(target)])
        media[ext] = target
    return media, long


def measure(client, path, cancel_at=None, interrupt_at=None):
    started = time.monotonic()
    job = client.post('/v1/jobs', json=dict(operation='transcription.run', path=str(path), model='small', language='en')).json()
    manager = client.app.state.manager
    processes, rss, cpu, gpu, progress = {}, [], [], [], []
    last_sequence, first_text, cancelled, interrupted = 0, None, False, False
    stop_started = None
    deadline = started + 3600
    while time.monotonic() < deadline:
        job = client.get(f"/v1/jobs/{job['id']}").json()
        active = manager.active_process
        if active:
            try:
                process = psutil.Process(active.process.pid)
                tree = [process, *process.children(recursive=True)]
                rss.append(sum(p.memory_info().rss for p in tree))
                total_cpu = 0
                for member in tree:
                    if member.pid not in processes:
                        member.cpu_percent()
                        processes[member.pid] = member
                    total_cpu += processes[member.pid].cpu_percent()
                cpu.append(total_cpu)
            except psutil.Error:
                pass
        for event in client.app.state.store.events(job['id'], last_sequence):
            last_sequence = event['sequence']
            if event['kind'] == 'progress':
                progress.append(int(event['message']))
        if (job.get('result') or {}).get('text'):
            if first_text is None:
                first_text = round(time.monotonic() - started, 3)
        if progress and cancel_at and max(progress) >= cancel_at and not cancelled:
            stop_started = time.monotonic()
            client.post(f"/v1/jobs/{job['id']}/cancel")
            cancelled = True
        if progress and interrupt_at and max(progress) >= interrupt_at and not interrupted:
            stop_started = time.monotonic()
            client.portal.call(manager.close)
            interrupted = True
        if job['status'] in {'succeeded', 'cancelled', 'failed', 'blocked', 'interrupted'}:
            break
        if len(rss) % 5 == 1:
            try:
                raw = subprocess.check_output(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used', '--format=csv,noheader,nounits'],
                                              timeout=5, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                gpu.append([int(n.strip()) for n in raw.strip().splitlines()[0].split(',')])
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
        time.sleep(.5)
    else:
        raise AssertionError('Ensaio excedeu uma hora')
    job = client.get(f"/v1/jobs/{job['id']}").json()
    result = job.get('result') or {}
    stop_seconds = round(time.monotonic() - stop_started, 3) if stop_started else None
    assert result.get('text'), job
    assert job['status'] == ('interrupted' if interrupt_at else 'cancelled' if cancel_at else 'succeeded'), job
    if not cancel_at and not interrupt_at:
        assert progress[-1] == 100, progress
    # Reconexão de leitura: checkpoint sobrevive a outro lifespan sem inferência repetida.
    before = job
    client.portal.call(manager.close)
    client.portal.call(manager.start)
    after = client.get(f"/v1/jobs/{job['id']}").json()
    assert before['result'] == after['result'] and after['attempt'] == 1
    if cancel_at or interrupt_at:
        assert client.post(f"/v1/jobs/{job['id']}/retry").status_code == 409
    assert all(not p.is_running() for p in processes.values()), 'Processo órfão'
    events = client.app.state.store.events(job['id'], 0)
    report = dict(file=path.name, bytes=path.stat().st_size, status=job['status'], seconds=round(time.monotonic()-started, 3),
                  first_text_seconds=first_text, peak_rss_mib=round(max(rss, default=0)/1024**2, 2),
                  stop_seconds=stop_seconds,
                  cpu_mean_percent=round(statistics.mean(cpu), 2) if cpu else None, cpu_peak_percent=max(cpu, default=0),
                  gpu_peak_percent=max((x[0] for x in gpu), default=None), gpu_peak_memory_mib=max((x[1] for x in gpu), default=None),
                  progress=progress, text_characters=len(result['text']), checkpoint_recovered=True, orphan=False,
                  statuses=[e['message'] for e in events if e['kind'] == 'status'])
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=int, default=1800)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('HORUS_E2E_TRANSCRIPTION') != '1':
        parser.error('Exige HORUS_E2E_TRANSCRIPTION=1')
    model_root = Path(os.environ['HORUS_WHISPER_MODELS_DIR']).resolve()
    required_root = Path(os.environ['LOCALAPPDATA']) / 'Horus/models/whisper'
    assert model_root == required_root.resolve(), 'Use somente o modelo previamente preparado autorizado'
    assert all((model_root / 'faster-whisper-small' / p).is_file() for p in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'))
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    reports = []
    with tempfile.TemporaryDirectory(prefix='horus-transcription-') as temporary:
        folder = Path(temporary)
        media, long = generate(folder, os.environ['HORUS_FFMPEG'], args.duration)
        settings = Settings(token='synthetic-test-token-0123456789', data_dir=folder / 'data', factory_root=None,
                            hermes_executable=None, hermes_config=folder / 'no-hermes.json')
        with TestClient(create_app(settings), base_url='http://127.0.0.1',
                        headers={'Authorization': f'Bearer {settings.token}'}) as client:
            try:
                for path in media.values():
                    reports.append(measure(client, path))
                    args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
                for options in ({}, {'cancel_at': 50}, {'interrupt_at': 5}):
                    reports.append(measure(client, long, **options))
                    args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
            finally:
                args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
