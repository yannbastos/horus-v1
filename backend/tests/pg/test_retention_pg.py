"""Retenção das cópias do Radar em PostgreSQL (HOR-164 / I6). Offline, cluster descartável."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from horus_backend import retention
from horus_backend.database.manager import DatabaseManager, DatabaseRefused
from horus_backend.database.vault import ConnectionVault
from horus_backend.retention import ERROR_MARKER, digest, encode, run_retention
from schema_support import schema_database

YOUTUBE_TEXT = ('[DEMONSTRAÇÃO]', 'example.org')
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope='module')
def _retention_schema_db(pg_cluster):
    with schema_database(pg_cluster) as state:
        yield state


@pytest.fixture
def pg_pool(request):
    admin, pool = request.getfixturevalue('_retention_schema_db')
    try:
        yield pool
    finally:
        admin.execute(
            'TRUNCATE horus.workflows, horus.jobs, horus.job_attempts, horus.artifacts, horus.approvals, horus.cognitive_runs, horus.workflow_events, horus.scripts, horus.script_receipts RESTART IDENTITY CASCADE'
        )


def future():
    return NOW + timedelta(days=29)


def _table_hashes(pool):
    tables = (
        'workflows', 'jobs', 'job_attempts', 'artifacts',
        'approvals', 'cognitive_runs', 'workflow_events',
    )
    with pool.connection() as conn:
        result = {}
        for t in tables:
            rows = conn.execute(f'SELECT * FROM horus.{t} ORDER BY 1').fetchall()
            result[t] = hashlib.sha256(json.dumps([list(r) for r in rows], default=str).encode()).hexdigest()
        return result


def _counts(pool):
    tables = (
        'workflows', 'jobs', 'job_attempts', 'artifacts',
        'approvals', 'cognitive_runs', 'workflow_events',
    )
    with pool.connection() as conn:
        return {t: conn.execute(f'SELECT COUNT(*) FROM horus.{t}').fetchone()[0] for t in tables}


def _seed_radar_workflow(
    pool,
    wid: str,
    *,
    status: str = 'awaiting_approval',
    expires_at: datetime | None = None,
    with_approval: bool = False,
    with_literal_errors: bool = False,
):
    exp = (expires_at or (NOW + timedelta(days=28))).isoformat().replace('+00:00', 'Z')
    dataset = {
        'snapshot_id': f'snap-{wid}',
        'revision': 1,
        'items': [{'id': 'vid-1', 'title': '[DEMONSTRAÇÃO] Título', 'url': 'https://example.org/watch?v=1', 'channel': 'Canal Teste'}],
    }
    payload = {
        'objective': 'Avaliar referências',
        'radar': {'snapshot_id': f'snap-{wid}', 'revision': 1, 'expires_at': exp},
        'dataset': dataset,
    }
    payload_json = encode(payload)

    job_id = f'job-{wid}'
    attempt_id = f'att-{wid}'
    art_research = {'dataset': dataset, 'snapshot_hash': 'h' * 64}
    art_analysis = {
        'result': {
            'sources': [{'url': 'https://example.org/watch?v=1'}],
            'facts': ['Fato [DEMONSTRAÇÃO]'],
            'opportunities': ['Ângulo editorial próprio'],
            'recommendation': 'Seguir com roteiro original',
        }
    }
    art_proposal = {'title': 'Proposta editorial limpa', 'summary': 'Sem cópia literal'}
    run_req = {'input': {'dataset': dataset}, 'stage': 'analysis'}

    err_text = 'Falha ao processar https://example.org/watch?v=1 ([DEMONSTRAÇÃO])' if with_literal_errors else None

    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                """INSERT INTO horus.workflows
                   (id, idempotency_key, payload, payload_canonical, revision, status, feedback, current_artifact_id, created_at, updated_at)
                   VALUES (%s, %s, %s::jsonb, %s, 1, %s, '', %s, %s, %s)""",
                (wid, f'idem-{wid}', payload_json, payload_json.encode('utf-8'), status, f'art-prop-{wid}', NOW, NOW),
            )
            conn.execute(
                """INSERT INTO horus.jobs
                   (id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, attempt, error, workflow_id, workflow_revision, step_key, step_index)
                   VALUES (%s, %s, %s::jsonb, %s, 'succeeded', %s, %s, 1, %s, %s, 1, 'research', 0)""",
                (job_id, f'jidem-{wid}', encode({'k': 1}), encode({'k': 1}).encode('utf-8'), NOW, NOW, err_text, wid),
            )
            conn.execute(
                """INSERT INTO horus.job_attempts
                   (id, job_id, number, status, executor, input_hash, input_artifacts, input_artifacts_canonical, started_at, finished_at, error)
                   VALUES (%s, %s, 1, 'succeeded', 'simulated', %s, %s::jsonb, %s, %s, %s, %s)""",
                (attempt_id, job_id, 'a' * 64, encode({}), encode({}).encode('utf-8'), NOW, NOW, err_text),
            )
            for idx, (art_id, stage, content, a_job_id) in enumerate([
                (f'art-res-{wid}', 'research', art_research, job_id),
                (f'art-ana-{wid}', 'analysis', art_analysis, f'job-ana-{wid}'),
                (f'art-prop-{wid}', 'proposal', art_proposal, f'job-prop-{wid}'),
            ]):
                if a_job_id != job_id:
                    conn.execute(
                        """INSERT INTO horus.jobs
                           (id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, attempt, workflow_id, workflow_revision, step_key, step_index)
                           VALUES (%s, %s, %s::jsonb, %s, 'succeeded', %s, %s, 1, %s, 1, %s, %s)""",
                        (a_job_id, f'jidem-{art_id}', encode({'k': 1}), encode({'k': 1}).encode('utf-8'), NOW, NOW, wid, stage, idx),
                    )
                c_json = encode(content)
                conn.execute(
                    """INSERT INTO horus.artifacts
                       (id, workflow_id, revision, stage, job_id, attempt_id, schema_version, content, content_canonical, content_hash, created_at)
                       VALUES (%s, %s, 1, %s, %s, %s, 1, %s::jsonb, %s, %s, %s)""",
                    (art_id, wid, stage, a_job_id, attempt_id, c_json, c_json.encode('utf-8'), digest(content), NOW + timedelta(seconds=idx)),
                )
            req_json = encode(run_req)
            conn.execute(
                """INSERT INTO horus.cognitive_runs
                   (id, job_id, attempt_id, provider, idempotency_key, request, request_canonical, request_hash, status, error, created_at, updated_at)
                   VALUES (%s, %s, %s, 'hermes', %s, %s::jsonb, %s, %s, 'completed', %s, %s, %s)""",
                (f'run-{wid}', job_id, attempt_id, f'ridem-{wid}', req_json, req_json.encode('utf-8'), digest(run_req), err_text, NOW, NOW),
            )
            if with_literal_errors:
                conn.execute(
                    """INSERT INTO horus.workflow_events (workflow_id, revision, kind, data, created_at)
                       VALUES (%s, 1, 'job.failed', %s::jsonb, %s)""",
                    (wid, encode({'error': err_text}), NOW),
                )
            if with_approval:
                req_app = encode({'decision': 'approve'})
                conn.execute(
                    """INSERT INTO horus.approvals
                       (id, workflow_id, revision, artifact_id, decision, feedback, operator_id, scope, idempotency_key, request, request_canonical, created_at)
                       VALUES (%s, %s, 1, %s, 'approve', 'Decisão do operador', 'op-1', 'workflow', %s, %s::jsonb, %s, %s)""",
                    (f'app-{wid}', wid, f'art-prop-{wid}', f'apidem-{wid}', req_app, req_app.encode('utf-8'), NOW),
                )


def test_not_expired_changes_nothing(pg_pool):
    _seed_radar_workflow(pg_pool, 'w1')
    before = _table_hashes(pg_pool)
    report = run_retention(pg_pool, clock=lambda: NOW)
    assert (report.evaluated, report.redacted, report.not_expired, report.ignored) == (1, 0, 1, 1)
    assert _table_hashes(pg_pool) == before


def test_expired_awaiting_approval_is_redacted_preserving_hashes_and_canonical(pg_pool):
    _seed_radar_workflow(pg_pool, 'w1')
    with pg_pool.connection() as conn:
        prop_before = conn.execute("SELECT content, content_hash FROM horus.artifacts WHERE id = 'art-prop-w1'").fetchone()
        res_hash_before = conn.execute("SELECT content_hash FROM horus.artifacts WHERE id = 'art-res-w1'").fetchone()[0]
        req_hash_before = conn.execute("SELECT request_hash FROM horus.cognitive_runs WHERE id = 'run-w1'").fetchone()[0]

    dry = run_retention(pg_pool, clock=future, dry_run=True)
    assert dry.redacted == 1 and dry.workflow_ids == ['w1']
    before = _table_hashes(pg_pool)
    assert run_retention(pg_pool, clock=future, dry_run=True).redacted == 1
    assert _table_hashes(pg_pool) == before

    report = run_retention(pg_pool, clock=future)
    assert (report.evaluated, report.redacted, report.failed) == (1, 1, 0)
    assert report.checkpoint is None and report.checkpoint_pending is False

    with pg_pool.connection() as conn:
        wf_payload, wf_canon = conn.execute(
            "SELECT payload, payload_canonical FROM horus.workflows WHERE id = 'w1'"
        ).fetchone()
        assert wf_payload['radar']['redacted_at'] and wf_payload['radar']['snapshot_id'] == 'snap-w1'
        assert wf_payload['dataset']['redacted'] is True
        assert json.loads(wf_canon.decode('utf-8')) == wf_payload
        assert not any(t in json.dumps(wf_payload, ensure_ascii=False) for t in YOUTUBE_TEXT)

        res_content, res_canon, res_hash_after = conn.execute(
            "SELECT content, content_canonical, content_hash FROM horus.artifacts WHERE id = 'art-res-w1'"
        ).fetchone()
        assert res_content['dataset']['redacted'] is True
        assert len(res_content['dataset']['sha256']) == 64
        assert res_hash_after == res_hash_before
        assert json.loads(res_canon.decode('utf-8')) == res_content

        ana_content = conn.execute("SELECT content FROM horus.artifacts WHERE id = 'art-ana-w1'").fetchone()[0]
        assert ana_content['result']['sources']['redacted'] is True
        assert ana_content['result']['facts']['redacted'] is True
        assert ana_content['result']['opportunities']

        prop_after = conn.execute("SELECT content, content_hash FROM horus.artifacts WHERE id = 'art-prop-w1'").fetchone()
        assert prop_after == prop_before

        run_req, run_canon, run_hash_after = conn.execute(
            "SELECT request, request_canonical, request_hash FROM horus.cognitive_runs WHERE id = 'run-w1'"
        ).fetchone()
        assert json.loads(run_req['input'])['redacted'] is True
        assert run_hash_after == req_hash_before
        assert json.loads(run_canon.decode('utf-8')) == run_req

        events = conn.execute(
            "SELECT data FROM horus.workflow_events WHERE workflow_id = 'w1' AND kind = 'workflow.radar_redacted'"
        ).fetchall()
        assert len(events) == 1
        assert events[0][0]['reason'] == 'radar_retention'
        assert events[0][0]['editorial_literal_matches'] == 0


def test_expired_terminal_keeps_decisions(pg_pool):
    _seed_radar_workflow(pg_pool, 'w-app', status='approved', with_approval=True)
    with pg_pool.connection() as conn:
        approvals_before = conn.execute('SELECT decision, feedback, artifact_id FROM horus.approvals').fetchall()
    assert run_retention(pg_pool, clock=future).redacted == 1
    with pg_pool.connection() as conn:
        assert conn.execute("SELECT status FROM horus.workflows WHERE id = 'w-app'").fetchone()[0] == 'approved'
        assert conn.execute('SELECT decision, feedback, artifact_id FROM horus.approvals').fetchall() == approvals_before


def test_idempotent_second_run(pg_pool):
    _seed_radar_workflow(pg_pool, 'w1')
    first = run_retention(pg_pool, clock=future)
    assert first.redacted == 1 and first.checkpoint is None and first.checkpoint_pending is False
    after = _table_hashes(pg_pool)
    second = run_retention(pg_pool, clock=future)
    assert (second.redacted, second.already_redacted, second.failed) == (0, 1, 0)
    assert _table_hashes(pg_pool) == after


def test_failure_rolls_back_only_that_workflow(pg_pool, monkeypatch):
    _seed_radar_workflow(pg_pool, 'w1')
    _seed_radar_workflow(pg_pool, 'w2')
    original, calls = retention._redact_artifacts, []

    def flaky(*args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('falha injetada após a atualização do payload')
        return original(*args)

    monkeypatch.setattr(retention, '_redact_artifacts', flaky)
    report = run_retention(pg_pool, clock=future)
    assert (report.redacted, report.failed) == (1, 1)
    failed_id = report.errors[0]['workflow_id']
    ok_id = 'w2' if failed_id == 'w1' else 'w1'
    with pg_pool.connection() as conn:
        for wid, expected in ((failed_id, False), (ok_id, True)):
            p = conn.execute('SELECT payload FROM horus.workflows WHERE id = %s', (wid,)).fetchone()[0]
            assert ('redacted_at' in p['radar']) is expected
        assert conn.execute("SELECT COUNT(*) FROM horus.workflow_events WHERE kind = 'workflow.radar_redacted'").fetchone()[0] == 1
    monkeypatch.setattr(retention, '_redact_artifacts', original)
    assert run_retention(pg_pool, clock=future).redacted == 1


def test_deferred_active_and_non_radar_untouched(pg_pool):
    _seed_radar_workflow(pg_pool, 'w-run', status='running')
    _seed_radar_workflow(pg_pool, 'w-can', status='cancel_requested')
    with pg_pool.connection() as conn:
        with conn.transaction():
            p = encode({'objective': 'Sem Radar'})
            conn.execute(
                """INSERT INTO horus.workflows
                   (id, idempotency_key, payload, payload_canonical, revision, status, created_at, updated_at)
                   VALUES ('w-sim', 'idem-sim', %s::jsonb, %s, 1, 'awaiting_approval', %s, %s)""",
                (p, p.encode('utf-8'), NOW, NOW),
            )
    before = _table_hashes(pg_pool)
    report = run_retention(pg_pool, clock=future)
    assert (report.evaluated, report.deferred_active, report.redacted) == (2, 2, 0)
    assert _table_hashes(pg_pool) == before


def test_scrubs_literal_errors_and_preserves_rows(pg_pool):
    _seed_radar_workflow(pg_pool, 'w1', with_literal_errors=True)
    before = _counts(pg_pool)
    report = run_retention(pg_pool, clock=future)
    assert report.redacted == 1
    after = _counts(pg_pool)
    assert after.pop('workflow_events') == before.pop('workflow_events') + 1 and after == before
    with pg_pool.connection() as conn:
        assert conn.execute("SELECT error FROM horus.jobs WHERE id = 'job-w1'").fetchone()[0] == ERROR_MARKER
        assert conn.execute("SELECT error FROM horus.job_attempts WHERE id = 'att-w1'").fetchone()[0] == ERROR_MARKER
        assert conn.execute("SELECT error FROM horus.cognitive_runs WHERE id = 'run-w1'").fetchone()[0] == ERROR_MARKER
        ev = conn.execute("SELECT data FROM horus.workflow_events WHERE workflow_id = 'w1' AND kind = 'job.failed'").fetchone()[0]
        assert ev['error'] == ERROR_MARKER


def test_database_unavailable_raises_503(tmp_path):
    class MemoryKeyring:
        def get_password(self, *_):
            return None

    manager = DatabaseManager(ConnectionVault(tmp_path, MemoryKeyring()))
    with pytest.raises(DatabaseRefused) as caught:
        run_retention(manager)
    assert caught.value.http_status == 503


def test_script_matcher_contract_hook(pg_pool):
    _seed_radar_workflow(pg_pool, 'w1')
    report = run_retention(pg_pool, clock=future, script_matcher=lambda db, wid, lits: (2, 1))
    assert report.redacted == 1
    with pg_pool.connection() as conn:
        data = conn.execute(
            "SELECT data FROM horus.workflow_events WHERE workflow_id = 'w1' AND kind = 'workflow.radar_redacted'"
        ).fetchone()[0]
    assert data['script_editorial_literal_matches'] == 2
    assert data['script_receipt_editorial_literal_matches'] == 1


def test_script_retention_preserves_editorial_copies_and_counts_literals(pg_pool):
    """Roteiros e recibos preservados são apenas contados; fontes brutas do Radar são redigidas."""
    _seed_radar_workflow(pg_pool, 'w1')
    doc = {
        'schema_version': 1,
        'simulated': True,
        'title': '[DEMONSTRAÇÃO] Título',
        'sections': [
            {'id': 'hook', 'kind': 'hook', 'heading': 'Abertura', 'narration': 'Roteiro [DEMONSTRAÇÃO] Título', 'source_ids': []},
            {'id': 'body', 'kind': 'body', 'heading': 'Corpo', 'narration': 'Referência https://example.org/watch?v=1', 'source_ids': []},
            {'id': 'conclusion', 'kind': 'conclusion', 'heading': 'Fim', 'narration': 'Limpo', 'source_ids': []},
        ],
    }
    doc_json = encode(doc)
    req_json = encode({'expected_revision': 1, 'proposal_artifact_id': 'art-prop-w1'})
    rec = encode({'id': 's-w1', 'document': doc, 'status': 'approved'})
    with pg_pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                """INSERT INTO horus.scripts(
                    id, workflow_id, workflow_revision, proposal_artifact_id, proposal_content_hash,
                    version, status, document, created_at, review, origin, generator, document_canonical, review_canonical
                ) VALUES('s-w1', 'w1', 1, %s, %s, 1, 'approved', %s::jsonb, %s, NULL, 'simulated', NULL, %s, NULL)""",
                ('art-prop-w1', 'h' * 64, doc_json, NOW, doc_json.encode('utf-8')),
            )
            for scope, key in (('generate:w1', 'k1'), ('review:s-w1', 'k2')):
                conn.execute(
                    """INSERT INTO horus.script_receipts(
                        scope, idempotency_key, request, response, request_canonical, response_canonical
                    ) VALUES(%s, %s, %s::jsonb, %s::jsonb, %s, %s)""",
                    (scope, key, req_json, rec, req_json.encode('utf-8'), rec.encode('utf-8')),
                )
    report = run_retention(pg_pool, clock=future)
    assert report.redacted == 1 and report.failed == 0
    with pg_pool.connection() as conn:
        data = conn.execute(
            "SELECT data FROM horus.workflow_events WHERE workflow_id = 'w1' AND kind = 'workflow.radar_redacted'"
        ).fetchone()[0]
        saved_doc = conn.execute("SELECT document FROM horus.scripts WHERE id = 's-w1'").fetchone()[0]
    assert saved_doc == doc
    assert data['script_editorial_literal_matches'] == 3
    assert data['script_receipt_editorial_literal_matches'] == 6
