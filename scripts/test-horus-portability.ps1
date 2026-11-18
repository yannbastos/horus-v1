#requires -Version 5.1
$ErrorActionPreference = 'Stop'
$script:passed = 0

function Assert-True($Condition, [string]$Name) {
  if (-not $Condition) { throw "FAIL: $Name" }
  $script:passed++
  Write-Host "PASS $Name"
}

function Assert-Rejected([scriptblock]$Action, [string]$Pattern, [string]$Name) {
  $message = ''
  try { & $Action | Out-Null } catch { $message = $_.Exception.Message }
  Assert-True ($message -match $Pattern) $Name
}

function gh {
  $global:LASTEXITCODE = 0
  if ($args -contains '.title') { return '[HOR-999] Fixture de retomada' }
  if ($args -contains '--paginate') {
    foreach ($comment in $hor166Comments) {
      [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($comment))
    }
    $global:LASTEXITCODE = $hor166CommentExit
    return
  }
  'SAIDA-SENSIVEL-SIMULADA'
}

function claude { $global:LASTEXITCODE = 0; 'SAIDA-SENSIVEL-SIMULADA' }
function codex { $global:LASTEXITCODE = 1; 'SAIDA-SENSIVEL-SIMULADA' }
function agy {}
function orca { $global:LASTEXITCODE = 0; 'SAIDA-SENSIVEL-SIMULADA' }

$temp = [IO.Path]::Combine([IO.Path]::GetTempPath(), "horus-166-$([guid]::NewGuid().ToString('N'))")
try {
  $workspace = Join-Path $temp 'espaço [A] &'
  $parent = Join-Path $workspace 'nível [B]'
  $repo = Join-Path $parent 'Hórus # app'
  $scripts = Join-Path $repo 'scripts'
  [IO.Directory]::CreateDirectory($scripts) | Out-Null
  & git -C $repo init --quiet
  Assert-True ($LASTEXITCODE -eq 0) 'checkout descartável inicializado'
  foreach ($name in 'bootstrap-horus-orca.ps1', 'install-workspace-skill-bridge.ps1') {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $scripts
  }
  foreach ($name in 'horus-maestro', 'supervisar') {
    $relative = ".claude/skills/$name"
    $source = Join-Path (Split-Path $PSScriptRoot -Parent) "$relative/SKILL.md"
    $target = [IO.Directory]::CreateDirectory((Join-Path $repo $relative))
    Copy-Item -LiteralPath $source -Destination $target.FullName
  }

  $bootstrap = Join-Path $scripts 'bootstrap-horus-orca.ps1'
  $dry = (& $bootstrap -WorkspacePath $workspace -DryRun *>&1 | Out-String)
  Assert-True ($dry -match 'Gravaria a ponte' -and -not [IO.Directory]::Exists((Join-Path $workspace '.claude'))) 'simulação sem gravação'
  Assert-True ($dry -notmatch 'SAIDA-SENSIVEL-SIMULADA' -and $dry -match 'codex: autenticação não confirmada') 'probes ocultam saídas e sinalizam login'
  $rootMode = (& $bootstrap -WorkspacePath $repo *>&1 | Out-String)
  Assert-True ($rootMode -match 'diretamente na raiz: /supervisar' -and $rootMode -match 'diretamente na raiz: /horus-maestro') 'skills nativas na raiz'
  & $bootstrap -WorkspacePath $workspace *> $null
  & $bootstrap *> $null
  foreach ($name in 'horus-maestro', 'supervisar') {
    $relative = ".claude/skills/$name/SKILL.md"
    foreach ($base in $parent, $workspace) {
      $installed = Join-Path $base $relative
      $content = [IO.File]::ReadAllText($installed)
      $reference = [regex]::Match($content, 'Leia e siga (.+?/SKILL\.md)').Groups[1].Value
      Assert-True ($reference -and [IO.File]::Exists((Join-Path $base $reference)) -and
        $content -notmatch 'SAIDA-SENSIVEL-SIMULADA') "descoberta de $name em $([IO.Path]::GetFileName($base))"
    }
  }
  $again = (& $bootstrap -WorkspacePath $workspace *>&1 | Out-String)
  Assert-True ([regex]::Matches($again, 'sem alterações').Count -eq 2) 'bootstrap idempotente'
  $manual = Join-Path $workspace '.claude/skills/supervisar/SKILL.md'
  [IO.File]::WriteAllText($manual, 'skill manual')
  Assert-Rejected { & $bootstrap -WorkspacePath $workspace } 'não é uma ponte' 'colisão com configuração manual'
  Assert-True ([IO.File]::ReadAllText($manual) -ceq 'skill manual') 'configuração manual preservada'

  $checkpoint = Join-Path $PSScriptRoot 'horus-checkpoint.ps1'
  $doc = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '../docs/RETOMADA-MAESTRO.md'))
  $sample = [regex]::Match($doc, '(?ms)^\[HORUS-CHECKPOINT v1\].*?^~~~\s*$').Value
  $samplePath = Join-Path $temp 'checkpoint [teste].md'
  [IO.File]::WriteAllText($samplePath, $sample)
  $parsed = & $checkpoint -Path $samplePath | ConvertFrom-Json
  Assert-True ($parsed.task -ceq 'HOR-999' -and $parsed.execution -ceq 'incerta') 'exemplo canônico válido'
  Assert-Rejected { & $checkpoint -Path $samplePath -RequireReleased } 'sem liberação' 'estado incerto bloqueia redistribuição'
  $released = $sample.Replace('"incerta"', '"livre"')
  $hor166CommentExit = 0
  $hor166Comments = @($sample, $released, 'Comentário humano posterior')
  $latest = & $checkpoint -Issue 1 -RequireReleased | ConvertFrom-Json
  Assert-True ($latest.execution -ceq 'livre') 'nova sessão recupera liberação do checkpoint mais recente pela Issue'
  foreach ($bad in @(
    @{ Text = $sample.Replace('v1]', 'v2]'); Error = 'versão' },
    @{ Text = $sample.Replace('"incerta"', '"em execução"'); Error = 'sem liberação' },
    @{ Text = $sample.Replace('HOR-999', 'HOR-888'); Error = 'incompatível' },
    @{ Text = $sample.Replace('2026-11-18', '2026-02-31'); Error = 'timestamp' },
    @{ Text = $sample.Replace('"branch": null', '"branch": "bad ref"'); Error = 'Branch inválida' },
    @{ Text = $sample.Replace('"branch": null', '"branch": "@{-1}"'); Error = 'Branch inválida' },
    @{ Text = $sample.Replace('"incerta"', ''); Error = 'JSON' },
    @{ Text = $sample.Replace('"pending": [', '"pending": "'); Error = 'JSON' },
    @{ Text = $sample.Replace('"task": "HOR-999",', ''); Error = 'Campos' },
    @{ Text = $sample.Replace('Objetivo aprovado na Issue', ('ghp_' + ('x' * 32))); Error = 'credencial' },
    @{ Text = $sample.Replace('Objetivo aprovado na Issue', 'run_0123456789abcdef'); Error = 'identificador local' }
  )) {
    $hor166Comments = @($released, $bad.Text)
    Assert-Rejected { & $checkpoint -Issue 1 -RequireReleased } $bad.Error "checkpoint mais recente rejeitado ($($bad.Error))"
  }
  $hor166Comments = @('Comentário sem marcador')
  Assert-Rejected { & $checkpoint -Issue 1 -RequireReleased } 'sem checkpoint' 'Issue sem checkpoint bloqueia redistribuição'
  $hor166CommentExit = 1
  $hor166Comments = @($released)
  Assert-Rejected { & $checkpoint -Issue 1 -RequireReleased } 'consultar todos' 'falha após página parcial não libera escopo'
} finally {
  if ([IO.Directory]::Exists($temp) -and (Split-Path $temp -Leaf) -match '^horus-166-[a-f0-9]{32}$' -and
      (Split-Path $temp -Parent) -ieq [IO.Path]::GetTempPath().TrimEnd('\', '/')) {
    Remove-Item -LiteralPath $temp -Recurse -Force
  }
}
Write-Host "TOTAL: $script:passed testes aprovados."
