#requires -Version 5.1
[CmdletBinding()]
param([string]$BuildRoot = "$env:USERPROFILE\kilix-build")
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($BuildRoot -match '[\s"'']') { throw 'Use a short build directory without spaces or quotes.' }
if ((Get-PSDrive -Name C).Free -lt 8GB) { throw 'At least 8 GiB free is required to provision the engine toolchain.' }
New-Item -ItemType Directory -Force $BuildRoot | Out-Null
$archive = Join-Path $BuildRoot 'msys2-base-x86_64-20260927.sfx.exe'
$bash = Join-Path $BuildRoot 'msys64\usr\bin\bash.exe'
if (!(Test-Path -LiteralPath $bash)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    if (!(Test-Path -LiteralPath $archive)) {
        Invoke-WebRequest -UseBasicParsing -OutFile $archive -Uri 'https://github.com/msys2/msys2-installer/releases/download/2026-09-27/msys2-base-x86_64-20260927.sfx.exe'
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne 'ad336cccfda47758b5e15cda993fbba421115cb0b126697daef1ee4dfe37209f') {
        throw 'MSYS2 archive SHA256 mismatch.'
    }
    & $archive -y "-o$BuildRoot"
    if ($LASTEXITCODE -ne 0) { throw "MSYS2 extraction failed: $LASTEXITCODE" }
}
$env:MSYSTEM = 'MINGW64'
$env:CHERE_INVOKING = '1'
& $bash -lc 'true'
if ($LASTEXITCODE -ne 0) { throw 'MSYS2 initialization failed.' }
# A core-runtime upgrade can deliberately terminate its own shell. Start a
# fresh shell for the second pass; no machine restart or other MSYS2 tree used.
& $bash -lc 'pacman --noconfirm --noprogressbar -Syu'
if ($LASTEXITCODE -notin @(0, 130)) { throw "Core toolchain update failed: $LASTEXITCODE" }
& $bash -lc 'pacman --noconfirm --noprogressbar -Syu'
if ($LASTEXITCODE -ne 0) { throw "Toolchain update failed: $LASTEXITCODE" }
$packages = @('base-devel','mingw-w64-x86_64-gcc','mingw-w64-x86_64-pkgconf',
    'mingw-w64-x86_64-python','mingw-w64-x86_64-go','mingw-w64-x86_64-harfbuzz',
    'mingw-w64-x86_64-freetype','mingw-w64-x86_64-fontconfig','mingw-w64-x86_64-cairo',
    'mingw-w64-x86_64-libpng','mingw-w64-x86_64-xxhash','mingw-w64-x86_64-openssl',
    'mingw-w64-x86_64-ncurses','mingw-w64-x86_64-lcms2','mingw-w64-x86_64-tk')
& $bash -lc ('pacman --noconfirm --noprogressbar --needed -S ' + ($packages -join ' '))
if ($LASTEXITCODE -ne 0) { throw "Engine dependencies failed: $LASTEXITCODE" }
& $bash -lc 'pacman -Q' | Set-Content -LiteralPath (Join-Path $BuildRoot 'toolchain-packages.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not record installed toolchain packages.' }
Write-Output "Engine toolchain ready: $BuildRoot\msys64"
