param()
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$directory = Join-Path $env:LOCALAPPDATA 'Horus/release-keys/test'
$key = Join-Path $directory 'updater-test.key'
# Nunca aceitar outro canal/caminho, sobrescrever ou imprimir a chave privada.
if ((Test-Path -LiteralPath $key) -or (Test-Path -LiteralPath ($key+'.pub'))) { throw 'Par de teste já existe; preservado.' }
if ([IO.Path]::GetFullPath($directory).StartsWith($root.TrimEnd('\')+'\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Chaves precisam ficar fora do repositório.' }
$ancestor = $directory
while ($ancestor) {
    if ((Test-Path -LiteralPath $ancestor) -and ((Get-Item -LiteralPath $ancestor).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Pasta de chaves não pode atravessar link/junction.' }
    $ancestor = Split-Path $ancestor -Parent
}
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$cli = Join-Path $root 'frontend/node_modules/@tauri-apps/cli/tauri.js'
# O signer imprime material privado; descartar TODOS os streams, inclusive em falhas.
& node $cli signer generate --ci --write-keys $key *> $null
if ($LASTEXITCODE) { throw 'Geração de chave TESTE falhou; saída omitida.' }
if (-not (Test-Path -LiteralPath ($key+'.pub'))) { throw 'Chave pública de teste ausente.' }
[Console]::WriteLine([IO.File]::ReadAllText($key+'.pub').Trim())
