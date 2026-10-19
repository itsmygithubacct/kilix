"""Portable native command logic; engine/Win32 execution is tested on Windows."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('native_commands',
    Path(__file__).resolve().parents[1] / 'native-commands.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeSettingsTests(unittest.TestCase):
    def test_scalar_settings_validate_before_atomic_update_and_preserve_comments(self):
        parser = ModuleType('kitty.options.parse')

        def parse_conf_item(key, value, output):
            if key != 'font_size':
                return False
            output[key] = float(value)
            return True

        parser.parse_conf_item = parse_conf_item
        options = ModuleType('kitty.options.types')
        options.defaults = SimpleNamespace(font_size=11, symbol_map={})
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LOCALAPPDATA': tmp}), \
                patch.dict('sys.modules', {'kitty.options.parse': parser, 'kitty.options.types': options}):
            path = Path(tmp) / 'Kilix/kitty.conf'
            path.parent.mkdir()
            path.write_text('# user comment\nfont_size 10\nshell powershell.exe -NoLogo\nfont_size 11\n')
            native.settings(['--set', 'font_size=13.5'])
            self.assertEqual(path.read_text(), '# user comment\nshell powershell.exe -NoLogo\nfont_size 13.5\n')
            before = path.read_bytes()
            for value in ('font_size=invalid', 'unknown_option=1', 'font_size=12\ninclude injected',
                          'map=ctrl+a launch example', 'symbol_map=U+E000-U+F8FF Test'):
                with self.subTest(value=value), patch('sys.stderr', new_callable=io.StringIO):
                    with self.assertRaises((ValueError, SystemExit)):
                        native.settings(['--set', value])
                    self.assertEqual(path.read_bytes(), before)

    def test_readonly_settings_and_help_do_not_create_a_config_directory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LOCALAPPDATA': tmp}), \
                patch('sys.stdout', new_callable=io.StringIO):
            for args in ([], ['--path'], ['--print']):
                self.assertEqual(native.settings(args), 0)
            self.assertFalse((Path(tmp) / 'Kilix').exists())

    def test_setup_update_scope_policy_to_packaged_script_and_forward_literal_arguments(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(native, 'ROOT', Path(tmp)), \
                patch.object(native.subprocess, 'call', return_value=0) as call:
            (Path(tmp) / 'setup.ps1').touch()
            (Path(tmp) / 'update.ps1').touch()
            native.main(['setup', 'install', '-Archive', "C:\\a b\\literal$(text).zip", '-Sha256', '0' * 64])
            self.assertEqual(call.call_args.args[0], ['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', str(Path(tmp) / 'setup.ps1'), '-Action', 'Install', '-Archive',
                "C:\\a b\\literal$(text).zip", '-Sha256', '0' * 64])
            native.main(['update', 'enable-schedule', '-ScheduleAction', 'Check', '-Channel', 'preview'])
            self.assertEqual(call.call_args.args[0][-6:],
                ['-Action', 'EnableSchedule', '-ScheduleAction', 'Check', '-Channel', 'preview'])

    def test_tk_uses_only_the_bundled_runtime_paths(self):
        import json
        with tempfile.TemporaryDirectory() as tmp, patch.object(native, 'ROOT', Path(tmp)), \
                patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            for child in ('pylib/tcl', 'pylib/tk'):
                (root / child).mkdir(parents=True)
            manifest = root / 'tk-runtime.json'
            manifest.write_text(json.dumps({'tcl': 'pylib/tcl', 'tk': 'pylib/tk'}))
            native.configure_tk()
            self.assertEqual(os.environ['TCL_LIBRARY'], str(root / 'pylib/tcl'))
            self.assertEqual(os.environ['TK_LIBRARY'], str(root / 'pylib/tk'))
            manifest.write_text(json.dumps({'tcl': '../outside', 'tk': 'pylib/tk'}))
            with self.assertRaises(ValueError):
                native.configure_tk()

    def test_native_recovery_refreshes_all_control_and_receives_remote_callback(self):
        provider = ModuleType('kitty.windows_pty_broker')
        calls = []
        provider.refresh_environment = lambda: calls.append('refresh')
        provider.main = lambda args, remote: calls.append((args, remote)) or 0
        with patch.dict('sys.modules', {'kitty.windows_pty_broker': provider}), \
                patch.dict(os.environ, {'KILIX_NATIVE_SESSION_ID': 'owned-generation'}), \
                patch.object(native.subprocess, 'call', return_value=0):
            self.assertEqual(native.main(['session', 'list']), 0)
            self.assertEqual(calls, ['refresh', (['list'], native.remote)])


if __name__ == '__main__':
    unittest.main()
