#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$Archive)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../setup-common.ps1')
# Retain this deep Unicode/space layout: full source closures can cross MAX_PATH.
# The small regression also checks integrity and cleanup beyond that boundary.
& (Join-Path $PSScriptRoot 'test-setup-longpaths.ps1')
$root = Join-Path ([IO.Path]::GetTempPath()) ('kilix-installed-test-' + [Guid]::NewGuid().ToString('N'))
$savedLocal = $env:LOCALAPPDATA
try {
    $env:LOCALAPPDATA = Join-Path $root 'user settings'
    $config = Join-Path $env:LOCALAPPDATA 'Kilix'
    [IO.Directory]::CreateDirectory($config) | Out-Null
    [IO.File]::WriteAllText((Join-Path $config 'kitty.conf'), '# user settings retained')
    $destination = Join-Path $root ('application with spaces ' + [char]0x03bb)
    $args = @{ Archive = (Resolve-Path -LiteralPath $Archive).Path; Sha256 = (Get-FileHash -LiteralPath $Archive).Hash;
        Destination = $destination; NoShortcuts = $true }
    $one = Invoke-KilixInstall @args
    $two = Invoke-KilixInstall @args
    if ($two.previous.path -ne $one.current.path) { throw 'Upgrade did not retain the previous package.' }
    $rolled = Invoke-KilixInstall -Action Rollback -Destination $destination -NoShortcuts
    if ($rolled.current.path -ne $one.current.path) { throw 'Rollback did not activate the retained package.' }
    if ([IO.File]::ReadAllText((Join-Path $config 'kitty.conf')) -ne '# user settings retained') { throw 'User config changed.' }
    Write-Host 'PASS: actual archive install, Unicode/spaces paths, CLI health, upgrade, rollback and config retention'
} finally {
    $env:LOCALAPPDATA = $savedLocal
    $filesystemRoot = ConvertTo-KilixFileSystemPath $root
    if (Test-Path -LiteralPath $filesystemRoot) { Remove-Item -LiteralPath $filesystemRoot -Recurse -Force }
}
