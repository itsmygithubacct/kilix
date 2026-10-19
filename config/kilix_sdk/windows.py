"""Windows desktop automation, separate from Kilix terminal pane control.

``DesktopClient(runner=callback)`` accepts a request-dict -> response-dict
transport, including one supplied by an SSH integration. Without a runner it
loads the packaged native ``desktop_transport.py`` on Windows, lazily. Import,
validation, capabilities and CLI help never connect to a desktop.

``execute`` returns the protocol envelope; ``call`` returns its result and
raises ``DesktopError`` on failure. Neither retries an uncertain operation.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import struct
import subprocess
import sys
import tempfile

SCHEMA = 'kilix.desktop/v1'
DEFAULT_TIMEOUT = 20
MAX_REQUEST_BYTES = 1024 * 1024
MAX_CAPTURE_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 48 * 1024 * 1024  # Base64 PNG plus the response envelope.
_U64 = (1 << 64) - 1
_I32 = (-(1 << 31), (1 << 31) - 1)
_KEYS = frozenset([
    'CTRL', 'SHIFT', 'ALT', 'WIN', 'ENTER', 'TAB', 'ESC', 'SPACE', 'BACKSPACE',
    'DELETE', 'HOME', 'END', 'PAGEUP', 'PAGEDOWN', 'LEFT', 'RIGHT', 'UP', 'DOWN',
    *('F' + str(i) for i in range(1, 13)), *'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
])
_DESCRIPTIONS = {
    'status': 'Report interactive session availability and runtime limitations.',
    'windows': 'List visible top-level Windows windows and full process identities.',
    'find': 'Find windows or UIA elements with conjunctive selectors; report incomplete searches.',
    'wait': 'Wait read-only for present, visible, enabled or closed in one native invocation.',
    'inspect': 'Read a flat UI Automation tree; password values are redacted.',
    'screenshot': 'Save a PNG of the desktop or a target window in physical pixels.',
    'capture': 'Read bounded PNG bytes and integrity metadata (raw protocol/SDK only).',
    'focus': 'Bring the target window to the foreground.',
    'move': 'Move and resize the target using physical desktop coordinates.',
    'minimize': 'Minimize the target window.',
    'maximize': 'Maximize the target window.',
    'restore': 'Restore the target window.',
    'close': 'Request WM_CLOSE; applications may prompt or decline to close.',
    'launch': 'Start literal Windows argv without a shell; report PID/window availability.',
    'type': 'Send Unicode text to the target, which must already be foreground.',
    'key': 'Send a named key or chord to the foreground target.',
    'click': 'Click a physical window-relative point belonging to the foreground target.',
    'scroll': 'Scroll at a physical window-relative point in the foreground target.',
    'invoke': 'Invoke one exact UI Automation runtime ID or uniquely matching selector.',
    'set-value': 'Set a uniquely selected UI Automation element value; password fields are refused.',
}
# Required fields, optional fields. This is also the offline capability inventory.
_FIELDS = {
    'status': ((), ()),
    'windows': ((), ('app', 'title', 'pid')),
    'find': ((), ('app', 'title', 'pid', 'selector', 'depth', 'limit', 'include_hidden')),
    'wait': (('state',), ('app', 'title', 'pid', 'selector', 'element', 'depth', 'limit', 'wait_ms', 'poll_ms')),
    'inspect': ((), ('depth', 'limit')),
    'screenshot': (('path',), ('overwrite',)),
    'capture': ((), ()),
    'focus': ((), ()),
    'move': (('x', 'y', 'width', 'height'), ()),
    'minimize': ((), ()),
    'maximize': ((), ()),
    'restore': ((), ()),
    'close': ((), ()),
    'launch': (('argv',), ('cwd',)),
    'type': (('text',), ()),
    'key': (('keys',), ()),
    'click': (('x', 'y'), ('button', 'count')),
    'scroll': (('x', 'y', 'delta'), ()),
    'invoke': ((), ('element', 'selector', 'depth', 'limit')),
    'set-value': (('text',), ('element', 'selector', 'depth', 'limit')),
}
OPERATIONS = tuple(_FIELDS)
_UNTARGETED = frozenset(('status', 'windows', 'launch'))
_READ_ONLY = frozenset(('status', 'windows', 'find', 'wait', 'inspect', 'screenshot', 'capture'))
_WINDOW_FILTERS = ('app', 'title', 'pid')
_ELEMENT_FILTERS = ('name', 'automation_id', 'control_type')
_WAIT_STATES = ('present', 'visible', 'enabled', 'closed')


class DesktopError(RuntimeError):
    """A local, transport or native error with a machine-readable ``code``."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class RequestError(DesktopError, ValueError):
    """An invalid request, rejected before the transport is called."""

    def __init__(self, message):
        super().__init__('invalid_request', message)


def _object(value, required, optional, label):
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise RequestError(f'{label} must be an object with string field names')
    unknown = value.keys() - set(required) - set(optional)
    if unknown:
        raise RequestError(f'{label} has unknown fields: {", ".join(sorted(unknown))}')
    missing = set(required) - value.keys()
    if missing:
        raise RequestError(f'{label} is missing: {", ".join(sorted(missing))}')


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise RequestError(f'{label} must be an integer from {low} to {high}')
    return value


def _text(value, maximum, label, *, empty=True):
    if not isinstance(value, str) or (not empty and not value) or '\0' in value:
        raise RequestError(f'{label} must be a {"nonempty " if not empty else ""}string without NUL')
    try:
        length = len(value.encode('utf-16-le')) // 2
    except UnicodeEncodeError as error:
        raise RequestError(f'{label} must contain valid Unicode') from error
    if length > maximum:
        raise RequestError(f'{label} exceeds {maximum} UTF-16 code units')
    return value


def _decimal(value, label):
    if (not isinstance(value, str) or len(value) > 20
            or re.fullmatch(r'[0-9]+', value) is None or not 0 < int(value) <= _U64):
        raise RequestError(f'{label} must be a positive decimal string within uint64')
    return str(int(value))


def validate_target(target):
    """Validate and copy an HWND/PID/process-creation-FILETIME identity."""
    _object(target, ('hwnd', 'pid', 'started'), (), 'target')
    return {'hwnd': _decimal(target['hwnd'], 'target.hwnd'),
            'pid': _integer(target['pid'], 1, (1 << 32) - 1, 'target.pid'),
            'started': _decimal(target['started'], 'target.started')}


def _windows_path(value, label):
    _text(value, 32760, label, empty=False)
    drive = bool(re.match(r'^[A-Za-z]:[\\/]', value))
    unc = bool(re.match(r'^\\\\[^\\/:*?"<>|]+\\[^\\/:*?"<>|]+[\\/]', value))
    if (not (drive or unc) or value.startswith(('\\\\.\\', '\\\\?\\'))
            or any(c in value for c in '*?"<>|') or ':' in value[2 if drive else 0:]
            or any(ord(c) < 32 for c in value)):
        raise RequestError(f'{label} must be an absolute Windows filesystem path')
    return value


def _quoted_argument(value):
    # Match the backend's always-quoted CRT argv encoding for its length check.
    result, slashes = ['"'], 0
    for char in value:
        if char == '\\':
            slashes += 1
            continue
        result.append('\\' * (slashes * 2 + 1 if char == '"' else slashes))
        result.append(char)
        slashes = 0
    result.extend(('\\' * (slashes * 2), '"'))
    return ''.join(result)


def _window_filters(params):
    if 'title' in params:
        _text(params['title'], 32767, 'title')
    if 'pid' in params:
        _integer(params['pid'], 1, (1 << 32) - 1, 'pid')
    if 'app' in params:
        app = _text(params['app'], 32760, 'app', empty=False)
        if '\\' in app or '/' in app or ':' in app:
            _windows_path(app, 'app')
            if app.replace('/', '\\').rsplit('\\', 1)[-1] in ('', '.', '..'):
                raise RequestError('app must name an executable, not a directory')
        elif app in ('.', '..') or any(c in app for c in '*?"<>|') or any(ord(c) < 32 for c in app):
            raise RequestError('app must be an exact executable basename or absolute Windows path')


def _element_selector(selector):
    _object(selector, (), _ELEMENT_FILTERS, 'selector')
    if not selector:
        raise RequestError('selector needs name, automation_id or control_type')
    for key, value in selector.items():
        _text(value, 32767 if key != 'control_type' else 128, 'selector.' + key)
    if 'control_type' in selector and re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', selector['control_type']) is None:
        raise RequestError('control_type must use the exact UIA name, e.g. Button or Edit')


def _element_id(value):
    _text(value, 2048, 'params.element', empty=False)
    parts = value.split('.')
    if len(parts) > 128 or any(re.fullmatch(r'0|-?[1-9][0-9]*', p) is None
                              or not _I32[0] <= int(p) <= _I32[1] for p in parts):
        raise RequestError('params.element must be a runtime ID returned by inspect/find')


def _search_limits(params, *, action=False):
    if not action:
        params.setdefault('depth', 32)
        params.setdefault('limit', 2000)
    _integer(params.get('depth', 32), 0, 64, 'params.depth')
    _integer(params.get('limit', 20000), 1, 20000, 'params.limit')


def validate_request(request):
    """Return a validated, independent request with explicit timeout/defaults.

    Unknown fields, booleans in numeric fields, incomplete identities, invalid
    Unicode and native argument/coordinate limits fail before any I/O.
    """
    _object(request, ('schema', 'operation', 'params'), ('target', 'timeout'), 'request')
    if request['schema'] != SCHEMA:
        raise RequestError(f'schema must be {SCHEMA}')
    operation = request['operation']
    if not isinstance(operation, str) or operation not in _FIELDS:
        raise RequestError('operation must be one of: ' + ', '.join(OPERATIONS))
    timeout = _integer(request.get('timeout', DEFAULT_TIMEOUT), 1, 60, 'timeout')
    required, optional = _FIELDS[operation]
    params = request['params']
    _object(params, required, optional, 'params')
    result = {'schema': SCHEMA, 'operation': operation, 'params': copy.deepcopy(params), 'timeout': timeout}
    params = result['params']
    if 'target' in request:
        if operation in _UNTARGETED:
            raise RequestError(f'{operation} does not accept target')
        result['target'] = validate_target(request['target'])
    elif operation not in _UNTARGETED and operation not in ('screenshot', 'capture', 'find', 'wait'):
        raise RequestError(f'{operation} requires target with hwnd, pid and started from windows/launch')

    if operation in ('windows', 'find', 'wait'):
        _window_filters(params)
    if operation in ('find', 'wait'):
        window_filter = any(key in params for key in _WINDOW_FILTERS)
        if 'target' in result and window_filter:
            raise RequestError('use either target or app/title/pid filters, not both')
        element_search = 'selector' in params or 'element' in params
        if 'element' in params and 'selector' in params:
            raise RequestError('use either element or selector, not both')
        if 'selector' in params:
            _element_selector(params['selector'])
        if 'element' in params:
            _element_id(params['element'])
            if 'target' not in result:
                raise RequestError('a runtime element ID requires an exact target')
        if operation == 'wait' or element_search:
            if 'target' not in result and not window_filter:
                raise RequestError('wait and element searches need target or app/title/pid filters')
        if operation == 'find' and 'target' in result and not element_search:
            raise RequestError('find with target needs an element selector; use inspect for the tree')
        if element_search:
            _search_limits(params)
        elif any(key in params for key in ('depth', 'limit')):
            raise RequestError('depth and limit apply only to UIA element searches')
        if 'include_hidden' in params and (element_search or type(params['include_hidden']) is not bool):
            raise RequestError('include_hidden is a boolean for window searches only')
        if operation == 'wait':
            if params['state'] not in _WAIT_STATES:
                raise RequestError('state must be present, visible, enabled or closed')
            params.setdefault('wait_ms', min(10000, (timeout - 1) * 1000))
            params.setdefault('poll_ms', 100)
            _integer(params['wait_ms'], 0, (timeout - 1) * 1000, 'params.wait_ms')
            _integer(params['poll_ms'], 20, 1000, 'params.poll_ms')
    elif operation == 'inspect':
        params.setdefault('depth', 3)
        params.setdefault('limit', 200)
        _integer(params['depth'], 0, 8, 'params.depth')
        _integer(params['limit'], 1, 500, 'params.limit')
    elif operation == 'screenshot':
        path = _windows_path(params['path'], 'params.path')
        if PureWindowsPath(path).suffix.lower() != '.png' or path.endswith(('\\', '/')):
            raise RequestError('params.path must name an absolute Windows PNG file')
        params.setdefault('overwrite', False)
        if type(params['overwrite']) is not bool:
            raise RequestError('params.overwrite must be a boolean')
    elif operation in ('move', 'click', 'scroll'):
        for field in ('x', 'y'):
            _integer(params[field], _I32[0] if operation == 'move' else 0, _I32[1], 'params.' + field)
        if operation == 'move':
            for field in ('width', 'height'):
                _integer(params[field], 1, 16384, 'params.' + field)
            if params['x'] + params['width'] > _I32[1] or params['y'] + params['height'] > _I32[1]:
                raise RequestError('window rectangle exceeds the signed coordinate range')
        elif operation == 'click':
            params.setdefault('button', 'left')
            params.setdefault('count', 1)
            if params['button'] not in ('left', 'right', 'middle'):
                raise RequestError('params.button must be left, right or middle')
            _integer(params['count'], 1, 2, 'params.count')
        else:
            _integer(params['delta'], -1200, 1200, 'params.delta')
    elif operation == 'launch':
        argv = params['argv']
        if not isinstance(argv, list) or not 1 <= len(argv) <= 64:
            raise RequestError('params.argv must be a list of 1..64 literal arguments')
        for index, arg in enumerate(argv):
            _text(arg, 32760, f'params.argv[{index}]', empty=index != 0)
        if '"' in argv[0] or argv[0] != argv[0].strip() or argv[0].lower().endswith(('.cmd', '.bat')):
            raise RequestError('params.argv[0] must name an executable without shell quoting')
        # CreateProcessW has a 32767 UTF-16-unit limit including the terminator.
        _text(' '.join(_quoted_argument(arg) for arg in argv), 32766, 'quoted Windows command line')
        if 'cwd' in params:
            _windows_path(params['cwd'], 'params.cwd')
    elif operation == 'key':
        keys = params['keys']
        if not isinstance(keys, list) or not 1 <= len(keys) <= 16:
            raise RequestError('params.keys must be a list of 1..16 supported key names')
        if any(not isinstance(key, str) or key not in _KEYS for key in keys):
            raise RequestError('unsupported key; use capabilities for the supported key names')
        if len(set(keys)) != len(keys):
            raise RequestError('params.keys must not contain duplicate keys')
    if operation in ('type', 'set-value'):
        _text(params['text'], 16384, 'params.text')
    if operation in ('invoke', 'set-value'):
        if ('element' in params) == ('selector' in params):
            raise RequestError('invoke/set-value require exactly one of element or selector')
        if 'element' in params:
            _element_id(params['element'])
        else:
            _element_selector(params['selector'])
        _search_limits(params, action=True)
    return result


def capabilities():
    """Offline contract metadata; this does not assert desktop availability."""
    return {
        'schema': SCHEMA, 'scope': 'windows-desktop', 'offline': True,
        'operations': {
            name: {'description': _DESCRIPTIONS[name], 'mutates': name not in _READ_ONLY,
                   'target': 'none' if name in _UNTARGETED else 'optional' if name in ('screenshot', 'capture', 'find', 'wait') else 'required',
                   'required_params': list(fields[0]), 'optional_params': list(fields[1])}
            for name, fields in _FIELDS.items()
        },
        'target': {'hwnd': 'decimal uint64 string', 'pid': 'positive uint32',
                   'started': 'process creation FILETIME, decimal uint64 string'},
        'selectors': {'window': 'app: exact basename/path ignoring case; title: substring ignoring case; pid: exact; all conjunctive',
                      'element': 'name, automation_id, control_type: exact case-sensitive conjunctive strings',
                      'actions': 'resolve one complete window search to full identity; never choose among multiple matches'},
        'wait': {'states': list(_WAIT_STATES), 'wait_ms_default': 'min(10000, (timeout - 1) * 1000)',
                 'wait_ms_max': '(timeout - 1) * 1000', 'poll_ms': [20, 1000], 'poll_ms_default': 100,
                 'timeout_code': 'wait_timeout', 'identity': 'bind first unique window; never rebind after disappearance'},
        'limits': {'timeout': {'default': 20, 'min': 1, 'max': 60},
                   'inspect_depth': {'default': 3, 'min': 0, 'max': 8},
                   'inspect_limit': {'default': 200, 'min': 1, 'max': 500},
                   'search_depth': {'default': 32, 'min': 0, 'max': 64},
                   'search_limit': {'default': 2000, 'action_default': 20000, 'min': 1, 'max': 20000},
                   'text_utf16_units': 16384, 'argv_count': 64, 'key_count': 16,
                   'element_id_utf16_units': 2048, 'move_size': [1, 16384],
                   'wheel_delta': [-1200, 1200], 'capture_png_bytes': MAX_CAPTURE_BYTES,
                   'response_bytes': MAX_RESPONSE_BYTES, 'capture_dimension': 16384,
                   'capture_pixels': 67108864},
        'keys': sorted(_KEYS), 'click_buttons': ['left', 'right', 'middle'],
        'coordinates': 'physical pixels; click/scroll relative to the window, move relative to the desktop',
        'screenshot': {'format': 'PNG', 'path': 'absolute Windows path', 'overwrite_default': False,
                       'local': '--local PATH writes on the caller; relative paths are allowed',
                       'wsl': 'positional Linux paths (including /mnt/c) write in WSL; Windows paths stay native',
                       'sdk': 'DesktopClient.capture_to_local(path, target=None, overwrite=False, timeout=20)'},
        'capture': {'protocol_only': True, 'encoding': 'base64', 'format': 'PNG',
                    'result_fields': ['png_base64', 'byte_length', 'sha256', 'bounds', 'capture', 'occlusion_possible'],
                    'persistent_remote_file': False},
        'transport': 'own-user interactive scheduled task; no server or automatic retry',
        'limitations': [
            'status reports actual session, user, interactive and locked availability',
            'input requires the exact target to be foreground; points must belong to the target',
            'elevated UI, UAC secure desktop and locked desktops can block access',
            'UI Automation depends on application providers; stale/ambiguous IDs are refused',
            'password values are never read or exposed; set-value refuses password fields',
            'launch reports process/window availability, not application task completion',
            'close sends WM_CLOSE only; applications may prompt or remain open',
            'no login or RDP session management',
        ],
    }


def _validate_response(response, operation):
    if (not isinstance(response, dict) or response.get('schema') != SCHEMA
            or response.get('operation') != operation or type(response.get('ok')) is not bool):
        raise DesktopError('invalid_response', 'Desktop transport returned an invalid response envelope')
    if response['ok']:
        valid = 'result' in response and 'error' not in response
    else:
        error = response.get('error')
        valid = (isinstance(error, dict) and isinstance(error.get('code'), str)
                 and bool(error['code']) and isinstance(error.get('message'), str) and 'result' not in response)
    if not valid:
        raise DesktopError('invalid_response', 'Desktop transport returned an incomplete response')
    try:
        json.dumps(response, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise DesktopError('invalid_response', 'Desktop response is not valid JSON') from error
    return response


def _local_capture_path(path, overwrite):
    if type(overwrite) is not bool:
        raise RequestError('overwrite must be a boolean')
    try:
        name = os.fspath(path)
    except TypeError as error:
        raise RequestError('local destination must be a path') from error
    if (not isinstance(name, str) or not name or any(ord(c) < 32 for c in name)
            or name.endswith(('/', '\\')) or Path(name).suffix.lower() != '.png'):
        raise RequestError('local destination must name a PNG file')
    try:
        destination = Path(os.path.abspath(os.path.expanduser(name)))
        if os.name == 'nt':
            _windows_path(str(destination), 'local destination')
        # Resolve the directory only: never follow a destination symlink.
        destination = destination.parent.resolve(strict=True) / destination.name
        if not destination.parent.is_dir():
            raise DesktopError('invalid_path', 'Local screenshot parent is not a directory')
        try:
            mode = destination.lstat().st_mode
        except FileNotFoundError:
            pass
        else:
            if not overwrite:
                raise DesktopError('file_exists', 'Local screenshot already exists; use --overwrite to replace it')
            if not stat.S_ISREG(mode):
                raise DesktopError('invalid_path', 'Local screenshot destination must be a regular file, not a symlink or directory')
        return destination
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(error, DesktopError):
            raise
        raise DesktopError('invalid_path', 'Local screenshot directory is missing or inaccessible') from error


def _capture_payload(result):
    """Check untrusted transport data before exposing a file or metadata."""
    def invalid():
        return DesktopError('invalid_response', 'Invalid PNG capture payload or integrity metadata')

    if not isinstance(result, dict):
        raise invalid()
    length, encoded, digest = (result.get(key) for key in ('byte_length', 'png_base64', 'sha256'))
    bounds = result.get('bounds')
    if (type(length) is not int or not 33 <= length <= MAX_CAPTURE_BYTES
            or not isinstance(encoded, str) or len(encoded) != 4 * ((length + 2) // 3)
            or not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None
            or not isinstance(bounds, dict) or set(bounds) != {'x', 'y', 'width', 'height'}
            or any(type(bounds[k]) is not int for k in bounds)
            or any(not _I32[0] <= bounds[k] <= _I32[1] for k in ('x', 'y'))
            or any(not 1 <= bounds[k] <= 16384 for k in ('width', 'height'))
            or bounds['width'] * bounds['height'] > 67108864
            or bounds['x'] + bounds['width'] > _I32[1] or bounds['y'] + bounds['height'] > _I32[1]
            or result.get('capture') != 'screen' or result.get('occlusion_possible') is not True):
        raise invalid()
    try:
        png = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise invalid() from error
    if (len(png) != length or hashlib.sha256(png).hexdigest() != digest
            or png[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
            or struct.unpack('>II', png[16:24]) != (bounds['width'], bounds['height'])
            or binascii.crc32(png[12:29]) != struct.unpack('>I', png[29:33])[0]):
        raise invalid()
    return png, {'byte_length': length, 'sha256': digest, 'bounds': dict(bounds),
                 'capture': 'screen', 'occlusion_possible': True}


def _native_execute(request, root):
    if sys.platform != 'win32':
        raise DesktopError('unsupported_platform',
                           'Windows desktop control needs native Windows, the WSL companion, or a runner callback.')
    if root is None:
        # Packaged SDK: ROOT/wsl-bin/config/kilix_sdk/windows.py. The entry
        # script path also supports embedding the SDK next to native-commands.py.
        candidates = [Path(sys.argv[0]).resolve().parent, Path(__file__).resolve().parents[3]]
        root = next((p for p in candidates if (p / 'desktop_transport.py').is_file()), candidates[-1])
    path = Path(root).resolve() / 'desktop_transport.py'
    if not path.is_file():
        raise DesktopError('transport_unavailable', 'Packaged desktop_transport.py is missing; install the complete package.')
    # Load the exact packaged file, never an ambient module with the same name.
    spec = importlib.util.spec_from_file_location('desktop_transport', path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get('desktop_transport')
    sys.modules['desktop_transport'] = module
    try:
        spec.loader.exec_module(module)
        executor = getattr(module, 'execute', None)
        if not callable(executor):
            raise DesktopError('transport_unavailable', 'Packaged desktop_transport.py does not export execute(request).')
        return executor(request)
    finally:
        if previous is None:
            sys.modules.pop('desktop_transport', None)
        else:
            sys.modules['desktop_transport'] = previous


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RequestError('JSON contains duplicate field names')
        value[key] = item
    return value


def _bad_constant(_value):
    raise RequestError('JSON must not contain NaN or Infinity')


def _load_json(value):
    try:
        if isinstance(value, bytes):
            value = value.decode('utf-8-sig')
        return json.loads(value, object_pairs_hook=_unique_object, parse_constant=_bad_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise RequestError('Expected valid UTF-8 JSON without duplicate fields or nonfinite numbers') from error


def cli_runner(command, *, run=None):
    """Build a request runner for a CLI prefix such as [kilix_cli, 'desktop'].

    A caller can supply an SSH command prefix. Only fixed protocol flags appear
    on its command line; all user data travels as UTF-8 JSON on stdin. ``run``
    has the subprocess.run signature, for embedding and transport-only tests.
    """
    if not isinstance(command, (list, tuple)) or not command:
        raise ValueError('command must be a nonempty argv sequence')
    argv = [_text(arg, 32767, 'command argument', empty=False) for arg in command]

    def send(request):
        request = validate_request(request)
        completed = (run or subprocess.run)(
            [*argv, '--json', '--request-json', '-'],
            input=json.dumps(request, ensure_ascii=True, allow_nan=False).encode('utf-8'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=request['timeout'] + 10,
        )
        try:
            output_size = len(completed.stdout.encode('utf-8') if isinstance(completed.stdout, str) else completed.stdout)
            if output_size > MAX_RESPONSE_BYTES:
                raise DesktopError('transport_error', 'Desktop response exceeds 48 MiB')
            response = _validate_response(_load_json(completed.stdout), request['operation'])
        except (RequestError, DesktopError) as error:
            raise DesktopError('transport_error',
                               f'Desktop CLI returned no valid response (exit {completed.returncode})') from error
        if completed.returncode and response['ok']:
            raise DesktopError('transport_error', f'Desktop CLI failed with exit {completed.returncode}')
        return response

    return send


class DesktopClient:
    """Reusable validated client. ``runner`` receives an independent dict."""

    def __init__(self, runner=None, *, root=None):
        self.runner = runner
        self.root = root

    def execute(self, request):
        request = validate_request(request)
        operation = request['operation']
        try:
            response = self.runner(request) if self.runner is not None else _native_execute(request, self.root)
        except DesktopError:
            raise
        except (subprocess.TimeoutExpired, TimeoutError) as error:
            raise DesktopError('timeout', 'Desktop transport timed out; the operation was not retried.') from error
        except (OSError, ImportError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise DesktopError('transport_error', str(error)) from error
        return _validate_response(response, operation)

    def _request(self, operation, *, params=None, target=None, timeout=DEFAULT_TIMEOUT,
                 window=None, app=None, title=None, pid=None):
        request = {'schema': SCHEMA, 'operation': operation, 'params': {} if params is None else params,
                   'timeout': timeout}
        filters = {key: value for key, value in (('app', app), ('title', title), ('pid', pid)) if value is not None}
        _window_filters(filters)
        if target is not None and (window is not None or filters):
            raise RequestError('use an exact target or window selectors, not both')
        if window is not None and filters:
            raise RequestError('--window cannot be combined with app/title/pid')
        if target is not None:
            request['target'] = target
        if operation in ('windows', 'find', 'wait') and filters:
            if not isinstance(request['params'], dict) or any(key in request['params'] for key in filters):
                raise RequestError('window filters must be supplied only once')
            request['params'] = {**request['params'], **filters}
            filters = {}
        if window is not None or filters:
            # Validate the entire action before even the read used to resolve it.
            request['target'] = {'hwnd': _decimal(window, 'window') if window is not None else '1',
                                 'pid': 1, 'started': '1'}
            request = validate_request(request)
            request['target'] = self.resolve_window(window, timeout=timeout, **filters)
        return validate_request(request)

    def call(self, operation, *, params=None, target=None, timeout=DEFAULT_TIMEOUT,
             window=None, app=None, title=None, pid=None):
        """Call once; selectors resolve uniquely before an action is transported."""
        request = self._request(operation, params=params, target=target, timeout=timeout,
                                window=window, app=app, title=title, pid=pid)
        response = self.execute(request)
        if not response['ok']:
            raise DesktopError(response['error']['code'], response['error']['message'])
        return response['result']

    def capture_to_local(self, path, *, target=None, overwrite=False, timeout=DEFAULT_TIMEOUT):
        """Save a verified PNG atomically on this caller; return metadata only.

        The parent directory must exist. Existing files require overwrite=True;
        symlinks and special files are refused. No remote screenshot file is
        created and failed operations are not retried. Metadata includes path,
        byte_length, sha256 and physical bounds (x, y, width, height).
        """
        request = {'schema': SCHEMA, 'operation': 'capture', 'params': {}, 'timeout': timeout}
        if target is not None:
            request['target'] = target
        request = validate_request(request)
        destination = _local_capture_path(path, overwrite)
        temporary = None
        try:
            # Probe actual directory writability before contacting the desktop.
            fd, temporary = tempfile.mkstemp(prefix='.kilix-capture-', suffix='.tmp', dir=destination.parent)
            with os.fdopen(fd, 'wb') as stream:
                response = self.execute(request)
                if not response['ok']:
                    raise DesktopError(response['error']['code'], response['error']['message'])
                png, metadata = _capture_payload(response['result'])
                stream.write(png)
                stream.flush()
                os.fsync(stream.fileno())
            if overwrite:
                os.replace(temporary, destination)
            elif os.name == 'nt':
                os.rename(temporary, destination)  # Windows rename refuses an existing destination.
            else:
                os.link(temporary, destination)  # Atomic no-clobber, even if a competing writer won.
            return {'path': str(destination), **metadata}
        except FileExistsError as error:
            raise DesktopError('file_exists', 'Local screenshot already exists; it was not overwritten') from error
        except OSError as error:
            raise DesktopError('io_error', 'Could not write the local screenshot: ' + str(error)) from error
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass

    def resolve_window(self, hwnd=None, *, app=None, title=None, pid=None, timeout=DEFAULT_TIMEOUT):
        """Resolve a unique visible window; incomplete/ambiguous searches fail."""
        filters = {key: value for key, value in (('app', app), ('title', title), ('pid', pid)) if value is not None}
        _window_filters(filters)
        if hwnd is not None:
            hwnd = _decimal(hwnd, 'window')
            if filters:
                raise RequestError('use HWND or app/title/pid, not both')
        elif not filters:
            raise RequestError('resolve_window needs HWND or app/title/pid')
        result = self.call('windows' if hwnd is not None else 'find', params=filters, timeout=timeout)
        if hwnd is None and (not isinstance(result, dict)
                             or type(result.get('skipped')) is not int or result['skipped'] < 0
                             or type(result.get('incomplete')) is not bool):
            raise DesktopError('invalid_response', 'find response is missing valid search-completeness metadata')
        rows = result.get('windows') if isinstance(result, dict) else result
        if not isinstance(rows, list):
            raise DesktopError('invalid_response', 'windows response has no window list')
        matches = []
        for row in rows:
            target = row.get('target') if isinstance(row, dict) else None
            if not isinstance(target, dict):
                raise DesktopError('invalid_response', 'window search returned a row without target identity')
            try:
                target = validate_target(target)
            except RequestError as error:
                raise DesktopError('invalid_response', 'window search returned an invalid identity') from error
            if hwnd is None or target['hwnd'] == hwnd:
                matches.append(target)
        if len(matches) > 1:
            raise DesktopError('ambiguous_target', 'More than one window matched; narrow app/title/pid. No action was sent.')
        if isinstance(result, dict) and (result.get('skipped', 0) or result.get('incomplete') or result.get('truncated')):
            raise DesktopError('search_incomplete', 'Window search was incomplete; uniqueness/absence is unknown. No action was sent.')
        if not matches:
            raise DesktopError('window_not_found', 'No visible window matched; run desktop find again.')
        return matches[0]

    def find_windows(self, *, app=None, title=None, pid=None, include_hidden=False, timeout=DEFAULT_TIMEOUT):
        return self.call('find', app=app, title=title, pid=pid,
                         params={'include_hidden': include_hidden}, timeout=timeout)

    def find_elements(self, *, target=None, app=None, title=None, pid=None,
                      name=None, automation_id=None, control_type=None, depth=32, limit=2000, timeout=DEFAULT_TIMEOUT):
        selector = {key: value for key, value in (('name', name), ('automation_id', automation_id),
                                                ('control_type', control_type)) if value is not None}
        return self.call('find', target=target, app=app, title=title, pid=pid, timeout=timeout,
                         params={'selector': selector, 'depth': depth, 'limit': limit})

    def wait(self, state, *, target=None, app=None, title=None, pid=None, element=None,
             name=None, automation_id=None, control_type=None, wait_ms=None, poll_ms=100,
             depth=32, limit=2000, timeout=DEFAULT_TIMEOUT):
        """Poll natively in one request; raise wait_timeout if not confirmed."""
        params = {'state': state, 'poll_ms': poll_ms}
        selector = {key: value for key, value in (('name', name), ('automation_id', automation_id),
                                                ('control_type', control_type)) if value is not None}
        if selector:
            params['selector'] = selector
        if element is not None:
            params['element'] = element
        if selector or element is not None:
            params.update(depth=depth, limit=limit)
        elif depth != 32 or limit != 2000:
            raise RequestError('depth/limit apply only to UIA controls')
        if wait_ms is not None:
            params['wait_ms'] = wait_ms
        return self.call('wait', params=params, target=target, app=app, title=title, pid=pid, timeout=timeout)


def execute(request, *, runner=None, root=None):
    """Validate and execute one request; return the response envelope."""
    return DesktopClient(runner=runner, root=root).execute(request)


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('allow_abbrev', False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        raise RequestError(message)


def _parser(prog):
    common = _Parser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument('--json', action='store_true', help='emit the JSON response envelope')
    common.add_argument('--timeout', type=int, metavar='SECONDS', help='request timeout, 1..60 (default 20)')
    parser = _Parser(prog=prog, parents=[common], description='Control Windows desktop applications, separate from terminal panes.',
                     epilog='Help and capabilities are offline. Use OPERATION --help for usage. No login/RDP management.')
    parser.add_argument('--request-json', choices=('-',), help='read one full kilix.desktop/v1 request as UTF-8 JSON on stdin')
    parser.add_argument('--capabilities', action='store_true', help='show the offline operation/limit inventory')
    subs = parser.add_subparsers(dest='operation')
    subs.add_parser('capabilities', parents=[common], help='offline operations, parameters and limits',
                    description='Describe the contract without loading the native transport or inspecting any session.')
    for operation in OPERATIONS:
        if operation == 'capture':
            continue  # Byte payloads are only exposed through the explicit raw protocol/SDK.
        sub = subs.add_parser(operation, parents=[common], help=_DESCRIPTIONS[operation], description=_DESCRIPTIONS[operation])
        if operation not in _UNTARGETED:
            sub.add_argument('--hwnd', metavar='DECIMAL', help='exact window handle from windows/launch')
            sub.add_argument('--started', metavar='FILETIME', help='process creation time; requires --hwnd and --pid')
            sub.add_argument('--window', metavar='HWND', help='resolve this exact decimal HWND to a fresh full identity')
        if operation not in ('status', 'launch'):
            sub.add_argument('--app', help='exact executable basename or absolute Windows path, ignoring case')
            sub.add_argument('--title', help='case-insensitive title substring; combines with app/PID')
            sub.add_argument('--pid', type=int, help='PID filter, or exact identity with --hwnd and --started')
        if operation in ('find', 'wait', 'invoke', 'set-value'):
            sub.add_argument('--name', help='exact case-sensitive UIA name')
            sub.add_argument('--automation-id', help='exact case-sensitive UIA automation ID')
            sub.add_argument('--control-type', help='exact UIA control type, e.g. Button or Edit')
            sub.add_argument('--depth', type=int, help='UIA search depth, 0..64 (default 32)')
            sub.add_argument('--limit', type=int, help='UIA nodes searched, 1..20000 (default 2000; actions 20000)')
        if operation == 'find':
            sub.add_argument('--include-hidden', action='store_true', default=None, help='include hidden top-level windows')
        elif operation == 'wait':
            sub.add_argument('--for', dest='state', required=True, choices=_WAIT_STATES, help='condition to confirm')
            sub.add_argument('--element', help='exact runtime ID; requires exact target identity')
            sub.add_argument('--wait-ms', type=int, help='condition deadline in ms; at most (timeout - 1) * 1000')
            sub.add_argument('--poll-ms', type=int, help='native poll interval, 20..1000 ms (default 100)')
        elif operation == 'inspect':
            sub.add_argument('--depth', type=int, help='UIA tree depth, 0..8 (default 3)')
            sub.add_argument('--limit', type=int, help='maximum nodes, 1..500 (default 200)')
        elif operation == 'screenshot':
            sub.add_argument('path', nargs='?', help='absolute Windows PNG path; in WSL a Linux path writes in WSL')
            sub.add_argument('--local', metavar='PATH', help='save a verified PNG on the caller (relative or absolute path); omit target for desktop')
            sub.add_argument('--overwrite', action='store_true', help='allow replacing the requested PNG file')
        elif operation in ('move', 'click', 'scroll'):
            for field in ('x', 'y'):
                sub.add_argument('--' + field, type=int, required=True,
                                 help='physical desktop coordinate' if operation == 'move' else 'physical window-relative coordinate')
            if operation == 'move':
                for field in ('width', 'height'):
                    sub.add_argument('--' + field, type=int, required=True, help='physical size, 1..16384 pixels')
            elif operation == 'click':
                sub.add_argument('--button', choices=('left', 'right', 'middle'), help='mouse button (default left)')
                sub.add_argument('--count', type=int, choices=(1, 2), help='click count (default 1)')
            else:
                sub.add_argument('--delta', type=int, required=True, help='wheel delta, -1200..1200; 120 is one upward detent')
        elif operation == 'launch':
            sub.add_argument('--cwd', help='absolute Windows working directory')
            sub.add_argument('argv', nargs=argparse.REMAINDER, help='PROGRAM [ARGS]; use -- before literal argv with options')
        elif operation == 'type':
            sub.add_argument('text', help='literal Unicode text, at most 16384 UTF-16 code units; use -- before leading dashes')
        elif operation == 'key':
            sub.add_argument('keys', nargs='+', help='named chord, e.g. CTRL A, CTRL+A or ENTER; see capabilities')
        elif operation in ('invoke', 'set-value'):
            sub.add_argument('element', nargs='?', help='exact runtime ID from inspect/find; omit when using UIA selector flags')
            if operation == 'set-value':
                sub.add_argument('text', help='literal value, at most 16384 UTF-16 code units')
    return parser


def _cli_request(ns, client, *, operation=None):
    operation = operation or ns.operation
    params = {name: getattr(ns, name) for name in sum(_FIELDS[operation], ())
              if getattr(ns, name, None) is not None}
    selector = {key: getattr(ns, key) for key in _ELEMENT_FILTERS if getattr(ns, key, None) is not None}
    if selector:
        params['selector'] = selector
    if operation == 'launch' and params['argv'][:1] == ['--']:
        params['argv'] = params['argv'][1:]
    if operation == 'key':
        params['keys'] = [part.upper() for key in params['keys'] for part in key.split('+')]
    target = None
    app, title, pid = (getattr(ns, key, None) for key in _WINDOW_FILTERS)
    for key in _WINDOW_FILTERS:
        params.pop(key, None)
    if operation not in _UNTARGETED:
        fields = {key: getattr(ns, key, None) for key in ('hwnd', 'pid', 'started')}
        if fields['hwnd'] is not None or fields['started'] is not None:
            if any(value is None for value in fields.values()):
                raise RequestError('--hwnd, --pid and --started are all required together')
            target, pid = fields, None
    return client._request(operation, params=params, target=target, window=getattr(ns, 'window', None),
                           app=app, title=title, pid=pid, timeout=getattr(ns, 'timeout', DEFAULT_TIMEOUT))


def _human(value, indent=0):
    prefix = ' ' * indent
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                print(prefix + key + ':')
                _human(item, indent + 2)
            else:
                print(prefix + key + ': ' + json.dumps(item, ensure_ascii=False))
    elif isinstance(value, list):
        for item in value:
            print(prefix + '-')
            _human(item, indent + 2)
    else:
        print(prefix + json.dumps(value, ensure_ascii=False))


def main(argv=None, *, runner=None, root=None, prog='kilix desktop', local_paths=False):
    """CLI entry point; return 0 on success, 1 on execution failure, 2 on usage."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ['help']:
        args = [*args[1:], '--help']
    json_mode = '--json' in args[:args.index('--')] if '--' in args else '--json' in args
    operation = 'unknown'
    try:
        parser = _parser(prog)
        try:
            ns = parser.parse_args(args)
        except SystemExit as help_exit:
            return int(help_exit.code or 0)
        json_mode = getattr(ns, 'json', False)
        operation = ns.operation or 'unknown'
        if ns.request_json is not None:
            if ns.operation or ns.capabilities or hasattr(ns, 'timeout'):
                raise RequestError('--request-json is used alone with optional --json; set timeout in the request')
            stream = getattr(sys.stdin, 'buffer', sys.stdin)
            payload = stream.read(MAX_REQUEST_BYTES + 1)
            if isinstance(payload, str):
                payload = payload.encode('utf-8')
            if len(payload) > MAX_REQUEST_BYTES:
                raise RequestError('request JSON exceeds 1 MiB')
            request = _load_json(payload)
            if isinstance(request, dict) and request.get('operation') in OPERATIONS:
                operation = request['operation']
            response = execute(request, runner=runner, root=root)
        elif ns.capabilities or ns.operation == 'capabilities':
            if ns.capabilities and ns.operation not in (None, 'capabilities'):
                raise RequestError('--capabilities cannot be combined with an operation')
            response = {'schema': SCHEMA, 'ok': True, 'operation': 'capabilities', 'result': capabilities()}
        elif ns.operation is None:
            parser.print_help()
            return 0
        else:
            client = DesktopClient(runner=runner, root=root)
            local = None
            if ns.operation == 'screenshot':
                if (ns.local is None) == (ns.path is None):
                    raise RequestError('screenshot requires either a Windows path or --local PATH')
                local = ns.local
                if local_paths and ns.path is not None and not (re.match(r'^[A-Za-z]:', ns.path) or ns.path.startswith('\\')):
                    local = ns.path
            if local is not None:
                local = _local_capture_path(local, ns.overwrite)
                request = _cli_request(ns, client, operation='capture')
                result = client.capture_to_local(local, target=request.get('target'),
                                                 overwrite=ns.overwrite, timeout=request['timeout'])
                response = {'schema': SCHEMA, 'ok': True, 'operation': 'screenshot', 'result': result}
            else:
                response = client.execute(_cli_request(ns, client))
    except (DesktopError, UnicodeError, OSError) as error:
        response = {'schema': SCHEMA, 'ok': False, 'operation': operation,
                    'error': {'code': getattr(error, 'code', 'invalid_request'), 'message': str(error)}}
    if json_mode:
        print(json.dumps(response, ensure_ascii=True, allow_nan=False))
    elif response['ok']:
        print(response['operation'] + ':')
        _human(response['result'], 2)
    else:
        print(f"{prog}: {response['error']['code']}: {response['error']['message']}", file=sys.stderr)
    return 0 if response['ok'] else 2 if response['error']['code'] == 'invalid_request' else 1


__all__ = ['SCHEMA', 'OPERATIONS', 'DesktopClient', 'DesktopError', 'RequestError',
           'capabilities', 'validate_target', 'validate_request', 'execute', 'cli_runner', 'main']


if __name__ == '__main__':
    raise SystemExit(main())
