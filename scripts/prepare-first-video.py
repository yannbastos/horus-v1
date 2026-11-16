"""Build privado do runtime offline. Nunca chamado pelo aplicativo ou por um job."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'scripts/first-video-artifacts.json'
REQUIREMENTS = ROOT / 'scripts/first-video-requirements.txt'
BUILD_REQUIREMENTS = ROOT / 'scripts/first-video-build-requirements.txt'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require_hash(path, expected):
    if sha(path) != expected:
        raise ValueError(f'Hash divergente: {Path(path).name}')


def download(url, expected, cache):
    destination = cache / expected
    if not destination.is_file():
        print('Download de build:', url, flush=True)
        with urllib.request.urlopen(url, timeout=180) as response, destination.open('wb') as stream:
            shutil.copyfileobj(response, stream)
    require_hash(destination, expected)
    return destination


def run(*args, **kwargs):
    print('Executando:', args[0], flush=True)
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def runtime_files(directory, names):
    """Inferência usa DLLs, não SDKs estáticos (dnnl.lib sozinho excede 2 GiB)."""
    excluded = {name for name in names if name == '__pycache__' or name.endswith(('.lib', '.pdb'))}
    if Path(directory).name == 'torch':
        excluded.add('include')
    return excluded


def prepare(args):
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    stage, cache = args.output.resolve(), ROOT / 'backend/.cache/first-video-downloads'
    stage.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(LOCK.read_bytes() + REQUIREMENTS.read_bytes() + BUILD_REQUIREMENTS.read_bytes() +
                                 Path(__file__).read_bytes()).hexdigest()
    marker = stage / 'runtime.json'
    if marker.exists():
        previous = json.loads(marker.read_text(encoding='utf-8'))
        if previous.get('build_fingerprint') != fingerprint:
            raise ValueError('Stage de outra revisão: use uma pasta de saída nova, sem apagar artefatos existentes.')
        for relative, digest in previous['files'].items():
            require_hash(stage / relative, digest)
        print('Stage completo e hashes verificados.', flush=True)
        return

    # Somente arquivos versionados de código/locks; nunca copiar canais, .env, bancos ou venv do operador.
    selected = ['scripts/fabrica', 'scripts/pyproject.toml', 'scripts/uv.lock',
                'remotion/src', 'remotion/package.json', 'remotion/package-lock.json', 'remotion/tsconfig.json']
    archive = subprocess.check_output(['git', '-C', str(args.factory_source), 'archive',
                                       lock['factory_revision'], *selected])
    factory = stage / 'factory'
    factory.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        stream.extractall(factory, filter='data')
    archive_hash = hashlib.sha256(archive).hexdigest()

    # Uma distribuição completa relocável, não a cópia de uma venv com pyvenv.cfg absoluto.
    python_root = factory / 'scripts/.venv/Scripts'
    python_root.mkdir(parents=True, exist_ok=True)
    python_archive = download(lock['python']['url'], lock['python']['sha256'], cache)
    with tarfile.open(python_archive, mode='r:gz') as stream:
        for member in stream.getmembers():
            if member.name.startswith('python/'):
                member.name = member.name[len('python/'):]
                if member.name:
                    stream.extract(member, python_root, filter='data')
    python = python_root / 'python.exe'
    # Mantém o código independente da fábrica importável sem PYTHONPATH ou instalação editável.
    site_packages = python_root / 'Lib/site-packages'
    site_packages.mkdir(parents=True, exist_ok=True)
    # Materializa wheels verificadas fora do runtime distribuído e copia somente arquivos de execução.
    dependencies = cache / ('python-deps-' + sha(REQUIREMENTS)[:16])
    run('uv', 'pip', 'install', '--python', python, '--target', dependencies,
        '--require-hashes', '-r', REQUIREMENTS, '--index', 'https://download.pytorch.org/whl/cpu',
        '--index-strategy', 'unsafe-best-match', '--build-constraints', BUILD_REQUIREMENTS, '--require-build-hashes')
    copy_source, copy_target = dependencies.resolve(), site_packages.resolve()
    if os.name == 'nt':
        copy_source, copy_target = Path('\\\\?\\' + str(copy_source)), Path('\\\\?\\' + str(copy_target))
    shutil.copytree(copy_source, copy_target, dirs_exist_ok=True, ignore=runtime_files)
    # Do site-packages até factory/scripts; arquivo relativo, sem caminho da máquina de build.
    (site_packages / 'horus_factory.pth').write_text('../../../..\n', encoding='utf-8')
    run(python, '-I', '-c', 'import torch,kokoro,PIL,pydantic,fabrica.render; print(torch.__version__)')

    node_archive = download(lock['node']['url'], lock['node']['sha256'], cache)
    tools = stage / 'tools'
    tools.mkdir(exist_ok=True)
    with zipfile.ZipFile(node_archive) as stream:
        prefix = f"node-v{lock['node']['version']}-win-x64/"
        for entry in stream.infolist():
            if entry.filename.startswith(prefix) and not entry.is_dir():
                relative = entry.filename[len(prefix):]
                target = tools / relative
                if not target.resolve().is_relative_to(tools):
                    raise ValueError('Caminho inválido no arquivo Node')
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream.open(entry) as source, target.open('wb') as dest:
                    shutil.copyfileobj(source, dest)
    env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ.get('PATH', ''),
           'npm_config_audit': 'false', 'npm_config_fund': 'false'}
    run(tools / 'node.exe', tools / 'node_modules/npm/bin/npm-cli.js', 'ci', '--ignore-scripts', '--no-audit', '--no-fund',
        cwd=factory / 'remotion', env=env)

    licenses = stage / 'licenses'
    licenses.mkdir(exist_ok=True)
    for name, digest in lock['ffmpeg']['files'].items():
        path = args.ffmpeg_source / 'bin' / name
        require_hash(path, digest)
        shutil.copy2(path, tools / name)
    shutil.copy2(args.ffmpeg_source / 'LICENSE.txt', licenses / 'FFmpeg-GPL.txt')
    run(tools / 'ffmpeg.exe', '-version', stdout=(licenses / 'FFmpeg-build.txt').open('w', encoding='utf-8'))

    model = stage / 'kokoro'
    for relative, digest in lock['kokoro'].items():
        target = model / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if args.kokoro_source:
            source = args.kokoro_source / relative
            require_hash(source, digest)
        else:
            source = download(f"https://huggingface.co/hexgrad/Kokoro-82M/resolve/{lock['kokoro_revision']}/{relative}", digest, cache)
        shutil.copy2(source, target)
    shutil.copy2(site_packages / 'kokoro-0.9.4.dist-info/licenses/LICENSE', licenses / 'Kokoro-Apache-2.0.txt')
    shutil.copy2(tools / 'LICENSE', licenses / 'Node.txt')
    shutil.copy2(python_root / 'LICENSE.txt', licenses / 'Python.txt')
    shutil.copy2(ROOT / 'docs/instalador-primeiro-video.md', licenses / 'LEIA-ME.md')
    shutil.copy2(LOCK, licenses / LOCK.name)
    shutil.copy2(REQUIREMENTS, licenses / REQUIREMENTS.name)
    shutil.copy2(BUILD_REQUIREMENTS, licenses / BUILD_REQUIREMENTS.name)
    fonts = stage / 'fonts'
    fonts.mkdir(exist_ok=True)
    for name in ('ArchivoExpanded-SemiBold.ttf', 'OFL-Archivo.txt'):
        shutil.copy2(ROOT / 'frontend/public/fonts' / name, fonts / name)

    packages = []
    for package in sorted((factory / 'remotion/node_modules').glob('**/package.json')):
        data = json.loads(package.read_text(encoding='utf-8'))
        if data.get('name') and data.get('version'):
            packages.append({key: data.get(key) for key in ('name', 'version', 'license')})
    (licenses / 'npm-packages.json').write_text(json.dumps(packages, ensure_ascii=False, indent=2), encoding='utf-8')
    # As licenças originais permanecem nos pacotes npm e nas pastas *.dist-info/licenses Python.
    paths = [p for p in stage.rglob('*') if p.is_file() and p != marker]
    if any(p.stat().st_size >= 2**31 for p in paths):
        raise ValueError('Arquivo excede o limite individual do NSIS; revise a seleção do runtime.')
    forbidden = [p for p in paths if p.name in {'.env', 'hermes.json', 'pyvenv.cfg'} or p.suffix in {'.sqlite3', '.key'}
                 or (p.suffix == '.pem' and p.name != 'cacert.pem')]
    if forbidden:
        raise ValueError('Arquivo operacional ou de ambiente não permitido no pacote')
    manifest = {'schema_version': 1, 'profile': 'first-video', 'build_fingerprint': fingerprint,
                'factory_revision': lock['factory_revision'], 'factory_archive_sha256': archive_hash,
                'artifacts': lock, 'files': {p.relative_to(stage).as_posix(): sha(p) for p in paths}}
    marker.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Runtime offline preparado:', stage, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--factory-source', type=Path, required=True)
    parser.add_argument('--ffmpeg-source', type=Path, required=True)
    parser.add_argument('--kokoro-source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bundle-output', type=Path)
    options = parser.parse_args()
    prepare(options)
    if options.bundle_output:
        source, destination = options.output.resolve(), options.bundle_output.resolve()
        # A saída do PyInstaller é nova. Não mesclar com runtimes de outra revisão.
        if os.name == 'nt':
            source, destination = Path('\\\\?\\' + str(source)), Path('\\\\?\\' + str(destination))
        shutil.copytree(source, destination)
