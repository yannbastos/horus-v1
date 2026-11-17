param([Parameter(Mandatory=$true)][string]$InputDirectory,[Parameter(Mandatory=$true)][string]$OutputDirectory,[switch]$DisposableVm)
$ErrorActionPreference='Stop'
$results=[ordered]@{schema=1;status='failed';started=[DateTime]::UtcNow.ToString('o');steps=@();error=$null}
$transcript=$false;$app=$null;$script:socket=$null;$canWrite=$false
try{
 if(!$DisposableVm){throw 'Explicit -DisposableVm required; never use a daily-use host'}
 $inputPath=[IO.Path]::GetFullPath($InputDirectory).TrimEnd('\')
 $outputPath=[IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\')
 if($inputPath -eq $outputPath -or $outputPath.StartsWith($inputPath+'\',[StringComparison]::OrdinalIgnoreCase) -or $inputPath.StartsWith($outputPath+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Output must be outside read-only input, with no nesting in either direction'}
 $manifest=Get-Content (Join-Path $inputPath 'manifest.json') -Raw|ConvertFrom-Json
 if(!$manifest.hostComputer -or $manifest.hostComputer -eq $env:COMPUTERNAME){throw 'HOST REFUSED: run only in a different disposable guest'}
 if((Test-Path -LiteralPath $outputPath) -and @(Get-ChildItem -LiteralPath $outputPath -Force).Count){throw 'Use a new empty evidence directory'}
 New-Item -ItemType Directory $outputPath -Force|Out-Null
 $OutputDirectory=$outputPath;$canWrite=$true
 Start-Transcript -Path "$outputPath/transcript.log"|Out-Null;$transcript=$true
 $results.computer=$env:COMPUTERNAME;$results.sourceCommit=$manifest.sourceCommit
 $principal=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
 if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Non-elevated standard guest session required to prove no UAC'}
 if(![Environment]::Is64BitProcess){throw 'Use 64-bit PowerShell'}
 $webview=@(foreach($base in @(${env:ProgramFiles(x86)},$env:LOCALAPPDATA)){if($base){Get-ChildItem (Join-Path $base 'Microsoft/EdgeWebView/Application/*/msedgewebview2.exe') -ErrorAction SilentlyContinue}})
 $results.webview=@($webview|ForEach-Object{@{path=$_.FullName;version=$_.VersionInfo.ProductVersion}})
 if(!$webview.Count){throw 'WebView2 absent: explicitly prepare official offline runtime in guest; no download attempted'}
 $product='HKCU:/Software/horus/HORUS';$uninstallKey='HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall/HORUS'
 $install=Join-Path $env:LOCALAPPDATA 'Programs/Horus'
 $data=Join-Path $env:LOCALAPPDATA 'com.horus.audiovisual';$roaming=Join-Path $env:APPDATA 'com.horus.audiovisual'
 foreach($path in @($product,$uninstallKey,$install,$data,$roaming)){if(Test-Path -LiteralPath $path){throw "Guest not clean: $path"}}
 if(@($manifest.packages).Count -ne 2){throw 'Manifest must contain N and N+1'}
 $packages=@(foreach($package in $manifest.packages){
  if([IO.Path]::GetFileName($package.file) -ne $package.file){throw 'Installer filename must be a leaf'}
  $path=Join-Path $inputPath $package.file
  if((Get-FileHash $path -Algorithm SHA256).Hash -ne $package.sha256){throw 'Installer hash mismatch'}
  $info=(Get-Item $path).VersionInfo
  if($info.ProductName -ne 'HORUS' -or [version]$info.ProductVersion -ne [version]$package.version){throw 'Production identity/version mismatch'}
  $path
 })
 if([version]$manifest.packages[1].version -le [version]$manifest.packages[0].version){throw 'N+1 must be newer'}
 . "$PSScriptRoot/guest-support.ps1"
 function Record([string]$Name,$Detail){$results.steps+=@{name=$Name;passed=$true;detail=$Detail}}
 function Check-Install([string]$Version){
  $actual=(Get-Item -LiteralPath $product).GetValue('');$entry=Get-ItemProperty -LiteralPath $uninstallKey
  if($actual -ne $install -or $entry.InstallLocation.Trim('"') -ne $install -or [version]$entry.DisplayVersion -ne [version]$Version){throw 'Destination/version mismatch'}
  Record 'destination' @{version=$Version;path=$actual;nonElevated=$true}
 }
 function Fixtures {
  @(foreach($folder in @($data,$roaming)){Get-ChildItem -LiteralPath $folder -Recurse -File -Force|Get-FileHash -Algorithm SHA256|Select-Object Path,Hash})|Sort-Object Path|ConvertTo-Json -Compress
 }
 function Verify-Data {
  foreach($folder in @($data,$roaming)){foreach($database in Get-ChildItem -LiteralPath $folder -Recurse -Filter '*.sqlite3' -File){[VmNative]::Database($database.FullName,$false)}}
  if((Fixtures) -ne $script:before){throw 'Synthetic data hashes changed'}
 }
 function Uninstall([bool]$Interactive){
  $uninstaller=Join-Path $install 'uninstall.exe'
  if($Interactive){
   Start-Process $uninstaller|Out-Null;$script:dialog=$null
   Wait-Until {
    $windows=[Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Children,[Windows.Automation.Condition]::TrueCondition)
    foreach($w in $windows){if($w.Current.Name -match 'HORUS' -and $w.Current.Name -match 'Uninstall|Desinstal'){$script:dialog=$w;return $true}}
    return $false
   } 'interactive uninstall confirmation'
   $checks=$script:dialog.FindAll([Windows.Automation.TreeScope]::Descendants,(New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ControlTypeProperty,[Windows.Automation.ControlType]::CheckBox)))
   Save-Screen 'uninstall-interactive-no-checkbox'
   if($checks.Count){throw 'Unexpected checkbox in uninstall confirmation'}
   $buttons=$script:dialog.FindAll([Windows.Automation.TreeScope]::Descendants,(New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ControlTypeProperty,[Windows.Automation.ControlType]::Button)))
   $clicked=$false
   foreach($b in $buttons){if($b.Current.Name -match '^(&?Uninstall|&?Desinstalar)$'){$b.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke();$clicked=$true;break}}
   if(!$clicked){throw 'Uninstall action not found'}
  }else{Run-Setup $uninstaller '/S'}
  Wait-Until {!(Test-Path -LiteralPath $uninstallKey) -and !(Test-Path (Join-Path $install 'horus-desktop.exe'))} 'uninstall completion'
  Verify-Data;Record 'uninstall' @{interactive=$Interactive;dataPreserved=$true;checkboxAbsent=$Interactive}
  if($Interactive){[VmNative]::PostMessage([IntPtr]$script:dialog.Current.NativeWindowHandle,0x10,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null}
 }
 Run-Setup $packages[0] '/S /NS';Check-Install $manifest.packages[0].version
 foreach($folder in @($data,$roaming)){
  New-Item -ItemType Directory $folder -Force|Out-Null
  [VmNative]::Database((Join-Path $folder 'vm-fixture-database.sqlite3'),$true)
  '{"name":"Projeto ficticio"}'|Set-Content (Join-Path $folder 'vm-fixture-project.json')
  '{"voice":"fixture"}'|Set-Content (Join-Path $folder 'vm-fixture-preferences.json')
  [IO.File]::WriteAllBytes((Join-Path $folder 'vm-fixture-media.bin'),[byte[]](1,2,3,4))
 }
 $script:before=Fixtures;$script:before|Set-Content "$outputPath/fixture-hashes-before.json"
 $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--remote-debugging-port=19447 --remote-debugging-address=127.0.0.1'
 if(@([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()|Where-Object Port -eq 19447).Count){throw 'CDP port already occupied; use a clean guest'}
 $env:HORUS_DATA_DIR=Join-Path $data 'workspace'
 $app=Start-Process (Join-Path $install 'horus-desktop.exe') -PassThru
 Wait-Until {$app.Refresh();$app.MainWindowHandle -ne 0} 'desktop window'
 [VmNative]::ShowWindow($app.MainWindowHandle,3)|Out-Null
 Wait-Until {[VmNative]::IsZoomed($app.MainWindowHandle)} 'maximized window'
 $script:target=$null
 Wait-Until {try{$items=Invoke-RestMethod 'http://127.0.0.1:19447/json/list' -TimeoutSec 2;$script:target=@($items|Where-Object type -eq 'page')[0];return !!$script:target.webSocketDebuggerUrl}catch{return $false}} 'WebView CDP'
 $script:socket=New-Object Net.WebSockets.ClientWebSocket
 $script:socket.ConnectAsync([uri]$script:target.webSocketDebuggerUrl,[Threading.CancellationToken]::None).GetAwaiter().GetResult();$script:cdpId=0
 Wait-Until {Evaluate "!![...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Ajustes')"} 'Ajustes button'
 Evaluate "[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Ajustes').click(); true"|Out-Null
 Start-Sleep -Milliseconds 1000
 $layout=Evaluate @'
(()=>{const d=document.documentElement,b=document.body;let white=0;
for(let x=20;x<innerWidth;x+=80)for(let y=20;y<innerHeight;y+=80){let e=document.elementFromPoint(x,y);while(e&&getComputedStyle(e).backgroundColor==='rgba(0, 0, 0, 0)')e=e.parentElement;if(e&&['rgb(255, 255, 255)','rgba(255, 255, 255, 1)'].includes(getComputedStyle(e).backgroundColor))white++;}
return {width:innerWidth,height:innerHeight,scrollHeight:Math.max(d.scrollHeight,b.scrollHeight),scrollWidth:Math.max(d.scrollWidth,b.scrollWidth),whiteSamples:white,settingsVisible:!!document.querySelector('.settings-navigation[aria-current="page"]')};})()
'@
 $layout|ConvertTo-Json|Set-Content "$outputPath/settings-layout.json"
 $shot=Invoke-Cdp 'Page.captureScreenshot' @{format='png';captureBeyondViewport=$false}
 [IO.File]::WriteAllBytes("$outputPath/settings-webview.png",[Convert]::FromBase64String($shot.data));Save-Screen 'settings-maximized-desktop'
 if(!$layout.settingsVisible -or $layout.scrollHeight -gt $layout.height+1 -or $layout.scrollWidth -gt $layout.width+1 -or $layout.whiteSamples -gt 0){throw 'HOR-148 regression: inspect layout JSON/screenshots'}
 Record 'settings-maximized' $layout
 $script:socket.Dispose();$script:socket=$null
 [VmNative]::PostMessage($app.MainWindowHandle,0x10,[IntPtr]::Zero,[IntPtr]::Zero)|Out-Null
 if(!$app.WaitForExit(30000)){throw 'Desktop did not close normally'};$app=$null
 foreach($database in Get-ChildItem -LiteralPath $data -Filter '*.sqlite3' -Recurse -File){[VmNative]::Database($database.FullName,$false)}
 $script:before=Fixtures
 Wait-Until {Start-Sleep -Milliseconds 500;$now=Fixtures;if($now -eq $script:before){return $true};$script:before=$now;return $false} 'stable closed-app data'
 $script:before|Set-Content "$outputPath/fixture-hashes-before.json"
 Run-Setup $packages[1] '/S /NS /UPDATE';Check-Install $manifest.packages[1].version;Verify-Data;Record 'upgrade-data' @{preserved=$true}
 Uninstall $true
 Run-Setup $packages[1] '/S /NS';Check-Install $manifest.packages[1].version;Verify-Data
 Uninstall $false
 Fixtures|Set-Content "$outputPath/fixture-hashes-after.json";$results.status='passed'
}catch{$results.error=$_.Exception.Message;$results.stack=$_.ScriptStackTrace;Write-Warning $results.error
 if($canWrite -and (Get-Command Save-Screen -ErrorAction SilentlyContinue)){try{Save-Screen 'failure'}catch{Write-Warning 'Failure screenshot unavailable'}}
}finally{
 if($script:socket){$script:socket.Dispose()}
 # Preserve failed guests for diagnosis; never kill by name or delete user data.
 if($app){$results.desktopPidLeftForGuestDiagnosis=$app.Id}
 $results.finished=[DateTime]::UtcNow.ToString('o')
 if($canWrite){$results|ConvertTo-Json -Depth 15|Set-Content (Join-Path $OutputDirectory 'results.json')}
 if($transcript){Stop-Transcript|Out-Null}
}
if($results.status -ne 'passed'){exit 1}
