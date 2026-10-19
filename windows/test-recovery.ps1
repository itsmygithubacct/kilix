#requires -Version 5.1
param(
    [Parameter(Mandatory=$true)][string]$ApplicationDirectory,
    [Parameter(Mandatory=$true)][string]$EngineDirectory
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'setup-common.ps1')
$ApplicationDirectory = (Resolve-Path -LiteralPath $ApplicationDirectory).Path
$EngineDirectory = (Resolve-Path -LiteralPath $EngineDirectory).Path
$savedPath = $env:PATH
$savedPythonHome = $env:PYTHONHOME
$savedPythonPath = $env:PYTHONPATH
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYTHONHOME = $null
    $env:PYTHONPATH = $null
    # Use the portable runtime. The general engine test runner removes home
    # entries from PATH, which prevents an unbundled development launcher from
    # finding its toolchain DLLs when these tests start helper processes.
    $code = 'import sys,unittest; sys.path.append(sys.argv[1]); suite=unittest.defaultTestLoader.loadTestsFromName(sys.argv[2]); result=unittest.TextTestRunner(verbosity=2).run(suite); raise SystemExit(0 if result.wasSuccessful() else 1)'
    $result = Invoke-KilixProcess (Join-Path $ApplicationDirectory 'kitty/launcher/kitty-console.exe') `
        @('+runpy', $code, $EngineDirectory, 'kitty_tests.windows_recovery') 180
    Write-Output $result.stdout
    Write-Output $result.stderr
    if ($result.exitCode -ne 0) { throw "Native recovery tests failed: $($result.exitCode)" }
    Write-Host 'PASS: owned native session recovery, process identity, exit codes and teardown'
} finally {
    $env:PATH = $savedPath
    $env:PYTHONHOME = $savedPythonHome
    $env:PYTHONPATH = $savedPythonPath
}
