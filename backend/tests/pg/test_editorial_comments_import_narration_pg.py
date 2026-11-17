"""Testes PostgreSQL para ReviewCommentService, ImportService e NarrationService (HOR-164 / I7)."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import psycopg
import pytest
from psycopg.types.json import Jsonb

from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.database.manager import DatabaseManager
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from horus_backend.narration.providers import NarrationError
from horus_backend.narration.service import EstimateRequest, NarrationService, Preference
from horus_backend.project_import import ImportRefused, ImportService, verify_package
from horus_backend.review_comments import CommentRequest, DecisionRequest, ReviewCommentService
from horus_backend.storage import DatabaseUnavailable, Store, canonical_json_bytes
from horus_backend.workflows import WorkflowConflict, digest
from schema_support import apply_chain
from test_database_contract import CONFIG, MemoryKeyring


def _make_pg_manager(pg_cluster, database: str, tmp_path: Path, monkeypatch, *, configure: bool = True) -> DatabaseManager:
    vault = ConnectionVault(tmp_path, MemoryKeyring())
    if configure:
        vault.write(ConnectionRequest.model_validate(CONFIG))
    manager = DatabaseManager(vault)

    def local_type(config):
        class LocalConnection(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo="", **kwargs):
                return super().connect(
                    pg_cluster.dsn(database, "horus_app"),
                    autocommit=True,
                    password=config.password.get_secret_value(),
                    prepare_threshold=None,
                )

        return LocalConnection

    monkeypatch.setattr(manager, "_make_connection_type", local_type)
    monkeypatch.setattr("horus_backend.database.manager.resolve_public", lambda *_: "8.8.8.8")
    if configure:
        assert manager.test().state == "connected"
    return manager


@pytest.fixture(scope="module")
def _editorial_cin_db(pg_cluster):
    with pg_cluster.connect(autocommit=True) as bootstrap:
        for name in ("anon", "authenticated"):
            bootstrap.execute(
                f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{name}') "
                f"THEN CREATE ROLE {name} NOLOGIN; END IF; END $$"
            )
    with pg_cluster.database() as pool:
        with pool.connection() as app:
            database = app.info.dbname
        with pg_cluster.connect(database, autocommit=True) as admin:
            admin.execute("DROP SCHEMA horus CASCADE")
            admin.execute("CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY)")
            apply_chain(admin)
            admin.execute(
                "INSERT INTO horus.schema_migrations(version, checksum, applied_at) "
                "VALUES (%s, %s, clock_timestamp()) ON CONFLICT (version) DO NOTHING",
                (EXPECTED_SCHEMA_VERSION, "a" * 64),
            )
            admin.execute(
                "INSERT INTO horus.database_identity(singleton, instance_id, created_at) "
                "VALUES (true, gen_random_uuid(), clock_timestamp()) ON CONFLICT (singleton) DO NOTHING"
            )
            yield database, admin


@pytest.fixture
def pg_store(pg_cluster, _editorial_cin_db, tmp_path, monkeypatch):
    database, admin = _editorial_cin_db
    manager = _make_pg_manager(pg_cluster, database, tmp_path, monkeypatch)
    store = Store(manager, data_dir=tmp_path)
    try:
        yield store, admin, manager
    finally:
        manager.close()
        admin.execute(
            "TRUNCATE horus.review_comment_receipts, horus.review_comment_events, horus.review_comments, "
            "horus.imports, horus.preferences, horus.script_receipts, horus.productions, horus.scripts, "
            "horus.artifacts, horus.job_attempts, horus.workflows, horus.jobs RESTART IDENTITY CASCADE"
        )


def _seed_production_graph(admin, *, prod_id: str = "prod0001", video_sha: str | None = None, approve_script: bool = True):
    now_dt = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
    wf_id = f"wf-{prod_id}"
    art_job_id = f"job-art-{prod_id}"
    att_id = f"att-{prod_id}"
    art_id = f"art-{prod_id}"
    script_id = f"a" * 32
    prod_job_id = f"job-prod-{prod_id}"
    video_sha = video_sha or ("1" * 64)

    wf_payload = {"topic": "teste"}
    admin.execute(
        "INSERT INTO horus.workflows (id, idempotency_key, payload, payload_canonical, revision, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, 1, 'approved', %s, %s)",
        (wf_id, f"wf-{wf_id}", Jsonb(wf_payload), canonical_json_bytes(wf_payload), now_dt, now_dt),
    )
    for jid in (art_job_id, prod_job_id):
        payload_obj = {"operation": "factory.profile"}
        admin.execute(
            "INSERT INTO horus.jobs (id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, workflow_id) "
            "VALUES (%s, %s, %s, %s, 'succeeded', %s, %s, %s)",
            (jid, f"k-{jid}", Jsonb(payload_obj), canonical_json_bytes(payload_obj), now_dt, now_dt, wf_id),
        )
    in_art = {}
    admin.execute(
        "INSERT INTO horus.job_attempts (id, job_id, number, status, executor, input_hash, input_artifacts, input_artifacts_canonical, started_at, finished_at) "
        "VALUES (%s, %s, 1, 'succeeded', 'simulated', %s, %s, %s, %s, %s)",
        (att_id, art_job_id, "0" * 64, Jsonb(in_art), canonical_json_bytes(in_art), now_dt, now_dt),
    )
    art_content = {"proposal": "ok"}
    admin.execute(
        "INSERT INTO horus.artifacts (id, workflow_id, revision, stage, job_id, attempt_id, schema_version, content, content_canonical, content_hash, created_at) "
        "VALUES (%s, %s, 1, 'proposal', %s, %s, 1, %s, %s, %s, %s)",
        (art_id, wf_id, art_job_id, att_id, Jsonb(art_content), canonical_json_bytes(art_content), "a" * 64, now_dt),
    )
    doc = {
        "schema_version": 1,
        "title": "Roteiro PG",
        "hook": "Gancho inicial.",
        "sections": [{"id": "s1", "heading": "Sec 1", "narration": "Primeira fala.", "visual_notes": "Cena 1", "source_ids": []}],
        "cta": "Inscreva-se.",
    }
    review = {"decision": "approve", "reviewed_at": now_dt.isoformat(), "operator_id": "op1"}
    admin.execute(
        "INSERT INTO horus.scripts (id, workflow_id, workflow_revision, proposal_artifact_id, proposal_content_hash, version, status, document, document_canonical, created_at, review, review_canonical) "
        "VALUES (%s, %s, 1, %s, %s, 1, %s, %s, %s, %s, %s, %s)",
        (
            script_id,
            wf_id,
            art_id,
            "a" * 64,
            "approved" if approve_script else "awaiting_review",
            Jsonb(doc),
            canonical_json_bytes(doc),
            now_dt,
            Jsonb(review) if approve_script else None,
            canonical_json_bytes(review) if approve_script else None,
        ),
    )
    if approve_script:
        receipt_req = {"decision": "approve"}
        receipt_resp = {"id": script_id, "version": 1, "status": "approved", "review": review, "document": doc}
        admin.execute(
            "INSERT INTO horus.script_receipts (scope, idempotency_key, request, request_canonical, response, response_canonical) "
            "VALUES (%s, 'rk1', %s, %s, %s, %s)",
            (f"review:{script_id}", Jsonb(receipt_req), canonical_json_bytes(receipt_req), Jsonb(receipt_resp), canonical_json_bytes(receipt_resp)),
        )
    sr = {"decision": "approve"}
    plan = {"mode": "simulated"}
    admin.execute(
        "INSERT INTO horus.productions (id, script_id, script_version, script_content_hash, script_review, script_review_canonical, job_id, mode, plan, plan_canonical, artifacts, created_at) "
        "VALUES (%s, %s, 1, %s, %s, %s, %s, 'simulated', %s, %s, '[]'::jsonb, %s)",
        (prod_id, script_id, digest(doc), Jsonb(sr), canonical_json_bytes(sr), prod_job_id, Jsonb(plan), canonical_json_bytes(plan), now_dt),
    )
    return script_id, video_sha


class _ProductionsStub:
    def __init__(self, store, records: dict[str, dict]):
        self.store = store
        self.records = records

    def get(self, production_id: str) -> dict:
        if production_id not in self.records:
            raise KeyError(production_id)
        return self.records[production_id]


def _make_export_zip(path: Path, *, export_id: str | None = None, tamper_hash: bool = False) -> Path:
    export_id = export_id or ("e" * 64)
    script = {
        "schema_version": 1,
        "simulated": True,
        "title": "Projeto importado",
        "sections": [
            {"id": "abertura", "kind": "hook", "heading": "Abertura", "narration": "Texto do roteiro.", "source_ids": ["src_a"]},
            {"id": "corpo-1", "kind": "body", "heading": "Bloco", "narration": "Desenvolvimento.", "source_ids": ["src_a"]},
            {"id": "fim", "kind": "conclusion", "heading": "Fim", "narration": "Fechamento.", "source_ids": []},
        ],
    }
    files_data = {
        "video.mp4": b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",
        "subtitles.srt": b"1\n00:00:00,000 --> 00:00:01,000\nOi\n",
        "script.json": json.dumps(script).encode("utf-8"),
        "quality-report.json": json.dumps({"status": "not_run", "checks": [], "warnings": [], "blocking_errors": []}).encode("utf-8"),
        "licenses.json": json.dumps({"publishable": False}).encode("utf-8"),
        "review.json": json.dumps({"publishable": False}).encode("utf-8"),
    }
    manifest = {
        "schema_version": 1,
        "horus_version": "0.1.0",
        "database_schema_version": 12,
        "id": export_id,
        "production_id": "b" * 32,
        "created_at": "2026-11-17T12:00:00+00:00",
        "publishable": False,
        "files": [
            {
                "name": name,
                "sha256": ("0" * 64 if tamper_hash and name == "subtitles.srt" else hashlib.sha256(data).hexdigest()),
                "size_bytes": len(data),
            }
            for name, data in files_data.items()
        ],
    }
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as zf:
        for name, data in files_data.items():
            zf.writestr(name, data)
        zf.writestr("manifest.json", json.dumps(manifest))
    return path


def test_comments_import_narration_unavailable_raise_503(pg_cluster, _editorial_cin_db, tmp_path, monkeypatch):
    database, _ = _editorial_cin_db
    unconfigured = _make_pg_manager(pg_cluster, database, tmp_path / "u", monkeypatch, configure=False)
    zip_path = _make_export_zip(tmp_path / "pkg.zip")
    try:
        for store in (Store(None, data_dir=tmp_path), Store(unconfigured, data_dir=tmp_path)):
            prods = _ProductionsStub(store, {"prod1": {"local_data": {"state": "present"}, "artifacts": [{"kind": "video", "sha256": "1" * 64}]}})
            comments = ReviewCommentService(tmp_path, prods, store=store)
            imports = ImportService(tmp_path, store=store)
            settings = SimpleNamespace(data_dir=tmp_path, piper_executable=None, piper_models=None, kokoro_python=None, kokoro_model_dir=None)
            narration = NarrationService(settings, store)

            for call in (
                lambda: comments.create("prod1", CommentRequest(artifact_sha256="1" * 64, at_seconds=1.0, text="Oi", author="Ana"), "k1"),
                lambda: comments.page("prod1"),
                imports.list,
                lambda: imports.import_package(zip_path, "ik1"),
                narration.get_preference,
                lambda: narration.estimate(EstimateRequest(provider="windows_sapi", voice_id="v1", script_id="a" * 32, script_version=1)),
            ):
                with pytest.raises(DatabaseUnavailable) as exc:
                    call()
                assert exc.value.http_status == 503
                assert exc.value.code == "database_unavailable"
    finally:
        unconfigured.close()


def test_review_comments_crud_trail_idempotency_immutability_and_no_n_plus_1(pg_store, tmp_path):
    store, admin, _ = pg_store
    _, video_sha = _seed_production_graph(admin, prod_id="prod1")
    prods = _ProductionsStub(store, {"prod1": {"local_data": {"state": "present"}, "artifacts": [{"kind": "video", "sha256": video_sha}]}})
    svc = ReviewCommentService(tmp_path, prods)

    c1, created = svc.create("prod1", CommentRequest(artifact_sha256=video_sha, at_seconds=12.5, text="Cortar pausa", author="Ana"), "k1")
    assert created is True
    assert c1["status"] == "open"
    assert len(c1["trail"]) == 1

    # Replay idempotente e conflito com mesma chave
    replay, created2 = svc.create("prod1", CommentRequest(artifact_sha256=video_sha, at_seconds=12.5, text="Cortar pausa", author="Ana"), "k1")
    assert created2 is False
    assert replay["id"] == c1["id"]
    with pytest.raises(WorkflowConflict):
        svc.create("prod1", CommentRequest(artifact_sha256=video_sha, at_seconds=12.5, text="Outro texto", author="Ana"), "k1")

    # Resolver e reabrir
    resolved = svc.decide("prod1", c1["id"], "resolve", DecisionRequest(author="Beto", note="Ajustado"), "rk1")
    assert resolved["status"] == "resolved"
    assert [e["action"] for e in resolved["trail"]] == ["created", "resolved"]
    assert resolved["trail"][1]["note"] == "Ajustado"

    # Replay de decisão e conflito já resolvido
    assert svc.decide("prod1", c1["id"], "resolve", DecisionRequest(author="Beto", note="Ajustado"), "rk1")["status"] == "resolved"
    with pytest.raises(WorkflowConflict):
        svc.decide("prod1", c1["id"], "resolve", DecisionRequest(author="Beto"), "rk2")

    reopened = svc.decide("prod1", c1["id"], "reopen", DecisionRequest(author="Ana"), "ok1")
    assert reopened["status"] == "open"
    assert [e["action"] for e in reopened["trail"]] == ["created", "resolved", "reopened"]

    # Append-only garantido no banco (triggers no_update / no_delete)
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("UPDATE horus.review_comment_events SET action = 'resolved' WHERE comment_id = %s", (c1["id"],))
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("DELETE FROM horus.review_comment_events WHERE comment_id = %s", (c1["id"],))
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("DELETE FROM horus.review_comment_receipts WHERE production_id = 'prod1'")

    # Sem arquivo JSON em disco
    assert not (tmp_path / "production-comments").exists()

    # Mais comentários e verificação de N+1 (3 queries fixas no page)
    for idx in range(6):
        svc.create("prod1", CommentRequest(artifact_sha256=video_sha, at_seconds=float(idx), text=f"Obs {idx}", author="Ana"), f"batch-{idx}")

    queries = []
    orig_uow = store.uow

    class CountingCursor:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=None, **kwargs):
            queries.append(sql)
            return self._inner.execute(sql, params, **kwargs)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    class CountingConn:
        def __init__(self, inner):
            self._inner = inner

        def cursor(self, *args, **kwargs):
            return CountingCursor(self._inner.cursor(*args, **kwargs))

        def __getattr__(self, name):
            return getattr(self._inner, name)

    @contextmanager
    def tracked_uow(*args, **kwargs):
        with orig_uow(*args, **kwargs) as conn:
            yield CountingConn(conn)

    store.uow = tracked_uow
    p1 = svc.page("prod1", limit=3)
    assert len(queries) == 3  # counts + window + batch events (zero N+1)
    assert len(p1["items"]) == 3
    assert p1["counts"] == {"open": 7, "resolved": 0}
    assert p1["next_after"] is not None

    # Recusas de validação e hash estrangeiro
    with pytest.raises(WorkflowConflict):
        svc.create("prod1", CommentRequest(artifact_sha256="9" * 64, at_seconds=1.0, text="Oi", author="Ana"), "bad-hash")
    with pytest.raises(ValueError):
        svc.create("prod1", CommentRequest(artifact_sha256=video_sha, at_seconds=1.0, text="Oi\x07", author="Ana"), "ctrl")


def test_review_comments_limits_and_deleted_local_data(pg_store, tmp_path, monkeypatch):
    store, admin, _ = pg_store
    _, video_sha = _seed_production_graph(admin, prod_id="prod-lim")
    prods = _ProductionsStub(store, {"prod-lim": {"local_data": {"state": "present"}, "artifacts": [{"kind": "video", "sha256": video_sha}]}})
    svc = ReviewCommentService(tmp_path, prods)

    monkeypatch.setattr("horus_backend.review_comments.MAX_COMMENTS", 1)
    monkeypatch.setattr("horus_backend.review_comments.MAX_EVENTS", 2)
    c1, _ = svc.create("prod-lim", CommentRequest(artifact_sha256=video_sha, at_seconds=1.0, text="Um", author="Ana"), "k1")
    with pytest.raises(WorkflowConflict):
        svc.create("prod-lim", CommentRequest(artifact_sha256=video_sha, at_seconds=2.0, text="Dois", author="Ana"), "k2")

    svc.decide("prod-lim", c1["id"], "resolve", DecisionRequest(author="Ana"), "r1")
    with pytest.raises(WorkflowConflict):
        svc.decide("prod-lim", c1["id"], "reopen", DecisionRequest(author="Ana"), "r2")

    prods_deleted = _ProductionsStub(store, {"prod-lim": {"local_data": {"state": "quarantined"}, "artifacts": [{"kind": "video", "sha256": video_sha}]}})
    svc_del = ReviewCommentService(tmp_path, prods_deleted)
    with pytest.raises(WorkflowConflict):
        svc_del.create("prod-lim", CommentRequest(artifact_sha256=video_sha, at_seconds=1.0, text="X", author="Ana"), "k3")


def test_project_import_pg_immutability_tampering_and_hostile(pg_store, tmp_path):
    store, admin, _ = pg_store
    svc = ImportService(tmp_path, store=store)
    pkg = _make_export_zip(tmp_path / "good.zip")

    detail, created = svc.import_package(pkg, "key-1")
    assert created is True
    assert detail["publishable"] is False
    assert detail["approved"] is False
    assert detail["historical_only"] is True
    assert not (tmp_path / "imports" / detail["id"] / "import.json").exists()

    # Replay e conflito 409
    replay, created2 = svc.import_package(pkg, "key-1")
    assert created2 is False
    assert replay["id"] == detail["id"]
    with pytest.raises(ImportRefused) as exc:
        svc.import_package(pkg, "key-2")
    assert exc.value.status == 409
    assert exc.value.code == "already_imported"

    # Append-only garantido no banco (triggers no_update / no_delete)
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("UPDATE horus.imports SET approved = true WHERE id = %s", (detail["id"],))
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("DELETE FROM horus.imports WHERE id = %s", (detail["id"],))

    # Listagem e download
    assert len(svc.list()["items"]) == 1
    vpath, vsize = svc.file(detail["id"], "video.mp4")
    assert vpath.is_file() and vsize > 0

    # Adulteração em disco detectada na leitura
    (tmp_path / "imports" / detail["id"] / "script.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ImportRefused) as tamper_exc:
        svc.get(detail["id"])
    assert tamper_exc.value.code == "import_tampered"

    # Pacote hostil recusado antes de qualquer escrita no banco ou disco
    bad_pkg = _make_export_zip(tmp_path / "bad.zip", export_id="f" * 64, tamper_hash=True)
    with pytest.raises(ImportRefused) as bad_exc:
        verify_package(bad_pkg)
    assert bad_exc.value.code == "hash_mismatch"
    with pytest.raises(ImportRefused):
        svc.import_package(bad_pkg, "key-bad")
    assert len(svc.list()["items"]) == 1


@pytest.mark.pg_strong
def test_project_import_concurrent_same_package_creates_one_copy(pg_store, tmp_path):
    store, _, _ = pg_store
    svc = ImportService(tmp_path, store=store)
    pkg = _make_export_zip(tmp_path / "concurrent.zip")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: svc.import_package(pkg, "same-key"), range(4)))
    ids = {item[0]["id"] for item in results}
    created_flags = [item[1] for item in results]
    assert len(ids) == 1
    assert created_flags.count(True) == 1
    assert len(svc.list()["items"]) == 1


def test_narration_preferences_estimate_and_paid_refusal(pg_store, tmp_path, monkeypatch):
    store, admin, _ = pg_store
    script_id, _ = _seed_production_graph(admin, prod_id="prod-narr", approve_script=True)
    settings = SimpleNamespace(data_dir=tmp_path, piper_executable=None, piper_models=None, kokoro_python=None, kokoro_model_dir=None)
    svc = NarrationService(settings, store)

    # Preferência inicial vazia e persistência em horus.preferences (sem JSON em disco)
    assert svc.get_preference() == {"provider": None, "voice_id": None}
    voices = svc.provider("windows_sapi").voices()
    if voices:
        saved = svc.save_preference(Preference(provider="windows_sapi", voice_id=voices[0]["id"]))
        assert saved == {"provider": "windows_sapi", "voice_id": voices[0]["id"]}
        assert not (tmp_path / "narration-preference.json").exists()
        svc2 = NarrationService(settings, store)
        assert svc2.get_preference() == saved

    # Estimativa por roteiro aprovado no PG
    monkeypatch.setattr(svc.provider("windows_sapi"), "estimate", lambda text: {"characters": len(text)})
    est = svc.estimate(EstimateRequest(provider="windows_sapi", voice_id="v1", script_id=script_id, script_version=1))
    assert est["characters"] == len("Primeira fala.")

    # Roteiro ausente -> 404
    with pytest.raises(NarrationError) as missing:
        svc.estimate(EstimateRequest(provider="windows_sapi", voice_id="v1", script_id="b" * 32, script_version=1))
    assert missing.value.status == 404

    # Versão errada -> 409
    with pytest.raises(NarrationError) as unapproved:
        svc.estimate(EstimateRequest(provider="windows_sapi", voice_id="v1", script_id=script_id, script_version=2))
    assert unapproved.value.status == 409

    # Provedor pago recusado no preview
    from horus_backend.narration.service import PreviewRequest

    with pytest.raises(NarrationError) as paid:
        svc.preview(PreviewRequest(provider="elevenlabs", voice_id="v1", text="Teste"))
    assert paid.value.status == 403
    assert paid.value.code == "paid_provider_not_authorized"
