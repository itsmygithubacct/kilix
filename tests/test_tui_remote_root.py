"""Whole host relaunch with a private terminal server and real root helper."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(os.environ.get('C9_TEST_HOST', Path(__file__).resolve().parents[1]))


class RemoteRootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='tui-root-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.storage = self.base / 'storage'
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.log = self.base / 'relaunch.jsonl'
        self.output = self.base / 'output.json'
        desktop = self.bin / 'kilix-tui'
        desktop.write_text('#!/usr/bin/python3\nimport json,os,sys\n'
            'from pathlib import Path\n'
            'Path(os.environ["TEST_OUTPUT"]).write_text(json.dumps({"argv":sys.argv[1:],'
            '"root":os.environ["KILIX_CONTENT_ROOT"]}))\n')
        desktop.chmod(0o700)
        engine = self.base / 'prebuilt/bin'
        engine.mkdir(parents=True)
        (engine / 'kitty').write_text('#!/bin/sh\nexit 99\n')
        (engine / 'kitty').chmod(0o700)
        relay = engine / 'kitten'
        relay.write_text('#!/usr/bin/python3\n'
            'import json,os,subprocess,sys\nfrom pathlib import Path\n'
            'args=sys.argv[1:]\n'
            'if "ls" in args: print("[]");sys.exit(0)\n'
            'assert "launch" in args and "--" in args\n'
            'cut=args.index("--");cmd=args[cut+1:]\n'
            'env=json.loads(Path(os.environ["TEST_SERVER_ENV"]).read_text())\n'
            'for i,a in enumerate(args[:cut]):\n'
            ' if a=="--env": k,v=args[i+1].split("=",1);env[k]=v\n'
            'with Path(os.environ["TEST_RELAUNCH_LOG"]).open("a") as log: '
            'log.write(json.dumps({"argv":cmd,"inherited_root":env.get("KILIX_CONTENT_ROOT")})+"\\n")\n'
            'sys.exit(subprocess.run(cmd,env=env,timeout=10).returncode)\n')
        relay.chmod(0o700)
        self.env = {
            'PATH': str(self.bin) + ':' + os.defpath, 'HOME': str(self.home),
            'GPU_TERMINAL_HOME': str(self.base / 'shared'),
            'GPU_TERMINAL_SOURCE_HOME': str(self.base / 'sources'),
            'GPU_TERMINAL_SETTINGS_FILE': str(self.base / 'settings'),
            'KILIX_STORAGE_HOME': str(self.storage),
            'KILIX_DATA_HOME': str(self.storage / 'data'),
            'KILIX_PREBUILT_HOME': str(engine.parent),
            'KILIX_CONTENT_ROOT': str(self.base / 'caller-stale'),
            '_kilix_tui_explicit_root': str(self.base / 'caller-private'),
            'KITTY_LISTEN_ON': 'unix:/private-fixture-only', 'KITTY_WINDOW_ID': '1234',
            'TEST_SERVER_ENV': str(self.base / 'server.json'),
            'TEST_RELAUNCH_LOG': str(self.log), 'TEST_OUTPUT': str(self.output),
            'PYTHONDONTWRITEBYTECODE': '1', 'KILIX_TUI_UTILS_AUTO_INSTALL': '0',
            'GIT_ALLOW_PROTOCOL': 'file',
        }
        server = dict(self.env, KILIX_CONTENT_ROOT=str(self.base / 'server-stale'),
                      _kilix_tui_explicit_root=str(self.base / 'server-private'))
        (self.base / 'server.json').write_text(json.dumps(server))

    def launch(self, argv, *, owned=False):
        self.output.unlink(missing_ok=True)
        self.log.unlink(missing_ok=True)
        env = dict(self.env)
        if owned:
            env['KILIX_IN_OVERLAY'] = '1'
        return subprocess.run([str(ROOT / 'kilix'), *argv], env=env,
                              capture_output=True, text=True, timeout=15)

    def test_explicit_roots_survive_real_relaunch_and_owned_tab(self):
        for alias in ('tui', 'kilix-tui'):
            for leaf in ("95 'quoted' $HOME $(false) `false`", '95 trailing\n'):
                for owned in (False, True):
                    with self.subTest(alias=alias, leaf=leaf, owned=owned):
                        raw = str(self.base) + '/unused/../' + leaf
                        args = [alias, '--content-root', raw, '--screenshot', 'two words']
                        result = self.launch(args, owned=owned)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(self.output.read_text()), {
                            'root': os.path.normpath(raw), 'argv': ['--screenshot', 'two words']})
                        self.assertFalse(Path(os.path.normpath(raw)).exists())
                        if owned:
                            self.assertFalse(self.log.exists())
                        else:
                            relaunch = json.loads(self.log.read_text())
                            self.assertEqual(relaunch['inherited_root'],
                                             str(self.base / 'server-stale'))
                            self.assertEqual(relaunch['argv'], [str(ROOT / 'kilix'), 'tui',
                                '--content-root', os.path.normpath(raw), '--screenshot', 'two words'])

    def test_ordinary_host_ignores_both_inherited_roots_on_relaunch(self):
        for alias in ('tui', 'kilix-tui'):
            with self.subTest(alias=alias):
                result = self.launch([alias, '--screenshot'])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(self.output.read_text()), {
                    'root': str(self.storage / 'data/desktop-apps'), 'argv': ['--screenshot']})
                self.assertEqual(json.loads(self.log.read_text())['argv'],
                                 [str(ROOT / 'kilix'), 'desktop', '--screenshot'])
                self.assertFalse((self.storage / 'data/desktop-apps').exists())

    def test_invalid_duplicate_and_misplaced_selectors_refuse_before_setup(self):
        selectors = [
            ['--content-root'], ['--content-root', 'relative'], ['--content-root', ''],
            ['--content-root', '--screenshot'], ['--content-root=/absolute'],
            ['--content-root', '/absolute', '--content-root', '/duplicate'],
            ['--screenshot', '--content-root', '/misplaced'],
            ['--content-root', '/absolute', '--screenshot', '--content-root', '/duplicate'],
            ['--', '--content-root', '/misplaced'],
        ]
        arguments = [[alias, *row] for alias in ('tui', 'kilix-tui') for row in selectors]
        arguments += [['desktop', alias, '--content-root', '/misplaced']
                      for alias in ('tui', 'kilix-tui')]
        for index, argv in enumerate(arguments):
            with self.subTest(argv=argv):
                # Every refusal starts with fresh writable destinations, so an
                # earlier parent failure cannot contaminate later observations.
                case = self.base / ('invalid-' + str(index))
                case.mkdir()
                home = case / 'home'
                home.mkdir()
                storage = case / 'storage'
                settings = case / 'settings'
                self.env.update(HOME=str(home), KILIX_STORAGE_HOME=str(storage),
                    KILIX_DATA_HOME=str(storage / 'data'),
                    GPU_TERMINAL_HOME=str(case / 'shared'),
                    GPU_TERMINAL_SETTINGS_FILE=str(settings))
                result = self.launch(argv)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('content-root', result.stderr)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.log.exists())
                self.assertFalse(storage.exists(), 'invalid selector created host storage')
                self.assertFalse(settings.exists())
                self.assertEqual(list(home.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
