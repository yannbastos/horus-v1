param(
    [string]$ConfigFile = (Join-Path $PSScriptRoot 'local-pilot.json'),
    [string]$DesktopPath,
    [string]$DataDirectory,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
try {
    $config = Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $DesktopPath) {
        $entries = @(Get-ChildItem HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall |
            ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DisplayName -eq 'HORUS' })
        if ($entries.Count -ne 1) { throw 'Instale o HORUS antes de abrir este atalho.' }
        $DesktopPath = Join-Path $entries[0].InstallLocation.Trim('"') 'horus-desktop.exe'
    }
    $requiredFiles = @($DesktopPath, $config.ffmpeg, $config.kokoro_python,
        (Join-Path $config.factory 'scripts/.venv/Scripts/python.exe'),
        (Join-Path $config.alignment_models_dir 'faster-whisper-small/model.bin'))
    foreach ($path in $requiredFiles) {
        if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Recurso local ausente: $path"
        }
    }
    foreach ($path in @($config.kokoro_model_dir, $config.whisper_models_dir,
        (Join-Path $config.factory 'remotion/node_modules'))) {
        if (-not [IO.Path]::IsPathRooted($path) -or -not (Test-Path -LiteralPath $path -PathType Container)) {
            throw "Pasta local ausente: $path"
        }
    }
    $data = if ($DataDirectory) { [IO.Path]::GetFullPath($DataDirectory) } else {
        Join-Path $env:LOCALAPPDATA 'Horus/first-video-codex-20261115/workspace'
    }
    $environment = @{
        HORUS_DATA_DIR = $data
        FABRICA_HOME = $config.factory
        HORUS_FFMPEG = $config.ffmpeg
        HORUS_KOKORO_PYTHON = $config.kokoro_python
        HORUS_KOKORO_MODEL_DIR = $config.kokoro_model_dir
        HORUS_WHISPER_MODELS_DIR = $config.whisper_models_dir
        HORUS_ALIGNMENT_MODEL = $config.alignment_models_dir
        HORUS_HERMES_CONFIG = (Join-Path $data 'missing-hermes.json')
        HORUS_HERMES_PROFILE = (Join-Path $data 'disabled-hermes-profile')
        HORUS_WORKFLOW_EXECUTOR = 'simulated'
        HORUS_RADAR_EXECUTOR = 'hermes_simulated'
        HORUS_NARRATION_PAID_ENABLED = '0'
        HORUS_WHISPER_DEVICE = 'cpu'
        HF_HUB_OFFLINE = '1'
        TRANSFORMERS_OFFLINE = '1'
        HF_HUB_DISABLE_TELEMETRY = '1'
    }
    if ($CheckOnly) {
        [pscustomobject]@{ ready = $true; desktop = $DesktopPath; data_directory = $data; launch = $false } |
            ConvertTo-Json -Compress
        exit 0
    }
    $previous = @{}
    try {
        foreach ($name in $environment.Keys) {
            $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process')
        }
        Start-Process -FilePath $DesktopPath -WorkingDirectory (Split-Path $DesktopPath -Parent) | Out-Null
    } finally {
        foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    }
} catch {
    if (-not $CheckOnly) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($_.Exception.Message, 'HORUS — teste local') | Out-Null
    }
    Write-Error $_.Exception.Message
    exit 1
}
