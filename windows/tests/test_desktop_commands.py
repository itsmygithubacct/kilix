"""Desktop client/CLI checks with fake transports; never touch a live desktop."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

WINDOWS = Path(__file__).resolve().parents[1]
CONFIG = WINDOWS.parent / 'config'
with patch.object(sys, 'path', [str(CONFIG), *sys.path]):
    from kilix_sdk import windows

TARGET = {'hwnd': '123456', 'pid': 4321, 'started': '134000000000000000'}
IDENTITY = ['--hwnd', TARGET['hwnd'], '--pid', str(TARGET['pid']), '--started', TARGET['started']]


def request(operation='status', params=None, target=None, **extra):
    result = {'schema': windows.SCHEMA, 'operation': operation, 'params': {} if params is None else params, **extra}
    if target is not None:
        result['target'] = copy.deepcopy(target)
    return result


def response(operation, result=None, *, error=None):
    value = {'schema': windows.SCHEMA, 'operation': operation, 'ok': error is None}
    value['error' if error else 'result'] = error if error else {} if result is None else result
    return value


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, 'path', [str(CONFIG), str(WINDOWS / 'wsl'), *sys.path]):
        spec.loader.exec_module(module)
    return module


class DesktopValidationTests(unittest.TestCase):
    def test_bad_envelopes_and_identities_never_call_transport(self):
        malformed = [None, [], {}, request(schema='kilix.desktop/v0'),
                     request(operation=[]), request(operation='capabilities'),
                     request(extra=True), {**request(), 'params': None}, request(params={'unknown': 1}),
                     request(timeout=True), request(timeout=1.5), request(timeout=0),
                     request(timeout=61), request(timeout='20'), request(target=TARGET),
                     request('focus'), request('focus', target={'hwnd': '123456'}),
                     request('focus', target={**TARGET, 'extra': 1}),
                     request('focus', target={**TARGET, 'hwnd': 123456}),
                     request('focus', target={**TARGET, 'hwnd': '0x123'}),
                     request('focus', target={**TARGET, 'hwnd': '0'}),
                     request('focus', target={**TARGET, 'started': '1' * 1000}),
                     request('focus', target={**TARGET, 'started': '-1'}),
                     request('focus', target={**TARGET, 'started': 134000000000000000}),
                     request('focus', target={**TARGET, 'pid': True}),
                     request('focus', target={**TARGET, 'pid': 0}),
                     request('focus', target={**TARGET, 'pid': 1 << 32})]
        runner = Mock(side_effect=AssertionError('unexpected transport'))
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(windows.RequestError):
                windows.execute(value, runner=runner)
        runner.assert_not_called()

    def test_native_limits_and_wrong_types_fail_before_transport(self):
        invalid = [
            ('windows', {'pid': False}), ('windows', {'title': 4}),
            ('inspect', {'depth': 9}), ('inspect', {'depth': -1}),
            ('inspect', {'limit': 501}), ('inspect', {'limit': 0}),
            ('inspect', {'limit': True}),
            ('screenshot', {'path': '/tmp/out.png'}),
            ('screenshot', {'path': r'C:out.png'}),
            ('screenshot', {'path': r'\Temp\out.png'}),
            ('screenshot', {'path': r'C:\Temp\out.jpg'}),
            ('screenshot', {'path': 'C:\\Temp\\out.png\\'}),
            ('screenshot', {'path': r'C:\Temp\out.png', 'overwrite': 1}),
            ('screenshot', {'path': r'\\.\pipe\out.png'}),
            ('screenshot', {'path': r'C:\Temp\out:stream.png'}),
            ('screenshot', {'path': r'\\?\C:\Temp\out.png'}),
            ('screenshot', {'path': 'C:\\Temp\\bad\nname.png'}),
            ('move', {'x': 1 << 31, 'y': 1, 'width': 3, 'height': 4}),
            ('move', {'x': 1, 'y': 2, 'width': 0, 'height': 4}),
            ('move', {'x': 1, 'y': 2, 'width': 3, 'height': 16385}),
            ('move', {'x': (1 << 31) - 1, 'y': 0, 'width': 1, 'height': 1}),
            ('click', {'x': -1, 'y': 2}),
            ('click', {'x': 1, 'y': True}),
            ('click', {'x': 1, 'y': 2, 'button': []}),
            ('click', {'x': 1, 'y': 2, 'count': 3}),
            ('scroll', {'x': 1, 'y': 2, 'delta': -1201}),
            ('scroll', {'x': 1, 'y': 2, 'delta': 120.0}),
            ('type', {'text': None}), ('type', {'text': 'a' * 16385}),
            ('type', {'text': '\U0001f600' * 8193}),
            ('type', {'text': 'nul\0text'}), ('type', {'text': '\ud800'}),
            ('key', {'keys': 'CTRL+A'}), ('key', {'keys': []}),
            ('key', {'keys': ['CTRL', 'A', 'A']}), ('key', {'keys': ['F13']}),
            ('key', {'keys': ['CTRL', 65]}), ('key', {'keys': ['a']}),
            ('invoke', {'element': ''}), ('invoke', {'element': 1}),
            ('invoke', {'element': '42:7:9'}), ('invoke', {'element': '42.01'}),
            ('set-value', {'element': '42.1', 'text': 'x' * 16385}),
            ('launch', {'argv': 'notepad.exe'}), ('launch', {'argv': []}),
            ('launch', {'argv': ['']}), ('launch', {'argv': ['app.exe', None]}),
            ('launch', {'argv': ['script.cmd']}), ('launch', {'argv': ['"app.exe"']}),
            ('launch', {'argv': ['app.exe'] * 65}),
            ('launch', {'argv': ['app.exe', 'a' * 20000, 'b' * 20000]}),
            ('launch', {'argv': ['app.exe'], 'cwd': 'relative'}),
            ('launch', {'argv': ['app.exe'], 'shell': True}),
        ]
        runner = Mock(side_effect=AssertionError('unexpected transport'))
        for operation, params in invalid:
            target = None if operation in ('windows', 'launch', 'screenshot') else TARGET
            with self.subTest(operation=operation, params=params), self.assertRaises(windows.RequestError):
                windows.execute(request(operation, params, target), runner=runner)
        runner.assert_not_called()

    def test_literals_defaults_and_copies_preserve_the_callers_request(self):
        argv = ['C:\\Apps with spaces\\Editor.exe', '', 'quote" and slash\\',
                "single'quote", '$(literal)', '`literal`', '%PATH%', 'a&b|c', '\u4e16\u754c \U0001f642', '--help']
        original = request('launch', {'argv': argv, 'cwd': 'C:\\Work with spaces'})
        before = copy.deepcopy(original)

        def send(value):
            self.assertEqual(value['params'], original['params'])
            self.assertEqual(value['timeout'], 20)
            value['params']['argv'].append('runner mutation')
            return response('launch', {'pid': 999, 'target': None})

        result = windows.execute(original, runner=send)
        self.assertEqual(original, before)
        self.assertIsNone(result['result']['target'])
        self.assertEqual(windows.validate_request(request('inspect', target=TARGET))['params'], {'depth': 3, 'limit': 200})
        self.assertEqual(windows.validate_request(request('click', {'x': 2, 'y': 3}, TARGET))['params'],
                         {'x': 2, 'y': 3, 'button': 'left', 'count': 1})
        for path in ('C:\\Temp\\a.png', '\\\\server\\share\\a.PNG'):
            self.assertFalse(windows.validate_request(request('screenshot', {'path': path}))['params']['overwrite'])
        windows.validate_request(request('type', {'text': '\U0001f600' * 8192}, TARGET))
        windows.validate_request(request('move', {'x': -1920, 'y': -300, 'width': 16384, 'height': 1}, TARGET))

    def test_all_chords_and_operations_are_discoverable_without_io(self):
        with patch.object(windows, '_native_execute', side_effect=AssertionError('not offline')):
            inventory = windows.capabilities()
        self.assertEqual(set(inventory['operations']), {
            'status', 'windows', 'inspect', 'screenshot', 'focus', 'move', 'minimize', 'maximize',
            'restore', 'close', 'launch', 'type', 'key', 'click', 'scroll', 'invoke', 'set-value'})
        self.assertTrue(inventory['offline'])
        for key in inventory['keys']:
            windows.validate_request(request('key', {'keys': [key]}, TARGET))
        inventory['operations'].clear()
        self.assertIn('inspect', windows.capabilities()['operations'])

    def test_response_validation_failures_and_timeout_are_never_retried(self):
        for result in ({}, response('focus'), {'schema': windows.SCHEMA, 'operation': 'status', 'ok': 1},
                       {'schema': windows.SCHEMA, 'operation': 'status', 'ok': True},
                       {'schema': windows.SCHEMA, 'operation': 'status', 'ok': False, 'error': {}},
                       response('status', {'number': float('nan')})):
            runner = Mock(return_value=result)
            with self.subTest(result=result), self.assertRaises(windows.DesktopError) as raised:
                windows.execute(request(), runner=runner)
            self.assertEqual(raised.exception.code, 'invalid_response')
            runner.assert_called_once()
        runner = Mock(side_effect=subprocess.TimeoutExpired(['fixture'], 20))
        with self.assertRaises(windows.DesktopError) as raised:
            windows.execute(request(), runner=runner)
        self.assertEqual(raised.exception.code, 'timeout')
        runner.assert_called_once()
        failed = response('focus', error={'code': 'stale_target', 'message': 'Process identity changed'})
        client = windows.DesktopClient(runner=lambda req: failed)
        self.assertEqual(client.execute(request('focus', target=TARGET)), failed)
        with self.assertRaises(windows.DesktopError) as raised:
            client.call('focus', target=TARGET)
        self.assertEqual(raised.exception.code, 'stale_target')

    def test_non_windows_default_fails_without_loading_transport(self):
        with patch.object(sys, 'platform', 'linux'), \
                patch.object(windows.importlib.util, 'spec_from_file_location') as load:
            with self.assertRaises(windows.DesktopError) as raised:
                windows.execute(request())
        self.assertEqual(raised.exception.code, 'unsupported_platform')
        load.assert_not_called()


class DesktopCLITests(unittest.TestCase):
    def run_cli(self, args, *, runner=None, stdin=''):
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.stdout', out), patch('sys.stderr', err), patch('sys.stdin', io.StringIO(stdin)):
            code = windows.main(args, runner=runner)
        return code, out.getvalue(), err.getvalue()

    def test_every_help_and_capabilities_path_stays_offline(self):
        runner = Mock(side_effect=AssertionError('help sent a request'))
        for args in ([], ['--help'], ['help'], ['--capabilities', '--json'],
                     ['capabilities', '--json'], *([op, '--help'] for op in windows.OPERATIONS)):
            with self.subTest(args=args):
                code, out, err = self.run_cli(args, runner=runner)
                self.assertEqual(code, 0, err)
                self.assertTrue(out)
                self.assertEqual(err, '')
        runner.assert_not_called()

    def test_cli_builds_each_operation_and_accepts_json_on_either_side(self):
        examples = [
            ('status', [], {}), ('windows', ['--pid', '4321', '--title', 'Editor'], {'pid': 4321, 'title': 'Editor'}),
            ('inspect', [], {'depth': 3, 'limit': 200}),
            ('screenshot', ['C:\\Temp\\out.png'], {'path': 'C:\\Temp\\out.png', 'overwrite': False}),
            ('focus', [], {}), ('move', ['--x', '-50', '--y', '10', '--width', '800', '--height', '600'],
                                {'x': -50, 'y': 10, 'width': 800, 'height': 600}),
            ('minimize', [], {}), ('maximize', [], {}), ('restore', [], {}), ('close', [], {}),
            ('launch', ['--', 'notepad.exe', 'a b.txt', '--help'], {'argv': ['notepad.exe', 'a b.txt', '--help']}),
            ('type', ['Hello, 世界'], {'text': 'Hello, 世界'}),
            ('key', ['ctrl+a'], {'keys': ['CTRL', 'A']}),
            ('click', ['--x', '8', '--y', '9'], {'x': 8, 'y': 9, 'button': 'left', 'count': 1}),
            ('scroll', ['--x', '8', '--y', '9', '--delta', '-120'], {'x': 8, 'y': 9, 'delta': -120}),
            ('invoke', ['42.1.2'], {'element': '42.1.2'}),
            ('set-value', ['42.1.2', 'literal "value" 世界'], {'element': '42.1.2', 'text': 'literal "value" 世界'}),
        ]
        for op, rest, params in examples:
            for before in (True, False):
                target_args = [] if op in ('status', 'windows', 'launch', 'screenshot') else IDENTITY
                args = (['--json', '--timeout', '30', op] if before else [op, '--json', '--timeout', '30'])
                runner = Mock(return_value=response(op, {'fixture': True}))
                with self.subTest(operation=op, before=before):
                    code, out, err = self.run_cli([*args, *target_args, *rest], runner=runner)
                    self.assertEqual((code, err), (0, ''))
                    self.assertTrue(json.loads(out)['ok'])
                    sent = runner.call_args.args[0]
                    self.assertEqual(sent['params'], params)
                    self.assertEqual(sent['timeout'], 30)
                    if target_args:
                        self.assertEqual(sent['target'], TARGET)
                    else:
                        self.assertNotIn('target', sent)

    def test_partial_targets_bad_options_and_bad_limits_do_not_even_resolve(self):
        examples = [['focus'], ['focus', '--hwnd', '123456'], ['focus', '--pid', '4321'],
                    ['focus', '--window', '123456', *IDENTITY],
                    ['type', '--window', '123456', 'x' * 16385],
                    ['inspect', '--window', '123456', '--depth', '9'],
                    ['windows', '--unknown', 'value'], ['windows', '--time', '3'],
                    ['focus', '--window', '0x123'], ['status', '--timeout', '61']]
        runner = Mock(side_effect=AssertionError('invalid input reached transport'))
        for args in examples:
            with self.subTest(args=args):
                code, out, err = self.run_cli(['--json', *args], runner=runner)
                self.assertEqual(code, 2, (out, err))
                self.assertEqual(json.loads(out)['error']['code'], 'invalid_request')
                self.assertEqual(err, '')
        runner.assert_not_called()

    def test_exact_window_resolution_sends_full_identity_and_honors_stale_failure(self):
        runner = Mock(side_effect=[
            response('windows', {'windows': [
                {'title': 'same title', 'target': {**TARGET, 'hwnd': '654321'}},
                {'title': 'same title', 'target': TARGET}]}),
            response('type', error={'code': 'stale_target', 'message': 'Target exited after listing'}),
        ])
        code, out, err = self.run_cli(['type', '--json', '--window', TARGET['hwnd'], 'literal'], runner=runner)
        self.assertEqual((code, err), (1, ''))
        self.assertEqual(json.loads(out)['error']['code'], 'stale_target')
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(runner.call_args_list[0].args[0]['operation'], 'windows')
        self.assertEqual(runner.call_args.args[0]['target'], TARGET)
        self.assertEqual(runner.call_args.args[0]['params'], {'text': 'literal'})

    def test_missing_and_ambiguous_window_resolutions_send_no_action(self):
        for rows, code in [([], 'window_not_found'), ([{'target': TARGET}] * 2, 'ambiguous_target')]:
            runner = Mock(return_value=response('windows', rows))
            rc, out, _ = self.run_cli(['--json', 'close', '--window', TARGET['hwnd']], runner=runner)
            self.assertEqual(rc, 1)
            self.assertEqual(json.loads(out)['error']['code'], code)
            runner.assert_called_once()

    def test_raw_json_rejects_duplicates_malformed_types_limits_and_mixed_flags(self):
        runner = Mock(side_effect=AssertionError('bad raw request dispatched'))
        examples = ['{', '[]', '{"schema":"a","schema":"b"}',
                    json.dumps(request('focus')), json.dumps(request('status', timeout=True)),
                    json.dumps(request('status', params={'x': float('nan')})),
                    json.dumps(request('status', unknown=True)), ' ' * (windows.MAX_REQUEST_BYTES + 1)]
        for source in examples:
            with self.subTest(source=source[:100]):
                code, out, err = self.run_cli(['--json', '--request-json', '-'], runner=runner, stdin=source)
                self.assertEqual((code, err), (2, ''))
                self.assertEqual(json.loads(out)['error']['code'], 'invalid_request')
        for extra in (['status'], ['--timeout', '10'], ['--capabilities']):
            code, out, _ = self.run_cli(['--json', '--request-json', '-', *extra], runner=runner, stdin=json.dumps(request()))
            self.assertEqual(code, 2, out)
        runner.assert_not_called()

    def test_raw_unicode_request_and_leading_dash_literals(self):
        value = request('set-value', {'element': '42.7.9', 'text': '"quoted" 世界\n$(literal)\\'}, TARGET, timeout=60)
        runner = Mock(return_value=response('set-value', {'accepted': True}))
        code, out, _ = self.run_cli(['--request-json', '-', '--json'], runner=runner,
                                    stdin=json.dumps(value, ensure_ascii=False))
        self.assertEqual(code, 0, out)
        self.assertEqual(runner.call_args.args[0], value)
        runner = Mock(return_value=response('type'))
        code, out, _ = self.run_cli(['type', '--json', *IDENTITY, '--', '--help'], runner=runner)
        self.assertEqual(code, 0, out)
        self.assertEqual(runner.call_args.args[0]['params'], {'text': '--help'})

    def test_stdin_requires_utf8_and_accepts_a_utf8_bom(self):
        source = json.dumps(request())
        for encoding, expected in (('utf-8', 0), ('utf-8-sig', 0), ('utf-16', 2)):
            runner = Mock(return_value=response('status'))
            out = io.StringIO()
            with io.TextIOWrapper(io.BytesIO(source.encode(encoding)), encoding='utf-8') as stream, \
                    patch('sys.stdin', stream), patch('sys.stdout', out):
                self.assertEqual(windows.main(['--json', '--request-json', '-'], runner=runner), expected)
            self.assertEqual(runner.call_count, 1 if expected == 0 else 0)
            self.assertEqual(json.loads(out.getvalue())['ok'], expected == 0)

    def test_default_output_is_human_and_child_json_flag_stays_literal(self):
        runner = Mock(return_value=response('windows', {'windows': [{'title': 'Editor 世界', 'target': TARGET}]}))
        code, out, err = self.run_cli(['windows'], runner=runner)
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(out.startswith('windows:\n'))
        self.assertIn('Editor 世界', out)
        self.assertIn('started: "' + TARGET['started'] + '"', out)
        runner = Mock(return_value=response('launch', {'pid': 99, 'target': None}))
        code, out, err = self.run_cli(['launch', '--', 'app.exe', '--json'], runner=runner)
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(out.startswith('launch:\n'))
        self.assertEqual(runner.call_args.args[0]['params']['argv'], ['app.exe', '--json'])


class DesktopIntegrationTests(unittest.TestCase):
    def test_cli_runner_transports_json_with_fixed_literal_arguments(self):
        value = request('launch', {'argv': ['C:\\Program Files\\app.exe', '世界', '', '"quote"\\', '$(literal)', '--json']})
        run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response('launch', {'pid': 9})).encode(), stderr=b''))
        result = windows.execute(value, runner=windows.cli_runner(['native with spaces.exe', 'desktop'], run=run))
        self.assertEqual(result['result'], {'pid': 9})
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ['native with spaces.exe', 'desktop', '--json', '--request-json', '-'])
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['params'], value['params'])
        self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_cli_runner_real_pipe_round_trip_with_fixture_process(self):
        # This subprocess only echoes JSON; it has no native backend or UI code.
        source = ('import json,sys; req=json.load(sys.stdin); '
                  'json.dump({"schema":req["schema"],"operation":req["operation"],'
                  '"ok":True,"result":{"echo":req["params"]}},sys.stdout)')
        client = windows.DesktopClient(runner=windows.cli_runner([sys.executable, '-B', '-c', source]))
        params = {'argv': ['app.exe', '你好\n🙂', '"quoted"', "a'b", '', 'trailing\\', '$HOME', 'a&b']}
        self.assertEqual(client.call('launch', params=params), {'echo': params})

    def test_cli_runner_reports_backend_errors_and_rejects_false_success(self):
        failed = response('status', error={'code': 'locked_desktop', 'message': 'Desktop is locked'})
        run = Mock(return_value=SimpleNamespace(returncode=1, stdout=json.dumps(failed).encode(), stderr=b''))
        self.assertEqual(windows.execute(request(), runner=windows.cli_runner(['fixture'], run=run)), failed)
        for rc, output in ((2, b'not json'), (2, json.dumps(response('status')).encode()),
                           (0, json.dumps(response('focus')).encode())):
            run = Mock(return_value=SimpleNamespace(returncode=rc, stdout=output, stderr=b'fixture diagnostic'))
            with self.assertRaises(windows.DesktopError):
                windows.execute(request(), runner=windows.cli_runner(['fixture'], run=run))
            run.assert_called_once()

    def test_native_dispatch_uses_packaged_root_and_bypasses_terminal_control(self):
        native = load_module('desktop_test_native', WINDOWS / 'native-commands.py')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'desktop_transport.py').write_text(
                'def execute(req):\n'
                '    return {"schema":req["schema"],"ok":True,"operation":req["operation"],'
                '"result":{"packaged":True,"request":req}}\n', encoding='utf-8')
            ambient = SimpleNamespace(execute=Mock(side_effect=AssertionError('ambient transport was loaded')))
            for alias in ('desktop', 'windows'):
                out = io.StringIO()
                with patch.object(native, 'ROOT', root), patch.object(sys, 'platform', 'win32'), \
                        patch.dict(sys.modules, {'desktop_transport': ambient}), patch('sys.stdout', out), \
                        patch.object(native, 'refresh_control', side_effect=AssertionError('terminal control touched')):
                    self.assertEqual(native.main([alias, 'status', '--json']), 0)
                self.assertTrue(json.loads(out.getvalue())['result']['packaged'])
            with patch.object(sys, 'argv', [str(root / 'native-commands.py')]), patch.object(sys, 'platform', 'win32'):
                self.assertTrue(windows.execute(request())['result']['packaged'])

    def test_native_and_wsl_help_do_not_refresh_sessions_or_spawn_processes(self):
        native = load_module('desktop_test_native_help', WINDOWS / 'native-commands.py')
        companion = load_module('desktop_test_companion_help', WINDOWS / 'wsl/companion.py')
        with patch('sys.stdout', io.StringIO()), patch.object(windows, '_native_execute') as execute, \
                patch.object(subprocess, 'run') as run, patch.object(subprocess, 'call') as call, \
                patch.object(native, 'refresh_control', side_effect=AssertionError('refresh')), \
                patch.object(companion, 'refresh_environment', side_effect=AssertionError('refresh')):
            for module in (native, companion):
                for args in (['desktop', 'click', '--help'], ['windows', 'capabilities', '--json'],
                             ['help', 'desktop', 'type'], ['desktop']):
                    self.assertEqual(module.main(args), 0)
        execute.assert_not_called()
        run.assert_not_called()
        call.assert_not_called()

    def test_wsl_dispatches_validated_request_via_native_cli_without_a_shell(self):
        companion = load_module('desktop_test_companion', WINDOWS / 'wsl/companion.py')
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(response('type', {'sent': True})).encode(), stderr=b'')
        with patch('sys.stdout', io.StringIO()), patch.object(companion, 'ROOT', Path('/fixture/native package')), \
                patch.object(companion, 'refresh_environment', side_effect=AssertionError('refresh')), \
                patch.object(subprocess, 'run', return_value=completed) as run:
            self.assertEqual(companion.main(['windows', '--json', 'type', *IDENTITY, 'literal "你好" $()']), 0)
        self.assertEqual(run.call_args.args[0], ['/fixture/native package/kilix-cli.exe', 'desktop', '--json', '--request-json', '-'])
        sent = json.loads(run.call_args.kwargs['input'])
        self.assertEqual(sent['target'], TARGET)
        self.assertEqual(sent['params'], {'text': 'literal "你好" $()'})

    def test_staging_contains_desktop_closure_and_installed_help_is_offline(self):
        stager = load_module('desktop_test_stager', WINDOWS / 'stage-commands.py')
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / 'source'
            source = fixture / 'windows'
            (source / 'wsl').mkdir(parents=True)
            config = fixture / 'config'
            (config / 'kilix_sdk').mkdir(parents=True)
            (config / 'kilix_tmux').mkdir()
            for name in ('__init__.py', 'windows.py'):
                shutil.copy2(CONFIG / 'kilix_sdk' / name, config / 'kilix_sdk' / name)
            for name in ('remote.py', 'tmux_control.py', 'content_models.py', 'kilix_clock_settings.py', 'agent_fixture.py'):
                (config / name).write_text('# fixture\n')
            (fixture / 'kilix-settings').write_text('# fixture\n')
            (fixture / 'scripts').mkdir()
            (fixture / 'scripts/transcript-view.py').write_text('# fixture\n')
            for name in ('kilix-content', 'kilix-transcript-clean'):
                dependency = fixture / 'third_party' / name
                (dependency / 'src').mkdir(parents=True)
                (dependency / 'LICENSE').write_text('Fixture license\n')
            shutil.copy2(WINDOWS / 'native-commands.py', source / 'native-commands.py')
            (source / 'desktop_transport.py').write_text('raise AssertionError("help imported transport")\n')
            for name in ('desktop.ps1', 'desktop-native.cs', 'desktop-dispatch.ps1'):
                (source / name).write_text('fixture ' + name)
            destination = Path(tmp) / 'package with spaces'
            with patch.object(stager, 'SOURCE', source):
                stager.stage(destination)
            for name in ('desktop_transport.py', 'desktop.ps1', 'desktop-native.cs', 'desktop-dispatch.ps1'):
                self.assertEqual((destination / name).read_bytes(), (source / name).read_bytes())
            result = subprocess.run([sys.executable, '-B', str(destination / 'native-commands.py'),
                                     'help', 'desktop', 'click'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--window HWND', result.stdout)
            # A partial package must not silently omit an executable worker.
            (source / 'desktop-native.cs').unlink()
            with patch.object(stager, 'SOURCE', source), self.assertRaises(FileNotFoundError):
                stager.stage(Path(tmp) / 'incomplete-package')


if __name__ == '__main__':
    unittest.main()
