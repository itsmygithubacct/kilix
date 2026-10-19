"""Selector/wait contract tests. All transports are fake; no desktop is touched."""
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

CONFIG = Path(__file__).resolve().parents[2] / 'config'
with patch.object(sys, 'path', [str(CONFIG), *sys.path]):
    from kilix_sdk import windows

TARGET = {'hwnd': '123456', 'pid': 4321, 'started': '134000000000000000'}
IDENTITY = ['--hwnd', TARGET['hwnd'], '--pid', str(TARGET['pid']), '--started', TARGET['started']]


def request(op, params, target=None, timeout=20):
    value = {'schema': windows.SCHEMA, 'operation': op, 'params': params, 'timeout': timeout}
    if target is not None:
        value['target'] = target
    return value


def reply(op, result=None, error=None):
    value = {'schema': windows.SCHEMA, 'operation': op, 'ok': error is None}
    value['error' if error else 'result'] = error if error else ({} if result is None else result)
    return value


def found(rows=None, **metadata):
    return reply('find', {'windows': [{'target': TARGET}] if rows is None else rows,
                          'skipped': 0, 'incomplete': False, **metadata})


class DesktopSelectorTests(unittest.TestCase):
    def cli(self, args, runner):
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.stdout', out), patch('sys.stderr', err):
            code = windows.main(['--json', *args], runner=runner)
        self.assertEqual(err.getvalue(), '')
        return code, json.loads(out.getvalue())

    def test_invalid_selector_and_wait_contracts_never_reach_transport(self):
        invalid = [
            request('find', {'app': x}) for x in ('', '*.exe', 'app?', 'app|other', 'C:app.exe',
                '/usr/bin/app', r'bin\app.exe', 'C:\\Apps\\', r'C:\Apps\.', r'C:\Apps\..',
                r'\\server\share', r'\\.\app.exe', '\ud800')
        ] + [
            request('find', {'selector': {'name': 'Save'}}),
            request('find', {'selector': {}}, TARGET),
            request('find', {'selector': {'unknown': 'Save'}}, TARGET),
            request('find', {'selector': {'name': None}}, TARGET),
            request('find', {'selector': {'name': '\ud800'}}, TARGET),
            request('find', {'selector': {'automation_id': 1}}, TARGET),
            request('find', {'selector': {'control_type': 'ControlType.Button'}}, TARGET),
            request('find', {'selector': {'control_type': 'Button\n'}}, TARGET),
            request('find', {'selector': {'control_type': ''}}, TARGET),
            request('find', {'selector': {'name': 'x'}, 'include_hidden': False}, TARGET),
            request('find', {'include_hidden': 1}),
            request('find', {'depth': 3}),
            request('find', {}, TARGET),
            request('find', {'title': 'x', 'selector': {'name': 'x'}}, TARGET),
            request('find', {'selector': {'name': 'x'}, 'depth': 65}, TARGET),
            request('find', {'selector': {'name': 'x'}, 'limit': 20001}, TARGET),
            request('wait', {'state': 'visible'}),
            request('wait', {'state': 'hidden', 'app': 'app.exe'}),
            request('wait', {'state': [], 'app': 'app.exe'}),
            request('wait', {'state': 'closed', 'pid': True}),
            request('wait', {'state': 'closed', 'element': '42.1', 'app': 'app.exe'}),
            request('wait', {'state': 'closed', 'element': '42.1', 'selector': {'name': 'x'}}, TARGET),
            request('wait', {'state': 'visible', 'wait_ms': -1}, TARGET),
            request('wait', {'state': 'visible', 'wait_ms': True}, TARGET),
            request('wait', {'state': 'visible', 'wait_ms': 19001}, TARGET),
            request('wait', {'state': 'visible', 'wait_ms': 1}, TARGET, timeout=1),
            request('wait', {'state': 'visible', 'poll_ms': 19}, TARGET),
            request('wait', {'state': 'visible', 'poll_ms': 1001}, TARGET),
            request('wait', {'state': 'visible', 'depth': 2}, TARGET),
            request('invoke', {'element': '42.1', 'selector': {'name': 'x'}}, TARGET),
            request('invoke', {}, TARGET),
            request('invoke', {'selector': {}}, TARGET),
            request('invoke', {'selector': {'name': 'x'}, 'depth': -1}, TARGET),
            request('set-value', {'selector': {'name': 'x'}, 'text': 'x' * 16385}, TARGET),
        ]
        runner = Mock(side_effect=AssertionError('invalid request reached transport'))
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(windows.RequestError):
                windows.execute(value, runner=runner)
        runner.assert_not_called()

    def test_valid_literals_defaults_and_no_caller_mutation(self):
        for app in ('APP.EXE', 'My Editor.exe', r'C:\Program Files\Editor\app.exe',
                    r'\\server\apps\app.exe', 'C:/Apps/app.exe'):
            original = request('find', {'app': app, 'title': '字 λ', 'pid': 1})
            self.assertEqual(windows.validate_request(original), original)
        original = request('find', {'selector': {'name': '', 'automation_id': '', 'control_type': 'Edit'}}, TARGET)
        before = copy.deepcopy(original)
        validated = windows.validate_request(original)
        self.assertEqual(original, before)
        self.assertEqual((validated['params']['depth'], validated['params']['limit']), (32, 2000))
        for timeout, expected in ((1, 0), (5, 4000), (20, 10000), (60, 10000)):
            validated = windows.validate_request(request('wait', {'state': 'closed'}, TARGET, timeout))
            self.assertEqual(validated['params'], {'state': 'closed', 'wait_ms': expected, 'poll_ms': 100})
        windows.validate_request(request('wait', {'state': 'present', 'wait_ms': 59000, 'poll_ms': 1000}, TARGET, 60))

    def test_sdk_actions_resolve_once_and_send_full_identity(self):
        runner = Mock(side_effect=[found(), reply('invoke', {'element': '42.9'})])
        client = windows.DesktopClient(runner)
        selector = {'name': 'Save', 'automation_id': 'saveButton', 'control_type': 'Button'}
        self.assertEqual(client.call('invoke', app='editor.exe', title='Untitled', pid=4321,
                                     params={'selector': selector}), {'element': '42.9'})
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(runner.call_args_list[0].args[0], request('find', {'app': 'editor.exe', 'title': 'Untitled', 'pid': 4321}))
        self.assertEqual(runner.call_args_list[1].args[0], request('invoke', {'selector': selector}, TARGET))

    def test_all_action_arguments_are_validated_before_selector_resolution(self):
        runner = Mock(side_effect=AssertionError('must not look up a target yet'))
        client = windows.DesktopClient(runner)
        invalid = [('type', {'text': '\ud800'}), ('set-value', {'selector': {'name': 'x'}, 'text': 'x' * 16385}),
                   ('invoke', {'selector': {'control_type': []}}), ('click', {'x': -1, 'y': 0}),
                   ('move', {'x': 0, 'y': 0, 'width': 0, 'height': 1}), ('key', {'keys': ['NO_KEY']})]
        for op, params in invalid:
            with self.subTest(op=op), self.assertRaises(windows.RequestError):
                client.call(op, app='editor.exe', params=params)
        for args in (['type', '--app', 'editor.exe', 'x' * 16385],
                     ['invoke', '--app', 'editor.exe', '--name', 'x', '--limit', '0'],
                     ['set-value', '--app', 'editor.exe', '--name', 'x', 'x' * 16385],
                     ['focus', '--app', '*.exe'], ['close', '--pid', '0'],
                     ['focus', '--app', 'editor.exe', *IDENTITY],
                     ['focus', '--window', '123', '--title', 'x'],
                     ['focus', '--pid', '123', '--started', '123']):
            with self.subTest(args=args):
                code, result = self.cli(args, runner)
                self.assertEqual(code, 2)
                self.assertEqual(result['error']['code'], 'invalid_request')
        runner.assert_not_called()

    def test_ambiguous_missing_or_incomplete_resolution_never_sends_action(self):
        cases = [
            (found([]), 'window_not_found'),
            (found([{'target': TARGET}, {'target': {**TARGET, 'hwnd': '765432'}}]), 'ambiguous_target'),
            (found(skipped=1), 'search_incomplete'), (found(incomplete=True), 'search_incomplete'),
            (found(truncated=True), 'search_incomplete'),
            (found([{'title': 'without identity'}]), 'invalid_response'),
            (found([{'target': {**TARGET, 'started': '0'}}]), 'invalid_response'),
            (reply('find', {'windows': [{'target': TARGET}]}), 'invalid_response'),
            (found(skipped=False), 'invalid_response'), (found(skipped=-1), 'invalid_response'),
            (found(incomplete=0), 'invalid_response'),
        ]
        for result, expected in cases:
            runner = Mock(return_value=result)
            with self.subTest(expected=expected), self.assertRaises(windows.DesktopError) as raised:
                windows.DesktopClient(runner).call('close', app='editor.exe')
            self.assertEqual(raised.exception.code, expected)
            runner.assert_called_once()

    def test_native_action_failures_are_never_retried_or_resolved_again(self):
        for code in ('stale_target', 'stale_element', 'element_disabled', 'element_offscreen',
                     'ambiguous_element', 'accessibility_incomplete', 'timeout'):
            runner = Mock(side_effect=[found(), reply('invoke', error={'code': code, 'message': 'fixture'})])
            with self.subTest(code=code), self.assertRaises(windows.DesktopError) as raised:
                windows.DesktopClient(runner).call('invoke', app='editor.exe', params={'selector': {'name': 'Save'}})
            self.assertEqual(raised.exception.code, code)
            self.assertEqual([call.args[0]['operation'] for call in runner.call_args_list], ['find', 'invoke'])

    def test_find_cli_and_sdk_return_all_matches_with_completeness(self):
        result = {'target': TARGET, 'elements': [{'id': '42.1'}, {'id': '42.2'}],
                  'visited': 20, 'incomplete': True, 'truncated': False}
        runner = Mock(return_value=reply('find', result))
        client = windows.DesktopClient(runner)
        self.assertEqual(client.find_elements(target=TARGET, name='Save', control_type='Button'), result)
        self.assertEqual(runner.call_args.args[0]['params'],
                         {'selector': {'name': 'Save', 'control_type': 'Button'}, 'depth': 32, 'limit': 2000})
        code, response = self.cli(['find', '--app', 'editor.exe', '--pid', '4321', '--name', 'Save',
                                   '--automation-id', 'save', '--control-type', 'Button'], runner)
        self.assertEqual(code, 0)
        self.assertEqual(response['result'], result)
        self.assertNotIn('target', runner.call_args.args[0])
        self.assertEqual(runner.call_args.args[0]['params']['selector'],
                         {'name': 'Save', 'automation_id': 'save', 'control_type': 'Button'})
        code, _ = self.cli(['find', '--app', 'editor.exe', '--include-hidden'], runner)
        self.assertEqual(code, 0)
        self.assertTrue(runner.call_args.args[0]['params']['include_hidden'])

    def test_selector_action_cli_and_legacy_element_positionals(self):
        cases = [(['invoke', '--name', 'Save'], 'invoke', {'selector': {'name': 'Save'}}),
                 (['set-value', '--automation-id', 'edit', '--', '--literal'], 'set-value',
                  {'selector': {'automation_id': 'edit'}, 'text': '--literal'}),
                 (['set-value', '42.9', 'literal'], 'set-value', {'element': '42.9', 'text': 'literal'}),
                 (['invoke', '42.9'], 'invoke', {'element': '42.9'})]
        for args, operation, params in cases:
            runner = Mock(side_effect=[found(), reply(operation)])
            with self.subTest(args=args):
                code, result = self.cli([args[0], '--app', 'editor.exe', '--title', 'Notes', *args[1:]], runner)
                self.assertEqual(code, 0, result)
                self.assertEqual(runner.call_args.args[0], request(operation, params, TARGET))
                self.assertEqual(runner.call_count, 2)
        runner = Mock(side_effect=[found(), reply('focus')])
        code, result = self.cli(['focus', '--pid', '4321'], runner)
        self.assertEqual(code, 0, result)
        self.assertEqual(runner.call_args_list[0].args[0]['params'], {'pid': 4321})

    def test_wait_is_one_request_even_with_window_and_element_selectors(self):
        success = {'state': 'enabled', 'matched': True, 'target': TARGET, 'element': {'id': '42.1'},
                   'window': {}, 'polls': 21, 'elapsed_ms': 2200}
        runner = Mock(return_value=reply('wait', success))
        client = windows.DesktopClient(runner)
        self.assertEqual(client.wait('enabled', app='editor.exe', title='Notes', name='Save',
                                     control_type='Button', wait_ms=5000, poll_ms=250), success)
        runner.assert_called_once()
        sent = runner.call_args.args[0]
        self.assertEqual(sent, request('wait', {'state': 'enabled', 'app': 'editor.exe', 'title': 'Notes',
            'selector': {'name': 'Save', 'control_type': 'Button'}, 'depth': 32, 'limit': 2000, 'wait_ms': 5000, 'poll_ms': 250}))
        runner.reset_mock()
        code, result = self.cli(['wait', '--app', 'editor.exe', '--for', 'visible', '--wait-ms', '0'], runner)
        self.assertEqual(code, 0, result)
        runner.assert_called_once()
        self.assertNotIn('target', runner.call_args.args[0])

    def test_wait_closed_keeps_exact_identity_even_if_already_gone(self):
        result = {'state': 'closed', 'matched': True, 'target': TARGET, 'window': None,
                  'element': None, 'polls': 1, 'elapsed_ms': 0}
        runner = Mock(return_value=reply('wait', result))
        self.assertEqual(windows.DesktopClient(runner).wait('closed', target=TARGET, wait_ms=0), result)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0]['target'], TARGET)
        runner.reset_mock()
        code, response = self.cli(['wait', '--for', 'closed', *IDENTITY, '--element', '42.9'], runner)
        self.assertEqual(code, 0, response)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0]['params']['element'], '42.9')

    def test_wait_timeout_and_identity_reuse_are_distinct_errors_without_retry(self):
        for code in ('wait_timeout', 'stale_target', 'ambiguous_target', 'ambiguous_element'):
            runner = Mock(return_value=reply('wait', error={'code': code, 'message': 'last=provider_unavailable'}))
            with self.subTest(code=code), self.assertRaises(windows.DesktopError) as raised:
                windows.DesktopClient(runner).wait('closed', target=TARGET)
            self.assertEqual(raised.exception.code, code)
            runner.assert_called_once()
            runner.reset_mock()
            rc, result = self.cli(['wait', '--for', 'closed', *IDENTITY], runner)
            self.assertEqual(rc, 1)
            self.assertEqual(result['error']['code'], code)
            runner.assert_called_once()

    def test_cli_transport_wait_serializes_one_native_request(self):
        result = reply('wait', error={'code': 'wait_timeout', 'message': 'last=disabled'})
        run = Mock(return_value=type('Completed', (), {'returncode': 1, 'stdout': json.dumps(result).encode()})())
        client = windows.DesktopClient(windows.cli_runner(['fixture-cli', 'desktop'], run=run))
        with self.assertRaises(windows.DesktopError) as raised:
            client.wait('enabled', app='editor.exe', automation_id='button', wait_ms=2500, timeout=5)
        self.assertEqual(raised.exception.code, 'wait_timeout')
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ['fixture-cli', 'desktop', '--json', '--request-json', '-'])
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['params']['wait_ms'], 2500)
        self.assertEqual(run.call_args.kwargs['timeout'], 15)


if __name__ == '__main__':
    unittest.main()
