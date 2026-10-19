#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Destination = "$env:LOCALAPPDATA\Programs\Kilix",
    [string]$ApplicationDirectory,
    [string]$WslDistribution,
    [switch]$VerifyFiles,
    [string]$OutputPath
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'setup-common.ps1')
$report = [ordered]@{ schema = 1; generated = [DateTime]::UtcNow.ToString('o'); checks = [ordered]@{} }
function Record([string]$Name, [scriptblock]$Probe) {
    try { $report.checks[$Name] = @{ status = 'observed'; value = (& $Probe) } }
    catch { $report.checks[$Name] = @{ status = 'unavailable'; reason = "$($_.Exception.Message)" } }
}
Record 'windows' {
    Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture
}
Record 'powershell' {
    @{ version = $PSVersionTable.PSVersion.ToString(); policies = @(Get-ExecutionPolicy -List | ForEach-Object {
        @{ scope = [string]$_.Scope; policy = [string]$_.ExecutionPolicy }
    }) }
}
Record 'install' {
    @{ state = (Get-KilixState $Destination); recoveryPending = (Test-Path -LiteralPath (Join-Path $Destination 'install-pending.json')) }
}
if (!$ApplicationDirectory) {
    try {
        $state = Get-KilixState $Destination
        if ($state) { $ApplicationDirectory = Join-Path $Destination $state.current.path }
        elseif (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'Kilix.exe')) { $ApplicationDirectory = $PSScriptRoot }
    } catch { }
}
if ($ApplicationDirectory) {
    Record 'application' {
        $application = (Resolve-Path -LiteralPath $ApplicationDirectory).Path
        $signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $application 'Kilix.exe')
        @{ version = (Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $application 'VERSION')).Trim();
            signature = [string]$signature.Status;
            signer = $(if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { $null });
            cli = (Invoke-KilixProcess (Join-Path $application 'kilix-cli.exe') @('--version')) }
    }
    if ($VerifyFiles) { Record 'manifest' { Test-KilixPackage (Resolve-Path -LiteralPath $ApplicationDirectory).Path -Installed } }
}
Record 'storage' {
    # The Windows host volume can be full even when the sparse WSL VHD reports free space.
    @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID, Size, FreeSpace)
}
Record 'graphics' {
    @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM, VideoModeDescription)
}
Record 'displays' {
    Add-Type -AssemblyName System.Windows.Forms
    @([Windows.Forms.Screen]::AllScreens | ForEach-Object {
        @{ primary = $_.Primary; bounds = $_.Bounds.ToString(); workArea = $_.WorkingArea.ToString(); bitsPerPixel = $_.BitsPerPixel }
    })
}
Record 'shellProfiles' {
    # Existence only: never collect the contents of profiles, history, environment or user config.
    $documents = [Environment]::GetFolderPath('MyDocuments')
    @('WindowsPowerShell/profile.ps1', 'WindowsPowerShell/Microsoft.PowerShell_profile.ps1',
        'PowerShell/profile.ps1', 'PowerShell/Microsoft.PowerShell_profile.ps1') | ForEach-Object {
        @{ profile = $_; exists = (Test-Path -LiteralPath (Join-Path $documents $_)) }
    }
}
Record 'wslDistributions' {
    $wsl = (Get-Command wsl.exe).Source
    $result = Invoke-KilixProcess $wsl @('--list', '--verbose') -OutputEncoding ([Text.Encoding]::Unicode)
    @{ exitCode = $result.exitCode; output = ($result.stdout + $result.stderr).Replace([string][char]0, '') }
}
if ($WslDistribution) {
    Record 'wslStorageAndTools' {
        Invoke-KilixProcess (Get-Command wsl.exe).Source @('--distribution', $WslDistribution, '--exec', '/bin/sh', '-c',
            'df -Pk "$HOME"; for t in python3 make cc pactl; do command -v "$t" || true; done; test -e "$HOME/.local/share/kilix-wsl/current" && echo companion-present')
    }
}
$report.qualification = @{
    hardware = 'Published baseline: Windows 10 build 19045, Intel HD620, Ubuntu WSL2. New changes require target validation.'
    windows11 = 'Not tested by this development record.'
    interactive = 'Run test-integration.ps1 and test-startup.ps1 on an isolated desktop session; manual IME, screen reader and mixed-DPI checks remain necessary.'
    privacy = 'No profile/config contents, clipboard text or credentials collected. Review paths and distribution names before sharing.'
}
if ($OutputPath) { Write-KilixJson ([IO.Path]::GetFullPath($OutputPath)) $report }
$report | ConvertTo-Json -Depth 16
