"""Constrained plain read forms, validated before existing product renderers run."""
import contextlib
import io
import json
import os

import kilix_pty as pty
import kilix_pty_request as request


class Capture(io.TextIOBase):
    def __init__(self):
        self.data = bytearray()

    @property
    def buffer(self):
        return self

    def write(self, value):
        raw = value.encode('utf-8') if isinstance(value, str) else value
        if len(self.data) + len(raw) > 24576:
            raise ValueError('read output exceeds controller bound')
        self.data.extend(raw)
        return len(value)

    def text(self):
        return self.data.decode('utf-8', errors='replace')


def parse(argv):
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 16
            or any(type(x) is not str for x in argv)):
        raise ValueError('invalid read argv')
    argv = list(argv)
    timeout = None
    if argv[:1] == ['--timeout']:
        if len(argv) < 3:
            raise ValueError('missing timeout or read verb')
        timeout = float(argv[1])
        del argv[:2]
    verb = argv.pop(0)
    args, flags, as_json, action = {}, [], False, None
    if verb in ('list', 'reaped', 'capabilities'):
        permitted = {'--json', '--all'} if verb == 'list' else {'--json'}
        if len(set(argv)) != len(argv) or not set(argv) <= permitted:
            raise ValueError('unsupported read option')
        as_json = '--json' in argv
        flags = argv
    elif verb in ('status', 'pane'):
        if '--json' in argv:
            argv.remove('--json')
            as_json = True
        if verb == 'pane' and len(argv) == 1:
            args['pane_id'] = int(argv[0])
        elif verb == 'status' and len(argv) == 2 and argv[0] == '--pane':
            args['pane_id'] = int(argv[1])
        elif verb == 'status' and len(argv) == 1:
            args['id'] = argv[0]
        else:
            raise ValueError('read needs exactly one session or pane')
    elif verb in ('observe', 'journals'):
        if verb == 'journals':
            action = argv.pop(0) if argv and not argv[0].startswith('--') else 'list'
            if action not in ('list', 'show'):
                raise ValueError('only archive list/show are available')
        if verb == 'observe' or action == 'show':
            if not argv:
                raise ValueError('missing read ID')
            args['id'] = argv.pop(0)
        flags = list(argv)
        seen = set()
        while argv:
            flag = argv.pop(0)
            if flag in seen:
                raise ValueError('duplicate read option')
            seen.add(flag)
            if flag == '--json':
                as_json = True
            elif flag == '--text':
                args['text'] = True
            elif flag in ('--lines', '--bytes', '--from') and argv:
                value = argv.pop(0)
                key = {'--lines': 'max_lines', '--bytes': 'max_bytes', '--from': 'from'}[flag]
                args[key] = value if flag == '--from' else int(value)
            elif flag == '--once' and verb == 'observe':
                pass
            else:
                raise ValueError('unsupported read option')
        if verb == 'observe' and '--once' not in seen:
            raise ValueError('constrained observe requires --once')
        if action == 'list' and seen - {'--json'}:
            raise ValueError('archive list accepts only --json')
    else:
        raise ValueError('unsupported constrained read verb')
    document = {'schema': request.REQUEST_SCHEMA,
                'verb': 'list' if verb == 'capabilities' else verb, 'args': args}
    if timeout is not None:
        document['timeout_seconds'] = timeout
    validated = request.validate(json.dumps(document, allow_nan=False).encode())
    return verb, validated, flags, as_json, action


def execute(controller, argv):
    if isinstance(argv, list) and argv and argv[0] in ('marker', 'foreground'):
        if len(argv) != 3 or argv[2] != '--json' or type(argv[1]) is not str:
            raise ValueError('semantic read requires one value and --json')
        import kilix_pty_semantic as semantic
        result = (semantic.marker_occurrences(controller._broker, argv[1]) if argv[0] == 'marker'
                  else semantic.foreground_sample(controller._broker, argv[1]))
        raw = json.dumps(result, separators=(',', ':'), ensure_ascii=True) + '\n'
        if len(raw.encode()) > 24576:
            raise ValueError('semantic result exceeds controller bound')
        return {'code': 0, 'stdout': raw, 'stderr': ''}
    verb, document, flags, as_json, action = parse(argv)
    broker, args = controller._broker, document['args']
    previous_timeout = broker.timeout
    out, err = Capture(), Capture()
    try:
        if 'timeout_seconds' in document:
            broker.timeout = format(document['timeout_seconds'], 'g')
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if 'pane_id' in args:
                code, mapped = controller.read({'schema': request.REQUEST_SCHEMA, 'verb': 'pane',
                                                'args': {'pane_id': args['pane_id']},
                                                **({'timeout_seconds': document['timeout_seconds']}
                                                   if 'timeout_seconds' in document else {})})
                if code:
                    pty.emit(mapped)
                    return {'code': code, 'stdout': out.text(), 'stderr': err.text()}
                if verb == 'pane':
                    pty.emit(mapped) if as_json else print(mapped['id'])
                    return {'code': 0, 'stdout': out.text(), 'stderr': err.text()}
                args = {'id': mapped['id']}
            if verb == 'list':
                code = pty.cmd_list(broker, []) if as_json else pty.cmd_passthrough(broker, ['list', *flags])
            elif verb == 'status':
                code = pty.cmd_status(broker, [args['id']]) if as_json else pty.cmd_passthrough(broker, ['status', args['id']])
            elif verb == 'reaped':
                code = pty.cmd_reaped(broker, []) if as_json else pty.cmd_passthrough(broker, ['reaped'])
            elif verb == 'observe':
                code = pty.cmd_observe(broker, [args['id'], *flags])
            elif verb == 'journals':
                code = pty.cmd_journals(broker, [action, os.path.join(controller._state, 'pty-journals'),
                                                *([args['id']] if 'id' in args else []), *flags])
            else:
                code = pty.cmd_capabilities(broker, flags)
        return {'code': code, 'stdout': out.text(), 'stderr': err.text()}
    finally:
        broker.timeout = previous_timeout
