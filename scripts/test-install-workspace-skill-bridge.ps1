#requires -Version 5.1
# Testes focados para install-workspace-skill-bridge.ps1 em pastas temporárias isoladas.
$ErrorActionPreference = 'Stop'

$scriptSource = Join-Path $PSScriptRoot 'install-workspace-skill-bridge.ps1'
if (-not (Test-Path -LiteralPath $scriptSource -PathType Leaf)) {
  throw "Script alvo não encontrado: $scriptSource"
}

$script:passed = 0

function Assert-True($Condition, $Name) {
  if (-not $Condition) { throw "FAIL: $Name" }
  $script:passed++
  Write-Host "PASS $Name"
}

function Assert-Equal($Actual, $Expected, $Name) {
  if ($Actual -ne $Expected) { throw "FAIL: $Name esperado=[$Expected] recebido=[$Actual]" }
  $script:passed++
  Write-Host "PASS $Name"
}

function Assert-Throws($ScriptBlock, $ExpectedPattern, $Name) {
  $threw = $false
  $msg = ''
  try {
    & $ScriptBlock | Out-Null
  } catch {
    $threw = $true
    $msg = $_.Exception.Message
  }
  if (-not $threw) { throw "FAIL: $Name (esperava exceção, mas não falhou)" }
  if ($ExpectedPattern -and ($msg -notmatch $ExpectedPattern)) {
    throw "FAIL: $Name mensagem inesperada=[$msg] padrão=[$ExpectedPattern]"
  }
  $script:passed++
  Write-Host "PASS $Name"
}

function Test-NoBomUtf8([byte[]]$Bytes) {
  if ($Bytes.Length -lt 3) { return $true }
  return -not ($Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF)
}

function Assert-BytesEqual([byte[]]$A, [byte[]]$B, $Name) {
  if ($A.Length -ne $B.Length) { throw "FAIL: $Name tamanhos diferentes ($($A.Length) vs $($B.Length))" }
  for ($i = 0; $i -lt $A.Length; $i++) {
    if ($A[$i] -ne $B[$i]) { throw "FAIL: $Name divergência no byte $i" }
  }
  $script:passed++
  Write-Host "PASS $Name"
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("horus-bridge-test-" + [guid]::NewGuid().ToString('N'))
try {
  # Estrutura temporária com espaços nos nomes e dois níveis acima do repositório.
  $wsTop = Join-Path $tempRoot '01. Projetos Teste'
  $wsMid = Join-Path $wsTop 'HORUS App'
  $repoDir = Join-Path $wsMid 'horus repo'
  $repoScripts = Join-Path $repoDir 'scripts'
  $repoSkillDir = Join-Path $repoDir '.claude/skills/horus-maestro'
  $repoSkillFile = Join-Path $repoSkillDir 'SKILL.md'
  $siblingDir = Join-Path $wsTop 'Outro Projeto'
  $prefixTrapDir = Join-Path $tempRoot '01. Projetos'

  New-Item -ItemType Directory -Force -Path $repoScripts, $repoSkillDir, $siblingDir, $prefixTrapDir | Out-Null
  Copy-Item -LiteralPath $scriptSource -Destination (Join-Path $repoScripts 'install-workspace-skill-bridge.ps1')
  [System.IO.File]::WriteAllText($repoSkillFile, "# skill original`n", [System.Text.UTF8Encoding]::new($false))

  $tempScript = Join-Path $repoScripts 'install-workspace-skill-bridge.ps1'

  # 1. DryRun com WorkspacePath omitido não grava nada.
  $dryOut = (& $tempScript -DryRun) -join "`n"
  $parentBridge = Join-Path $wsMid '.claude/skills/horus-maestro/SKILL.md'
  Assert-True ($dryOut -match 'Gravaria a ponte em:') 'DryRun informa destino'
  Assert-Equal (Test-Path -LiteralPath (Join-Path $wsMid '.claude')) $false 'DryRun não cria diretório'

  # 2. WorkspacePath omitido grava na pasta-mãe imediata.
  $outDefault = (& $tempScript) -join "`n"
  Assert-True ($outDefault -match 'Ponte gravada em:') 'omitido grava na pasta-mãe'
  Assert-Equal (Test-Path -LiteralPath $parentBridge -PathType Leaf) $true 'arquivo criado na pasta-mãe'
  $bytesDefault = [System.IO.File]::ReadAllBytes($parentBridge)
  Assert-True (Test-NoBomUtf8 $bytesDefault) 'omitido grava UTF-8 sem BOM'
  $textDefault = [System.Text.UTF8Encoding]::new($false).GetString($bytesDefault)
  Assert-True ($textDefault -match 'horus repo/\.claude/skills/horus-maestro/SKILL\.md') 'omitido usa caminho relativo da pasta-mãe'
  Assert-True (-not $textDefault.Contains($tempRoot)) 'omitido não contém caminho absoluto'

  Push-Location $wsMid
  try {
    Assert-True ($textDefault -match 'Leia e siga (.+?/SKILL\.md)') 'omitido expõe caminho da skill'
    $relInDefault = $Matches[1]
    Assert-Equal (Test-Path -LiteralPath $relInDefault -PathType Leaf) $true 'caminho relativo na pasta-mãe resolve para a skill original'
  } finally {
    Pop-Location
  }

  # 3. WorkspacePath dois níveis acima com espaços e casing diferente (case-insensitive no Windows).
  $wsTopWrongCase = $wsTop.ToLowerInvariant()
  $outTop = (& $tempScript -WorkspacePath $wsTopWrongCase) -join "`n"
  $topBridge = Join-Path $wsTop '.claude/skills/horus-maestro/SKILL.md'
  Assert-True ($outTop -match 'Ponte gravada em:') 'WorkspacePath dois níveis acima grava ponte'
  Assert-Equal (Test-Path -LiteralPath $topBridge -PathType Leaf) $true 'arquivo criado no workspace informado'
  $bytesTop1 = [System.IO.File]::ReadAllBytes($topBridge)
  Assert-True (Test-NoBomUtf8 $bytesTop1) 'WorkspacePath grava UTF-8 sem BOM'
  $textTop = [System.Text.UTF8Encoding]::new($false).GetString($bytesTop1)
  Assert-True ($textTop -match 'HORUS App/horus repo/\.claude/skills/horus-maestro/SKILL\.md') 'dois níveis acima usa caminho relativo com / e espaços'
  Assert-True (-not $textTop.Contains($tempRoot)) 'dois níveis acima não contém caminho absoluto'

  Push-Location $wsTop
  try {
    Assert-True ($textTop -match 'Leia e siga (.+?/SKILL\.md)') 'dois níveis acima expõe caminho da skill'
    $relInTop = $Matches[1]
    Assert-Equal (Test-Path -LiteralPath $relInTop -PathType Leaf) $true 'caminho relativo dois níveis acima resolve para a skill original'
  } finally {
    Pop-Location
  }

  # 4. Idempotência: segunda execução mantém bytes idênticos e informa "sem alterações".
  $outTop2 = (& $tempScript -WorkspacePath $wsTop) -join "`n"
  $bytesTop2 = [System.IO.File]::ReadAllBytes($topBridge)
  Assert-True ($outTop2 -match 'sem alterações') 'segunda execução informa sem alterações'
  Assert-BytesEqual $bytesTop1 $bytesTop2 'segunda execução preserva bytes idênticos'

  $outTopDrySame = (& $tempScript -WorkspacePath $wsTop -DryRun) -join "`n"
  Assert-True ($outTopDrySame -match 'sem alterações') 'DryRun quando igual também informa sem alterações'

  # 5. Atualiza ponte antiga que contenha marcador install-workspace-skill-bridge.ps1.
  $oldBridge = "Gerada por horus/scripts/install-workspace-skill-bridge.ps1.`nConteúdo antigo.`n"
  [System.IO.File]::WriteAllText($topBridge, $oldBridge, [System.Text.UTF8Encoding]::new($false))
  $outUpdate = (& $tempScript -WorkspacePath $wsTop) -join "`n"
  Assert-True ($outUpdate -match 'Ponte gravada em:') 'sobrescreve ponte antiga com marcador'
  $bytesTop3 = [System.IO.File]::ReadAllBytes($topBridge)
  Assert-BytesEqual $bytesTop1 $bytesTop3 'ponte atualizada volta aos bytes esperados'

  # 6. Proteção: recusa sobrescrever arquivo não relacionado (sem marcador).
  $unrelatedDir = Join-Path $tempRoot 'Workspace Protegido'
  $unrelatedSkillDir = Join-Path $unrelatedDir '.claude/skills/horus-maestro'
  $unrelatedSkill = Join-Path $unrelatedSkillDir 'SKILL.md'
  $unrelatedParent = Join-Path $unrelatedDir 'HORUS App'
  $unrelatedRepo = Join-Path $unrelatedParent 'horus'
  New-Item -ItemType Directory -Force -Path (Join-Path $unrelatedRepo 'scripts'), (Join-Path $unrelatedRepo '.claude/skills/horus-maestro'), $unrelatedSkillDir | Out-Null
  Copy-Item -LiteralPath $scriptSource -Destination (Join-Path $unrelatedRepo 'scripts/install-workspace-skill-bridge.ps1')
  [System.IO.File]::WriteAllText((Join-Path $unrelatedRepo '.claude/skills/horus-maestro/SKILL.md'), "# original`n", [System.Text.UTF8Encoding]::new($false))
  $unrelatedContent = "# Minha skill manual`nNão apagar.`n"
  [System.IO.File]::WriteAllText($unrelatedSkill, $unrelatedContent, [System.Text.UTF8Encoding]::new($false))
  $unrelatedBytesBefore = [System.IO.File]::ReadAllBytes($unrelatedSkill)

  $unrelatedScript = Join-Path $unrelatedRepo 'scripts/install-workspace-skill-bridge.ps1'
  Assert-Throws { & $unrelatedScript -WorkspacePath $unrelatedDir } 'não é uma ponte gerada por install-workspace-skill-bridge\.ps1' 'recusa sobrescrever SKILL.md não relacionado'
  $unrelatedBytesAfter = [System.IO.File]::ReadAllBytes($unrelatedSkill)
  Assert-BytesEqual $unrelatedBytesBefore $unrelatedBytesAfter 'arquivo não relacionado permanece intacto'

  # 7. Validações de ancestralidade e existência do workspace.
  Assert-Throws { & $tempScript -WorkspacePath (Join-Path $tempRoot 'nao-existe') } 'não encontrada' 'erro para workspace inexistente'
  Assert-Throws { & $tempScript -WorkspacePath $repoDir } 'ancestral' 'erro quando workspace é o próprio repositório'
  Assert-Throws { & $tempScript -WorkspacePath $repoScripts } 'ancestral' 'erro quando workspace é subpasta do repositório'
  Assert-Throws { & $tempScript -WorkspacePath $siblingDir } 'ancestral' 'erro quando workspace é pasta irmã'
  Assert-Throws { & $tempScript -WorkspacePath $prefixTrapDir } 'ancestral' 'erro quando workspace é apenas prefixo parcial de nome'
  Assert-Throws { & $tempScript -WorkspacePath '   ' } 'não pode ser vazio' 'erro para WorkspacePath vazio'

  Assert-Equal (Test-Path -LiteralPath (Join-Path $siblingDir '.claude')) $false 'não cria .claude em workspace inválido'
  Assert-Equal (Test-Path -LiteralPath (Join-Path $prefixTrapDir '.claude')) $false 'não cria .claude em prefixo parcial'
} finally {
  if (Test-Path -LiteralPath $tempRoot) {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
  }
}

Write-Host "TOTAL: $script:passed testes aprovados."
