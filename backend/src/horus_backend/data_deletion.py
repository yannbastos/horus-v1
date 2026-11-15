"""Exclusão segura de dados LOCAIS (HOR-117): prévia exata, frase digitada, backup verificado, quarentena e auditoria.

Escopos desta fatia: `production` (pasta da produção, exports e comentários) e `import` (pasta da importação). O banco
operacional **não é alterado** (preserva trilha, licenças append-only e chaves estrangeiras); só arquivos mudam de lugar.
Fluxo: `preview` (somente leitura, gera plano e token de uso único) → `execute` (frase exata, nenhum job ativo, plano
inalterado, pacote local gerado e verificado ANTES, auditoria `started`, mover para `<data_dir>/.quarentena/<audit_id>/`,
auditoria `completed`) → `restore` (devolve da quarentena conferindo o resumo dos arquivos). A purga definitiva da
quarentena não faz parte desta fatia. Os pacotes de pré-exclusão ficam em `<data_dir>-backups/` e nunca são apagados
automaticamente. O pacote local (banco + manifestos JSON) NÃO contém vídeos/mídia/exports: quem preserva esses arquivos é a
quarentena.

Auditoria: `<data_dir>/deletion-audit.json`, JSON atômico, eventos append-only encadeados por hash (`prev_hash`/`hash`),
sem conteúdo de arquivos: só ids, caminhos relativos, contagens, tamanhos e resumos. Sem migração.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .local_data_export import export_local_data, verify_export
from .workflows import WorkflowConflict

SCHEMA = 1
AUDIT_FILE = "deletion-audit.json"
QUARANTINE = ".quarentena"
PLAN_TTL_SECONDS = 300
MAX_PHRASE_ATTEMPTS = 5
MAX_PLANS = 50
ID = re.compile(r"^[a-f0-9]{32}$")
ACTIVE_JOBS = ("queued", "running", "cancel_requested")
GENESIS = "0" * 64
LABEL = {"production": "producao", "import": "importacao"}


class DeletionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["production", "import"]
    id: str = Field(pattern=r"^[a-f0-9]{32}$")


class DeletionExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    intent_token: str = Field(min_length=20, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    phrase: str = Field(min_length=1, max_length=120)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


# --- auditoria ------------------------------------------------------------------------------------------------------

class DeletionAudit:
    """Registro append-only encadeado por hash. Leitura tolerante (nunca levanta); escrita recusa registro adulterado."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / AUDIT_FILE
        self.lock = threading.RLock()
        self._cache: tuple[tuple[int, int], list[dict], bool] | None = None

    def _read(self) -> tuple[list[dict], bool]:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return [], True
        key = (stat.st_mtime_ns, stat.st_size)
        if self._cache and self._cache[0] == key:
            return self._cache[1], self._cache[2]
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            events = document["events"]
            if document.get("schema_version") != SCHEMA or not isinstance(events, list):
                raise ValueError("estrutura inválida")
            ok = self._chain_ok(events)
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            events, ok = [], False
        self._cache = (key, events, ok)
        return events, ok

    @staticmethod
    def event_hash(event: dict) -> str:
        return hashlib.sha256(_canonical({k: v for k, v in event.items() if k != "hash"})).hexdigest()

    @classmethod
    def _chain_ok(cls, events: list) -> bool:
        previous = GENESIS
        for number, event in enumerate(events, 1):
            if (not isinstance(event, dict) or event.get("seq") != number or event.get("prev_hash") != previous
                    or event.get("hash") != cls.event_hash(event)):
                return False
            previous = event["hash"]
        return True

    def events(self) -> tuple[list[dict], bool]:
        with self.lock:
            return self._read()

    def append(self, **fields) -> dict:
        with self.lock:
            events, ok = self._read()
            if not ok:
                raise WorkflowConflict("O registro de auditoria das exclusões está ilegível ou adulterado; nada foi alterado.")
            event = {"seq": len(events) + 1, "id": uuid.uuid4().hex, "at": _now(), **fields,
                     "prev_hash": events[-1]["hash"] if events else GENESIS}
            event["hash"] = self.event_hash(event)
            document = {"schema_version": SCHEMA, "events": [*events, event]}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".deletion-audit-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(document, handle, ensure_ascii=False)
                for attempt in range(6):
                    try:
                        os.replace(temporary, self.path)
                        break
                    except PermissionError:
                        if attempt == 5:
                            raise
                        time.sleep(.02 * (attempt + 1))
            finally:
                Path(temporary).unlink(missing_ok=True)
            self._cache = None
            return event

    def state(self, scope: str, target_id: str) -> dict | None:
        """Estado derivado da auditoria: último ciclo do alvo (`deleted`, `incomplete` ou None)."""
        events, ok = self.events()
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
    """None se não existe; senão {files, bytes, digest, links}. Nunca segue links; o resumo cobre caminho relativo e tamanho."""
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
        self.manager, self.settings, self.productions = manager, manager.settings, manager.productions
        self.audit: DeletionAudit = manager.deletion_audit
        self.data_dir = Path(self.settings.data_dir)
        self.plans: dict[str, dict] = {}
        self.lock = threading.RLock()

    # alvos ----------------------------------------------------------------------------------------------------------

    def _targets(self, scope: str, target_id: str) -> list[tuple[str, str]]:
        if scope == "production":
            return [("production_folder", f"productions/{target_id}"), ("exports", f"exports/{target_id}"),
                    ("comments", f"production-comments/{target_id}.json")]
        return [("import_folder", f"imports/{target_id}")]

    def _active_jobs(self) -> int:
        with self.manager.store.connect() as db:
            count = db.execute(f"SELECT COUNT(*) FROM jobs WHERE status IN ({','.join('?' * len(ACTIVE_JOBS))})", ACTIVE_JOBS).fetchone()[0]
        return count + (1 if self.manager.active_id else 0)

    def _db_kept(self, production_id: str) -> tuple[list[dict], list[dict]]:
        with self.manager.store.connect() as db:
            row = db.execute("SELECT script_id, script_version, job_id FROM productions WHERE id=?", (production_id,)).fetchone()
            if row is None:
                raise KeyError(production_id)
            count = lambda sql, *a: db.execute(sql, a).fetchone()[0]
            kept = [{"table": "productions", "rows": 1, "reason": "preserva a trilha; o banco não é alterado nesta exclusão"},
                    {"table": "jobs", "rows": 1, "reason": "referenciado pela produção"},
                    {"table": "events", "rows": count("SELECT COUNT(*) FROM events WHERE job_id=?", row["job_id"]), "reason": "histórico do job"},
                    {"table": "job_attempts", "rows": count("SELECT COUNT(*) FROM job_attempts WHERE job_id=?", row["job_id"]), "reason": "histórico do job"},
                    {"table": "production_receipts", "rows": count("SELECT COUNT(*) FROM production_receipts WHERE production_id=?", production_id),
                     "reason": "idempotência"},
                    {"table": "license_reviews", "rows": count("SELECT COUNT(*) FROM license_reviews WHERE production_id=?", production_id),
                     "reason": "append-only: não pode ser apagado nem alterado"}]
            script = db.execute("SELECT workflow_id FROM scripts WHERE id=?", (row["script_id"],)).fetchone()
        dependencies = [{"kind": "script", "id": row["script_id"], "version": row["script_version"],
                         "note": "roteiro aprovado usado pela produção; não é apagado e pode servir a outras produções"}]
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
            items.append({"kind": kind, "path": relative, "exists": info is not None,
                          "files": info["files"] if info else 0, "bytes": info["bytes"] if info else 0})
            if info:
                digests[relative] = info["digest"]
                if info["links"]:
                    blockers.append({"code": "links", "message": f"{relative} contém link simbólico ou junção; recusado."})
        state = self.audit.state(scope, target_id)
        if state:
            blockers.append({"code": "already_deleted" if state["state"] == "deleted" else "incomplete_deletion",
                             "message": "Os dados locais deste alvo já foram excluídos ou há uma exclusão incompleta; veja a auditoria."})
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
            self.plans[plan_id] = {"scope": scope, "id": target_id, "digests": digests, "phrase": phrase, "attempts": 0,
                                   "token_hash": hashlib.sha256(token.encode()).hexdigest(), "expires": now + PLAN_TTL_SECONDS}
        return {"plan_id": plan_id, "intent_token": token, "expires_in_seconds": PLAN_TTL_SECONDS, "scope": scope, "id": target_id,
                "confirmation_phrase": phrase, "items": items,
                "totals": {"files": sum(i["files"] for i in items), "bytes": sum(i["bytes"] for i in items)},
                "db_kept": kept, "dependencies": dependencies, "blockers": blockers, "can_execute": not blockers,
                "notice": "Os arquivos vão para a quarentena (recuperáveis); o pacote local de pré-exclusão cobre banco e manifestos, não vídeos."}

    # execução -------------------------------------------------------------------------------------------------------

    def _backup(self, audit_id: str) -> dict:
        folder = self.data_dir.parent / f"{self.data_dir.name}-backups"
        package = folder / f"pre-exclusao-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{audit_id[:8]}.zip"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            report = export_local_data(self.data_dir, package)
        except (OSError, ValueError, sqlite3.Error):
            raise WorkflowConflict("Não foi possível gerar o pacote local de pré-exclusão; nada foi excluído.") from None
        check = verify_export(package)
        if not check["ok"]:
            raise WorkflowConflict(f"O pacote de pré-exclusão não passou na verificação ({package.name}); nada foi excluído.")
        return {"name": package.name, "sha256": report["sha256"], "bytes": package.stat().st_size, "verified": True,
                "location": f"{self.data_dir.name}-backups"}

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
            self.plans.pop(request.plan_id)            # uso único
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
        if self._active_jobs():                        # nova checagem depois do backup (pode ter demorado)
            raise WorkflowConflict("Uma tarefa foi iniciada durante o backup; nada foi excluído. O pacote ficou guardado.")
        items = [{"path": rel, "files": v["files"], "bytes": v["bytes"], "digest": v["digest"]} for rel, v in current.items()]
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
            for source, destination in reversed(moved):         # desfaz: nada parcial
                try:
                    os.rename(destination, source)
                except OSError:
                    pass
            self.audit.append(action="failed", audit_id=audit_id, scope=scope, target_id=target_id, error=type(error).__name__,
                              restored_all=all(not d.exists() for _, d in moved))
            raise WorkflowConflict("Não foi possível mover os arquivos para a quarentena; nada foi excluído.") from error
        self.audit.append(action="completed", audit_id=audit_id, scope=scope, target_id=target_id)
        return {"audit_id": audit_id, "scope": scope, "id": target_id, "items": items, "backup": backup,
                "quarantine": f"{QUARANTINE}/{audit_id}", "recoverable": True,
                "totals": {"files": sum(i["files"] for i in items), "bytes": sum(i["bytes"] for i in items)}}

    # restauração e leitura -----------------------------------------------------------------------------------------

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
            return {"audit_id": audit_id, "scope": started["scope"], "id": started["target_id"], "restored": [i["path"] for i in started["items"]]}

    def page(self, limit: int = 50, before: int | None = None) -> dict:
        events, ok = self.audit.events()
        shown = [e for e in reversed(events) if before is None or e["seq"] < before][:max(1, min(limit, 200))]
        return {"chain_ok": ok, "items": shown, "next_before": shown[-1]["seq"] if len(shown) == limit and shown[-1]["seq"] > 1 else None}
