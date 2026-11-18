#requires -Version 5.1
[CmdletBinding(DefaultParameterSetName = 'Validate')]
param(
  [Parameter(Mandatory = $true, ParameterSetName = 'Validate')]
  [string]$Path,
  [Parameter(Mandatory = $true, ParameterSetName = 'Issue')]
  [ValidateRange(1, 2147483647)]
  [int]$Issue,
  [switch]$RequireReleased
)

$ErrorActionPreference = 'Stop'
$marker = '[HORUS-CHECKPOINT v1]'
$fields = @('task', 'objective', 'owner', 'status', 'timestamp', 'branch', 'commits', 'pr',
  'done', 'evidence', 'tests', 'pending', 'dependencies', 'blockers', 'decisions', 'next', 'execution')

if ($PSCmdlet.ParameterSetName -eq 'Issue') {
  $repository = 'repos/yannbastos/horus-management'
  $ErrorActionPreference = 'Continue'
  try {
    $title = & gh api "$repository/issues/$Issue" --jq '.title' 2>$null
    if ($LASTEXITCODE -ne 0 -or $title -cnotmatch '\[(HOR-[0-9]+)\]') {
      throw 'Issue inacessível ou sem identificador HOR no título.'
    }
    $expectedTask = $Matches[1]
    # Base64 mantém cada comentário em uma linha e evita problemas de codificação no PowerShell 5.1.
    $comments = @(& gh api --paginate "$repository/issues/$Issue/comments?per_page=100" --jq '.[] | .body | @base64' 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Não foi possível consultar todos os comentários da Issue.' }
  } finally { $ErrorActionPreference = 'Stop' }
  $body = $null
  for ($i = $comments.Count - 1; $i -ge 0; $i--) {
    $candidate = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($comments[$i]))
    if ($candidate.StartsWith('[HORUS-CHECKPOINT', [StringComparison]::Ordinal)) {
      $body = $candidate
      break
    }
  }
  if (-not $body) { throw 'Issue sem checkpoint. Confirme a execução antes de redistribuir.' }
} else {
  $body = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $Path).ProviderPath)
}

$lines = $body.TrimEnd() -split '\r?\n'
if ($lines.Count -lt 5 -or $lines[0] -cne $marker -or $lines[1] -cne '' -or $lines[2] -cne '~~~json' -or $lines[-1] -cne '~~~') {
  throw 'Formato de checkpoint inválido ou versão desconhecida. Confira o comentário mais recente.'
}
$json = $lines[3..($lines.Count - 2)] -join "`n"
$parseOptions = @{}
if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $parseOptions.DateKind = 'String' }
try { $checkpoint = ConvertFrom-Json -InputObject $json @parseOptions } catch { throw 'JSON do checkpoint inválido.' }
if ($checkpoint -isnot [pscustomobject] -or
    @(Compare-Object $fields @($checkpoint.PSObject.Properties.Name) -CaseSensitive).Count) {
  throw 'Campos do checkpoint inválidos.'
}

foreach ($field in 'task', 'objective', 'owner', 'status', 'timestamp', 'next', 'execution') {
  if ($checkpoint.$field -isnot [string] -or [string]::IsNullOrWhiteSpace($checkpoint.$field)) {
    throw "Campo obrigatório inválido: $field."
  }
}
foreach ($field in 'commits', 'done', 'evidence', 'tests', 'pending', 'dependencies', 'blockers', 'decisions') {
  if ($checkpoint.$field -isnot [array]) { throw "Lista obrigatória inválida: $field." }
  foreach ($entry in $checkpoint.$field) {
    if ($entry -isnot [string] -or [string]::IsNullOrWhiteSpace($entry)) {
      throw "Entrada inválida na lista $field."
    }
  }
}
if ($checkpoint.task -cnotmatch '^HOR-[0-9]+$' -or ($expectedTask -and $checkpoint.task -cne $expectedTask)) {
  throw 'Identificador incompatível com a Issue.'
}
if ($checkpoint.status -cnotin 'Backlog', 'Pronto', 'Em andamento', 'Em revisão', 'Bloqueado', 'Concluído' -or
    $checkpoint.execution -cnotin 'livre', 'em execução', 'incerta') {
  throw 'Status ou estado de execução inválido.'
}
$timestamp = [datetime]::MinValue
if (-not [datetime]::TryParseExact($checkpoint.timestamp, "yyyy-MM-dd'T'HH:mm:ss'Z'",
    [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$timestamp)) {
  throw 'Use timestamp UTC no formato yyyy-MM-ddTHH:mm:ssZ.'
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
if (($checkpoint | ConvertTo-Json -Depth 3 -Compress) -match '(?i)\b(?:run_[a-f0-9]+|term_[a-f0-9-]{36}|gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]+|sk-[a-z0-9_-]{20,})\b|-----BEGIN [A-Z ]*PRIVATE KEY-----') {
  throw 'Checkpoint contém possível credencial ou identificador local.'
}
if ($RequireReleased -and $checkpoint.execution -cne 'livre') {
  throw 'Escopo sem liberação registrada. Reconcilie a execução antes de redistribuir.'
}
$json
