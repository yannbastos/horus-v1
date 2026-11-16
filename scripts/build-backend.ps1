param([switch]$SkipInstall, [switch]$IncludeTranscription, [switch]$IncludeFirstVideo)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$backend = Join-Path $root 'backend'
if ($IncludeFirstVideo) { $IncludeTranscription = $true }
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
    # Fontes necessárias ao bridge no Python da fábrica; pesos continuam externos.
    $workerSources = (Join-Path $backend 'src/horus_backend') + ';horus_backend'
    & $python -m PyInstaller --noconfirm --clean --onedir --name horus-backend --distpath dist --workpath build/work --specpath build --paths src --collect-submodules uvicorn --collect-submodules horus_backend --hidden-import keyring.backends.Windows --add-data $workerSources @modules launcher.py
    if ($LASTEXITCODE) { throw 'PyInstaller falhou.' }
} finally { Pop-Location }
if ($IncludeFirstVideo) {
    if (-not $env:HORUS_BUILD_FACTORY_SOURCE -or -not $env:HORUS_BUILD_FFMPEG_SOURCE) {
        throw 'O perfil primeiro vídeo exige HORUS_BUILD_FACTORY_SOURCE e HORUS_BUILD_FFMPEG_SOURCE somente na máquina de build.'
    }
    $stage = if ($env:HORUS_BUILD_FIRST_VIDEO_STAGE) { $env:HORUS_BUILD_FIRST_VIDEO_STAGE } else {
        Join-Path $backend '.cache/first-video-stage-v3'
    }
    $arguments = @((Join-Path $PSScriptRoot 'prepare-first-video.py'), '--factory-source', $env:HORUS_BUILD_FACTORY_SOURCE,
        '--ffmpeg-source', $env:HORUS_BUILD_FFMPEG_SOURCE, '--output', $stage,
        '--bundle-output', (Join-Path $backend 'dist/horus-backend/first-video'))
    if ($env:HORUS_BUILD_KOKORO_SOURCE) { $arguments += @('--kokoro-source', $env:HORUS_BUILD_KOKORO_SOURCE) }
    & $python @arguments
    if ($LASTEXITCODE) { throw 'Preparação do runtime offline falhou.' }
}
