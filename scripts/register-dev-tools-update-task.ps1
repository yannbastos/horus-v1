#requires -Version 5.1
[CmdletBinding()]
param([switch]$Register,[switch]$Unregister,[switch]$DryRun)
$ErrorActionPreference='Stop'
if ($Register -and $Unregister) { throw 'Escolha Register ou Unregister.' }
$name='HORUS-DevTools-Check'
$scriptPath=Join-Path $PSScriptRoot 'update-dev-tools.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) { throw 'Atualizador não encontrado ao lado deste script.' }
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments='-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $scriptPath + '" -Check -All -NonInteractive'
$plan=[pscustomobject]@{
    Task=$name; User=$identity; Trigger='Ao logon do usuário atual'; RunLevel='Limited';
    Executable=(Join-Path $PSHOME 'powershell.exe'); Arguments=$arguments;
    Policy='Verificar ao logon; atualizar somente mediante autorização explícita e sem sessões ativas. Esta tarefa apenas verifica.'
}
$plan
if ($DryRun -or (-not $Register -and -not $Unregister)) { return }
if ($Unregister) {
    $existing=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.Principal.UserId -ne $identity) { throw 'Tarefa pertence a outro usuário; não remover.' }
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    return
}
# Sem Force: nunca sobrescrever tarefa preexistente, sem senha e sem elevação.
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { throw 'Tarefa já existe; inspecione antes de substituir.' }
$action=New-ScheduledTaskAction -Execute $plan.Executable -Argument $arguments
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal=New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $plan.Policy
