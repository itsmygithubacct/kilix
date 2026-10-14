#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Archive,
    [Parameter(Mandatory=$true)][string]$Sha256,
    [string]$Destination = "$env:LOCALAPPDATA\Programs\Kilix-0.2.2-windows-dev",
    [switch]$NoShortcuts
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Archive = (Resolve-Path -LiteralPath $Archive).Path
if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash -ne $Sha256) { throw 'Application archive hash mismatch.' }
if (Test-Path -LiteralPath $Destination) {
    $Destination += '-' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
}
New-Item -ItemType Directory -Path $Destination | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory($Archive, $Destination)
$application = Join-Path $Destination 'kilix'
$exe = Join-Path $application 'Kilix.exe'
if (!(Test-Path -LiteralPath $exe)) { throw 'The archive did not contain the expected application.' }
if (!$NoShortcuts) {
    $shell = New-Object -ComObject WScript.Shell
    $menus = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
    foreach ($directory in $menus) {
        foreach ($profile in @(@('Kilix Windows', ''), @('Kilix WSL', '--wsl'))) {
            $shortcut = $shell.CreateShortcut((Join-Path $directory ($profile[0] + '.lnk')))
            $shortcut.TargetPath = $exe
            $shortcut.Arguments = $profile[1]
            $shortcut.WorkingDirectory = $env:USERPROFILE
            $shortcut.Description = 'Kilix 0.2.2 native Windows development build'
            $shortcut.IconLocation = (Join-Path $application 'kitty\launcher\kitty.ico')
            $shortcut.Save()
        }
    }
}
Write-Output "Installed: $exe"
Write-Output 'Settings: %LOCALAPPDATA%\Kilix\kitty.conf'
