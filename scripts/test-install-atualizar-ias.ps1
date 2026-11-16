#requires -Version 5.1
# Testes do instalador `atualizar-ias` sem Pester e sem alterar perfis reais do usuário.
$ErrorActionPreference = 'Stop'

$script:passed = 0
function Assert-Equal($Actual, $Expected, [string]$Name) {
    if ($Actual -ne $Expected) {
        throw "$Name esperado='$Expected' recebido='$Actual'"
    }
    $script:passed++
    Write-Host "PASS $Name"
}

function Assert-True([bool]$Condition, [string]$Name) {
    if (-not $Condition) {
        throw "$Name esperado=True recebido=False"
    }
    $script:passed++
    Write-Host "PASS $Name"
}

$installerSource = Join-Path $PSScriptRoot 'install-atualizar-ias.ps1'
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("horus-test-atualizar-ias-" + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $tempRoot -Force

try {
    # 1. Instalação em perfil inexistente (com caminho contendo espaços)
    $profileNew = Join-Path $tempRoot 'Pasta Com Espacos\WindowsPowerShell\Microsoft.PowerShell_profile.ps1'
    $dryRes = & $installerSource -DryRun -ProfilePath $profileNew
    Assert-Equal $dryRes.Action 'Criado' 'DryRun em perfil inexistente planeja criação'
    Assert-Equal $dryRes.Applied $false 'DryRun não aplica'
    Assert-Equal (Test-Path -LiteralPath $profileNew) $false 'DryRun não cria arquivo'

    $res1 = & $installerSource -ProfilePath $profileNew
    Assert-Equal $res1.Action 'Criado' 'instalação em perfil inexistente cria arquivo'
    Assert-Equal $res1.Applied $true 'instalação aplicada'
    Assert-True (Test-Path -LiteralPath $profileNew) 'arquivo de perfil criado com pasta pai'

    $bytes1 = [System.IO.File]::ReadAllBytes($profileNew)
    Assert-True ($bytes1.Length -ge 3 -and $bytes1[0] -eq 0xEF -and $bytes1[1] -eq 0xBB -and $bytes1[2] -eq 0xBF) 'perfil novo gravado em UTF-8 com BOM'

    # 2. Idempotência: segunda execução produz exatamente os mesmos bytes
    $res2 = & $installerSource -ProfilePath $profileNew
    Assert-Equal $res2.Action 'SemAlteracao' 'segunda execução detecta sem alteração'
    $bytes2 = [System.IO.File]::ReadAllBytes($profileNew)
    Assert-Equal ([Convert]::ToBase64String($bytes1)) ([Convert]::ToBase64String($bytes2)) 'idempotência: 2 execuções geram bytes iguais'

    # 3. Perfil com conteúdo alheio preservado + encoding sem BOM preservado
    $profileExisting = Join-Path $tempRoot 'Perfil Existente\profile.ps1'
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $profileExisting) -Force
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $beforeText = "# Meu perfil pessoal`r`nSet-Alias ll Get-ChildItem`r`n"
    [System.IO.File]::WriteAllText($profileExisting, $beforeText, $utf8NoBom)

    $resExist = & $installerSource -ProfilePath $profileExisting
    Assert-Equal $resExist.Action 'Instalado' 'instalação em perfil existente'
    $bytesExist = [System.IO.File]::ReadAllBytes($profileExisting)
    Assert-True (-not ($bytesExist[0] -eq 0xEF -and $bytesExist[1] -eq 0xBB -and $bytesExist[2] -eq 0xBF)) 'preserva UTF-8 sem BOM existente'
    $textExist = $utf8NoBom.GetString($bytesExist)
    Assert-True ($textExist.StartsWith($beforeText)) 'conteúdo alheio preservado antes do bloco'
    Assert-True ($textExist.Contains('# >>> horus atualizar-ias >>>')) 'marcador inicial presente'
    Assert-True ($textExist.Contains('# <<< horus atualizar-ias <<<')) 'marcador final presente'

    # 4. Atualização do bloco antigo preservando conteúdo antes e depois
    $outdatedProfile = Join-Path $tempRoot 'Perfil Desatualizado\profile.ps1'
    $null = New-Item -ItemType Directory -Path (Split-Path -Parent $outdatedProfile) -Force
    $outdatedContent = "# Antes`r`n# >>> horus atualizar-ias >>>`r`nfunction atualizar-ias { 'antigo' }`r`n# <<< horus atualizar-ias <<<`r`n# Depois`r`n"
    [System.IO.File]::WriteAllText($outdatedProfile, $outdatedContent, $utf8NoBom)

    $resUpd = & $installerSource -ProfilePath $outdatedProfile
    Assert-Equal $resUpd.Action 'Atualizado' 'atualiza bloco desatualizado'
    $updatedText = [System.IO.File]::ReadAllText($outdatedProfile, $utf8NoBom)
    Assert-True ($updatedText.StartsWith("# Antes`r`n")) 'conteúdo antes do bloco preservado na atualização'
    Assert-True ($updatedText.EndsWith("`r`n# Depois`r`n")) 'conteúdo depois do bloco preservado na atualização'
    Assert-True (-not $updatedText.Contains("'antigo'")) 'conteúdo antigo do bloco substituído'

    # 5. Uninstall remove só o bloco e preserva conteúdo alheio
    $resUninstDry = & $installerSource -Uninstall -DryRun -ProfilePath $outdatedProfile
    Assert-Equal $resUninstDry.Action 'Removido' 'Uninstall DryRun planeja remoção'
    Assert-True ([System.IO.File]::ReadAllText($outdatedProfile, $utf8NoBom).Contains('# >>> horus atualizar-ias >>>')) 'Uninstall DryRun não modifica arquivo'

    $resUninst = & $installerSource -Uninstall -ProfilePath $outdatedProfile
    Assert-Equal $resUninst.Action 'Removido' 'Uninstall remove o bloco'
    $afterUninst = [System.IO.File]::ReadAllText($outdatedProfile, $utf8NoBom)
    Assert-Equal $afterUninst "# Antes`r`n# Depois`r`n" 'Uninstall remove somente o bloco marcado'

    $resUninst2 = & $installerSource -Uninstall -ProfilePath $outdatedProfile
    Assert-Equal $resUninst2.Action 'SemAlteracao' 'Uninstall repetido é idempotente'

    # 6. Testar função gerada com script falso em caminho com espaços (sem atualizar nada real)
    $fakeRepo = Join-Path $tempRoot 'Repo Com Espacos\scripts'
    $null = New-Item -ItemType Directory -Path $fakeRepo -Force
    $fakeInstaller = Join-Path $fakeRepo 'install-atualizar-ias.ps1'
    $fakeUpdater = Join-Path $fakeRepo 'update-dev-tools.ps1'
    $argsLog = Join-Path $tempRoot 'fake-args.log'
    Copy-Item -LiteralPath $installerSource -Destination $fakeInstaller

    @'
param(
    [switch]$Check,
    [switch]$DryRun,
    [string[]]$Tool,
    [switch]$All,
    [switch]$NonInteractive
)
$log = $env:HORUS_TEST_ARGS_LOG
$line = "Check=$($Check.IsPresent);DryRun=$($DryRun.IsPresent);All=$($All.IsPresent);NonInteractive=$($NonInteractive.IsPresent);Tool=$($Tool -join ',')"
Add-Content -LiteralPath $log -Value $line -Encoding UTF8
if ($env:HORUS_TEST_EXIT_CODE) { exit ([int]$env:HORUS_TEST_EXIT_CODE) }
'@ | Set-Content -LiteralPath $fakeUpdater -Encoding UTF8

    $fakeProfile = Join-Path $tempRoot 'Fake Profile\Microsoft.PowerShell_profile.ps1'
    $resFake = & $fakeInstaller -ProfilePath $fakeProfile
    Assert-Equal $resFake.Action 'Criado' 'instalador em pasta com espaços cria perfil'

    # Dot-source do perfil falso em escopo local para testar a função `atualizar-ias`
    $env:HORUS_TEST_ARGS_LOG = $argsLog
    Remove-Item Env:HORUS_TEST_EXIT_CODE -ErrorAction SilentlyContinue
    . $fakeProfile

    atualizar-ias
    Assert-Equal $LASTEXITCODE 0 'código de saída 0 preservado'

    atualizar-ias -All
    atualizar-ias -All -DryRun -NonInteractive
    atualizar-ias -Tool codex
    atualizar-ias -Tool claude,codex

    $env:HORUS_TEST_EXIT_CODE = '7'
    atualizar-ias -Check
    Assert-Equal $LASTEXITCODE 7 'código de saída não-zero propagado'
    Remove-Item Env:HORUS_TEST_EXIT_CODE -ErrorAction SilentlyContinue

    $recorded = @(Get-Content -LiteralPath $argsLog -Encoding UTF8)
    Assert-Equal $recorded.Count 7 'total de invocações registradas pelo script falso'
    Assert-Equal $recorded[0] 'Check=True;DryRun=False;All=False;NonInteractive=False;Tool=' 'sem argumentos repassa -Check'
    Assert-Equal $recorded[1] 'Check=False;DryRun=False;All=True;NonInteractive=False;Tool=' '-All repassa -All sem -Check'
    Assert-Equal $recorded[2] 'Check=False;DryRun=True;All=True;NonInteractive=True;Tool=' '-All -DryRun -NonInteractive repassados'
    Assert-Equal $recorded[3] 'Check=False;DryRun=False;All=False;NonInteractive=False;Tool=codex' '-Tool codex repassado'
    Assert-Equal $recorded[4] 'Check=False;DryRun=False;All=False;NonInteractive=False;Tool=claude' '-Tool múltiplo (1/2)'
    Assert-Equal $recorded[5] 'Check=False;DryRun=False;All=False;NonInteractive=False;Tool=codex' '-Tool múltiplo (2/2)'
    Assert-Equal $recorded[6] 'Check=True;DryRun=False;All=False;NonInteractive=False;Tool=' '-Check explícito repassado'

    # 7. Script ausente -> erro claro mandando rodar o instalador de novo
    Remove-Item -LiteralPath $fakeUpdater
    $missingError = ''
    try {
        atualizar-ias
    } catch {
        $missingError = $_.Exception.Message
    }
    Assert-True ($missingError -match 'não encontrado' -and $missingError -match 'install-atualizar-ias\.ps1') 'script ausente exibe erro claro orientando reinstalação'

    # 8. Aviso quando ExecutionPolicy efetiva for Restricted/AllSigned (mock em memória)
    . $installerSource
    function Get-ExecutionPolicy { 'Restricted' }
    $warnings = @()
    $null = Install-AtualizarIas -DryRun -ProfilePath $profileNew -WarningVariable warnings 3>$null
    Assert-True ($warnings.Count -ge 1 -and ($warnings[0].ToString() -match 'Restricted')) 'avisa quando ExecutionPolicy é Restricted sem alterá-la'

    # 9. PS 5.1 filho com PSModulePath contaminado por módulo do pwsh 7 não aborta em Get-ExecutionPolicy
    $pwsh7ModDir = Join-Path $tempRoot 'pwsh7-modules'
    $secModDir = Join-Path $pwsh7ModDir 'Microsoft.PowerShell.Security'
    $null = New-Item -ItemType Directory -Path $secModDir -Force
    @'
@{
    ModuleVersion = '7.0.0.0'
    PowerShellVersion = '7.0'
    CompatiblePSEditions = @('Core')
    CmdletsToExport = @('Get-ExecutionPolicy')
}
'@ | Set-Content -LiteralPath (Join-Path $secModDir 'Microsoft.PowerShell.Security.psd1') -Encoding UTF8

    $pollutedProfile = Join-Path $tempRoot 'PollutedPSModulePath\Microsoft.PowerShell_profile.ps1'
    $winPsExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $oldModulePath = $env:PSModulePath
    try {
        $env:PSModulePath = "$pwsh7ModDir;$oldModulePath"
        $childOut = & $winPsExe -NoProfile -ExecutionPolicy Bypass -Command "& '$installerSource' -ProfilePath '$pollutedProfile' 3>&1 | ForEach-Object { `$_.ToString() }"
        $childExit = $LASTEXITCODE
    } finally {
        $env:PSModulePath = $oldModulePath
    }
    $childText = ($childOut -join "`n")
    Assert-Equal $childExit 0 'PS 5.1 com PSModulePath do pwsh 7 conclui sem erro'
    Assert-True ($childText -match 'não foi possível verificar a ExecutionPolicy') 'PS 5.1 com PSModulePath do pwsh 7 emite aviso curto'
    Assert-True (Test-Path -LiteralPath $pollutedProfile) 'PS 5.1 com PSModulePath do pwsh 7 cria o perfil mesmo assim'

    Write-Host "TOTAL: $script:passed testes aprovados."
} finally {
    Remove-Item Env:HORUS_TEST_ARGS_LOG -ErrorAction SilentlyContinue
    Remove-Item Env:HORUS_TEST_EXIT_CODE -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
