param([Parameter(Mandatory=$true)][string]$Installer,[Parameter(Mandatory=$true)][string]$UpgradeInstaller,[string]$Python='python')
$ErrorActionPreference='Stop'
$product='HKCU:/Software/horus/HORUS Test'
$uninstallKey='HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall/HORUS Test'
if ((Test-Path -LiteralPath $product) -or (Test-Path -LiteralPath $uninstallKey)) { throw 'Produto de teste já registrado: usar VM limpa.' }
foreach ($p in @($Installer,$UpgradeInstaller)) {
    if ((Get-Item -LiteralPath $p).VersionInfo.ProductName -ne 'HORUS Test') { throw 'Somente identidade HORUS Test permitida.' }
}
$root=Join-Path ([IO.Path]::GetTempPath()) ('hor147-cycle-'+[guid]::NewGuid())
$install=Join-Path $root 'app'
$data=Join-Path $root 'data'
New-Item -ItemType Directory $data -Force|Out-Null
$oldData=$env:HORUS_DATA_DIR
$env:HORUS_DATA_DIR=$data
# Descartáveis fora de INSTDIR: banco, projeto, preferência e mídia sintéticos.
& $Python -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('create table fixture(value text)'); c.execute('insert into fixture values (?)', ('preservar',)); c.commit(); c.close()" (Join-Path $data 'database.sqlite3')
if($LASTEXITCODE) { throw 'Fixture SQLite falhou.' }
foreach($name in @('project.json','preferences.json','media.mp4')) { [IO.File]::WriteAllBytes((Join-Path $data $name),[byte[]](1,2,3,4,5)) }
function Hashes { @(Get-ChildItem -LiteralPath $data -File | Sort-Object Name | Get-FileHash -Algorithm SHA256 | Select-Object Hash) | ConvertTo-Json -Compress }
$before=Hashes
$checks=0
try {
    foreach($package in @($Installer,$UpgradeInstaller,$Installer)) {
        $p=Start-Process -FilePath $package -ArgumentList "/S /NS /D=$install" -WindowStyle Hidden -PassThru -Wait
        if($p.ExitCode) { throw "Instalação falhou: $($p.ExitCode)" }
        if(-not(Test-Path -LiteralPath (Join-Path $install 'horus-desktop.exe'))) { throw 'Executável ausente.' }
        $expected=(Get-Item -LiteralPath $package).VersionInfo.ProductVersion
        if([version](Get-ItemProperty -LiteralPath $uninstallKey).DisplayVersion -ne [version]$expected) { throw 'Versão incorreta.' }
        if((Hashes) -ne $before) { throw 'Instalação alterou dados externos.' }
        $checks++
    }
} finally {
    try {
        $uninstall=Join-Path $install 'uninstall.exe'
        if(Test-Path -LiteralPath $uninstall) {
            $p=Start-Process -FilePath $uninstall -ArgumentList '/S' -WindowStyle Hidden -PassThru -Wait
            if($p.ExitCode) { throw 'Uninstall falhou.' }
            $deadline=[DateTime]::UtcNow.AddSeconds(30)
            while((Test-Path -LiteralPath $uninstallKey) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
            if((Test-Path -LiteralPath $uninstallKey) -or (Test-Path -LiteralPath (Join-Path $install 'horus-desktop.exe'))) { throw 'Desinstalação incompleta.' }
        }
        if((Hashes) -ne $before) { throw 'Desinstalação alterou dados externos.' }
        if(Test-Path -LiteralPath $product) {
            $key=Get-Item -LiteralPath $product
            if($key.GetValue('') -ne $install -or $key.SubKeyCount) { throw 'Registro inesperado preservado.' }
            Remove-Item -LiteralPath $product
        }
        [pscustomobject]@{install_upgrade_rollback=$checks;uninstalled=$true;external_data_hashes_unchanged=$true;test_registry_clean=$true}|ConvertTo-Json -Compress
    } finally { $env:HORUS_DATA_DIR=$oldData }
    $resolved=[IO.Path]::GetFullPath($root)
    $tempRoot=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if(-not $resolved.StartsWith($tempRoot,[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $resolved -Leaf) -notlike 'hor147-cycle-*') { throw 'Sandbox inválido.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
