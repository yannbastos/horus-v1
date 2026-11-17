$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$python = Join-Path $root 'backend/.venv/Scripts/python.exe'
$tests = @(Get-ChildItem (Join-Path $root 'backend/tests') -Filter 'test_*.py' -File -Recurse | Sort-Object FullName)
foreach ($pattern in @('test_network_inventory*', 'test_api_contract_snapshot*', 'test_supply_chain*', 'test_endpoint_auth*')) {
    if (-not @($tests | Where-Object Name -Like $pattern).Count) { throw "Teste transversal ausente: $pattern" }
}
# Um processo por módulo evita contaminação de globals/env entre arquivos.
$failed = @()
foreach ($test in $tests) {
    & $python -m pytest $test.FullName -q --tb=short
    if ($LASTEXITCODE) { $failed += $test.Name }
}
if ($failed.Count) { throw "Módulos com falha: $($failed -join ', ')" }
Write-Output "$($tests.Count) módulos backend aprovados em processos separados, incluindo os transversais."
