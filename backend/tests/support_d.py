"""Helpers compartilhados para os testes PostgreSQL do Grupo D (HOR-164)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
import zipfile

import psycopg

from horus_backend.database.expected_schema import EXPECTED_SCHEMA_VERSION
from horus_backend.database.manager import DatabaseManager
from horus_backend.database.models import ConnectionRequest
from horus_backend.database.vault import ConnectionVault
from horus_backend.storage import Store, canonical_json_bytes
from conftest import PG_TEST_CONFIG, PG_TEST_TOKEN
from keyring_support import MemoryKeyring

PG_DIR = str(Path(__file__).resolve().parent / "pg")
if PG_DIR not in sys.path:
    sys.path.insert(0, PG_DIR)


def auth_headers(extra: dict | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {PG_TEST_TOKEN}"}
    if extra:
        headers.update(extra)
    return headers


def make_pg_manager(cluster, database: str, vault_dir: Path, monkeypatch) -> DatabaseManager:
    """Cria um DatabaseManager conectado ao banco descartável `database` via `horus_app`."""
    vault = ConnectionVault(vault_dir, MemoryKeyring())
    vault.write(ConnectionRequest.model_validate(PG_TEST_CONFIG))
    manager = DatabaseManager(vault)

    def local_type(config):
        class LocalConnection(psycopg.Connection):
            @classmethod
            def connect(cls, conninfo="", **kwargs):
                return super().connect(
                    cluster.dsn(database, "horus_app"),
                    autocommit=True,
                    password=config.password.get_secret_value(),
                    prepare_threshold=None,
                )

        return LocalConnection

    monkeypatch.setattr(manager, "_make_connection_type", local_type)
    monkeypatch.setattr("horus_backend.database.manager.resolve_public", lambda *_: "8.8.8.8")
    assert manager.test().state == "connected"
    return manager


def make_pg_store(cluster, database: str, tmp_path: Path, monkeypatch) -> tuple[DatabaseManager, Store]:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    manager = make_pg_manager(cluster, database, tmp_path / "vault", monkeypatch)
    return manager, Store(manager, data_dir=data_dir)


def truncate_horus(cluster, database: str) -> None:
    """Limpa todas as tabelas operacionais do schema `horus` preservando migrations e identity."""
    with cluster.connect(database, autocommit=True) as admin:
        tables = admin.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'horus' "
            "AND tablename NOT IN ('schema_migrations', 'database_identity') ORDER BY tablename"
        ).fetchall()
        if tables:
            joined = ", ".join(f"horus.{t[0]}" for t in tables)
            admin.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")


@contextmanager
def isolated_empty_pg_db(cluster):
    """Provisiona um banco PostgreSQL isolado e vazio com o schema `horus` completo."""
    from schema_support import schema_database

    with schema_database(cluster) as (admin, pool):
        with pool.connection() as conn:
            dbname = conn.info.dbname
        admin.execute(
            "INSERT INTO horus.database_identity(singleton, instance_id, created_at) "
            "VALUES (true, gen_random_uuid(), now()) ON CONFLICT (singleton) DO NOTHING"
        )
        yield admin, dbname


def seed_radar_workflow(
    store: Store,
    marker: str,
    *,
    expires: str = "2026-10-01T00:00:00Z",
    items: int = 40,
    status: str = "awaiting_approval",
    wid: str | None = None,
) -> str:
    """Insere um workflow Radar sintético em `horus.workflows` contendo o marcador."""
    wid = wid or uuid.uuid4().hex
    dataset = {
        "items": [
            {
                "title": f"{marker} vídeo {i} " + "x" * 60,
                "url": f"https://example.org/{marker}/{i}",
                "channel": f"Canal {marker}",
            }
            for i in range(items)
        ]
    }
    payload = {
        "objective": "Avaliar referências",
        "dataset": dataset,
        "radar": {"snapshot_id": "snap-1", "expires_at": expires},
    }
    now = datetime.now(timezone.utc)
    with store.uow() as conn:
        conn.execute(
            "INSERT INTO horus.workflows("
            "id, idempotency_key, payload, revision, status, feedback, created_at, updated_at, executor, cognitive_client, payload_canonical"
            ") VALUES (%s, %s, %s, 1, %s, '', %s, %s, 'simulated', 'live', %s)",
            (wid, wid, json.dumps(payload, ensure_ascii=False), status, now, now, canonical_json_bytes(payload)),
        )
    return wid


def create_pg_backup_zip(
    path: Path,
    *,
    schema_version: str = EXPECTED_SCHEMA_VERSION,
    radar: bool = True,
    redacted: bool = False,
    age_days: int = 0,
    clock_now: datetime | None = None,
) -> Path:
    """Cria um pacote de backup lógico v2 sintético válido para testes de inventário/retenção."""
    now = clock_now or datetime.now(timezone.utc)
    rows: list[bytes] = []
    if radar:
        radar_meta = {"expires_at": "2026-10-01T00:00:00Z"}
        if redacted:
            radar_meta["redacted_at"] = "2026-11-01T00:00:00Z"
        row = {
            "id": "wf-1",
            "idempotency_key": "wf-1",
            "payload": {"dataset": {"items": [{"title": "x"}]}, "radar": radar_meta},
            "revision": 1,
            "status": "failed",
        }
        rows.append((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))

    dump_bytes = b"".join(rows)
    dump_sha = hashlib.sha256(dump_bytes).hexdigest()
    files_meta = [
        {"name": "pg_dump/workflows.jsonl", "size": len(dump_bytes), "sha256": dump_sha}
    ] if rows else []
    tables_meta = {"workflows": {"rows": len(rows), "sha256": dump_sha}} if rows else {}
    manifest = {
        "schema_version": 2,
        "format": "horus-pg-export-v2",
        "created_at": now.isoformat(),
        "contains_private_data": True,
        "database": {
            "schema_version": schema_version,
            "instance_id": str(uuid.uuid4()),
            "tables": tables_meta,
        },
        "files": files_meta,
        "excluded": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if rows:
            archive.writestr("pg_dump/workflows.jsonl", dump_bytes)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    if age_days:
        stamp = (now - timedelta(days=age_days)).timestamp()
        os.utime(path, (stamp, stamp))
    return path


def poll_job(client, job_id: str, *, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/jobs/{job_id}", headers=auth_headers())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data["status"] in {"succeeded", "failed", "blocked", "interrupted"}:
            return data
        time.sleep(0.05)
    raise AssertionError(f"Timeout aguardando job {job_id}")


def create_simulated_production(client, *, title: str = "Produção D") -> tuple[dict, str]:
    """Cria workflow manual, roteiro aprovado e produção simulada concluída via API PG."""
    key = uuid.uuid4().hex
    wf_resp = client.post(
        "/v1/workflows/manual",
        headers=auth_headers({"Idempotency-Key": f"wf-{key}"}),
        json={"title": title, "objective": "Validar persistência PostgreSQL no grupo D"},
    )
    assert wf_resp.status_code == 201, wf_resp.text
    wf = wf_resp.json()
    wf_id = wf["workflow_id"]
    wf_detail = client.get(f"/v1/workflows/{wf_id}", headers=auth_headers()).json()

    script_resp = client.post(
        f"/v1/workflows/{wf_id}/scripts",
        headers=auth_headers({"Idempotency-Key": f"script-{key}"}),
        json={
            "expected_revision": wf_detail["revision"],
            "proposal_artifact_id": wf["proposal_artifact_id"],
        },
    )
    assert script_resp.status_code == 201, script_resp.text
    script_id = script_resp.json()["id"]

    rev_resp = client.post(
        f"/v1/scripts/{script_id}/review",
        headers=auth_headers({"Idempotency-Key": f"rev-{key}"}),
        json={"decision": "approve", "expected_version": 1},
    )
    assert rev_resp.status_code == 200, rev_resp.text

    prod_resp = client.post(
        f"/v1/scripts/{script_id}/productions",
        headers=auth_headers({"Idempotency-Key": f"prod-{key}"}),
        json={"expected_version": 1, "mode": "simulated"},
    )
    assert prod_resp.status_code == 201, prod_resp.text
    prod = prod_resp.json()
    job_done = poll_job(client, prod["job_id"])
    assert job_done["status"] == "succeeded", job_done

    record = client.get(f"/v1/productions/{prod['id']}", headers=auth_headers()).json()
    artifact = record["artifacts"][0]
    return record, artifact["sha256"]


def post_comment(client, pid: str, body: dict, *, key: str | None = None):
    return client.post(
        f"/v1/productions/{pid}/comments",
        headers=auth_headers({"Idempotency-Key": key or uuid.uuid4().hex}),
        json=body,
    )


def comment_body(sha: str, *, text: str = "Cortar aqui", at_seconds: float = 1.0, author: str = "Operador") -> dict:
    return {
        "artifact_sha256": sha,
        "at_seconds": at_seconds,
        "text": text,
        "author": author,
    }


def create_renderable_production(client, app, data_dir: Path, *, approve: bool = False) -> tuple[dict, str]:
    """Cria produção `local_demo` com video.mp4 e subtitles.srt prontos para comentário/revisão/exportação."""
    from horus_backend.production import inspect_artifact
    from horus_backend.storage import now as utc_now

    sim_record, _ = create_simulated_production(client)
    prod2_id = uuid.uuid4().hex
    prod2_job_id = uuid.uuid4().hex
    prod2_root = Path(data_dir) / "productions" / prod2_id
    prod2_root.mkdir(parents=True, exist_ok=True)
    (prod2_root / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 128)
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
            (prod2_id, prod2_job_id, json.dumps(artifacts_list), ts, sim_record["id"]),
        )
    if approve:
        rev = client.post(
            f"/v1/productions/{prod2_id}/review",
            headers=auth_headers({"Idempotency-Key": f"rev-{uuid.uuid4().hex}"}),
            json={"decision": "approve", "artifact_sha256": video_sha, "acknowledge_quality_errors": True},
        )
        assert rev.status_code == 200, rev.text
    record = client.get(f"/v1/productions/{prod2_id}", headers=auth_headers()).json()
    return record, video_sha
