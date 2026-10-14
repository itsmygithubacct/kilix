#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$Python = "$env:USERPROFILE\kilix-build\msys64\mingw64\bin\python.exe"
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$fontHash = 'f0f624d9b474bea1662cf7e862d44aebe1ae1f6c7f9cb7a0ca5d0e5ac9561c60'
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$target = Join-Path $OutputDirectory 'SymbolsNerdFontMono-Regular.ttf'
if ((Test-Path -LiteralPath $target) -and (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $fontHash) { return }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('kilix-font-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $temporary | Out-Null
try {
    $archive = Join-Path $temporary 'symbols.tar.xz'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -OutFile $archive -Uri 'https://github.com/ryanoasis/nerd-fonts/releases/download/v3.4.0/NerdFontsSymbolsOnly.tar.xz'
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '7f8c090da3b0eaa7108646bf34cbbb6ed13d5358a72460522108b06c7ecd716a') { throw 'Nerd Fonts archive hash mismatch.' }
    $extracted = Join-Path $temporary 'SymbolsNerdFontMono-Regular.ttf'
    & $Python -c 'import pathlib,sys,tarfile; t=tarfile.open(sys.argv[1]); pathlib.Path(sys.argv[2]).write_bytes(t.extractfile(sys.argv[3]).read()); t.close()' $archive $extracted SymbolsNerdFontMono-Regular.ttf
    if ($LASTEXITCODE -ne 0) { throw 'Cannot extract bundled font.' }
    if ((Get-FileHash -LiteralPath $extracted -Algorithm SHA256).Hash -ne $fontHash) { throw 'Nerd Fonts file hash mismatch.' }
    Copy-Item -LiteralPath $extracted -Destination $target
} finally { Remove-Item -LiteralPath $temporary -Recurse -Force }
