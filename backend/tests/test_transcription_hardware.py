"""Ensaio longo real, explicitamente opt-in; sem mídia do usuário ou download."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get('HORUS_E2E_TRANSCRIPTION') != '1' or os.name != 'nt',
                    reason='Exige Windows, modelo local, FFmpeg e HORUS_E2E_TRANSCRIPTION=1')
def test_long_transcription_real_formats_and_checkpoint(tmp_path):
    root = Path(__file__).resolve().parents[2]
    output = Path(os.environ.get('HORUS_TRANSCRIPTION_REPORT', tmp_path / 'hardware.json'))
    result = subprocess.run([sys.executable, str(root / 'scripts/measure-transcription.py'), '--output', str(output)],
                            cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=7200)
    assert result.returncode == 0, result.stdout + result.stderr
