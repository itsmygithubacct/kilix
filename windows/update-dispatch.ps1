#requires -Version 5.1
# SPDX-License-Identifier: GPL-3.0-only
# Copied to the managed install root when scheduling is explicitly enabled.
# Keep this dispatcher independent of the code in any retained generation.
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Apply')][string]$Action = 'Check',
    [ValidateSet('stable', 'preview')][string]$Channel = 'stable',
    [switch]$NoShortcuts,
    [string]$SignerThumbprint,
    [string]$WslDistribution,
    [string]$WslUser
)
$ErrorActionPreference = 'Stop'
function Assert-KilixDispatchPath([string]$Path) {
    $item = [IO.Path]::GetFullPath($Path)
    while ($item) {
        if ((Test-Path -LiteralPath $item) -and
            ((Get-Item -Force -LiteralPath $item).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Scheduled updates do not follow reparse points: $item"
        }
        $item = [IO.Path]::GetDirectoryName($item)
    }
}
$statePath = Join-Path $PSScriptRoot 'install-state.json'
Assert-KilixDispatchPath $statePath
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($state.schema -ne 1 -or !$state.current -or $state.current.path -cnotmatch '^versions/[a-f0-9-]+/kilix$') {
    throw 'Invalid managed installation state for scheduled update.'
}
$updater = Join-Path $PSScriptRoot ($state.current.path + '/update.ps1')
Assert-KilixDispatchPath $updater
if (!(Test-Path -LiteralPath $updater -PathType Leaf)) { throw "Active Kilix updater is missing: $updater" }
& $updater -Destination $PSScriptRoot @PSBoundParameters
