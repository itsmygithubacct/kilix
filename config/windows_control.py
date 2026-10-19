#!/usr/bin/env python3
"""Windows desktop commands over an explicitly selected SSH connection."""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys

from kilix_sdk import windows


def remote_script(root=None):
    if root is None:
        locate = r"""$install=Join-Path $env:LOCALAPPDATA 'Programs\Kilix'
$state=ConvertFrom-Json -InputObject ([IO.File]::ReadAllText((Join-Path $install 'install-state.json')))
$root=Join-Path $install $state.current.path
"""
    else:
        encoded = base64.b64encode(root.encode('utf-8')).decode('ascii')
        locate = "$root=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "'))\n"
    script = r"""$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$OutputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=$OutputEncoding
try {
""" + locate + r"""$exe=Join-Path $root 'kilix-cli.exe'
if(!(Test-Path -LiteralPath $exe -PathType Leaf)){throw 'Kilix native installation not found; use --windows-root for a portable package'}
$payload=[Console]::In.ReadToEnd()
$payload | & $exe desktop --request-json - --json
exit $LASTEXITCODE
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
"""
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return 'powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + encoded


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
                input=json.dumps(request, ensure_ascii=True, allow_nan=False),
                text=True, encoding='utf-8', capture_output=True,
                timeout=request.get('timeout', 20) + 35)
        except subprocess.TimeoutExpired:
            return failure('timeout', 'SSH timed out. The action may have run; inspect before retrying.')
        except OSError as error:
            return failure('transport_error', str(error))
        try:
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
                  'Screenshot paths are on Windows; copy them with scp if needed.\n')
            return windows.main(['--help'])
        if remaining[0] == 'capabilities' or '--help' in remaining or '-h' in remaining:
            return windows.main(remaining)
        parser.error('--host is required for Windows desktop control from Linux')
    try:
        transport = SSHTransport(options.host, options.windows_root)
    except ValueError as error:
        parser.error(str(error))
    return windows.main(remaining, runner=transport, prog='kilix windows')


if __name__ == '__main__':
    raise SystemExit(main())
