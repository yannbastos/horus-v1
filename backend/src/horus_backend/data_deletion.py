"""Exclusão segura de dados LOCAIS com auditoria PostgreSQL encadeada (HOR-117 / HOR-164 I8).

Escopos desta fatia: `production` (pasta da produção, exports e comentários) e `import`
(pasta da importação). O banco PostgreSQL operacional **não é alterado** (preserva trilha,
licenças append-only e chaves estrangeiras); apenas os arquivos locais são movidos para
quarentena recuperável.

Fluxo:
- `preview`: somente leitura em disco + PostgreSQL, gera plano e token de uso único;
- `execute`: valida frase/token/plano, gera e verifica pacote v2 pré-exclusão, grava
  intenção (`started`) em `horus.deletion_events` ANTES de mover arquivos, move para
  `<data_dir>/.quarentena/<audit_id>/` e grava desfecho (`completed`/`failed`) DEPOIS;
- `reconcile`: se a rede/PG falhar após o movimento em disco, reconcilia o estado
  `incomplete` comparando os resumos SHA-256 gravados em `started`;
- `restore`: devolve da quarentena conferindo os resumos e audita `restored`.

Nota sobre retenção PostgreSQL: devido a MVCC, WAL e backups externos do provedor,
este módulo NÃO realiza nem promete `secure_delete` ou zeragem física imediata em disco.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Literal
import uuid

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .local_data_export import export_local_data, verify_export
from .storage import DatabaseUnavailable, Store, canonical_json_bytes
from .workflows import WorkflowConflict

QUARANTINE = ".quarentena"
PLAN_TTL_SECONDS = 300
MAX_PHRASE_ATTEMPTS = 5
MAX_PLANS = 50
ID = re.compile(r"^[a-f0-9]{32}$")
ACTIVE_JOBS = ("queued", "running", "cancel_requested")
GENESIS = "0" * 64
LABEL = {"production": "producao", "import": "importacao"}
DELETION_ADVISORY_KEY = 3  # pg_advisory_xact_lock(164, 3) reservado em 20261117160200_horus_security.sql


class DeletionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["production", "import"]
    id: str = Field(pattern=r"^[a-f0-9]{32}$")


class DeletionExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    intent_token: str = Field(min_length=20, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    phrase: str = Field(min_length=1, max_length=120)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


# --- auditoria PostgreSQL -------------------------------------------------------------------------------------------


class DeletionAudit:
    """Registro append-only encadeado por hash em `horus.deletion_events`."""

    def __init__(
        self,
        target: Store | Path | None = None,
        *,
        store: Store | None = None,
        data_dir: Path | None = None,
        device_id: str = "local",
    ):
        if hasattr(target, "uow"):
            self.store: Store | None = target
            self.data_dir: Path | None = Path(data_dir) if data_dir is not None else None
        else:
            self.store = store
            self.data_dir = Path(target) if target is not None else (Path(data_dir) if data_dir is not None else None)
        self.device_id = device_id
        self.lock = threading.RLock()

    def _require_store(self) -> Store:
        if self.store is None:
            raise DatabaseUnavailable("database_unavailable")
        return self.store

    @staticmethod
    def event_hash(event: dict) -> str:
        payload = {k: v for k, v in event.items() if k not in {"hash", "seq"}}
        return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()

    @classmethod
    def _chain_ok(cls, rows: list[dict]) -> bool:
        previous = GENESIS
        for row in rows:
            raw_canonical = bytes(row["payload_canonical"])
            expected_hash = hashlib.sha256(raw_canonical).hexdigest()
            if row["prev_hash"] != previous or row["hash"] != expected_hash:
                return False
            previous = row["hash"]
        return True

    def events(self) -> tuple[list[dict], bool]:
        store = self._require_store()
        with store.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT seq, id, audit_id, action, target, device_id, created_at,
                           payload, payload_canonical, prev_hash, hash
                    FROM horus.deletion_events
                    ORDER BY seq ASC
                    """
                )
                rows = cur.fetchall()
        chain_ok = self._chain_ok(rows)
        result: list[dict] = []
        for row in rows:
            payload = dict(row["payload"] or {})
            payload["seq"] = int(row["seq"])
            payload["id"] = row["id"]
            payload["audit_id"] = row["audit_id"]
            payload["action"] = row["action"]
            payload["prev_hash"] = row["prev_hash"]
            payload["hash"] = row["hash"]
            if "at" not in payload and row["created_at"] is not None:
                dt = row["created_at"]
                payload["at"] = dt.astimezone(timezone.utc).isoformat() if isinstance(dt, datetime) else str(dt)
            result.append(payload)
        return result, chain_ok

    def append(self, *, action: str, audit_id: str, scope: str, target_id: str, **fields) -> dict:
        store = self._require_store()
        with self.lock:
            with store.uow() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(164, %s)", (DELETION_ADVISORY_KEY,))
                prev_row = conn.execute(
                    "SELECT hash FROM horus.deletion_events ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                prev_hash = prev_row[0] if prev_row else GENESIS
                event_id = uuid.uuid4().hex
                created_at = datetime.now(timezone.utc)
                at_iso = created_at.isoformat()
                payload = {
                    "id": event_id,
                    "audit_id": audit_id,
                    "action": action,
                    "scope": scope,
                    "target_id": target_id,
                    "device_id": self.device_id,
                    "at": at_iso,
                    "prev_hash": prev_hash,
                    **fields,
                }
                payload_canonical = canonical_json_bytes(payload)
                event_hash = hashlib.sha256(payload_canonical).hexdigest()
                seq = conn.execute(
                    """
                    INSERT INTO horus.deletion_events (
                        id, audit_id, action, target, device_id, created_at,
                        payload, payload_canonical, prev_hash, hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING seq
                    """,
                    (
                        event_id,
                        audit_id,
                        action,
                        f"{scope}:{target_id}",
                        self.device_id,
                        created_at,
                        Jsonb(payload),
                        payload_canonical,
                        prev_hash,
                        event_hash,
                    ),
                ).fetchone()[0]
            return {**payload, "seq": int(seq), "hash": event_hash}

    def state(self, scope: str, target_id: str) -> dict | None:
        """Estado derivado da auditoria: último ciclo do alvo (`deleted`, `incomplete` ou None)."""
        events, _ok = self.events()
        last: dict | None = None
        for event in events:
            if event.get("scope") != scope or event.get("target_id") != target_id:
                continue
            if event["action"] == "started":
                last = {"state": "incomplete", "audit_id": event["audit_id"], "at": event["at"]}
            elif last and event.get("audit_id") == last["audit_id"]:
                if event["action"] == "completed":
                    last = {"state": "deleted", "audit_id": event["audit_id"], "at": event["at"]}
                elif event["action"] in ("failed", "restored"):
                    last = None
        return last

    def local_data(self, scope: str, target_id: str) -> dict:
        state = self.state(scope, target_id)
        if state is None:
            return {"state": "present", "audit_id": None, "changed_at": None}
        return {"state": state["state"], "audit_id": state["audit_id"], "changed_at": state["at"]}


# --- medição --------------------------------------------------------------------------------------------------------


def measure(path: Path) -> dict | None:
    """None se não existe; senão {files, bytes, digest, links}. Nunca segue links."""
    if not os.path.lexists(path):
        return None
    if _is_link(path):
        return {"files": 0, "bytes": 0, "digest": "", "links": 1}
    if path.is_file():
        size = path.stat().st_size
        return {"files": 1, "bytes": size, "digest": hashlib.sha256(f"{path.name}\0{size}".encode()).hexdigest(), "links": 0}
    lines, files, total, links = [], 0, 0, 0
    for folder, dirs, names in os.walk(path, followlinks=False):
        here = Path(folder)
        for name in list(dirs):
            if _is_link(here / name):
                links += 1
                dirs.remove(name)
        for name in names:
            item = here / name
            if _is_link(item):
                links += 1
                continue
            size = item.stat().st_size
            lines.append(f"{item.relative_to(path).as_posix()}\0{size}")
            files += 1
            total += size
    lines.sort()
    return {"files": files, "bytes": total, "digest": hashlib.sha256("\n".join(lines).encode()).hexdigest(), "links": links}


# --- serviço --------------------------------------------------------------------------------------------------------


class DataDeletionService:
    def __init__(self, manager):
        self.manager = manager
        self.settings = manager.settings
        self.productions = manager.productions
        self.store: Store | None = getattr(manager, "store", None)
        self.audit: DeletionAudit = manager.deletion_audit
        if self.audit.store is None and self.store is not None:
            self.audit.store = self.store
        self.data_dir = Path(self.settings.data_dir)
        self.plans: dict[str, dict] = {}
        self.lock = threading.RLock()

    def _require_store(self) -> Store:
        if self.store is None:
            raise DatabaseUnavailable("database_unavailable")
        return self.store

    def _targets(self, scope: str, target_id: str) -> list[tuple[str, str]]:
        if scope == "production":
            return [
                ("production_folder", f"productions/{target_id}"),
                ("exports", f"exports/{target_id}"),
                ("comments", f"production-comments/{target_id}.json"),
            ]
        return [("import_folder", f"imports/{target_id}")]

    def _active_jobs(self) -> int:
        store = self._require_store()
        with store.uow(readonly=True) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM horus.jobs WHERE status = ANY(%s)",
                (list(ACTIVE_JOBS),),
            ).fetchone()[0]
        return int(count) + (1 if self.manager.active_id else 0)

    def _db_kept(self, production_id: str) -> tuple[list[dict], list[dict]]:
        store = self._require_store()
        with store.uow(readonly=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT script_id, script_version, job_id FROM horus.productions WHERE id = %s",
                    (production_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError(production_id)

                def count(query: str, param: str) -> int:
                    cur.execute(query, (param,))
                    return int(cur.fetchone()["cnt"])

                kept = [
                    {"table": "productions", "rows": 1, "reason": "preserva a trilha; o banco PostgreSQL não é alterado nesta exclusão"},
                    {"table": "jobs", "rows": 1, "reason": "referenciado pela produção"},
                    {"table": "events", "rows": count("SELECT COUNT(*) AS cnt FROM horus.events WHERE job_id = %s", row["job_id"]), "reason": "histórico do job"},
                    {"table": "job_attempts", "rows": count("SELECT COUNT(*) AS cnt FROM horus.job_attempts WHERE job_id = %s", row["job_id"]), "reason": "histórico do job"},
                    {"table": "production_receipts", "rows": count("SELECT COUNT(*) AS cnt FROM horus.production_receipts WHERE production_id = %s", production_id), "reason": "idempotência"},
                    {"table": "license_reviews", "rows": count("SELECT COUNT(*) AS cnt FROM horus.license_reviews WHERE production_id = %s", production_id), "reason": "append-only: não pode ser apagado nem alterado"},
                ]
                cur.execute("SELECT workflow_id FROM horus.scripts WHERE id = %s", (row["script_id"],))
                script = cur.fetchone()

        dependencies = [
            {
                "kind": "script",
                "id": row["script_id"],
                "version": row["script_version"],
                "note": "roteiro aprovado usado pela produção; não é apagado e pode servir a outras produções",
            }
        ]
        if script:
            dependencies.append({"kind": "workflow", "id": script["workflow_id"], "note": "origem do roteiro; não é apagado"})
        return [k for k in kept if k["rows"]], dependencies

    # prévia ---------------------------------------------------------------------------------------------------------

    def preview(self, request: DeletionPreviewRequest) -> dict:
        scope, target_id = request.scope, request.id
        kept, dependencies = [], []
        if scope == "production":
            kept, dependencies = self._db_kept(target_id)
        elif not (self.data_dir / "imports" / target_id).is_dir():
            raise KeyError(target_id)
        items, blockers, digests = [], [], {}
        for kind, relative in self._targets(scope, target_id):
            info = measure(self.data_dir / relative)
            items.append(
                {
                    "kind": kind,
                    "path": relative,
                    "exists": info is not None,
                    "files": info["files"] if info else 0,
                    "bytes": info["bytes"] if info else 0,
                }
            )
            if info:
                digests[relative] = info["digest"]
                if info["links"]:
                    blockers.append({"code": "links", "message": f"{relative} contém link simbólico ou junção; recusado."})
        state = self.audit.state(scope, target_id)
        if state:
            blockers.append(
                {
                    "code": "already_deleted" if state["state"] == "deleted" else "incomplete_deletion",
                    "message": "Os dados locais deste alvo já foram excluídos ou há uma exclusão incompleta; veja a auditoria.",
                }
            )
        if not digests:
            blockers.append({"code": "nothing_to_delete", "message": "Não há arquivos locais para excluir."})
        active = self._active_jobs()
        if active:
            blockers.append({"code": "active_jobs", "message": f"Há {active} tarefa(s) ativa(s); aguarde terminar ou cancele antes de excluir."})
        if not self.audit.events()[1]:
            blockers.append({"code": "audit_unreadable", "message": "O registro de auditoria está ilegível ou adulterado."})
        phrase = f"APAGAR {LABEL[scope]} {target_id[:8]}"
        plan_id, token = uuid.uuid4().hex, secrets.token_urlsafe(24)
        with self.lock:
            now = time.time()
            self.plans = {k: v for k, v in self.plans.items() if v["expires"] > now}
            while len(self.plans) >= MAX_PLANS:
                self.plans.pop(next(iter(self.plans)))
            self.plans[plan_id] = {
                "scope": scope,
                "id": target_id,
                "digests": digests,
                "phrase": phrase,
                "attempts": 0,
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "expires": now + PLAN_TTL_SECONDS,
            }
        return {
            "plan_id": plan_id,
            "intent_token": token,
            "expires_in_seconds": PLAN_TTL_SECONDS,
            "scope": scope,
            "id": target_id,
            "confirmation_phrase": phrase,
            "items": items,
            "totals": {"files": sum(i["files"] for i in items), "bytes": sum(i["bytes"] for i in items)},
            "db_kept": kept,
            "dependencies": dependencies,
            "blockers": blockers,
            "can_execute": not blockers,
            "notice": (
                "Os arquivos locais vão para a quarentena (recuperáveis); o pacote de pré-exclusão cobre "
                "dump lógico PostgreSQL e manifestos, não vídeos. O banco PostgreSQL preserva a trilha "
                "operacional; devido a MVCC, WAL e backups do provedor, não há zeragem física imediata."
            ),
        }

    # execução -------------------------------------------------------------------------------------------------------

    def _backup(self, audit_id: str) -> dict:
        store = self._require_store()
        folder = self.data_dir.parent / f"{self.data_dir.name}-backups"
        package = folder / f"pre-exclusao-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{audit_id[:8]}.zip"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            report = export_local_data(self.data_dir, package, store=store)
        except DatabaseUnavailable:
            raise
        except (OSError, ValueError):
            raise WorkflowConflict("Não foi possível gerar o pacote local de pré-exclusão; nada foi excluído.") from None
        check = verify_export(package)
        if not check["ok"]:
            raise WorkflowConflict(f"O pacote de pré-exclusão não passou na verificação ({package.name}); nada foi excluído.")
        return {
            "name": package.name,
            "sha256": report["sha256"],
            "bytes": package.stat().st_size,
            "verified": True,
            "location": f"{self.data_dir.name}-backups",
        }

    def execute(self, request: DeletionExecuteRequest) -> dict:
        with self.lock:
            plan = self.plans.get(request.plan_id)
            if plan is None or plan["expires"] < time.time():
                self.plans.pop(request.plan_id, None)
                raise WorkflowConflict("Prévia inexistente, expirada ou já utilizada; gere uma nova prévia.")
            token_ok = secrets.compare_digest(plan["token_hash"], hashlib.sha256(request.intent_token.encode()).hexdigest())
            plan["attempts"] += 1
            if not token_ok or not secrets.compare_digest(plan["phrase"].encode(), request.phrase.encode()):
                if plan["attempts"] >= MAX_PHRASE_ATTEMPTS:
                    self.plans.pop(request.plan_id, None)
                raise WorkflowConflict("Token ou frase de confirmação incorretos; nada foi excluído.")
            self.plans.pop(request.plan_id)
            return self._execute(plan)

    def _execute(self, plan: dict) -> dict:
        scope, target_id = plan["scope"], plan["id"]
        if self.audit.state(scope, target_id):
            raise WorkflowConflict("Os dados locais deste alvo já foram excluídos; veja a auditoria.")
        if self._active_jobs():
            raise WorkflowConflict("Há tarefa ativa; aguarde terminar antes de excluir.")
        current = {}
        for _kind, relative in self._targets(scope, target_id):
            info = measure(self.data_dir / relative)
            if info:
                if info["links"]:
                    raise WorkflowConflict("Há link simbólico ou junção no alvo; recusado.")
                current[relative] = info
        if {k: v["digest"] for k, v in current.items()} != plan["digests"]:
            raise WorkflowConflict("Os arquivos mudaram desde a prévia; gere uma nova prévia.")
        audit_id = uuid.uuid4().hex
        backup = self._backup(audit_id)
        if self._active_jobs():
            raise WorkflowConflict("Uma tarefa foi iniciada durante o backup; nada foi excluído. O pacote ficou guardado.")
        items = [{"path": rel, "files": v["files"], "bytes": v["bytes"], "digest": v["digest"]} for rel, v in current.items()]
        # Intenção auditada em PG ANTES de mover qualquer arquivo em disco.
        self.audit.append(action="started", audit_id=audit_id, scope=scope, target_id=target_id, items=items, backup=backup)
        quarantine = self.data_dir / QUARANTINE / audit_id
        moved: list[tuple[Path, Path]] = []
        try:
            for item in items:
                source, destination = self.data_dir / item["path"], quarantine / item["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.rename(source, destination)
                moved.append((source, destination))
        except BaseException as error:
            for source, destination in reversed(moved):
                try:
                    os.rename(destination, source)
                except OSError:
                    pass
            self.audit.append(
                action="failed",
                audit_id=audit_id,
                scope=scope,
                target_id=target_id,
                error=type(error).__name__,
                restored_all=all(not d.exists() for _, d in moved),
            )
            raise WorkflowConflict("Não foi possível mover os arquivos para a quarentena; nada foi excluído.") from error
        # Resultado auditado em PG DEPOIS de mover. Se a rede cair aqui, o estado fica `incomplete` e reconciliável.
        self.audit.append(action="completed", audit_id=audit_id, scope=scope, target_id=target_id)
        return {
            "audit_id": audit_id,
            "scope": scope,
            "id": target_id,
            "items": items,
            "backup": backup,
            "quarantine": f"{QUARANTINE}/{audit_id}",
            "recoverable": True,
            "totals": {"files": sum(i["files"] for i in items), "bytes": sum(i["bytes"] for i in items)},
        }

    # reconciliação, restauração e leitura ---------------------------------------------------------------------------

    def reconcile(self, audit_id: str) -> dict:
        """Reconcilia exclusão `incomplete` comparando resumos SHA-256 em disco/quarentena com `started`."""
        if not ID.fullmatch(audit_id):
            raise KeyError(audit_id)
        with self.lock:
            events, ok = self.audit.events()
            started = next((e for e in events if e["action"] == "started" and e["audit_id"] == audit_id), None)
            if started is None or not ok:
                raise KeyError(audit_id)
            actions = {e["action"] for e in events if e.get("audit_id") == audit_id}
            if actions & {"completed", "failed", "restored"}:
                raise WorkflowConflict("Esta exclusão já foi finalizada; reconciliação não é necessária.")

            quarantine = self.data_dir / QUARANTINE / audit_id
            all_in_quarantine = True
            all_at_origin = True
            for item in started["items"]:
                orig = measure(self.data_dir / item["path"])
                quar = measure(quarantine / item["path"])
                if not (quar and not quar["links"] and quar["digest"] == item["digest"] and orig is None):
                    all_in_quarantine = False
                if not (orig and not orig["links"] and orig["digest"] == item["digest"] and quar is None):
                    all_at_origin = False

            if all_in_quarantine:
                self.audit.append(
                    action="completed",
                    audit_id=audit_id,
                    scope=started["scope"],
                    target_id=started["target_id"],
                    reconciled=True,
                )
                return {
                    "audit_id": audit_id,
                    "scope": started["scope"],
                    "id": started["target_id"],
                    "outcome": "completed",
                    "state": "deleted",
                }
            if all_at_origin:
                self.audit.append(
                    action="failed",
                    audit_id=audit_id,
                    scope=started["scope"],
                    target_id=started["target_id"],
                    reconciled=True,
                    restored_all=True,
                )
                return {
                    "audit_id": audit_id,
                    "scope": started["scope"],
                    "id": started["target_id"],
                    "outcome": "failed",
                    "state": "present",
                }
            raise WorkflowConflict("Estado em disco diverge dos hashes registrados; reconciliação automática recusada.")

    def restore(self, audit_id: str) -> dict:
        if not ID.fullmatch(audit_id):
            raise KeyError(audit_id)
        with self.lock:
            events, ok = self.audit.events()
            started = next((e for e in events if e["action"] == "started" and e["audit_id"] == audit_id), None)
            if started is None or not ok:
                raise KeyError(audit_id)
            done = [e["action"] for e in events if e.get("audit_id") == audit_id]
            if "restored" in done or "failed" in done:
                raise WorkflowConflict("Esta exclusão já foi restaurada ou nunca foi concluída.")
            if self._active_jobs():
                raise WorkflowConflict("Há tarefa ativa; aguarde antes de restaurar.")
            quarantine = self.data_dir / QUARANTINE / audit_id
            plan = []
            for item in started["items"]:
                source, destination = quarantine / item["path"], self.data_dir / item["path"]
                info = measure(source)
                if info is None or info["links"] or info["digest"] != item["digest"]:
                    raise WorkflowConflict("A quarentena foi alterada ou está incompleta; nada foi restaurado.")
                if os.path.lexists(destination):
                    raise WorkflowConflict("Já existe um arquivo no destino; nada foi restaurado.")
                plan.append((source, destination))
            moved = []
            try:
                for source, destination in plan:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(source, destination)
                    moved.append((source, destination))
            except BaseException as error:
                for source, destination in reversed(moved):
                    try:
                        os.rename(destination, source)
                    except OSError:
                        pass
                raise WorkflowConflict("Não foi possível restaurar; nada foi alterado.") from error
            self.audit.append(action="restored", audit_id=audit_id, scope=started["scope"], target_id=started["target_id"])
            return {
                "audit_id": audit_id,
                "scope": started["scope"],
                "id": started["target_id"],
                "restored": [i["path"] for i in started["items"]],
            }

    def page(self, limit: int = 50, before: int | None = None) -> dict:
        events, ok = self.audit.events()
        shown = [e for e in reversed(events) if before is None or e["seq"] < before][: max(1, min(limit, 200))]
        return {"chain_ok": ok, "items": shown, "next_before": shown[-1]["seq"] if len(shown) == limit and shown[-1]["seq"] > 1 else None}
