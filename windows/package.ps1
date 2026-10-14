#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$EngineDirectory = (Join-Path $PSScriptRoot '..\src'),
    [string]$ToolchainDirectory = "$env:USERPROFILE\kilix-build\msys64",
    [switch]$StageExisting
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
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
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (Test-Path -LiteralPath $archive) {
    Move-Item -LiteralPath $archive -Destination ($archive + '.previous.' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))
}
[IO.Compression.ZipFile]::CreateFromDirectory($artifact, $archive, [IO.Compression.CompressionLevel]::Optimal, $true)
Get-FileHash -LiteralPath $archive -Algorithm SHA256
Write-Output "Application: $artifact\Kilix.exe"
