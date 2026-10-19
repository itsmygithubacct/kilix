#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$EngineDirectory = (Join-Path $PSScriptRoot '..\src'),
    [string]$ToolchainDirectory = "$env:USERPROFILE\kilix-build\msys64",
    [switch]$StageExisting,
    [ValidateSet('development', 'preview', 'release')][string]$Channel = 'development',
    [string]$CompanionArchive,
    [string]$CompanionSha256,
    [string]$CertificateThumbprint,
    [string]$TimestampServer
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'setup-common.ps1')
if ($CompanionArchive) {
    $CompanionArchive = (Resolve-Path -LiteralPath $CompanionArchive).Path
    Test-KilixHash $CompanionArchive $CompanionSha256
} elseif ($CompanionSha256) { throw 'Pass -CompanionArchive with its SHA256.' }
$EngineDirectory = (Resolve-Path -LiteralPath $EngineDirectory).Path
$bash = Join-Path $ToolchainDirectory 'usr\bin\bash.exe'
$cygpath = Join-Path $ToolchainDirectory 'usr\bin\cygpath.exe'
if (!(Test-Path -LiteralPath $bash)) { throw 'Run provision-engine-toolchain.ps1 first.' }
& (Join-Path $PSScriptRoot 'fetch-conpty.ps1') -OutputDirectory (Join-Path $EngineDirectory 'kitty\launcher')
& (Join-Path $PSScriptRoot 'fetch-font.ps1') -OutputDirectory (Join-Path $EngineDirectory 'fonts') -Python (Join-Path $ToolchainDirectory 'mingw64\bin\python.exe')
$scriptPath = & $cygpath -u (Join-Path $PSScriptRoot 'package.sh')
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve package script path.' }
$enginePath = & $cygpath -u $EngineDirectory
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve engine source path.' }
$env:MSYSTEM = 'MINGW64'
$env:CHERE_INVOKING = '1'
if ($StageExisting) { & $bash -l $scriptPath $enginePath --stage-existing }
else { & $bash -l $scriptPath $enginePath }
if ($LASTEXITCODE -ne 0) { throw "Packaging failed with exit code $LASTEXITCODE" }
$artifact = Join-Path $EngineDirectory 'dist\kilix'
$archive = Join-Path $EngineDirectory 'dist\kilix-0.2.2-windows-x64-dev.zip'
if ($CompanionArchive) {
    Copy-Item -LiteralPath $CompanionArchive -Destination (Join-Path $artifact 'wsl/companion.tar.gz')
    [IO.File]::WriteAllText((Join-Path $artifact 'wsl/companion.tar.gz.sha256'), $CompanionSha256.ToLowerInvariant() + "  companion.tar.gz`n")
} else {
    # StageExisting must not accidentally publish a stale/personal model bundle.
    foreach ($name in @('wsl/companion.tar.gz', 'wsl/companion.tar.gz.sha256')) {
        $path = Join-Path $artifact $name
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
}
Write-KilixJson (Join-Path $artifact 'release.json') @{ channel = $Channel; version = '0.2.2'; architecture = 'x64' }
& (Join-Path $PSScriptRoot 'sign-package.ps1') -ApplicationDirectory $artifact -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer
& (Join-Path $ToolchainDirectory 'mingw64/bin/python.exe') (Join-Path $PSScriptRoot 'package-manifest.py') $artifact
if ($LASTEXITCODE -ne 0) { throw 'Final manifest generation failed.' }
if ($CertificateThumbprint) {
    & (Join-Path $PSScriptRoot 'sign-package.ps1') -ApplicationDirectory $artifact -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer -SignManifestOnly
}
$null = Test-KilixPackage $artifact $CertificateThumbprint
Add-Type -AssemblyName System.IO.Compression.FileSystem
$temporary = $archive + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
try {
    [IO.Compression.ZipFile]::CreateFromDirectory($artifact, $temporary, [IO.Compression.CompressionLevel]::Optimal, $true)
    if (Test-Path -LiteralPath $archive) {
        [IO.File]::Replace($temporary, $archive, ($archive + '.previous.' + [Guid]::NewGuid().ToString('N')))
    } else { [IO.File]::Move($temporary, $archive) }
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
}
Get-FileHash -LiteralPath $archive -Algorithm SHA256
Write-Output "Application: $artifact\Kilix.exe"
