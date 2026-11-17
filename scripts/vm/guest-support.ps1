# Guest helpers: loaded only after guest attestation and host-name refusal.
Add-Type -AssemblyName System.Drawing,System.Windows.Forms,UIAutomationClient,UIAutomationTypes
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class VmNative {
 [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr w);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr w,int mode);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr w,uint msg,IntPtr a,IntPtr b);
 [DllImport("winsqlite3.dll",CharSet=CharSet.Unicode)] static extern int sqlite3_open16(string p,out IntPtr db);
 [DllImport("winsqlite3.dll",CallingConvention=CallingConvention.Cdecl)] static extern int sqlite3_exec(IntPtr db,[MarshalAs(UnmanagedType.LPStr)] string sql,IntPtr cb,IntPtr arg,out IntPtr err);
 [DllImport("winsqlite3.dll",CharSet=CharSet.Unicode)] static extern int sqlite3_prepare16_v2(IntPtr db,string sql,int n,out IntPtr st,IntPtr tail);
 [DllImport("winsqlite3.dll")] static extern int sqlite3_step(IntPtr st);
 [DllImport("winsqlite3.dll")] static extern IntPtr sqlite3_column_text16(IntPtr st,int col);
 [DllImport("winsqlite3.dll")] static extern int sqlite3_finalize(IntPtr st);
 [DllImport("winsqlite3.dll")] static extern int sqlite3_close(IntPtr db);
 public static void Database(string path,bool seed) {
  IntPtr db; if(sqlite3_open16(path,out db)!=0) throw new Exception("SQLite open failed");
  try { IntPtr err; if(seed && sqlite3_exec(db,"CREATE TABLE fixture(v TEXT); INSERT INTO fixture VALUES('preserve');",IntPtr.Zero,IntPtr.Zero,out err)!=0) throw new Exception("SQLite seed failed");
   IntPtr st; if(sqlite3_prepare16_v2(db,"PRAGMA integrity_check",-1,out st,IntPtr.Zero)!=0) throw new Exception("SQLite prepare failed");
   try {if(sqlite3_step(st)!=100 || Marshal.PtrToStringUni(sqlite3_column_text16(st,0))!="ok") throw new Exception("SQLite integrity failed");} finally{sqlite3_finalize(st);}
  } finally {sqlite3_close(db);}
 }
}
"@
function Save-Screen([string]$Name){
 $b=[Windows.Forms.SystemInformation]::VirtualScreen
 $image=New-Object Drawing.Bitmap $b.Width,$b.Height
 $g=[Drawing.Graphics]::FromImage($image)
 try{$g.CopyFromScreen($b.Left,$b.Top,0,0,$image.Size);$image.Save((Join-Path $OutputDirectory "$Name.png"))}finally{$g.Dispose();$image.Dispose()}
}
function Wait-Until([scriptblock]$Check,[string]$Reason,[int]$Seconds=60){
 $end=[DateTime]::UtcNow.AddSeconds($Seconds)
 do{if(& $Check){return};Start-Sleep -Milliseconds 200}while([DateTime]::UtcNow -lt $end)
 throw "Timeout: $Reason"
}
function Invoke-Cdp([string]$Method,$Params=@{}){
 $script:cdpId++;$id=$script:cdpId
 $bytes=[Text.Encoding]::UTF8.GetBytes((@{id=$id;method=$Method;params=$Params}|ConvertTo-Json -Depth 20 -Compress))
 $cancel=New-Object Threading.CancellationTokenSource 15000
 try{
  $script:socket.SendAsync([ArraySegment[byte]]::new($bytes),[Net.WebSockets.WebSocketMessageType]::Text,$true,$cancel.Token).GetAwaiter().GetResult()
  do{
   $stream=New-Object IO.MemoryStream
   try{do{$buffer=New-Object byte[] 65536;$part=$script:socket.ReceiveAsync([ArraySegment[byte]]::new($buffer),$cancel.Token).GetAwaiter().GetResult();if($part.MessageType -eq 'Close'){throw 'CDP closed'};$stream.Write($buffer,0,$part.Count)}while(!$part.EndOfMessage)
    $reply=[Text.Encoding]::UTF8.GetString($stream.ToArray())|ConvertFrom-Json
   }finally{$stream.Dispose()}
  }while($reply.id -ne $id)
  if($reply.error){throw ($reply.error|ConvertTo-Json -Compress)}
  return $reply.result
 }finally{$cancel.Dispose()}
}
function Evaluate([string]$Expression){
 $r=Invoke-Cdp 'Runtime.evaluate' @{expression=$Expression;returnByValue=$true;awaitPromise=$true}
 if($r.exceptionDetails){throw ($r.exceptionDetails|ConvertTo-Json -Compress)}
 return $r.result.value
}
function Run-Setup([string]$Path,[string]$Arguments){
 # Never use /D: the initial and remembered default are the subject of this test.
 Write-Host "Starting guest setup: $Path $Arguments"
 $p=Start-Process -FilePath $Path -ArgumentList $Arguments -PassThru
 if(!$p.WaitForExit(180000)){throw 'Installer timeout (possible elevation/dialog); do not accept a UAC prompt'}
 if($p.ExitCode){throw "Installer exit $($p.ExitCode)"}
 Write-Host 'Guest setup exit: 0'
}
