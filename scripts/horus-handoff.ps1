#requires -Version 5.1
<#
.SYNOPSIS
  Transfere o Run Orca de um Maestro para outro terminal local do mesmo worktree.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^term_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z')]
  [string]$PreviousTerminal,
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^run_[a-f0-9]+$')]
  [string]$RunId
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$orca = if ($env:ORCA_CLI_COMMAND) { $env:ORCA_CLI_COMMAND } else { 'orca' }

function Invoke-Orca {
  $output = & $orca @args --environment local --json
  $response = $output | Out-String | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or -not $response.ok) {
    throw "Orca: $($response.error.code) $($response.error.message)"
  }
  $response.result
}

$current = $env:ORCA_TERMINAL_HANDLE
if (-not $current -or $current -eq $PreviousTerminal) {
  throw 'Run from the new terminal, passing the old terminal handle and the Run ID.'
}
$terminals = (Invoke-Orca terminal list --worktree "path:$repo").terminals
foreach ($handle in $current, $PreviousTerminal) {
  if (-not ($terminals | Where-Object { $_.handle -eq $handle -and $_.connected })) {
    throw 'Both terminals must be connected in this worktree.'
  }
}
if ((Invoke-Orca orchestration run-current --from $PreviousTerminal --full).run.id -ne $RunId -or
    (Invoke-Orca orchestration run-current --from $current --full).run) {
  throw 'The old terminal must own the requested Run and the new terminal must be free.'
}
$taken = (Invoke-Orca orchestration run-use --id $RunId --from $current --full).run
if ($taken.coordinator_handle -ne $current -or (Invoke-Orca orchestration run-current --from $current --full).run.id -ne $RunId) {
  throw 'Run ownership was not verified. Inspect both terminals before retrying.'
}
$taken | ConvertTo-Json -Compress
