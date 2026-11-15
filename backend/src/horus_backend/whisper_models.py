"""Importação explícita de pesos locais; sem rede nem importação do motor ASR."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .file_access import client_path
from .speech_alignment import model_directory, model_file, resolve_model_path
from .transcriber import MODELS

ModelName = Literal['tiny', 'base', 'small', 'medium', 'large-v3-turbo']
REQUIRED = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')
OPTIONAL = ('README.md', 'model-card.md', 'LICENSE', 'LICENSE.txt', 'preprocessor_config.json', 'tokenizer_config.json')
MANIFEST = 'horus-model.json'
MIN_WEIGHTS = 1024 * 1024
MAX_WEIGHTS = 16 * 1024**3
MAX_METADATA = 16 * 1024**2


class ImportModelRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: ModelName
    source_directory: str = Field(min_length=1, max_length=4096)
    confirm_license: Literal[True]


class ModelLicense(BaseModel):
    identifier: Literal['MIT']
    status: Literal['local_declaration', 'declared_unverified']
    model_card_present: bool


class ModelSource(BaseModel):
    kind: Literal['local_directory']
    name: str


class InstalledModel(BaseModel):
    model: ModelName
    installed: bool
    managed: bool
    size_bytes: int = Field(ge=0)
    license: ModelLicense
    source: ModelSource | None
    removable: bool


class ModelCatalog(BaseModel):
    models: list[InstalledModel]


class RemovedModel(BaseModel):
    model: ModelName
    removed: Literal[True]


class ModelConflict(ValueError):
    pass


def _link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


class WhisperModels:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.root = self.data_dir / 'models'
        self.lock = threading.RLock()

    def _root(self):
        if _link(self.root) or self.root.resolve() != self.root:
            raise ModelConflict('A pasta de modelos foi substituída por um link; restaure o armazenamento local.')
        self.root.mkdir(parents=True, exist_ok=True)

    def _manifest(self, folder: Path, model: str) -> dict | None:
        path = folder / MANIFEST
        if not model_directory(folder) or not model_file(path) or path.lstat().st_size > MAX_METADATA:
            return None
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            files = value['files']
            if (value['schema_version'] != 1 or value['model'] != model or not isinstance(files, dict)
                    or not set(REQUIRED) <= set(files) or not set(files) <= set(REQUIRED + OPTIONAL)):
                return None
            if any(not model_file(folder / name) for name in files):
                return None
            if type(value['size_bytes']) is not int or value['size_bytes'] < 0 or not isinstance(value['license'], dict):
                return None
            if (not isinstance(value['source']['name'], str) or value['source']['kind'] != 'local_directory'
                    or value['license']['identifier'] != 'MIT'
                    or value['license']['status'] not in {'local_declaration', 'declared_unverified'}
                    or type(value['license']['model_card_present']) is not bool):
                return None
            return value
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return None

    def list(self) -> dict:
        with self.lock:
            self._root()
            models = []
            for model in MODELS:
                folder = resolve_model_path(self.root, model, required_files=REQUIRED)
                if folder and (_link(folder) or not folder.resolve().is_relative_to(self.root)):
                    folder = None
                record = self._manifest(folder, model) if folder else None
                models.append({'model': model, 'installed': folder is not None, 'managed': record is not None,
                    'size_bytes': record['size_bytes'] if record else sum((folder / n).lstat().st_size for n in REQUIRED) if folder else 0,
                    'license': record['license'] if record else {'identifier': 'MIT', 'status': 'declared_unverified', 'model_card_present': False},
                    'source': {'kind': 'local_directory', 'name': record['source']['name']} if record else None,
                    'removable': record is not None})
            return {'models': models}

    def _validate(self, source: Path) -> tuple[list[str], dict]:
        if not model_directory(source):
            raise ValueError('Escolha uma pasta local existente sem links com o modelo faster-whisper.')
        files = []
        for name in REQUIRED + OPTIONAL:
            path = source / name
            if _link(path):
                raise ValueError('Arquivos do modelo não podem ser links; escolha uma cópia local completa.')
            if not model_file(path) and not path.exists() and name in OPTIONAL:
                continue
            if not model_file(path):
                raise ValueError(f'Arquivo obrigatório ausente ou inválido: {name}.')
            size = path.lstat().st_size
            if name == 'model.bin':
                if not MIN_WEIGHTS <= size <= MAX_WEIGHTS:
                    raise ValueError('model.bin deve ter entre 1 MiB e 16 GiB; confira se os pesos foram baixados integralmente.')
            elif not 0 < size <= MAX_METADATA:
                raise ValueError(f'Arquivo vazio ou maior que 16 MiB: {name}.')
            if name.endswith('.json'):
                try:
                    value = json.loads(path.read_text(encoding='utf-8'))
                    if not isinstance(value, dict):
                        raise ValueError()
                except (ValueError, RecursionError):
                    raise ValueError(f'JSON do modelo inválido: {name}.') from None
            files.append(name)
        cards = [name for name in ('README.md', 'model-card.md') if name in files]
        declared = False
        for name in cards:
            text = (source / name).read_text(encoding='utf-8-sig')
            front = re.match(r'\A---\s*\n(.*?)\n---', text, re.S)
            match = re.search(r'^license\s*:\s*(.*?)\s*$', front[1], re.M | re.I) if front else None
            if match:
                license_id = match[1].strip().strip('\"\'').lower()
                if license_id != 'mit':
                    raise ValueError('O model card declara licença diferente de MIT. A importação foi recusada; revise a origem e a licença.')
                declared = True
        for name in ('LICENSE', 'LICENSE.txt'):
            if name in files:
                text = (source / name).read_text(encoding='utf-8-sig').lower()
                if 'mit license' not in text and 'permission is hereby granted, free of charge' not in text:
                    raise ValueError('A licença local não confirma MIT. A importação foi recusada; revise a licença da distribuição.')
                declared = True
        return files, {'identifier': 'MIT', 'status': 'local_declaration' if declared else 'declared_unverified',
                       'model_card_present': bool(cards)}

    def install(self, request: ImportModelRequest) -> dict:
        with self.lock:
            source = client_path(request.source_directory)
            if not model_directory(Path(request.source_directory).expanduser()):
                raise ValueError('A pasta de origem não pode ser um link; selecione uma pasta local completa.')
            self._root()
            target = self.root / request.model
            if target.exists() or _link(target) or resolve_model_path(self.root, request.model) is not None:
                raise ModelConflict('Este modelo já está instalado. Remova explicitamente a instalação gerenciada antes de substituí-la.')
            files, license_info = self._validate(source)
            stage = Path(tempfile.mkdtemp(prefix='.import-', dir=self.root))
            try:
                hashes = {}
                total = 0
                for name in files:
                    origin = source / name
                    before = origin.lstat()
                    maximum = MAX_WEIGHTS if name == 'model.bin' else MAX_METADATA
                    if not model_file(origin) or before.st_size > maximum:
                        raise ValueError('A origem mudou durante a importação; tente novamente com uma cópia estável.')
                    digest = hashlib.sha256()
                    copied = 0
                    with origin.open('rb') as reader, (stage / name).open('xb') as writer:
                        while chunk := reader.read(1024 * 1024):
                            copied += len(chunk)
                            if copied > before.st_size:
                                raise ValueError('A origem mudou durante a importação; tente novamente com uma cópia estável.')
                            writer.write(chunk)
                            digest.update(chunk)
                        writer.flush()
                        os.fsync(writer.fileno())
                    after = origin.lstat()
                    if copied != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                        raise ValueError('A origem mudou durante a importação; tente novamente com uma cópia estável.')
                    hashes[name] = {'sha256': digest.hexdigest(), 'size_bytes': copied}
                    total += copied
                # Valida também a cópia: a licença deve corresponder aos bytes publicados.
                _, copied_license = self._validate(stage)
                if copied_license != license_info:
                    raise ValueError('A licença mudou durante a cópia; revise a origem antes de tentar novamente.')
                record = {'schema_version': 1, 'model': request.model, 'size_bytes': total, 'files': hashes,
                    'license': copied_license, 'source': {'kind': 'local_directory', 'name': source.name, 'directory': str(source)},
                    'imported_at': datetime.now(timezone.utc).isoformat()}
                with (stage / MANIFEST).open('x', encoding='utf-8') as writer:
                    json.dump(record, writer, ensure_ascii=False, indent=2)
                    writer.flush()
                    os.fsync(writer.fileno())
                self._root()
                if target.exists() or _link(target):
                    raise ModelConflict('O destino mudou durante a cópia; nenhuma instalação foi sobrescrita.')
                stage.rename(target)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
            return next(item for item in self.list()['models'] if item['model'] == request.model)

    def remove(self, model: ModelName) -> dict:
        with self.lock:
            if model not in MODELS:
                raise ValueError('Modelo desconhecido.')
            self._root()
            target = self.root / model
            record = self._manifest(target, model)
            if record is None:
                raise ModelConflict('Somente modelos importados e gerenciados pelo Hórus podem ser removidos por esta ação.')
            owned = set(record['files']) | {MANIFEST}
            if {p.name for p in target.iterdir()} != owned:
                raise ModelConflict('A instalação contém arquivos adicionais; preserve-os antes de remover o modelo.')
            # Caminho fixo sob models; nunca remove a origem nem diretórios de legado.
            for name in owned:
                path = target / name
                if not model_file(path):
                    raise ModelConflict('A instalação mudou; a remoção foi recusada para preservar os arquivos.')
            for name in owned:
                (target / name).unlink()
            target.rmdir()
            return {'model': model, 'removed': True}
