#requires -Version 5.1
# Testes sem Pester e sem rede/atualizações. Mocks limitados a este processo.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'update-dev-tools.ps1')
$script:passed=0
function Assert-Equal($Actual,$Expected,$Name) {
    if ($Actual -ne $Expected) { throw "$Name esperado=$Expected recebido=$Actual" }
    $script:passed++; Write-Host "PASS $Name"
}
$script:entry=@(Get-DevCatalog)[1]
$script:fixture=[pscustomobject]@{ Paths=@('C:\Users\test\codex.cmd'); Apps=@(); Manager='npm global'; Npm='npm.cmd'; Conflict=$false; Policy=@() }
$script:installed='codex-cli 1.0.0'
$script:latest='1.1.0'
$script:active=$false
$script:networkFail=$false
$script:updateFail=$false
$script:mutations=0
$script:versionCalls=0
function Get-DevInstall { param($Entry); $script:fixture }
function Get-DevLatest { param($Entry); if ($script:networkFail) { throw 'mock network offline' }; $script:latest }
function Get-DevProcesses { @() }
function Test-DevActive { param($Entry,$Processes); $script:active }
function Invoke-DevCommand {
    param($File,$Arguments)
    if ($Arguments[0] -eq '--version') { $script:versionCalls++; return $script:installed }
    if ($Arguments[0] -ne 'install') { throw 'unexpected command' }
    if ($Arguments -notcontains '@openai/codex@1.1.0' -or $Arguments -notcontains '--engine-strict') { throw 'unsafe target' }
    $script:mutations++
    if ($script:updateFail) { throw 'mock update failed' }
    if ($script:wrongAfter) { $script:installed='codex-cli 1.0.0' } else { $script:installed='codex-cli 1.1.0' }
}
Assert-Equal (Invoke-DevTool $entry $false $false).Status 'Disponivel' 'instalada com atualização disponível'
Assert-Equal $script:mutations 0 'check não atualiza'
$script:fixture.Paths=@()
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'Ausente' 'ausente não instala'
$script:fixture.Paths=@('C:\Users\test\codex.cmd')
$script:installed='1.1.0'
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'Atualizada' 'já atualizada'
$script:installed='1.2.0'
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'Atualizada' 'não faz downgrade'
$script:installed='1.0.0'
$script:networkFail=$true
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'RedeIndisponivel' 'falha de rede isolada'
$script:networkFail=$false
$script:active=$true
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'SessaoAtiva' 'sessão ativa bloqueia'
Assert-Equal $script:mutations 0 'nenhuma mutação até aqui'
$script:active=$false
$script:fixture.Policy=@('por máquina')
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'Gerenciado' 'máquina/TI bloqueia'
$script:fixture.Policy=@()
$script:fixture.Conflict=$true
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'Conflito' 'duplicatas bloqueiam'
$script:fixture.Conflict=$false
foreach ($manager in @('scoop','winget/MSIX','pipx','uv tool','pip/venv (confirmar distribuição)','desconhecido','instalador próprio / auto-update nativo')) {
    $script:fixture.Manager=$manager
    Assert-Equal (Invoke-DevTool $entry $true $false).Status 'Manual' "gerenciador $manager não é substituído"
}
$script:fixture.Manager='npm global'
$script:installed='versão desconhecida'
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'VersaoIncompativel' 'versão incompatível'
$script:installed='1.0.0-beta.1'
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'VersaoIncompativel' 'prerelease instalada recusada'
$script:installed='1.0.0'
$script:latest='1.1.0-rc.1'
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'VersaoIncompativel' 'alvo prerelease recusado'
$script:latest='1.1.0'
Assert-Equal (Invoke-DevTool $entry $true $true).Status 'Disponivel' 'dryrun não atualiza'
Assert-Equal $script:mutations 0 'simulação sem mutações'
$script:updateFail=$true
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'Falha' 'falha durante atualização'
$script:updateFail=$false
$r=Invoke-DevTool $entry $true $false
Assert-Equal $r.Status 'AtualizadaAgora' 'atualização simulada completa'
Assert-Equal $r.After 'codex-cli 1.1.0' 'confirma versão e inicialização'
$script:installed='1.0.0'; $script:wrongAfter=$true
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'Falha' 'versão final incorreta não declara sucesso'
$script:wrongAfter=$false
$script:activeChecks=0
function Test-DevActive { param($Entry,$Processes); $script:activeChecks++; return ($script:activeChecks -ge 2) }
$oldMutations=$script:mutations
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'SessaoAtiva' 'sessão aberta entre consulta e atualização bloqueia'
Assert-Equal $script:mutations $oldMutations 'revalidação não executa mutação'
# Confirma default seguro e precedência Check/DryRun na função pública.
$script:applies=@()
function Invoke-DevTool { param($Entry,$Apply,$Preview); $script:applies+=([bool]$Apply); [pscustomobject]@{ Tool=$Entry.Name; Status='Mock'; Before=''; LatestStable=''; Detail='' } }
$null=Start-DevToolsUpdate -NonInteractive
Assert-Equal (@($script:applies | Where-Object { $_ }).Count) 0 'NonInteractive sozinho consulta'
$script:applies=@(); $null=Start-DevToolsUpdate -All -Check
Assert-Equal (@($script:applies | Where-Object { $_ }).Count) 0 'Check vence All'
$script:applies=@(); $null=Start-DevToolsUpdate -All -DryRun
Assert-Equal (@($script:applies | Where-Object { $_ }).Count) 0 'DryRun vence All'
$script:applies=@(); $null=Start-DevToolsUpdate -Tool codex -NonInteractive
Assert-Equal $script:applies.Count 1 'seleção por ferramenta'
Assert-Equal $script:applies[0] $true 'Tool explícito autoriza'
# Agendador em DryRun jamais chama os cmdlets de escrita.
function Register-ScheduledTask { throw 'registro indevido' }
function Unregister-ScheduledTask { throw 'remoção indevida' }
$plan=& (Join-Path $PSScriptRoot 'register-dev-tools-update-task.ps1') -DryRun
Assert-Equal ($plan.Arguments -match '-Check') $true 'logon somente leitura'
$null=& (Join-Path $PSScriptRoot 'register-dev-tools-update-task.ps1') -Register -DryRun
Assert-Equal $true $true 'Register DryRun não registra'
Write-Host "$script:passed testes aprovados; nenhuma atualização real, rede ou tarefa registrada."
# Restaura funções de produção para testar descoberta e processos com mocks de SO.
. (Join-Path $PSScriptRoot 'update-dev-tools.ps1')
$script:commandPaths=@()
function Get-Command { param($Name,[switch]$All,$ErrorAction); if($Name -ne 'npm.cmd') { foreach($path in $script:commandPaths) { [pscustomobject]@{ CommandType='Application'; Source=$path } } } }
function Get-ItemProperty { param($Path,$ErrorAction); @() }
function Get-DevPolicy { param($Paths); @() }
foreach ($pair in @(
    @('C:\Users\test\scoop\shims\codex.cmd','scoop'),
    @('C:\Users\test\AppData\Local\Microsoft\WinGet\Links\codex.exe','winget/MSIX'),
    @('C:\Users\test\pipx\venvs\codex\bin\codex.exe','pipx'),
    @('C:\Users\test\uv\tools\codex\bin\codex.exe','uv tool'),
    @('C:\Users\test\venv\Scripts\codex.exe','pip/venv (confirmar distribuição)'),
    @('C:\Users\test\custom\codex.exe','desconhecido')
)) {
    $script:commandPaths=@($pair[0]); $detected=Get-DevInstall $entry
    Assert-Equal $detected.Manager $pair[1] "detecção origem $($pair[1])"
}
$script:commandPaths=@('C:\Users\test\npm\codex.cmd','C:\Users\test\npm\codex.ps1')
Assert-Equal (Get-DevInstall $entry).Conflict $false 'shims adjacentes sem conflito'
$script:commandPaths+=@('C:\Users\test\other\codex.exe')
Assert-Equal (Get-DevInstall $entry).Conflict $true 'duas localizações em conflito'
$script:commandPaths=@()
Assert-Equal (Get-DevInstall $entry).Paths.Count 0 'descoberta de ausente'
Assert-Equal (Test-DevActive $entry @([pscustomobject]@{Name='node.exe';Path='';Line='node C:\npm\@openai\codex\bin\codex.js'})) $true 'sessão node por linha de comando'
Assert-Equal (Test-DevActive $entry @([pscustomobject]@{Name='node.exe';Path='';Line=''})) $true 'host inacessível bloqueia'
Assert-Equal (Test-DevActive $entry @([pscustomobject]@{Name='node.exe';Path='';Line='node unrelated-server.js'})) $false 'node alheio identificável não bloqueia'
function Get-Process { param($ErrorAction); [pscustomobject]@{Id=98765;ProcessName='node'} }
function Get-CimInstance { param($ClassName,$ErrorAction); [pscustomobject]@{ProcessId=98765;Name='node.exe';CommandLine='node unrelated-server.js';ExecutablePath='node.exe'} }
$processes=@(Get-DevProcesses)
Assert-Equal $processes.Count 1 'não duplica host conhecido com linha vazia'
Assert-Equal (Test-DevActive $entry $processes) $false 'inventário preserva linha conhecida'
function Get-CimInstance { throw 'acesso negado' }
$blocked=$false
try { $null=Get-DevProcesses } catch { $blocked=$true }
Assert-Equal $blocked $true 'falha de inventário não libera atualização'
Write-Host "TOTAL: $script:passed testes aprovados."
