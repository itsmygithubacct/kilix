#requires -Version 5.1
[CmdletBinding()]
param([string]$ToolchainDirectory = "$env:LOCALAPPDATA\gpu_terminal\kilix-win\toolchains")
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$version = '0.17.0'
$name = "zig-x86_64-windows-$version"
$sha256 = 'b5663f69581dcf391293fbf16c06cb80d81d806545ce618b4d0bab7f0eb8c428'
New-Item -ItemType Directory -Force $ToolchainDirectory | Out-Null
$archive = Join-Path $ToolchainDirectory "$name.zip"
$executable = Join-Path $ToolchainDirectory "$name\zig.exe"
if (!(Test-Path -LiteralPath $executable)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    if (!(Test-Path -LiteralPath $archive)) {
        Invoke-WebRequest -UseBasicParsing "https://ziglang.org/download/$version/$name.zip" -OutFile $archive
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $sha256) {
        throw "Compiler checksum mismatch: $archive"
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $ToolchainDirectory -Force
}
& $executable version
if ($LASTEXITCODE -ne 0) { throw 'Compiler did not start.' }
Write-Output "Compiler: $executable"
