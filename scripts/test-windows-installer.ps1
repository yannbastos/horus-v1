param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [string]$UpgradeInstaller = '',
    [switch]$WithTranscription,
    [switch]$WithFirstVideo,
    [string]$CudaRuntimeDir = '',
    [string]$ModelDir = '',
    [string]$Audio = '',
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Python) { $Python = Join-Path (Split-Path $PSScriptRoot -Parent) 'backend/.venv/Scripts/python.exe' }
$existing = Get-ChildItem HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall |
    ForEach-Object { Get-ItemProperty $_.PSPath } |
    Where-Object { $_.DisplayName -eq 'HORUS Test' }
if ($existing) { throw 'Instalação HORUS preexistente: execute em outro usuário/VM para preservá-la.' }
$productRegistry = 'HKCU:/Software/horus/HORUS Test'
$productRegistryExisted = Test-Path -LiteralPath $productRegistry
if ($productRegistryExisted) { throw 'Registro de teste preexistente: preservar e usar VM limpa.' }
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
$upgradePath = if ($UpgradeInstaller) { (Resolve-Path -LiteralPath $UpgradeInstaller).Path } else { '' }
foreach ($package in @($installerPath, $upgradePath)) {
    if ($package -and (Get-Item -LiteralPath $package).VersionInfo.ProductName -ne 'HORUS Test') { throw 'Ensaio exige instalador HORUS Test; nunca executar produto real.' }
}
$smokeOptions = @()
if ($WithFirstVideo) { $WithTranscription = $true; $smokeOptions += '--with-first-video' }
if ($WithTranscription) { $smokeOptions += '--with-transcription' }
if ($CudaRuntimeDir -and (-not $WithTranscription -or -not $ModelDir -or -not $Audio)) { throw 'Ensaio CUDA exige WithTranscription, ModelDir e Audio sintético.' }
if ($ModelDir) { $ModelDir = (Resolve-Path -LiteralPath $ModelDir).Path }
if ($Audio) { $Audio = (Resolve-Path -LiteralPath $Audio).Path }
function Assert-InstalledVersion([string]$Package) {
    $expected = (Get-Item -LiteralPath $Package).VersionInfo.ProductVersion
    if (-not $expected) { throw 'Instalador sem versão de produto.' }
    $entry = @(Get-ChildItem HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall |
        ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DisplayName -eq 'HORUS Test' })
    if ($entry.Count -ne 1 -or [version]$entry[0].DisplayVersion -ne [version]$expected) {
        throw "Versão registrada não corresponde ao instalador $expected."
    }
    [pscustomobject]@{ installed_version = $entry[0].DisplayVersion } | ConvertTo-Json -Compress
}
if ($upgradePath -and [version](Get-Item -LiteralPath $upgradePath).VersionInfo.ProductVersion -le [version](Get-Item -LiteralPath $installerPath).VersionInfo.ProductVersion) {
    throw 'UpgradeInstaller deve ter versão maior que Installer.'
}
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('horus-installer-' + [guid]::NewGuid())
$install = Join-Path $temporary 'app'
New-Item -ItemType Directory -Path $temporary | Out-Null
$oldDataDir = $env:HORUS_DATA_DIR
$env:HORUS_DATA_DIR = Join-Path $temporary 'data'
try {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $installerPath -ArgumentList "/S /D=$install" -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw "NSIS falhou: $($process.ExitCode)" }
    $installSeconds = $watch.Elapsed.TotalSeconds
    Assert-InstalledVersion $installerPath
    $desktop = Join-Path $install 'horus-desktop.exe'
    if (-not (Test-Path -LiteralPath $desktop)) { throw 'Desktop não foi instalado no destino temporário.' }
    & $Python (Join-Path $PSScriptRoot 'smoke-windows-package.py') $desktop --desktop @smokeOptions
    if ($LASTEXITCODE) { throw 'Smoke do desktop falhou.' }
    & $Python (Join-Path $PSScriptRoot 'smoke-windows-package.py') $desktop --desktop --crash @smokeOptions
    if ($LASTEXITCODE) { throw 'Smoke de queda do desktop falhou.' }
    if ($CudaRuntimeDir) {
        $smoke = Join-Path $PSScriptRoot 'smoke-windows-package.py'
        # CUDA explicitamente solicitada em todos: ausência/inválido/UNC devem usar CPU.
        & $Python $smoke $desktop --desktop --with-transcription --model-dir $ModelDir --audio $Audio --expected-device cpu
        if ($LASTEXITCODE) { throw 'Fallback CPU sem runtime falhou.' }
        foreach ($invalid in @((Join-Path $temporary 'missing-runtime'), '\\invalid-server\runtime')) {
            & $Python $smoke $desktop --desktop --with-transcription --cuda-runtime-dir $invalid --model-dir $ModelDir --audio $Audio --expected-device cpu
            if ($LASTEXITCODE) { throw 'Fallback CPU com runtime inválido/UNC falhou.' }
        }
        & $Python $smoke $desktop --desktop --with-transcription --cuda-runtime-dir $CudaRuntimeDir --model-dir $ModelDir --audio $Audio --expected-device cuda
        if ($LASTEXITCODE) { throw 'Transcrição instalada em CUDA falhou.' }
        $oldCudaPath = $env:CUDA_PATH
        try {
            # Apenas ambiente deste ensaio, nunca configuração global do Windows.
            $env:CUDA_PATH = $CudaRuntimeDir
            & $Python $smoke $desktop --desktop --with-transcription --cuda-runtime-dir system --model-dir $ModelDir --audio $Audio --expected-device cuda
            if ($LASTEXITCODE) { throw 'Descoberta system instalada em CUDA falhou.' }
        } finally { $env:CUDA_PATH = $oldCudaPath }
    }
    if ($upgradePath) {
        $dataCheck = Join-Path $PSScriptRoot 'check-installer-data.py'
        & $Python (Join-Path $PSScriptRoot 'smoke-windows-package.py') $desktop --desktop --data-dir $env:HORUS_DATA_DIR @smokeOptions
        if ($LASTEXITCODE) { throw 'Inicialização N falhou.' }
        & $Python $dataCheck $env:HORUS_DATA_DIR seed
        if ($LASTEXITCODE) { throw 'Criação de dados sintéticos falhou.' }
        foreach ($step in @(@{ name = 'upgrade'; path = $upgradePath }, @{ name = 'rollback'; path = $installerPath })) {
            $process = Start-Process -FilePath $step.path -ArgumentList "/S /D=$install" -WindowStyle Hidden -PassThru -Wait
            if ($process.ExitCode -ne 0) { throw "$($step.name): NSIS falhou ($($process.ExitCode))." }
            Assert-InstalledVersion $step.path
            # Conferir antes de abrir: reinstalação não pode recriar um banco vazio.
            & $Python $dataCheck $env:HORUS_DATA_DIR verify
            if ($LASTEXITCODE) { throw "$($step.name): dados perdidos na instalação." }
            & $Python (Join-Path $PSScriptRoot 'smoke-windows-package.py') $desktop --desktop --data-dir $env:HORUS_DATA_DIR @smokeOptions
            if ($LASTEXITCODE) { throw "$($step.name): smoke falhou." }
            & $Python $dataCheck $env:HORUS_DATA_DIR verify
            if ($LASTEXITCODE) { throw "$($step.name): dados perdidos ao iniciar." }
            & $Python (Join-Path $PSScriptRoot 'smoke-windows-package.py') $desktop --desktop --crash --data-dir $env:HORUS_DATA_DIR @smokeOptions
            if ($LASTEXITCODE) { throw "$($step.name): smoke de queda falhou." }
            & $Python $dataCheck $env:HORUS_DATA_DIR future --executable (Join-Path $install 'backend/horus-backend.exe')
            if ($LASTEXITCODE) { throw "$($step.name): recusa de schema futuro falhou." }
            [pscustomobject]@{ stage = $step.name; data_preserved = $true; orphan_backend = $false } | ConvertTo-Json -Compress
        }
    }
    [pscustomobject]@{ installer_bytes = (Get-Item -LiteralPath $installerPath).Length; install_seconds = [math]::Round($installSeconds, 3) } | ConvertTo-Json -Compress
} finally {
    try {
        $uninstall = Join-Path $install 'uninstall.exe'
        if (Test-Path -LiteralPath $uninstall) {
            $watch = [Diagnostics.Stopwatch]::StartNew()
            Start-Process -FilePath $uninstall -ArgumentList '/S' -WindowStyle Hidden -Wait
            $deadline = [DateTime]::UtcNow.AddSeconds(30)
            while ((Test-Path -LiteralPath (Join-Path $install 'horus-desktop.exe')) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
            if (Test-Path -LiteralPath (Join-Path $install 'horus-desktop.exe')) { throw "Desinstalação incompleta: $install" }
            $remaining = Get-ChildItem HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall |
                ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.DisplayName -eq 'HORUS Test' }
            if ($remaining) { throw 'Entrada de desinstalação HORUS permaneceu no HKCU.' }
            # O NSIS mantém o local de instalação para reinstalações. Só remover a chave
            # criada por ESTE ensaio quando ela aponta exatamente para a pasta exclusiva.
            if (-not $productRegistryExisted -and (Test-Path -LiteralPath $productRegistry)) {
                $key = Get-Item -LiteralPath $productRegistry
                if ($key.GetValue('').Trim('"') -ne $install -or $key.SubKeyCount -ne 0 -or
                    @($key.GetValueNames() | Where-Object { $_ -notin @('', 'Installer Language') }).Count) {
                    throw 'Chave de produto inesperada: preservada para inspeção.'
                }
                Remove-Item -LiteralPath $productRegistry
                $parent = Get-Item -LiteralPath 'HKCU:/Software/horus'
                if ($parent.SubKeyCount -eq 0 -and $parent.ValueCount -eq 0) { Remove-Item -LiteralPath 'HKCU:/Software/horus' }
            }
            $shortcut = Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs/HORUS Test.lnk'
            if (Test-Path -LiteralPath $shortcut) { throw 'Atalho HORUS permaneceu após desinstalar.' }
            if ($upgradePath -and (Test-Path -LiteralPath (Join-Path $env:HORUS_DATA_DIR 'installer-fixture-hashes.json'))) {
                & $Python (Join-Path $PSScriptRoot 'check-installer-data.py') $env:HORUS_DATA_DIR verify
                if ($LASTEXITCODE) { throw 'Desinstalação alterou os dados semeados.' }
            }
            [pscustomobject]@{ uninstall_seconds = [math]::Round($watch.Elapsed.TotalSeconds, 3); uninstalled = $true } | ConvertTo-Json -Compress
        }
    } finally { $env:HORUS_DATA_DIR = $oldDataDir }
    # O diretório do ensaio é exclusivo; resolver e conferir antes de remover.
    $resolved = [IO.Path]::GetFullPath($temporary)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path $resolved -Leaf) -notlike 'horus-installer-*') { throw 'Diretório temporário inesperado.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
