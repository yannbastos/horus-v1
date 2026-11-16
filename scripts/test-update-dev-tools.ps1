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
$script:fixture=[pscustomobject]@{ Paths=@('C:\Users\test\codex.cmd'); Apps=@(); Manager='npm global'; Npm='npm.cmd'; DetectionError=''; Conflict=$false; Policy=@() }
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
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'VersaoInstaladaSuperior' 'não faz downgrade'
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
foreach ($manager in @('scoop','winget/MSIX','pipx','uv tool','pip/venv (confirmar distribuição)','instalador próprio / auto-update nativo')) {
    $script:fixture.Manager=$manager
    Assert-Equal (Invoke-DevTool $entry $true $false).Status 'Manual' "gerenciador $manager não é substituído"
}
$script:fixture.Manager='desconhecido'
$r=Invoke-DevTool $entry $true $false
Assert-Equal $r.Status 'InstalacaoDesconhecida' 'instalação não reconhecida bloqueia'
Assert-Equal ($r.Detail -match 'bloqueada') $true 'instalação desconhecida explica o bloqueio'
$script:fixture.DetectionError='npm root -g: saída vazia'
$r=Invoke-DevTool $entry $true $false
Assert-Equal $r.Status 'FalhaDeteccao' 'falha de detecção distinta de desconhecida'
Assert-Equal ($r.Detail -match 'saída vazia') $true 'falha de detecção traz motivo curto'
$script:fixture.DetectionError=''
Assert-Equal $script:mutations 0 'desconhecida/falha de detecção não mutam'
$script:fixture.Manager='npm global'
$script:installed='versão desconhecida'
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'VersaoNaoComparavel' 'versão incompatível'
$script:installed='1.0.0-beta.1'
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'VersaoNaoComparavel' 'prerelease instalada recusada'
$script:installed='1.0.0'
$script:latest='1.1.0-rc.1'
Assert-Equal (Invoke-DevTool $entry $true $false).Status 'VersaoNaoComparavel' 'alvo prerelease recusado'
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
# --- HOR-144: proibição de downgrade (caso real Claude 2.1.325 x stable 2.1.311).
$script:fixture.Manager='npm global'; $script:fixture.Policy=@(); $script:fixture.Conflict=$false; $script:active=$false
function Test-DevActive { param($Entry,$Processes); $script:active }
function Invoke-DevCommand {
    param($File,$Arguments)
    if ($Arguments[0] -eq '--version') { return $script:installed }
    if ($Arguments[0] -ne 'install') { throw 'unexpected command' }
    $script:mutations++; $script:lastArgs=$Arguments
    $script:installed=($script:fmt -f $script:latestVer)
}
$script:fmt='{0}'; $script:latestVer=''
$cases=@(
    @{ Name='claude'; Fmt='{0} (Claude Code)'; Pkg='@anthropic-ai/claude-code' },
    @{ Name='codex'; Fmt='codex-cli {0}'; Pkg='@openai/codex' }
)
foreach ($c in $cases) {
    $e=@(Get-DevCatalog | Where-Object Name -eq $c.Name)[0]; $script:fmt=$c.Fmt
    $script:installed=($c.Fmt -f '2.1.325'); $script:latest='2.1.311'; $script:latestVer='2.1.311'; $script:mutations=0
    $r=Invoke-DevTool $e $true $false
    Assert-Equal $r.Status 'VersaoInstaladaSuperior' "$($c.Name): 2.1.325 x 2.1.311 não faz downgrade"
    Assert-Equal $script:mutations 0 "$($c.Name): downgrade nunca chama instalação"
    # Sessão ativa/conflito/política não mascaram o motivo da versão.
    $script:active=$true; $script:fixture.Conflict=$true; $script:fixture.Policy=@('x')
    Assert-Equal (Invoke-DevTool $e $true $false).Status 'VersaoInstaladaSuperior' "$($c.Name): versão decide antes das proteções"
    $script:active=$false; $script:fixture.Conflict=$false; $script:fixture.Policy=@()
    $script:installed=($c.Fmt -f '2.1.311')
    Assert-Equal (Invoke-DevTool $e $true $false).Status 'Atualizada' "$($c.Name): iguais"
    Assert-Equal $script:mutations 0 "$($c.Name): iguais sem comando"
    foreach ($bad in @('','2.1.325-beta.1','abc','2.1','2.1.325.1')) {
        $script:latest=$bad; $script:installed=($c.Fmt -f '2.1.325')
        Assert-Equal (Invoke-DevTool $e $true $false).Status 'VersaoNaoComparavel' "$($c.Name): fonte '$bad' não comparável"
    }
    $script:latest='2.1.326'; $script:latestVer='2.1.326'; $script:installed=($c.Fmt -f '2.1.325')
    $r=Invoke-DevTool $e $true $false
    Assert-Equal $r.Status 'AtualizadaAgora' "$($c.Name): superior atualiza"
    Assert-Equal $script:mutations 1 "$($c.Name): chamada única"
    Assert-Equal ($script:lastArgs -contains "$($c.Pkg)@2.1.326") $true "$($c.Name): versão fixada"
    Assert-Equal (@($script:lastArgs | Where-Object { $_ -match 'latest' }).Count) 0 "$($c.Name): nunca latest"
    $script:mutations=0
}
# Antigravity e Hermes (instalador próprio): nunca executam comando, mesmo com -All.
foreach ($pair in @(@('antigravity','1.0.5','1.0.4'),@('hermes','Hermes Agent v0.23.1 (2026.9.24) Python 3.12','v0.22.0'))) {
    $e=@(Get-DevCatalog | Where-Object Name -eq $pair[0])[0]
    $script:fixture.Manager='instalador próprio / auto-update nativo'
    $script:installed=$pair[1]; $script:latest=$pair[2]; $script:mutations=0
    Assert-Equal (Invoke-DevTool $e $true $false).Status 'VersaoInstaladaSuperior' "$($pair[0]): instalada superior"
    $script:latest='v9.99.0'; $script:installed=if ($pair[0] -eq 'hermes') { $pair[1] } else { '1.0.5' }
    Assert-Equal (Invoke-DevTool $e $true $false).Status 'Manual' "$($pair[0]): superior só orienta, sem executar"
    Assert-Equal $script:mutations 0 "$($pair[0]): sem chamada de instalação"
}
$script:fixture.Manager='npm global'
# Comparação numérica e extração de prefixos.
Assert-Equal (Compare-DevVersion '0.9.0' '0.10.0').Result 'DisponivelSuperior' '0.9.0 < 0.10.0'
Assert-Equal (Compare-DevVersion '1.5.6' '1.5.9').Result 'DisponivelSuperior' '1.5.6 < 1.5.9'
Assert-Equal (Compare-DevVersion '0.10.0' '0.9.0').Result 'InstaladaSuperior' '0.10.0 > 0.9.0'
Assert-Equal (Compare-DevVersion '2.1.325 (Claude Code)' '2.1.311').Result 'InstaladaSuperior' 'formato Claude'
Assert-Equal (Compare-DevVersion 'codex-cli 0.167.2' 'rust-v0.169.0').Result 'DisponivelSuperior' 'codex-cli x rust-v'
Assert-Equal (Compare-DevVersion 'Hermes Agent v0.23.1 (2026.9.24) x' 'v0.23.1').Result 'Igual' 'Hermes ignora data'
Assert-Equal (Compare-DevVersion '1.5.6' '1.5.6').Result 'Igual' 'iguais'
Assert-Equal (Compare-DevVersion '' '1.0.0').Result 'NaoComparavel' 'instalada vazia'
Assert-Equal (Compare-DevVersion '1.0.0-rc.1' '1.0.0').Result 'NaoComparavel' 'instalada prerelease'
Assert-Equal (Compare-DevVersion '1.0.0' 'latest').Result 'NaoComparavel' 'disponível sem versão'
Assert-Equal (Compare-DevVersion '1.0.0' '1.1.0-beta.2').Result 'NaoComparavel' 'disponível prerelease'
Assert-Equal (Compare-DevVersion '1.0.0' 'x 1.1.0').Result 'NaoComparavel' 'disponível ambíguo'
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
function Get-DevPolicy { param($Paths,$Name,$Manager); @() }
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

# --- HOR-144: npm com ruído, política por ferramenta e estados do diagnóstico.
. (Join-Path $PSScriptRoot 'update-dev-tools.ps1')
foreach ($f in @('Get-Command','Get-ItemProperty','Get-Process','Get-CimInstance')) { Remove-Item "function:$f" -ErrorAction SilentlyContinue }
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('dev-tools-test-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    function Invoke-DevStdout { param($File,$Arguments); if ($script:npmFail) { throw 'exit 1' }; $script:npmLines }
    $script:npmFail=$false
    $script:npmLines=@($tmp)
    $d=Get-DevNpmDirectory 'npm.cmd' 'root'
    Assert-Equal $d.Path $tmp 'npm root: saída limpa'
    $script:npmLines=@('npm.cmd : npm warn Unknown user config "always-auth".',"  $tmp  ",'')
    Assert-Equal (Get-DevNpmDirectory 'npm.cmd' 'prefix').Path $tmp 'npm prefix: ruído antes, usa a última linha não vazia'
    $script:npmLines=@('npm.cmd : npm warn Unknown user config')
    $d=Get-DevNpmDirectory 'npm.cmd' 'root'
    Assert-Equal $d.Path '' 'texto de aviso não vira caminho'
    Assert-Equal ($d.Error -match 'absoluto') $true 'aviso: motivo informado'
    $script:npmLines=@('','  ')
    Assert-Equal ((Get-DevNpmDirectory 'npm.cmd' 'root').Error -match 'vazia') $true 'saída vazia'
    $script:npmLines=@('relativo\dir')
    Assert-Equal ((Get-DevNpmDirectory 'npm.cmd' 'root').Error -match 'absoluto') $true 'caminho relativo recusado'
    $script:npmLines=@((Join-Path $tmp 'nao-existe'))
    Assert-Equal ((Get-DevNpmDirectory 'npm.cmd' 'root').Error -match 'inexistente') $true 'diretório inexistente recusado'
    $script:npmFail=$true
    Assert-Equal ((Get-DevNpmDirectory 'npm.cmd' 'root').Error -match 'exit 1') $true 'falha do npm vira erro, sem exceção'
    $script:npmFail=$false

    # Get-DevInstall com npm inválido: sem exceção, falha de detecção para Claude não-npm.
    $claude=@(Get-DevCatalog)[0]
    $script:cmds=@()
    function Get-Command { param($Name,[switch]$All,$ErrorAction)
        if ($Name -eq 'npm.cmd') { [pscustomobject]@{ CommandType='Application'; Source='C:\fake\npm.cmd' } }
        else { foreach ($p in $script:cmds) { [pscustomobject]@{ CommandType='Application'; Source=$p } } } }
    function Get-ItemProperty { param($Path,$ErrorAction); @() }
    $script:cmds=@('C:\Users\test\custom\claude.exe')
    $script:npmLines=@('npm.cmd : npm warn lixo')
    $i=Get-DevInstall $claude
    Assert-Equal $i.Manager 'desconhecido' 'npm inválido: origem desconhecida'
    Assert-Equal ($i.DetectionError -match 'npm root -g') $true 'npm inválido: falha de detecção com motivo'
    # Claude nativo reconhecido não é afetado pela falha do npm.
    $nativeClaude = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
    $script:cmds=@($nativeClaude)
    $i=Get-DevInstall $claude
    Assert-Equal $i.Manager 'instalador próprio / auto-update nativo' 'Claude nativo não presume npm'
    Assert-Equal $i.DetectionError '' 'nativo reconhecido sem falha de detecção'
    # npm global comprovado continua reconhecido.
    $modules=Join-Path $tmp 'node_modules'; $pkgDir=Join-Path $modules '@openai\codex'
    New-Item -ItemType Directory -Path $pkgDir -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $pkgDir 'package.json') -Value '{"name":"@openai/codex"}'
    function Invoke-DevStdout { param($File,$Arguments); if ($Arguments[0] -eq 'root') { $modules } else { $tmp } }
    $script:cmds=@((Join-Path $tmp 'codex.cmd'))
    Assert-Equal (Get-DevInstall $entry).Manager 'npm global' 'npm global comprovado reconhecido'
    Remove-Item function:Get-Command; Remove-Item function:Get-ItemProperty
} finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }

# Políticas: chave genérica do Google não gerencia Antigravity/Hermes.
function Test-Path { param($Path,$LiteralPath,$PathType); $script:existing -contains $Path }
$mine=@((Join-Path $env:USERPROFILE 'x\tool.exe'))
$script:existing=@('HKLM:\SOFTWARE\Policies\Google','HKCU:\SOFTWARE\Policies\Google')
foreach ($n in @('antigravity','hermes','claude','codex')) {
    Assert-Equal @(Get-DevPolicy $mine $n 'instalador próprio').Count 0 "Policies\Google genérica não gerencia $n"
}
$script:existing=@('HKLM:\SOFTWARE\Policies\Google\Antigravity')
$p=@(Get-DevPolicy $mine 'antigravity' 'instalador próprio')
Assert-Equal @($p | Where-Object { $_ -match 'Google\\Antigravity' }).Count 1 'política específica do Antigravity bloqueia e cita a chave'
Assert-Equal @(Get-DevPolicy $mine 'hermes' 'instalador próprio').Count 0 'política do Antigravity não afeta Hermes'
$script:existing=@('HKCU:\SOFTWARE\Policies\Anthropic')
Assert-Equal @(Get-DevPolicy $mine 'claude' 'npm global').Count 1 'política Anthropic bloqueia Claude'
Assert-Equal @(Get-DevPolicy $mine 'codex' 'npm global').Count 0 'política Anthropic não afeta Codex'
$script:existing=@('HKLM:\SOFTWARE\Policies\Microsoft\Windows\AppInstaller')
Assert-Equal @(Get-DevPolicy $mine 'codex' 'npm global').Count 0 'AppInstaller ignorado fora do winget'
Assert-Equal @(Get-DevPolicy $mine 'codex' 'winget/MSIX').Count 1 'AppInstaller vale para winget/MSIX'
$script:existing=@()
Assert-Equal (@(Get-DevPolicy @('C:\Program Files\Tool\t.exe') 'hermes' '').Count -ge 1) $true 'instalação por máquina continua bloqueando'
Remove-Item function:Test-Path
Write-Host "TOTAL: $script:passed testes aprovados."
