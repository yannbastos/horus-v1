"""Backup pré-atualização PostgreSQL + arquivos locais permitidos (HOR-164 / I8).

Preserva o contrato `/v1/update/backup` e o rótulo seguro (`pre-update-<hex>.zip`),
exigindo exportação lógica PostgreSQL consistente e verificação completa.
Falha fechada: sem conexão PG (`DatabaseUnavailable` 503) ou com pacote/comprovante
incompleto ou inválido (`UpdateBackupBlocked`), a atualização é bloqueada.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .local_data_export import EXCLUDED_AREAS, _is_link, export_local_data, verify_export
from .storage import Store


class UpdateBackupBlocked(ValueError):
    """Bloqueia a atualização em falha fechada com código estável."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def create_update_backup(
    data_dir: Path,
    *,
    store: Store | None = None,
    remote_receipt: dict | None = None,
) -> dict:
    data_dir = Path(data_dir)
    # Irmão do workspace: o exportador recusa destinos dentro da origem.
    folder = data_dir.parent / "update-backups"
    if _is_link(data_dir) or _is_link(folder):
        raise UpdateBackupBlocked("Pasta de backup não pode ser link.", code="update_backup_link_refused")
    folder.mkdir(exist_ok=True)
    label = f"pre-update-{uuid4().hex}.zip"
    target = folder / label

    exported = export_local_data(data_dir, target, store=store)
    if any(item["path"] not in EXCLUDED_AREAS for item in exported["excluded"]):
        raise UpdateBackupBlocked(
            "Backup incompleto; preserve o pacote para inspeção e não atualize.",
            code="update_backup_incomplete",
        )
    checked = verify_export(target)
    if not checked["ok"]:
        raise UpdateBackupBlocked(
            "Backup não verificado; não atualize.",
            code="update_backup_verification_failed",
        )

    db_meta = exported.get("database") or {}
    if not db_meta.get("schema_version") or not db_meta.get("tables_count"):
        raise UpdateBackupBlocked(
            "Comprovante lógico PostgreSQL ausente ou incompleto; não atualize.",
            code="update_backup_remote_proof_missing",
        )

    if remote_receipt is not None:
        if (
            not isinstance(remote_receipt, dict)
            or not remote_receipt.get("verified_at")
            or (
                remote_receipt.get("instance_id") is not None
                and remote_receipt.get("instance_id") != db_meta.get("instance_id")
            )
        ):
            raise UpdateBackupBlocked(
                "Comprovante remoto incompatível com o banco operacional; não atualize.",
                code="update_backup_remote_proof_invalid",
            )

    remote_proof = {
        "instance_id": db_meta.get("instance_id"),
        "schema_version": db_meta.get("schema_version"),
        "tables_count": db_meta.get("tables_count"),
        "verified_at": (remote_receipt or {}).get("verified_at") or datetime.now(timezone.utc).isoformat(),
        "package_sha256": exported["sha256"],
    }
    return {
        "ok": True,
        "code": "update_backup_verified",
        "label": label,
        "sha256": exported["sha256"],
        "files": exported["files"],
        "remote_proof": remote_proof,
    }
