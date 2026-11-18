#requires -Version 5.1
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('Find', 'Start', 'Handoff', 'VerifyRun', 'VerifyDelivery', 'CheckTerminal')]
  [string]$Action,
  [ValidatePattern('^term_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z')]
  [string]$PreviousTerminal,
  [ValidatePattern('^run_[a-f0-9]+$')]
  [string]$RunId,
  [ValidatePattern('^run_[a-f0-9]+$')]
  [string]$FromRunId,
  [ValidatePattern('^msg_[a-f0-9]+$')]
  [string]$MessageId,
  [ValidatePattern('^msg_[a-f0-9]+$')]
  [string]$ThreadId,
  [ValidateSet('question', 'status')]
  [string]$Type,
  [string]$Subject
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$orca = if ($env:ORCA_CLI_COMMAND) { $env:ORCA_CLI_COMMAND } else { 'orca' }

function Get-GitPath($path, $flag) {
  $ErrorActionPreference = 'Continue'
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

function Assert-CheckoutTerminal($Terminals) {
  $handle = $env:ORCA_TERMINAL_HANDLE
  $callers = @($Terminals | Where-Object { $handle -and $_.handle -ceq $handle -and $_.connected })
  if ($callers.Count -ne 1 -or -not $callers[0].worktreePath -or
      (Get-GitPath $callers[0].worktreePath '--show-toplevel') -ne $root -or
      [IO.Path]::GetFullPath($callers[0].worktreePath).TrimEnd('\', '/').ToUpperInvariant() -ne $root) {
    throw 'Open a NEW Orca terminal in this Hórus checkout/worktree. A parent workspace or changing directory in its terminal does not bind the terminal to Git.'
  }
}

if ($Action -eq 'CheckTerminal') {
  $catalog = Invoke-Orca terminal list --limit 10000
  if ($catalog.truncated) { throw 'Incomplete local terminal list.' }
  Assert-CheckoutTerminal $catalog.terminals
  return
}

function Get-VerifiedRun([string]$Id) {
  $current = $env:ORCA_TERMINAL_HANDLE
  if (-not $Id -and -not $current) { throw 'Use a connected Orca terminal or specify a local Run ID.' }
  $run = if ($Id) {
    (Invoke-Orca orchestration run-show --id $Id --full).run
  } else {
    (Invoke-Orca orchestration run-current --from $current --full).run
  }
  if (-not $run.id -or -not $run.coordinator_handle -or
      ($Id -and $run.id -cne $Id) -or (-not $Id -and $run.coordinator_handle -ne $current)) {
    throw 'Run coordinator could not be verified.'
  }
  $catalog = Invoke-Orca terminal list --limit 10000
  if ($catalog.truncated) { throw 'Incomplete local terminal list.' }
  $owners = @($catalog.terminals | Where-Object { $_.handle -eq $run.coordinator_handle -and $_.connected })
  if ($owners.Count -ne 1 -or -not $owners[0].worktreePath -or
      (Get-GitPath $owners[0].worktreePath '--git-common-dir') -ne $common -or
      (Get-GitPath $owners[0].worktreePath '--show-toplevel') -ne
        [IO.Path]::GetFullPath($owners[0].worktreePath).TrimEnd('\', '/').ToUpperInvariant()) {
    throw 'Run coordinator is not connected in a worktree of this Git repository.'
  }
  [pscustomobject]@{ run = $run.id; coordinator = $run.coordinator_handle; worktree = $owners[0].worktreePath }
}

if ($Action -eq 'VerifyRun') {
  Get-VerifiedRun $RunId | ConvertTo-Json -Compress
  return
}

function Get-VerifiedInbox([string]$Mailbox) {
  $limit = 1000
  $inbox = Invoke-Orca orchestration inbox --terminal $Mailbox --limit $limit --full
  if ($inbox.truncated -or $inbox.count -ge $limit -or @($inbox.messages | Where-Object { $_ }).Count -ge $limit) {
    throw 'Incomplete inbox history. Reconcile the decision on GitHub before acting or retrying.'
  }
  $inbox
}

if ($Action -eq 'VerifyDelivery') {
  if (-not $RunId -or -not $FromRunId -or $RunId -eq $FromRunId -or -not $MessageId -or
      -not $Subject -or -not $Type -or (($Type -eq 'status') -ne [bool]$ThreadId)) {
    throw 'Provide both Runs, message ID, subject, type and a question thread ID for status.'
  }
  if ($Subject -cnotmatch '^(pergunta|resposta|decisão|recebida): (HOR-[0-9]+/[a-z0-9]+(?:-[a-z0-9]+)*)$') {
    throw 'Use an exact coordination subject with the Issue and stable key.'
  }
  $actionName = $Matches[1]
  $key = $Matches[2]
  if (($actionName -ceq 'pergunta') -ne ($Type -eq 'question')) { throw 'Subject and type do not match.' }

  $sender = Get-VerifiedRun $FromRunId
  $recipient = Get-VerifiedRun $RunId
  $mailbox = "run:$($recipient.run)"
  $inbox = Get-VerifiedInbox $mailbox
  $found = @($inbox.messages | Where-Object {
    $_.from_handle -ceq $sender.coordinator -and $_.to_handle -ceq $mailbox -and $_.type -ceq $Type -and
      $_.subject -ceq $Subject -and [string]$_.thread_id -ceq [string]$ThreadId
  })
  if ($found.Count -ne 1 -or $found[0].id -cne $MessageId) {
    throw 'Unique delivery not verified in the recipient mailbox. Reconcile before acting or retrying.'
  }

  if ($ThreadId) {
    $questionSender = $sender.coordinator
    $questionMailbox = $mailbox
    $questionInbox = $inbox
    if ($actionName -ceq 'resposta') {
      $questionSender = $recipient.coordinator
      $questionMailbox = "run:$($sender.run)"
      $questionInbox = Get-VerifiedInbox $questionMailbox
    }
    $question = @($questionInbox.messages | Where-Object {
      $_.id -ceq $ThreadId -and $_.from_handle -ceq $questionSender -and $_.to_handle -ceq $questionMailbox -and
        $_.type -ceq 'question' -and $_.subject -ceq "pergunta: $key" -and -not $_.thread_id
    })
    if ($question.Count -ne 1) { throw 'Original question not verified for this thread and pair of Runs.' }
  }
  $found[0] | Select-Object id, from_handle, to_handle, type, subject, thread_id | ConvertTo-Json -Compress
  return
}

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
  Assert-CheckoutTerminal $terminals
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
