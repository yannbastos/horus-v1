"""Gerador de bases SQLite 100% sintéticas usando o DDL legado de migrations.py."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from horus_backend.migrations import MIGRATIONS


PRIVATE_MARKER_PROMPT = "PRIVATE_SECRET_PROMPT_XYZ_999"
PRIVATE_MARKER_SCRIPT = "PRIVATE_SCRIPT_TEXT_ABC_888"
LARGE_SEQ_BASE = 5_000_000_000


def build_synthetic_data_dir(
    root: Path,
    *,
    target_version: int = 12,
    include_radar: bool = True,
) -> Path:
    """Cria uma cópia fria sintética completa de data_dir com todas as 21 tabelas."""
    root.mkdir(parents=True, exist_ok=True)
    core_path = root / "horus.sqlite3"

    # Canonical com 1.0 preservado para provar preservação byte a byte em *_canonical.
    payload_wf = '{"operation":"synthetic_video","ratio":1.0,"prompt":"' + PRIVATE_MARKER_PROMPT + '"}'
    payload_job1 = '{"step":"prepare","scale":1.0}'
    payload_job2 = '{"step":"render","scale":1}'
    input_artifacts = '[{"id":"art-1","weight":1.0}]'
    result_attempt = '{"ok":true,"score":1.0}'
    content_artifact = '{"summary":"' + PRIVATE_MARKER_PROMPT + '","version":1.0}'
    req_json = '{"operator":"op-sintetico","limit":1.0}'
    doc_script = '{"title":"Roteiro Sintético","body":"' + PRIVATE_MARKER_SCRIPT + '"}'
    review_script = '{"decision":"approved","note":"ok"}'
    resp_receipt = '{"script_id":"scr-1","status":"approved"}'
    plan_prod = '{"steps":["a","b"],"fps":30.0}'
    artifacts_prod = '[{"kind":"mp4","bytes":1234}]'
    review_prod = '{"decision":"verified"}'
    req_checkpoint = '{"action":"publish","amount":12.50}'

    with closing(sqlite3.connect(core_path)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for ver in range(1, target_version + 1):
            for stmt in MIGRATIONS[ver]:
                db.execute(stmt)
            db.execute(f"PRAGMA user_version={ver}")

        db.execute(
            "INSERT INTO media(id,path,name,size,modified_ns,created_at) VALUES (?,?,?,?,?,?)",
            ("med-1", "/sintetico/clip.mp4", "clip.mp4", 1048576, 1700000000000000000, "2026-11-17T10:00:00Z"),
        )
        db.execute(
            "INSERT INTO workflows(id,idempotency_key,payload,revision,status,feedback,current_artifact_id,created_at,updated_at,executor,cognitive_client) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("wf-1", "wf-key-1", payload_wf, 1, "approved", "", None, "2026-11-17T10:00:00+00:00", "2026-11-17T10:05:00Z", "hermes", "simulated"),
        )
        db.execute(
            "INSERT INTO jobs(id,idempotency_key,payload,status,created_at,updated_at,attempt,result,error,workflow_id,workflow_revision,step_key,step_index,depends_on) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("job-1", "job-key-1", payload_job1, "succeeded", "2026-11-17T10:00:00Z", "2026-11-17T10:01:00Z", 1, '{"done":true}', None, "wf-1", 1, "prepare", 0, None),
        )
        db.execute(
            "INSERT INTO jobs(id,idempotency_key,payload,status,created_at,updated_at,attempt,result,error,workflow_id,workflow_revision,step_key,step_index,depends_on) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("job-2", "job-key-2", payload_job2, "succeeded", "2026-11-17T10:01:00Z", "2026-11-17T10:02:00Z", 1, None, None, "wf-1", 1, "render", 1, "job-1"),
        )
        db.execute(
            "INSERT INTO job_attempts(id,job_id,number,status,executor,input_hash,input_artifacts,started_at,finished_at,result,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("att-1", "job-1", 1, "succeeded", "simulated", "a" * 64, input_artifacts, "2026-11-17T10:00:00Z", "2026-11-17T10:01:00Z", result_attempt, None),
        )
        db.execute(
            "INSERT INTO artifacts(id,workflow_id,revision,stage,job_id,attempt_id,schema_version,content,content_hash,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("art-1", "wf-1", 1, "proposal", "job-1", "att-1", 1, content_artifact, "b" * 64, "2026-11-17T10:01:00Z"),
        )
        db.execute("UPDATE workflows SET current_artifact_id='art-1' WHERE id='wf-1'")
        db.execute(
            "INSERT INTO approvals(id,workflow_id,revision,artifact_id,decision,feedback,operator_id,scope,idempotency_key,request,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("app-1", "wf-1", 1, "art-1", "approve", "aprovado", "op-1", "proposal", "app-key-1", req_json, "2026-11-17T10:02:00Z"),
        )
        db.execute(
            "INSERT INTO cognitive_runs(id,job_id,attempt_id,provider,idempotency_key,request,request_hash,external_ref,status,remote_status,usage,error,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("run-1", "job-1", "att-1", "hermes", "run-key-1", req_json, "c" * 64, "ext-1", "completed", "done", '{"tokens":42}', None, "2026-11-17T10:00:10Z", "2026-11-17T10:00:50Z"),
        )
        db.execute(
            "INSERT INTO authorizations(id,workflow_id,revision,job_id,input_artifact_id,scope,max_runs,operator_id,idempotency_key,request,consumed_attempt_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("auth-1", "wf-1", 1, "job-1", "art-1", "cognitive_run", 1, "op-1", "auth-key-1", req_json, "att-1", "2026-11-17T10:00:05Z"),
        )
        if target_version >= 10:
            db.execute(
                "INSERT INTO scripts(id,workflow_id,workflow_revision,proposal_artifact_id,proposal_content_hash,version,status,document,created_at,review,origin,generator) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("scr-1", "wf-1", 1, "art-1", "b" * 64, 1, "approved", doc_script, "2026-11-17T10:03:00Z", review_script, "human", None),
            )
        else:
            db.execute(
                "INSERT INTO scripts(id,workflow_id,workflow_revision,proposal_artifact_id,proposal_content_hash,version,status,document,created_at,review,origin) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("scr-1", "wf-1", 1, "art-1", "b" * 64, 1, "approved", doc_script, "2026-11-17T10:03:00Z", review_script, "human"),
            )
        db.execute(
            "INSERT INTO script_receipts(scope,idempotency_key,request,response) VALUES (?,?,?,?)",
            ("review", "sr-key-1", req_json, resp_receipt),
        )
        db.execute(
            "INSERT INTO productions(id,script_id,script_version,script_content_hash,script_review,job_id,mode,plan,artifacts,review,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("prod-1", "scr-1", 1, "d" * 64, review_script, "job-2", "local_pilot", plan_prod, artifacts_prod, review_prod, "2026-11-17T10:04:00Z"),
        )
        db.execute(
            "INSERT INTO production_receipts(scope,idempotency_key,request,production_id) VALUES (?,?,?,?)",
            ("create", "pr-key-1", req_json, "prod-1"),
        )
        db.execute(
            "INSERT INTO license_reviews(seq,id,production_id,asset_id,asset_sha256,decision,license,evidence,reviewed_by,reviewed_at,supersedes,idempotency_key,request) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (LARGE_SEQ_BASE + 1, "lr-1", "prod-1", "asset-1", "e" * 64, "rejected", "CC0", "evidencia inicial", "human", "2026-11-17T10:04:10Z", None, "lr-key-1", req_json),
        )
        db.execute(
            "INSERT INTO license_reviews(seq,id,production_id,asset_id,asset_sha256,decision,license,evidence,reviewed_by,reviewed_at,supersedes,idempotency_key,request) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (LARGE_SEQ_BASE + 2, "lr-2", "prod-1", "asset-1", "e" * 64, "verified", "CC0", "evidencia revisada", "human", "2026-11-17T10:04:20Z", "lr-1", "lr-key-2", req_json),
        )
        db.execute(
            "INSERT INTO events(sequence,job_id,kind,message,created_at) VALUES (?,?,?,?,?)",
            (LARGE_SEQ_BASE + 10, "job-1", "job.succeeded", "Concluído", "2026-11-17T10:01:00Z"),
        )
        db.execute(
            "INSERT INTO workflow_events(sequence,workflow_id,revision,kind,data,created_at) VALUES (?,?,?,?,?,?)",
            (LARGE_SEQ_BASE + 20, "wf-1", 1, "workflow.approved", '{"ok":true}', "2026-11-17T10:02:00Z"),
        )
        if target_version >= 11:
            db.execute(
                "INSERT INTO transmissions(seq,id,at,kind,stage,workflow_id,job_id,attempt_id,run_id,destination,provider,model,envelope_bytes,envelope_sha256,authorization_id,result,detail,tokens_total,cost_usd) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (LARGE_SEQ_BASE + 30, "tx-1", "2026-11-17T10:00:15Z", "submit", "proposal", "wf-1", "job-1", "att-1", "run-1", "hermes.local", "hermes", "model-x", 512, "f" * 64, "auth-1", "completed", "ok", 42, "0.0125"),
            )
        if target_version >= 12:
            db.execute(
                "INSERT INTO human_checkpoints(seq,id,action,subject,description,approved_by,operator_id,approved_at,expires_at,limit_amount,currency,consumed_at,consumed_ref,idempotency_key,request) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (LARGE_SEQ_BASE + 40, "hc-1", "publish", "1" * 64, "Publicação sintética", "human", "op-1", "2026-11-17T10:05:00Z", "2026-11-18T10:05:00Z", "25.50", "USD", None, None, "hc-key-1", req_checkpoint),
            )

        db.execute("PRAGMA foreign_keys=ON")
        db.commit()

    if include_radar:
        radar_dir = root / "radar"
        radar_dir.mkdir(parents=True, exist_ok=True)
        radar_path = radar_dir / "radar-youtube.sqlite3"
        with closing(sqlite3.connect(radar_path)) as rdb:
            rdb.execute("CREATE TABLE radar_cache(key TEXT PRIMARY KEY, fetched TEXT NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL)")
            rdb.execute("CREATE TABLE radar_usage(id TEXT PRIMARY KEY, at REAL NOT NULL, bucket TEXT NOT NULL)")
            rdb.execute("CREATE TABLE radar_blocks(bucket TEXT PRIMARY KEY, until REAL NOT NULL)")
            rdb.execute("CREATE TABLE radar_datasets(id TEXT NOT NULL, revision INTEGER NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(id,revision))")
            rdb.execute("INSERT INTO radar_cache VALUES (?,?,?,?)", ("q:1", "2026-11-17T10:00:00Z", 1763460000.0, '{"items":[]}'))
            rdb.execute("INSERT INTO radar_usage VALUES (?,?,?)", ("ru-1", 1763373600.0, "search"))
            rdb.execute("INSERT INTO radar_blocks VALUES (?,?)", ("search", 1763460000.0))
            rdb.execute("INSERT INTO radar_datasets VALUES (?,?,?,?)", ("ds-1", 1, 1763460000.0, '{"collected_at":"2026-11-17T10:00:00Z","rows":[]}'))
            rdb.commit()

    return root
