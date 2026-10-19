#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ApplicationDirectory,
    [string]$ResultDirectory = (Join-Path $env:TEMP ('kilix-integration-' + [Guid]::NewGuid().ToString('N'))),
    [switch]$Desktop,
    [switch]$Clipboard,
    [switch]$AllMonitors
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'setup-common.ps1')
$ApplicationDirectory = (Resolve-Path -LiteralPath $ApplicationDirectory).Path
[IO.Directory]::CreateDirectory($ResultDirectory) | Out-Null
$ResultDirectory = (Resolve-Path -LiteralPath $ResultDirectory).Path
foreach ($name in @('child.json', 'engine-pid.txt', 'window-handle.txt', 'integration.jsonl', 'result.json')) {
    if (Test-Path -LiteralPath (Join-Path $ResultDirectory $name)) { throw 'Use a new result directory so stale evidence cannot pass qualification.' }
}
if (!$Desktop) {
    $name = 'Kilix-Integration-' + [Guid]::NewGuid().ToString('N')
    $arguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath,
        '-Desktop', '-ApplicationDirectory', $ApplicationDirectory, '-ResultDirectory', $ResultDirectory)
    if ($Clipboard) { $arguments += '-Clipboard' }
    if ($AllMonitors) { $arguments += '-AllMonitors' }
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Argument (@($arguments | ForEach-Object { ConvertTo-KilixArgument $_ }) -join ' ')
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
    Register-ScheduledTask -TaskName $name -InputObject (New-ScheduledTask -Action $action -Principal $principal -Settings $settings) | Out-Null
    try {
        Start-ScheduledTask -TaskName $name
        $deadline = [DateTime]::UtcNow.AddSeconds(70)
        do {
            Start-Sleep -Milliseconds 500
            $task = Get-ScheduledTask -TaskName $name
        } while ($task.State -eq 'Running' -and [DateTime]::UtcNow -lt $deadline)
        if ((Get-ScheduledTaskInfo -TaskName $name).LastTaskResult -ne 0 -or !(Test-Path -LiteralPath (Join-Path $ResultDirectory 'result.json'))) {
            throw "Integration qualification failed or did not finish. Logs: $ResultDirectory"
        }
        Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $ResultDirectory 'result.json')
    } finally {
        if ((Get-ScheduledTask -TaskName $name).State -eq 'Running') { Stop-ScheduledTask -TaskName $name }
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
    return
}
$env:PSExecutionPolicyPreference = $null
$env:KILIX_QUAL_RESULT = $ResultDirectory
$env:KILIX_QUAL_CLIPBOARD = if ($Clipboard) { '1' } else { '0' }
$env:KILIX_QUAL_MONITORS = if ($AllMonitors) { '1' } else { '0' }
$env:KILIX_QUAL_CHILD = Join-Path $PSScriptRoot 'tests/integration-child.py'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:KITTY_CONFIG_DIRECTORY = Join-Path $ResultDirectory 'config'
[IO.Directory]::CreateDirectory($env:KITTY_CONFIG_DIRECTORY) | Out-Null
$cwd = Join-Path $ResultDirectory ('path with spaces ' + [char]0x03bb + [char]0x4e2d)
[IO.Directory]::CreateDirectory($cwd) | Out-Null
$engine = Join-Path $ApplicationDirectory 'kitty/launcher/kitty-console.exe'
$watcher = (Join-Path $PSScriptRoot 'tests/integration-session.py').Replace('\', '/')
$arguments = @('--config', (Join-Path $ApplicationDirectory 'config/kilix.conf'), '--title', 'Kilix integration qualification',
    '--directory', $cwd, '-o', ('watcher=' + $watcher), '-o', 'confirm_os_window_close=0',
    $engine, '+runpy', "import os, runpy; runpy.run_path(os.environ['KILIX_QUAL_CHILD'], run_name='__main__')")
$process = New-Object Diagnostics.Process
$process.StartInfo.FileName = $engine
$process.StartInfo.Arguments = (@($arguments | ForEach-Object { ConvertTo-KilixArgument $_ }) -join ' ')
$process.StartInfo.UseShellExecute = $false
$process.StartInfo.RedirectStandardOutput = $true
$process.StartInfo.RedirectStandardError = $true
try {
    $null = $process.Start()
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    $handleFile = Join-Path $ResultDirectory 'window-handle.txt'
    while (!(Test-Path -LiteralPath $handleFile) -and !$process.HasExited -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    $accessibility = @{ status = 'unavailable' }
    try {
        Add-Type -AssemblyName UIAutomationClient
        Add-Type -AssemblyName UIAutomationTypes
        $handle = [IntPtr][long](Get-Content -Raw -Encoding UTF8 -LiteralPath $handleFile)
        $element = [Windows.Automation.AutomationElement]::FromHandle($handle)
        $pattern = $null
        $accessibility = @{ status = 'observed'; controlType = $element.Current.ControlType.ProgrammaticName;
            namePresent = [bool]$element.Current.Name; keyboardFocusable = $element.Current.IsKeyboardFocusable;
            textPattern = $element.TryGetCurrentPattern([Windows.Automation.TextPattern]::Pattern, [ref]$pattern);
            limitation = 'A window/UIA observation does not establish screen-reader terminal text support.' }
    } catch { $accessibility = @{ status = 'unavailable'; reason = "$($_.Exception.Message)" } }
    if (!$process.WaitForExit(30000)) { throw 'Integration test window timed out.' }
    [IO.File]::WriteAllText((Join-Path $ResultDirectory 'stdout.log'), $stdout.Result)
    [IO.File]::WriteAllText((Join-Path $ResultDirectory 'stderr.log'), $stderr.Result)
    if ($process.ExitCode -ne 0) { throw "Engine failed: $($process.ExitCode)" }
    $records = @(Get-Content -Encoding UTF8 -LiteralPath (Join-Path $ResultDirectory 'integration.jsonl') | ForEach-Object { ConvertFrom-Json $_ })
    if ($records | Where-Object { $_.check -eq 'error' }) { throw 'Watcher reported an integration failure.' }
    $child = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $ResultDirectory 'child.json') | ConvertFrom-Json
    $unicode = 'Unicode input: ' + [char]0x03bb + ' ' + [char]0x4e2d + ' ' + [char]::ConvertFromUtf32(0x1f642)
    $drop = 'C:\drop path with spaces\' + [char]0x03bb + [char]0x4e2d + '.txt'
    if ($child.received.Count -ne 2 -or $child.received[0] -cne $unicode -or $child.received[1] -cne $drop) { throw 'Unicode input or text drop handler changed the received text.' }
    if ($child.cwd -ne $cwd) { throw 'Unicode working directory was not preserved.' }
    Write-KilixJson (Join-Path $ResultDirectory 'result.json') @{
        inputAndDropHandler = 'passed'; unicodeWorkingDirectory = 'passed'; observations = $records; accessibility = $accessibility;
        manual = @('Explorer/OLE file drag and drop', 'screen-reader terminal content', 'IME candidate UI',
            'visual sharpness after mixed-DPI moves', 'existing shell profile behavior: run test-startup.ps1') }
} catch {
    [IO.File]::WriteAllText((Join-Path $ResultDirectory 'failure.txt'), ($_ | Out-String))
    throw
} finally {
    if (!$process.HasExited) { & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F | Out-Null }
    $process.Dispose()
}
