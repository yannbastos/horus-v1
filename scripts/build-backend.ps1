param([switch]$SkipInstall, [switch]$IncludeTranscription)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$backend = Join-Path $root 'backend'
$environment = if ($IncludeTranscription) { '.venv-bundle-transcription' } else { '.venv-bundle' }
$lock = if ($IncludeTranscription) { 'backend-bundle-transcription-requirements.txt' } else { 'backend-bundle-requirements.txt' }
$python = Join-Path $backend "$environment/Scripts/python.exe"
if (-not $SkipInstall) {
    # Ambiente exclusivo: extras de desenvolvimento nunca contaminam a distribuição.
    if (-not (Test-Path -LiteralPath $python)) {
        uv venv --python 3.12 (Join-Path $backend $environment)
        if ($LASTEXITCODE) { throw 'Falha ao preparar Python de build.' }
    }
    uv pip sync --python $python --require-hashes (Join-Path $PSScriptRoot $lock)
    if ($LASTEXITCODE) { throw 'Falha ao sincronizar dependências de build.' }
    uv pip install --python $python --no-deps $backend
    if ($LASTEXITCODE) { throw 'Falha ao instalar dependências de build.' }
}
Push-Location $backend
try {
    $modules = @('--exclude-module', 'torch', '--exclude-module', 'kokoro', '--exclude-module', 'yt_dlp')
    if ($IncludeTranscription) {
        $modules += @('--collect-all', 'faster_whisper', '--collect-all', 'ctranslate2', '--collect-all', 'av', '--collect-all', 'tokenizers', '--copy-metadata', 'ctranslate2')
    } else {
        foreach ($name in @('faster_whisper', 'ctranslate2', 'av', 'tokenizers')) { $modules += @('--exclude-module', $name) }
    }
    # O Python externo do Kokoro precisa do script real, não do módulo no PYZ.
    $kokoroWorker = (Join-Path $backend 'src/horus_backend/narration/kokoro_worker.py') + ';horus_backend/narration'
    & $python -m PyInstaller --noconfirm --clean --onedir --name horus-backend --distpath dist --workpath build/work --specpath build --paths src --collect-submodules uvicorn --collect-submodules horus_backend --hidden-import keyring.backends.Windows --add-data $kokoroWorker @modules launcher.py
    if ($LASTEXITCODE) { throw 'PyInstaller falhou.' }
} finally { Pop-Location }
