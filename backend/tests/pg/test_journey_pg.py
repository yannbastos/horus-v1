"""Jornada HTTP completa em PostgreSQL com bloqueio de `sqlite3` em runtime (HOR-164 / I-final A)."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient
import psycopg
import pytest

from horus_backend.api import create_app
from horus_backend.config import Settings
from horus_backend.database.manager import DatabaseManager
from horus_backend.database.vault import ConnectionVault
from keyring_support import MemoryKeyring
from schema_support import schema_database
from test_database_contract import CONFIG as PG_TEST_CONFIG


def _poll_until(client: TestClient, path: str, headers: dict, predicate, *, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if predicate(last):
            return last
        time.sleep(0.1)
    raise AssertionError(f"Timeout aguardando {path}: último estado = {last}")


@pytest.fixture
def _journey_db(pg_cluster):
    with schema_database(pg_cluster) as (admin, pool):
        with pool.connection() as conn:
            dbname = conn.info.dbname
        admin.execute(
            "INSERT INTO horus.database_identity(singleton, instance_id, created_at) "
            "VALUES (true, gen_random_uuid(), now()) ON CONFLICT (singleton) DO NOTHING"
        )
        yield dbname


@pytest.mark.pg_strong
def test_http_journey_pg_without_sqlite(pg_cluster, _journey_db, tmp_path, monkeypatch):
    """health -> status -> conectar banco -> job -> workflow/checkpoint -> produção simulada -> comentário -> exportação."""
    dbname = _journey_db

    # Bloqueia sqlite3/_sqlite3 no processo da API e em qualquer subprocesso Python filho (ex.: production_worker).
    monkeypatch.setitem(sys.modules, "sqlite3", None)
    monkeypatch.setitem(sys.modules, "_sqlite3", None)
    hook_dir = tmp_path / "no_sqlite_hook"
    hook_dir.mkdir()
    marker_file = tmp_path / "worker_subproc_ran.txt"
    (hook_dir / "sitecustomize.py").write_text(
        "import os, sys\n"
        "sys.modules['sqlite3'] = None\n"
        "sys.modules['_sqlite3'] = None\n"
        f"with open({str(marker_file)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(f'{os.getpid()}\\n')\n",
        encoding="utf-8",
    )
    src_dir = Path(__file__).resolve().parents[2] / "src"
    existing_pp = os.environ.get("PYTHONPATH", "")
    pp_parts = [str(hook_dir), str(src_dir)] + ([existing_pp] if existing_pp else [])
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(pp_parts))

    from horus_backend.processes import OwnedProcess

    orig_start = OwnedProcess.start

    @classmethod
    async def wrapped_start(cls, command, *, env=None, cwd=None, **kwargs):
        patched_env = dict(env or {})
        patched_env["PYTHONPATH"] = os.pathsep.join(
            [str(hook_dir), *(p for p in [patched_env.get("PYTHONPATH", "")] if p)]
        )
        return await orig_start.__func__(cls, command, env=patched_env, cwd=cwd, **kwargs)

    monkeypatch.setattr(OwnedProcess, "start", wrapped_start)

    memory_keyring = MemoryKeyring()
    monkeypatch.setattr(ConnectionVault, "backend", lambda self: memory_keyring)

    def local_type(self, config):
        class LocalConnection(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo="", **kwargs):
                return super().connect(
                    pg_cluster.dsn(dbname, "horus_app"),
                    autocommit=True,
                    password=config.password.get_secret_value(),
                    prepare_threshold=None,
                )

        return LocalConnection

    monkeypatch.setattr(DatabaseManager, "_make_connection_type", local_type)
    monkeypatch.setattr("horus_backend.database.manager.resolve_public", lambda *_: "8.8.8.8")

    token = "journey-pg-test-token-0123456789abcdef"
    headers = {"Authorization": f"Bearer {token}"}
    data_dir = tmp_path / "workspace"
    settings = Settings(
        token=token,
        data_dir=data_dir,
        factory_root=None,
        hermes_executable=None,
        hermes_config=tmp_path / "hermes.json",
    )
    app = create_app(settings)

    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        # 1. Health e status inicial em modo diagnóstico (sem banco).
        h1 = client.get("/health", headers=headers)
        assert h1.status_code == 200
        assert h1.json()["database_id"] is None
        assert h1.json()["database_state"] == "not_configured"

        s1 = client.get("/v1/database/status", headers=headers)
        assert s1.status_code == 200
        assert s1.json()["state"] == "not_configured"

        blocked = client.get("/v1/workflows", headers=headers)
        assert blocked.status_code == 503
        assert blocked.json()["detail"]["code"] == "database_unavailable"

        # 2. Conectar ao banco PostgreSQL de teste.
        conn_resp = client.post("/v1/database/connection", headers=headers, json=PG_TEST_CONFIG)
        assert conn_resp.status_code == 200, conn_resp.text
        assert conn_resp.json()["state"] == "connected"

        h2 = client.get("/health", headers=headers)
        assert h2.status_code == 200
        assert h2.json()["database_id"] is not None
        assert h2.json()["database_state"] == "connected"

        # 3. Registrar mídia e criar dataset demo do Radar (exercita Store e RadarState em PG).
        media_file = tmp_path / "sample.mp4"
        media_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
        media_resp = client.post("/v1/media", headers=headers, json={"path": str(media_file)})
        assert media_resp.status_code == 200, media_resp.text

        demo_resp = client.post(
            "/v1/radar/datasets/demo",
            headers={**headers, "Idempotency-Key": "journey-radar-demo-1"},
        )
        assert demo_resp.status_code == 201, demo_resp.text

        # 4. Checkpoint humano + Workflow manual + roteiro + revisão de roteiro.
        cp_resp = client.post(
            "/v1/checkpoints",
            headers={**headers, "Idempotency-Key": "journey-cp-1"},
            json={
                "action": "publish",
                "subject": "a" * 64,
                "description": "Aprovação humana para jornada PG",
                "approved_by": "human",
            },
        )
        assert cp_resp.status_code == 201, cp_resp.text

        wf_resp = client.post(
            "/v1/workflows/manual",
            headers={**headers, "Idempotency-Key": "journey-wf-1"},
            json={
                "title": "Jornada PG",
                "objective": "Explicar persistência PostgreSQL sem SQLite",
            },
        )
        assert wf_resp.status_code == 201, wf_resp.text
        wf = wf_resp.json()
        wf_id = wf["workflow_id"]

        wf_detail = client.get(f"/v1/workflows/{wf_id}", headers=headers)
        assert wf_detail.status_code == 200, wf_detail.text

        script_resp = client.post(
            f"/v1/workflows/{wf_id}/scripts",
            headers={**headers, "Idempotency-Key": "journey-script-1"},
            json={
                "expected_revision": wf_detail.json()["revision"],
                "proposal_artifact_id": wf["proposal_artifact_id"],
            },
        )
        assert script_resp.status_code == 201, script_resp.text
        script_id = script_resp.json()["id"]

        script_rev = client.post(
            f"/v1/scripts/{script_id}/review",
            headers={**headers, "Idempotency-Key": "journey-script-rev-1"},
            json={"decision": "approve", "expected_version": 1},
        )
        assert script_rev.status_code == 200, script_rev.text

        # 5. Produção simulada (exercita subprocesso worker sem sqlite3).
        prod_resp = client.post(
            f"/v1/scripts/{script_id}/productions",
            headers={**headers, "Idempotency-Key": "journey-prod-1"},
            json={"expected_version": 1, "mode": "simulated"},
        )
        assert prod_resp.status_code == 201, prod_resp.text
        prod_id = prod_resp.json()["id"]
        job_id = prod_resp.json()["job_id"]

        job_done = _poll_until(
            client,
            f"/v1/jobs/{job_id}",
            headers,
            lambda j: j["status"] in {"succeeded", "failed", "blocked", "interrupted"},
            timeout=20.0,
        )
        assert job_done["status"] == "succeeded", f"Produção simulada falhou: {job_done}"
        assert marker_file.is_file(), "Subprocesso worker não executou com sitecustomize de bloqueio SQLite."

        prod_data = client.get(f"/v1/productions/{prod_id}", headers=headers).json()
        target_artifact = prod_data["artifacts"][0]

        # 6. Revisão de produção simulada é recusada (regra de segurança: exige vídeo real).
        prod_rev_sim = client.post(
            f"/v1/productions/{prod_id}/review",
            headers={**headers, "Idempotency-Key": "journey-prod-rev-sim"},
            json={
                "decision": "approve",
                "artifact_sha256": target_artifact["sha256"],
                "acknowledge_quality_errors": True,
            },
        )
        assert prod_rev_sim.status_code == 409, prod_rev_sim.text

        # 7. Produção local_demo sintética para exercitar revisão (200), comentário (201) e exportação (201).
        import hashlib
        import json
        import uuid
        from horus_backend.production import inspect_artifact
        from horus_backend.storage import canonical_json_bytes, now as utc_now

        prod2_id = uuid.uuid4().hex
        prod2_job_id = uuid.uuid4().hex
        prod2_root = data_dir / "productions" / prod2_id
        prod2_root.mkdir(parents=True, exist_ok=True)
        video_bytes = b"\x00\x00\x00\x18ftypmp42" + b"0" * 128
        video_file = prod2_root / "video.mp4"
        video_file.write_bytes(video_bytes)
        (prod2_root / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nOlá\n", encoding="utf-8")
        video_art = inspect_artifact(prod2_root, "video.mp4", "video")
        subs_art = inspect_artifact(prod2_root, "subtitles.srt", "subtitles")
        video_sha = video_art["sha256"]
        artifacts_list = [video_art, subs_art]
        ts = utc_now()
        with app.state.store.uow() as conn:
            conn.execute(
                """INSERT INTO horus.jobs(id, idempotency_key, payload, payload_canonical, status, result, created_at, updated_at)
                   VALUES (%s, %s, %s::jsonb, %s, 'succeeded', %s::jsonb, %s, %s)""",
                (
                    prod2_job_id,
                    f"production:{prod2_id}",
                    json.dumps({"operation": "production.run", "production_id": prod2_id}),
                    canonical_json_bytes({"operation": "production.run", "production_id": prod2_id}),
                    json.dumps({"stage": "complete"}),
                    ts,
                    ts,
                ),
            )
            conn.execute(
                """INSERT INTO horus.productions(
                       id, script_id, script_version, script_content_hash, script_review, script_review_canonical,
                       job_id, mode, plan, plan_canonical, artifacts, created_at
                   )
                   SELECT %s, script_id, script_version, script_content_hash, script_review, script_review_canonical,
                          %s, 'local_demo', plan, plan_canonical, %s::jsonb, %s
                   FROM horus.productions WHERE id = %s""",
                (
                    prod2_id,
                    prod2_job_id,
                    json.dumps(artifacts_list),
                    ts,
                    prod_id,
                ),
            )

        prod_rev = client.post(
            f"/v1/productions/{prod2_id}/review",
            headers={**headers, "Idempotency-Key": "journey-prod-rev-1"},
            json={
                "decision": "approve",
                "artifact_sha256": video_sha,
                "acknowledge_quality_errors": True,
            },
        )
        assert prod_rev.status_code == 200, prod_rev.text

        comment_resp = client.post(
            f"/v1/productions/{prod2_id}/comments",
            headers={**headers, "Idempotency-Key": "journey-comment-1"},
            json={
                "artifact_sha256": video_sha,
                "at_seconds": 1.0,
                "text": "Áudio e corte validados em PostgreSQL.",
                "author": "Operador",
            },
        )
        assert comment_resp.status_code == 201, comment_resp.text

        export_resp = client.post(
            f"/v1/productions/{prod2_id}/export",
            headers={**headers, "Idempotency-Key": "journey-export-1"},
        )
        assert export_resp.status_code == 201, export_resp.text
        assert export_resp.json()["production_id"] == prod2_id

    # Nenhum arquivo .db/.sqlite* deve existir no data_dir.
    if data_dir.exists():
        db_files = [
            p for p in data_dir.rglob("*")
            if p.is_file() and any(s in p.name.lower() for s in (".db", ".sqlite", "-wal", "-shm"))
        ]
        assert not db_files, f"Arquivos SQLite encontrados em data_dir: {db_files}"
