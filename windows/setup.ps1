#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Wsl', 'Rollback', 'Recover', 'Status', 'Diagnose')][string]$Action = 'Install',
    [string]$Archive,
    [string]$Sha256,
    [string]$Destination = "$env:LOCALAPPDATA\Programs\Kilix",
    [switch]$NoShortcuts,
    [string]$SignerThumbprint,
    [string]$WslDistribution,
    [string]$WslUser,
    [switch]$InstallWsl,
    [switch]$InstallWslDependencies,
    [string]$CompanionArchive,
    [string]$CompanionSha256,
    [string]$DiagnosticOutput
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'setup-common.ps1')
if ($Action -eq 'Diagnose') {
    & (Join-Path $PSScriptRoot 'diagnostics.ps1') -Destination $Destination -OutputPath $DiagnosticOutput -WslDistribution $WslDistribution
    return
}
if (($InstallWsl -or $InstallWslDependencies -or $WslUser -or $Action -eq 'Wsl') -and !$WslDistribution) {
    throw 'Specify -WslDistribution so setup targets the intended distribution and Linux user.'
}
if ($Action -eq 'Wsl') {
    $state = Invoke-KilixInstall -Action Recover -Destination $Destination
    if (!$state) { throw 'Install the native package first, or pass the existing managed -Destination.' }
} else {
    $state = Invoke-KilixInstall -Action $Action -Archive $Archive -Sha256 $Sha256 `
        -Destination $Destination -NoShortcuts:$NoShortcuts -SignerThumbprint $SignerThumbprint
}
if ($state) { Write-Host ('Application: ' + (Join-Path $Destination ($state.current.path + '/Kilix.exe'))) }
if ($Action -in @('Rollback', 'Recover', 'Status')) {
    if ($state) { $state | ConvertTo-Json -Depth 8 }
    return
}
if (!$WslDistribution) { return } # Native Windows setup never requires WSL.

$application = Join-Path ([IO.Path]::GetFullPath($Destination)) $state.current.path
try {
    $wsl = (Get-Command wsl.exe -ErrorAction Stop).Source
    $prefix = @('--distribution', $WslDistribution)
    if ($WslUser) { $prefix += @('--user', $WslUser) }
    $probe = Invoke-KilixProcess $wsl ($prefix + @('--exec', '/bin/true')) 45
    if ($probe.exitCode -ne 0) {
        if ($InstallWsl) {
            # Microsoft may require elevation, reboot and first-run account creation.
            # Do not configure root as the default user or change an existing distribution.
            & $wsl --install --distribution $WslDistribution --no-launch
            $installExit = $LASTEXITCODE
            throw "WSL installation returned $installExit. Complete any Windows reboot and distribution account setup, then rerun setup.ps1 -Action Wsl -WslDistribution '$WslDistribution'. Native installation is ready."
        }
        throw "WSL distribution is unavailable. Initialize it first, or pass -InstallWsl to request Windows WSL installation. $($probe.stderr)"
    }
    function Get-LinuxPath([string]$Path) {
        $resolved = (Resolve-Path -LiteralPath $Path).Path
        $result = Invoke-KilixProcess $wsl ($prefix + @('--exec', 'wslpath', '-a', '-u', $resolved))
        if ($result.exitCode -ne 0 -or !$result.stdout.Trim()) { throw 'WSL could not translate an installation path.' }
        return $result.stdout.Trim()
    }
    if (!$CompanionArchive) {
        $CompanionArchive = Join-Path $application 'wsl/companion.tar.gz'
        $sidecar = $CompanionArchive + '.sha256'
        if (!(Test-Path -LiteralPath $sidecar)) { throw 'This package has no source bundle. Pass -CompanionArchive and -CompanionSha256 from a pinned companion export.' }
        $CompanionSha256 = ((Get-Content -Raw -Encoding UTF8 -LiteralPath $sidecar).Trim() -split '\s+')[0]
    }
    Test-KilixHash $CompanionArchive $CompanionSha256
    $bootstrap = Get-LinuxPath (Join-Path $application 'wsl-bootstrap.sh')
    $linuxArchive = Get-LinuxPath $CompanionArchive
    $arguments = $prefix + @('--exec', '/bin/sh', $bootstrap, $linuxArchive, $CompanionSha256.ToLowerInvariant())
    if ($InstallWslDependencies) { $arguments += '--install-dependencies' }
    # Inherit the console for sudo, compiler output, and normal setup errors.
    & $wsl @arguments
    if ($LASTEXITCODE -ne 0) { throw "Companion setup failed (exit $LASTEXITCODE)." }
    Write-Host 'Windows and WSL setup complete. Existing model decisions and user work retained. Open a new Kilix WSL window.'
} catch {
    throw "Native installation remains ready. WSL setup was not completed: $_ Rerun with -Action Wsl after correcting the reported issue."
}
