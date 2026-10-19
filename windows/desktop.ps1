#requires -Version 5.1
<#
.SYNOPSIS
Runs one Kilix desktop request in the current interactive Windows user session.
.DESCRIPTION
The transport supplies private request/response paths and owns task lifetime.
This script does not schedule tasks, retry actions, or select another session.
Use Get-Help for offline documentation; -Help also performs no desktop work.
#>
[CmdletBinding()]
param(
    [string]$RequestPath,
    [string]$ResponsePath,
    [switch]$Help
)

if ($Help) {
    Write-Output 'desktop.ps1 -RequestPath FILE -ResponsePath FILE'
    Write-Output 'Read one kilix.desktop/v1 UTF-8 JSON request and atomically write its response.'
    exit 0
}

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$operation = $null
$exitCode = 1
$response = $null
$phase = 'read'
$responseAllowed = $true

try {
    if (!$RequestPath -or !$ResponsePath) { throw 'RequestPath and ResponsePath are required.' }
    $requestFullPath = [IO.Path]::GetFullPath($RequestPath)
    $responseFullPath = [IO.Path]::GetFullPath($ResponsePath)
    if ([string]::Equals($requestFullPath, $responseFullPath, [StringComparison]::OrdinalIgnoreCase)) {
        $responseAllowed = $false
        throw 'Request and response paths must differ.'
    }
    $requestInfo = [IO.FileInfo]::new($requestFullPath)
    if ($requestInfo.Length -gt 1048576) { throw 'Request JSON exceeds 1 MiB.' }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $requestBytes = [IO.File]::ReadAllBytes($requestFullPath)
    if ($requestBytes.Length -gt 1048576) { throw 'Request JSON exceeds 1 MiB.' }
    $offset = 0
    if ($requestBytes.Length -ge 3 -and $requestBytes[0] -eq 239 -and $requestBytes[1] -eq 187 -and $requestBytes[2] -eq 191) { $offset = 3 }
    $raw = $utf8.GetString($requestBytes, $offset, $requestBytes.Length - $offset)
    # Only recover the operation label for load/platform error envelopes here.
    # This loose header conversion is never used for native dispatch or validation.
    try {
        $header = ConvertFrom-Json -InputObject $raw -ErrorAction Stop
        if ($null -ne $header) {
            foreach ($property in $header.PSObject.Properties) {
                if ($property.Name -ceq 'operation' -and $property.Value -is [string]) { $operation = $property.Value }
            }
        }
    } catch { }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or [IntPtr]::Size -ne 8) {
        $response = @{ schema = 'kilix.desktop/v1'; ok = $false; operation = $operation; error = @{ code = 'unsupported_platform'; message = 'Desktop automation requires Windows x64.' } }
    } else {
        $phase = 'load'
        if (-not ('KilixDesktop' -as [type])) {
            $names = @('System', 'System.Core', 'System.Windows.Forms', 'System.Drawing', 'UIAutomationClient', 'UIAutomationTypes', 'WindowsBase')
            $references = foreach ($name in $names) {
                Add-Type -AssemblyName $name -ErrorAction Stop
                ([AppDomain]::CurrentDomain.GetAssemblies() | Where-Object { $_.GetName().Name -eq $name } | Select-Object -First 1).Location
            }
            Add-Type -LiteralPath (Join-Path $PSScriptRoot 'desktop-native.cs') -ReferencedAssemblies $references -ErrorAction Stop
        }
        $phase = 'read'
        $request = [KilixDesktop]::ReadRequest($raw)
        if ($request.ContainsKey('operation') -and $request['operation'] -is [string]) { $operation = $request['operation'] }
        $phase = 'execute'
        $result = [KilixDesktop]::Execute($request)
        $response = @{ schema = 'kilix.desktop/v1'; ok = $true; operation = $operation; result = $result }
        $exitCode = 0
    }
} catch {
    $exception = $_.Exception
    while ($exception.InnerException) { $exception = $exception.InnerException }
    $code = 'invalid_request'
    $message = 'The request could not be read or validated.'
    if ($exception.GetType().FullName -eq 'KilixDesktopException') {
        $code = $exception.Code
        $message = $exception.Message
    } elseif ($phase -eq 'load') {
        $code = 'backend_unavailable'
        $message = 'The Windows desktop backend could not be loaded.'
    } elseif ($phase -eq 'execute') {
        $code = 'native_error'
        $message = 'The Windows desktop operation failed; completion may be unknown.'
    }
    $response = @{ schema = 'kilix.desktop/v1'; ok = $false; operation = $operation; error = @{ code = $code; message = $message } }
}

# Write a complete UTF-8 file beside the destination, then rename it atomically.
# A response is never streamed to the transport while the operation is running.
$temporary = $null
try {
    if (!$ResponsePath -or !$responseAllowed) { throw 'A distinct ResponsePath is required.' }
    $responseFullPath = [IO.Path]::GetFullPath($ResponsePath)
    $temporary = [IO.Path]::Combine([IO.Path]::GetDirectoryName($responseFullPath), '.kilix-response-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    $json = ConvertTo-Json -InputObject $response -Depth 32 -Compress
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json + "`n")
    # Allow a 32 MiB PNG in base64 plus its envelope, with a bounded reply.
    if ($bytes.Length -gt 50331648) { throw 'Desktop response exceeds 48 MiB.' }
    $stream = [IO.FileStream]::new($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    if ([IO.File]::Exists($responseFullPath)) {
        # PowerShell converts ordinary $null to an empty string for this overload.
        [IO.File]::Replace($temporary, $responseFullPath, [System.Management.Automation.Language.NullString]::Value)
    }
    else { [IO.File]::Move($temporary, $responseFullPath) }
    $temporary = $null
} catch {
    [Console]::Error.WriteLine('Kilix desktop: unable to write the response file.')
    $exitCode = 1
} finally {
    if ($temporary -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
}
exit $exitCode
