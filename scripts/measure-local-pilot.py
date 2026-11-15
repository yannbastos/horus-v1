"""Observação externa opt-in da jornada real: não altera código ou saída da produção."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

import psutil


def role(command):
    joined = ' '.join(command).lower()
    if command and Path(command[0]).name.lower() in {'ffmpeg', 'ffmpeg.exe'}:
        if '-frames:v' in command:
            return 'ffmpeg_thumbnail'
        if 'image2pipe' in command:
            return 'ffmpeg_qc_visual'
        if 'subtitles=' in joined:
            return 'ffmpeg_subtitle_overlay'
        if 'ebur128=' in joined or 'loudnorm=' in joined:
            return 'ffmpeg_loudness'
        return 'ffmpeg'
    for marker in ('kokoro_worker.py', 'speech_alignment', 'narration.production_worker',
                   'production_worker', 'ffmpeg', 'ffprobe', 'remotion', 'msedge', 'vite', 'playwright'):
        if marker in joined:
            return marker
    return Path(command[0]).name if command else 'unknown'


def artifact_paths(data):
    """Caminhos fixos: nunca varrer node_modules durante o benchmark."""
    production_root = data / 'productions'
    if not production_root.is_dir():
        return
    for production in production_root.iterdir():
        render = production / 'canais/demo/videos/fixture/render'
        for name in ('pilot-evidence.json', 'quality-report.json'):
            yield production / name
        for name in ('validation.json', 'caption-overlay.json', 'short.mp4', 'short-rascunho.mp4'):
            yield render / name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--interval', type=float, default=1.0, help='Amostragem em segundos (0.1–5); padrão 1 para limitar o custo do observador.')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not .1 <= args.interval <= 5:
        parser.error('--interval deve estar entre 0.1 e 5 segundos')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('forneça o comando após --')
    sandbox = Path(tempfile.mkdtemp(prefix='hp-'))
    env = {**os.environ, 'TEMP': str(sandbox), 'TMP': str(sandbox)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log = args.output.with_suffix('.log')
    started = time.perf_counter()
    records, stages, facts, stamps = {}, {}, {}, {}
    peak_rss = peak_cpu = 0
    previous_cpu = 0
    intervals = []
    previous_time = started
    with log.open('w', encoding='utf-8') as output:
        process = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT)
        parent = psutil.Process(process.pid)
        while process.poll() is None:
            now = time.perf_counter()
            intervals.append(now-previous_time)
            rss = cpu = 0
            try:
                children = [parent, *parent.children(recursive=True)]
            except psutil.Error:
                children = []
            for child in children:
                try:
                    with child.oneshot():
                        key = str(child.pid)
                        item = records.setdefault(key, {'role': role(child.cmdline()),
                            'first_seconds': round(now-started, 3), 'peak_rss_bytes': 0, 'cpu_seconds': 0})
                        if item['role'] == 'kokoro_worker.py' and 'section' not in item:
                            request = Path(child.cmdline()[-1])
                            if request.is_relative_to(sandbox):
                                try:
                                    item['section'] = Path(json.loads(request.read_text(encoding='utf-8'))['output']).name
                                except (OSError, ValueError, KeyError):
                                    pass
                        memory = child.memory_info().rss
                        item['last_seconds'] = round(now-started, 3)
                        item['peak_rss_bytes'] = max(item['peak_rss_bytes'], memory)
                        times = child.cpu_times()
                        item['cpu_seconds'] = times.user + times.system
                        item['peak_threads'] = max(item.get('peak_threads', 0), child.num_threads())
                        rss += memory
                except psutil.Error:
                    pass
            cpu = sum(item['cpu_seconds'] for item in records.values())
            peak_rss = max(peak_rss, rss)
            peak_cpu = max(peak_cpu, 100 * max(0, cpu-previous_cpu) / max(.01, now-previous_time))
            previous_cpu, previous_time = cpu, now
            for data in sandbox.glob('horus-e2e-*'):
                database = data / 'horus.sqlite3'
                if database.exists():
                    try:
                        with sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True, timeout=.1) as db:
                            for row in db.execute("SELECT job_id,kind,message,created_at FROM events WHERE kind IN ('stage','state') ORDER BY sequence"):
                                stages.setdefault('|'.join(str(v) for v in row), list(row))
                    except sqlite3.Error:
                        pass
                for artifact in artifact_paths(data):
                    relative = artifact.relative_to(data).as_posix()
                    try:
                        stamp = artifact.stat()
                        if stamp.st_size and stamps.get(relative) != stamp.st_mtime_ns:
                            if artifact.suffix == '.json':
                                facts[relative] = json.loads(artifact.read_text(encoding='utf-8'))
                                stamps[relative] = stamp.st_mtime_ns
                            # Hash MP4 só quando a produção tem QC: não ler arquivo ainda em escrita.
                            elif any((parent / 'quality-report.json').is_file() for parent in artifact.parents if parent.is_relative_to(data)):
                                with artifact.open('rb') as stream:
                                    facts[relative] = {'sha256': hashlib.file_digest(stream, 'sha256').hexdigest(), 'size_bytes': stamp.st_size}
                                stamps[relative] = stamp.st_mtime_ns
                    except (OSError, ValueError, IndexError):
                        pass
            time.sleep(args.interval)
    result = {'command_kind': 'real_journey', 'exit_code': process.returncode,
        'elapsed_seconds': round(time.perf_counter()-started, 3), 'sample_seconds': args.interval,
        'logical_cpus': psutil.cpu_count(), 'physical_cpus': psutil.cpu_count(logical=False),
        'observer_cpu_seconds': sum(psutil.Process().cpu_times()[:2]),
        'sample_interval_mean_seconds': sum(intervals)/len(intervals) if intervals else 0,
        'sample_interval_max_seconds': max(intervals, default=0),
        'peak_tree_rss_bytes': peak_rss, 'peak_cpu_percent_one_core': round(peak_cpu, 2),
        'processes': records, 'events': list(stages.values()), 'artifacts': facts, 'sandbox': str(sandbox), 'log': str(log)}
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('exit_code', 'elapsed_seconds', 'peak_tree_rss_bytes')}))
    raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
