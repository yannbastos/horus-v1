"""Download explícito com catálogo fixado; nenhum acesso de rede ao importar."""
from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import hashlib
import http.client
from pathlib import Path
import shutil
import socket
import ssl
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict
from typing import Literal

from .whisper_models import WhisperModels, ImportModelRequest
from . import egress

from .whisper_download_catalog import CATALOG
# Hosts exatos usados pelo Hub e armazenamento público Xet; sem curingas.
HOSTS = frozenset({'huggingface.co', 'cdn-lfs.huggingface.co', 'cdn-lfs-us-1.huggingface.co',
                   'cas-bridge.xethub.hf.co'})


class CatalogSource(BaseModel):
    host: str
    url: str


class CatalogLicense(BaseModel):
    identifier: Literal['MIT']
    url: str


class CatalogModel(BaseModel):
    model: str
    size_bytes: int
    source: CatalogSource
    license: CatalogLicense
    sha256: str
    destination_label: str


class DownloadCatalog(BaseModel):
    models: list[CatalogModel]


class ConfirmDownload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: Literal[True]


class DownloadStarted(BaseModel):
    job_id: str


def checked_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('Origem de download não permitida.')
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        authorize_download(newurl)
        return super().redirect_request(req, fp, code, msg, headers, checked_url(newurl))


def authorize_download(url):
    gate = egress.gateway()
    gate.authorize('whisper_model_download', 'GET', url)
    # A configuração genérica de egress não amplia o catálogo público deste transporte.
    try:
        checked_url(url)
    except ValueError:
        gate._record('whisper_model_download', 'policy_violation', gate._destination(url), None,
                     'destination_not_allowed')
        raise


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        gate = egress.gateway()
        url = req.full_url
        try:
            _, host, port, ips = gate._check('whisper_model_download', 'GET', url, {}, None, resolve=True)
        except egress.EgressBlocked as error:
            gate._record('whisper_model_download', 'policy_violation', gate._destination(url), None, error.code)
            raise

        class PinnedConnection(http.client.HTTPSConnection):
            def connect(self):
                # A resolução ocorre uma única vez no gateway; TLS mantém Host/SNI originais.
                sock = socket.create_connection((ips[0], port), timeout=self.timeout)
                try:
                    self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
                except BaseException:
                    sock.close()
                    raise

        return self.do_open(PinnedConnection, req)


def open_download(url: str):
    authorize_download(url)
    # Não herda proxies/credenciais do ambiente nem cookies do operador.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect(), PinnedHTTPSHandler()).open(
        urllib.request.Request(checked_url(url), headers={'User-Agent': 'Horus-model-download/1'}), timeout=10)


def catalog() -> dict:
    return {'models': [dict(model=name, size_bytes=sum(f['size_bytes'] for f in item['files'].values()),
        source={'host': 'huggingface.co', 'url': f"https://huggingface.co/{item['repo']}/tree/{item['revision']}"},
        license={'identifier': 'MIT', 'url': f"https://huggingface.co/{item['repo']}/blob/{item['revision']}/README.md"},
        sha256=item['files']['model.bin']['sha256'], destination_label=f'models/{name}')
        for name, item in CATALOG.items()]}


class DownloadCancelled(Exception):
    pass


def clean_stale_downloads(service: WhisperModels):
    # Chamado apenas no executor exclusivo, por novo download confirmado.
    allowed = {'model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt', 'README.md'}
    for stage in service.root.glob('.download-*'):
        if (stage.parent != service.root or stage.resolve() != stage or not stage.is_dir()
                or stage.is_symlink() or stage.is_junction()):
            continue
        children = list(stage.iterdir())
        if all(p.name in allowed and p.is_file() and not p.is_symlink() and not p.is_junction() for p in children):
            shutil.rmtree(stage)


def transfer(service: WhisperModels, model: str, cancelled: threading.Event, progress) -> dict:
    item = CATALOG[model]
    service._root()
    clean_stale_downloads(service)
    # Pasta aleatória própria: nenhum parcial é considerado modelo instalado.
    stage = Path(tempfile.mkdtemp(prefix='.download-', dir=service.root))
    deadline = time.monotonic() + 3600
    total = sum(f['size_bytes'] for f in item['files'].values())
    downloaded = 0
    try:
        for name, expected in item['files'].items():
            if cancelled.is_set():
                raise DownloadCancelled()
            # Nomes vêm exclusivamente do catálogo versionado, nunca do cliente/servidor.
            if name not in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt', 'README.md'):
                raise ValueError('Arquivo fora do catálogo permitido.')
            digest = hashlib.sha256()
            size = 0
            url = f"https://huggingface.co/{item['repo']}/resolve/{item['revision']}/{name}"
            with open_download(url) as response, (stage / name).open('xb') as writer:
                while True:
                    if cancelled.is_set():
                        raise DownloadCancelled()
                    if time.monotonic() > deadline:
                        raise ValueError('Tempo limite do download excedido.')
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > expected['size_bytes']:
                        raise ValueError('Tamanho do arquivo diverge do catálogo.')
                    digest.update(chunk)
                    writer.write(chunk)
                    downloaded += len(chunk)
                    progress(min(99, downloaded * 100 // total))
            if size != expected['size_bytes'] or digest.hexdigest() != expected['sha256']:
                raise ValueError('Integridade do modelo não confere com o catálogo.')
        if cancelled.is_set():
            raise DownloadCancelled()
        # Reutiliza validação, hash, publicação atômica e proteção de destino da importação.
        return service.install(ImportModelRequest(model=model, source_directory=str(stage), confirm_license=True))
    finally:
        # Confere o alvo absoluto antes de remover somente nosso estágio.
        if stage.parent == service.root and stage.name.startswith('.download-') and stage.resolve() == stage:
            shutil.rmtree(stage)


async def run(manager, job):
    job_id = job['id']
    manager.active_id = job_id
    cancelled = threading.Event()
    loop = asyncio.get_running_loop()
    last = -1

    def progress(percent):
        nonlocal last
        if percent == last:
            return
        last = percent
        pending = asyncio.run_coroutine_threadsafe(manager.emit(job_id, 'progress', str(percent)), loop)
        while True:
            try:
                pending.result(timeout=0.1)
                break
            except FutureTimeout:
                if cancelled.is_set():
                    pending.cancel()
                    raise DownloadCancelled()

    manager.store.update(job_id, 'running', new_attempt=True)
    await manager.emit(job_id, 'state', 'running')
    task = asyncio.create_task(asyncio.to_thread(transfer, WhisperModels(manager.settings.data_dir),
                                               job['payload']['model'], cancelled, progress))
    try:
        while not task.done():
            if manager.store.get_job(job_id)['status'] == 'cancel_requested':
                cancelled.set()
            await asyncio.wait({task}, timeout=0.1)
        result = task.result()
        # Publicação atômica já concluída: não apresentar uma instalação válida como perdida.
        manager.store.update(job_id, 'succeeded', result=result)
        await manager.emit(job_id, 'progress', '100')
    except DownloadCancelled:
        manager.store.update(job_id, 'cancelled')
    except asyncio.CancelledError:
        cancelled.set()
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        manager.store.update(job_id, 'interrupted', error='Download interrompido; inicie novamente para baixar uma cópia completa.')
        raise
    except Exception:
        # Erros de HTTP podem conter URLs assinadas; nunca persistir a exceção original.
        manager.store.update(job_id, 'failed', error='Download não concluído. Verifique conexão, espaço livre e integridade; parciais foram descartados.')
    finally:
        manager.active_id = None
        await manager.emit(job_id, 'state', manager.store.get_job(job_id)['status'])
