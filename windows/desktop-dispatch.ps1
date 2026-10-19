# One-shot, same-user interactive desktop dispatch for native, WSL and SSH.
param([Parameter(Mandatory=$true)][string]$Launcher)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$operation = ''
$task = $null
$folder = $null
$work = $null
$taskName = 'KilixDesktop-' + [Guid]::NewGuid().ToString('N')
$reply = $null
$received = $false
try {
    $payload = [Console]::In.ReadToEnd()
    if ($payload.Length -gt 1048576) { throw 'Request exceeds 1 MiB' }
    $request = ConvertFrom-Json -InputObject $payload
    $operation = [string]$request.operation
    if ($request.schema -ne 'kilix.desktop/v1') { throw 'Unsupported desktop request schema' }
    $timeout = 20.0
    if ($null -ne $request.timeout) { $timeout = [double]$request.timeout }
    if ([Double]::IsNaN($timeout) -or [Double]::IsInfinity($timeout) -or $timeout -lt 1 -or $timeout -gt 60) { throw 'Invalid timeout' }
    $worker = Join-Path $PSScriptRoot 'desktop.ps1'
    if (!(Test-Path -LiteralPath $worker -PathType Leaf)) { throw 'Desktop backend is missing; reinstall the complete Kilix package' }
    if (!(Test-Path -LiteralPath $Launcher -PathType Leaf)) { throw 'The graphical Python launcher is missing' }

    # Explicitly select the caller's one active login. A service/SSH session's
    # own session ID is not the desktop, and a disconnected RDP seat is not a
    # substitute for the active desktop.
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.Principal;
public static class KilixDesktopLogins {
    [StructLayout(LayoutKind.Sequential)] struct Session { public int Id; public IntPtr Station; public int State; }
    [DllImport("wtsapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool WTSEnumerateSessions(IntPtr server,int reserved,int version,out IntPtr sessions,out int count);
    [DllImport("wtsapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool WTSQuerySessionInformation(IntPtr server,int session,int info,out IntPtr buffer,out int bytes);
    [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr memory);
    static string Info(int session,int field) { IntPtr p; int n; if(!WTSQuerySessionInformation(IntPtr.Zero,session,field,out p,out n)) return ""; try{return Marshal.PtrToStringUni(p)??"";} finally {WTSFreeMemory(p);} }
    public static int[] ForUser(string sid) {
        IntPtr p; int count; var found=new List<int>();
        if(!WTSEnumerateSessions(IntPtr.Zero,0,1,out p,out count)) throw new System.ComponentModel.Win32Exception();
        try { for(int i=0;i<count;i++) { var s=(Session)Marshal.PtrToStructure(IntPtr.Add(p,i*Marshal.SizeOf(typeof(Session))),typeof(Session)); if(s.State!=0) continue;
            string user=Info(s.Id,5),domain=Info(s.Id,7); if(user.Length==0)continue;
            try { var account=new NTAccount(domain,user); if(account.Translate(typeof(SecurityIdentifier)).Value==sid)found.Add(s.Id); } catch(IdentityNotMappedException){}
        }} finally { WTSFreeMemory(p); } return found.ToArray();
    }
}
'@
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $sessions = @([KilixDesktopLogins]::ForUser($identity.User.Value))
    if ($sessions.Count -ne 1) {
        $reply = @{schema='kilix.desktop/v1';ok=$false;operation=$operation;error=@{code='desktop_unavailable';message='Exactly one active Windows desktop for this user is required. Log in or reconnect that desktop.'}}
    } else {
        $work = Join-Path ([IO.Path]::GetTempPath()) $taskName
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true, $false)
        $acl.SetOwner($identity.User)
        foreach ($sid in @($identity.User, (New-Object Security.Principal.SecurityIdentifier('S-1-5-18')))) {
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
            $acl.AddAccessRule($rule)
        }
        [IO.Directory]::CreateDirectory($work, $acl) | Out-Null
        $inputPath = Join-Path $work 'request.json'
        $outputPath = Join-Path $work 'response.json'
        [IO.File]::WriteAllText($inputPath, $payload, $utf8)
        # Launch PowerShell without a console from the bundled GUI interpreter.
        # Starting powershell.exe directly steals foreground briefly before its
        # -WindowStyle Hidden option is processed, breaking targeted input.
        $hostScript = Join-Path $work 'worker.py'
        $workerCommand = @((Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'),
            '-NoLogo', '-NoProfile', '-STA', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', $worker, '-RequestPath', $inputPath, '-ResponsePath', $outputPath)
        $hostCode = 'import subprocess;raise SystemExit(subprocess.call(' + (ConvertTo-Json -InputObject $workerCommand -Compress) + ',creationflags=0x08000000))'
        [IO.File]::WriteAllText($hostScript, $hostCode, $utf8)
        $service = New-Object -ComObject 'Schedule.Service'
        $service.Connect()
        $folder = $service.GetFolder('\')
        $definition = $service.NewTask(0)
        $definition.RegistrationInfo.Description = 'Kilix one-shot Windows desktop command'
        $definition.Principal.UserId = $identity.User.Value
        $definition.Principal.LogonType = 3 # interactive token, no stored password
        $definition.Principal.RunLevel = 0
        $definition.Settings.Enabled = $true
        $definition.Settings.AllowDemandStart = $true
        $definition.Settings.Hidden = $true
        $definition.Settings.DisallowStartIfOnBatteries = $false
        $definition.Settings.StopIfGoingOnBatteries = $false
        $definition.Settings.ExecutionTimeLimit = 'PT' + ([Math]::Ceiling($timeout) + 5) + 'S'
        $action = $definition.Actions.Create(0)
        $action.Path = $Launcher
        $action.Arguments = '+runpy "import runpy,sys;runpy.run_path(sys.argv[1],run_name=''__main__'')" "' + $hostScript + '"'
        $task = $folder.RegisterTaskDefinition($taskName, $definition, 2, $identity.User.Value, $null, 3, $null)
        $running = $task.RunEx($null, 4, [int]$sessions[0], $null)
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while (!(Test-Path -LiteralPath $outputPath -PathType Leaf) -and $watch.Elapsed.TotalSeconds -lt $timeout) {
            Start-Sleep -Milliseconds 100
        }
        if (Test-Path -LiteralPath $outputPath -PathType Leaf) {
            if ((Get-Item -LiteralPath $outputPath).Length -gt 50331648) { throw 'Desktop response exceeds 48 MiB.' }
            $reply = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($outputPath, $utf8))
            $received = $true
        } else {
            $reply = @{schema='kilix.desktop/v1';ok=$false;operation=$operation;error=@{code='timeout';message='Desktop action timed out. It may have run; inspect the desktop before retrying.'}}
        }
    }
} catch {
    $reply = @{schema='kilix.desktop/v1';ok=$false;operation=$operation;error=@{code='transport_error';message=$_.Exception.Message}}
} finally {
    if ($null -ne $task) {
        # A successful launch may have left an application running. Do not
        # terminate its task's process tree after receiving the final response.
        if (!$received) { try { $task.Stop(0) } catch {} }
        try { $folder.DeleteTask($taskName, 0) } catch {}
    }
    if ($work -and (Test-Path -LiteralPath $work)) {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}
[Console]::Out.WriteLine(($reply | ConvertTo-Json -Depth 30 -Compress))
if (!$reply.ok) { exit 1 }
