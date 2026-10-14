#requires -Version 5.1
[CmdletBinding()]
param([string]$OutputDirectory = "$env:LOCALAPPDATA\gpu_terminal\kilix-win\build")
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$files = @(
    @('runtimes/win-x64/native/conpty.dll', 'conpty.dll', 'feeef341d891643c62d30b6b07800bc70f0bc148f44cb8c3bee84aa557ae805a'),
    @('build/native/runtimes/x64/OpenConsole.exe', 'OpenConsole.exe', '3d66b23d0a71bb8eed2b77edc8b9df9bf54ce6c8fb74c863a30e760997f80586')
)
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$ready = $true
foreach ($file in $files) {
    $target = Join-Path $OutputDirectory $file[1]
    if (!(Test-Path -LiteralPath $target) -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $file[2]) { $ready = $false }
}
if ($ready) { return }
$cache = Join-Path (Split-Path $OutputDirectory) 'cache'
New-Item -ItemType Directory -Force $cache | Out-Null
$archive = Join-Path $cache 'conpty-1.25.260930003.nupkg'
if (!(Test-Path -LiteralPath $archive)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -OutFile $archive -Uri 'https://api.nuget.org/v3-flatcontainer/microsoft.windows.console.conpty/1.25.260930003/microsoft.windows.console.conpty.1.25.260930003.nupkg'
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '02b07b349af66d801159bdf9e440d4a1ce78bb951f37fc8609731665afdae7ee') {
    throw 'Microsoft ConPTY package SHA256 mismatch.'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    foreach ($file in $files) {
        $entry = $zip.GetEntry($file[0])
        if (!$entry) { throw "Missing package entry: $($file[0])" }
        $target = Join-Path $OutputDirectory $file[1]
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $file[2]) { throw "Runtime digest mismatch: $target" }
    }
} finally { $zip.Dispose() }
