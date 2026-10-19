"""Run one desktop request in the calling Windows user's interactive session.

No listener, elevation or persistent service is installed. Timed-out actions
are never retried: input may have reached an application before its reply.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

SCHEMA = 'kilix.desktop/v1'


def failure(operation, code, message):
    return {'schema': SCHEMA, 'ok': False, 'operation': operation,
            'error': {'code': code, 'message': message}}


def execute(request: dict) -> dict:
    operation = request.get('operation', '')
    if os.name != 'nt':
        return failure(operation, 'unsupported_platform', 'Use the native Windows launcher or an SSH Windows host.')
    timeout = request.get('timeout', 20)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 60:
        return failure(operation, 'invalid_request', 'timeout must be between 1 and 60 seconds')
    payload = json.dumps(request, ensure_ascii=True, allow_nan=False)
    if len(payload) > 1048576:
        return failure(operation, 'invalid_request', 'request exceeds 1 MiB')
    script = Path(__file__).with_name('desktop-dispatch.ps1')
    launcher = script.parent / 'kitty/launcher/kitty.exe'
    if not launcher.is_file():
        # Also supports source qualification through the packaged interpreter.
        launcher = Path(sys.executable).with_name('kitty.exe')
    if not launcher.is_file():
        return failure(operation, 'transport_error', 'The bundled graphical Python launcher is missing.')
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run(
            [str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Launcher', str(launcher)],
            input=payload, text=True, encoding='utf-8', capture_output=True,
            timeout=timeout + 20, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return failure(operation, 'timeout', 'Desktop dispatch timed out. The action may have run; inspect before retrying.')
    except OSError as error:
        return failure(operation, 'transport_error', str(error))
    try:
        reply = json.loads(result.stdout.lstrip('\ufeff'))
        if (not isinstance(reply, dict) or reply.get('schema') != SCHEMA
                or type(reply.get('ok')) is not bool or reply.get('operation') != operation):
            raise ValueError('invalid desktop response')
        return reply
    except (ValueError, TypeError):
        detail = result.stderr.strip()[-2000:]
        return failure(operation, 'transport_error', detail or 'The desktop dispatcher did not return a valid response.')
