"""HOR-096 (rede de segurança): caracteriza o comportamento ATUAL de interrupção/recuperação da produção.

Estes testes documentam que hoje NADA é reaproveitado após uma interrupção: a tentativa interrompida fica preservada para
conciliação, não é registrada, não é retomada e nenhum arquivo dela entra numa nova produção. Quando a retomada segura for
implementada (docs/retomada-de-pipeline.md), cada afirmação "não reaproveita" aqui deve ser revisada de forma consciente —
nunca apagada em silêncio. Sem rede, modelos, render real nem provedores pagos; não altera código de produção.
"""
import hashlib
import json
from pathlib import Path

import pytest

from horus_backend import local_pilot
from horus_backend.processes import OwnedProcess
from test_production import script_for, start, finished
from test_workflows import settings, client_for  # noqa: F401


def count_worker_runs(monkeypatch):
    original, runs = OwnedProcess.start, []

    async def counting(command, **kwargs):
        if 'production_worker' in str(command):
            runs.append(Path(command[2]))
        return await original(command, **kwargs)
    monkeypatch.setattr(OwnedProcess, 'start', counting)
    return runs


def tree_hashes(root: Path):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*')) if p.is_file()}


def interrupt(settings, stage):
    """Primeira sessão: produção em andamento que termina sem concluir, deixando saída parcial na pasta da tentativa."""
    with client_for(settings) as client:
        script = script_for(client)
        client.portal.call(client.app.state.manager.worker.cancel)
        item = start(client, script).json()
        root = settings.data_dir / 'productions' / item['id']
        (root / 'canais' / 'demo' / 'videos' / 'fixture').mkdir(parents=True)
        (root / 'plan.json').write_text('{"parcial":true}', encoding='utf-8')
        (root / 'canais/demo/videos/fixture/fixture.wav').write_bytes(b'RIFF-parcial')
        client.app.state.store.update(item['job_id'], 'running', new_attempt=True, result={'stage': stage})
    return script, item, root


@pytest.mark.parametrize('stage', ['narration', 'align', 'render'])
def test_restart_runs_no_worker_and_leaves_attempt_files_byte_identical(settings, monkeypatch, stage):
    runs = count_worker_runs(monkeypatch)
    script, item, root = interrupt(settings, stage)
    before = tree_hashes(root)
    with client_for(settings) as client:
        record = client.get('/v1/productions/' + item['id']).json()
        assert record['status'] == 'interrupted' and record['stage'] == stage and record['artifacts'] == []
        assert record['error']
    assert runs == []                      # reiniciar o backend nunca relança o worker
    assert tree_hashes(root) == before     # nada é apagado, completado ou reescrito


def test_new_production_after_interruption_starts_from_a_fresh_root_and_reuses_nothing(settings, monkeypatch):
    runs = count_worker_runs(monkeypatch)
    script, item, old_root = interrupt(settings, 'narration')
    old_files = tree_hashes(old_root)
    with client_for(settings) as client:
        fresh = finished(client, start(client, script, key='explicit-next').json())
        assert fresh['status'] == 'succeeded' and fresh['id'] != item['id']
        new_root = settings.data_dir / 'productions' / fresh['id']
        assert len(runs) == 1 and runs[0].resolve() == new_root.resolve()   # a produção inteira roda de novo, na pasta nova
        assert (new_root / 'plan.json').read_text(encoding='utf-8') != '{"parcial":true}'
        assert 'canais/demo/videos/fixture/fixture.wav' not in tree_hashes(new_root) or \
            tree_hashes(new_root)['canais/demo/videos/fixture/fixture.wav'] != old_files['canais/demo/videos/fixture/fixture.wav']
    assert tree_hashes(old_root) == old_files


def test_existing_attempt_folder_blocks_a_queued_run_instead_of_overwriting_or_resuming(settings, monkeypatch):
    runs = count_worker_runs(monkeypatch)
    with client_for(settings) as client:
        script = script_for(client)
        client.portal.call(client.app.state.manager.worker.cancel)
        item = start(client, script).json()
        root = settings.data_dir / 'productions' / item['id']
        root.mkdir(parents=True)
        (root / 'narracao-parcial.wav').write_bytes(b'RIFF-parcial')
    before = tree_hashes(root)
    with client_for(settings) as client:     # o job ainda está 'queued' e é executado ao subir
        record = finished(client, item)
        assert record['status'] == 'blocked' and 'tentativa anterior' in record['error']
        assert record['artifacts'] == []
    assert runs == [] and tree_hashes(root) == before


def test_direct_retry_of_an_interrupted_production_job_is_refused_and_attempt_is_unchanged(settings):
    script, item, root = interrupt(settings, 'render')
    with client_for(settings) as client:
        response = client.post('/v1/jobs/' + item['job_id'] + '/retry')
        assert response.status_code == 409
        job = client.get('/v1/jobs/' + item['job_id']).json()
        assert job['status'] == 'interrupted' and job['attempt'] == 1


def test_same_idempotency_key_returns_the_interrupted_production_and_never_starts_a_resume(settings, monkeypatch):
    runs = count_worker_runs(monkeypatch)
    script, item, root = interrupt(settings, 'align')
    with client_for(settings) as client:
        again = start(client, script)
        assert again.status_code == 200 and again.json()['id'] == item['id']
        assert client.get('/v1/productions/' + item['id']).json()['status'] == 'interrupted'
    assert runs == []


def test_equal_inputs_produce_an_equal_plan_across_productions(settings):
    """Base para um futuro 'hash das entradas': o plano aprovado é determinístico para o mesmo roteiro/seleção."""
    with client_for(settings) as client:
        script = script_for(client)
        client.portal.call(client.app.state.manager.worker.cancel)
        first, second = (start(client, script, key=k).json() for k in ('a', 'b'))
        with client.app.state.store.connect() as db:
            plans = [json.loads(db.execute('SELECT plan FROM productions WHERE id=?', (p['id'],)).fetchone()[0]) for p in (first, second)]
        assert first['id'] != second['id']
        assert plans[0] == plans[1]


def test_local_pilot_synthesizes_all_sections_in_one_call_with_no_per_segment_reuse(settings, monkeypatch, tmp_path):
    """Hoje a narração é uma chamada única para todas as seções; WAV por seção já existente não é consultado."""
    with client_for(settings) as client:
        script = script_for(client)
        client.portal.call(client.app.state.manager.worker.cancel)
        item = start(client, script, mode='local_pilot').json()
        with client.app.state.store.connect() as db:
            plan = json.loads(db.execute('SELECT plan FROM productions WHERE id=?', (item['id'],)).fetchone()[0])
    folder = tmp_path / 'folder'
    folder.mkdir()
    (folder / 'section-1.wav').write_bytes(b'RIFF-ja-existia')
    calls = []

    async def spy(path):
        calls.append(json.loads(Path(path).read_text(encoding='utf-8')))
        raise RuntimeError('parada controlada')
    monkeypatch.setattr(local_pilot, 'synthesize', spy)
    with pytest.raises(RuntimeError, match='parada controlada'):
        local_pilot.prepare(tmp_path, folder, plan)
    assert len(calls) == 1 and len(calls[0]['sections']) == len(plan['roteiro']['linhas'])
    assert (folder / 'section-1.wav').read_bytes() == b'RIFF-ja-existia'   # nem apagado nem validado/reaproveitado
