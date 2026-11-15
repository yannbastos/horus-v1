"""Jornada API do desktop instalado, dados descartáveis e mídia fictícia; sem downloads."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import winreg

import psutil

smoke = importlib.import_module('smoke-windows-package')


def installed():
    entries = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Uninstall')
    except FileNotFoundError:
        return entries
    with root:
        for index in range(winreg.QueryInfoKey(root)[0]):
            with winreg.OpenKey(root, winreg.EnumKey(root, index)) as item:
                try:
                    if winreg.QueryValueEx(item, 'DisplayName')[0] == 'HORUS':
                        entries.append(winreg.QueryValueEx(item, 'DisplayVersion')[0])
                except FileNotFoundError:
                    pass
    return entries


class API:
    def __init__(self, port, token, report):
        self.base, self.token, self.report = f'http://127.0.0.1:{port}', token, report
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, payload=None, method=None, raw=False):
        started = time.monotonic()
        request = urllib.request.Request(self.base + path, method=method or ('POST' if payload is not None else 'GET'),
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
                     'Idempotency-Key': uuid.uuid4().hex})
        try:
            response = self.opener.open(request, timeout=180)
        except urllib.error.HTTPError as error:
            detail = error.read().decode('utf-8', errors='replace')
            raise AssertionError(f'{request.method} {path}: HTTP {error.code}: {detail}') from None
        with response:
            data = response.read()
            self.report['requests'].append(dict(method=request.method, path=path, status=response.status,
                                               seconds=round(time.monotonic() - started, 3)))
            return data if raw else json.loads(data)

    def wait(self, path, key, accepted, seconds=1200):
        deadline = time.monotonic() + seconds
        previous = None
        while time.monotonic() < deadline:
            record = self.call(path)
            state = (record.get(key), record.get('stage'))
            if state != previous:
                print(json.dumps(dict(path=path, state=state)), flush=True)
                previous = state
            if record.get(key) in accepted:
                return record
            if record.get(key) in {'failed', 'blocked', 'interrupted', 'cancelled'}:
                raise AssertionError(json.dumps(record, ensure_ascii=False))
            time.sleep(1)
        raise TimeoutError(f'Prazo esgotado: {path}')


def journey(api, args, report):
    report['diagnostics_before_import'] = api.call('/v1/diagnostics')
    report['models_before'] = api.call('/v1/transcription/models')
    report['imported_model'] = api.call('/v1/transcription/models/import',
        dict(model='small', source_directory=str(args.whisper_source), confirm_license=True))
    report['models_after'] = api.call('/v1/transcription/models')
    report['diagnostics_after_import'] = api.call('/v1/diagnostics')
    assert next(c for c in report['diagnostics_after_import']['production']['checks']
                if c['id'] == 'alignment_model')['available'], 'Modelo importado não disponível para alinhamento.'
    report['narration_providers'] = api.call('/v1/narration/providers')
    report['preference'] = api.call('/v1/narration/preference', dict(provider='kokoro', voice_id='pf_dora'), 'PUT')
    preview = api.call('/v1/narration/preview', dict(provider='kokoro', voice_id='pf_dora',
        text='Este é um ensaio fictício do aplicativo instalado, sem publicação.'))
    wav = api.call(preview['url'], raw=True)
    assert wav[:4] == b'RIFF' and wav[8:12] == b'WAVE'
    report['preview_bytes'] = len(wav)
    workflow = api.call('/v1/workflows', dict(objective='Ensaio fictício do primeiro vídeo instalado, sem publicação.'))
    workflow = api.wait('/v1/workflows/' + workflow['id'], 'status', {'awaiting_approval'}, 60)
    api.call(f"/v1/workflows/{workflow['id']}/review", dict(expected_revision=workflow['revision'],
        artifact_id=workflow['current_artifact_id'], decision='approve', feedback='Proposta fictícia aprovada somente para teste local.'))
    document = dict(schema_version=1, simulated=False, title='Primeiro vídeo fictício no aplicativo instalado', sections=[
        dict(id='hook', kind='hook', heading='Abertura', source_ids=[],
             narration='Este é um vídeo fictício para conferir o aplicativo instalado. Nenhum conteúdo será publicado.'),
        dict(id='body', kind='body', heading='Verificação', source_ids=[],
             narration='A voz local, as legendas e a imagem são verificadas antes da revisão. Os modelos foram importados sem download.'),
        dict(id='conclusion', kind='conclusion', heading='Encerramento', source_ids=[],
             narration='A decisão final continua humana. Este pacote serve apenas como evidência de teste e não concede direitos comerciais.')])
    script = api.call(f"/v1/workflows/{workflow['id']}/scripts", dict(source='manual',
        expected_revision=workflow['revision'], proposal_artifact_id=workflow['current_artifact_id'], document=document))
    script = api.call(f"/v1/scripts/{script['id']}/review", dict(expected_version=script['version'], decision='approve',
        feedback='Roteiro sintético aprovado exclusivamente para ensaio isolado.'))
    production = api.call(f"/v1/scripts/{script['id']}/productions", dict(expected_version=script['version'],
        mode='local_pilot', render_quality='draft', narration=dict(provider='kokoro', voice_id='pf_dora')))
    report['production_id'] = production['id']
    try:
        production = api.wait('/v1/productions/' + production['id'], 'status', {'succeeded'})
    except Exception:
        report['production_failure'] = api.call('/v1/productions/' + production['id'])
        raise
    report['production'] = production
    assert production['stage'] == 'review'
    assert production['quality_report']['status'] != 'fail'
    video = next(a for a in production['artifacts'] if a['kind'] == 'video')
    content = api.call(f"/v1/productions/{production['id']}/artifacts/{video['id']}", raw=True)
    assert hashlib.sha256(content).hexdigest() == video['sha256']
    report['review'] = api.call(f"/v1/productions/{production['id']}/review", dict(decision='approve',
        artifact_sha256=video['sha256'], acknowledge_quality_errors=True,
        notes=[dict(at_seconds=0.0, text='Decisão FICTÍCIA do ensaio local; sem licença ou autorização de publicação.')]))
    report['export'] = api.call(f"/v1/productions/{production['id']}/export", {})
    archive = api.call(f"/v1/productions/{production['id']}/exports/{report['export']['id']}/file", raw=True)
    assert hashlib.sha256(archive).hexdigest() == report['export']['sha256']
    (args.report_dir / 'first-video-package.zip').write_bytes(archive)
    report['success'] = True


def main(args):
    if installed():
        raise RuntimeError('HORUS previamente instalado: ensaio recusado para preservar a instalação existente.')
    args.report_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='horus-installed-first-video-')).resolve()
    install, data = temporary / 'app', temporary / 'data'
    report = dict(success=False, temporary=str(temporary), requests=[], fixture_only=True)
    proc = None
    children = []
    try:
        command = subprocess.list2cmdline([str(args.installer)]) + ' /S /D=' + str(install)
        result = subprocess.run(command, timeout=180)
        assert result.returncode == 0
        assert (install / 'horus-desktop.exe').is_file()
        env = {k: v for k, v in os.environ.items() if not k.startswith(('HORUS_', 'FABRICA_', 'PYTHON', 'UV_', 'VIRTUAL_ENV'))}
        env.update(HORUS_DATA_DIR=str(data), FABRICA_HOME=str(args.factory), HORUS_FFMPEG=str(args.ffmpeg),
            HORUS_KOKORO_PYTHON=str(args.kokoro_python), HORUS_KOKORO_MODEL_DIR=str(args.kokoro_model_dir),
            HORUS_HERMES_CONFIG=str(data / 'missing-hermes.json'), HORUS_HERMES_PROFILE=str(data / 'hermes-profile'),
            HORUS_WORKFLOW_EXECUTOR='simulated', HORUS_RADAR_EXECUTOR='hermes_simulated')
        proc = subprocess.Popen([str(install / 'horus-desktop.exe')], env=env, cwd=temporary,
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            children = psutil.Process(proc.pid).children(recursive=True)
            backend = next((p for p in children if p.name() == 'horus-backend.exe'), None)
            if backend:
                port = next((c.laddr.port for c in backend.net_connections(kind='tcp') if c.status == 'LISTEN'), None)
                if port:
                    config = backend.environ()
                    assert config['HORUS_DATA_DIR'] == str(data)
                    api = API(port, config['HORUS_API_TOKEN'], report)
                    try:
                        assert api.call('/health')['status'] == 'ready'
                        break
                    except (OSError, AssertionError):
                        pass
            time.sleep(.2)
        else:
            raise TimeoutError('Backend instalado não iniciou.')
        journey(api, args, report)
    except Exception as error:
        report['error'] = str(error)
        print(report['error'], flush=True)
    finally:
        if proc:
            if proc.poll() is None:
                children = psutil.Process(proc.pid).children(recursive=True)
                smoke.close_window(proc.pid)
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
            _, alive = psutil.wait_procs(children, timeout=10)
            report['orphan_processes'] = [p.pid for p in alive]
            for child in alive:
                child.kill()
            psutil.wait_procs(alive, timeout=10)
        uninstaller = install / 'uninstall.exe'
        if uninstaller.is_file():
            subprocess.run([str(uninstaller), '/S'], timeout=120)
            deadline = time.monotonic() + 30
            while ((install / 'horus-desktop.exe').exists() or installed()) and time.monotonic() < deadline:
                time.sleep(.2)
            report['uninstalled'] = not (install / 'horus-desktop.exe').exists() and not installed()
        (args.report_dir / 'installed-first-video.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if report['success'] and report.get('uninstalled') and not report.get('orphan_processes'):
            assert temporary.parent == Path(tempfile.gettempdir()).resolve() and temporary.name.startswith('horus-installed-first-video-')
            shutil.rmtree(temporary)
    return 0 if report['success'] and report.get('uninstalled') and not report.get('orphan_processes') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('installer', 'factory', 'whisper-source', 'kokoro-python', 'kokoro-model-dir', 'ffmpeg', 'report-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    options = parser.parse_args()
    for name in ('installer', 'factory', 'whisper_source', 'kokoro_python', 'kokoro_model_dir', 'ffmpeg'):
        setattr(options, name, getattr(options, name).resolve(strict=True))
    raise SystemExit(main(options))
