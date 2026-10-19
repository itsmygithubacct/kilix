#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Rollback', 'Recover', 'Status')][string]$Action = 'Install',
    [string]$Archive,
    [string]$Sha256,
    [string]$Destination = "$env:LOCALAPPDATA\Programs\Kilix",
    [switch]$NoShortcuts,
    [string]$SignerThumbprint
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'setup-common.ps1')
$state = Invoke-KilixInstall -Action $Action -Archive $Archive -Sha256 $Sha256 `
    -Destination $Destination -NoShortcuts:$NoShortcuts -SignerThumbprint $SignerThumbprint
if ($state) {
    $state | ConvertTo-Json -Depth 8
    Write-Host ('Application: ' + (Join-Path $Destination ($state.current.path + '/Kilix.exe')))
}
Write-Host 'Settings retained at %LOCALAPPDATA%\Kilix\kitty.conf. Reopen shells after upgrading.'
