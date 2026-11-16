"""Backup pré-atualização no formato de exportação existente, sem retenção automática."""
from pathlib import Path
from uuid import uuid4

from .local_data_export import export_local_data, verify_export, _is_link, EXCLUDED_AREAS


def create_update_backup(data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    # Irmão do workspace: o exportador recusa destinos dentro da origem.
    folder = data_dir.parent / "update-backups"
    if _is_link(data_dir) or _is_link(folder):
        raise ValueError("Pasta de backup não pode ser link.")
    folder.mkdir(exist_ok=True)
    label = f"pre-update-{uuid4().hex}.zip"
    target = folder / label
    exported = export_local_data(data_dir, target)
    if any(item['path'] not in EXCLUDED_AREAS for item in exported["excluded"]):
        raise ValueError("Backup incompleto; preserve o pacote para inspeção e não atualize.")
    checked = verify_export(target)
    if not checked["ok"]:
        raise ValueError("Backup não verificado; não atualize.")
    return {"ok": True, "label": label, "sha256": exported["sha256"], "files": exported["files"]}
