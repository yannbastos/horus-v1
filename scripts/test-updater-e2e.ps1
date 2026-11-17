param(
    [string]$ChannelDirectory = '',
    [string]$DefaultInstaller = '',
    [string]$Root = '',
    [switch]$Build
)
$ErrorActionPreference='Stop'
$repository=Split-Path $PSScriptRoot -Parent
if (-not $ChannelDirectory){$ChannelDirectory=Join-Path $env:LOCALAPPDATA 'Horus/dev-updater-test/channel-c'}
if (-not $DefaultInstaller){$DefaultInstaller=Join-Path $env:LOCALAPPDATA 'Horus/dev-reports/HOR-100-C-artifacts/default-setup.exe'}
if (-not $Root){$Root=Join-Path ([IO.Path]::GetTempPath()) ('horus-updater-e2e-'+[guid]::NewGuid())}
$Root=[IO.Path]::GetFullPath($Root)
$temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
if (-not $Root.StartsWith($temp,[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $Root -Leaf) -notlike 'horus-updater-e2e-*'){throw 'Sandbox exclusivo TEMP obrigatório.'}
New-Item -ItemType Directory -Path $Root -Force|Out-Null
if((Get-Item -LiteralPath $Root).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Sandbox não pode ser link.'}
$existing=@(Get-ChildItem HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall|ForEach-Object{Get-ItemProperty $_.PSPath}|Where-Object DisplayName -eq 'HORUS Test')
if($existing.Count -or (Test-Path -LiteralPath 'HKCU:/Software/horus/HORUS Test')){throw 'Registro preexistente: preservar e usar outro usuário/VM.'}
function RealDataCounts {
    foreach($base in @($env:LOCALAPPDATA,$env:APPDATA)){
        $target=Join-Path $base 'com.horus.audiovisual';$present=Test-Path -LiteralPath $target;$files=@()
        if($present){$files=@(Get-ChildItem -LiteralPath $target -Recurse -File -Force -ErrorAction Stop)}
        [pscustomobject]@{path=$target;exists=$present;files=$files.Count;bytes=[long]($files|Measure-Object Length -Sum).Sum}
    }
}
$before=RealDataCounts|ConvertTo-Json
$before|Set-Content -LiteralPath (Join-Path $Root 'real-before.json')
$python=Join-Path $repository 'backend/.venv/Scripts/python.exe'
try {
    if($Build){
        $env:UV_NATIVE_TLS='1';$env:NODE_OPTIONS='--use-system-ca'
        # Guardar o build padrão antes de o instalador N de teste ocupar o mesmo nome.
        & npm --prefix (Join-Path $repository 'frontend') run tauri build -- --config (Join-Path $PSScriptRoot 'tauri-installer-test.json')
        if($LASTEXITCODE){throw 'Build padrão falhou'}
        New-Item -ItemType Directory -Path (Split-Path $DefaultInstaller -Parent) -Force|Out-Null
        Copy-Item -LiteralPath (Join-Path $repository 'frontend/src-tauri/target/release/bundle/nsis/HORUS Test_0.1.0_x64-setup.exe') -Destination $DefaultInstaller
        foreach($version in @('0.1.0','0.1.1')){
            & (Join-Path $PSScriptRoot 'build-updater-test.ps1') -Version $version -Port 19430 -OutputDirectory $ChannelDirectory -E2eRoot $Root -DebugPort 19431
            if($LASTEXITCODE){throw "Build $version falhou"}
        }
    }
    foreach ($package in @($DefaultInstaller, (Join-Path $ChannelDirectory '0.1.0/setup.exe'), (Join-Path $ChannelDirectory '0.1.1/setup.exe'))) {
        if ((Get-Item -LiteralPath $package).VersionInfo.ProductName -ne 'HORUS Test') { throw 'Ensaio exige instaladores HORUS Test; reconstrua todos os artefatos.' }
    }
    & node (Join-Path $PSScriptRoot 'updater-e2e.mjs') $Root $ChannelDirectory $DefaultInstaller
    if($LASTEXITCODE){throw 'Ensaio falhou; preservar evidências no sandbox.'}
} finally {
    & $python (Join-Path $PSScriptRoot 'updater-e2e-support.py') stop $Root
    $uninstall=Join-Path $Root 'app/uninstall.exe'
    if(Test-Path -LiteralPath $uninstall){
        $p=Start-Process -FilePath $uninstall -ArgumentList '/S' -WindowStyle Hidden -PassThru -Wait
        if($p.ExitCode){throw 'Desinstalação falhou'}
        $end=[DateTime]::UtcNow.AddSeconds(30)
        while((Test-Path -LiteralPath (Join-Path $Root 'app/horus-desktop.exe')) -and [DateTime]::UtcNow -lt $end){Start-Sleep -Milliseconds 200}
        if(Test-Path -LiteralPath (Join-Path $Root 'app/horus-desktop.exe')){throw 'Desktop permaneceu instalado'}
    }
    $product='HKCU:/Software/horus/HORUS Test'
    if(Test-Path -LiteralPath $product){
        $key=Get-Item -LiteralPath $product
        if($key.GetValue('').Trim('"') -ne (Join-Path $Root 'app') -or $key.SubKeyCount -or @($key.GetValueNames()|Where-Object{$_ -notin @('','Installer Language')}).Count){throw 'Registro inesperado preservado'}
        Remove-Item -LiteralPath $product
        $parent=Get-Item HKCU:/Software/horus
        if(-not $parent.SubKeyCount -and -not $parent.ValueCount){Remove-Item -LiteralPath HKCU:/Software/horus}
    }
    $remaining=@(Get-ChildItem HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall|ForEach-Object{Get-ItemProperty $_.PSPath}|Where-Object DisplayName -eq 'HORUS Test')
    if($remaining.Count -or (Test-Path "$env:APPDATA/Microsoft/Windows/Start Menu/Programs/HORUS Test.lnk") -or (Test-Path "$env:USERPROFILE/Desktop/HORUS Test.lnk")){throw 'Resíduo de instalação encontrado'}
    $after=RealDataCounts|ConvertTo-Json;$after|Set-Content -LiteralPath (Join-Path $Root 'real-after.json')
    [pscustomobject]@{registry_clean=($remaining.Count -eq 0);real_data_unchanged=($before -eq $after);uninstalled=(-not(Test-Path -LiteralPath (Join-Path $Root 'app/horus-desktop.exe')))}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $Root 'cleanup-result.json')
    if($before -ne $after){throw 'Contagem/tamanho dos dados reais mudou: interromper e investigar'}
    if(Test-Path -LiteralPath (Join-Path $Root 'data/installer-fixture-hashes.json')){
        & $python (Join-Path $PSScriptRoot 'updater-e2e-support.py') verify $Root
        if($LASTEXITCODE){throw 'Dados semeados alterados após desinstalação'}
    }
    [pscustomobject]@{sandbox=$Root;uninstalled=$true;registry_clean=$true;real_data_unchanged=$true}|ConvertTo-Json -Compress
    # Preservar somente este sandbox para evidências; nenhuma remoção recursiva automática.
}
