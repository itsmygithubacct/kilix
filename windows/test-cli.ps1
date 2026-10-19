#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ApplicationDirectory)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'setup-common.ps1')
foreach ($case in @(
 @('--help', 0, 'kilix engine --help'),
 @('help', 0, 'Kilix native control commands'),
 @('--version', 0, 'Kilix 0.2.2'),
 @('version', 0, 'Kilix 0.2.2'),
 @('needle --help', 2, 'Unsupported Windows command: needle'),
 @('launch --help', 0, 'literal native argv'),
 @('@ launch --type=tab --help', 0, '--title'),
 @('@ launch --cwd current -h', 0, '--title'),
 @('@ launch -- fixture --help', 2, 'authenticated control'),
 @('@ launch --title --help -- fixture', 2, 'authenticated control'),
 @('help pane', 0, 'kilix pane'),
 @('help tab', 0, 'kilix tab'),
 @('settings --help', 0, 'kitty.conf'),
 @('transcript --help', 0, 'capture'),
 @('status --json', 0, 'native-windows'),
 @('desktop --help', 0, 'Control Windows desktop applications'),
 @('windows capabilities --json', 0, 'kilix.desktop/v1'),
 @('desktop focus --json', 2, 'requires target'),
 @('setup --help', 0, 'rollback'),
 @('update --help', 0, 'enable-schedule'),
 @('--nonexistent', 2, 'Unsupported Windows command: --nonexistent'),
 @('engine --help', 0, '--config')
)) {
 $p=New-Object System.Diagnostics.Process
 $p.StartInfo.FileName=Join-Path $ApplicationDirectory 'kilix-cli.exe'
 $p.StartInfo.Arguments=$case[0]
 $p.StartInfo.UseShellExecute=$false
 $p.StartInfo.RedirectStandardOutput=$true
 $p.StartInfo.RedirectStandardError=$true
 $p.StartInfo.StandardOutputEncoding=[Text.Encoding]::UTF8
 $p.StartInfo.StandardErrorEncoding=[Text.Encoding]::UTF8
 # Offline checks must not inherit control of the invoking user's window.
 foreach ($key in @('KITTY_LISTEN_ON','KITTY_RC_PASSWORD','KITTY_WINDOW_ID')) {
  $p.StartInfo.EnvironmentVariables.Remove($key)
 }
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

# Both an ACP-like Python stream and UTF-8 must produce the same Unicode
# command output through redirected native launcher handles. No real user
# configuration directory is opened or written by --path.
foreach ($encoding in @('cp1252','utf-8')) {
 $p=New-Object System.Diagnostics.Process
 $p.StartInfo.FileName=Join-Path $ApplicationDirectory 'kilix-cli.exe'
 $p.StartInfo.Arguments='settings --path'
 $p.StartInfo.UseShellExecute=$false
 $p.StartInfo.RedirectStandardOutput=$true
 $p.StartInfo.RedirectStandardError=$true
 $p.StartInfo.StandardOutputEncoding=[Text.Encoding]::UTF8
 $p.StartInfo.StandardErrorEncoding=[Text.Encoding]::UTF8
 $directory=Join-Path ([IO.Path]::GetTempPath()) ('Kilix-CLI-' + [char]0xE9 + '-' + [Guid]::NewGuid().ToString('N'))
 $p.StartInfo.EnvironmentVariables['LOCALAPPDATA']=$directory
 $p.StartInfo.EnvironmentVariables['PYTHONIOENCODING']=$encoding
 try {
  if (!$p.Start()) { throw 'Could not start Unicode CLI test' }
  $stdout=$p.StandardOutput.ReadToEndAsync()
  $stderr=$p.StandardError.ReadToEndAsync()
  if (!$p.WaitForExit(15000)) { $p.Kill(); throw 'Unicode CLI test hung' }
  if ($p.ExitCode -ne 0 -or $stdout.Result.Trim() -ne (Join-Path $directory 'Kilix\kitty.conf')) {
   throw "Unicode CLI failed with $encoding`: $($stdout.Result) $($stderr.Result)"
  }
  if (Test-Path -LiteralPath $directory) { throw 'Read-only settings path created state' }
  "PASS native Unicode output with $encoding"
 } finally { $p.Dispose() }
}

# Exercise the engine's wide CRT entry point directly. The native CLI already
# uses wide arguments; its downstream Python host must preserve them as well.
$unicode = 'caf' + [char]0xe9 + ' ' + [char]0x03bb + [char]0x4e2d + ' ' + [char]::ConvertFromUtf32(0x1f642)
$literal = @('', 'a b', 'quote " and ''', 'backslash\', $unicode, '$HOME;$(false)', '--help')
$code = 'import json,sys; print(json.dumps(sys.argv[1:], ensure_ascii=True))'
$result = Invoke-KilixProcess (Join-Path $ApplicationDirectory 'kitty/launcher/kitty-console.exe') `
    (@('+runpy', $code) + $literal) 15
if ($result.exitCode -ne 0) { throw "Engine argument check failed: $($result.stderr)" }
$received = ConvertFrom-Json $result.stdout
if ($received.Count -ne $literal.Count) { throw 'Engine argument count changed.' }
for ($i = 0; $i -lt $literal.Count; $i++) {
    if ($received[$i] -cne $literal[$i]) { throw "Engine argument $i changed." }
}
Write-Host 'PASS engine Unicode, empty, quoted and literal command arguments'
