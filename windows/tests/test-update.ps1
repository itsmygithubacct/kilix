#requires -Version 5.1
$ErrorActionPreference = 'Stop'
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
Write-Host "PASS: $script:checks update selection assertions (offline; no release or schedule created)"
