#requires -Version 5.1
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('Find', 'Start', 'Handoff')]
  [string]$Action,
  [string]$PreviousTerminal,
  [ValidatePattern('^run_[a-f0-9]+$')]
  [string]$RunId
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$orca = if ($env:ORCA_CLI_COMMAND) { $env:ORCA_CLI_COMMAND } else { 'orca' }

function Get-GitPath($path, $flag) {
  $value = & git -C $path rev-parse --path-format=absolute $flag 2>$null
  if ($LASTEXITCODE -eq 0) {
    [IO.Path]::GetFullPath($value).TrimEnd('\', '/').ToUpperInvariant()
  }
}

function Invoke-Orca {
  $output = & $orca @args --environment local --json
  $response = $output | Out-String | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or -not $response.ok) {
    throw "Orca: $($response.error.code) $($response.error.message)"
  }
  $response.result
}

$common = Get-GitPath $repo '--git-common-dir'
$root = Get-GitPath $repo '--show-toplevel'
if (-not $common -or -not $root) { throw 'Git repository required.' }
$sha = [Security.Cryptography.SHA256]::Create()
try {
  $key = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($common))).Replace('-', '')
} finally {
  $sha.Dispose()
}
$objective = "Horus Supervisor v1 $key"

if ($Action -eq 'Handoff') {
  $current = $env:ORCA_TERMINAL_HANDLE
  if (-not $current -or -not $PreviousTerminal -or $current -eq $PreviousTerminal -or -not $RunId) {
    throw 'Run Handoff from the new terminal with the old terminal and Run ID.'
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
  return
}

# The same mutex covers all worktrees of this local Git repository.
$mutex = [Threading.Mutex]::new($false, "HorusSupervisor-$key")
$held = $false
try {
  try { $held = $mutex.WaitOne(5000) } catch [Threading.AbandonedMutexException] { $held = $true }
  if (-not $held) { throw 'Supervisor activation is already in progress.' }
  $matches = @((Invoke-Orca orchestration run-list).runs | Where-Object { $_.objective -ceq $objective })
  if ($matches.Count -gt 1) { throw 'Multiple Supervisor Runs found. Inspect Orca before proceeding.' }
  $registered = if ($matches.Count) { (Invoke-Orca orchestration run-show --id $matches[0].id --full).run }
  $catalog = Invoke-Orca terminal list --limit 10000
  if ($catalog.truncated) { throw 'Incomplete local terminal list.' }
  $terminals = $catalog.terminals
  $owner = $terminals | Where-Object { $_.handle -eq $registered.coordinator_handle -and $_.connected }
  if ($owner -and (Get-GitPath $owner.worktreePath '--git-common-dir') -ne $common) {
    throw 'Supervisor Run owner belongs to another repository.'
  }
  if ($Action -eq 'Find') {
    if ($owner) { $registered.id }
    return
  }

  $current = $env:ORCA_TERMINAL_HANDLE
  $caller = $terminals | Where-Object { $_.handle -eq $current -and $_.connected }
  if (-not $caller -or (Get-GitPath $caller.worktreePath '--show-toplevel') -ne $root) {
    throw 'Activate the Supervisor from a connected Orca terminal in this worktree.'
  }
  if ($owner -and $owner.handle -ne $current) { throw 'A Supervisor already owns this Run. Use Handoff.' }
  $bound = (Invoke-Orca orchestration run-current --from $current --full).run
  if ($bound -and $bound.id -ne $registered.id) { throw 'Use a terminal without a Maestro Run.' }
  if ($bound) { $bound.id }
  elseif ($registered) { (Invoke-Orca orchestration run-use --id $registered.id --from $current --full).run.id }
  else { (Invoke-Orca orchestration run-create --objective $objective --from $current --full).run.id }
} finally {
  if ($held) { $mutex.ReleaseMutex() }
  $mutex.Dispose()
}
