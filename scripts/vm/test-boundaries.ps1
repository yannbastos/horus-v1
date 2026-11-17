$ErrorActionPreference='Stop'
foreach($file in Get-ChildItem $PSScriptRoot -Filter '*.ps1'){
 $tokens=$null;$errors=$null
 [Management.Automation.Language.Parser]::ParseFile($file.FullName,[ref]$tokens,[ref]$errors)|Out-Null
 if($errors){throw ($errors|Out-String)}
}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile("$PSScriptRoot/guest-support.ps1",[ref]$tokens,[ref]$errors)
$definition=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Run-Setup'},$true)
# Load only this function, never guest.ps1 or native/UI helpers.
Invoke-Expression $definition.Extent.Text
$script:exitCode=0;$script:finish=$true;$script:arguments=''
function Start-Process {
 param($FilePath,$ArgumentList,[switch]$PassThru)
 $script:arguments=$ArgumentList
 $p=[pscustomobject]@{ExitCode=$script:exitCode}
 $p|Add-Member ScriptMethod WaitForExit {param($Timeout) return $script:finish}
 return $p
}
Run-Setup 'fixture.exe' '/S /NS'
if($script:arguments -ne '/S /NS'){throw 'Arguments changed'}
$script:exitCode=2;$caught=$false
try{Run-Setup 'fixture.exe' '/S'}catch{$caught=$true}
if(!$caught){throw 'Nonzero exit accepted'}
$script:finish=$false;$caught=$false
try{Run-Setup 'fixture.exe' '/S'}catch{$caught=$true}
if(!$caught){throw 'Timeout accepted'}
$guest=Get-Content "$PSScriptRoot/guest.ps1" -Raw
if($guest -match 'Run-Setup[^\r\n]*''/[^'']*D='){throw 'Explicit directory bypasses default'}
if(!$guest.Contains('Uninstall $true') -or !$guest.Contains('Uninstall $false')){throw 'Both uninstall modes required'}
'boundary assertions: 5'
# Exercise only the SQLite helper against a fresh synthetic file, not UI APIs.
$source=Get-Content "$PSScriptRoot/guest-support.ps1" -Raw
$native=[regex]::Match($source,'(?s)Add-Type @"\r?\n(.*?)\r?\n"@').Groups[1].Value
Add-Type -TypeDefinition $native
$fixture=[IO.Path]::GetTempFileName()
try{
 [VmNative]::Database($fixture,$true)
 $before=(Get-FileHash $fixture -Algorithm SHA256).Hash
 [VmNative]::Database($fixture,$false)
 if((Get-FileHash $fixture -Algorithm SHA256).Hash -ne $before){throw 'Integrity check changed synthetic SQLite'}
 'SQLite fixture integrity/hash: passed'
}finally{[IO.File]::Delete($fixture)}
