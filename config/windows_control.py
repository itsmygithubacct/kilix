#!/usr/bin/env python3
"""Windows desktop commands over an explicitly selected SSH connection."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

from kilix_sdk import windows


TIMING = 'kilix-ssh-timing '


def split_timing(stderr, request_id):
    """Remove the remote timing line; return (diagnostics, phases or None).

    A timing line naming another request means the reply is not ours."""
    lines, phases = [], None
    for line in (stderr or '').splitlines():
        if not line.startswith(TIMING):
            lines.append(line)
            continue
        try:
            data = json.loads(line[len(TIMING):])
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get('id') != request_id:
            raise ValueError('reply belongs to another request')
        phases = {key: data[key] for key in ('setup_ms', 'native_ms', 'remote_ms')
                  if type(data.get(key)) is int and data[key] >= 0}
    return '\n'.join(lines).strip(), phases


def remote_script(root=None):
    if root is None:
        locate = r"""$install=Join-Path $env:LOCALAPPDATA 'Programs\Kilix'
$state=ConvertFrom-Json -InputObject ([IO.File]::ReadAllText((Join-Path $install 'install-state.json')))
$root=Join-Path $install $state.current.path
"""
    else:
        # Keep portable paths in stdin: embedding UTF-8/base64 paths in the
        # encoded command can exceed the default Windows SSH shell's limit.
        locate = r"""$root=[string]$packet.root
"""
    script = r"""$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$OutputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=$OutputEncoding
$clock=[Diagnostics.Stopwatch]::StartNew()
$id=''
$setup=$null
$native=$null
try {
$packet=ConvertFrom-Json -InputObject ([Console]::In.ReadLine())
$payload=[string]$packet.request
$id=[string]$packet.id
""" + locate + r"""$exe=Join-Path $root 'kilix-cli.exe'
if(!(Test-Path -LiteralPath $exe -PathType Leaf)){throw 'Kilix native installation not found; use --windows-root for a portable package'}
if(!$payload){throw 'Missing desktop request line'}
$request=ConvertFrom-Json -InputObject $payload
$seconds=20
if($null -ne $request.timeout){$seconds=[Math]::Max(1,[Math]::Min(60,[int]$request.timeout))}
$limit=($seconds+25)*1000+$clock.ElapsedMilliseconds
function Remaining { [int][Math]::Max(0,$limit-$clock.ElapsedMilliseconds) }
function Await($task,[string]$phase) {
    if(!$task.Wait((Remaining))){throw "Native desktop command timed out during $phase. It may have run; inspect before retrying."}
}
# The second input line is our local process helper, sent outside cmd.exe's limit.
Add-Type -TypeDefinition (ConvertFrom-Json -InputObject ([Console]::In.ReadLine()))
$setup=$clock.ElapsedMilliseconds
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
    $native=$clock.ElapsedMilliseconds-$setup
    [Console]::Out.Write($stdout.Result)
    [Console]::Error.Write($stderr.Result)
    $code=$process.ExitCode
} finally {
    try {if(!$process.HasExited){$process.Kill()}} catch {}
    $process.Dispose()
}
} catch { [Console]::Error.WriteLine($_.Exception.Message); $code=1 }
# Last stderr line: this request's identity and remote phase times.
[Console]::Error.WriteLine("`n" + $TIMING + (ConvertTo-Json -Compress @{id=$id;setup_ms=$setup;native_ms=$native;remote_ms=$clock.ElapsedMilliseconds}))
exit $code
"""
    script = script.replace('$TIMING', "'" + TIMING + "'")
    # The default Windows SSH shell runs this through cmd.exe's 8191-character
    # command limit. Formatting/comments need not consume that budget.
    script = '\n'.join(line.strip() for line in script.splitlines()
                       if line.strip() and not line.lstrip().startswith('#'))
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return 'powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + encoded


def request_line(request, root=None, request_id=''):
    packet = {'id': request_id, 'request': json.dumps(request, ensure_ascii=True, allow_nan=False)}
    if root is not None:
        packet['root'] = root
    payload = json.dumps(packet, ensure_ascii=True)
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
        request_id = secrets.token_hex(8)
        started = time.monotonic()
        transport = {'request_id': request_id, 'host': self.host}

        def timed(reply, phases=None):
            # Where a slow request spent its time: SSH and PowerShell startup,
            # the remote helper's setup, then the native desktop command.
            transport['total_ms'] = round((time.monotonic() - started) * 1000)
            if phases:
                transport.update(phases)
                if 'remote_ms' in phases:
                    transport['ssh_ms'] = max(0, transport['total_ms'] - phases['remote_ms'])
            return {**reply, 'transport': transport}

        def failure(code, message, phases=None):
            return timed({'schema': 'kilix.desktop/v1', 'ok': False, 'operation': request['operation'],
                          'error': {'code': code, 'message': f'{message} (SSH request {request_id})'}}, phases)
        try:
            result = subprocess.run(
                ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=10', '--',
                 self.host, remote_script(self.root)],
                # Complete lines avoid waiting for Windows OpenSSH's stdin EOF
                # propagation before dispatching the request.
                input=request_line(request, self.root, request_id),
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
            diagnostics, phases = split_timing(result.stderr, request_id)
        except ValueError as error:
            return failure('transport_error', str(error))
        try:
            if len(result.stdout.encode('utf-8')) > windows.MAX_RESPONSE_BYTES:
                return failure('transport_error', 'Desktop response exceeds 48 MiB.', phases)
            reply = json.loads(result.stdout.lstrip('\ufeff'))
            if (not isinstance(reply, dict) or reply.get('schema') != 'kilix.desktop/v1'
                    or reply.get('operation') != request['operation'] or type(reply.get('ok')) is not bool):
                raise ValueError('invalid response')
            if result.returncode and reply['ok']:
                raise ValueError('SSH failed after response')
            return timed(reply, phases)
        except (ValueError, TypeError):
            return failure('transport_error', diagnostics[-2000:] or 'SSH did not return a valid desktop response.', phases)


def leading_options(arguments):
    """Split the leading --host/--windows-root options from the desktop command.

    Everything after them belongs to the desktop CLI, including an option-first
    raw request such as `--json --request-json -`, which argparse's REMAINDER
    would reject. Later --host words stay literal command data.
    """
    options, rest = {'host': None, 'windows_root': None}, list(arguments)
    while rest:
        key, sep, value = rest[0].partition('=')
        if key not in ('--host', '--windows-root'):
            break
        if sep:
            rest = rest[1:]
        elif len(rest) > 1:
            value, rest = rest[1], rest[2:]
        else:
            raise ValueError(f'{key} needs a value')
        options[key[2:].replace('-', '_')] = value
    return argparse.Namespace(**options, command=rest)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='kilix windows', add_help=False)
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments in (['--help'], ['-h']):
        arguments = ['help']
    try:
        options = leading_options(arguments)
    except ValueError as error:
        parser.error(str(error))
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
