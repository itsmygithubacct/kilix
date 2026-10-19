#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ApplicationDirectory,
    [string]$ResultDirectory = (Join-Path $env:TEMP ('kilix-input-test-' + [Guid]::NewGuid().ToString('N'))),
    [switch]$Desktop
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ApplicationDirectory = (Resolve-Path -LiteralPath $ApplicationDirectory).Path
New-Item -ItemType Directory -Force $ResultDirectory | Out-Null
$ResultDirectory = (Resolve-Path -LiteralPath $ResultDirectory).Path
function Quote-Arg([string]$Value) {
    if ($Value.Contains('"')) { throw 'A test path contains an unsupported quote.' }
    return '"' + $Value.TrimEnd('\') + '"'
}
if (!$Desktop) {
    $name = 'Kilix-Input-Test-' + [Guid]::NewGuid().ToString('N')
    $arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File ' + (Quote-Arg $PSCommandPath) +
        ' -Desktop -ApplicationDirectory ' + (Quote-Arg $ApplicationDirectory) +
        ' -ResultDirectory ' + (Quote-Arg $ResultDirectory)
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $arguments
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
    Register-ScheduledTask -TaskName $name -InputObject (New-ScheduledTask -Action $action -Principal $principal -Settings $settings) | Out-Null
    try {
        Start-ScheduledTask -TaskName $name
        Start-Sleep -Seconds 2
        $deadline = [DateTime]::UtcNow.AddSeconds(60)
        while ((Get-ScheduledTask -TaskName $name).State -eq 'Running' -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 250 }
        $result = Get-ScheduledTaskInfo -TaskName $name
        if ($result.LastTaskResult -ne 0 -or !(Test-Path "$ResultDirectory\input-pass.txt")) {
            throw "Native input test failed ($($result.LastTaskResult)). Logs: $ResultDirectory"
        }
        Get-Content "$ResultDirectory\input-pass.txt"
        Get-Content "$ResultDirectory\input-message-results.json"
    } finally {
        if ((Get-ScheduledTask -TaskName $name).State -eq 'Running') { Stop-ScheduledTask -TaskName $name }
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    return
}
if (Test-Path "$ResultDirectory\input-ready") { throw 'Use a fresh result directory.' }
$env:KILIX_TEST_RESULT_DIR = $ResultDirectory
$env:KILIX_TEST_ISOLATED = '1'
$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:KITTY_CONFIG_DIRECTORY = Join-Path $ResultDirectory 'config'
New-Item -ItemType Directory -Force $env:KITTY_CONFIG_DIRECTORY | Out-Null
$engine = Join-Path $ApplicationDirectory 'kitty\launcher\kitty-console.exe'
$watcher = (Join-Path $PSScriptRoot 'tests\input-session.py').Replace('\', '/')
$child = Join-Path $PSScriptRoot 'tests\input-child.py'
$entry = "import runpy,sys; runpy.run_path(sys.argv[1],run_name='__main__')"
$arguments = '--config ' + (Quote-Arg (Join-Path $ApplicationDirectory 'config\kilix.conf')) +
    ' --title "Kilix native input test" -o ' + (Quote-Arg ('watcher=' + $watcher)) +
    ' -o confirm_os_window_close=0 ' + (Quote-Arg $engine) + ' +runpy ' + (Quote-Arg $entry) + ' ' + (Quote-Arg $child)
$process = New-Object System.Diagnostics.Process
$process.StartInfo.FileName = $engine
$process.StartInfo.Arguments = $arguments
$process.StartInfo.WorkingDirectory = $ResultDirectory
$process.StartInfo.UseShellExecute = $false
$process.StartInfo.RedirectStandardOutput = $true
$process.StartInfo.RedirectStandardError = $true
try {
    $process.Start() | Out-Null
    $outputRead = $process.StandardOutput.ReadToEndAsync()
    $errorRead = $process.StandardError.ReadToEndAsync()
    if (!$process.WaitForExit(35000)) { $process.Kill(); throw 'Native input test timed out.' }
    $outputRead.Result | Set-Content "$ResultDirectory\stdout.log"
    $errorRead.Result | Set-Content "$ResultDirectory\stderr.log"
    if (Test-Path "$ResultDirectory\input-error.json") { throw (Get-Content "$ResultDirectory\input-error.json" -Raw) }
    if ($process.ExitCode -ne 0 -or !(Test-Path "$ResultDirectory\input-pass.txt")) { throw 'Native input assertions did not pass.' }
} finally {
    $process.Dispose()
}
