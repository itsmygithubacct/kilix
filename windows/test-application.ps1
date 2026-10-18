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
    $name = 'Kilix-Desktop-Test-' + [Guid]::NewGuid().ToString('N')
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

# Exercise the portable runtime without MSYS2, Python or Go on PATH.
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\WindowsPowerShell\v1.0;$env:SystemRoot\System32\OpenSSH"
$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:FONTCONFIG_FILE = $null
$env:FONTCONFIG_PATH = $null
$env:KILIX_TEST_RESULT_DIR = $ResultDirectory
$env:KILIX_TEST_WSL_DISTRO = $WslDistribution
$env:KITTY_CONFIG_DIRECTORY = Join-Path $ResultDirectory 'config'
New-Item -ItemType Directory -Force $env:KITTY_CONFIG_DIRECTORY | Out-Null
$child = Join-Path $ResultDirectory 'child.ps1'
@'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Write-Host 'KILIX_POWERSHELL_READY'
$escape = [char]27
[Console]::Write("${escape}_Ga=T,f=24,s=1,v=1,i=42;/wAA${escape}\")
Start-Sleep -Seconds 24
'@ | Set-Content -LiteralPath $child -Encoding UTF8
$watcher = (Join-Path $PSScriptRoot 'tests\gui-session.py').Replace('\', '/')
$arguments = '--config ' + (Quote-Arg (Join-Path $ApplicationDirectory 'config\kilix.conf')) +
    ' --title "Kilix Windows application test" -o ' + (Quote-Arg ('watcher=' + $watcher)) +
    ' -o confirm_os_window_close=0 powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ' + (Quote-Arg $child)
$process = New-Object System.Diagnostics.Process
$process.StartInfo.FileName = Join-Path $ApplicationDirectory 'kitty\launcher\kitty-console.exe'
$process.StartInfo.Arguments = $arguments
$process.StartInfo.WorkingDirectory = $ResultDirectory
$process.StartInfo.UseShellExecute = $false
$process.StartInfo.RedirectStandardOutput = $true
$process.StartInfo.RedirectStandardError = $true
try {
    $process.Start() | Out-Null
    $outputRead = $process.StandardOutput.ReadToEndAsync()
    $errorRead = $process.StandardError.ReadToEndAsync()
    if (!$process.WaitForExit(50000)) { $process.Kill(); throw 'The application did not exit within 50 seconds.' }
    $outputRead.Result | Set-Content (Join-Path $ResultDirectory 'stdout.log')
    $errorRead.Result | Set-Content (Join-Path $ResultDirectory 'stderr.log')
    if ($process.ExitCode -ne 0) { throw "Engine exit code: $($process.ExitCode)" }
    $records = @(Get-Content (Join-Path $ResultDirectory 'desktop-ui-results.jsonl') | ForEach-Object { ConvertFrom-Json $_ })
    if (@($records | Where-Object { $_.error }).Count) { throw 'A GUI watcher action failed.' }
    if (!($records | Where-Object { $_.fullscreen_round_trips -eq 'passed' })) { throw 'Fullscreen geometry or placement restoration failed.' }
    $windows = @($records | ForEach-Object { $_.windows })
    if (!($windows | Where-Object { $_.text -match 'KILIX_POWERSHELL_READY' })) { throw 'PowerShell output was not rendered.' }
    if (!($windows | Where-Object { $_.text -match 'KILIX_CMD_READY' })) { throw 'Command Prompt output was not rendered.' }
    if ($WslDistribution -and !($windows | Where-Object { $_.text -match 'KILIX_WSL_READY' })) { throw 'WSL output was not rendered.' }
    if ($WslDistribution -and !($windows | Where-Object { $_.text -match 'KILIX_WSL_READY' -and $_.images -ge 1 -and $_.uploads.full -ge 1 })) { throw 'The WSL graphics stream was not rendered.' }
    if (!($windows | Where-Object { $_.images -ge 1 -and $_.uploads.full -ge 1 })) { throw 'The graphics image was not uploaded to the GPU.' }
    if (!($records | Where-Object { $_.tabs -ge 2 })) { throw 'The new tab was not created.' }
    if ($WslDistribution -and !($records | Where-Object { $_.windows.Count -ge 3 })) { throw 'The split was not created.' }
    'PASS: portable native rendering, PowerShell, Command Prompt, tabs, graphics, clean shutdown' | Set-Content (Join-Path $ResultDirectory 'result.txt')
    'PASS: native fullscreen geometry, hidden caption controls, normal and maximized restoration' | Add-Content (Join-Path $ResultDirectory 'result.txt')
    if ($WslDistribution) { "PASS: WSL distribution $WslDistribution and pane split" | Add-Content (Join-Path $ResultDirectory 'result.txt') }
} catch {
    $_ | Out-String | Set-Content (Join-Path $ResultDirectory 'failure.txt')
    throw
} finally { $process.Dispose() }
