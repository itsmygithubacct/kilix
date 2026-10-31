#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ApplicationDirectory)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'setup-common.ps1')

# Run a disposable copy of the packaged interpreter and its bundled modules.
# Isolated Python ignores PYTHONDONTWRITEBYTECODE, so startup caches must be
# confined by the copy before any interpreter code runs.
function Get-CounterPackageSnapshot([string]$Directory) {
    Assert-KilixRegularPath $Directory
    $prefix = $Directory.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $rows = @(foreach ($entry in Get-ChildItem -LiteralPath $Directory -Recurse -Force | Sort-Object FullName) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Counter test refuses reparse points' }
        $name = $entry.FullName.Substring($prefix.Length)
        if ($entry.PSIsContainer) { [ordered]@{name=$name;directory=$true} }
        else { [ordered]@{name=$name;bytes=$entry.Length;sha256=(Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash} }
    })
    return ConvertTo-Json -InputObject $rows -Depth 4 -Compress
}

$ApplicationDirectory = [IO.Path]::GetFullPath($ApplicationDirectory).TrimEnd('\', '/')
$null = Test-KilixPackage $ApplicationDirectory -Installed
$before = Get-CounterPackageSnapshot $ApplicationDirectory
$work = Join-Path ([IO.Path]::GetTempPath()) ('Kilix-System-Load-' + [Guid]::NewGuid().ToString('N'))
$work = [IO.Path]::GetFullPath($work)
if ($work.StartsWith($ApplicationDirectory + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Counter test temporary directory must be outside the source package'
}
Assert-KilixRegularPath $work
$owned = $false

$code = @'
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


require(sys.platform == 'win32', 'This check requires native Windows')
from kitty import kilix_system_load as system
from kitty import kilix_system_load_windows as native
from kitty.kilix_chrome import settings

# Keep settings/cache operations in this child and its temporary directory.
# An inherited fixture override must not substitute /proc data for Win32 APIs.
os.environ.pop('KILIX_SYSTEM_PROC_ROOT', None)
os.environ.pop(system.SYSTEM_LOAD_MODE_KEY, None)
with tempfile.TemporaryDirectory(prefix='kilix-system-load-') as temporary:
    os.environ['GPU_TERMINAL_SETTINGS_FILE'] = str(Path(temporary) / 'settings.conf')
    settings._cache_signature = None
    groups = native._kernel32().GetActiveProcessorGroupCount()
    require(groups > 0, 'Could not query processor groups')
    raw = native.read_cpu_times()
    if groups == 1:
        require(raw is not None, 'Native CPU counters unavailable')
        total, idle, cpus = raw
        require(total > 0 and 0 <= idle <= total and cpus > 0, 'Invalid native CPU counters')
    else:
        require(raw is None, 'A processor-group subset was reported as whole-system CPU')
        cpus = None

    memory = native.read_memory_used()
    require(isinstance(memory, int) and memory >= 0, 'Native physical memory unavailable')
    system._PREVIOUS_TIMES = None
    first = system.system_load(force=True)
    require(first.cpu_cores is None, 'First CPU sample had no baseline')
    require(first.memory_used_bytes is not None, 'Sampler did not use native memory')
    time.sleep(0.25)
    second = system.system_load(force=True)
    require(second.memory_used_bytes is not None, 'Native memory disappeared')
    if groups == 1:
        require(second.cpu_cores is not None, 'Second CPU sample missing')
        require(math.isfinite(second.cpu_cores) and 0 <= second.cpu_cores <= cpus,
                'CPU sample is outside logical-core bounds')
    else:
        require(second.cpu_cores is None, 'Multi-group CPU should remain unknown')

    require(system.system_load_mode() == 'always', 'Native system readout disabled by default')
    segment = system.system_load_segment()
    require(segment is not None and bool(segment[0].strip()), 'Native system readout is empty')
    require(segment[1] is None, 'Native readout advertises an unavailable Linux action')
    os.environ[system.SYSTEM_LOAD_MODE_KEY] = 'off'
    require(system.system_load_segment() is None, 'Explicit off mode was ignored')
    print(json.dumps({
        'schema': 'kilix.native-system-load-test/v1', 'ok': True,
        'processor_groups': groups, 'logical_cpus': cpus,
        'cpu_supported': groups == 1, 'cpu_cores': second.cpu_cores,
        'memory_used_bytes': second.memory_used_bytes,
        'segment': segment[0], 'action': segment[1],
        'default_enabled': True, 'off_mode_respected': True,
        'gui_rendering_verified': False,
    }, ensure_ascii=True))
'@
try {
    $null = New-Item -ItemType Directory -Path $work -ErrorAction Stop
    $owned = $true
    $copy = Join-Path $work 'application'
    Copy-Item -LiteralPath $ApplicationDirectory -Destination $copy -Recurse -Force
    if ((Get-CounterPackageSnapshot $copy) -cne $before) { throw 'Disposable package copy differs from source' }
    # Exercise startup without existing caches, even when the caller supplied
    # an installed package. Only the owned copy is changed.
    foreach ($directory in @(Get-ChildItem -LiteralPath $copy -Directory -Recurse -Force |
                             Where-Object {$_.Name -ceq '__pycache__'} | Sort-Object FullName -Descending)) {
        Remove-Item -LiteralPath $directory.FullName -Recurse -Force
    }
    $childEnvironment = @{}
    foreach ($pair in @(
            @('USERPROFILE','profile'), @('LOCALAPPDATA','local'), @('APPDATA','roaming'),
            @('TEMP','temp'), @('TMP','temp'), @('TMPDIR','temp'), @('KITTY_CONFIG_DIRECTORY','kitty-config'),
            @('XDG_CONFIG_HOME','xdg-config'), @('XDG_DATA_HOME','xdg-data'),
            @('XDG_CACHE_HOME','xdg-cache'), @('GPU_TERMINAL_HOME','gpu-state'))) {
        $directory = Join-Path $work $pair[1]
        [IO.Directory]::CreateDirectory($directory) | Out-Null
        $childEnvironment[$pair[0]] = $directory
    }
    $childEnvironment['GPU_TERMINAL_SETTINGS_FILE'] = Join-Path $work 'settings.conf'
    foreach ($key in @('KITTY_LISTEN_ON','KITTY_RC_PASSWORD','KITTY_PUBLIC_KEY','KITTY_WINDOW_ID','KITTY_PID',
                        'KILIX_SYSTEM_PROC_ROOT','KILIX_CHROME_SYSTEM_LOAD_MODE')) { $childEnvironment[$key] = $null }
    foreach ($key in [Environment]::GetEnvironmentVariables('Process').Keys) {
        if ($key.StartsWith('KILIX_NATIVE_', [StringComparison]::OrdinalIgnoreCase)) { $childEnvironment[$key] = $null }
    }
    $result = Invoke-KilixProcess (Join-Path $copy 'kitty/launcher/kitty-console.exe') @('+runpy', $code) 15 `
        -ChildEnvironment $childEnvironment
    if ($result.exitCode -ne 0) { throw "Native system counters failed: $($result.stderr)" }
    $report = ConvertFrom-Json $result.stdout
    if ($report.schema -cne 'kilix.native-system-load-test/v1' -or $report.ok -ne $true) {
        throw 'Missing native system counter result'
    }
} finally {
    try {
        if ($owned) {
            Assert-KilixRegularPath $work
            Remove-Item -LiteralPath $work -Recurse -Force
            if (Test-Path -LiteralPath $work) { throw 'Counter test temporary cleanup failed' }
        }
    } finally {
        if ((Get-CounterPackageSnapshot $ApplicationDirectory) -cne $before) { throw 'Source package changed during counter test' }
    }
}
$report | Add-Member -NotePropertyName original_package_preserved -NotePropertyValue $true
$report | Add-Member -NotePropertyName disposable_copy_removed -NotePropertyValue $true
$report | ConvertTo-Json -Depth 4
Write-Host 'PASS packaged native system CPU/RAM sampling and display policy'
