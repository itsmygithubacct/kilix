#!/usr/bin/env python3
"""Windows desktop commands over an explicitly selected SSH connection."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re
import subprocess
import sys

from kilix_sdk import windows


def remote_script(root=None):
    if root is None:
        locate = r"""$payload=[Console]::In.ReadLine()
$install=Join-Path $env:LOCALAPPDATA 'Programs\Kilix'
$state=ConvertFrom-Json -InputObject ([IO.File]::ReadAllText((Join-Path $install 'install-state.json')))
$root=Join-Path $install $state.current.path
"""
    else:
        # Keep portable paths in stdin: embedding UTF-8/base64 paths in the
        # encoded command can exceed the default Windows SSH shell's limit.
        locate = r"""$packet=ConvertFrom-Json -InputObject ([Console]::In.ReadLine())
$root=[string]$packet.root
$payload=[string]$packet.request
"""
    script = r"""$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$OutputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=$OutputEncoding
try {
""" + locate + r"""$exe=Join-Path $root 'kilix-cli.exe'
if(!(Test-Path -LiteralPath $exe -PathType Leaf)){throw 'Kilix native installation not found; use --windows-root for a portable package'}
if($null -eq $payload){throw 'Missing desktop request line'}
$request=ConvertFrom-Json -InputObject $payload
$seconds=20
if($null -ne $request.timeout){$seconds=[Math]::Max(1,[Math]::Min(60,[int]$request.timeout))}
$limit=($seconds+25)*1000
$clock=[Diagnostics.Stopwatch]::StartNew()
function Remaining { [int][Math]::Max(0,$limit-$clock.ElapsedMilliseconds) }
function Await($task,[string]$phase) {
    if(!$task.Wait((Remaining))){throw "Native desktop command timed out during $phase. It may have run; inspect before retrying."}
}
# The second input line is our local process helper, sent outside cmd.exe's limit.
Add-Type -TypeDefinition (ConvertFrom-Json -InputObject ([Console]::In.ReadLine()))
$process=[KilixSshProcess]::Start($exe,'desktop --request-json - --json')
try {
    $stdout=$process.StandardOutput.ReadToEndAsync()
    $stderr=$process.StandardError.ReadToEndAsync()
    $process.StandardInput.AutoFlush=$true
    Await ($process.StandardInput.WriteLineAsync($payload)) 'request input'
    $process.StandardInput.Close()
    if(!$process.WaitForExit((Remaining))){
        throw 'Native desktop command timed out during process exit. It may have run; inspect before retrying.'
    }
    Await $stdout 'stdout'
    Await $stderr 'stderr'
    [Console]::Out.Write($stdout.Result)
    [Console]::Error.Write($stderr.Result)
    $code=$process.ExitCode
} finally {
    try {if(!$process.HasExited){$process.Kill()}} catch {}
    $process.Dispose()
}
exit $code
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
"""
    # The default Windows SSH shell runs this through cmd.exe's 8191-character
    # command limit. Formatting/comments need not consume that budget.
    script = '\n'.join(line.strip() for line in script.splitlines()
                       if line.strip() and not line.lstrip().startswith('#'))
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return 'powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + encoded


def request_line(request, root=None):
    payload = json.dumps(request, ensure_ascii=True, allow_nan=False)
    if root is not None:
        payload = json.dumps({'root': root, 'request': payload}, ensure_ascii=True)
    # Only this fixed local source is compiled remotely; request values remain
    # JSON data. Line framing avoids waiting for Windows OpenSSH to deliver EOF.
    helper = Path(__file__).with_name('_windows_process.cs').read_text(encoding='utf-8')
    return payload + '\n' + json.dumps(helper, ensure_ascii=True) + '\n'


class SSHTransport:
    def __init__(self, host, root=None):
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:\[\]-]*', host):
            raise ValueError('Use an SSH host or configured alias, without shell syntax or options.')
        if root is not None and ('\0' in root or len(root) > 4096):
            raise ValueError('Invalid Windows package root')
        self.host, self.root = host, root

    def __call__(self, request):
        def failure(code, message):
            return {'schema': 'kilix.desktop/v1', 'ok': False,
                    'operation': request['operation'], 'error': {'code': code, 'message': message}}
        try:
            result = subprocess.run(
                ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=10', '--',
                 self.host, remote_script(self.root)],
                # Complete lines avoid waiting for Windows OpenSSH's stdin EOF
                # propagation before dispatching the request.
                input=request_line(request, self.root),
                text=True, encoding='utf-8', capture_output=True,
                timeout=request.get('timeout', 20) + 35)
        except subprocess.TimeoutExpired as error:
            message = 'SSH timed out. The action may have run; inspect before retrying.'
            diagnostic = error.stderr or ''
            if isinstance(diagnostic, bytes):
                diagnostic = diagnostic.decode('utf-8', errors='replace')
            if diagnostic.strip():
                message += '\n' + diagnostic.strip()[-2000:]
            return failure('timeout', message)
        except OSError as error:
            return failure('transport_error', str(error))
        try:
            if len(result.stdout.encode('utf-8')) > windows.MAX_RESPONSE_BYTES:
                return failure('transport_error', 'Desktop response exceeds 48 MiB.')
            reply = json.loads(result.stdout.lstrip('\ufeff'))
            if (not isinstance(reply, dict) or reply.get('schema') != 'kilix.desktop/v1'
                    or reply.get('operation') != request['operation'] or type(reply.get('ok')) is not bool):
                raise ValueError('invalid response')
            if result.returncode and reply['ok']:
                raise ValueError('SSH failed after response')
            return reply
        except (ValueError, TypeError):
            return failure('transport_error', result.stderr.strip()[-2000:] or 'SSH did not return a valid desktop response.')


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--host')
    parser.add_argument('--windows-root')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments in (['--help'], ['-h']):
        arguments = ['help']
    options = parser.parse_args(arguments)
    remaining = options.command
    if not options.host:
        if not remaining or remaining in (['help'], ['--help'], ['-h']):
            print('Usage: kilix windows --host SSH_ALIAS [--windows-root WINDOWS_PACKAGE_DIR] COMMAND [OPTIONS]\n'
                  'Uses existing SSH authentication and ProxyJump/ProxyCommand configuration.\n'
                  'The Windows user must have one active desktop and Kilix installed.\n'
                  'screenshot --local PATH saves on this caller; positional Windows paths save on Windows.\n')
            return windows.main(['--help'], prog='kilix windows')
        if remaining[0] == 'capabilities' or '--help' in remaining or '-h' in remaining:
            return windows.main(remaining, prog='kilix windows')
        parser.error('--host is required for Windows desktop control from Linux')
    try:
        transport = SSHTransport(options.host, options.windows_root)
    except ValueError as error:
        parser.error(str(error))
    return windows.main(remaining, runner=transport, prog='kilix windows')


if __name__ == '__main__':
    raise SystemExit(main())
