param([string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$frontend = Join-Path $root 'frontend'
$config = Get-Content (Join-Path $frontend 'src-tauri/tauri.conf.json') -Raw | ConvertFrom-Json
$version = [version]$config.version
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root 'installer-test-artifacts' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$output = (Resolve-Path -LiteralPath $OutputDirectory).Path
$override = Join-Path ([IO.Path]::GetTempPath()) ('horus-build-version-' + [guid]::NewGuid() + '.json')
try {
    foreach ($number in @($config.version, "$($version.Major).$($version.Minor).$($version.Build + 1)")) {
        # Override de build: nenhum manifesto versionado é modificado.
        [IO.File]::WriteAllText($override, (@{ version = $number; productName = 'HORUS Test'; identifier = 'com.horus.installer-test' } | ConvertTo-Json))
        & npm --prefix $frontend run tauri build -- --config $override
        if ($LASTEXITCODE) { throw "Build $number falhou." }
        $installer = Join-Path $frontend "src-tauri/target/release/bundle/nsis/HORUS Test_${number}_x64-setup.exe"
        Copy-Item -LiteralPath $installer -Destination $output
        Get-FileHash -LiteralPath (Join-Path $output (Split-Path $installer -Leaf)) -Algorithm SHA256
    }
} finally { Remove-Item -LiteralPath $override -Force }
