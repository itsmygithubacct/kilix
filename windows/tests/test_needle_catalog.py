"""Exercise the vocabulary delta against the selected public parser source."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('needle_catalog', ROOT / 'wsl/needle_catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class SelectedNeedleVocabularyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get('KILIX_WSL_NEEDLE_TEST_SOURCE')
        if not path:
            if os.environ.get('KILIX_REQUIRE_WSL_NEEDLE_TESTS') == '1':
                raise RuntimeError('The selected WSL Needle source fixture is required')
            raise unittest.SkipTest('Set KILIX_WSL_NEEDLE_TEST_SOURCE to the selected exported Needle tree')
        cls.source = Path(path).resolve()
        if not (cls.source / 'apps.py').is_file():
            raise RuntimeError('The selected Needle source fixture has no apps.py')

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.generation = Path(tmp.name)
        (self.generation / 'needle').mkdir()
        self.parser = self.generation / 'needle/apps.py'
        shutil.copyfile(self.source / 'apps.py', self.parser)

    def test_selected_parser_keeps_catalog_admission_and_refusal_behavior(self):
        catalog.patch_needle(self.generation)
        # Execute the existing functional vocabulary checks with the real selected
        # interpreter modules. They propose/interpret only; no action is dispatched.
        code = '''import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('check_catalog', sys.argv[1])
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
assert check.check_needle(pathlib.Path(sys.argv[2])) == 16
import apps
assert apps.GAMES.count('tictactoe-tui') == 1
assert apps.GAMES.count('pleb-plant-grower') == 1
assert 'pleb-tower' in apps.GAMES
assert 'kilix-help-llm' in apps.APPS and 'kilix-waydroid' in apps.APPS
'''
        env = dict(os.environ, PYTHONPATH=str(self.source), PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'tests/check-catalog.py'),
                        str(self.parser.parent)], env=env, check=True, timeout=30)

    def test_selected_parser_patch_is_idempotent(self):
        catalog.patch_needle(self.generation)
        first = self.parser.read_bytes()
        catalog.patch_needle(self.generation)
        self.assertEqual(self.parser.read_bytes(), first)

    def test_changed_parser_is_refused_without_writing(self):
        changed = self.parser.read_bytes() + b'\n# changed source\n'
        self.parser.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'source differs'):
            catalog.patch_needle(self.generation)
        self.assertEqual(self.parser.read_bytes(), changed)


if __name__ == '__main__':
    unittest.main()
