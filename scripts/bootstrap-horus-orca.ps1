#requires -Version 5.1
[CmdletBinding()]
param(
  [string]$WorkspacePath,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Test-ToolStatus([string]$Tool, [string[]]$Arguments) {
  $ErrorActionPreference = 'Continue'
  & $Tool @Arguments *> $null
  $LASTEXITCODE -eq 0
}

$orca = if ($env:ORCA_CLI_COMMAND) { $env:ORCA_CLI_COMMAND } else { 'orca' }
foreach ($tool in @('git', 'gh', $orca, 'claude', 'codex', 'agy')) {
  if (Get-Command $tool -ErrorAction SilentlyContinue) {
    Write-Host "Disponível: $tool"
  } else {
    Write-Warning "Ferramenta ausente no PATH: $tool"
  }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git é necessário para localizar o checkout.' }
$repo = & git -c core.quotePath=false -C $PSScriptRoot rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0 -or -not $repo) { throw 'Execute o bootstrap a partir de um checkout Git.' }
$repo = (Resolve-Path -LiteralPath $repo).ProviderPath
$workspace = if ($PSBoundParameters.ContainsKey('WorkspacePath')) {
  if ([string]::IsNullOrWhiteSpace($WorkspacePath)) { throw 'Informe uma pasta de workspace.' }
  $selected = if ([IO.Path]::IsPathRooted($WorkspacePath)) { $WorkspacePath } else { Join-Path $repo $WorkspacePath }
  (Resolve-Path -LiteralPath $selected).ProviderPath
} else { Split-Path $repo -Parent }

Write-Host "Raiz Git: $repo"
Write-Host "Workspace: $workspace"
if ($workspace.TrimEnd('\', '/') -ieq $repo.TrimEnd('\', '/')) {
  foreach ($skill in 'supervisar', 'horus-maestro') {
    if (-not (Test-Path -LiteralPath (Join-Path $repo ".claude/skills/$skill/SKILL.md") -PathType Leaf)) {
      throw "Skill versionada ausente: $skill"
    }
    Write-Host "Skill disponível diretamente na raiz: /$skill"
  }
} else {
  & (Join-Path $repo 'scripts/install-workspace-skill-bridge.ps1') -WorkspacePath $workspace -DryRun:$DryRun
}

if (Get-Command gh -ErrorAction SilentlyContinue) {
  if (-not (Test-ToolStatus gh @('auth', 'status', '--hostname', 'github.com', '--active'))) {
    Write-Warning 'GitHub: autenticação não confirmada. Execute gh auth login.'
  } else {
    if (Test-ToolStatus gh @('api', 'repos/yannbastos/horus-management/issues?per_page=1')) {
      Write-Host 'GitHub: acesso às Issues de gestão confirmado.'
    } else { Write-Warning 'GitHub: acesso às Issues de gestão não confirmado.' }
    if (Test-ToolStatus gh @('project', 'view', '1', '--owner', 'yannbastos', '--format', 'json')) {
      Write-Host 'GitHub: acesso ao Project confirmado.'
    } else { Write-Warning 'GitHub: acesso ao Project não confirmado. Confira a conta e o escopo project.' }
  }
}

if ((Get-Command $orca -ErrorAction SilentlyContinue) -and
    -not (Test-ToolStatus $orca @('orchestration', 'run-list', '--environment', 'local', '--json'))) {
  Write-Warning 'Orca: runtime local de orquestração indisponível. Confira o aplicativo.'
}
foreach ($probe in @(
  @{ Tool = 'claude'; Arguments = @('auth', 'status') },
  @{ Tool = 'codex'; Arguments = @('login', 'status') }
)) {
  if (Get-Command $probe.Tool -ErrorAction SilentlyContinue) {
    if (Test-ToolStatus $probe.Tool $probe.Arguments) {
      Write-Host "$($probe.Tool): autenticação local confirmada."
    } else { Write-Warning "$($probe.Tool): autenticação não confirmada. Confira na CLI." }
  }
}
if (Get-Command agy -ErrorAction SilentlyContinue) {
  if (Test-ToolStatus agy @('models')) {
    Write-Host 'Antigravity: catálogo disponível. Confirme o login na CLI antes de delegar.'
  } else { Write-Warning 'Antigravity: catálogo indisponível. Confira o login na CLI.' }
}
