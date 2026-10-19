#requires -Version 5.1
# Small ZIP/manifest fixtures only; no application install, execution or settings.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../setup-common.ps1')
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = Join-Path ([IO.Path]::GetTempPath()) ('kilix-longpath-test-' + [Guid]::NewGuid().ToString('N'))
$filesystemRoot = ConvertTo-KilixFileSystemPath $root
$checks = 0
function Assert-LongPath($Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
    $script:checks++
}
function Expect-LongPathFailure([scriptblock]$Body, [string]$Pattern) {
    $caught = $null
    try { & $Body | Out-Null } catch { $caught = $_ }
    Assert-LongPath ($caught -and "$caught" -match $Pattern) "Expected $Pattern, got $caught"
}
try {
    [IO.Directory]::CreateDirectory($filesystemRoot) | Out-Null
    $destination = Join-Path $root (('application with spaces ' + [char]0x03bb) + '/versions/' + [Guid]::NewGuid().ToString('N'))
    $application = Join-Path $destination 'kilix'
    $deep = 'wsl-bin/third_party/' + ('source-' * 12) + '/' + ('contracts-' * 8) + '/'
    $longName = $deep + 'kilix.content.asset-v3.schema.json'
    $legacyName = $deep + 'legacy [' + [char]0x03bb + '].txt'
    $files = [ordered]@{}
    foreach ($name in @('Kilix.exe', 'Kilix.com', 'kilix-cli.exe', 'VERSION', 'config/kilix.conf',
            'kitty/launcher/kitty.exe', 'kitty/launcher/kitty-console.exe', $longName, $legacyName)) {
        $files[$name] = [Text.Encoding]::UTF8.GetBytes(('fixture ' + [char]0x03bb + ' ' + $name))
    }
    $manifest = [ordered]@{}
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($name in $files.Keys) {
            $manifest[$name] = @{ size = $files[$name].Length;
                sha256 = [BitConverter]::ToString($sha256.ComputeHash($files[$name])).Replace('-', '').ToLowerInvariant() }
        }
    } finally { $sha256.Dispose() }
    $files['manifest.json'] = [Text.Encoding]::UTF8.GetBytes((@{ version = 'path-fixture'; files = $manifest } | ConvertTo-Json -Depth 8))
    $archive = Join-Path $root 'fixture.zip'
    $zip = [IO.Compression.ZipFile]::Open($archive, 'Create')
    try {
        foreach ($name in $files.Keys) {
            $entryName = 'kilix/' + $name
            if ($name -eq $legacyName) { $entryName = $entryName.Replace('/', '\') }
            $stream = $zip.CreateEntry($entryName).Open()
            try { $stream.Write($files[$name], 0, $files[$name].Length) } finally { $stream.Dispose() }
        }
    } finally { $zip.Dispose() }

    $longPath = Join-Path $application $longName
    Assert-LongPath ($longPath.Length -gt 300) 'Fixture must exceed MAX_PATH'
    Assert-LongPath ([IO.Path]::GetDirectoryName((ConvertTo-KilixFileSystemPath $longPath)).Length -gt 248) 'Fixture must exercise long directories'
    Expand-KilixArchive $archive $destination
    Assert-LongPath ((Test-KilixPackage $application) -ceq 'path-fixture') 'Long-path manifest validation failed'
    Assert-LongPath ((Test-KilixPackage $application -Installed) -ceq 'path-fixture') 'Retained-generation validation failed'
    foreach ($name in @($longName, $legacyName)) {
        $path = ConvertTo-KilixFileSystemPath (Join-Path $application $name)
        Assert-LongPath ([IO.File]::ReadAllText($path) -ceq [Text.Encoding]::UTF8.GetString($files[$name])) 'Long Unicode ZIP entry changed'
    }

    $filesystemPath = ConvertTo-KilixFileSystemPath $longPath
    $damaged = [IO.File]::ReadAllBytes($filesystemPath)
    $damaged[0] = $damaged[0] -bxor 1
    [IO.File]::WriteAllBytes($filesystemPath, $damaged)
    Expect-LongPathFailure { Test-KilixPackage $application } 'SHA256 mismatch'
    [IO.File]::WriteAllBytes($filesystemPath, $files[$longName])
    $extra = ConvertTo-KilixFileSystemPath (Join-Path $application ($deep + 'unlisted.txt'))
    [IO.File]::WriteAllText($extra, 'unlisted')
    Expect-LongPathFailure { Test-KilixPackage $application } 'Unlisted package file'
    [IO.File]::Delete($extra)
    Assert-LongPath ((Test-KilixPackage $application) -ceq 'path-fixture') 'Repaired fixture did not validate'
    if ($env:OS -eq 'Windows_NT') {
        Assert-LongPath ((ConvertTo-KilixFileSystemPath '\\server.invalid\share\file') -ceq '\\?\UNC\server.invalid\share\file') 'UNC path conversion changed'
        Assert-LongPath ((ConvertTo-KilixFileSystemPath $filesystemPath) -ceq $filesystemPath) 'Extended path conversion is not idempotent'
        $target = Join-Path $root 'junction-target'
        $junction = Join-Path $root 'junction'
        [IO.Directory]::CreateDirectory($target) | Out-Null
        New-Item -ItemType Junction -Path $junction -Target $target | Out-Null
        try {
            Expect-LongPathFailure { Assert-KilixRegularPath (Join-Path $junction $longName) } 'reparse points'
        } finally { (Get-Item -LiteralPath $junction).Delete() }
    }
} finally {
    if (Test-Path -LiteralPath $filesystemRoot) { Remove-Item -LiteralPath $filesystemRoot -Recurse -Force }
}
Assert-LongPath (![IO.Directory]::Exists($filesystemRoot)) 'Long-path fixture cleanup failed'
Write-Host "PASS: $checks long-path assertions (ZIP, Unicode, manifest hashes, unlisted files, retained validation and cleanup)"
