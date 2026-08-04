"""The Media Player installer reads the catalog rather than a pin of its own.

Kilix Amp is catalog content, so the property under test is that this script
takes its commit and its build from the shared, pinned catalog and installs
into Kilix's own data directory — never into the source tree, and never from a
ref written down here.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-amp.py"
CATALOG = (ROOT / "third_party" / "kilix-content" / "src" / "kilix_content"
           / "catalog" / "plebian.json")


def catalog_entry():
    content = json.loads(CATALOG.read_text(encoding="utf-8"))["content"]
    for entry in content:
        if entry["id"] == "kilix-amp":
            return entry
    raise AssertionError("the catalog no longer carries kilix-amp")


class AmpInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = dict(os.environ)
        self.env.update({
            "HOME": str(self.root / "home"),
            "GPU_TERMINAL_HOME": str(self.root / "gpu_terminal"),
        })
        self.env.pop("KILIX_STORAGE_HOME", None)
        self.env.pop("KILIX_DATA_HOME", None)

    def tearDown(self):
        self.temp.cleanup()

    def run_installer(self, *arguments, **overrides):
        env = dict(self.env)
        env.update(overrides)
        return subprocess.run(
            ["python3", str(INSTALLER), *arguments], env=env,
            capture_output=True, text=True, check=False)

    def test_printed_ref_is_the_catalog_pin(self):
        result = self.run_installer("--print-ref")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), catalog_entry()["source"]["ref"])

    def test_the_ref_is_a_full_immutable_commit(self):
        """A catalog ref is a 40-character commit; a branch would not pin."""
        ref = catalog_entry()["source"]["ref"]
        self.assertEqual(len(ref), 40)
        self.assertTrue(all(character in "0123456789abcdef" for character in ref))

    def test_printing_the_ref_installs_nothing(self):
        result = self.run_installer("--print-ref")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "gpu_terminal").exists())

    def test_auto_install_can_be_refused(self):
        """A provisioner that has not opted in must not trigger a compile."""
        result = self.run_installer(KILIX_AMP_AUTO_INSTALL="0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("KILIX_AMP_AUTO_INSTALL=1", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_it_installs_under_kilix_data_not_the_source_tree(self):
        """Where it would install, without paying for a clone to find out."""
        result = self.run_installer(KILIX_AMP_AUTO_INSTALL="0")
        expected = str(self.root / "gpu_terminal" / "kilix" / "data"
                       / "desktop-apps")
        self.assertIn(expected, result.stderr)

    def test_the_launcher_exposes_it(self):
        launcher = (ROOT / "kilix").read_text(encoding="utf-8")
        self.assertIn("amp|media-player)", launcher)
        self.assertIn("install-kilix-amp.py", launcher)
        # The backend has to be reachable from the launcher, or kilix-music has
        # nothing to ask for on a machine with no player yet.
        self.assertIn("--headless", launcher)


if __name__ == "__main__":
    unittest.main()
