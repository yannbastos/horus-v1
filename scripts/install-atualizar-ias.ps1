#requires -Version 5.1
<#
.SYNOPSIS
Instala ou remove o comando de usuário `atualizar-ias` nos perfis do PowerShell 5.1 e 7.
.DESCRIPTION
Grava um bloco delimitado por marcadores nos perfis do usuário atual, sem alterar
PATH, sem alterar ExecutionPolicy e sem exigir privilégios de administrador.
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$DryRun,
    [string[]]$ProfilePath
)

$ErrorActionPreference = 'Stop'

$script:StartMarker = '# >>> horus atualizar-ias >>>'
$script:EndMarker   = '# <<< horus atualizar-ias <<<'

function Get-DefaultProfilePaths {
    $docs = [Environment]::GetFolderPath('MyDocuments')
    if (-not $docs) {
        throw 'Pasta Documentos do usuário não encontrada.'
    }
    @(
        (Join-Path $docs 'WindowsPowerShell\Microsoft.PowerShell_profile.ps1'),
        (Join-Path $docs 'PowerShell\Microsoft.PowerShell_profile.ps1')
    )
}

function Get-ProfileFileState {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{
            Exists   = $false
            Content  = ''
            Encoding = (New-Object System.Text.UTF8Encoding($true))
            NewLine  = [Environment]::NewLine
        }
    }

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0) {
        return [pscustomobject]@{
            Exists   = $true
            Content  = ''
            Encoding = (New-Object System.Text.UTF8Encoding($true))
            NewLine  = [Environment]::NewLine
        }
    }

    $encoding = $null
    $offset = 0

    if ($bytes.Length -ge 4 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE -and $bytes[2] -eq 0x00 -and $bytes[3] -eq 0x00) {
        $encoding = New-Object System.Text.UTF32Encoding($false, $true)
        $offset = 4
    } elseif ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $encoding = New-Object System.Text.UTF8Encoding($true)
        $offset = 3
    } elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
        $encoding = [System.Text.Encoding]::Unicode
        $offset = 2
    } elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF) {
        $encoding = [System.Text.Encoding]::BigEndianUnicode
        $offset = 2
    } else {
        $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
        try {
            $null = $strictUtf8.GetString($bytes)
            $encoding = New-Object System.Text.UTF8Encoding($false)
        } catch {
            $encoding = [System.Text.Encoding]::Default
        }
    }

    $content = $encoding.GetString($bytes, $offset, $bytes.Length - $offset)
    $nl = if ($content.Contains("`r`n")) { "`r`n" } elseif ($content.Contains("`n")) { "`n" } else { [Environment]::NewLine }

    [pscustomobject]@{
        Exists   = $true
        Content  = $content
        Encoding = $encoding
        NewLine  = $nl
    }
}

function New-AtualizarIasBlock {
    param(
        [string]$UpdaterScriptPath,
        [string]$InstallerScriptPath,
        [string]$NewLine
    )
    $escapedUpdater = $UpdaterScriptPath -replace "'", "''"
    $escapedInstaller = $InstallerScriptPath -replace "'", "''"

    $lines = @(
        $script:StartMarker,
        'function atualizar-ias {',
        '    [CmdletBinding()]',
        '    param(',
        '        [switch]$Check,',
        '        [switch]$DryRun,',
        "        [ValidateSet('claude','codex','antigravity','hermes')][string[]]`$Tool,",
        '        [switch]$All,',
        '        [switch]$NonInteractive',
        '    )',
        "    `$scriptPath = '$escapedUpdater'",
        "    `$installerPath = '$escapedInstaller'",
        '    if (-not (Test-Path -LiteralPath $scriptPath)) {',
        '        throw "Atualizador não encontrado em ''$scriptPath''. Execute novamente ''$installerPath'' para registrar o comando."',
        '    }',
        "    `$exe = if (`$PSVersionTable.PSEdition -eq 'Core') { Join-Path `$PSHOME 'pwsh.exe' } else { Join-Path `$PSHOME 'powershell.exe' }",
        "    `$baseArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', `$scriptPath)",
        '    if ($Check -or (-not $All -and (-not $Tool -or $Tool.Count -eq 0))) {',
        "        `$baseArgs += '-Check'",
        '    }',
        "    if (`$DryRun) { `$baseArgs += '-DryRun' }",
        "    if (`$All) { `$baseArgs += '-All' }",
        "    if (`$NonInteractive) { `$baseArgs += '-NonInteractive' }",
        '    if ($Tool -and $Tool.Count -gt 1) {',
        '        $exitCode = 0',
        '        foreach ($t in $Tool) {',
        "            & `$exe @baseArgs '-Tool' `$t",
        '            if ($LASTEXITCODE -ne 0) { $exitCode = $LASTEXITCODE }',
        '        }',
        '        $global:LASTEXITCODE = $exitCode',
        '    } else {',
        "        if (`$Tool -and `$Tool.Count -eq 1) { `$baseArgs += @('-Tool', `$Tool[0]) }",
        '        & $exe @baseArgs',
        '    }',
        '}',
        $script:EndMarker
    )
    $lines -join $NewLine
}

function Test-EffectiveExecutionPolicyWarning {
    param([string]$UpdaterScriptPath)
    $policy = Get-ExecutionPolicy
    if ($policy -in @('Restricted', 'AllSigned')) {
        Write-Warning ("A ExecutionPolicy efetiva atual é '{0}', o que pode impedir o carregamento automático do perfil do PowerShell. Nenhuma política foi alterada. Alternativas: (1) liberar scripts locais para o usuário atual com 'Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned', ou (2) executar diretamente: powershell -NoProfile -ExecutionPolicy Bypass -File `"{1}`" -Check" -f $policy, $UpdaterScriptPath)
    }
}

function Install-AtualizarIas {
    [CmdletBinding()]
    param(
        [switch]$Uninstall,
        [switch]$DryRun,
        [string[]]$ProfilePath,
        [string]$UpdaterScriptPath
    )

    $installerPath = (Resolve-Path -LiteralPath $PSCommandPath -ErrorAction Stop).ProviderPath
    if (-not $UpdaterScriptPath) {
        $candidate = Join-Path $PSScriptRoot 'update-dev-tools.ps1'
        if ($Uninstall) {
            $UpdaterScriptPath = if (Test-Path -LiteralPath $candidate) { (Resolve-Path -LiteralPath $candidate).ProviderPath } else { $candidate }
        } else {
            if (-not (Test-Path -LiteralPath $candidate)) {
                throw 'Atualizador update-dev-tools.ps1 não encontrado ao lado deste script.'
            }
            $UpdaterScriptPath = (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).ProviderPath
        }
    }

    if (-not $Uninstall) {
        Test-EffectiveExecutionPolicyWarning -UpdaterScriptPath $UpdaterScriptPath
    }

    $targets = if ($ProfilePath -and $ProfilePath.Count -gt 0) { @($ProfilePath) } else { @(Get-DefaultProfilePaths) }
    $blockPattern = '(?ms)^[ \t]*' + [regex]::Escape($script:StartMarker) + '\r?\n.*?^[ \t]*' + [regex]::Escape($script:EndMarker)
    $removePattern = '(?ms)^[ \t]*' + [regex]::Escape($script:StartMarker) + '\r?\n.*?^[ \t]*' + [regex]::Escape($script:EndMarker) + '[ \t]*(?:\r?\n)?'

    foreach ($target in $targets) {
        $state = Get-ProfileFileState -Path $target
        $nl = $state.NewLine
        $hasBlock = [regex]::IsMatch($state.Content, $blockPattern)
        $action = 'SemAlteracao'
        $newContent = $state.Content

        if ($Uninstall) {
            if ($state.Exists -and $hasBlock) {
                $newContent = [regex]::Replace($state.Content, $removePattern, '', 1)
                $action = 'Removido'
            }
        } else {
            $block = New-AtualizarIasBlock -UpdaterScriptPath $UpdaterScriptPath -InstallerScriptPath $installerPath -NewLine $nl
            if ($hasBlock) {
                $newContent = [regex]::Replace($state.Content, $blockPattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $block }, 1)
                $action = if ($newContent -ceq $state.Content) { 'SemAlteracao' } else { 'Atualizado' }
            } elseif (-not $state.Exists) {
                $newContent = $block + $nl
                $action = 'Criado'
            } elseif ($state.Content.Length -eq 0) {
                $newContent = $block + $nl
                $action = 'Instalado'
            } elseif ($state.Content.EndsWith("`n")) {
                $newContent = $state.Content + $block + $nl
                $action = 'Instalado'
            } else {
                $newContent = $state.Content + $nl + $block + $nl
                $action = 'Instalado'
            }
        }

        $applied = $false
        if (-not $DryRun -and $action -ne 'SemAlteracao') {
            $dir = Split-Path -Parent $target
            if ($dir -and -not (Test-Path -LiteralPath $dir)) {
                $null = New-Item -ItemType Directory -Path $dir -Force
            }
            [System.IO.File]::WriteAllText($target, $newContent, $state.Encoding)
            $applied = $true
        }

        [pscustomobject]@{
            Profile = $target
            Action  = $action
            Applied = $applied
            DryRun  = [bool]$DryRun
            Script  = $UpdaterScriptPath
        }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Install-AtualizarIas @PSBoundParameters
}
