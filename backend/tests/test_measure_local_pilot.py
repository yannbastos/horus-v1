"""Observador opt-in: sem produção, modelos ou rede nos testes."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip('psutil')
SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/measure-local-pilot.py'
spec = importlib.util.spec_from_file_location('measure_local_pilot', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_artifact_observer_never_walks_renderer_dependencies(tmp_path, monkeypatch):
    root = tmp_path / 'productions/id'
    root.mkdir(parents=True)
    monkeypatch.setattr(Path, 'glob', lambda *a, **k: pytest.fail('Varredura recursiva no benchmark'))
    monkeypatch.setattr(Path, 'rglob', lambda *a, **k: pytest.fail('Varredura recursiva no benchmark'))
    paths = list(module.artifact_paths(tmp_path))
    assert root / 'quality-report.json' in paths
    assert root / 'canais/demo/videos/fixture/render/validation.json' in paths
    assert len(paths) == 6


@pytest.mark.parametrize('arguments,expected', [
    (['ffmpeg.exe', '-frames:v', '1', 'frame.png'], 'ffmpeg_thumbnail'),
    (['ffmpeg', '-f', 'image2pipe', '-'], 'ffmpeg_qc_visual'),
    (['ffmpeg.exe', '-vf', 'subtitles=fixture.srt'], 'ffmpeg_subtitle_overlay'),
    (['ffmpeg.exe', '-af', 'loudnorm=print_format=json'], 'ffmpeg_loudness'),
    (['python.exe', '-I', 'kokoro_worker.py'], 'kokoro_worker.py'),
])
def test_process_classification_without_recording_raw_arguments(arguments, expected):
    assert module.role(arguments) == expected


def test_observer_preserves_child_exit_and_collects_resource_report(tmp_path):
    report = tmp_path / 'metrics.json'
    result = subprocess.run([sys.executable, str(SCRIPT), '--output', str(report), '--', sys.executable,
        '-c', 'import time; print("fixture output"); time.sleep(.5); raise SystemExit(3)'],
        env={**os.environ, 'TEMP': str(tmp_path), 'TMP': str(tmp_path)}, capture_output=True, timeout=15)
    assert result.returncode == 3
    document = json.loads(report.read_text(encoding='utf-8'))
    assert document['exit_code'] == 3 and document['elapsed_seconds'] >= .5
    assert document['sample_seconds'] == 1
    assert document['peak_tree_rss_bytes'] > 0
    assert document['processes']
    assert 'fixture output' in report.with_suffix('.log').read_text(encoding='utf-8')


def test_invalid_interval_is_rejected_before_starting_child(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), '--output', str(tmp_path / 'metrics.json'),
        '--interval', '0', '--', sys.executable, '-c', 'raise RuntimeError("never")'], capture_output=True, timeout=15)
    assert result.returncode == 2
    assert not (tmp_path / 'metrics.json').exists()
