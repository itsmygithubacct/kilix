#requires -Version 5.1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../setup-common.ps1')
. (Join-Path $PSScriptRoot '../update-common.ps1')
$script:checks = 0
function Assert($Value, [string]$Message) {
    if (!$Value) { throw $Message }
    $script:checks++
}
function Release([string]$Tag, [bool]$Draft = $false, [bool]$Preview = $false) {
    $base = 'https://github.com/itsmygithubacct/kilix/releases/download/' + $Tag + '/'
    [pscustomobject]@{ tag_name = $Tag; draft = $Draft; prerelease = $Preview; published_at = '2026-01-01'; assets = @(
        @{ name = 'kilix-0.2.2-windows-x64-dev.zip'; browser_download_url = $base + 'kilix-0.2.2-windows-x64-dev.zip' },
        @{ name = 'SHA256SUMS'; browser_download_url = $base + 'SHA256SUMS' }) }
}
$stable = Release 'windows-v0.2.2'
$preview = Release 'windows-v0.2.3-preview.2' $false $true
$draft = Release 'windows-v0.2.4' $true
$linux = Release 'v9.0.0'
$releases = @($draft, $preview, $linux, $stable)
Assert ((Select-KilixRelease $releases 'stable').release.tag_name -eq 'windows-v0.2.2') 'Stable channel selected a preview/draft/unrelated tag'
Assert ((Select-KilixRelease $releases 'preview').release.tag_name -eq $preview.tag_name) 'Preview channel did not select newer preview'
Assert ($null -eq (Select-KilixRelease @($draft, $linux) 'preview')) 'No public Windows release should return no update'
Assert ($null -eq (Select-KilixRelease @() 'stable')) 'Empty release list should return no update'
$candidate = Select-KilixRelease @($stable) 'stable'
$assets = Get-KilixReleaseAssets $candidate
Assert ($assets.archive.name -eq 'kilix-0.2.2-windows-x64-dev.zip') 'Correct application asset not selected'
$hash = 'a' * 64
Assert ((Get-KilixReleaseDigest ($hash + '  ' + $assets.archive.name) $assets.archive.name) -eq $hash) 'Valid SHA256SUMS was not parsed'
foreach ($bad in @('', ($hash + '  wrong.zip'), ($hash + '  ' + $assets.archive.name + "`n" + $hash + '  ' + $assets.archive.name))) {
    $failed = $false
    try { $null = Get-KilixReleaseDigest $bad $assets.archive.name } catch { $failed = $true }
    Assert $failed 'Missing/ambiguous digest was accepted'
}
$stable.assets[0].browser_download_url = 'https://example.invalid/evil.zip'
$failed = $false
try { $null = Get-KilixReleaseAssets $candidate } catch { $failed = $true }
Assert $failed 'Foreign release asset URL was accepted'
Assert (!(Test-KilixReleaseNewer $candidate @{ version = '0.2.3-windows-dev' })) 'Core version downgrade accepted'
Assert (!(Test-KilixReleaseNewer $candidate @{ releaseTag = 'windows-v0.2.2' })) 'Same published version reinstalled'
Assert (Test-KilixReleaseNewer $candidate @{ releaseTag = 'windows-v0.2.2-preview.10' }) 'Stable should supersede its previews'
$selected = Select-KilixRelease @((Release 'windows-v0.2.2-preview.9'), (Release 'windows-v0.2.2-preview.10')) 'preview'
Assert ($selected.preview -eq 10) 'Preview numbers sorted lexically'
Assert (!(Test-KilixReleaseNewer $selected @{ releaseTag = 'windows-v0.2.2' })) 'Stable downgraded to same-version preview'

# Execute the real managed-root dispatcher in child PowerShell processes. No
# ScheduledTasks cmdlet is called and no network, install or desktop is touched.
$root = Join-Path ([IO.Path]::GetTempPath()) ('kilix-update-test-' + [Guid]::NewGuid().ToString('N'))
$destination = Join-Path $root ('install [with spaces] ' + [char]0x03bb)
$fixture = @'
param([string]$Destination, [string]$Action = 'Check', [string]$Channel = 'stable',
    [switch]$NoShortcuts, [string]$SignerThumbprint, [string]$WslDistribution, [string]$WslUser)
$result = @{ updater = $PSScriptRoot; destination = $Destination; action = $Action; channel = $Channel;
    noShortcuts = [bool]$NoShortcuts; signer = $SignerThumbprint; distribution = $WslDistribution; user = $WslUser }
[IO.File]::WriteAllText((Join-Path $Destination 'dispatched.json'), ($result | ConvertTo-Json), [Text.Encoding]::UTF8)
'@
$resultPath = Join-Path $destination 'dispatched.json'
$statePath = Join-Path $destination 'install-state.json'
function Invoke-Dispatch([string[]]$Arguments) {
    $launchArguments = $Arguments
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        # PowerShell on Linux cannot accept WindowStyle. Keep all other task
        # arguments, including the process policy flag, intact for this probe.
        $index = [Array]::IndexOf($launchArguments, '-WindowStyle')
        $launchArguments = @($launchArguments[0..($index - 1)] + $launchArguments[($index + 2)..($launchArguments.Count - 1)])
    }
    return Invoke-KilixProcess (Get-Process -Id $PID).Path $launchArguments
}
try {
    foreach ($relative in @('versions/aaaa/kilix', 'versions/bbbb/kilix')) {
        $application = Join-Path $destination $relative
        [IO.Directory]::CreateDirectory($application) | Out-Null
        [IO.File]::WriteAllText((Join-Path $application 'update.ps1'), $fixture, [Text.Encoding]::UTF8)
    }
    $source = Join-Path $PSScriptRoot '../update-dispatch.ps1'
    $dispatcher = Join-Path $destination 'update-dispatch.ps1'
    Install-KilixUpdateDispatcher $source $destination
    Install-KilixUpdateDispatcher $source $destination # Existing schedule can be reconfigured atomically.
    Assert ((Get-FileHash -LiteralPath $dispatcher).Hash -eq (Get-FileHash -LiteralPath $source).Hash) 'Dispatcher bytes changed during copy'
    Assert (@(Get-ChildItem -LiteralPath $destination -Filter '*.tmp*').Count -eq 0) 'Dispatcher replacement leaked temporary files'
    $arguments = Get-KilixScheduledUpdateArguments -Destination $destination -Action Apply -Channel preview `
        -NoShortcuts -SignerThumbprint ('1' * 40) -WslDistribution ('Ubuntu Test ' + [char]0x03bb) -WslUser 'test-user'
    $policyIndex = [Array]::IndexOf($arguments, '-ExecutionPolicy')
    Assert ($policyIndex -ge 0 -and $arguments[$policyIndex + 1] -ceq 'Bypass') 'Task must set process-scoped ExecutionPolicy Bypass'
    $fileIndex = [Array]::IndexOf($arguments, '-File')
    Assert ($fileIndex -ge 0 -and $arguments[$fileIndex + 1] -ceq $dispatcher) 'Task must target the managed-root dispatcher'
    # Keep exactly the same scheduled argv across install, upgrade and rollback.
    foreach ($relative in @('versions/aaaa/kilix', 'versions/bbbb/kilix', 'versions/aaaa/kilix')) {
        Write-KilixJson $statePath @{ schema = 1; current = @{ path = $relative } }
        $run = Invoke-Dispatch $arguments
        Assert ($run.exitCode -eq 0) "Scheduled dispatcher failed: $($run.stderr)"
        $record = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $expectedUpdater = [IO.Path]::GetFullPath((Join-Path $destination $relative))
        Assert ($record.updater -ceq $expectedUpdater) 'Schedule executed a stale updater after upgrade/rollback'
        Assert ($record.destination -ceq $destination) 'Dispatcher lost Unicode/spaces install destination'
        Assert ($record.action -ceq 'Apply' -and $record.channel -ceq 'preview' -and $record.noShortcuts -and
            $record.signer -ceq ('1' * 40) -and $record.distribution -ceq ('Ubuntu Test ' + [char]0x03bb) -and
            $record.user -ceq 'test-user') 'Scheduled update options changed during dispatch'
    }
    $defaults = Get-KilixScheduledUpdateArguments -Destination $destination -Action Check -Channel stable
    $run = Invoke-Dispatch $defaults
    Assert ($run.exitCode -eq 0) "Default scheduled check failed: $($run.stderr)"
    $record = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert ($record.action -ceq 'Check' -and $record.channel -ceq 'stable' -and !$record.noShortcuts -and
        !$record.signer -and !$record.distribution -and !$record.user) 'Default check gained unrequested options'
    Write-KilixJson $statePath @{ schema = 1; current = @{ path = 'versions/cccc/kilix' } }
    $run = Invoke-Dispatch $arguments
    Assert ($run.exitCode -ne 0 -and $run.stderr -match 'Active Kilix updater is missing') 'Missing current updater silently fell back to a retained generation'
    Write-KilixJson $statePath @{ schema = 1; current = @{ path = '../elsewhere' } }
    $run = Invoke-Dispatch $arguments
    Assert ($run.exitCode -ne 0 -and $run.stderr -match 'Invalid managed installation state') 'Dispatcher accepted a path outside the managed generations'
    Write-Host "PASS: $script:checks update selection/schedule assertions (offline; no release or schedule created)"
} finally { if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force } }
