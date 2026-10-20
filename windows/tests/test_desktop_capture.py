"""Caller-local screenshots through fake/native-package, WSL and SSH transports.

No desktop, network connection, or Windows login is used by these tests.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

from test_desktop_commands import CONFIG, WINDOWS, TARGET, load_module, request, response, windows


def png_bytes():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0\0\xff\0')) + chunk(b'IEND', b''))


PNG = png_bytes()


def payload(png=PNG):
    return {'png_base64': base64.b64encode(png).decode('ascii'), 'byte_length': len(png),
            'sha256': hashlib.sha256(png).hexdigest(),
            'bounds': {'x': -100, 'y': 20, 'width': 2, 'height': 1},
            'capture': 'screen', 'occlusion_possible': True}


class CaptureFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.destination = self.root / 'screen 字.png'
        self.runner = Mock(return_value=response('capture', payload()))
        self.client = windows.DesktopClient(runner=self.runner)

    def assert_no_temporary(self):
        self.assertEqual(list(self.root.glob('.kilix-capture-*')), [])


class LocalCaptureTests(CaptureFixture):
    def test_verified_sdk_capture_has_only_metadata_and_full_identity(self):
        result = self.client.capture_to_local(self.destination, target=TARGET, timeout=7)
        self.assertEqual(self.destination.read_bytes(), PNG)
        self.assertEqual(result, {'path': str(self.destination), **{k: v for k, v in payload().items() if k != 'png_base64'}})
        self.assertEqual(self.runner.call_args.args[0], windows.validate_request(request('capture', target=TARGET, timeout=7)))
        self.assertNotIn('png_base64', json.dumps(result))
        self.assert_no_temporary()

    def test_relative_destination_is_resolved_on_the_caller(self):
        result = self.client.capture_to_local(os.path.relpath(self.destination))
        self.assertEqual(result['path'], str(self.destination))
        self.assertEqual(self.destination.read_bytes(), PNG)
        self.assertNotIn('path', self.runner.call_args.args[0]['params'])

    def test_local_destination_and_arguments_fail_before_transport(self):
        for path, options in [(self.root / 'missing' / 'out.png', {}), (self.root / 'out.jpg', {}),
                              ('', {}), (None, {}), (b'out.png', {}), ('nul\0.png', {}),
                              (str(self.destination) + '/', {}), (self.destination, {'overwrite': 1}),
                              (self.destination, {'timeout': 0}), (self.destination, {'target': {'hwnd': '3'}})]:
            with self.subTest(path=path, options=options), self.assertRaises(windows.DesktopError):
                self.client.capture_to_local(path, **options)
        self.destination.mkdir()
        with self.assertRaises(windows.DesktopError):
            self.client.capture_to_local(self.destination, overwrite=True)
        self.runner.assert_not_called()
        self.assert_no_temporary()

    def test_directory_writability_is_probed_before_transport(self):
        with patch.object(windows.tempfile, 'mkstemp', side_effect=PermissionError('denied')):
            with self.assertRaises(windows.DesktopError) as caught:
                self.client.capture_to_local(self.destination)
        self.assertEqual(caught.exception.code, 'io_error')
        self.runner.assert_not_called()

    def test_existing_file_requires_explicit_overwrite(self):
        self.destination.write_bytes(b'keep')
        with self.assertRaises(windows.DesktopError) as caught:
            self.client.capture_to_local(self.destination)
        self.assertEqual(caught.exception.code, 'file_exists')
        self.runner.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), b'keep')
        self.client.capture_to_local(self.destination, overwrite=True)
        self.assertEqual(self.destination.read_bytes(), PNG)
        self.assert_no_temporary()

    def test_existing_and_dangling_symlinks_are_never_followed(self):
        victim = self.root / 'victim'
        for dangling in (False, True):
            if not dangling:
                victim.write_bytes(b'keep')
            try:
                self.destination.symlink_to(victim)
            except OSError:
                self.skipTest('symlink creation unavailable')
            for overwrite in (False, True):
                with self.assertRaises(windows.DesktopError):
                    self.client.capture_to_local(self.destination, overwrite=overwrite)
            self.assertTrue(self.destination.is_symlink())
            self.destination.unlink()
            if not dangling:
                self.assertEqual(victim.read_bytes(), b'keep')
                victim.unlink()
        self.runner.assert_not_called()

    def test_competing_writer_wins_without_clobber(self):
        def compete(_request):
            self.destination.write_bytes(b'other writer')
            return response('capture', payload())
        self.runner.side_effect = compete
        with self.assertRaises(windows.DesktopError) as caught:
            self.client.capture_to_local(self.destination)
        self.assertEqual(caught.exception.code, 'file_exists')
        self.assertEqual(self.destination.read_bytes(), b'other writer')
        self.assert_no_temporary()

    def test_competing_symlink_is_not_followed(self):
        victim = self.root / 'victim'
        victim.write_bytes(b'keep')
        def compete(_request):
            self.destination.symlink_to(victim)
            return response('capture', payload())
        self.runner.side_effect = compete
        with self.assertRaises(windows.DesktopError):
            self.client.capture_to_local(self.destination)
        self.assertEqual(victim.read_bytes(), b'keep')
        self.assertTrue(self.destination.is_symlink())
        self.assert_no_temporary()

    def test_overwrite_publishes_complete_file_in_one_replace(self):
        self.destination.write_bytes(b'previous')
        replace = os.replace
        def observe(source, destination):
            self.assertEqual(self.destination.read_bytes(), b'previous')
            self.assertEqual(Path(source).read_bytes(), PNG)
            return replace(source, destination)
        with patch.object(windows.os, 'replace', side_effect=observe) as commit:
            self.client.capture_to_local(self.destination, overwrite=True)
        commit.assert_called_once()
        self.assert_no_temporary()

    def test_transport_and_integrity_failures_preserve_old_file_and_clean_up(self):
        mutations = [None, {}, {'byte_length': True}, {'byte_length': len(PNG) + 1},
                     {'byte_length': windows.MAX_CAPTURE_BYTES + 1}, {'png_base64': '!!!!'},
                     {'png_base64': payload()['png_base64'][:-1] + '!'}, {'sha256': '0' * 64},
                     {'sha256': 'x' * 64}, {'bounds': None}, {'bounds': {'x': 0}},
                     {'bounds': {**payload()['bounds'], 'width': 3}},
                     {'bounds': {**payload()['bounds'], 'width': True}},
                     {'bounds': {**payload()['bounds'], 'height': 16385}},
                     {'occlusion_possible': False}, {'capture': 'unknown'},
                     payload(b'not PNG' + PNG[7:]), payload(PNG[:12] + b'BAD!' + PNG[16:]),
                     payload(PNG[:29] + b'\0\0\0\0' + PNG[33:])]
        for change in mutations:
            value = None if change is None else {**payload(), **change}
            if change == {}:
                value = {}
            self.runner.return_value = response('capture', value)
            self.destination.write_bytes(b'previous')
            with self.subTest(change=change), self.assertRaises(windows.DesktopError) as caught:
                self.client.capture_to_local(self.destination, overwrite=True)
            self.assertEqual(caught.exception.code, 'invalid_response')
            self.assertEqual(self.destination.read_bytes(), b'previous')
            self.assert_no_temporary()
        self.runner.return_value = response('capture', error={'code': 'desktop_unavailable', 'message': 'Locked'})
        with self.assertRaises(windows.DesktopError) as caught:
            self.client.capture_to_local(self.destination, overwrite=True)
        self.assertEqual(caught.exception.code, 'desktop_unavailable')
        self.assert_no_temporary()

    def test_timeout_has_no_retry_or_partial_file(self):
        self.runner.side_effect = subprocess.TimeoutExpired('fixture', 20)
        with self.assertRaises(windows.DesktopError) as caught:
            self.client.capture_to_local(self.destination)
        self.assertEqual(caught.exception.code, 'timeout')
        self.runner.assert_called_once()
        self.assertFalse(self.destination.exists())
        self.assert_no_temporary()

    def test_local_write_failure_preserves_previous_file(self):
        self.destination.write_bytes(b'previous')
        with patch.object(windows.os, 'fsync', side_effect=OSError('disk full')):
            with self.assertRaises(windows.DesktopError) as caught:
                self.client.capture_to_local(self.destination, overwrite=True)
        self.assertEqual(caught.exception.code, 'io_error')
        self.assertEqual(self.destination.read_bytes(), b'previous')
        self.assert_no_temporary()

    def test_exact_png_limit_round_trips_through_real_cli_pipe(self):
        # Exercise >2/16 MiB JSON transport boundaries without touching a desktop.
        source = ('import sys,json,base64,hashlib; r=json.load(sys.stdin); '
                  'p=base64.b64decode(sys.argv[1]); p+=b"x"*(int(sys.argv[2])-len(p)); '
                  'v=json.loads(sys.argv[3]); v.update(png_base64=base64.b64encode(p).decode(),'
                  'byte_length=len(p),sha256=hashlib.sha256(p).hexdigest()); '
                  'json.dump(dict(schema=r["schema"],operation=r["operation"],ok=True,result=v),sys.stdout)')
        runner = windows.cli_runner([sys.executable, '-B', '-c', source, base64.b64encode(PNG).decode(),
                                     str(windows.MAX_CAPTURE_BYTES), json.dumps(payload())])
        result = windows.DesktopClient(runner=runner).capture_to_local(self.destination)
        self.assertEqual(self.destination.stat().st_size, windows.MAX_CAPTURE_BYTES)
        self.assertEqual(result['sha256'], hashlib.sha256(self.destination.read_bytes()).hexdigest())


class CaptureCLITests(CaptureFixture):
    def run_cli(self, args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.stdout', out), patch('sys.stderr', err):
            code = windows.main(args, runner=self.runner, **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_local_cli_returns_metadata_without_image_bytes(self):
        for mode in ([], ['--json']):
            code, out, err = self.run_cli(['screenshot', '--local', str(self.destination), *mode])
            self.assertEqual((code, err), (0, ''))
            self.assertNotIn('png_base64', out)
            self.assertNotIn(payload()['png_base64'], out)
            self.assertIn('sha256', out)
            if mode:
                self.assertEqual(json.loads(out)['operation'], 'screenshot')
            self.destination.unlink()

    def test_path_conflicts_and_missing_destinations_are_offline(self):
        for tail in ([], ['--local', str(self.destination), r'C:\Temp\out.png'],
                     ['--local', str(self.root / 'missing' / 'out.png'), '--window', '123456']):
            code, out, _err = self.run_cli(['screenshot', '--json', *tail])
            self.assertNotEqual(code, 0, out)
        self.runner.assert_not_called()

    def test_window_resolution_keeps_target_and_timeout(self):
        self.runner.side_effect = [response('windows', {'windows': [{'target': TARGET}]}), response('capture', payload())]
        code, out, _err = self.run_cli(['screenshot', '--local', str(self.destination), '--window', TARGET['hwnd'], '--timeout', '8'])
        self.assertEqual(code, 0, out)
        self.assertEqual([call.args[0]['operation'] for call in self.runner.call_args_list], ['windows', 'capture'])
        self.assertEqual(self.runner.call_args.args[0]['target'], TARGET)
        self.assertEqual(self.runner.call_args.args[0]['timeout'], 8)

    def test_wsl_positional_linux_paths_include_mounted_drives(self):
        for path in ('relative.png', '/tmp/screen.png', '/mnt/c/Temp/screen.png'):
            with patch.object(windows, '_local_capture_path', return_value=self.destination):
                code, out, _err = self.run_cli(['screenshot', path], local_paths=True)
            self.assertEqual(code, 0, out)
            self.assertEqual(self.runner.call_args.args[0]['operation'], 'capture')
            self.assertEqual(self.destination.read_bytes(), PNG)
            self.destination.unlink()

    def test_windows_drive_and_unc_paths_remain_native_in_wsl(self):
        self.runner.return_value = response('screenshot', {'path': r'C:\Temp\screen.png'})
        for path in (r'C:\Temp\screen.png', 'C:/Temp/screen.png', r'\\server\share\screen.png'):
            code, out, _err = self.run_cli(['screenshot', path], local_paths=True)
            self.assertEqual(code, 0, out)
            self.assertEqual(self.runner.call_args.args[0]['operation'], 'screenshot')
            self.assertEqual(self.runner.call_args.args[0]['params'], {'path': path, 'overwrite': False})
        self.runner.reset_mock()
        code, _out, _err = self.run_cli(['screenshot', 'C:screen.png'], local_paths=True)
        self.assertEqual(code, 2)
        self.runner.assert_not_called()

    def test_capture_has_no_byte_printing_normal_command(self):
        code, _out, _err = self.run_cli(['capture', '--json'])
        self.assertEqual(code, 2)
        self.runner.assert_not_called()
        caps = windows.capabilities()
        self.assertFalse(caps['operations']['capture']['mutates'])
        self.assertEqual(caps['operations']['capture']['target'], 'optional')
        self.assertTrue(caps['capture']['protocol_only'])
        self.assertGreater(caps['limits']['response_bytes'], 4 * ((caps['limits']['capture_png_bytes'] + 2) // 3))
        for params in ({'path': r'C:\Temp\screen.png'}, {'overwrite': True}):
            with self.assertRaises(windows.RequestError):
                windows.validate_request(request('capture', params))

    def test_raw_protocol_explicitly_carries_capture_bytes(self):
        out = io.StringIO()
        with patch('sys.stdin', io.StringIO(json.dumps(request('capture')))), patch('sys.stdout', out):
            code = windows.main(['--json', '--request-json', '-'], runner=self.runner)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['result']['png_base64'], payload()['png_base64'])

    def test_cli_and_ssh_reject_oversized_responses_before_json_parsing(self):
        ssh = load_module('capture_ssh_limit', CONFIG / 'windows_control.py')
        completed = SimpleNamespace(returncode=0, stdout='x' * 1025, stderr='')
        with patch.object(windows, 'MAX_RESPONSE_BYTES', 1024), \
                patch.object(windows, '_load_json', side_effect=AssertionError('oversized JSON parsed')):
            with self.assertRaises(windows.DesktopError) as caught:
                windows.cli_runner(['fixture'], run=Mock(return_value=completed))(request('capture'))
            self.assertEqual(caught.exception.code, 'transport_error')
            with patch.object(subprocess, 'run', return_value=completed):
                reply = ssh.SSHTransport('fixture-alias')(request('capture'))
            self.assertEqual(reply['error']['code'], 'transport_error')
            self.assertIn('48 MiB', reply['error']['message'])

    def test_native_transport_accepts_capture_and_bounds_responses(self):
        native = load_module('capture_native_transport', WINDOWS / 'desktop_transport.py')
        self.assertEqual(native.MAX_RESPONSE_BYTES, windows.MAX_RESPONSE_BYTES)
        for output, limit, ok in ((json.dumps(response('capture', payload())), windows.MAX_RESPONSE_BYTES, True),
                                 ('x' * 1025, 1024, False)):
            completed = SimpleNamespace(returncode=0, stdout=output, stderr='')
            with patch.object(native, 'os', SimpleNamespace(name='nt', environ={'SystemRoot': str(self.root)})), \
                    patch.object(Path, 'is_file', return_value=True), patch.object(native, 'MAX_RESPONSE_BYTES', limit), \
                    patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                    patch.object(subprocess, 'run', return_value=completed):
                reply = native.execute(request('capture'))
            self.assertEqual(reply['ok'], ok)
            if ok:
                self.assertEqual(reply['result'], payload())
            else:
                self.assertEqual(reply['error']['code'], 'transport_error')

    def test_source_import_and_help_are_offline(self):
        for tail in (['screenshot', '--help'], ['capabilities', '--json']):
            result = subprocess.run([sys.executable, '-B', str(CONFIG / 'windows_control.py'), *tail],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--local', result.stdout)

    def test_native_package_sdk_routing_writes_on_caller(self):
        (self.root / 'desktop_transport.py').write_text('def execute(request):\n    return ' + repr(response('capture', payload())) + '\n')
        with patch.object(sys, 'platform', 'win32'):
            result = windows.DesktopClient(root=self.root).capture_to_local(self.destination)
        self.assertEqual(result['byte_length'], len(PNG))
        self.assertEqual(self.destination.read_bytes(), PNG)

    def test_wsl_companion_and_ssh_wrapper_integrate_local_capture(self):
        companion = load_module('capture_companion', WINDOWS / 'wsl/companion.py')
        ssh = load_module('capture_ssh', CONFIG / 'windows_control.py')
        for main, args in ((companion.main, ['desktop', 'screenshot', str(self.destination), '--json']),
                           (ssh.main, ['--host', 'fixture-alias', 'screenshot', '--local', str(self.destination), '--json'])):
            reply = json.dumps(response('capture', payload()))
            completed = SimpleNamespace(returncode=0, stdout=reply, stderr='')
            out = io.StringIO()
            with patch.object(subprocess, 'run', return_value=completed) as run, patch('sys.stdout', out), \
                    patch.object(companion, 'refresh_environment', side_effect=AssertionError('refresh')):
                self.assertEqual(main(args), 0)
            sent = json.loads(run.call_args.kwargs['input'].splitlines()[0])
            self.assertEqual(sent['operation'], 'capture')
            self.assertEqual(sent['params'], {})
            self.assertNotIn(str(self.destination), run.call_args.kwargs['input'].decode() if isinstance(run.call_args.kwargs['input'], bytes) else run.call_args.kwargs['input'])
            self.assertNotIn('png_base64', out.getvalue())
            self.assertEqual(self.destination.read_bytes(), PNG)
            self.destination.unlink()


if __name__ == '__main__':
    unittest.main()
