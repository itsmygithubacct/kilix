#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ApplicationDirectory)
$ErrorActionPreference='Stop'
foreach ($case in @(
 @('--help', 0, 'kilix engine --help'),
 @('help', 0, 'Inside WSL, kilix help'),
 @('--version', 0, 'Kilix 0.2.2'),
 @('version', 0, 'Kilix 0.2.2'),
 @('needle --help', 2, 'Unsupported Windows command: needle'),
 @('--launch kilix-pong', 2, 'Unsupported Windows command: --launch'),
 @('--nonexistent', 2, 'Unsupported Windows command: --nonexistent'),
 @('engine --help', 0, '--config')
)) {
 $p=New-Object System.Diagnostics.Process
 $p.StartInfo.FileName=Join-Path $ApplicationDirectory 'kilix-cli.exe'
 $p.StartInfo.Arguments=$case[0]
 $p.StartInfo.UseShellExecute=$false
 $p.StartInfo.RedirectStandardOutput=$true
 $p.StartInfo.RedirectStandardError=$true
 try {
  if (!$p.Start()) { throw 'Could not start CLI' }
  $stdout=$p.StandardOutput.ReadToEndAsync()
  $stderr=$p.StandardError.ReadToEndAsync()
  if (!$p.WaitForExit(15000)) { $p.Kill(); throw "CLI hung: $($case[0])" }
  $output=$stdout.Result+$stderr.Result
  if ($p.ExitCode -ne $case[1] -or !$output.Contains($case[2])) {
   throw "CLI failed: $($case[0]); exit=$($p.ExitCode); output=$output"
  }
  "PASS $($case[0])"
 } finally { $p.Dispose() }
}
