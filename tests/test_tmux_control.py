from pathlib import Path
import json
import os
import subprocess
import shlex
import shutil
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TmuxControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = {'PATH': os.environ['PATH'], 'HOME': str(self.root),
                    'GPU_TERMINAL_HOME': str(self.root / 'untouched'),
                    'TMUX': '/live/socket,1,0', 'TMUX_PANE': '%88'}
        self.cli = self.root / 'cli'
        self.cli.write_text('#!/usr/bin/env python3\nimport json,sys\n'
                            'print(json.dumps(sys.argv[1:]))\n')
        self.cli.chmod(0o700)
        self.env['KILIX_TMUX_CLI'] = str(self.cli)
        self.addCleanup(self.temp.cleanup)

    def call(self, *args):
        r = subprocess.run([str(ROOT / 'kilix'), 'tmux', *args],
                           env=self.env, capture_output=True, text=True, timeout=5)
        self.assertFalse((self.root / 'untouched').exists())
        return r

    def test_missing_and_relative_socket_refuse_before_backend(self):
        for args in (('--json', 'list'), ('--socket', 'relative', '--json', 'list'),
                     ('--socket',), ('--socket', '--json', 'list')):
            r = self.call(*args)
            self.assertEqual(r.returncode, 2, r.stderr)
            if '--json' in args:
                self.assertEqual(json.loads(r.stdout)['code'], 'EUSAGE')

    def test_literal_arguments_forwarded_without_shell(self):
        text = '`touch nowhere` $(false) "quoted" ; $HOME'
        args = ('--socket', str(self.root / 'socket'), '--json', 'send',
                '%1', text)
        r = self.call(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), list(args))

    def test_explicit_missing_implementation_does_not_fall_back(self):
        self.env['KILIX_TMUX_CLI'] = str(self.root / 'missing')
        r = self.call('--socket', str(self.root / 'socket'), '--json', 'list')
        self.assertEqual(r.returncode, 7)
        self.assertEqual(json.loads(r.stdout)['code'], 'ETMUX')

    def test_module_selection(self):
        self.env.pop('KILIX_TMUX_CLI')
        package = self.root / 'kilix_tmux'
        package.mkdir()
        (package / '__init__.py').write_text('')
        (package / '__main__.py').write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n')
        self.env['KILIX_TMUX_MODULE_ROOT'] = str(self.root)
        args = ('--socket', str(self.root / 'socket'), '--json', 'list')
        r = self.call(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), list(args))

    def test_help_needs_no_backend_or_storage(self):
        self.env['KILIX_TMUX_CLI'] = str(self.root / 'absent')
        r = self.call('--help')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Submission does not establish command completion', r.stdout)

    def test_verb_help_without_socket_cannot_block_the_next_command(self):
        self.env.pop('KILIX_TMUX_CLI')
        for verb in ('send', 'type', 'close', 'new'):
            r = self.call(verb, '--help')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('usage: kilix-tmux', r.stdout)
        self.assertEqual(self.call('close', '$0').returncode, 2)


@unittest.skipUnless(shutil.which('tmux'), 'tmux unavailable')
class BundledTmuxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kilix-tmux-')
        self.root = Path(self.temp.name)
        self.socket = self.root / 'socket'
        self.env = {'PATH': os.environ['PATH'], 'HOME': str(self.root),
                    'GPU_TERMINAL_HOME': str(self.root / 'untouched'),
                    'TERM': 'xterm-256color'}
        self.tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'fixture',
                  '-x', '200', '-y', '30', '/bin/bash --noprofile --norc')
        self.pane = self.tmux('display-message', '-p', '-t', '=fixture:', '#{pane_id}').strip()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.tmux, 'kill-server')

    def tmux(self, *args):
        return subprocess.run(['tmux', '-S', str(self.socket), *args], env=self.env,
                              capture_output=True, text=True, timeout=5, check=True).stdout

    def call(self, *args):
        p = subprocess.run([str(ROOT / 'kilix'), 'tmux', '--socket', str(self.socket),
                            '--json', *args], env=self.env, capture_output=True,
                           text=True, timeout=10)
        self.assertFalse((self.root / 'untouched').exists())
        return p.returncode, json.loads(p.stdout)

    def test_bundled_literal_send_preserves_trailing_semicolon(self):
        for text in ('literal;', 'literal;;', 'literal\\;', 'literal $HOME `false`;'):
            with self.subTest(text=text):
                code, result = self.call('send', self.pane, text)
                self.assertEqual(code, 0, result)
                self.assertFalse(result['data']['submitted'])
                time.sleep(.08)
                _, captured = self.call('read', self.pane, '--lines', '1')
                self.assertTrue(captured['data']['text'].rstrip().endswith(text), captured)
                self.call('key', self.pane, 'C-u')

    def test_bundled_lifecycle_dry_run_and_exact_targets(self):
        _, initial = self.call('list')
        _, planned = self.call('--dry-run', 'new', 'second')
        self.assertTrue(planned['data']['dry_run'])
        self.assertEqual(self.call('list')[1], initial)
        _, created = self.call('new', 'second')
        ident = created['data']['session']['id']
        _, renamed = self.call('rename', ident, 'renamed')
        self.assertEqual(renamed['data']['session']['id'], ident)
        self.assertEqual(self.call('close', 'rename')[1]['code'], 'ENOENT')
        self.assertTrue(self.call('close', ident)[1]['ok'])
        self.assertEqual(self.call('list')[1], initial)

    def test_bundled_type_receipt_is_from_target(self):
        receipt = self.root / 'receipt'
        command = 'printf "%s" "$TMUX_PANE" > ' + shlex.quote(str(receipt))
        code, result = self.call('type', self.pane, command)
        self.assertEqual(code, 0, result)
        self.assertTrue(result['data']['submitted'])
        self.assertEqual(result['data']['completion'], 'unknown')
        for _ in range(40):
            if receipt.exists():
                break
            time.sleep(.025)
        self.assertEqual(receipt.read_text(), self.pane)


if __name__ == '__main__':
    unittest.main()
