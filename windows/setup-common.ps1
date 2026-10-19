#requires -Version 5.1
# SPDX-License-Identifier: GPL-3.0-only
# Shared by setup, diagnostics and offline transaction tests. No actions on import.

function ConvertTo-KilixFileSystemPath([string]$Path) {
    if ($env:OS -ne 'Windows_NT') { return [IO.Path]::GetFullPath($Path) }
    $path = $Path.Replace('/', '\')
    if ($path.StartsWith('\\?\', [StringComparison]::Ordinal)) { return $path }
    # Resolve relative/dot segments before adding the extended prefix. Package
    # paths are already absolute with validated relative entry names, so avoid
    # legacy GetFullPath's MAX_PATH check on those complete file paths.
    if ($path -notmatch '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+(?:\\|$))' -or
        $path -match '(?:^|\\)\.\.?(?:\\|$)') {
        $path = [IO.Path]::GetFullPath($path)
    }
    if ($path.StartsWith('\\', [StringComparison]::Ordinal)) { return '\\?\UNC\' + $path.Substring(2) }
    return '\\?\' + $path
}

function Write-KilixJson([string]$Path, $Value) {
    $temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    $backup = $temporary + '.previous'
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 16) + "`n")
        $stream = [IO.File]::Open($temporary, 'CreateNew', 'Write', 'None')
        try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
        if ([IO.File]::Exists($Path)) { [IO.File]::Replace($temporary, $Path, $backup) }
        else { [IO.File]::Move($temporary, $Path) }
    } finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
        if ([IO.File]::Exists($backup)) { [IO.File]::Delete($backup) }
    }
}

function Assert-KilixRegularPath([string]$Path) {
    $item = [IO.Path]::GetFullPath((ConvertTo-KilixFileSystemPath $Path))
    while ($item) {
        if (Test-Path -LiteralPath $item) {
            if ((Get-Item -Force -LiteralPath $item).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Setup does not follow reparse points: $item"
            }
        }
        $item = [IO.Path]::GetDirectoryName($item)
    }
}

function Assert-KilixRelativePath([string]$Name) {
    # Reject aliases, ADS, devices and traversal on every test platform.
    if (!$Name -or $Name.Contains('\') -or $Name.StartsWith('/')) { throw "Unsafe package path: $Name" }
    foreach ($part in $Name.Split('/')) {
        if (!$part -or $part -match '[\x00-\x1f<>:"|?*]' -or $part -match '[. ]$' -or
            $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
            throw "Unsafe package path: $Name"
        }
    }
}

function Test-KilixHash([string]$Path, [string]$Expected) {
    if ($Expected -notmatch '^[a-fA-F0-9]{64}$') { throw 'Pass the published SHA256 (64 hexadecimal characters).' }
    if ((Get-FileHash -LiteralPath (ConvertTo-KilixFileSystemPath $Path) -Algorithm SHA256).Hash -ne $Expected) { throw "SHA256 mismatch: $Path" }
}

function Expand-KilixArchive([string]$Archive, [string]$Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead((ConvertTo-KilixFileSystemPath $Archive))
    try {
        $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        [long]$size = 0
        if ($zip.Entries.Count -gt 100000) { throw 'Package has too many entries.' }
        foreach ($entry in $zip.Entries) {
            # .NET Framework CreateFromDirectory used backslashes in older Windows packages.
            $name = $entry.FullName.Replace('\', '/').TrimEnd('/')
            Assert-KilixRelativePath $name
            if ($name -ne 'kilix' -and !$name.StartsWith('kilix/', [StringComparison]::Ordinal)) {
                throw 'The archive must contain one kilix/ application directory.'
            }
            if (!$seen.Add($name)) { throw "Duplicate package path: $name" }
            $mode = ($entry.ExternalAttributes -shr 16) -band 0xf000
            if ($mode -ne 0 -and $mode -ne 0x8000 -and $mode -ne 0x4000) { throw "Nonregular archive entry: $name" }
            $size += $entry.Length
            if ($size -gt 32GB) { throw 'Expanded package exceeds 32 GiB.' }
        }
        $drive = New-Object IO.DriveInfo ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Destination)))
        if ($drive.IsReady -and $drive.AvailableFreeSpace -lt ($size + 64MB)) {
            throw 'Insufficient free space for the new generation; the previous installation is retained.'
        }
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            # PS5.1 Join-Path cannot join an extended path. Keep ordinary paths
            # for joining/state/launchers and convert only at the I/O boundary.
            $path = ConvertTo-KilixFileSystemPath (Join-Path $Destination $name)
            if ($name.EndsWith('/')) { [IO.Directory]::CreateDirectory($path) | Out-Null }
            else {
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path)) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $path, $false)
            }
        }
    } finally { $zip.Dispose() }
}

function Test-KilixPackage([string]$ApplicationDirectory, [string]$SignerThumbprint, [switch]$Installed) {
    $ApplicationDirectory = [IO.Path]::GetFullPath($ApplicationDirectory)
    $filesystemDirectory = ConvertTo-KilixFileSystemPath $ApplicationDirectory
    Assert-KilixRegularPath $ApplicationDirectory
    if ($SignerThumbprint) {
        # A signed hash binds the entire file manifest, including Python and vendor files,
        # rather than trusting a package that merely contains some signed executables.
        $proofPath = ConvertTo-KilixFileSystemPath (Join-Path $ApplicationDirectory 'manifest-signature.ps1')
        Assert-KilixRegularPath $proofPath
        $proof = Get-AuthenticodeSignature -LiteralPath $proofPath
        if ($proof.Status -ne 'Valid' -or $proof.SignerCertificate.Thumbprint -ne $SignerThumbprint) { throw 'Trusted manifest signature check failed.' }
        $header = Get-Content -Encoding UTF8 -LiteralPath $proofPath -TotalCount 1
        if ($header -cnotmatch '^# Kilix manifest SHA256: ([a-f0-9]{64})$') { throw 'Invalid signed manifest digest.' }
        Test-KilixHash (Join-Path $ApplicationDirectory 'manifest.json') $Matches[1]
    }
    $manifestPath = ConvertTo-KilixFileSystemPath (Join-Path $ApplicationDirectory 'manifest.json')
    $manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json
    if (!$manifest.version -or !$manifest.files) { throw 'Missing package version or file manifest.' }
    $expected = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($property in $manifest.files.PSObject.Properties) {
        $name = $property.Name
        Assert-KilixRelativePath $name
        if ($name -ieq 'manifest.json' -or !$expected.Add($name)) { throw "Duplicate manifest entry: $name" }
        $path = ConvertTo-KilixFileSystemPath (Join-Path $ApplicationDirectory $name)
        Assert-KilixRegularPath $path
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing package file: $name" }
        if ((Get-Item -LiteralPath $path).Length -ne $property.Value.size) { throw "Package size mismatch: $name" }
        Test-KilixHash $path $property.Value.sha256
    }
    foreach ($file in Get-ChildItem -LiteralPath $filesystemDirectory -Recurse -Force) {
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Package contains a reparse point.' }
        if (!$file.PSIsContainer) {
            $relative = $file.FullName.Substring($filesystemDirectory.TrimEnd('\', '/').Length + 1).Replace('\', '/')
            if ($relative -notin @('manifest.json', 'manifest-signature.ps1') -and !$expected.Contains($relative)) {
                # Older installed runtimes wrote caches. Permit only caches of manifested Python
                # sources during rollback; fresh ZIP validation still rejects every unlisted file.
                if ($Installed -and $relative -match '^(.*?/)?__pycache__/([^/]+)\.(?:cpython|pypy)-[0-9]+(?:\.opt-[0-9]+)?\.pyc$' -and
                    $expected.Contains($Matches[1] + $Matches[2] + '.py')) { continue }
                throw "Unlisted package file: $relative"
            }
        }
    }
    foreach ($required in @('Kilix.exe', 'Kilix.com', 'kilix-cli.exe', 'VERSION', 'config/kilix.conf',
            'kitty/launcher/kitty.exe', 'kitty/launcher/kitty-console.exe')) {
        if (!$expected.Contains($required)) { throw "Required package file is not listed: $required" }
    }
    if ($SignerThumbprint) {
        if ($SignerThumbprint -notmatch '^[a-fA-F0-9]{40}$') { throw 'SignerThumbprint must be the certificate SHA1 thumbprint.' }
        foreach ($name in @('Kilix.exe', 'Kilix.com', 'kilix-cli.exe')) {
            $signature = Get-AuthenticodeSignature -LiteralPath (ConvertTo-KilixFileSystemPath (Join-Path $ApplicationDirectory $name))
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $SignerThumbprint) {
                throw "Trusted publisher signature check failed: $name"
            }
        }
    }
    return $manifest.version
}

function ConvertTo-KilixArgument([AllowEmptyString()][string]$Value) {
    # Windows CRT quoting, including empty values and trailing backslashes.
    # wsl.exe treats a quoted option name as the Linux command. Leave simple
    # arguments unquoted; ProcessStartInfo invokes the program without a shell.
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    return '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function Invoke-KilixProcess([string]$FilePath, [string[]]$ArgumentList, [int]$TimeoutSeconds = 30,
                            [Text.Encoding]$OutputEncoding = [Text.Encoding]::UTF8) {
    $process = New-Object Diagnostics.Process
    $process.StartInfo.FileName = $FilePath
    $process.StartInfo.Arguments = (@($ArgumentList | ForEach-Object { ConvertTo-KilixArgument $_ }) -join ' ')
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.StandardOutputEncoding = $OutputEncoding
    $process.StartInfo.StandardErrorEncoding = $OutputEncoding
    $process.StartInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    try {
        if (!$process.Start()) { throw "Could not start $FilePath" }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (!$process.WaitForExit($TimeoutSeconds * 1000)) {
            if ($env:OS -eq 'Windows_NT') { & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F | Out-Null }
            else { $process.Kill() }
            throw "Command timed out after ${TimeoutSeconds}s: $FilePath"
        }
        return [pscustomobject]@{ exitCode = $process.ExitCode; stdout = $stdout.Result; stderr = $stderr.Result }
    } finally { $process.Dispose() }
}

function Test-KilixHealth([string]$ApplicationDirectory) {
    foreach ($case in @(@('--version', 'Kilix'), @('engine', '--help', '--config'))) {
        $result = Invoke-KilixProcess (Join-Path $ApplicationDirectory 'kilix-cli.exe') $case[0..($case.Count - 2)]
        if ($result.exitCode -ne 0 -or !$result.stdout.Contains($case[-1])) {
            throw "Installed CLI health check failed: $($case[0]); exit=$($result.exitCode). $($result.stderr)"
        }
    }
}

function Get-KilixShortcutPaths {
    foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        if (!$folder) { throw 'Desktop/Start menu folders are unavailable; use -NoShortcuts.' }
        foreach ($name in @('Kilix Windows', 'Kilix WSL')) { Join-Path $folder ($name + '.lnk') }
    }
}

function Set-KilixShortcuts([string]$ApplicationDirectory, [object[]]$Paths) {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($path in $Paths) {
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path)) | Out-Null
        $shortcut = $shell.CreateShortcut($path)
        $shortcut.TargetPath = Join-Path $ApplicationDirectory 'Kilix.exe'
        $shortcut.Arguments = if ([IO.Path]::GetFileName($path) -eq 'Kilix WSL.lnk') { '--wsl' } else { '' }
        $shortcut.WorkingDirectory = $env:USERPROFILE
        $shortcut.Description = 'Kilix for Windows'
        $shortcut.IconLocation = Join-Path $ApplicationDirectory 'kitty/launcher/kitty.ico'
        $shortcut.Save()
    }
}

function Get-KilixState([string]$Destination) {
    $path = Join-Path $Destination 'install-state.json'
    if (!(Test-Path -LiteralPath $path)) { return $null }
    $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $path | ConvertFrom-Json
    if ($state.schema -ne 1 -or !$state.current) { throw 'Unsupported or damaged install-state.json; retain it for diagnosis.' }
    foreach ($generation in @($state.current, $state.previous)) {
        if ($generation -and $generation.path -notmatch '^versions/[a-f0-9-]+/kilix$') { throw 'Invalid generation path in install state.' }
    }
    return $state
}

function Repair-KilixInstall([string]$Destination) {
    $journalPath = Join-Path $Destination 'install-pending.json'
    if (!(Test-Path -LiteralPath $journalPath)) { return }
    $journal = Get-Content -Raw -Encoding UTF8 -LiteralPath $journalPath | ConvertFrom-Json
    if ($journal.schema -ne 1 -or !$journal.transaction) { throw 'Invalid install journal; retain it for diagnosis.' }
    $state = Get-KilixState $Destination
    if (!$state -or $state.transaction -ne $journal.transaction) {
        # State is the commit point. Restore exact pre-update shortcut bytes after a crash/failure.
        $allowed = @()
        if (@($journal.shortcuts).Count) { $allowed = @(Get-KilixShortcutPaths) }
        foreach ($shortcut in $journal.shortcuts) {
            if ($shortcut.path -notin $allowed) { throw 'Unexpected shortcut location in install journal.' }
            Assert-KilixRegularPath $shortcut.path
            if ($shortcut.existed) { [IO.File]::WriteAllBytes($shortcut.path, [Convert]::FromBase64String($shortcut.bytes)) }
            elseif ([IO.File]::Exists($shortcut.path)) { [IO.File]::Delete($shortcut.path) }
        }
        Write-Host 'Recovered interrupted setup; the previous application remains active.'
    }
    [IO.File]::Delete($journalPath)
}

function Invoke-KilixInstall {
    param([string]$Action = 'Install', [string]$Archive, [string]$Sha256, [string]$Destination,
          [switch]$NoShortcuts, [string]$SignerThumbprint, [string]$ReleaseTag, [string]$ExpectedTransaction)
    $Destination = [IO.Path]::GetFullPath($Destination)
    Assert-KilixRegularPath $Destination
    if ($Action -eq 'Status') { return Get-KilixState $Destination }
    if ($Action -eq 'Install') {
        if (!$Archive) { throw 'Pass -Archive and its published -Sha256 for installation or upgrade.' }
        $Archive = (Resolve-Path -LiteralPath $Archive).Path
        Test-KilixHash $Archive $Sha256
    }
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    foreach ($name in @('.install.lock', 'install-state.json', 'install-pending.json', 'versions')) {
        Assert-KilixRegularPath (Join-Path $Destination $name)
    }
    $lock = $null
    $stage = $null
    $transaction = $null
    try {
        try { $lock = [IO.File]::Open((Join-Path $Destination '.install.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
        catch { throw 'Another setup is using this installation, or the directory is not writable.' }
        Repair-KilixInstall $Destination
        $old = Get-KilixState $Destination
        if ($ExpectedTransaction -and (!$old -or $old.transaction -ne $ExpectedTransaction)) {
            throw 'The active installation changed while downloading; rerun the update check.'
        }
        if ($Action -eq 'Recover') { return $old }
        $transaction = [Guid]::NewGuid().ToString('N')
        if ($Action -eq 'Install') {
            $relative = 'versions/' + $transaction + '/kilix'
            $stage = Join-Path $Destination ('versions/' + $transaction)
            [IO.Directory]::CreateDirectory($stage) | Out-Null
            Expand-KilixArchive $Archive $stage
            $application = Join-Path $Destination $relative
            $version = Test-KilixPackage $application $SignerThumbprint
            Test-KilixHealth $application
            $current = [pscustomobject]@{ path = $relative; version = $version; sha256 = $Sha256.ToLowerInvariant(); signer = $SignerThumbprint; releaseTag = $ReleaseTag }
        } elseif ($Action -eq 'Rollback') {
            if (!$old -or !$old.previous) { throw 'No previous managed installation is available.' }
            $current = $old.previous
            $application = Join-Path $Destination $current.path
            $null = Test-KilixPackage $application $current.signer -Installed
            Test-KilixHealth $application
        } else { throw "Unknown install action: $Action" }
        $next = [pscustomobject]@{ schema = 1; transaction = $transaction; current = $current;
            previous = $(if ($old) { $old.current } else { $null }); updated = [DateTime]::UtcNow.ToString('o') }
        $snapshots = @()
        if (!$NoShortcuts) {
            foreach ($path in Get-KilixShortcutPaths) {
                Assert-KilixRegularPath $path
                $exists = [IO.File]::Exists($path)
                $snapshots += [pscustomobject]@{ path = $path; existed = $exists;
                    bytes = $(if ($exists) { [Convert]::ToBase64String([IO.File]::ReadAllBytes($path)) } else { '' }) }
            }
        }
        $journal = [pscustomobject]@{ schema = 1; transaction = $transaction; shortcuts = $snapshots }
        Write-KilixJson (Join-Path $Destination 'install-pending.json') $journal
        if (!$NoShortcuts) { Set-KilixShortcuts $application @($snapshots | ForEach-Object { $_.path }) }
        Write-KilixJson (Join-Path $Destination 'install-state.json') $next
        $stage = $null # Never delete a committed generation, including a running previous version.
        [IO.File]::Delete((Join-Path $Destination 'install-pending.json'))
        Write-Host "$Action completed. Previous application files and user data retained."
        return $next
    } catch {
        $failure = $_
        if ($lock) {
            # A successful state commit followed by journal cleanup failure is still committed.
            $committed = Get-KilixState $Destination
            if ($committed -and $committed.transaction -eq $transaction) { $stage = $null }
            try { Repair-KilixInstall $Destination } catch { Write-Warning "Recovery remains pending: $_" }
        }
        throw $failure
    } finally {
        try {
            if ($stage) {
                $filesystemStage = ConvertTo-KilixFileSystemPath $stage
                if (Test-Path -LiteralPath $filesystemStage) { Remove-Item -LiteralPath $filesystemStage -Recurse -Force }
            }
        } finally { if ($lock) { $lock.Dispose() } }
    }
}
