"""Testes PostgreSQL para PublishingService e ExportService (HOR-164 / I7)."""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import psycopg
import pytest
from psycopg.types.json import Jsonb

from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.database.manager import DatabaseManager
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from horus_backend.exports import ExportService
from horus_backend.production import inspect_artifact
from horus_backend.publishing import (
    DISCLOSURE_MARKER,
    LICENSES_PENDING,
    LICENSES_REVIEWED,
    MetadataRequest,
    MetadataTextError,
    PublishingService,
    ThumbnailRequest,
)
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
def _editorial_pe_db(pg_cluster):
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
def pg_store(pg_cluster, _editorial_pe_db, tmp_path, monkeypatch):
    database, admin = _editorial_pe_db
    manager = _make_pg_manager(pg_cluster, database, tmp_path, monkeypatch)
    store = Store(manager, data_dir=tmp_path)
    try:
        yield store, admin, manager
    finally:
        manager.close()
        admin.execute(
            "TRUNCATE horus.publishing_receipts, horus.license_reviews, horus.script_receipts, "
            "horus.productions, horus.scripts, horus.artifacts, horus.job_attempts, horus.workflows, horus.jobs "
            "RESTART IDENTITY CASCADE"
        )


def _png_bytes(width: int = 1080, height: int = 1920) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + b"\x00\x00\x00" * width)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class _ProductionsAdapter:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self.manager = SimpleNamespace(tokens=None)

    def root(self, production_id: str) -> Path:
        return self.settings.data_dir / "productions" / production_id

    @staticmethod
    def require_local_data(production_id: str) -> None:
        return None


def _seed_approved_production(
    admin,
    data_dir: Path,
    *,
    prod_id: str = "a" * 32,
    approved: bool = True,
    job_status: str = "succeeded",
    subtitles_text: str = "1\n00:00:00,000 --> 00:00:01,000\nOlá mundo\n",
    with_provenance: bool = False,
):
    now_dt = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)
    wf_id = f"wf-{prod_id[:8]}"
    art_job_id = f"job-art-{prod_id[:8]}"
    an_job_id = f"job-an-{prod_id[:8]}"
    prod_job_id = f"job-prod-{prod_id[:8]}"
    att_id = f"att-{prod_id[:8]}"
    an_att_id = f"att-an-{prod_id[:8]}"
    art_id = f"art-{prod_id[:8]}"
    an_art_id = f"art-an-{prod_id[:8]}"
    script_id = f"sc-{prod_id[:8]}"

    wf_payload = {"topic": "teste"}
    admin.execute(
        "INSERT INTO horus.workflows (id, idempotency_key, payload, payload_canonical, revision, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, 1, 'approved', %s, %s)",
        (wf_id, f"wf-{wf_id}", Jsonb(wf_payload), canonical_json_bytes(wf_payload), now_dt, now_dt),
    )
    for jid, res in (
        (art_job_id, None),
        (an_job_id, None),
        (prod_job_id, {"narration_disclosure": "Narração local sintetizada."}),
    ):
        st = job_status if jid == prod_job_id else "succeeded"
        p_obj = {"operation": "factory.profile"}
        admin.execute(
            "INSERT INTO horus.jobs (id, idempotency_key, payload, payload_canonical, status, created_at, updated_at, result, workflow_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (jid, f"k-{jid}", Jsonb(p_obj), canonical_json_bytes(p_obj), st, now_dt, now_dt, Jsonb(res) if res else None, wf_id),
        )
    in_art = {}
    for aid, jid in ((att_id, art_job_id), (an_att_id, an_job_id)):
        admin.execute(
            "INSERT INTO horus.job_attempts (id, job_id, number, status, executor, input_hash, input_artifacts, input_artifacts_canonical, started_at, finished_at) "
            "VALUES (%s, %s, 1, 'succeeded', 'simulated', %s, %s, %s, %s, %s)",
            (aid, jid, "0" * 64, Jsonb(in_art), canonical_json_bytes(in_art), now_dt, now_dt),
        )

    analysis_doc = {"result": {"sources": [{"id": "src_a", "url": "https://example.com/a", "title": "Fonte A"}]}}
    prop_doc = {"proposal": "ok"}
    admin.execute(
        "INSERT INTO horus.artifacts (id, workflow_id, revision, stage, job_id, attempt_id, schema_version, content, content_canonical, content_hash, created_at) "
        "VALUES (%s, %s, 1, 'proposal', %s, %s, 1, %s, %s, %s, %s)",
        (art_id, wf_id, art_job_id, att_id, Jsonb(prop_doc), canonical_json_bytes(prop_doc), digest(prop_doc), now_dt),
    )
    admin.execute(
        "INSERT INTO horus.artifacts (id, workflow_id, revision, stage, job_id, attempt_id, schema_version, content, content_canonical, content_hash, created_at) "
        "VALUES (%s, %s, 1, 'analysis', %s, %s, 1, %s, %s, %s, %s)",
        (an_art_id, wf_id, an_job_id, an_att_id, Jsonb(analysis_doc), canonical_json_bytes(analysis_doc), digest(analysis_doc), now_dt),
    )

    doc = {
        "schema_version": 1,
        "simulated": True,
        "title": "Vídeo de Teste",
        "sections": [
            {"id": "abertura", "kind": "hook", "heading": "Abertura", "narration": "Texto inicial.", "source_ids": ["src_a"]},
            {"id": "corpo-1", "kind": "body", "heading": "Corpo", "narration": "Meio do vídeo.", "source_ids": []},
            {"id": "fim", "kind": "conclusion", "heading": "Fim", "narration": "Encerramento.", "source_ids": []},
        ],
    }
    script_review = {"decision": "approve", "reviewed_at": now_dt.isoformat(), "operator_id": "op1"}
    admin.execute(
        "INSERT INTO horus.scripts (id, workflow_id, workflow_revision, proposal_artifact_id, proposal_content_hash, version, status, document, document_canonical, created_at, review, review_canonical) "
        "VALUES (%s, %s, 1, %s, %s, 1, 'approved', %s, %s, %s, %s, %s)",
        (script_id, wf_id, art_id, digest(prop_doc), Jsonb(doc), canonical_json_bytes(doc), now_dt, Jsonb(script_review), canonical_json_bytes(script_review)),
    )
    rec_req = {"decision": "approve"}
    rec_resp = {"id": script_id, "version": 1, "status": "approved", "review": script_review, "document": doc}
    admin.execute(
        "INSERT INTO horus.script_receipts (scope, idempotency_key, request, request_canonical, response, response_canonical) "
        "VALUES (%s, 'rk1', %s, %s, %s, %s)",
        (f"review:{script_id}", Jsonb(rec_req), canonical_json_bytes(rec_req), Jsonb(rec_resp), canonical_json_bytes(rec_resp)),
    )

    prod_root = data_dir / "productions" / prod_id
    prod_root.mkdir(parents=True, exist_ok=True)
    (prod_root / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")
    (prod_root / "subtitles.srt").write_text(subtitles_text, encoding="utf-8")
    artifacts = [
        inspect_artifact(prod_root, "video.mp4", "video"),
        inspect_artifact(prod_root, "subtitles.srt", "subtitles"),
    ]
    if with_provenance:
        prov = {
            "schema_version": 1,
            "assets": [
                {
                    "id": "asset-1",
                    "kind": "image",
                    "source": "stock",
                    "license": "CC-BY-4.0",
                    "commercial_use": "allowed",
                    "attribution": "Autor X",
                    "sha256": artifacts[0]["sha256"],
                    "relative_path": "video.mp4",
                }
            ],
        }
        (prod_root / "asset-provenance.json").write_text(json.dumps(prov), encoding="utf-8")
        artifacts.append(inspect_artifact(prod_root, "asset-provenance.json", "manifest"))

    prod_review = {"decision": "approve" if approved else "reject", "artifact_sha256": artifacts[0]["sha256"], "reviewed_at": now_dt.isoformat()}
    plan = {"mode": "simulated"}
    admin.execute(
        "INSERT INTO horus.productions (id, script_id, script_version, script_content_hash, script_review, script_review_canonical, job_id, mode, plan, plan_canonical, artifacts, review, review_canonical, created_at) "
        "VALUES (%s, %s, 1, %s, %s, %s, %s, 'simulated', %s, %s, %s, %s, %s, %s)",
        (
            prod_id,
            script_id,
            digest(doc),
            Jsonb(script_review),
            canonical_json_bytes(script_review),
            prod_job_id,
            Jsonb(plan),
            canonical_json_bytes(plan),
            Jsonb(artifacts),
            Jsonb(prod_review),
            canonical_json_bytes(prod_review),
            now_dt,
        ),
    )
    return prod_id, artifacts[0]["sha256"]


def test_publishing_and_exports_unavailable_raise_503(pg_cluster, _editorial_pe_db, tmp_path, monkeypatch):
    database, _ = _editorial_pe_db
    unconfigured = _make_pg_manager(pg_cluster, database, tmp_path / "u", monkeypatch, configure=False)
    try:
        for store in (Store(None, data_dir=tmp_path), Store(unconfigured, data_dir=tmp_path)):
            settings = SimpleNamespace(data_dir=tmp_path, token="tok-secret", ffmpeg=None)
            prods = _ProductionsAdapter(store, settings)
            pub = PublishingService(prods)
            exp = ExportService(prods)
            for call in (
                lambda: pub.metadata("a" * 32),
                lambda: pub.thumbnail("a" * 32, ThumbnailRequest(timecode_seconds=1.0), "k1"),
                lambda: exp.list("a" * 32),
                lambda: exp.create("a" * 32, "ek1"),
            ):
                with pytest.raises(DatabaseUnavailable) as exc:
                    call()
                assert exc.value.http_status == 503
                assert exc.value.code == "database_unavailable"
    finally:
        unconfigured.close()


def test_publishing_thumbnail_idempotency_failure_and_tampering(pg_store, tmp_path, monkeypatch):
    store, admin, _ = pg_store
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"stub")
    ffprobe.write_bytes(b"stub")
    settings = SimpleNamespace(data_dir=tmp_path, token="tok-secret", ffmpeg=ffmpeg)
    prod_id, _ = _seed_approved_production(admin, tmp_path)
    pub = PublishingService(_ProductionsAdapter(store, settings))

    def fake_run(command, timeout):
        exe = Path(command[0]).name
        if "ffprobe" in exe:
            return json.dumps({"format": {"duration": "10.0"}})
        out = Path(command[-1])
        if out.suffix == ".png":
            out.write_bytes(_png_bytes(1080, 1920))
        else:
            out.write_bytes(b"\xff\xd8\xff\xe0jpgdata")
        return ""

    monkeypatch.setattr(pub, "_run", fake_run)

    saved, created = pub.thumbnail(prod_id, ThumbnailRequest(timecode_seconds=2.5), "thumb-key-1")
    assert created is True
    assert saved["publishable"] is False
    assert saved["width"] == 1080 and saved["height"] == 1920

    # Replay idempotente via horus.publishing_receipts
    replay, created2 = pub.thumbnail(prod_id, ThumbnailRequest(timecode_seconds=2.5), "thumb-key-1")
    assert created2 is False
    assert replay["video_sha256"] == saved["video_sha256"]

    # Mesma chave com outro timecode -> conflito
    with pytest.raises(WorkflowConflict):
        pub.thumbnail(prod_id, ThumbnailRequest(timecode_seconds=3.0), "thumb-key-1")

    # Falha registrada em horus.publishing_receipts bloqueia mesma chave e permite nova chave
    with pytest.raises(WorkflowConflict):
        pub.thumbnail(prod_id, ThumbnailRequest(timecode_seconds=25.0), "thumb-fail-key")
    with pytest.raises(WorkflowConflict) as fail_exc:
        pub.thumbnail(prod_id, ThumbnailRequest(timecode_seconds=1.0), "thumb-fail-key")
    assert "Tentativa anterior desta chave falhou" in str(fail_exc.value)

    saved2, created3 = pub.thumbnail(prod_id, ThumbnailRequest(timecode_seconds=1.0), "thumb-key-2")
    assert created3 is True

    # Adulteração em disco bloqueia replay
    png_path = tmp_path / "productions" / prod_id / saved["artifacts"][0]["relative_path"]
    png_path.write_bytes(b"tampered")
    with pytest.raises(WorkflowConflict):
        pub.thumbnail(prod_id, ThumbnailRequest(timecode_seconds=2.5), "thumb-key-1")


def test_publishing_metadata_disclosures_and_unapproved_refusal(pg_store, tmp_path):
    store, admin, _ = pg_store
    settings = SimpleNamespace(data_dir=tmp_path, token="tok-secret", ffmpeg=None)
    prod_id, _ = _seed_approved_production(admin, tmp_path, with_provenance=True)
    pub = PublishingService(_ProductionsAdapter(store, settings))

    meta = pub.metadata(prod_id)
    assert meta["publishable"] is False
    assert meta["licenses_pending"] is True
    assert LICENSES_PENDING in meta["description"]
    assert meta["sources"] == [{"id": "src_a", "url": "https://example.com/a", "title": "Fonte A"}]

    # Editar mantendo texto antes do marcador
    edited = pub.edit_metadata(prod_id, MetadataRequest(title="Novo Título", description="Texto novo\n\n" + DISCLOSURE_MARKER.strip()))
    assert edited["title"] == "Novo Título"
    assert edited["description"].startswith("Texto novo")
    assert LICENSES_PENDING in edited["description"]

    # Tentar alterar texto após o marcador -> MetadataTextError (422)
    with pytest.raises(MetadataTextError):
        pub.edit_metadata(prod_id, MetadataRequest(title="T", description="Ok" + DISCLOSURE_MARKER + "texto forjado"))

    # Produção rejeitada não publica
    bad_prod, _ = _seed_approved_production(admin, tmp_path, prod_id="c" * 32, approved=False)
    with pytest.raises(WorkflowConflict):
        pub.metadata(bad_prod)


def test_exports_create_redaction_idempotency_and_failures(pg_store, tmp_path, monkeypatch):
    store, admin, _ = pg_store
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"stub")
    ffprobe.write_bytes(b"stub")
    settings = SimpleNamespace(data_dir=tmp_path, token="tok-secret-123", ffmpeg=ffmpeg)
    prod_id, video_sha = _seed_approved_production(admin, tmp_path, with_provenance=True)

    # Registrar review de licença e thumbnail + metadata com token para testar redação
    now_dt = datetime(2026, 11, 17, 15, 30, tzinfo=timezone.utc)
    lr_req = {"decision": "verified"}
    admin.execute(
        "INSERT INTO horus.license_reviews (id, production_id, asset_id, asset_sha256, decision, license, evidence, reviewed_by, reviewed_at, idempotency_key, request, request_canonical) "
        "VALUES ('lr1', %s, 'asset-1', %s, 'verified', 'CC-BY-4.0', 'Conferido em C:\\\\Users\\\\local\\\\doc.pdf', 'human', %s, 'lk1', %s, %s)",
        (prod_id, video_sha, now_dt, Jsonb(lr_req), canonical_json_bytes(lr_req)),
    )

    prods = _ProductionsAdapter(store, settings)
    pub = PublishingService(prods)
    monkeypatch.setattr(
        pub,
        "_run",
        lambda cmd, timeout: (
            json.dumps({"format": {"duration": "10.0"}})
            if "ffprobe" in Path(cmd[0]).name
            else (Path(cmd[-1]).write_bytes(_png_bytes() if Path(cmd[-1]).suffix == ".png" else b"\xff\xd8\xff\xe0jpg") or "")
        ),
    )
    pub.thumbnail(prod_id, ThumbnailRequest(timecode_seconds=1.0), "tk1")
    pub.edit_metadata(prod_id, MetadataRequest(title="Titulo", description="Descrição com tok-secret-123"))

    exp = ExportService(prods)
    meta, created = exp.create(prod_id, "exp-key-1")
    assert created is True
    assert meta["publishable"] is False

    # Replay idempotente
    replay, created2 = exp.create(prod_id, "exp-key-1")
    assert created2 is False
    assert replay["sha256"] == meta["sha256"]

    zip_path = exp.file(prod_id, meta["id"])
    with ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert {"manifest.json", "video.mp4", "subtitles.srt", "script.json", "script.txt", "quality-report.json", "licenses.json", "review.json", "metadata.json", "thumbnail.png", "thumbnail.jpg"} <= names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["publishable"] is False
        assert manifest["metadata_redacted"] is True
        assert "tok-secret-123" not in zf.read("metadata.json").decode("utf-8")
        assert "C:\\Users\\local" not in zf.read("licenses.json").decode("utf-8")

    # Listagem e recusa de pacote adulterado sem esconder os bons
    assert len(exp.list(prod_id)["items"]) == 1

    # Legendas com caminho absoluto são recusadas
    bad_prod, _ = _seed_approved_production(
        admin,
        tmp_path,
        prod_id="d" * 32,
        subtitles_text="1\n00:00:00,000 --> 00:00:01,000\nC:\\Users\\privado\\segredo\n",
    )
    with pytest.raises(WorkflowConflict):
        exp.create(bad_prod, "exp-bad-sub")
    # Mesma chave continua falhada em horus.publishing_receipts
    with pytest.raises(WorkflowConflict) as fail_again:
        exp.create(bad_prod, "exp-bad-sub")
    assert "Tentativa anterior desta chave falhou" in str(fail_again.value)
