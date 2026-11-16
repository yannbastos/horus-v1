#requires -Version 5.1
<#
.SYNOPSIS
Inventaria e atualiza CLIs de IA por usuário, com canal estável e gates conservadores.
.DESCRIPTION
Sem parâmetros: somente leitura. -All ou -Tool autoriza atualização; -Check e
-DryRun nunca atualizam. -NonInteractive não constitui autorização por si só.
Fontes e limitações: docs/dev-tools-update.md. Nenhuma instalação nova é feita.
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$DryRun,
    [ValidateSet('claude','codex','antigravity','hermes')][string[]]$Tool,
    [switch]$All,
    [switch]$NonInteractive
)

function Invoke-DevCommand {
    param([string]$File, [string[]]$Arguments)
    # Sem Invoke-Expression: argumentos nunca viram código PowerShell.
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $File @Arguments 2>&1
        $code = $LASTEXITCODE
        if ($code -ne 0) { throw "Comando falhou (exit $code): $File" }
        return ($output | Out-String).Trim()
    } finally { $ErrorActionPreference = $previous }
}

function Get-DevJson {
    param([string]$Uri)
    Invoke-RestMethod -Uri $Uri -TimeoutSec 20 -ErrorAction Stop
}

function ConvertTo-DevVersion {
    param([string]$Value)
    # Recusa prerelease, nightly, hashes e saídas incompatíveis, sem adivinhar.
    if ($Value -match '(?i)alpha|beta|nightly|canary|preview|\d-rc') { return $null }
    if ($Value -match '(?<!\d)(\d+\.\d+\.\d+)(?![\d.+-])') {
        # [version] compara por componente numérico, nunca por texto (0.9.0 < 0.10.0).
        try { return [version]$Matches[1] } catch { return $null }
    }
    return $null
}

function Compare-DevVersion {
    param([string]$Installed, [string]$Available)
    # Única porta de decisão de versão. Só 'DisponivelSuperior' autoriza atualizar.
    $result = { param($r, $reason, $i, $a) [pscustomobject]@{ Result=$r; Reason=$reason; Installed=$i; Available=$a } }
    $inst = ConvertTo-DevVersion $Installed
    if (-not $inst) { return (& $result 'NaoComparavel' 'Versão instalada ausente, prerelease ou em formato não reconhecido.' $null $null) }
    # A fonte do canal estável devolve uma versão limpa (ex.: 2.1.311, v0.23.1, rust-v0.169.0);
    # qualquer outra forma sugere outro canal ou resposta ambígua.
    if ([string]::IsNullOrWhiteSpace($Available) -or $Available.Trim() -notmatch '^(?:[A-Za-z]+-)?v?\d+\.\d+\.\d+$') {
        return (& $result 'NaoComparavel' 'Versão do canal estável ausente, prerelease ou ambígua.' $inst $null)
    }
    $avail = ConvertTo-DevVersion $Available
    if (-not $avail) { return (& $result 'NaoComparavel' 'Versão do canal estável não reconhecida.' $inst $null) }
    if ($avail -gt $inst) { return (& $result 'DisponivelSuperior' 'Versão estável comprovadamente superior.' $inst $avail) }
    if ($avail -eq $inst) { return (& $result 'Igual' 'Versões iguais.' $inst $avail) }
    return (& $result 'InstaladaSuperior' 'Versão instalada superior à estável; downgrade proibido.' $inst $avail)
}

function Get-DevCatalog {
    @(
        [pscustomobject]@{ Name='claude'; Command='claude'; Package='@anthropic-ai/claude-code'; Repo='anthropics/claude-code'; Source='https://code.claude.com/docs/en/setup' },
        [pscustomobject]@{ Name='codex'; Command='codex'; Package='@openai/codex'; Repo='openai/codex'; Source='https://github.com/openai/codex' },
        [pscustomobject]@{ Name='antigravity'; Command='agy'; Package=''; Repo=''; Source='https://antigravity.google/docs/cli/reference/' },
        [pscustomobject]@{ Name='hermes'; Command='hermes'; Package=''; Repo='NousResearch/hermes-agent'; Source='https://hermes-agent.nousresearch.com/docs/getting-started/updating' }
    )
}

function Get-DevProcesses {
    # Falha de inventário deve bloquear, nunca ser interpretada como ausência.
    $native = @(Get-Process -ErrorAction Stop)
    $cim = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    foreach ($p in $cim) {
        if ($p.ProcessId -eq $PID) { continue }
        [pscustomobject]@{ Name=$p.Name; Line=$p.CommandLine; Path=$p.ExecutablePath }
    }
    # Get-Process cobre nomes mesmo quando a linha de comando é inacessível.
    foreach ($p in $native) {
        if ($p.Id -ne $PID -and $p.Id -notin @($cim.ProcessId)) { [pscustomobject]@{ Name=$p.ProcessName; Line=''; Path='' } }
    }
}

function Test-DevActive {
    param($Entry, $Processes)
    $pattern = switch ($Entry.Name) {
        'claude' { 'claude|@anthropic-ai[\\/]claude-code' }
        'codex' { 'codex|@openai[\\/]codex' }
        'antigravity' { 'antigravity|(?:^|[\\/\s"''])agy(?:\.exe|\s|$)' }
        'hermes' { 'hermes|hermes_cli|hermes-agent' }
    }
    foreach ($p in $Processes) {
        if ($p.Name -match $pattern -or $p.Path -match $pattern -or $p.Line -match $pattern) { return $true }
        # Um host com argumentos inacessíveis pode hospedar qualquer agente.
        if ($p.Name -match '^(node|python|pythonw)(\.exe)?$' -and -not $p.Line) { return $true }
    }
    return $false
}

function Get-DevPolicyKeys {
    param([string]$Name, [string]$Manager)
    # Cada política só vale para a ferramenta a que pertence. Chaves genéricas de
    # fornecedor (ex.: Policies\Google do Chrome/Google Update) não indicam gestão
    # de Antigravity nem de Hermes.
    $keys = switch ($Name) {
        'claude' { @('Anthropic') }
        'codex' { @('OpenAI') }
        'antigravity' { @('Google\Antigravity') }
        'hermes' { @('NousResearch') }
        default { @() }
    }
    foreach ($k in $keys) { "HKLM:\SOFTWARE\Policies\$k"; "HKCU:\SOFTWARE\Policies\$k" }
    # Política do App Installer só importa quando a instalação vem do winget/MSIX.
    if ($Manager -match 'winget') { 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\AppInstaller' }
}

function Get-DevPolicy {
    param([string[]]$Paths, [string]$Name = '', [string]$Manager = '')
    $reasons = @()
    foreach ($path in $Paths) {
        foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramData, $env:windir)) {
            if ($root -and $path.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { $reasons += 'Instalação por máquina' }
        }
        if (-not $path.StartsWith($env:USERPROFILE.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { $reasons += 'Fora do perfil do usuário; propriedade não comprovada' }
    }
    # Apenas existência de políticas, nunca conteúdo privado ou credenciais.
    foreach ($key in @(Get-DevPolicyKeys $Name $Manager)) {
        if (Test-Path $key) { $reasons += "Política corporativa presente para ${Name}: $key" }
    }
    if ($Name -eq 'claude' -and (Test-Path (Join-Path $env:ProgramData 'ClaudeCode/managed-settings.json'))) { $reasons += 'Claude gerenciado: ProgramData\ClaudeCode\managed-settings.json' }
    return @($reasons | Select-Object -Unique)
}

function Invoke-DevStdout {
    param([string]$File, [string[]]$Arguments)
    # Somente stdout: no Windows PowerShell 5.1, 2>&1 embrulha o stderr do npm
    # ("npm warn ...") em "npm.cmd : npm warn ...", que virava caminho inválido.
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $File @Arguments 2>$null
        if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
        return @($output | ForEach-Object { "$_" })
    } finally { $ErrorActionPreference = $previous }
}

function Get-DevNpmDirectory {
    param([string]$Npm, [ValidateSet('root','prefix')][string]$What)
    $fail = { param($m) [pscustomobject]@{ Path=''; Error="npm $What -g: $m" } }
    try { $lines = @(Invoke-DevStdout $Npm @($What, '-g')) } catch { return (& $fail $_.Exception.Message) }
    $line = @($lines | ForEach-Object { $_.Trim().Trim('"') } | Where-Object { $_ } | Select-Object -Last 1)
    if (-not $line.Count) { return (& $fail 'saída vazia') }
    $value = [string]$line[0]
    if ($value -notmatch '^(?:[A-Za-z]:[\\/]|\\\\)' -or $value.IndexOfAny([IO.Path]::GetInvalidPathChars()) -ge 0) { return (& $fail 'saída não é um caminho absoluto') }
    if (-not (Test-Path -LiteralPath $value -PathType Container)) { return (& $fail 'diretório inexistente') }
    [pscustomobject]@{ Path=$value.TrimEnd('\','/'); Error='' }
}

function Get-DevInstall {
    param($Entry)
    $commands = @(Get-Command $Entry.Command -All -ErrorAction SilentlyContinue)
    $paths = @($commands | Where-Object { $_.CommandType -in @('Application','ExternalScript') } | ForEach-Object { $_.Source } | Select-Object -Unique)
    $aliasConflict = @($commands | Where-Object { $_.CommandType -notin @('Application','ExternalScript') }).Count -gt 0
    # Inventário do distribuidor pelo registro, sem invocar instaladores/winget.
    $registered = @()
    $displayPattern = switch ($Entry.Name) {
        'claude' { 'Claude Code' }
        'codex' { 'Codex' }
        'antigravity' { 'Antigravity' }
        'hermes' { 'Hermes Agent' }
    }
    foreach ($key in @('HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
        $registered += @(Get-ItemProperty $key -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match $displayPattern } | Select-Object DisplayName,DisplayVersion,InstallLocation,PSPath,WinGetPackageIdentifier)
    }
    # App e CLI têm versões independentes.
    $apps = if ($Entry.Name -eq 'antigravity') { @($registered) } else { @() }
    $manager = 'desconhecido'
    $npm = $null
    $origins = @()
    foreach ($path in $paths) {
        if ($path -match '[\\/]scoop[\\/]') { $origins += 'scoop' }
        elseif ($path -match 'WinGet|WindowsApps') { $origins += 'winget/MSIX' }
        elseif ($path -match '[\\/]pipx[\\/]') { $origins += 'pipx' }
        elseif ($path -match '[\\/]uv[\\/]tools[\\/]') { $origins += 'uv tool' }
        elseif ($path -match '[\\/](site-packages|Scripts)[\\/]') { $origins += 'pip/venv (confirmar distribuição)' }
    }
    $detectionError = ''
    $npmCmd = @(if ($Entry.Package) { Get-Command npm.cmd -ErrorAction SilentlyContinue | Where-Object { $_.Source } | Select-Object -First 1 })
    if ($npmCmd.Count) {
        $npm = $npmCmd[0].Source
        $root = Get-DevNpmDirectory $npm 'root'
        $prefix = Get-DevNpmDirectory $npm 'prefix'
        if ($root.Error -or $prefix.Error) {
            $detectionError = (@($root.Error, $prefix.Error) | Where-Object { $_ }) -join '; '
        } else {
            $manifest = Join-Path (Join-Path $root.Path $Entry.Package) 'package.json'
            if (Test-Path -LiteralPath $manifest) {
                $pkg = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($pkg.name -eq $Entry.Package -and @($paths | Where-Object { (Split-Path $_ -Parent).TrimEnd('\') -eq $prefix.Path }).Count) { $origins += 'npm global' }
            }
        }
    }
    if (@($registered | Where-Object WinGetPackageIdentifier).Count) { $origins += 'winget/MSIX' }
    if (-not $origins.Count -and $paths.Count) {
        $nativePattern = switch ($Entry.Name) {
            'claude' { '[\\/]\.local[\\/]bin[\\/]claude\.exe$' }
            'codex' { '[\\/]Programs[\\/]OpenAI[\\/]Codex[\\/]bin[\\/]codex\.exe$' }
            'antigravity' { '[\\/]agy[\\/]bin[\\/]agy\.exe$' }
            'hermes' { '[\\/]hermes[\\/]bin[\\/]hermes\.exe$' }
        }
        if ($paths[0] -match $nativePattern) { $origins += 'instalador próprio / auto-update nativo' }
    }
    if ($origins.Count) { $manager = ($origins | Select-Object -Unique) -join ', ' }
    # .cmd/.ps1/.exe adjacentes do mesmo shim são uma instalação, mas todos aparecem no relatório.
    $locations = @($paths | ForEach-Object { Split-Path $_ -Parent } | Select-Object -Unique)
    $policyPaths = @($paths) + @($registered | Where-Object InstallLocation | ForEach-Object InstallLocation)
    $policy = @(Get-DevPolicy $policyPaths $Entry.Name $manager)
    if (@($registered | Where-Object { $_.PSPath -match 'HKEY_LOCAL_MACHINE' }).Count) { $policy += 'App por máquina' }
    [pscustomobject]@{ Paths=$paths; Apps=$apps; Manager=$manager; Npm=$npm; DetectionError=$(if ($manager -eq 'desconhecido') { $detectionError } else { '' }); Conflict=($aliasConflict -or $locations.Count -gt 1 -or @($origins | Select-Object -Unique).Count -gt 1); Policy=$policy }
}

function Get-DevLatest {
    param($Entry)
    if ($Entry.Name -eq 'claude') {
        # dist-tag stable é diferente de latest, intencionalmente.
        $data = Get-DevJson 'https://registry.npmjs.org/@anthropic-ai%2fclaude-code'
        return [string]$data.'dist-tags'.stable
    }
    if ($Entry.Name -eq 'codex') {
        $data = Get-DevJson 'https://api.github.com/repos/openai/codex/releases/latest'
        if ($data.prerelease -or $data.draft) { throw 'Release não estável' }
        return [string]$data.tag_name
    }
    if ($Entry.Name -eq 'hermes') {
        $data = Get-DevJson 'https://api.github.com/repos/NousResearch/hermes-agent/releases/latest'
        if ($data.prerelease -or $data.draft) { throw 'Release não estável' }
        return [string]$data.tag_name
    }
    # Manifesto de distribuição pública usado pelo instalador oficial do agy.
    # Nunca executar o instalador remoto nem usar endpoints de preview.
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $manifest = Get-DevJson "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/windows_$arch.json"
    if ($manifest.url -notmatch '^https://storage\.googleapis\.com/antigravity-public/antigravity-cli/' -or -not (ConvertTo-DevVersion $manifest.version)) { throw 'Manifesto público agy incompatível/não estável' }
    return [string]$manifest.version
}

function Get-DevPlan {
    param($Entry, $Install, [version]$Target)
    $file = if ($Install.Paths.Count) { $Install.Paths[0] } else { '' }
    if ($Install.Manager -eq 'npm global' -and $Entry.Package) {
        return [pscustomobject]@{ File=$Install.Npm; Args=@('install','--global',"$($Entry.Package)@$Target",'--engine-strict','--registry=https://registry.npmjs.org'); Manual='' }
    }
    if ($Install.Manager -like 'instalador próprio*') {
        switch ($Entry.Name) {
            'codex' { return [pscustomobject]@{ File=$file; Args=@('update'); Manual='Atualizador nativo não permite fixar versão/canal nesta versão: revisar manualmente codex update.' } }
            'claude' { return [pscustomobject]@{ File=$file; Args=@('update'); Manual='claude update usa canal configurado; revisar manualmente o canal stable sem o script alterar settings.' } }
            'antigravity' { return [pscustomobject]@{ File=$file; Args=@('update'); Manual='agy update: confirmar canal estável e versão na documentação/interface oficial antes de executar manualmente.' } }
            'hermes' { return [pscustomobject]@{ File=$file; Args=@('update','--channel','stable','--no-gateway-restart'); Manual='hermes update pode migrar configuração/estado: revisão manual obrigatória para preservar memória e configurações.' } }
        }
    }
    $hint = switch -Regex ($Install.Manager) {
        'winget' { 'winget upgrade --id <ID oficial confirmado> --exact --scope user' }
        'scoop' { 'scoop update <manifesto oficial confirmado>' }
        'pipx' { 'pipx upgrade <distribuição oficial confirmada>' }
        'uv tool' { 'uv tool upgrade <distribuição oficial confirmada>' }
        'pip' { 'python -m pip install --upgrade <distribuição oficial confirmada> (no ambiente detectado)' }
        default { 'Revisar instalador oficial e origem; não instalar outra cópia.' }
    }
    [pscustomobject]@{ File=''; Args=@(); Manual=$hint }
}

function Invoke-DevTool {
    param($Entry, [bool]$Apply, [bool]$Preview)
    $r = [ordered]@{ Tool=$Entry.Name; Paths=@(); Apps=@(); Manager=''; Before=''; LatestStable=''; Method=''; Source=$Entry.Source; Status=''; Detail=''; After=''; Recovery='Reinstalar manualmente pela fonte oficial usando o gerenciador original após encerrar sessões; preservar os dados. Sem garantia de rollback.' }
    try {
        $i = Get-DevInstall $Entry
        $r.Paths=$i.Paths; $r.Apps=$i.Apps; $r.Manager=$i.Manager
        if (-not $i.Paths.Count) { $r.Status=if ($i.Apps.Count) { 'Manual' } else { 'Ausente' }; $r.Detail='CLI ausente no PATH; nenhuma instalação será feita. App, se presente, usa atualização pela interface oficial.'; return [pscustomobject]$r }
        # --version é a única inicialização permitida; nunca abrir uma sessão de inferência.
        $r.Before = Invoke-DevCommand $i.Paths[0] @('--version')
        $networkError = ''
        try { $r.LatestStable=Get-DevLatest $Entry } catch { $networkError=$_.Exception.Message }
        # Decisão de versão antes de qualquer outra proteção: downgrade é proibido sempre.
        if ($networkError) { $r.Status='RedeIndisponivel'; $r.Detail=$networkError; return [pscustomobject]$r }
        $cmp = Compare-DevVersion $r.Before $r.LatestStable
        if ($cmp.Result -eq 'InstaladaSuperior') { $r.Status='VersaoInstaladaSuperior'; $r.Detail="Instalada $($cmp.Installed) é superior à estável $($cmp.Available); nenhum downgrade será feito."; return [pscustomobject]$r }
        if ($cmp.Result -eq 'Igual') { $r.Status='Atualizada'; $r.Detail='Já igual à estável; nenhum comando executado.'; return [pscustomobject]$r }
        if ($cmp.Result -ne 'DisponivelSuperior') { $r.Status='VersaoNaoComparavel'; $r.Detail=$cmp.Reason; return [pscustomobject]$r }
        $target = $cmp.Available
        $plan = Get-DevPlan $Entry $i $target
        $r.Method = (@($plan.File) + @($plan.Args)) -join ' '
        if ($plan.Manual) { $r.Method += ' | ' + $plan.Manual }
        if ($i.Policy.Count) { $r.Status='Gerenciado'; $r.Detail=$i.Policy -join '; '; return [pscustomobject]$r }
        if ($i.DetectionError) { $r.Status='FalhaDeteccao'; $r.Detail="Não foi possível identificar a origem da instalação ($($i.DetectionError)); atualização bloqueada."; return [pscustomobject]$r }
        if ($i.Conflict) { $r.Status='Conflito'; $r.Detail='Múltiplas origens/localizações ou alias; resolver manualmente sem alterar PATH.'; return [pscustomobject]$r }
        if ($i.Manager -eq 'desconhecido') { $r.Status='InstalacaoDesconhecida'; $r.Detail='Instalação não reconhecida (não é npm global comprovado nem local conhecido); atualização bloqueada para não criar uma segunda cópia.'; return [pscustomobject]$r }
        if (Test-DevActive $Entry @(Get-DevProcesses)) { $r.Status='SessaoAtiva'; $r.Detail='Pulado: processo ativo ou host sem linha de comando verificável; nenhum processo encerrado.'; return [pscustomobject]$r }
        if ($plan.Manual -or -not $plan.File) { $r.Status='Manual'; $r.Detail=$plan.Manual; return [pscustomobject]$r }
        $r.Status='Disponivel'
        if (-not $Apply -or $Preview) { $r.Detail='Somente consulta/simulação; atualização não executada.'; return [pscustomobject]$r }
        # Revalidar sessões imediatamente antes da mutação.
        if (Test-DevActive $Entry @(Get-DevProcesses)) { $r.Status='SessaoAtiva'; return [pscustomobject]$r }
        # Última barreira: o comando precisa fixar exatamente a versão comparada.
        if (@($plan.Args | Where-Object { $_ -like "*@$target" }).Count -ne 1) { throw 'Comando de atualização não fixa a versão comparada; abortado.' }
        Write-Host "ANTES $($Entry.Name): $($r.Before); método: $($r.Method)"
        $null = Invoke-DevCommand $plan.File $plan.Args
        $afterInstall = Get-DevInstall $Entry
        if ($afterInstall.Conflict -or -not $afterInstall.Paths.Count) { throw 'Resolução do comando mudou após atualização; verificar manualmente.' }
        $r.After=Invoke-DevCommand $afterInstall.Paths[0] @('--version')
        $confirmed=ConvertTo-DevVersion $r.After
        if (-not $confirmed -or $confirmed -ne $target) { throw 'Versão final não corresponde à versão estável solicitada.' }
        $r.Status='AtualizadaAgora'; $r.Detail='Comando resolvido novamente e inicialização --version confirmada.'
    } catch { $r.Status='Falha'; $r.Detail=$_.Exception.Message }
    [pscustomobject]$r
}

function Start-DevToolsUpdate {
    param([switch]$Check,[switch]$DryRun,[string[]]$Tool,[switch]$All,[switch]$NonInteractive)
    if ($env:OS -ne 'Windows_NT') { throw 'Este script suporta Windows PowerShell 5.1; em outro SO, usar os métodos oficiais manualmente.' }
    $apply = ($All -or $Tool.Count -gt 0) -and -not $Check -and -not $DryRun
    $catalog = @(Get-DevCatalog | Where-Object { -not $Tool.Count -or $_.Name -in $Tool })
    foreach ($entry in $catalog) {
        $result = Invoke-DevTool $entry $apply ([bool]$DryRun)
        Write-Host ("[{0}] {1}: {2} -> {3}; {4}" -f $result.Status,$result.Tool,$result.Before,$result.LatestStable,$result.Detail)
        $result
    }
}

# Dot-source oferece funções para testes, sem executar inventário ou rede.
if ($MyInvocation.InvocationName -ne '.') { Start-DevToolsUpdate @PSBoundParameters }
