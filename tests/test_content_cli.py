"""`kilix content set-aside` moves an installed tree aside and deletes nothing."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from _env_support import sandbox_env  # noqa: E402


class ContentSetAsideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "apps"
        self.tree = self.root / "some-game"
        self.tree.mkdir(parents=True)
        (self.tree / "marker").write_text("installed\n")

    def kilix(self, *arguments):
        return subprocess.run([str(ROOT / "kilix"), "content", *arguments],
                              env=sandbox_env(HOME=self.temp.name), text=True,
                              capture_output=True, timeout=60)

    def test_the_tree_moves_to_a_hidden_sibling_that_is_printed(self):
        result = self.kilix("set-aside", str(self.tree))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.tree.exists())
        siblings = [path for path in self.root.iterdir()
                    if path.name.startswith(".some-game.set-aside-")]
        self.assertEqual(len(siblings), 1, list(self.root.iterdir()))
        self.assertEqual((siblings[0] / "marker").read_text(), "installed\n")
        self.assertIn(str(siblings[0]), result.stdout)
        self.assertIn("rm -rf --", result.stdout)

    def test_a_symlink_or_missing_path_is_refused(self):
        link = self.root / "link"
        link.symlink_to(self.tree)
        for target in (link, self.root / "missing"):
            with self.subTest(target=target.name):
                result = self.kilix("set-aside", str(target))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("kilix content:", result.stderr)
        self.assertTrue((self.tree / "marker").is_file())
        self.assertTrue(link.is_symlink())

    def test_a_subcommand_is_required(self):
        self.assertNotEqual(self.kilix().returncode, 0)


if __name__ == "__main__":
    unittest.main()
