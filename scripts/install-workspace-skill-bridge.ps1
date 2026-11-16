<#
.SYNOPSIS
  Cria (ou atualiza) a ponte da skill horus-maestro na pasta de trabalho que contém o repositório horus.

.DESCRIPTION
  O Claude Code só descobre .claude/skills/ da pasta em que a sessão é aberta. Quando o Orca ou o Claude
  Code são iniciados em uma pasta de trabalho acima do repositório (pasta-mãe imediata ou ancestral que
  reúne vários repositórios), a skill versionada em .claude/skills/horus-maestro não aparece diretamente.
  Este script grava, no workspace informado (ou na pasta-mãe imediata quando -WorkspacePath é omitido),
  uma ponte mínima que só aponta para a skill versionada pelo caminho relativo — sem regras próprias,
  sem caminhos absolutos e sem credenciais.
  Idempotente: só reescreve quando o conteúdo muda e recusa sobrescrever arquivo não gerado por este script.

.PARAMETER WorkspacePath
  Pasta de trabalho ancestral do repositório onde gravar .claude/skills/horus-maestro/SKILL.md.
  Quando omitido, usa a pasta-mãe imediata do repositório.

.PARAMETER DryRun
  Simula a execução sem criar diretórios nem gravar arquivos.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File horus/scripts/install-workspace-skill-bridge.ps1

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File HORUS/horus/scripts/install-workspace-skill-bridge.ps1 -WorkspacePath .
#>
#requires -Version 5.1
param(
  [string]$WorkspacePath,
  [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).ProviderPath.TrimEnd('\', '/')
$source = Join-Path $repo '.claude/skills/horus-maestro/SKILL.md'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
  throw "Skill versionada não encontrada: $source"
}

if ($PSBoundParameters.ContainsKey('WorkspacePath')) {
  if ([string]::IsNullOrWhiteSpace($WorkspacePath)) {
    throw 'O parâmetro -WorkspacePath não pode ser vazio.'
  }
  if (-not (Test-Path -LiteralPath $WorkspacePath -PathType Container)) {
    throw "Pasta de workspace não encontrada: $WorkspacePath"
  }
  $workspace = (Resolve-Path -LiteralPath $WorkspacePath).ProviderPath.TrimEnd('\', '/')
} else {
  $workspace = (Split-Path $repo -Parent).TrimEnd('\', '/')
}

$workspacePrefix = "$workspace\"
if (-not $repo.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "A pasta de workspace informada deve ser ancestral do repositório ($repo): $workspace"
}

$repoRel = ($repo.Substring($workspacePrefix.Length) -replace '\\', '/').Trim('/')
if ([string]::IsNullOrWhiteSpace($repoRel)) {
  throw "Não foi possível calcular o caminho relativo entre o workspace ($workspace) e o repositório ($repo)."
}

$targetDir = Join-Path $workspace '.claude/skills/horus-maestro'
$target = Join-Path $targetDir 'SKILL.md'
$content = @"
---
name: horus-maestro
description: Inicializa, retoma, sincroniza ou transfere a coordenação do projeto Hórus (Maestro) quando a sessão é aberta na pasta de trabalho que contém os repositórios. Ponte para a skill versionada em $repoRel/.claude/skills/horus-maestro. Use quando o usuário pedir para iniciar, retomar, continuar, sincronizar ou transferir o contexto do Hórus.
---

# Hórus — ponte para a skill do repositório

Gerada por $repoRel/scripts/install-workspace-skill-bridge.ps1. Não acrescenta nem altera regras.

1. Leia e siga $repoRel/.claude/skills/horus-maestro/SKILL.md integralmente, usando $repoRel/ como raiz do repositório (procedimento em $repoRel/docs/RETOMADA-MAESTRO.md).
2. Valem $repoRel/AGENTS.md, $repoRel/CLAUDE.md e $repoRel/docs/AGENT-GOVERNANCE.md; em divergência, prevalecem os arquivos do repositório.

Nunca, sem autorização específica do usuário: inferência real do Hermes, APIs ou serviços pagos, credenciais, publicação, envio de dados privados, Git destrutivo.
"@

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$desiredBytes = $utf8NoBom.GetBytes($content)

if (Test-Path -LiteralPath $target) {
  if (Test-Path -LiteralPath $target -PathType Container) {
    throw "O destino já existe como diretório: $target"
  }
  $existingBytes = [System.IO.File]::ReadAllBytes($target)
  $existingText = $utf8NoBom.GetString($existingBytes)
  if ($existingText -notmatch 'install-workspace-skill-bridge\.ps1') {
    throw "Arquivo existente não é uma ponte gerada por install-workspace-skill-bridge.ps1; nada alterado: $target"
  }

  $sameBytes = ($existingBytes.Length -eq $desiredBytes.Length)
  if ($sameBytes) {
    for ($i = 0; $i -lt $existingBytes.Length; $i++) {
      if ($existingBytes[$i] -ne $desiredBytes[$i]) {
        $sameBytes = $false
        break
      }
    }
  }
  if ($sameBytes) {
    "Ponte já atualizada (sem alterações): $target"
    return
  }
}

if ($DryRun) {
  "Gravaria a ponte em: $target"
  return
}

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
[System.IO.File]::WriteAllBytes($target, $desiredBytes)
"Ponte gravada em: $target"
