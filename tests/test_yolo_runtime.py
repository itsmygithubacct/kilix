"""The detection runtime has to be installable from the list, not only by hand.

kilix-nvr links no ML library on purpose — its detector is a subprocess, which
is what lets inference run in a virtualenv or on another machine entirely. The
cost is that a freshly provisioned box has a recorder that cannot detect
anything, and nothing in the software list to fix it with. These tests hold the
three joins that close that gap:

  * `kilix install` offers a `yolo` row, so the launcher's software centre —
    which reads `kilix install --json` and nothing else — offers it too;
  * the installer reports honestly on a machine where it has never run, rather
    than claiming a runtime it has not built;
  * the launcher exports the setting the installer writes, because an install
    whose result no later session can see is not an install.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-yolo.sh"


def _install_rows() -> list[dict]:
    result = subprocess.run(
        ["python3", str(ROOT / "config" / "install.py"), "--json"],
        capture_output=True, text=True, check=False, timeout=120)
    if result.returncode != 0:
        raise AssertionError(f"kilix install --json failed: {result.stderr}")
    return json.loads(result.stdout)


class YoloRow(unittest.TestCase):
    def test_the_list_offers_the_runtime(self):
        rows = _install_rows()
        runtimes = [r for r in rows if r.get("kind") == "runtime"]
        self.assertTrue(runtimes, "no runtime rows in `kilix install --json`")
        yolo = next((r for r in runtimes if r.get("id") == "yolo"), None)
        self.assertIsNotNone(yolo, "no yolo runtime row")
        # The software centre renders label, kind and installed for every row
        # it is given, so every row has to carry them.
        for field in ("id", "label", "kind", "description", "installed"):
            self.assertIn(field, yolo)
        self.assertIsInstance(yolo["installed"], bool)

    def test_the_table_prints_the_runtime_section(self):
        result = subprocess.run(
            ["python3", str(ROOT / "config" / "install.py")],
            capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        # A kind missing from the table's own list of kinds is a row that is
        # in the json and invisible on the terminal, which is exactly the bug
        # this guards.
        self.assertIn("runtimes", result.stdout)
        self.assertIn("yolo", result.stdout)

    def test_an_unknown_id_is_still_refused(self):
        result = subprocess.run(
            ["python3", str(ROOT / "config" / "install.py"), "yolo-typo"],
            capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown item", result.stderr)


class Installer(unittest.TestCase):
    def test_it_is_executable(self):
        self.assertTrue(INSTALLER.is_file())
        self.assertTrue(os.access(INSTALLER, os.X_OK))

    def test_check_reports_a_machine_it_has_never_run_on(self):
        with tempfile.TemporaryDirectory() as scratch:
            environment = dict(os.environ,
                               KILIX_YOLO_DIR=os.path.join(scratch, "yolo"))
            result = subprocess.run([str(INSTALLER), "--check"], env=environment,
                                    capture_output=True, text=True, check=False,
                                    timeout=120)
            # Non-zero, because "is it ready" is the question --check answers.
            self.assertEqual(result.returncode, 1)
            self.assertIn("not installed", result.stdout)
            self.assertIn("missing", result.stdout)

    def test_it_refuses_a_broad_runtime_directory(self):
        for directory in (os.path.expanduser("~"), "/"):
            environment = dict(os.environ, KILIX_YOLO_DIR=directory)
            result = subprocess.run([str(INSTALLER), "--check"], env=environment,
                                    capture_output=True, text=True, check=False,
                                    timeout=120)
            self.assertEqual(result.returncode, 1, directory)
            self.assertIn("refusing broad runtime path", result.stderr)

    def test_a_relative_directory_is_refused(self):
        environment = dict(os.environ, KILIX_YOLO_DIR="runtimes/yolo")
        result = subprocess.run([str(INSTALLER), "--check"], env=environment,
                                capture_output=True, text=True, check=False,
                                timeout=120)
        self.assertEqual(result.returncode, 1)
        self.assertIn("absolute path", result.stderr)

    def test_remove_on_a_missing_runtime_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as scratch:
            environment = dict(os.environ,
                               KILIX_YOLO_DIR=os.path.join(scratch, "yolo"))
            result = subprocess.run([str(INSTALLER), "--remove"],
                                    env=environment, capture_output=True,
                                    text=True, check=False, timeout=120)
            self.assertEqual(result.returncode, 0)

    def test_upgrade_before_install_says_so(self):
        with tempfile.TemporaryDirectory() as scratch:
            environment = dict(os.environ,
                               KILIX_YOLO_DIR=os.path.join(scratch, "yolo"))
            result = subprocess.run([str(INSTALLER), "--upgrade"],
                                    env=environment, capture_output=True,
                                    text=True, check=False, timeout=120)
            self.assertEqual(result.returncode, 1)
            self.assertIn("nothing installed", result.stderr)


class LauncherWiring(unittest.TestCase):
    def test_the_verb_exists_and_documents_itself(self):
        result = subprocess.run([str(ROOT / "kilix"), "yolo", "--help"],
                                capture_output=True, text=True, check=False,
                                timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        for word in ("install", "check", "update", "remove"):
            self.assertIn(word, result.stdout)

    def test_the_setting_the_installer_writes_is_exported(self):
        """`KILIX_NVR_DETECT` has to survive into a pane.

        The installer records it in kilix.env; the launcher exports only keys
        it lists. A key written but not listed is an install that works once,
        in the shell that ran it, and never again.
        """
        launcher = (ROOT / "kilix").read_text(encoding="utf-8")
        self.assertIn("KILIX_NVR_DETECT", launcher)
        self.assertIn("KILIX_SOUND_CLASSIFIER", launcher)
        self.assertIn("KILIX_YOLO_DIR", launcher)


if __name__ == "__main__":
    unittest.main()
