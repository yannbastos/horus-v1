from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time

import psycopg
import pytest

from support_a import settings, client_for, create, wait, review, pg_admin_connect
from horus_backend.workflows import digest, encode


def approved(client):
    workflow = wait(client, create(client)["id"])
    assert review(client, workflow).status_code == 200
    return workflow


def generate(client, workflow, key="generate", **extra):
    return client.post(f"/v1/workflows/{workflow['id']}/scripts", headers={"Idempotency-Key": key},
        json={"expected_revision": workflow["revision"], "proposal_artifact_id": workflow["current_artifact_id"], **extra})


def decide(client, script, decision="approve", key="script-review", **extra):
    return client.post(f"/v1/scripts/{script['id']}/review", headers={"Idempotency-Key": key},
        json={"expected_version": script["version"], "decision": decision, **extra})


def test_versioned_review_and_restart(settings):
    with client_for(settings) as client:
        workflow = approved(client)
        jobs = client.get('/v1/jobs').json()
        first = generate(client, workflow)
        assert first.status_code == 201, first.text
        first = first.json()
        assert first['status'] == 'awaiting_review'
        assert first['document']['simulated'] is True
        assert first['review'] is None
        assert generate(client, workflow).json() == first
        assert generate(client, workflow, key='double-click').status_code == 409
        assert decide(client, first, 'request_changes', feedback='').status_code == 422
        changed = decide(client, first, 'request_changes', feedback='Incluir fechamento original')
        assert changed.status_code == 200
        second = generate(client, workflow, key='generate-2').json()
        assert second['version'] == 2 and second['id'] != first['id']
        assert 'Incluir fechamento original' in second['document']['sections'][1]['narration']
        assert decide(client, first, key='old').status_code == 409
        assert decide(client, first, 'request_changes', feedback='Incluir fechamento original').json() == changed.json()
        result = decide(client, second, key='approve-2')
        assert result.status_code == 200
        result = result.json()
        assert result['status'] == 'approved'
        assert result['document'] == second['document']
        assert result['created_at'] <= result['review']['reviewed_at']
        assert generate(client, workflow, key='final').status_code == 409
        assert client.get('/v1/jobs').json() == jobs
        assert len(client.get(f"/v1/workflows/{workflow['id']}").json()['approvals']) == 1
    with client_for(settings) as client:
        assert client.get(f"/v1/scripts/{second['id']}").json() == result
        assert [{k: v for k, v in item.items() if k != 'changes'} for item in client.get(f"/v1/workflows/{workflow['id']}/scripts").json()['items']] == [result, changed.json()]
        assert generate(client, workflow).json() == first
        assert decide(client, second, key='approve-2').json() == result


def test_approval_revision_artifact_and_hash_gates(settings, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = wait(client, create(client)['id'])
        assert generate(client, workflow).status_code == 409
        review(client, workflow)
        assert generate(client, workflow, expected_revision=2).status_code == 409
        assert generate(client, workflow, proposal_artifact_id='f'*32).status_code == 409
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            db.execute("UPDATE horus.artifacts SET content='{}'::jsonb, content_canonical='{}'::bytea WHERE id=%s", (workflow['current_artifact_id'],))
        assert generate(client, workflow).status_code == 409
        assert client.get(f"/v1/workflows/{workflow['id']}/scripts").json() == {'items': []}


def test_persisted_human_approval_is_required(settings, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client)
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            db.execute('DELETE FROM horus.approvals')
        assert generate(client, workflow).status_code == 409


def test_api_auth_payload_and_idempotency(settings):
    with client_for(settings) as client:
        workflow = approved(client)
        path = f"/v1/workflows/{workflow['id']}/scripts"
        assert client.post(path, json={}).status_code == 422
        assert generate(client, workflow, executor='hermes').status_code == 422
        assert generate(client, workflow, expected_revision=True).status_code == 422
        first = generate(client, workflow).json()
        assert generate(client, workflow, expected_revision=2).status_code == 409
        assert decide(client, first).status_code == 200
        assert decide(client, first, 'reject').status_code == 409
        assert decide(client, first, key='different').status_code == 409
        for route in (path, f"/v1/scripts/{first['id']}", f"/v1/scripts/{first['id']}/review"):
            assert client.get(route, headers={'Authorization': ''}).status_code == 401
            assert client.post(route, json={}, headers={'Authorization': ''}).status_code == 401
        assert client.get('/v1/scripts/'+'f'*32).status_code == 404
        assert client.get('/v1/workflows/'+'f'*32+'/scripts').status_code == 404


def test_competing_generation_and_review(settings):
    with client_for(settings) as client:
        workflow = approved(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda key: generate(client, workflow, key), ['one', 'two']))
        assert sorted(r.status_code for r in results) == [201, 409]
        script = next(r.json() for r in results if r.status_code == 201)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda decision: decide(client, script, decision, decision), ['approve', 'reject']))
        assert sorted(r.status_code for r in results) == [200, 409]
        assert len(client.get(f"/v1/workflows/{workflow['id']}/scripts").json()['items']) == 1


def test_transaction_rollback_allows_safe_retry(settings, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client)
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            db.execute("""
                CREATE OR REPLACE FUNCTION horus.fail_script_fn()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'test' USING ERRCODE = '23000';
                END;
                $$;
                CREATE TRIGGER fail_script BEFORE INSERT ON horus.script_receipts
                FOR EACH ROW EXECUTE FUNCTION horus.fail_script_fn();
            """)
        with pytest.raises(psycopg.IntegrityError):
            generate(client, workflow)
        assert client.get(f"/v1/workflows/{workflow['id']}/scripts").json()['items'] == []
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            db.execute('DROP TRIGGER fail_script ON horus.script_receipts')
        script = generate(client, workflow).json()
        assert script['version'] == 1
        assert decide(client, script, 'reject').json()['status'] == 'rejected'
        assert generate(client, workflow, key='after-reject').status_code == 409


def _radar_wait(client, workflow_id, predicate):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        workflow = client.get(f'/v1/workflows/{workflow_id}').json()
        if predicate(workflow):
            return workflow
        time.sleep(0.02)
    pytest.fail(f'Workflow de radar não atingiu condição esperada: {workflow}')


def _start_radar_workflow(client):
    dataset = client.post('/v1/radar/datasets/demo', headers={'Idempotency-Key': 'radar-dataset'})
    assert dataset.status_code == 201, dataset.text
    snap_id = dataset.json()['snapshot_id']
    resp = client.post('/v1/workflows/radar', headers={'Idempotency-Key': 'radar-proposal'}, json={
        'snapshot_id': snap_id, 'revision': 1, 'objective': 'Vídeo original sobre tecnologia',
        'channel': {'channel_id': 'demo', 'summary': 'Canal de demonstração'}})
    assert resp.status_code == 202, resp.text
    return _radar_wait(client, resp.json()['id'], lambda w: w['pending_authorization'] is not None)


def _authorize_radar_workflow(client, workflow):
    resp = client.post(f"/v1/workflows/{workflow['id']}/authorize", headers={'Idempotency-Key': 'radar-auth'},
                       json={'expected_revision': workflow['revision'],
                             'job_id': workflow['pending_authorization']['job_id']})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_simulated_radar_sources_and_invalid_output_are_sanitized(settings, pg_app_cluster, pg_app_database, monkeypatch, caplog):
    settings = replace(settings, hermes_poll_seconds=.03)
    from horus_backend import scripts

    with client_for(settings) as client:
        client.app.state.monkeypatch = monkeypatch
        monkeypatch.setattr('horus_backend.workflow_hermes.read_connection', lambda *a, **k: pytest.fail('Configuração privada acessada'))
        workflow = _start_radar_workflow(client)
        _authorize_radar_workflow(client, workflow)
        workflow = _radar_wait(client, workflow['id'], lambda w: w['status'] == 'awaiting_approval')
        assert review(client, workflow).status_code == 200
        analysis = next(a for a in workflow['artifacts'] if a['stage'] == 'analysis')
        allowed = {s['id'] for s in analysis['content']['result']['sources']}
        assert allowed
        original = scripts.simulate_script
        sentinel = 'CONFIDENTIAL_SENTINEL_PRIVATE_TEXT'

        def invented(*args):
            doc = original(*args)
            doc['sections'][1]['source_ids'] = [sentinel]
            return doc

        monkeypatch.setattr(scripts, 'simulate_script', invented)
        response = generate(client, workflow)
        assert response.status_code == 422 and sentinel not in response.text

        def malformed(*args):
            return {**original(*args), 'title': {'private': sentinel}}

        monkeypatch.setattr(scripts, 'simulate_script', malformed)
        response = generate(client, workflow)
        assert response.status_code == 422 and sentinel not in response.text
        response = generate(client, workflow, expected_revision=sentinel)
        assert response.status_code == 422 and sentinel not in response.text
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert db.execute('SELECT COUNT(*) FROM horus.scripts').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM horus.script_receipts').fetchone()[0] == 0
            events = db.execute('SELECT workflow_id, kind, data::text FROM horus.workflow_events').fetchall()
            assert sentinel not in encode(events)
        assert sentinel not in caplog.text
        monkeypatch.setattr(scripts, 'simulate_script', original)
        script = generate(client, workflow).json()
        cited = {ref for section in script['document']['sections'] for ref in section['source_ids']}
        assert cited == allowed
        assert not cited.intersection({a['id'] for a in workflow['artifacts']})
        assert decide(client, script).status_code == 200


def test_corrupted_referenced_analysis_is_refused(settings, pg_app_cluster, pg_app_database, monkeypatch):
    settings = replace(settings, hermes_poll_seconds=.03)
    with client_for(settings) as client:
        client.app.state.monkeypatch = monkeypatch
        workflow = _start_radar_workflow(client)
        _authorize_radar_workflow(client, workflow)
        workflow = _radar_wait(client, workflow['id'], lambda w: w['status'] == 'awaiting_approval')
        review(client, workflow)
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            db.execute("UPDATE horus.artifacts SET content='{}'::jsonb, content_canonical='{}'::bytea WHERE workflow_id=%s AND stage='analysis'", (workflow['id'],))
        assert generate(client, workflow).status_code == 409


def test_analysis_from_another_workflow_is_refused_even_with_valid_hash(settings, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client)
        other = wait(client, create(client, key='other-workflow')['id'])
        foreign_analysis = next(a for a in other['artifacts'] if a['stage'] == 'analysis')
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            row = db.execute('SELECT content FROM horus.artifacts WHERE id=%s', (workflow['current_artifact_id'],)).fetchone()
            content = {**row[0], 'schema_version': 2,
                       'source_artifact_ids': [foreign_analysis['id']]}
            encoded = encode(content)
            db.execute('UPDATE horus.artifacts SET content=%s::jsonb, content_canonical=%s, content_hash=%s WHERE id=%s',
                       (encoded, encoded.encode('utf-8'), digest(content), workflow['current_artifact_id']))
        assert generate(client, workflow).status_code == 409
        assert client.get(f"/v1/workflows/{workflow['id']}/scripts").json()['items'] == []


def test_identical_concurrent_requests_replay(settings):
    with client_for(settings) as client:
        workflow = approved(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: generate(client, workflow), range(2)))
        assert sorted(r.status_code for r in results) == [200, 201]
        assert results[0].json() == results[1].json()
        script = results[0].json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: decide(client, script), range(2)))
        assert [r.status_code for r in results] == [200, 200]
        assert results[0].json() == results[1].json()


def test_approved_workflow_persists_across_restart_for_scripts(settings):
    with client_for(settings) as client:
        workflow = approved(client)
        before = client.get(f"/v1/workflows/{workflow['id']}").json()
    with client_for(settings) as client:
        after = client.get(f"/v1/workflows/{workflow['id']}").json()
        assert after == before
        assert generate(client, workflow).status_code == 201


def test_long_proposal_preserves_review_feedback(settings, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = wait(client, create(client)['id'])
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            artifact = db.execute('SELECT content FROM horus.artifacts WHERE id=%s', (workflow['current_artifact_id'],)).fetchone()
            content = {**artifact[0], 'text': 'P' * 8000}
            encoded = encode(content)
            db.execute('UPDATE horus.artifacts SET content=%s::jsonb, content_canonical=%s, content_hash=%s WHERE id=%s',
                       (encoded, encoded.encode('utf-8'), digest(content), workflow['current_artifact_id']))
        assert review(client, workflow).status_code == 200
        first = generate(client, workflow).json()
        feedback = 'F' * 1995 + 'FINAL'
        assert decide(client, first, 'request_changes', feedback=feedback).status_code == 200
        second = generate(client, workflow, key='second').json()
        assert second['document']['sections'][1]['narration'].endswith(feedback)


def test_review_rollback_preserves_pending_state(settings, pg_app_cluster, pg_app_database):
    with client_for(settings) as client:
        workflow = approved(client)
        script = generate(client, workflow).json()
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            db.execute("""
                CREATE OR REPLACE FUNCTION horus.fail_review_fn()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'test' USING ERRCODE = '23000';
                END;
                $$;
                CREATE TRIGGER fail_review BEFORE INSERT ON horus.script_receipts
                FOR EACH ROW EXECUTE FUNCTION horus.fail_review_fn();
            """)
        with pytest.raises(psycopg.IntegrityError):
            decide(client, script)
        assert client.get(f"/v1/scripts/{script['id']}").json() == script
        with pg_admin_connect(pg_app_cluster, pg_app_database) as db:
            assert db.execute("SELECT COUNT(*) FROM horus.workflow_events WHERE kind='script.reviewed'").fetchone()[0] == 0
            db.execute('DROP TRIGGER fail_review ON horus.script_receipts')
        assert decide(client, script).json()['status'] == 'approved'


def test_simulated_script_omits_adjustments_label_without_feedback():
    from horus_backend.scripts import simulate_script
    proposal = {"title": "T", "recommendation": {"premise": "Premissa."}}
    empty = simulate_script(proposal, "", set())["sections"][1]["narration"]
    assert "Ajustes" not in empty and "\n" not in empty
    with_feedback = simulate_script(proposal, "Mais exemplos", set())["sections"][1]["narration"]
    assert with_feedback.endswith("Ajustes da revisão anterior: Mais exemplos")
