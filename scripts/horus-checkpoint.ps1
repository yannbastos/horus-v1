#requires -Version 5.1
[CmdletBinding(DefaultParameterSetName = 'Validate')]
param(
  [Parameter(Mandatory = $true, ParameterSetName = 'Validate')]
  [string]$Path,
  [Parameter(Mandatory = $true, ParameterSetName = 'Issue')]
  [ValidateRange(1, 2147483647)]
  [int]$Issue,
  [switch]$Coordination,
  [switch]$RequireReleased
)

$ErrorActionPreference = 'Stop'
$prefix = if ($Coordination) { '[HORUS-COORDINATION' } else { '[HORUS-CHECKPOINT' }
$marker = "$prefix v1]"
$parseOptions = @{}
if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $parseOptions.DateKind = 'String' }

function Read-Record([string]$Body, [string[]]$Fields, [string]$ExpectedTask) {
  $lines = $Body.TrimEnd() -split '\r?\n'
  if ($lines.Count -lt 5 -or $lines[0] -cne $marker -or $lines[1] -cne '' -or
      $lines[2] -cne '~~~json' -or $lines[-1] -cne '~~~') {
    throw 'Formato de checkpoint inválido ou versão desconhecida. Confira o comentário mais recente.'
  }
  $json = $lines[3..($lines.Count - 2)] -join "`n"
  try { $record = ConvertFrom-Json -InputObject $json @parseOptions } catch { throw 'JSON do checkpoint inválido.' }
  if ($record -isnot [pscustomobject] -or
      @(Compare-Object $Fields @($record.PSObject.Properties.Name) -CaseSensitive).Count) {
    throw 'Campos do checkpoint inválidos.'
  }
  if ($record.task -isnot [string] -or $record.task -cnotmatch '^HOR-[0-9]+$' -or
      ($expectedTask -and $record.task -cne $expectedTask)) {
    throw 'Identificador incompatível com a Issue.'
  }
  $timestamp = [datetime]::MinValue
  if ($record.timestamp -isnot [string] -or -not [datetime]::TryParseExact($record.timestamp, "yyyy-MM-dd'T'HH:mm:ss'Z'",
      [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$timestamp)) {
    throw 'Use timestamp UTC no formato yyyy-MM-ddTHH:mm:ssZ.'
  }
  if (($record | ConvertTo-Json -Depth 5 -Compress) -match '(?i)\b(?:run_[a-f0-9]+|term_[a-f0-9-]{36}|gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]+|sk-[a-z0-9_-]{20,})\b|-----BEGIN [A-Z ]*PRIVATE KEY-----') {
    throw 'Checkpoint contém possível credencial ou identificador local.'
  }
  $record
}

function Assert-RecordValues($Record, [string[]]$Text, [string[]]$Lists) {
  foreach ($field in $Text) {
    if ($Record.$field -isnot [string] -or [string]::IsNullOrWhiteSpace($Record.$field)) {
      throw "Campo obrigatório inválido: $field."
    }
  }
  foreach ($field in $Lists) {
    if ($Record.$field -isnot [array]) { throw "Lista obrigatória inválida: $field." }
    foreach ($entry in $Record.$field) {
      if ($entry -isnot [string] -or [string]::IsNullOrWhiteSpace($entry)) {
        throw "Entrada inválida na lista $field."
      }
    }
  }
}

function Read-Coordination([string]$Body, [string]$ExpectedTask) {
  $event = Read-Record $Body @('task', 'key', 'kind', 'state', 'owner', 'scope', 'mode', 'timestamp',
    'details', 'options', 'recommendation', 'resolution', 'evidence') $ExpectedTask
  Assert-RecordValues $event @('key', 'kind', 'state', 'owner', 'scope', 'mode', 'details') @('options', 'evidence')
  if ($event.key -cnotmatch '^[a-z0-9]+(?:-[a-z0-9]+)*$' -or $event.mode -cnotin 'maestro', 'independente', 'supervisor' -or
      $event.kind -cnotin 'decisão', 'bloqueio', 'falha') {
    throw 'Chave, tipo ou papel de coordenação inválido.'
  }
  if ($event.kind -ceq 'decisão') {
    Assert-RecordValues $event @('recommendation') @()
    if ($event.options.Count -lt 2 -or $event.state -cnotin 'aberta', 'respondida', 'cancelada') {
      throw 'Decisão requer duas opções e um estado válido.'
    }
  } elseif ($event.state -cnotin 'aberta', 'resolvida', 'cancelada' -or $null -ne $event.recommendation -or $event.options.Count) {
    throw 'Bloqueio ou falha deve usar estado válido e opções vazias.'
  }
  if ($event.state -ceq 'aberta') {
    if ($null -ne $event.resolution) { throw 'Evento aberto deve ter resolução nula.' }
  } else {
    Assert-RecordValues $event @('resolution') @()
    if (-not $event.evidence.Count) { throw 'Encerramento requer evidência.' }
  }
  $event
}

if ($Coordination -and $RequireReleased) { throw '-RequireReleased aplica-se ao checkpoint de execução.' }

if ($PSCmdlet.ParameterSetName -eq 'Issue') {
  $repository = 'repos/yannbastos/horus-management'
  $ErrorActionPreference = 'Continue'
  try {
    $title = & gh api "$repository/issues/$Issue" --jq '.title' 2>$null
    if ($LASTEXITCODE -ne 0 -or $title -cnotmatch '\[(HOR-[0-9]+)\]') {
      throw 'Issue inacessível ou sem identificador HOR no título.'
    }
    $expectedTask = $Matches[1]
    # Base64 mantém um comentário por linha, inclusive no Windows PowerShell 5.1.
    $comments = @(& gh api --paginate "$repository/issues/$Issue/comments?per_page=100" --jq '.[] | .body | @base64' 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível consultar todos os comentários da Issue.' }
  } finally { $ErrorActionPreference = 'Stop' }
} else {
  $body = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $Path).ProviderPath)
}

if ($Coordination) {
  if ($PSCmdlet.ParameterSetName -eq 'Validate') {
    Read-Coordination $body | ConvertTo-Json -Depth 5
    return
  }
  $byKey = [ordered]@{}
  foreach ($encoded in $comments) {
    $candidate = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
    if (-not $candidate.StartsWith($prefix, [StringComparison]::Ordinal)) { continue }
    $event = Read-Coordination $candidate $expectedTask
    $previous = $byKey[$event.key]
    if ($previous) {
      if (($previous | ConvertTo-Json -Compress -Depth 5) -ceq ($event | ConvertTo-Json -Compress -Depth 5)) { continue }
      if ($previous.kind -cne $event.kind -or $previous.scope -cne $event.scope -or $previous.mode -cne $event.mode -or
          $previous.state -cne 'aberta') {
        throw "Histórico conflitante na chave $($event.key). Confira autor e evidências na Issue."
      }
    } elseif ($event.state -cne 'aberta') {
      throw "Encerramento sem abertura na chave $($event.key). Confira a Issue."
    }
    $byKey[$event.key] = $event
  }
  ConvertTo-Json -InputObject @($byKey.Values | ForEach-Object { $_ }) -Depth 5 -Compress
  return
}

if ($PSCmdlet.ParameterSetName -eq 'Issue') {
  for ($i = $comments.Count - 1; $i -ge 0; $i--) {
    $candidate = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($comments[$i]))
    if ($candidate.StartsWith($prefix, [StringComparison]::Ordinal)) {
      $body = $candidate
      break
    }
  }
  if (-not $body) { throw 'Issue sem checkpoint. Confirme a execução antes de redistribuir.' }
}

$checkpoint = Read-Record $body @('task', 'objective', 'owner', 'status', 'timestamp', 'branch', 'commits', 'pr',
  'done', 'evidence', 'tests', 'pending', 'dependencies', 'blockers', 'decisions', 'next', 'execution', 'mode') $expectedTask
Assert-RecordValues $checkpoint @('objective', 'owner', 'status', 'next', 'execution', 'mode') @(
  'commits', 'done', 'evidence', 'tests', 'pending', 'dependencies', 'blockers', 'decisions')
if ($checkpoint.status -cnotin 'Backlog', 'Pronto', 'Em andamento', 'Em revisão', 'Bloqueado', 'Concluído' -or
    $checkpoint.execution -cnotin 'livre', 'em execução', 'incerta' -or
    $checkpoint.mode -cnotin 'maestro', 'independente', 'supervisor') {
  throw 'Status ou estado de execução inválido.'
}
if ($null -ne $checkpoint.branch) {
  if ($checkpoint.branch -isnot [string] -or $checkpoint.branch -match '^(?:-|@\{|HEAD$)') { throw 'Branch inválida.' }
  & git check-ref-format "refs/heads/$($checkpoint.branch)" *> $null
  if ($LASTEXITCODE -ne 0) { throw 'Branch inválida.' }
}
if ($null -ne $checkpoint.pr -and ($checkpoint.pr -isnot [string] -or
    $checkpoint.pr -cnotmatch '^https://github\.com/yannbastos/horus/pull/[1-9][0-9]*$')) {
  throw 'URL de PR inválida.'
}
foreach ($commit in $checkpoint.commits) {
  if ($commit -cnotmatch '^[0-9a-f]{40}$') { throw 'Informe o SHA completo de cada commit.' }
}
if ($RequireReleased -and $checkpoint.execution -cne 'livre') {
  throw 'Escopo sem liberação registrada. Reconcilie a execução antes de redistribuir.'
}
$checkpoint | ConvertTo-Json -Depth 5
