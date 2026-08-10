"""PDF Conversion installs only from Kilix's pinned content catalog."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-pdf.py"
CATALOG = (ROOT / "third_party" / "kilix-content" / "src" / "kilix_content"
           / "catalog" / "plebian.json")


def catalog_entry():
    content = json.loads(CATALOG.read_text(encoding="utf-8"))["content"]
    for entry in content:
        if entry["id"] == "kilix-pdf-conversion":
            return entry
    raise AssertionError("the catalog no longer carries kilix-pdf-conversion")


class PdfInstallerTests(unittest.TestCase):
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

    def test_printed_ref_is_a_full_catalog_commit(self):
        result = self.run_installer("--print-ref")
        self.assertEqual(result.returncode, 0, result.stderr)
        ref = catalog_entry()["source"]["ref"]
        self.assertEqual(result.stdout.strip(), ref)
        self.assertEqual(len(ref), 40)
        self.assertTrue(all(character in "0123456789abcdef" for character in ref))

    def test_printing_the_ref_installs_nothing(self):
        result = self.run_installer("--print-ref")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "gpu_terminal").exists())

    def test_auto_install_can_be_refused(self):
        result = self.run_installer(KILIX_PDF_AUTO_INSTALL="0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("KILIX_PDF_AUTO_INSTALL=1", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_it_installs_under_kilix_data_not_the_source_tree(self):
        result = self.run_installer(KILIX_PDF_AUTO_INSTALL="0")
        expected = str(self.root / "gpu_terminal" / "kilix" / "data"
                       / "desktop-apps")
        self.assertIn(expected, result.stderr)

    def test_catalog_uses_the_reproducible_runtime_target(self):
        entry = catalog_entry()
        self.assertEqual(entry["binary"], "kilix-pdf")
        self.assertEqual(entry["build"], ["make", "runtime"])

    def test_the_launcher_exposes_it(self):
        launcher = (ROOT / "kilix").read_text(encoding="utf-8")
        self.assertIn("pdf|pdf-conversion)", launcher)
        self.assertIn("app|application)", launcher)
        self.assertIn("content_app.py", launcher)


if __name__ == "__main__":
    unittest.main()
