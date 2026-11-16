param(
    [Parameter(Mandatory=$true)][string]$Version,
    [int]$Port = 19420,
    [string]$OutputDirectory = '',
    [string]$E2eRoot = '',
    [int]$DebugPort = 19431
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') { throw 'Use SemVer estável no ensaio NSIS.' }
if ($Port -lt 1024 -or $Port -gt 65535 -or $Port -in @(1420,8765)) { throw 'Porta de teste inválida/reservada.' }
$testRoot = Join-Path $env:LOCALAPPDATA 'Horus/dev-updater-test'
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $testRoot 'channel' }
foreach ($target in @($testRoot,$OutputDirectory)) {
    $absolute = [IO.Path]::GetFullPath($target)
    if ($absolute.StartsWith([IO.Path]::GetFullPath($root).TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) -or $absolute -eq $root) { throw 'Chaves e artefatos precisam ficar fora do repositório.' }
    New-Item -ItemType Directory -Path $absolute -Force | Out-Null
    if ((Get-Item -LiteralPath $absolute).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Diretório de teste não pode ser link/junction.' }
}
$key = Join-Path $testRoot 'horus-updater-test.key'
$cli = Join-Path $root 'frontend/node_modules/@tauri-apps/cli/tauri.js'
if (-not (Test-Path -LiteralPath $key)) {
    # A ferramenta imprime a chave gerada: descartar toda a saída, nunca gravar log.
    & node $cli signer generate --ci --write-keys $key *> $null
    if ($LASTEXITCODE) { throw 'Falha ao gerar chave exclusiva de teste; saída omitida para proteger a chave.' }
}
if (-not (Test-Path -LiteralPath ($key+'.pub'))) { throw 'Chave pública de teste ausente; não substituir chave existente.' }
$overlay = Join-Path $testRoot 'tauri-updater-test.generated.json'
$configuration = @{
    version=$Version
    bundle=@{createUpdaterArtifacts=$true}
    plugins=@{updater=@{
        pubkey=[IO.File]::ReadAllText($key+'.pub').Trim()
        endpoints=@("http://127.0.0.1:$Port/latest.json")
        dangerousInsecureTransportProtocol=$true
        requireSignedVersion=$true
        windows=@{installMode='passive'}
    }}
}
if ($E2eRoot) {
    $sandbox=[IO.Path]::GetFullPath($E2eRoot)
    $temp=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if (-not $sandbox.StartsWith($temp,[StringComparison]::OrdinalIgnoreCase) -or (Split-Path $sandbox -Leaf) -notlike 'horus-updater-e2e-*') {throw 'Sandbox E2E deve ser exclusivo sob TEMP.'}
    if ($DebugPort -in @(1420,8765,$Port) -or $DebugPort -lt 1024 -or $DebugPort -gt 65535) {throw 'Porta CDP inválida.'}
    New-Item -ItemType Directory -Path $sandbox -Force | Out-Null
    if ((Get-Item -LiteralPath $sandbox).Attributes -band [IO.FileAttributes]::ReparsePoint) {throw 'Sandbox não pode ser link.'}
    $configuration.plugins.horusUpdaterTest=@{root=$sandbox}
    $window=(Get-Content (Join-Path $root 'frontend/src-tauri/tauri.conf.json') -Raw|ConvertFrom-Json).app.windows[0]
    $window|Add-Member -NotePropertyName additionalBrowserArgs -NotePropertyValue "--remote-debugging-port=$DebugPort" -Force
    $configuration.app=@{windows=@($window);appDirectoriesOverride=(Join-Path $sandbox 'tauri')}
}
[IO.File]::WriteAllText($overlay,($configuration | ConvertTo-Json -Depth 10))
$oldKey=$env:TAURI_SIGNING_PRIVATE_KEY
$oldPassword=$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD
try {
    $env:TAURI_SIGNING_PRIVATE_KEY=$key
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD=''
    & npm --prefix (Join-Path $root 'frontend') run tauri build -- --config $overlay
    if ($LASTEXITCODE) { throw 'Build assinado de teste falhou.' }
    $installer=Join-Path $root "frontend/src-tauri/target/release/bundle/nsis/HORUS_${Version}_x64-setup.exe"
    $release=Join-Path $OutputDirectory $Version
    New-Item -ItemType Directory -Path $release -Force | Out-Null
    Copy-Item -LiteralPath $installer -Destination (Join-Path $release 'setup.exe')
    Copy-Item -LiteralPath ($installer+'.sig') -Destination (Join-Path $release 'setup.exe.sig')
    $manifest=@{version=$Version;notes='Protótipo local HOR-100; não distribuir';pub_date=[DateTime]::UtcNow.ToString('o');platforms=@{
        'windows-x86_64'=@{url="http://127.0.0.1:$Port/$Version/setup.exe";signature=[IO.File]::ReadAllText($installer+'.sig').Trim()}
    }}
    [IO.File]::WriteAllText((Join-Path $OutputDirectory 'latest.json'),($manifest | ConvertTo-Json -Depth 10))
    Get-FileHash -LiteralPath (Join-Path $release 'setup.exe') -Algorithm SHA256
} finally {
    $env:TAURI_SIGNING_PRIVATE_KEY=$oldKey
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD=$oldPassword
}
