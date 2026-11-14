param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$backend = Join-Path $root 'backend'
$python = Join-Path $backend '.venv-bundle/Scripts/python.exe'
if (-not $SkipInstall) {
    # Ambiente exclusivo: extras de desenvolvimento nunca contaminam a distribuição.
    if (-not (Test-Path -LiteralPath $python)) {
        uv venv --python 3.12 (Join-Path $backend '.venv-bundle')
        if ($LASTEXITCODE) { throw 'Falha ao preparar Python de build.' }
    }
    uv pip sync --python $python --require-hashes (Join-Path $PSScriptRoot 'backend-bundle-requirements.txt')
    if ($LASTEXITCODE) { throw 'Falha ao sincronizar dependências de build.' }
    uv pip install --python $python --no-deps $backend
    if ($LASTEXITCODE) { throw 'Falha ao instalar dependências de build.' }
}
Push-Location $backend
try {
    & $python -m PyInstaller --noconfirm --clean --onedir --name horus-backend --distpath dist --workpath build/work --specpath build --paths src --collect-submodules uvicorn --collect-submodules horus_backend --hidden-import keyring.backends.Windows --exclude-module faster_whisper --exclude-module ctranslate2 --exclude-module av --exclude-module tokenizers --exclude-module torch --exclude-module kokoro --exclude-module yt_dlp launcher.py
    if ($LASTEXITCODE) { throw 'PyInstaller falhou.' }
} finally { Pop-Location }
