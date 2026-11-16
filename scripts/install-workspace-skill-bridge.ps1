<#
.SYNOPSIS
  Cria (ou atualiza) a ponte da skill horus-maestro na pasta de trabalho que contém o repositório horus.

.DESCRIPTION
  O Claude Code só descobre .claude/skills/ da pasta em que a sessão é aberta. Quando o Orca ou o Claude
  Code são iniciados na pasta-mãe (que reúne horus, horus-storybook, fabrica-canais), a skill versionada em
  horus/.claude/skills/horus-maestro não aparece. Este script grava, na pasta-mãe, uma ponte mínima que só
  aponta para a skill versionada — sem regras próprias, sem caminhos absolutos e sem credenciais.
  Idempotente: reescreve o mesmo conteúdo; não toca em nada fora de <pasta-mãe>/.claude/skills/horus-maestro.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File horus/scripts/install-workspace-skill-bridge.ps1
#>
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'

$repo = Split-Path $PSScriptRoot -Parent
$repoName = Split-Path $repo -Leaf
$workspace = Split-Path $repo -Parent
$source = Join-Path $repo '.claude/skills/horus-maestro/SKILL.md'
if (-not (Test-Path -LiteralPath $source)) { throw "Skill versionada não encontrada: $source" }

$target = Join-Path $workspace '.claude/skills/horus-maestro/SKILL.md'
$content = @"
---
name: horus-maestro
description: Inicializa, retoma, sincroniza ou transfere a coordenação do projeto Hórus (Maestro) quando a sessão é aberta na pasta de trabalho que contém os repositórios. Ponte para a skill versionada em $repoName/.claude/skills/horus-maestro. Use quando o usuário pedir para iniciar, retomar, continuar, sincronizar ou transferir o contexto do Hórus.
---

# Hórus — ponte para a skill do repositório

Gerada por $repoName/scripts/install-workspace-skill-bridge.ps1. Não acrescenta nem altera regras.

1. Leia e siga $repoName/.claude/skills/horus-maestro/SKILL.md integralmente, usando $repoName/ como raiz do repositório (procedimento em $repoName/docs/RETOMADA-MAESTRO.md).
2. Valem $repoName/AGENTS.md, $repoName/CLAUDE.md e $repoName/docs/AGENT-GOVERNANCE.md; em divergência, prevalecem os arquivos do repositório.

Nunca, sem autorização específica do usuário: inferência real do Hermes, APIs ou serviços pagos, credenciais, publicação, envio de dados privados, Git destrutivo.
"@

if ($DryRun) { "Gravaria a ponte em: $target"; return }
New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
[System.IO.File]::WriteAllText($target, $content, [System.Text.UTF8Encoding]::new($false))
"Ponte gravada em: $target"
