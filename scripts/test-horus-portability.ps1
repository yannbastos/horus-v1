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
function orca {
  $global:LASTEXITCODE = 0
  if (-not $hor166OrcaFixture) { return 'SAIDA-SENSIVEL-SIMULADA' }
  $result = switch ($args[1]) {
    'run-show' { @{ run = $hor166OrcaFixture.runs[$args[3]] } }
    'list' { @{ terminals = $hor166OrcaFixture.terminals; truncated = $false } }
    'inbox' { @{ messages = $hor166OrcaFixture.mail[$args[3]]; truncated = $hor166OrcaFixture.truncated } }
    default { throw "Comando imprevisto no Orca simulado: $($args[1])" }
  }
  @{ ok = $true; result = $result } | ConvertTo-Json -Depth 8 -Compress
}

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
  Push-Location -LiteralPath $temp
  try { $dry = (& $bootstrap -WorkspacePath '../..' -DryRun *>&1 | Out-String) }
  finally { Pop-Location }
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
  Assert-True ($parsed.task -ceq 'HOR-999' -and $parsed.execution -ceq 'incerta' -and $parsed.mode -ceq 'maestro') 'exemplo canônico válido'
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
    @{ Text = $sample.Replace('"incerta"', '{'); Error = 'JSON' },
    @{ Text = $sample.Replace('["Verificar o escopo"]', '"Verificar o escopo"'); Error = 'Lista obrigatória' },
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

  $event = [regex]::Match($doc, '(?ms)^\[HORUS-COORDINATION v1\].*?^~~~\s*$').Value
  [IO.File]::WriteAllText($samplePath, $event)
  $opening = & $checkpoint -Path $samplePath -Coordination | ConvertFrom-Json
  Assert-True ($opening.kind -ceq 'decisão' -and $opening.state -ceq 'aberta') 'exemplo canônico de coordenação válido'
  $answer = $event.Replace('"state": "aberta"', '"state": "respondida"').Replace('"resolution": null', '"resolution": "Resposta simulada"').Replace('"evidence": []', '"evidence": ["Confirmação humana simulada"]')
  $cancelled = $answer.Replace('"respondida"', '"cancelada"')
  $failure = $event.Replace('"decisão"', '"falha"').Replace('"validar-ambiente"', '"entrega-incerta"').Replace('"maestro"', '"independente"')
  $failure = $failure.Replace('["Usar ambiente isolado", "Adiar a validação"]', '[]').Replace('"Usar ambiente isolado."', 'null')
  $hor166CommentExit = 0
  $hor166Comments = @($sample, $event, $event, $failure)
  $recovered = & $checkpoint -Issue 1 -Coordination | ConvertFrom-Json
  Assert-True (@($recovered | Where-Object kind -eq 'decisão').Count -eq 1) 'reconstrução apresenta uma pendência por chave'
  Assert-True (($recovered | Where-Object kind -eq 'falha').mode -ceq 'independente') 'reconstrução distingue frente independente de Maestro'
  $hor166Comments += @($answer, $answer)
  $recovered = & $checkpoint -Issue 1 -Coordination | ConvertFrom-Json
  Assert-True (($recovered | Where-Object kind -eq 'decisão').state -ceq 'respondida') 'resposta única elimina a pergunta da fila'
  $hor166Comments = @($event, $cancelled)
  $recovered = & $checkpoint -Issue 1 -Coordination | ConvertFrom-Json
  Assert-True ($recovered.state -ceq 'cancelada') 'cancelamento elimina a pergunta da fila'
  $hor166Comments = @($event, $answer, $cancelled)
  Assert-Rejected { & $checkpoint -Issue 1 -Coordination } 'conflitante' 'fechamentos contraditórios sinalizados'
  $hor166Comments = @($answer)
  Assert-Rejected { & $checkpoint -Issue 1 -Coordination } 'sem abertura' 'resposta órfã rejeitada'
  $hor166Comments = @($event, $answer.Replace('HOR-999','HOR-888'))
  Assert-Rejected { & $checkpoint -Issue 1 -Coordination } 'incompatível' 'resposta atribuída a outra Issue rejeitada'
  $hor166Comments = @($event.Replace('Validação da HOR-999', 'run_00abcd'))
  Assert-Rejected { & $checkpoint -Issue 1 -Coordination } 'identificador local' 'identificador local proibido nos eventos'
  $hor166Comments = @($event.Replace('[HORUS-COORDINATION v1]', '[HORUS-COORDINATION v2]'))
  Assert-Rejected { & $checkpoint -Issue 1 -Coordination } 'versão' 'versão de coordenação desconhecida interrompe reconstrução'
  $hor166Comments = @($sample)
  $empty = & $checkpoint -Issue 1 -Coordination
  Assert-True ($empty.Trim() -ceq '[]') 'Issue sem eventos retorna lista vazia'

  $verify = Join-Path $PSScriptRoot 'horus-supervisor.ps1'
  $gitRoot = Split-Path $PSScriptRoot -Parent
  $maestroHandle = 'term_00000000-0000-0000-0000-000000000001'
  $supervisorHandle = 'term_00000000-0000-0000-0000-000000000002'
  $question = @{
    id = 'msg_a'; from_handle = $maestroHandle; to_handle = 'run:run_b';
    type = 'question'; subject = 'pergunta: HOR-999/validar-ambiente'; thread_id = $null
  }
  $reply = @{
    id = 'msg_b'; from_handle = $supervisorHandle; to_handle = 'run:run_a';
    type = 'status'; subject = 'resposta: HOR-999/validar-ambiente'; thread_id = 'msg_a'
  }
  $hor166OrcaFixture = @{
    runs = @{
      run_a = @{ id = 'run_a'; coordinator_handle = $maestroHandle }
      run_b = @{ id = 'run_b'; coordinator_handle = $supervisorHandle }
    }
    terminals = @(
      @{ handle = $maestroHandle; connected = $true; worktreePath = $gitRoot },
      @{ handle = $supervisorHandle; connected = $true; worktreePath = $gitRoot }
    )
    mail = @{ 'run:run_a' = @($reply); 'run:run_b' = @($question) }
  }
  $questionArgs = @{ RunId = 'run_b'; FromRunId = 'run_a'; MessageId = 'msg_a'; Type = 'question'; Subject = $question.subject }
  $replyArgs = @{ RunId = 'run_a'; FromRunId = 'run_b'; MessageId = 'msg_b'; Type = 'status'; Subject = $reply.subject; ThreadId = 'msg_a' }
  $verified = & $verify VerifyDelivery @questionArgs | ConvertFrom-Json
  Assert-True ($verified.id -ceq 'msg_a') 'pergunta em caixa e com origem verificadas'
  $verified = & $verify VerifyDelivery @replyArgs | ConvertFrom-Json
  Assert-True ($verified.thread_id -ceq 'msg_a') 'resposta status verifica pergunta e destinatário original'
  $hor166OrcaFixture.truncated = $true
  Assert-Rejected { & $verify VerifyDelivery @replyArgs } 'Incomplete inbox' 'caixa incompleta não comprova unicidade'
  $hor166OrcaFixture.truncated = $false
  $hor166OrcaFixture.mail['run:run_a'] += $reply
  Assert-Rejected { & $verify VerifyDelivery @replyArgs } 'Unique delivery' 'encaminhamento duplicado sinalizado'
  $hor166OrcaFixture.mail['run:run_a'] = @($reply)
  $question.subject = 'pergunta: HOR-999/outra-chave'
  Assert-Rejected { & $verify VerifyDelivery @replyArgs } 'Original question' 'resposta ao thread de outra chave rejeitada'
  $question.subject = $questionArgs.Subject
  $reply.from_handle = $maestroHandle
  Assert-Rejected { & $verify VerifyDelivery @replyArgs } 'Unique delivery' 'remetente incompatível rejeitado'
  $hor166OrcaFixture.terminals[0].worktreePath = $workspace
  Assert-Rejected { & $verify VerifyRun -RunId run_a } 'not connected in a worktree' 'terminal de pasta ancestral não prova origem Git'
} finally {
  if ([IO.Directory]::Exists($temp) -and (Split-Path $temp -Leaf) -match '^horus-166-[a-f0-9]{32}$' -and
      (Split-Path $temp -Parent) -ieq [IO.Path]::GetTempPath().TrimEnd('\', '/')) {
    Remove-Item -LiteralPath $temp -Recurse -Force
  }
}
Write-Host "TOTAL: $script:passed testes aprovados."
