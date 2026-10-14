#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Zig,
    [string]$OutputDirectory = "$env:LOCALAPPDATA\gpu_terminal\kilix-win\build",
    [switch]$Test,
    [string]$WslDistribution
)
$ErrorActionPreference = 'Stop'
if (!$Zig) { $Zig = "$env:LOCALAPPDATA\gpu_terminal\kilix-win\toolchains\zig-x86_64-windows-0.17.0\zig.exe" }
if (!(Test-Path -LiteralPath $Zig)) { throw 'Run bootstrap.ps1 first or pass -Zig with a compiler path.' }
$version = & $Zig version
if ($LASTEXITCODE -ne 0 -or $version -ne '0.17.0') { throw "Expected Zig 0.17.0; got $version" }
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
& "$PSScriptRoot\fetch-conpty.ps1" -OutputDirectory $OutputDirectory
$env:ZIG_GLOBAL_CACHE_DIR = Join-Path (Split-Path $OutputDirectory) 'cache\zig'
$exe = Join-Path $OutputDirectory 'kilix-conpty-probe.exe'
& $Zig cc -target x86_64-windows-gnu -std=c11 -Wall -Wextra -Werror -O2 -g `
    -U_WIN32_WINNT -D_WIN32_WINNT=0x0A00 -DUNICODE -D_UNICODE -municode `
    -I "$PSScriptRoot\src" "$PSScriptRoot\src\session.c" "$PSScriptRoot\tests\probe.c" -o $exe
if ($LASTEXITCODE -ne 0) { throw "Native compile failed: $LASTEXITCODE" }
Get-FileHash -LiteralPath $exe -Algorithm SHA256
$graphics = Join-Path $OutputDirectory 'kilix-wgl-probe.exe'
& $Zig cc -target x86_64-windows-gnu -std=c11 -Wall -Wextra -Werror -O2 -g `
    -U_WIN32_WINNT -D_WIN32_WINNT=0x0A00 -DUNICODE -D_UNICODE -municode `
    "$PSScriptRoot\tests\graphics.c" -lopengl32 -lgdi32 -luser32 -o $graphics
if ($LASTEXITCODE -ne 0) { throw "Native graphics probe compile failed: $LASTEXITCODE" }
Get-FileHash -LiteralPath $graphics -Algorithm SHA256
if ($Test) {
    if ($WslDistribution) { & $exe --wsl $WslDistribution } else { & $exe }
    if ($LASTEXITCODE -ne 0) { throw "ConPTY checks failed: $LASTEXITCODE" }
}
