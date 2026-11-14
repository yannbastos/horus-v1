"""Opt-in Windows: queda real da árvore e recuperação em banco TEMPORÁRIO.

Com as cinco variáveis de docs/piloto-local.md e o extra backend[measure]:
  $env:HORUS_E2E_CRASH='1'
  python -m pytest backend/tests/test_crash_recovery_real.py -q -s -x
HORUS_E2E_CRASH_STAGES=render,align (padrão); também aceita narration.
Não injeta falhas, não altera o banco para simular recuperação e nunca usa dados operacionais.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import threading
import time

import httpx
import pytest

from horus_backend.worker_lock import WorkerLock

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / 'artifacts/crash-recovery'
STAGES = os.environ.get('HORUS_E2E_CRASH_STAGES', 'render,align').split(',')
REQUIRED = ('HORUS_PILOT_FACTORY', 'HORUS_ALIGNMENT_MODEL', 'HORUS_FFMPEG',
            'HORUS_KOKORO_PYTHON', 'HORUS_KOKORO_MODEL_DIR')
pytestmark = [pytest.mark.skipif(os.environ.get('HORUS_E2E_CRASH') != '1', reason='Ensaio real exige HORUS_E2E_CRASH=1'),
              pytest.mark.skipif(os.name != 'nt', reason='Ensaio de taskkill /T /F e Job Objects do Windows')]


def wait_for(read, predicate, *, timeout=600):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = read()
        if predicate(last):
            return last
        time.sleep(.1)
    pytest.fail(f'Tempo esgotado; último estado: {last}')


class Backend:
    def __init__(self, temporary, generation):
        import psutil  # extra measure já declarado; só importado no opt-in
        self.psutil = psutil
        self.data = temporary / 'data'
        self.data.mkdir(exist_ok=True)
        assert self.data.resolve().is_relative_to(temporary.resolve())
        self.database = self.data / 'horus.sqlite3'
        self.log = temporary / f'backend-{generation}.log'
        self.handle = self.log.open('wb')
        token = secrets.token_hex(24)
        env = {**os.environ, 'HORUS_DATA_DIR': str(self.data), 'HORUS_API_TOKEN': token,
               'HORUS_MANAGED': '0', 'HORUS_WORKFLOW_EXECUTOR': 'simulated',
               'HORUS_RADAR_EXECUTOR': 'hermes_simulated', 'HORUS_NARRATION_PAID_ENABLED': '0',
               'HORUS_HERMES_CONFIG': str(temporary / 'no-hermes.json'),
               'HORUS_HERMES_PROFILE': str(temporary / 'no-hermes-profile'),
               'FABRICA_HOME': os.environ['HORUS_PILOT_FACTORY'],
               'PYTHONPATH': str(ROOT / 'backend/src'), 'PYTHONUTF8': '1',
               'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'}
        self.process = subprocess.Popen([sys.executable, '-m', 'horus_backend', '--port', '0'],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=self.handle, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.owner = psutil.Process(self.process.pid)
        self.seen = {}
        self.mutex = threading.Lock()
        self.stop_monitor = threading.Event()
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()
        self.client = None
        self.kill_exit_code = None
        self.crash_evidence = None
        try:
            ready = wait_for(self._ready, lambda value: value is not None, timeout=60)
            self.client = httpx.Client(base_url=f"http://127.0.0.1:{ready['port']}",
                headers={'Authorization': 'Bearer ' + token}, timeout=10, trust_env=False)
            response = self.client.get('/health')
            assert response.status_code == 200, response.text
            self.database_id = response.json()['database_id']
            self.server_pid = ready['pid']
        except BaseException:
            self.close()
            raise

    def _ready(self):
        assert self.process.poll() is None, self.log.read_text(encoding='utf-8', errors='replace')
        for line in self.log.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get('event') == 'ready':
                return value
        return None

    def _sample(self):
        ps = self.psutil
        try:
            children = self.owner.children(recursive=True)
        except ps.NoSuchProcess:
            return
        for process in children:
            try:
                created = process.create_time()
                name = process.name()
                executable = Path(name).stem.lower()
                command = ' '.join(process.cmdline()).lower() if executable.startswith('python') else ''
                role = executable if executable in {'node', 'ffmpeg', 'ffprobe', 'msedge', 'chrome'} else next(
                    (tag for tag in ('speech_alignment', 'kokoro_worker', 'narration.production_worker',
                     'production_worker.py') if tag in command), name)
                with self.mutex:
                    self.seen[(process.pid, created)] = (process, {'pid': process.pid, 'created': created,
                        'name': name, 'role': role, 'parent_pid': process.ppid()})
            except ps.NoSuchProcess:
                pass  # processo transitório já terminou; não conta como testemunha ativa

    def _monitor(self):
        while not self.stop_monitor.wait(.05):
            self._sample()

    def alive(self):
        self._sample()
        with self.mutex:
            return [info for process, info in self.seen.values() if process.is_running()]

    def marker_processes(self):
        marker = str(self.data).lower()
        found = []
        for process in self.psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if marker in ' '.join(process.info['cmdline'] or []).lower():
                    found.append({'pid': process.pid, 'name': process.info['name']})
            except (self.psutil.NoSuchProcess, self.psutil.AccessDenied):
                continue
        return found

    def request(self, method, route, data=None, *, expected=200, key=None):
        headers = {'Idempotency-Key': key or secrets.token_hex(16)} if method != 'GET' else {}
        response = self.client.request(method, route, headers=headers, json=data)
        assert response.status_code == expected, (response.status_code, response.text)
        return response.json()

    def crash(self):
        before = self.alive()
        assert before, 'Nenhum filho real ativo: o teste não pode passar de forma vazia'
        self.crash_evidence = {'active_descendants_before_kill': before}
        assert self.process.poll() is None
        killed = subprocess.run([str(Path(os.environ['SYSTEMROOT']) / 'System32/taskkill.exe'),
            '/PID', str(self.process.pid), '/T', '/F'], capture_output=True, timeout=20)
        self.kill_exit_code = killed.returncode
        backend_exit = self.process.wait(timeout=20)
        self.crash_evidence.update(taskkill_exit_code=killed.returncode, backend_exit_code=backend_exit)
        assert backend_exit != 0, 'Encerramento gracioso não exercita o cenário de queda'
        try:
            wait_for(self.alive, lambda children: not children, timeout=15)
        finally:
            self.crash_evidence['alive_after_kill'] = self.alive()
            self.crash_evidence['marker_processes_after_kill'] = self.marker_processes()
        markers = self.crash_evidence['marker_processes_after_kill']
        assert not markers, f'Processo remanescente com diretório exclusivo do ensaio: {markers}'
        with self.mutex:
            observed = [info for _, info in self.seen.values()]
        self.crash_evidence['observed_descendants'] = observed
        return self.crash_evidence

    def close(self):
        try:
            if self.process.poll() is None:
                if self.client:
                    try:
                        self.client.post('/v1/shutdown')
                        self.process.wait(timeout=15)
                    except (httpx.HTTPError, subprocess.TimeoutExpired):
                        pass
                if self.process.poll() is None:
                    subprocess.run([str(Path(os.environ['SYSTEMROOT']) / 'System32/taskkill.exe'),
                        '/PID', str(self.process.pid), '/T', '/F'], capture_output=True, timeout=20)
                    self.process.wait(timeout=20)
            # Em falha, limpa só processos observados com identidade PID+criação,
            # sem transformar essa limpeza em prova de ausência após a queda.
            with self.mutex:
                processes = [process for process, _ in self.seen.values()]
            for process in processes:
                if process.is_running():
                    process.kill()
                    process.wait(timeout=10)
        finally:
            self.stop_monitor.set()
            self.monitor.join(timeout=5)
            if self.client:
                self.client.close()
            self.handle.close()
        return self.process.returncode


def manual_script(backend):
    workflow = backend.request('POST', '/v1/workflows',
        {'objective': 'Ensaio temporário de recuperação de produção local', 'references': ['Fixture do operador']}, expected=202)
    workflow = wait_for(lambda: backend.request('GET', '/v1/workflows/' + workflow['id']),
                        lambda value: value['status'] == 'awaiting_approval', timeout=60)
    backend.request('POST', f"/v1/workflows/{workflow['id']}/review", {'expected_revision': workflow['revision'],
        'artifact_id': workflow['current_artifact_id'], 'decision': 'approve'})
    document = {'title': 'Roteiro temporário para recuperação após queda', 'sections': [
        {'id': 'hook', 'kind': 'hook', 'heading': 'Gancho',
         'narration': 'Este roteiro foi escrito diretamente pelo usuário no Hórus.'},
        {'id': 'body', 'kind': 'body', 'heading': 'Desenvolvimento',
         'narration': 'Sem geração simulada de texto, seguimos direto para a aprovação e produção.'},
        {'id': 'end', 'kind': 'conclusion', 'heading': 'Conclusão',
         'narration': 'Aprovação humana registrada antes da produção local.'}]}
    script = backend.request('POST', f"/v1/workflows/{workflow['id']}/scripts", {'source': 'manual',
        'expected_revision': workflow['revision'], 'proposal_artifact_id': workflow['current_artifact_id'],
        'document': document}, expected=201)
    script = backend.request('POST', f"/v1/scripts/{script['id']}/review", {'expected_version': script['version'], 'decision': 'approve'})
    assert script['origin'] == 'human' and script['status'] == 'approved'
    return script


def start(backend, script):
    return backend.request('POST', f"/v1/scripts/{script['id']}/productions", {'expected_version': script['version'],
        'mode': 'local_pilot', 'render_quality': 'draft', 'narration': {'provider': 'kokoro', 'voice_id': 'pf_dora'}}, expected=201)


@pytest.mark.parametrize('kill_stage', STAGES)
def test_real_process_tree_crash_and_new_production(tmp_path, kill_stage):
    assert kill_stage in {'render', 'align', 'narration'}, 'Use HORUS_E2E_CRASH_STAGES=render,align ou narration'
    assert all(os.environ.get(key) for key in REQUIRED), f'Configure {REQUIRED} conforme docs/piloto-local.md'
    try:
        import psutil  # noqa: F401
    except ImportError:
        pytest.fail('Instale o extra backend[measure] (psutil) no ambiente isolado para observar os filhos reais.')
    began = time.monotonic()
    report = {'kill_stage': kill_stage, 'success': False, 'timings': {}, 'temporary_directory': str(tmp_path)}
    backends = []

    def timed(name, action):
        began_step = time.monotonic()
        try:
            return action()
        finally:
            report['timings'][name] = round(time.monotonic() - began_step, 3)

    try:
        backend = timed('start_backend', lambda: Backend(tmp_path, 1))
        backends.append(backend)
        with pytest.raises(RuntimeError, match='Outro backend'):
            WorkerLock(backend.database).acquire()
        script = timed('manual_script_and_approval', lambda: manual_script(backend))
        first = start(backend, script)
        report['interrupted_production_id'] = first['id']

        def ready_to_kill():
            record = backend.request('GET', '/v1/productions/' + first['id'])
            assert record['status'] in {'queued', 'running'}, record
            children = backend.alive()
            wanted = {'render': 'node', 'align': 'speech_alignment', 'narration': 'kokoro_worker'}[kill_stage]
            return record, [child for child in children if child['role'] == wanted]

        record, witnesses = timed('wait_real_stage_and_child', lambda: wait_for(ready_to_kill,
            lambda value: value[0]['stage'] == kill_stage and bool(value[1])))
        report['stage_at_kill'] = record['stage']
        report['stage_process_witnesses'] = witnesses
        report['crash'] = timed('force_kill_and_verify_no_children', backend.crash)
        with closing(sqlite3.connect(f'{backend.database.as_uri()}?mode=ro', uri=True)) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert db.execute('SELECT status FROM jobs WHERE id=?', (first['job_id'],)).fetchone()[0] == 'running'
        lock = WorkerLock(backend.database)
        lock.acquire()
        lock.close()
        report['lock_released_after_crash'] = True
        old_root = backend.data / 'productions' / first['id']
        report['partial_mp4_files_on_disk'] = [{'path': p.relative_to(old_root).as_posix(), 'bytes': p.stat().st_size}
                                              for p in old_root.rglob('*.mp4')]
        restarted = timed('restart_same_directory', lambda: Backend(tmp_path, 2))
        backends.append(restarted)
        assert backend.database_id == restarted.database_id
        with pytest.raises(RuntimeError, match='Outro backend'):
            WorkerLock(restarted.database).acquire()
        recovered = restarted.request('GET', '/v1/productions/' + first['id'])
        report['recovered'] = recovered
        assert recovered['status'] == 'interrupted'
        assert recovered['failure']['code'] == 'production_interrupted'
        assert recovered['failure']['stage'] == kill_stage
        assert not any(a['kind'] == 'video' for a in recovered['artifacts'])
        assert recovered['review'] is None and not recovered['publishable']
        restarted.request('POST', f"/v1/jobs/{first['job_id']}/retry", expected=409)
        next_production = start(restarted, script)
        assert next_production['id'] != first['id']
        new = timed('new_production_to_terminal', lambda: wait_for(
            lambda: restarted.request('GET', '/v1/productions/' + next_production['id']),
            lambda value: value['status'] not in {'queued', 'running', 'cancel_requested'}))
        report['new_production'] = new
        assert new['status'] == 'succeeded', new
        assert new['quality_report'] is not None and new['quality_report']['status'] in {'pass', 'warn'}
        assert not new['quality_report']['blocking_errors']
        assert not [c for c in new['quality_report']['checks'] if c['status'] == 'fail']
        video = next(a for a in new['artifacts'] if a['kind'] == 'video')
        with (restarted.data / 'productions' / new['id'] / video['relative_path']).open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == video['sha256']
        assert restarted.request('GET', '/v1/productions/' + first['id'])['status'] == 'interrupted'
        report['success'] = True
    except BaseException as error:
        report['error'] = repr(error)
        raise
    finally:
        already_failed = sys.exc_info()[0] is not None
        report['backend_exit_codes'], cleanup_errors = {}, []
        for index in reversed(range(len(backends))):
            backend = backends[index]
            if backend.crash_evidence:
                report['crash'] = backend.crash_evidence
            try:
                report['backend_exit_codes'][str(index + 1)] = backend.close()
            except BaseException as error:
                cleanup_errors.append(repr(error))
        if cleanup_errors:
            report['success'] = False
            report['cleanup_errors'] = cleanup_errors
        report['elapsed_seconds'] = round(time.monotonic() - began, 3)
        REPORTS.mkdir(parents=True, exist_ok=True)
        (REPORTS / f'{kill_stage}.json').write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
        print(json.dumps({'stage': kill_stage, 'success': report['success'], 'elapsed_seconds': report['elapsed_seconds'],
                          'backend_exit_codes': report['backend_exit_codes'], 'crash': report.get('crash', {})}))
        if cleanup_errors and not already_failed:
            pytest.fail(f'Falha ao encerrar somente os processos próprios do ensaio: {cleanup_errors}')
