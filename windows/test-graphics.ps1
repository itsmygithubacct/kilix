#requires -Version 5.1
[CmdletBinding()]
param([string]$BuildDirectory = "$env:LOCALAPPDATA\gpu_terminal\kilix-win\build")
$ErrorActionPreference = 'Stop'
$exe = Join-Path $BuildDirectory 'kilix-wgl-probe.exe'
if (!(Test-Path -LiteralPath $exe)) { throw 'Run build.ps1 first.' }
$name = 'Kilix-WGL-Probe-' + [Guid]::NewGuid().ToString('N')
$report = Join-Path $BuildDirectory ($name + '.txt')
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $exe -Argument ('"' + $report + '"')
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
$task = New-ScheduledTask -Action $action -Principal $principal -Settings $settings
$registered = $false
try {
    Register-ScheduledTask -TaskName $name -InputObject $task | Out-Null
    $registered = $true
    Start-ScheduledTask -TaskName $name
    $deadline = [DateTime]::UtcNow.AddSeconds(40)
    $complete = $false
    do {
        Start-Sleep -Milliseconds 200
        if ((Test-Path -LiteralPath $report) -and
            (Get-ScheduledTask -TaskName $name).State -ne 'Running') {
            $complete = $true
            break
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    if (!$complete) { throw 'Desktop probe did not finish. The build user must be logged in interactively.' }
    $text = Get-Content -LiteralPath $report -Raw
    Write-Output $text
    if ((Get-ScheduledTaskInfo -TaskName $name).LastTaskResult -ne 0 -or $text -notmatch 'RESULT=PASS') {
        throw "Graphics capability check failed. Report: $report"
    }
    Write-Output "Report: $report"
} finally {
    if ($registered) {
        if ((Get-ScheduledTask -TaskName $name).State -eq 'Running') {
            Stop-ScheduledTask -TaskName $name
        }
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
}
