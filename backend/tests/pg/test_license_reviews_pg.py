"""Testes PostgreSQL para LicenseReviewService (HOR-164 / I5)."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import threading
import uuid

import psycopg
import pytest

from horus_backend.config import Settings
from horus_backend.jobs import JobManager
from horus_backend.license_reviews import LicenseReviewRequest, LicenseReviewService, ReviewLicense
from horus_backend.production import ProductionRequest
from horus_backend.scripts import ScriptRequest, ScriptReviewRequest, ScriptService
import horus_backend.security as security
from horus_backend.storage import DatabaseUnavailable, Store
from horus_backend.workflows import WorkflowConflict, encode
from schema_support import schema_database
from test_scripts_pg import _approved_workflow

STOCK_LICENSE = ReviewLicense(
    id="pexels-license",
    name="Pexels License",
    url="https://www.pexels.com/license/",
    commercial_use="allowed",
    attribution_required=False,
)


@pytest.fixture(scope="module")
def _lr_schema_db(pg_cluster):
    with schema_database(pg_cluster) as state:
        yield state


@pytest.fixture
def pg_pool(request, pg_cluster):
    if request.node.get_closest_marker("pg_strong"):
        with schema_database(pg_cluster) as (_, pool):
            yield pool
    else:
        admin, pool = request.getfixturevalue("_lr_schema_db")
        try:
            yield pool
        finally:
            admin.execute(
                """TRUNCATE horus.workflows, horus.jobs, horus.job_attempts, horus.artifacts,
                            horus.approvals, horus.workflow_events, horus.cognitive_runs,
                            horus.authorizations, horus.human_checkpoints, horus.transmissions,
                            horus.events, horus.scripts, horus.script_receipts,
                            horus.productions, horus.production_receipts, horus.license_reviews
                   RESTART IDENTITY CASCADE"""
            )


@pytest.fixture
def settings(tmp_path):
    profile = tmp_path / "profile"
    security.harden_profile(profile)
    connection = tmp_path / "connection.json"
    connection.write_text(json.dumps({"base_url": "http://127.0.0.1:8643", "token": "connection-ficticia-01234567890123"}))
    return Settings(
        token="lr-pg-test-token-01234567890123456",
        data_dir=tmp_path / "data",
        factory_root=None,
        hermes_executable=None,
        hermes_profile=profile,
        hermes_config=connection,
        workflow_executor="simulated",
        max_tokens_per_run=10000,
        max_tokens_per_day=50000,
        max_runs_per_day=5,
        budget_total_usd=Decimal("10"),
        budget_daily_usd=Decimal("5"),
        price_input_usd_per_mtok=Decimal("10"),
        price_output_usd_per_mtok=Decimal("20"),
    )


def _seed_production_with_provenance(pg_pool, settings: Settings) -> tuple[Store, JobManager, dict, str]:
    store = Store(pg_pool, data_dir=settings.data_dir)
    wf = _approved_workflow(store, settings, key="wf-lr")
    scripts = ScriptService(store, settings)
    s1, _ = scripts.create(wf["id"], ScriptRequest(expected_revision=1, proposal_artifact_id=wf["current_artifact_id"]), "gen-lr")
    scripts.review(s1["id"], ScriptReviewRequest(expected_version=1, decision="approve"), "rev-lr")

    manager = JobManager(store, settings, device_id="dev-lr")
    prod, _ = asyncio.run(manager.productions.create(s1["id"], ProductionRequest(expected_version=1, mode="local_demo"), "prod-lr"))
    root = manager.productions.root(prod["id"])
    inputs_dir = root / "canais" / "demo" / "videos" / "fixture"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    asset_file = inputs_dir / "scene-2.png"
    asset_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    asset_sha = hashlib.sha256(asset_file.read_bytes()).hexdigest()

    unverified = {
        "id": None,
        "name": None,
        "url": None,
        "commercial_use": "unknown",
        "attribution_required": None,
        "verified_by": None,
        "verified_at": None,
    }
    prov = {
        "schema_version": 1,
        "assets": [
            {
                "asset_id": "asset-bg-1",
                "relative_path": "canais/demo/videos/fixture/scene-2.png",
                "sha256": asset_sha,
                "kind": "image",
                "origin": "stock_pexels",
                "source_url": "https://www.pexels.com/photo/1/",
                "author": "Autor",
                "license": {**unverified, "id": "pexels", "name": "Pexels", "url": "https://www.pexels.com/license/"},
                "status": "unverified",
                "notes": "",
            }
        ],
    }
    prov_path = root / "asset-provenance.json"
    prov_path.write_text(encode(prov), encoding="utf-8")
    prov_sha = hashlib.sha256(prov_path.read_bytes()).hexdigest()
    artifacts = [
        {"id": uuid.uuid4().hex[:32], "kind": "manifest", "relative_path": "asset-provenance.json", "size_bytes": prov_path.stat().st_size, "sha256": prov_sha},
        {"id": uuid.uuid4().hex[:32], "kind": "scene", "relative_path": "canais/demo/videos/fixture/scene-2.png", "size_bytes": asset_file.stat().st_size, "sha256": asset_sha},
    ]
    with pg_pool.connection() as conn:
        with conn.transaction():
            conn.execute("UPDATE horus.productions SET artifacts = %s::jsonb WHERE id = %s", (encode(artifacts), prod["id"]))
    return store, manager, prod, asset_sha


def test_license_review_list_create_supersedes_and_idempotency(pg_pool, settings):
    store, _, prod, asset_sha = _seed_production_with_provenance(pg_pool, settings)
    service = LicenseReviewService(store, settings.data_dir)

    empty = service.list(prod["id"])
    assert empty["current"] == []
    assert empty["history"] == []
    assert empty["effective_provenance"]["assets"][0]["status"] == "unverified"

    req_verify = LicenseReviewRequest(
        asset_id="asset-bg-1",
        asset_sha256=asset_sha,
        decision="verified",
        license=STOCK_LICENSE,
        evidence="Conferido na página oficial da Pexels.",
        reviewed_by="human",
    )
    r1, created1 = service.create(prod["id"], req_verify, "lr-1")
    assert created1 is True
    assert r1["decision"] == "verified"
    assert r1["supersedes"] is None

    # Replay idempotente
    r1_replay, created_replay = service.create(prod["id"], req_verify, "lr-1")
    assert created_replay is False
    assert r1_replay == r1

    # Conflito de chave com decisão diferente
    req_reject = LicenseReviewRequest(
        asset_id="asset-bg-1",
        asset_sha256=asset_sha,
        decision="rejected",
        license=STOCK_LICENSE,
        evidence="Licença revogada.",
        reviewed_by="human",
    )
    with pytest.raises(WorkflowConflict):
        service.create(prod["id"], req_reject, "lr-1")

    # Nova decisão substitui (supersedes) preservando histórico
    r2, created2 = service.create(prod["id"], req_reject, "lr-2")
    assert created2 is True
    assert r2["supersedes"] == r1["id"]
    listed = service.list(prod["id"])
    assert len(listed["history"]) == 2
    assert len(listed["current"]) == 1
    assert listed["current"][0]["id"] == r2["id"]
    assert listed["effective_provenance"]["assets"][0]["status"] == "rejected"


def test_license_review_negative_cases(pg_pool, settings):
    store, manager, prod, asset_sha = _seed_production_with_provenance(pg_pool, settings)
    service = LicenseReviewService(store, settings.data_dir)

    # Produção inexistente -> KeyError
    with pytest.raises(KeyError):
        service.list("f" * 32)

    # commercial_use unknown com decision=verified -> recusado
    with pytest.raises(WorkflowConflict):
        service.create(
            prod["id"],
            LicenseReviewRequest(
                asset_id="asset-bg-1",
                asset_sha256=asset_sha,
                decision="verified",
                license=ReviewLicense(
                    id="pexels-license",
                    name="Pexels License",
                    url="https://www.pexels.com/license/",
                    commercial_use="unknown",
                    attribution_required=False,
                ),
                evidence="Tentativa com unknown.",
                reviewed_by="human",
            ),
            "lr-unk",
        )

    # sha256 divergente
    with pytest.raises(WorkflowConflict):
        service.create(
            prod["id"],
            LicenseReviewRequest(
                asset_id="asset-bg-1",
                asset_sha256="0" * 64,
                decision="verified",
                license=STOCK_LICENSE,
                evidence="Hash errado.",
                reviewed_by="human",
            ),
            "lr-sha",
        )

    # URL de host não autorizado
    with pytest.raises(WorkflowConflict):
        service.create(
            prod["id"],
            LicenseReviewRequest(
                asset_id="asset-bg-1",
                asset_sha256=asset_sha,
                decision="verified",
                license=ReviewLicense(
                    id="bad",
                    name="Bad",
                    url="https://evil.example.org/license",
                    commercial_use="allowed",
                    attribution_required=False,
                ),
                evidence="URL inválida.",
                reviewed_by="human",
            ),
            "lr-url",
        )

    # Proveniência adulterada no disco
    prov_path = manager.productions.root(prod["id"]) / "asset-provenance.json"
    prov_path.write_text('{"tampered": true}', encoding="utf-8")
    with pytest.raises(WorkflowConflict):
        service.create(
            prod["id"],
            LicenseReviewRequest(
                asset_id="asset-bg-1",
                asset_sha256=asset_sha,
                decision="verified",
                license=STOCK_LICENSE,
                evidence="Após adulteração.",
                reviewed_by="human",
            ),
            "lr-tamper",
        )


def test_license_reviews_are_append_only_in_postgres(pg_pool, settings):
    store, _, prod, asset_sha = _seed_production_with_provenance(pg_pool, settings)
    service = LicenseReviewService(store, settings.data_dir)
    r1, _ = service.create(
        prod["id"],
        LicenseReviewRequest(
            asset_id="asset-bg-1",
            asset_sha256=asset_sha,
            decision="verified",
            license=STOCK_LICENSE,
            evidence="Ok.",
            reviewed_by="human",
        ),
        "lr-app",
    )

    with pg_pool.connection() as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE horus.license_reviews SET decision = 'rejected' WHERE id = %s", (r1["id"],))
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM horus.license_reviews WHERE id = %s", (r1["id"],))


@pytest.mark.pg_strong
def test_concurrent_license_reviews_serialize_supersedes_chain(pg_pool, settings):
    store, _, prod, asset_sha = _seed_production_with_provenance(pg_pool, settings)
    service = LicenseReviewService(store, settings.data_dir)
    barrier = threading.Barrier(2)

    def worker(idx: int):
        barrier.wait(timeout=5)
        return service.create(
            prod["id"],
            LicenseReviewRequest(
                asset_id="asset-bg-1",
                asset_sha256=asset_sha,
                decision="verified" if idx == 1 else "rejected",
                license=STOCK_LICENSE,
                evidence=f"Decisão {idx}",
                reviewed_by="human",
            ),
            f"conc-{idx}",
        )[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        r1, r2 = list(pool.map(worker, [1, 2]))
    history = service.list(prod["id"])["history"]
    assert len(history) == 2
    assert history[0]["supersedes"] is None
    assert history[1]["supersedes"] == history[0]["id"]


def test_license_reviews_unavailable_raises_503(tmp_path):
    service = LicenseReviewService(Store(None), tmp_path)
    with pytest.raises(DatabaseUnavailable) as caught:
        service.list("a" * 32)
    assert caught.value.http_status == 503
