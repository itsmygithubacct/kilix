#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ApplicationDirectory,
    [string]$WslDistribution,
    [string]$ResultDirectory = (Join-Path $env:TEMP ('kilix-desktop-test-' + [Guid]::NewGuid().ToString('N'))),
    [switch]$Desktop
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ApplicationDirectory = (Resolve-Path -LiteralPath $ApplicationDirectory).Path
New-Item -ItemType Directory -Force $ResultDirectory | Out-Null
$ResultDirectory = (Resolve-Path -LiteralPath $ResultDirectory).Path
function Quote-Arg([string]$Value) {
    # Paths and names used by this runner contain no embedded quotes.
    if ($Value.Contains('"')) { throw 'A test path contains an unsupported quote.' }
    return '"' + $Value.TrimEnd('\') + '"'
}
if (!$Desktop) {
    $name = 'Kilix-Startup-Test-' + [Guid]::NewGuid().ToString('N')
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File ' + (Quote-Arg $PSCommandPath) +
        ' -Desktop -ApplicationDirectory ' + (Quote-Arg $ApplicationDirectory) +
        ' -ResultDirectory ' + (Quote-Arg $ResultDirectory)
    if ($WslDistribution) { $arguments += ' -WslDistribution ' + (Quote-Arg $WslDistribution) }
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
    Register-ScheduledTask -TaskName $name -InputObject (New-ScheduledTask -Action $action -Principal $principal -Settings $settings) | Out-Null
    try {
        Start-ScheduledTask -TaskName $name
        Start-Sleep -Seconds 2
        $deadline = [DateTime]::UtcNow.AddSeconds(75)
        while ((Get-ScheduledTask -TaskName $name).State -eq 'Running' -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 250 }
        $result = Get-ScheduledTaskInfo -TaskName $name
        if ($result.LastTaskResult -ne 0) { throw "Desktop test failed ($($result.LastTaskResult)). Logs: $ResultDirectory" }
        Get-Content -LiteralPath (Join-Path $ResultDirectory 'result.txt')
        Write-Output "Logs: $ResultDirectory"
    } finally {
        if ((Get-ScheduledTask -TaskName $name).State -eq 'Running') { Stop-ScheduledTask -TaskName $name }
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    return
}


Start-Transcript -Path (Join-Path $ResultDirectory 'transcript.txt') | Out-Null
try {
# Do not let the runner's temporary policy override leak into the application.
$env:PSExecutionPolicyPreference = $null
$env:PATH="$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\WindowsPowerShell\v1.0;$env:SystemRoot\System32\OpenSSH"
$env:PYTHONHOME=$null
$env:PYTHONPATH=$null
$expectedPolicy = (& powershell.exe -NoProfile -Command Get-ExecutionPolicy | Out-String).Trim()
$profiles = @('powershell','cmd')
if ($WslDistribution) { $profiles += 'wsl' }
foreach ($profile in $profiles) {
 $results=Join-Path $ResultDirectory $profile
 New-Item -ItemType Directory -Force $results | Out-Null
 $env:KILIX_TEST_RESULT_DIR=$results
 $env:KILIX_TEST_PROFILE=$profile
 $watcher=(Join-Path $PSScriptRoot 'tests\startup-session.py').Replace('\','/')
 $arguments='-o ' + (Quote-Arg ('watcher=' + $watcher)) + ' -o confirm_os_window_close=0'
 if ($profile -eq 'wsl') { $arguments='--wsl '+(Quote-Arg $WslDistribution)+' '+$arguments }
 if ($profile -eq 'cmd') { $arguments='--cmd '+$arguments }
 Start-Process -FilePath (Join-Path $ApplicationDirectory 'Kilix.exe') -ArgumentList $arguments -WorkingDirectory $env:USERPROFILE
 $deadline=[DateTime]::UtcNow.AddSeconds(20)
 while (!(Test-Path "$results\engine-pid.txt") -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
 $enginePid=[int](Get-Content "$results\engine-pid.txt")
 $p=Get-Process -Id $enginePid
 if (!$p.WaitForExit(40000)) { $p.Kill(); throw "Startup test timed out: $profile" }
 $samples=@(Get-Content "$results\interactive.jsonl" | ForEach-Object { ConvertFrom-Json $_ })
 if (@($samples | Where-Object { $_.error }).Count) { throw "Watcher errors: $profile" }
 $latest=@($samples | Where-Object { $_.windows.Count -eq 2 })[-1]
 if (!$latest) { throw "Default split failed: $profile" }
 foreach ($pane in $latest.windows) {
  # Retain evidence across redraws and window resizes while the test runs.
  $text = (@($samples | ForEach-Object { $_.windows } | Where-Object { $_.id -eq $pane.id } | ForEach-Object { $_.text }) -join "`n")
  if ($text -notmatch '(?m)^KILIX_STARTUP_READY\s*$') { throw "Interactive input failed: $profile" }
  if ($text -match 'PSSecurityException|UnauthorizedAccess|CommandNotFoundException|cannot be loaded|is not digitally signed') { throw "Startup error: $profile" }
  if ($profile -ne 'wsl' -and $text -notmatch '(?m)^Kilix 0\.2\.2 Windows development build') { throw "Console version failed: $profile" }
  if ($profile -eq 'powershell' -and $text -notmatch ('(?m)^KILIX_POLICY_'+[regex]::Escape($expectedPolicy)+'\s*$')) { throw 'PowerShell policy changed' }
 }
 "$profile PASS" | Add-Content (Join-Path $ResultDirectory 'result.txt')
}

} finally { Stop-Transcript | Out-Null }
