param([switch]$Build,[string]$VersionN='',[string]$VersionNext='')
$ErrorActionPreference='Stop'
# Host preparation only: this script never invokes an installer or starts a guest.
$repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$config=Get-Content "$repo/frontend/src-tauri/tauri.conf.json" -Raw|ConvertFrom-Json
if(!$VersionN){$VersionN=$config.version}
if(!$VersionNext){$v=[version]$VersionN;$VersionNext="$($v.Major).$($v.Minor).$($v.Build+1)"}
foreach($v in @($VersionN,$VersionNext)){if($v -notmatch '^\d+\.\d+\.\d+$'){throw 'Stable three-part versions required'}}
if([version]$VersionNext -le [version]$VersionN){throw 'N+1 must be newer'}
$preflight=[ordered]@{computer=$env:COMPUTERNAME;versions=@($VersionN,$VersionNext);node=!!(Get-Command node -ErrorAction SilentlyContinue);cargo=!!(Get-Command cargo -ErrorAction SilentlyContinue);dependencies=(Test-Path "$repo/frontend/node_modules");sandboxExecutable=!!(Get-Command WindowsSandbox.exe -ErrorAction SilentlyContinue);buildRequested=[bool]$Build}
$preflight|ConvertTo-Json
if(!$Build){return}
if(!$preflight.node -or !$preflight.cargo -or !$preflight.dependencies){throw 'Prepare Node, Rust/MSVC and repository dependencies explicitly first'}
$root=Join-Path ([IO.Path]::GetTempPath()) ('horus-vm-input-'+[guid]::NewGuid())
$inputDir=Join-Path $root 'input';New-Item -ItemType Directory $inputDir -Force|Out-Null
$oldTarget=$env:CARGO_TARGET_DIR
try{
 $env:CARGO_TARGET_DIR=Join-Path $root 'target'
 $packages=@()
 foreach($v in @($VersionN,$VersionNext)){
  $overlay=Join-Path $root "version-$v.json"
  @{version=$v;productName='HORUS';identifier='com.horus.audiovisual'}|ConvertTo-Json|Set-Content $overlay
  & npm --prefix "$repo/frontend" run tauri build -- --config $overlay
  if($LASTEXITCODE){throw "Build $v failed; logs/artifacts retained at $root"}
  $source=Join-Path $env:CARGO_TARGET_DIR "release/bundle/nsis/HORUS_${v}_x64-setup.exe"
  if((Get-Item $source).VersionInfo.ProductName -ne 'HORUS'){throw 'Production product identity required'}
  $name="HORUS-$v.exe";Copy-Item -LiteralPath $source -Destination (Join-Path $inputDir $name)
  $packages+=@{file=$name;version=$v;sha256=(Get-FileHash $source -Algorithm SHA256).Hash}
 }
 @{schema=1;hostComputer=$env:COMPUTERNAME;packages=$packages;sourceCommit=(& git -C $repo rev-parse HEAD)}|ConvertTo-Json -Depth 5|Set-Content "$inputDir/manifest.json"
 Copy-Item "$PSScriptRoot/guest.ps1","$PSScriptRoot/guest-support.ps1" $inputDir
 $preflight|ConvertTo-Json|Set-Content "$root/preflight.json"
 Write-Output "Prepared only: $inputDir (map read-only); choose a separate empty evidence directory"
}finally{$env:CARGO_TARGET_DIR=$oldTarget}
