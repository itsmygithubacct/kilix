#requires -Version 5.1
# Offline transaction tests: real ZIPs, manifests, state, locks and failure recovery.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../setup-common.ps1')
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = Join-Path ([IO.Path]::GetTempPath()) ('kilix-setup-test-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
$destination = Join-Path $root ('install with spaces ' + [char]0x03bb)
$shortcutPath = Join-Path $root ('test ' + [char]0x4e2d + '.lnk')
$script:checks = 0
$script:failHealth = $false
$script:failShortcut = $false
# Only OS-dependent seams are replaced; tests exercise the production transaction.
function Test-KilixHealth([string]$ApplicationDirectory) {
    if ($script:failHealth) { throw 'Injected health failure' }
}
function Get-KilixShortcutPaths { $shortcutPath }
function Set-KilixShortcuts([string]$ApplicationDirectory, [object[]]$Paths) {
    [IO.File]::WriteAllText($Paths[0], $ApplicationDirectory)
    if ($script:failShortcut) { throw 'Injected shortcut failure' }
}
function Assert($Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
    $script:checks++
}
function Expect-Failure([scriptblock]$Body, [string]$Pattern) {
    $caught = $null
    try { & $Body | Out-Null } catch { $caught = $_ }
    Assert ($caught -and "$caught" -match $Pattern) "Expected failure $Pattern, got $caught"
}
function Make-Package([string]$Name, [hashtable]$Extra = @{}, [string]$Omit = '') {
    $content = Join-Path $root ($Name + '/kilix')
    [IO.Directory]::CreateDirectory($content) | Out-Null
    $manifest = [ordered]@{}
    foreach ($file in @('Kilix.exe', 'Kilix.com', 'kilix-cli.exe', 'VERSION', 'config/kilix.conf', 'wsl/example.py',
            'kitty/launcher/kitty.exe', 'kitty/launcher/kitty-console.exe')) {
        if ($file -eq $Omit) { continue }
        $target = Join-Path $content $file
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
        [IO.File]::WriteAllText($target, "$Name $file")
        $manifest[$file] = @{ sha256 = (Get-FileHash -LiteralPath $target).Hash; size = (Get-Item -LiteralPath $target).Length }
    }
    Write-KilixJson (Join-Path $content 'manifest.json') @{ version = $Name; files = $manifest }
    $archive = Join-Path $root ($Name + '.zip')
    [IO.Compression.ZipFile]::CreateFromDirectory($content, $archive, 'Optimal', $true)
    if ($Extra.Count) {
        $zip = [IO.Compression.ZipFile]::Open($archive, 'Update')
        try {
            foreach ($entry in $Extra.GetEnumerator()) {
                $stream = $zip.CreateEntry($entry.Key).Open()
                try { $bytes = [Text.Encoding]::UTF8.GetBytes($entry.Value); $stream.Write($bytes, 0, $bytes.Length) }
                finally { $stream.Dispose() }
            }
        } finally { $zip.Dispose() }
    }
    return @{ Archive = $archive; Sha256 = (Get-FileHash -LiteralPath $archive).Hash; Destination = $destination }
}
try {
    $first = Make-Package 'one'
    $one = Invoke-KilixInstall @first
    Assert ($one.current.version -eq 'one') 'Fresh install did not activate'
    $firstPath = Join-Path $destination $one.current.path
    # Imports from older runtimes created caches after installation.
    $cache = Join-Path $firstPath 'wsl/__pycache__'
    [IO.Directory]::CreateDirectory($cache) | Out-Null
    [IO.File]::WriteAllText((Join-Path $cache 'example.cpython-312.pyc'), 'runtime cache')
    Expect-Failure { Test-KilixPackage $firstPath } 'Unlisted package file'
    $oldShortcut = [IO.File]::ReadAllText($shortcutPath)
    # Legacy files and personal data inside/alongside an old installation are retained.
    [IO.File]::WriteAllText((Join-Path $destination 'user-work.txt'), 'keep me')
    $second = Make-Package 'two'
    $two = Invoke-KilixInstall @second
    Assert ($two.previous.path -eq $one.current.path) 'Previous generation missing'
    Assert (Test-Path -LiteralPath (Join-Path $firstPath 'Kilix.exe')) 'Upgrade removed old files'
    Assert ([IO.File]::ReadAllText((Join-Path $destination 'user-work.txt')) -eq 'keep me') 'Upgrade changed user work'
    $rolled = Invoke-KilixInstall -Action Rollback -Destination $destination
    Assert ($rolled.current.path -eq $one.current.path) 'Rollback did not restore generation'
    Assert ([IO.File]::ReadAllText($shortcutPath) -eq $oldShortcut) 'Rollback did not restore shortcut target'
    [IO.File]::WriteAllText((Join-Path $cache 'unknown.cpython-312.pyc'), 'not a known source')
    Expect-Failure { Test-KilixPackage $firstPath -Installed } 'Unlisted package file'
    [IO.File]::Delete((Join-Path $cache 'unknown.cpython-312.pyc'))

    $active = [IO.File]::ReadAllText((Join-Path $destination 'install-state.json'))
    $third = Make-Package 'three'
    Expect-Failure { Invoke-KilixInstall @third -ExpectedTransaction 'stale-update-check' } 'changed while downloading'
    $script:failHealth = $true
    Expect-Failure { Invoke-KilixInstall @third } 'health failure'
    $script:failHealth = $false
    Assert ([IO.File]::ReadAllText((Join-Path $destination 'install-state.json')) -eq $active) 'Failed health changed state'
    Assert (@(Get-ChildItem -LiteralPath (Join-Path $destination 'versions')).Count -eq 2) 'Failed stage leaked files'
    $script:failShortcut = $true
    Expect-Failure { Invoke-KilixInstall @third } 'shortcut failure'
    $script:failShortcut = $false
    Assert ([IO.File]::ReadAllText($shortcutPath) -eq $oldShortcut) 'Failed shortcut update was not restored'
    Assert ([IO.File]::ReadAllText((Join-Path $destination 'install-state.json')) -eq $active) 'Failed shortcut changed state'

    # Interrupted before state commit: restore old bytes. After commit: keep new bytes.
    $pending = Join-Path $destination 'install-pending.json'
    $snapshot = @(@{ path = $shortcutPath; existed = $true; bytes = [Convert]::ToBase64String([IO.File]::ReadAllBytes($shortcutPath)) })
    Write-KilixJson $pending @{ schema = 1; transaction = 'interrupted'; shortcuts = $snapshot }
    [IO.File]::WriteAllText($shortcutPath, 'partial shortcut')
    $null = Invoke-KilixInstall -Action Recover -Destination $destination
    Assert ([IO.File]::ReadAllText($shortcutPath) -eq $oldShortcut) 'Crash recovery failed'
    Write-KilixJson $pending @{ schema = 1; transaction = $rolled.transaction; shortcuts = $snapshot }
    [IO.File]::WriteAllText($shortcutPath, 'committed shortcut')
    $null = Invoke-KilixInstall -Action Recover -Destination $destination
    Assert ([IO.File]::ReadAllText($shortcutPath) -eq 'committed shortcut') 'Recovery undid committed shortcut'
    Assert (!(Test-Path -LiteralPath $pending)) 'Recovery did not clear journal'

    $lock = [IO.File]::Open((Join-Path $destination '.install.lock'), 'Open', 'ReadWrite', 'None')
    try { Expect-Failure { Invoke-KilixInstall @third } 'Another setup' } finally { $lock.Dispose() }
    $badHash = $third.Clone(); $badHash.Sha256 = '0' * 64
    Expect-Failure { Invoke-KilixInstall @badHash } 'SHA256 mismatch'
    # Legacy .NET Framework ZIP paths use backslashes. Check duplicate aliases too.
    $legacyZip = Make-Package 'legacy-zip'
    $zip = [IO.Compression.ZipFile]::Open($legacyZip.Archive, 'Update')
    try {
        foreach ($entry in @($zip.Entries)) {
            $data = New-Object IO.MemoryStream
            $input = $entry.Open()
            try { $input.CopyTo($data) } finally { $input.Dispose() }
            $name = $entry.FullName.Replace('/', '\')
            $entry.Delete()
            $output = $zip.CreateEntry($name).Open()
            try { $bytes = $data.ToArray(); $output.Write($bytes, 0, $bytes.Length) }
            finally { $output.Dispose(); $data.Dispose() }
        }
    } finally { $zip.Dispose() }
    $legacyZip.Sha256 = (Get-FileHash -LiteralPath $legacyZip.Archive).Hash
    $legacyZip.Destination = Join-Path $root 'legacy-install'
    $legacyState = Invoke-KilixInstall @legacyZip -NoShortcuts
    Assert ($legacyState.current.version -eq 'legacy-zip') 'Legacy backslash ZIP failed'
    foreach ($name in @('../escape', 'kilix/../escape', 'kilix\..\escape', 'kilix/con.txt', 'kilix/file:stream', 'kilix/trailing.', 'kilix/KILIX.EXE', 'kilix\Kilix.exe', 'kilix/\evil')) {
        $extra = @{}; $extra[$name] = 'bad'
        $bad = Make-Package ('bad-' + [Guid]::NewGuid().ToString('N')) $extra
        Expect-Failure { Invoke-KilixInstall @bad } 'Unsafe package path|Duplicate package path'
    }
    $unlisted = Make-Package 'unlisted' @{ 'kilix/surprise.dll' = 'bad' }
    Expect-Failure { Invoke-KilixInstall @unlisted } 'Unlisted package file'
    $missing = Make-Package 'missing' @{} 'Kilix.exe'
    Expect-Failure { Invoke-KilixInstall @missing } 'Required package file'
    $damaged = Make-Package 'damaged'
    $zip = [IO.Compression.ZipFile]::Open($damaged.Archive, 'Update')
    try {
        $zip.GetEntry('kilix/Kilix.exe').Delete()
        $stream = $zip.CreateEntry('kilix/Kilix.exe').Open()
        try { $stream.WriteByte(1) } finally { $stream.Dispose() }
    } finally { $zip.Dispose() }
    $damaged.Sha256 = (Get-FileHash -LiteralPath $damaged.Archive).Hash
    Expect-Failure { Invoke-KilixInstall @damaged } 'Package size mismatch'
    # One-way upgrade to the same root leaves unrecognized old portable layouts alone.
    $legacy = Join-Path $destination 'kilix'
    [IO.Directory]::CreateDirectory($legacy) | Out-Null
    [IO.File]::WriteAllText((Join-Path $legacy 'model.bin'), 'existing')
    $null = Invoke-KilixInstall @third -NoShortcuts
    Assert ([IO.File]::ReadAllText((Join-Path $legacy 'model.bin')) -eq 'existing') 'Legacy data was modified'
    Assert ([IO.File]::ReadAllText($shortcutPath) -eq 'committed shortcut') 'NoShortcuts modified shortcuts'
    # Signature API seam only: prove that a pinned signer binds the manifest bytes.
    # This fixture creates no real certificate/signature and claims no Windows trust.
    $script:fixtureSigner = '1' * 40
    function Get-AuthenticodeSignature([string]$LiteralPath) {
        [pscustomobject]@{ Status = 'Valid'; SignerCertificate = @{ Thumbprint = $script:fixtureSigner } }
    }
    $signedFixture = Join-Path $root 'three/kilix'
    $manifestPath = Join-Path $signedFixture 'manifest.json'
    $digest = (Get-FileHash -LiteralPath $manifestPath).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $signedFixture 'manifest-signature.ps1'), "# Kilix manifest SHA256: $digest`r`n")
    Assert ((Test-KilixPackage $signedFixture $script:fixtureSigner) -eq 'three') 'Signed manifest fixture rejected'
    [IO.File]::AppendAllText($manifestPath, ' ')
    Expect-Failure { Test-KilixPackage $signedFixture $script:fixtureSigner } 'SHA256 mismatch'
    # Real subprocess argument preservation (Windows CI uses the same code under PS5.1).
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (!$python) { $python = (Get-Command python3).Source }
    $values = @('', 'two words', 'C:\trailing\', 'an"embedded"quote', '$literal; &', ([string][char]0x03bb))
    $result = Invoke-KilixProcess $python (@('-c', 'import json,sys; print(json.dumps(sys.argv[1:]))') + $values)
    $roundTrip = @($result.stdout | ConvertFrom-Json)
    Assert ($result.exitCode -eq 0 -and $roundTrip.Count -eq $values.Count) 'Subprocess argument count failed'
    for ($i = 0; $i -lt $values.Count; $i++) { Assert ($roundTrip[$i] -ceq $values[$i]) "Subprocess argument $i changed" }
    Write-Host "PASS: $script:checks transaction assertions"
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
