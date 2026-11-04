"""Execute generated session launchers from a non-interactive shell."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import gui_route


class GuiRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'original commands'
        self.bin.mkdir()
        self.log = self.root / 'argv.json'
        for name in ('kilix', 'firefox', 'evince', 'mpv', 'qemu-system-x86_64',
                     'cheese', 'xterm', 'gparted', 'kilix-amp', 'lxpolkit', 'python3'):
            script = self.bin / name
            script.write_text('#!/usr/bin/python3\nimport json,os,sys\n'
                              'from pathlib import Path\n'
                              'Path(os.environ["RECORD"]).write_text(json.dumps(sys.argv))\n'
                              'Path(os.environ["RECORD"]+".env").write_text(json.dumps(dict(os.environ)))\n'
                              'raise SystemExit(23)\n')
            script.chmod(0o700)
        self.env = dict(PATH=str(self.bin) + ':/usr/bin:/bin', HOME=str(self.root),
                        DISPLAY=':321', RECORD=str(self.log),
                        XDG_DATA_HOME=str(self.root), XDG_DATA_DIRS=str(self.root),
                        KILIX_RUN_ALIASES='1')
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(gui_route, 'installed_gui_commands',
                             return_value=['python3', 'lxpolkit', 'cheese']):
            self.route = Path(gui_route.prepare(str(self.root), str(self.bin/'kilix')))
        self.env['PATH'] = str(self.route) + ':' + self.env['PATH']

    def run_command(self, command, args=(), **env):
        result = subprocess.run(['/bin/sh', '-c', 'exec "$@"', 'test', command, *args],
                                env={**self.env, **env}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        return json.loads(self.log.read_text()), result.stderr

    def test_observed_apps_enter_tab_runner(self):
        for command in ('firefox', 'evince', 'mpv', 'cheese', 'xterm', 'gparted', 'kilix-amp'):
            with self.subTest(command=command):
                argv, _ = self.run_command(command, ['a b', ';touch forbidden', '--literal=$HOME'])
                self.assertEqual(argv, [str(self.bin/'kilix'), 'run', str(self.bin/command),
                                        'a b', ';touch forbidden', '--literal=$HOME'])

    def test_private_children_and_opt_out_never_recurse(self):
        for env in ({'KILIX_PRIVATE_XAPP': '1'}, {'KILIX_RUN_ALIASES': '0'}, {'DISPLAY': ''}):
            argv, _ = self.run_command('evince', ['report.pdf'], **env)
            self.assertEqual(argv, [str(self.bin/'evince'), 'report.pdf'])

    def test_terminal_and_headless_uses_remain_direct(self):
        for command, args in [('firefox', ['--headless']), ('evince', ['--version']),
                              ('mpv', ['--vo=kitty', 'movie']),
                              ('qemu-system-x86_64', ['-nographic', '-daemonize']),
                              ('qemu-system-x86_64', ['-display', 'none', '-daemonize']),
                              ('qemu-system-x86_64', ['-vnc', ':1'])]:
            argv, _ = self.run_command(command, args)
            self.assertEqual(argv, [str(self.bin/command), *args])

    def test_graphical_qemu_is_owned_by_tab(self):
        for flag in ('-daemonize', '--daemonize'):
            argv, warning = self.run_command('qemu-system-x86_64',
                                             ['-display', 'gtk', flag, '-pidfile', 'guest.pid'])
            self.assertEqual(argv, [str(self.bin/'kilix'), 'run',
                                   str(self.bin/'qemu-system-x86_64'),
                                   '-display', 'gtk', '-pidfile', 'guest.pid'])
            self.assertIn('closing that tab ends the VM', warning)

    def test_desktop_in_place_guard_does_not_swallow_new_tab(self):
        self.run_command('evince', ['report.pdf'], KILIX_IN_OVERLAY='1')
        env = json.loads(Path(str(self.log)+'.env').read_text())
        self.assertNotIn('KILIX_IN_OVERLAY', env)
        self.run_command('evince', ['report.pdf'], KILIX_IN_OVERLAY='1', KILIX_PRIVATE_XAPP='1')
        env = json.loads(Path(str(self.log)+'.env').read_text())
        self.assertEqual(env['KILIX_IN_OVERLAY'], '1')

    def test_services_and_interpreters_are_not_intercepted(self):
        for command in ('lxpolkit', 'python3'):
            self.assertFalse((self.route/command).exists())

    def test_exclusion_and_private_modes(self):
        with patch.dict(os.environ, {**self.env, 'PATH': str(self.bin),
                                     'KILIX_RUN_ALIAS_EXCLUDE_APPS': 'evince'}, clear=True), \
                patch.object(gui_route, 'installed_gui_commands', return_value=[]):
            route = Path(gui_route.prepare(str(self.root), str(self.bin/'kilix')))
        self.assertFalse((route/'evince').exists())
        self.assertEqual(route.stat().st_mode & 0o777, 0o700)
        self.assertEqual((route/'mpv').stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
