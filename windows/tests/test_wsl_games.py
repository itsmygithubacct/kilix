"""Catalog discovery and real launcher behavior without a desktop session."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'wsl'


def load(name):
    spec = importlib.util.spec_from_file_location('wsl_' + name, SOURCE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, 'path', [str(SOURCE), *sys.path]):
        spec.loader.exec_module(module)
    return module


games = load('games')
setup = load('setup')
companion = load('companion')


class GamesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wsl games ' café ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'bin').mkdir()
        self.catalog = json.loads((SOURCE / 'game-catalog.json').read_text())
        (self.root / 'game-catalog.json').write_text(json.dumps(self.catalog))
        for game in self.catalog:
            binary = self.root / 'bin' / game['name']
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)

    def test_all_catalog_names_and_aliases_resolve_without_prefix_guessing(self):
        self.assertEqual(len(games.installed_games(self.root)), 15)
        names = set()
        for game in self.catalog:
            for name in [game['name'], *game['aliases']]:
                self.assertNotIn(name, names)
                names.add(name)
                self.assertEqual(games.game_path(name, self.root), self.root / 'bin' / game['name'])
        for name in ('../kilix-pong', '/bin/sh', 'kilix-../pong', 'python3', 'unknown'):
            self.assertIsNone(games.game_path(name, self.root))

    def test_listing_and_readiness_exclude_missing_or_nonexecutable_games(self):
        (self.root / 'bin/chess-bash').unlink()
        (self.root / 'bin/joustix').chmod(0o644)
        with patch.object(games, 'installation', return_value=self.root):
            self.assertFalse(games.game_ready('chess'))
            self.assertFalse(games.game_ready('joustix'))
            self.assertTrue(games.game_ready('solitaire'))
            self.assertEqual(len(games.installed_games()), 13)

    def test_existing_pong_generation_still_works(self):
        (self.root / 'game-catalog.json').unlink()
        self.assertEqual(games.installed_games(self.root), ['kilix-pong'])
        self.assertEqual(games.game_path('pong', self.root), self.root / 'bin/kilix-pong')

    def test_cli_lists_canonical_names_and_resolves_game_launches(self):
        with patch.object(companion, 'INSTALL', self.root), patch('sys.stdout', new_callable=io.StringIO) as out:
            companion.main(['games', 'list', '--json'])
            self.assertEqual(json.loads(out.getvalue()), sorted(g['name'] for g in self.catalog))
            with patch.object(companion, 'remote', return_value=0) as remote:
                companion.main(['--launch', 'chess', '--help'])
                self.assertEqual(remote.call_args.args[0][-2:], [str(self.root / 'bin/chess-bash'), '--help'])
            with patch.object(companion.os, 'execve', side_effect=SystemExit) as execute:
                with self.assertRaises(SystemExit):
                    companion.main(['games', 'play', 'solitaire', '--seed', '7'])
                self.assertEqual(execute.call_args.args[1], [str(self.root / 'bin/solitaire-tui'), '--seed', '7'])

    def test_game_help_does_not_require_an_installation_or_connection(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(companion, 'INSTALL', self.root / 'absent'):
            for args in (['--help'], ['list', '--help'], ['play', '--help']):
                with patch('sys.stdout', new_callable=io.StringIO) as out:
                    self.assertEqual(companion.main(['games', *args]), 0)
                    self.assertIn('usage:', out.getvalue())

    def test_built_launcher_finds_assets_and_preserves_literal_arguments(self):
        source = self.root / 'fixture'
        source.mkdir()
        program = source / 'game.py'
        program.write_text('import json,os,sys\nfrom pathlib import Path\n'
                           'print(json.dumps([Path("asset").read_text(),sys.argv[1:],'
                           'os.environ["KITTYFB_TRANSPORT"],sys.executable]))\n')
        (source / 'asset').write_text('game data')
        (source / 'Makefile').write_text('all:\n\tprintf \'#!/bin/sh\\nexec python3 game.py "$$@"\\n\' > game\n\tchmod +x game\n')
        fixture = [dict(name='fixture', source='fixture', executable='game', aliases=['short'])]
        (self.root / 'game-catalog.json').write_text(json.dumps(fixture))
        with patch.object(setup, '__file__', str(self.root / 'setup.py')):
            setup.build_games(self.root, str(Path(sys.executable).resolve()))
        args = ['', 'a b', "quote'", '$HOME;$(false)', 'line\nbreak', '日本語']
        result = subprocess.check_output([str(self.root / 'bin/short'), *args], cwd='/', text=True)
        data, actual, transport, python = json.loads(result)
        self.assertEqual((data, actual, transport), ('game data', args, 'inline'))
        self.assertEqual(Path(python).resolve(), Path(sys.executable).resolve())

    def test_a_missing_game_binary_prevents_catalog_publication(self):
        (self.root / 'game-catalog.json').unlink()
        with patch.object(setup.subprocess, 'run'):
            with self.assertRaisesRegex(ValueError, 'did not produce'):
                setup.build_games(self.root, sys.executable)
        self.assertFalse((self.root / 'game-catalog.json').exists())


if __name__ == '__main__':
    unittest.main()
