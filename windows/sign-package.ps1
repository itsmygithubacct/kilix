#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ApplicationDirectory,
    [string]$CertificateThumbprint,
    [string]$TimestampServer,
    [switch]$SignManifestOnly
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'setup-common.ps1')
$ApplicationDirectory = (Resolve-Path -LiteralPath $ApplicationDirectory).Path
$record = [ordered]@{ status = 'unsigned'; files = @() }
$manifestSignature = Join-Path $ApplicationDirectory 'manifest-signature.ps1'
if (!$SignManifestOnly -and (Test-Path -LiteralPath $manifestSignature)) { Remove-Item -LiteralPath $manifestSignature }
if ($SignManifestOnly -and !$CertificateThumbprint) { throw 'Manifest signing requires an actual certificate.' }
if ($CertificateThumbprint) {
    if ($CertificateThumbprint -notmatch '^[a-fA-F0-9]{40}$') { throw 'Pass a code-signing certificate SHA1 thumbprint.' }
    $cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Thumbprint -eq $CertificateThumbprint }
    if (!$cert -or !$cert.HasPrivateKey) { throw 'The supplied code-signing certificate/private key is not available in CurrentUser\My.' }
    $chain = New-Object Security.Cryptography.X509Certificates.X509Chain
    try {
        if (!$chain.Build($cert)) { throw 'The supplied signing certificate does not validate on this Windows build host.' }
    } finally { $chain.Dispose() }
    if (!$TimestampServer) { throw 'Pass the certificate provider timestamp server with -TimestampServer.' }
    $record.status = 'authenticode-verified-on-build-host'
    $record.signerThumbprint = $cert.Thumbprint
    $record.signedManifest = 'manifest-signature.ps1'
    # Sign our launchers and scripts. Preserve vendor runtime signatures and attribution.
    $files = @('Kilix.exe', 'Kilix.com', 'kilix-cli.exe', 'kitty/launcher/kitty.exe',
        'kitty/launcher/kitty-console.exe', 'bin/kitten.exe')
    $files += @(Get-ChildItem -LiteralPath $ApplicationDirectory -Filter '*.ps1' | ForEach-Object { $_.Name })
    if ($SignManifestOnly) {
        $digest = (Get-FileHash -LiteralPath (Join-Path $ApplicationDirectory 'manifest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText($manifestSignature, "# Kilix manifest SHA256: $digest`r`n", [Text.Encoding]::ASCII)
        $files = @('manifest-signature.ps1')
    }
    foreach ($name in $files) {
        $path = Join-Path $ApplicationDirectory $name
        if (!(Test-Path -LiteralPath $path)) { throw "Signing input missing: $name" }
        $signature = Set-AuthenticodeSignature -LiteralPath $path -Certificate $cert -HashAlgorithm SHA256 -TimestampServer $TimestampServer
        if ($signature.Status -ne 'Valid' -or !$signature.TimeStamperCertificate) { throw "Signing/timestamp verification failed: $name ($($signature.Status))" }
        $verified = Get-AuthenticodeSignature -LiteralPath $path
        if ($verified.Status -ne 'Valid' -or $verified.SignerCertificate.Thumbprint -ne $cert.Thumbprint) { throw "Signature read-back failed: $name" }
        $record.files += $name
    }
} elseif ($TimestampServer) { throw 'A timestamp server alone does not sign a package; provide an actual certificate.' }
if (!$SignManifestOnly) { Write-KilixJson (Join-Path $ApplicationDirectory 'signing.json') $record }
Write-Host "Package signing: $($record.status)"
