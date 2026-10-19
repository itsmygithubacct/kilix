#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Apply', 'EnableSchedule', 'DisableSchedule')][string]$Action = 'Check',
    [ValidateSet('stable', 'preview')][string]$Channel = 'stable',
    [string]$Destination = "$env:LOCALAPPDATA\Programs\Kilix",
    [switch]$NoShortcuts,
    [string]$SignerThumbprint,
    [string]$WslDistribution,
    [string]$WslUser,
    [ValidateSet('Check', 'Apply')][string]$ScheduleAction = 'Check',
    [ValidatePattern('^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')][string]$DailyAt = '09:00'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'setup-common.ps1')
. (Join-Path $PSScriptRoot 'update-common.ps1')
$Destination = [IO.Path]::GetFullPath($Destination)
Assert-KilixRegularPath $Destination
$state = Get-KilixState $Destination
if (!$state) { throw 'No managed install at this destination. Run setup.ps1 -Archive ZIP -Sha256 HASH first.' }
if (!$SignerThumbprint) { $SignerThumbprint = $state.current.signer }
if ($Action -in @('EnableSchedule', 'DisableSchedule')) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $id = ([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($Destination.ToLowerInvariant())))).Replace('-', '').Substring(0, 16) }
    finally { $hasher.Dispose() }
    $taskName = 'Kilix-Update-' + $id
    if ($Action -eq 'DisableSchedule') {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
        Write-Host 'Kilix scheduled update task removed.'
        return
    }
    $arguments = @('-NoLogo', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-File', $PSCommandPath,
        '-Action', $ScheduleAction, '-Channel', $Channel, '-Destination', $Destination)
    if ($NoShortcuts) { $arguments += '-NoShortcuts' }
    if ($SignerThumbprint) { $arguments += @('-SignerThumbprint', $SignerThumbprint) }
    if ($WslDistribution) { $arguments += @('-WslDistribution', $WslDistribution) }
    if ($WslUser) { $arguments += @('-WslUser', $WslUser) }
    $taskAction = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Argument (@($arguments | ForEach-Object { ConvertTo-KilixArgument $_ }) -join ' ')
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $trigger = New-ScheduledTaskTrigger -Daily -At ([DateTime]::ParseExact($DailyAt, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture))
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -InputObject (New-ScheduledTask -Action $taskAction -Principal $principal -Trigger $trigger -Settings $settings) -Force | Out-Null
    Write-Host "Enabled daily $ScheduleAction on channel $Channel at $DailyAt while logged in. Results: $Destination\update-status.json"
    return
}
$download = $null
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $candidate = Select-KilixRelease @(Get-KilixPublishedReleases) $Channel
    $report = [ordered]@{ checked = [DateTime]::UtcNow.ToString('o'); channel = $Channel; status = 'no-update'; release = $null }
    if ($candidate -and (Test-KilixReleaseNewer $candidate $state.current)) {
        $assets = Get-KilixReleaseAssets $candidate
        $response = Invoke-WebRequest -UseBasicParsing -Uri $assets.checksums.browser_download_url -TimeoutSec 30
        $content = if ($response.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($response.Content) } else { [string]$response.Content }
        $digest = Get-KilixReleaseDigest $content $assets.archive.name
        if ($digest -ne $state.current.sha256) {
            $report.status = 'available'
            $report.release = $candidate.release.tag_name
            $report.sha256 = $digest
            if ($Action -eq 'Apply') {
                $download = Join-Path $Destination ('.download-' + [Guid]::NewGuid().ToString('N'))
                [IO.Directory]::CreateDirectory($download) | Out-Null
                $archive = Join-Path $download $assets.archive.name
                Invoke-WebRequest -UseBasicParsing -Uri $assets.archive.browser_download_url -OutFile $archive -TimeoutSec 900
                $installed = Invoke-KilixInstall -Archive $archive -Sha256 $digest -Destination $Destination `
                    -NoShortcuts:$NoShortcuts -SignerThumbprint $SignerThumbprint -ReleaseTag $candidate.release.tag_name -ExpectedTransaction $state.transaction
                $report.status = 'installed'
                if ($WslDistribution) {
                    & (Join-Path $Destination ($installed.current.path + '/setup.ps1')) -Action Wsl -Destination $Destination `
                        -WslDistribution $WslDistribution -WslUser $WslUser
                }
            }
        }
    }
    Write-KilixJson (Join-Path $Destination 'update-status.json') $report
    $report | ConvertTo-Json -Depth 8
} catch {
    Write-KilixJson (Join-Path $Destination 'update-status.json') @{ status = 'failed'; checked = [DateTime]::UtcNow.ToString('o'); error = "$($_.Exception.Message)" }
    throw
} finally {
    if ($download -and (Test-Path -LiteralPath $download)) { Remove-Item -LiteralPath $download -Recurse -Force }
}
