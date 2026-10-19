#requires -Version 5.1
# SPDX-License-Identifier: GPL-3.0-only
# Release selection is separated from network and installation for offline tests.
function Get-KilixReleaseVersion([string]$Tag) {
    if ($Tag -cmatch '^windows-v(\d+\.\d+\.\d+)(?:-preview\.(\d+))?$') {
        return [pscustomobject]@{ version = [Version]$Matches[1]; preview = $(if ($Matches[2]) { [int]$Matches[2] } else { [int]::MaxValue }) }
    }
    return $null
}

function Select-KilixRelease([object[]]$Releases, [string]$Channel = 'stable') {
    $candidates = @()
    foreach ($release in $Releases) {
        $version = Get-KilixReleaseVersion $release.tag_name
        if ($release.draft -or !$release.published_at -or !$version) { continue }
        if ($Channel -eq 'stable' -and ($release.prerelease -or $version.preview -ne [int]::MaxValue)) { continue }
        # Stable tags mislabeled as prereleases are not silently promoted.
        if ($version.preview -eq [int]::MaxValue -and $release.prerelease) { continue }
        $candidates += [pscustomobject]@{ release = $release; version = $version.version; preview = $version.preview }
    }
    return $candidates | Sort-Object -Property version, preview -Descending | Select-Object -First 1
}

function Get-KilixReleaseAssets($Candidate) {
    $release = $Candidate.release
    $version = [regex]::Escape([string]$Candidate.version)
    $archives = @($release.assets | Where-Object { $_.name -cmatch "^kilix-$version-windows-x64(?:-(?:dev|preview))?\.zip$" })
    $checksums = @($release.assets | Where-Object { $_.name -ceq 'SHA256SUMS' })
    if ($archives.Count -ne 1 -or $checksums.Count -ne 1) { throw 'The Windows release must have exactly one x64 application ZIP and SHA256SUMS.' }
    foreach ($asset in @($archives[0], $checksums[0])) {
        $expected = 'https://github.com/itsmygithubacct/kilix/releases/download/' +
            [Uri]::EscapeDataString($release.tag_name) + '/' + [Uri]::EscapeDataString($asset.name)
        if ($asset.browser_download_url -cne $expected) { throw 'Unexpected release asset URL.' }
    }
    return [pscustomobject]@{ archive = $archives[0]; checksums = $checksums[0] }
}

function Get-KilixReleaseDigest([string]$Checksums, [string]$Name) {
    $hashes = @()
    foreach ($line in ($Checksums -split '\r?\n')) {
        if ($line -cmatch '^([a-fA-F0-9]{64}) [ *](.+)$' -and $Matches[2] -ceq $Name) { $hashes += $Matches[1].ToLowerInvariant() }
    }
    if ($hashes.Count -ne 1) { throw 'SHA256SUMS must contain exactly one digest for the application ZIP.' }
    return $hashes[0]
}

function Get-KilixPublishedReleases {
    $releases = @()
    for ($page = 1; $page -le 10; $page++) {
        $batch = @(Invoke-RestMethod -Uri "https://api.github.com/repos/itsmygithubacct/kilix/releases?per_page=100&page=$page" `
            -Headers @{ Accept = 'application/vnd.github+json'; 'User-Agent' = 'Kilix-Windows-Updater' } -TimeoutSec 30)
        $releases += $batch
        if ($batch.Count -lt 100) { return $releases }
    }
    throw 'Release index exceeded the updater pagination limit; inspect releases manually.'
}

function Test-KilixReleaseNewer($Candidate, $Current) {
    if ($Current.releaseTag) {
        $installed = Get-KilixReleaseVersion $Current.releaseTag
        if ($installed) {
            return $Candidate.version -gt $installed.version -or
                ($Candidate.version -eq $installed.version -and $Candidate.preview -gt $installed.preview)
        }
    }
    # Development/legacy installs have no release tag. Never downgrade their core version.
    if ($Current.version -match '^(\d+\.\d+\.\d+)') { return $Candidate.version -ge [Version]$Matches[1] }
    return $true
}
