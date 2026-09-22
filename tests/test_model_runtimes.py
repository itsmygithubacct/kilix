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
import pathlib
import subprocess
import sys
import tempfile
import unittest

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402



ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-yolo.sh"
YAMNET = ROOT / "scripts" / "install-yamnet.sh"


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
            environment = sandbox_env(
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
            environment = sandbox_env(KILIX_YOLO_DIR=directory)
            result = subprocess.run([str(INSTALLER), "--check"], env=environment,
                                    capture_output=True, text=True, check=False,
                                    timeout=120)
            self.assertEqual(result.returncode, 1, directory)
            self.assertIn("refusing broad runtime path", result.stderr)

    def test_a_relative_directory_is_refused(self):
        environment = sandbox_env(KILIX_YOLO_DIR="runtimes/yolo")
        result = subprocess.run([str(INSTALLER), "--check"], env=environment,
                                capture_output=True, text=True, check=False,
                                timeout=120)
        self.assertEqual(result.returncode, 1)
        self.assertIn("absolute path", result.stderr)

    def test_remove_on_a_missing_runtime_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as scratch:
            environment = sandbox_env(
                KILIX_YOLO_DIR=os.path.join(scratch, "yolo"))
            result = subprocess.run([str(INSTALLER), "--remove"],
                                    env=environment, capture_output=True,
                                    text=True, check=False, timeout=120)
            self.assertEqual(result.returncode, 0)

    def test_it_prefers_uv_but_does_not_require_it(self):
        """uv is minutes faster on a torch-sized install.

        Named as the first way and not the only one: a machine without uv
        should still be able to install a detector, so both paths have to
        exist and `--check` has to say which one it would take.
        """
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('"$KILIX_UV" venv', installer)
        self.assertIn('"$KILIX_UV" pip install', installer)
        # ...and the fallback, for a machine that has never heard of uv.
        self.assertIn("-m venv", installer)
        self.assertIn("-m pip install", installer)

        with tempfile.TemporaryDirectory() as scratch:
            environment = sandbox_env(
                KILIX_YOLO_DIR=os.path.join(scratch, "yolo"))
            result = subprocess.run([str(INSTALLER), "--check"], env=environment,
                                    capture_output=True, text=True, check=False,
                                    timeout=120)
            self.assertIn("installer:", result.stdout)

    def test_upgrade_before_install_says_so(self):
        with tempfile.TemporaryDirectory() as scratch:
            environment = sandbox_env(
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
        """`KILIX_OBJECT_DETECTOR` has to survive into a pane.

        The installer records it in kilix.env; the launcher exports only keys
        it lists. A key written but not listed is an install that works once,
        in the shell that ran it, and never again.
        """
        launcher = (ROOT / "kilix").read_text(encoding="utf-8")
        for key in ("KILIX_OBJECT_DETECTOR", "KILIX_SOUND_CLASSIFIER",
                    "KILIX_YOLO_DIR"):
            self.assertIn(key, launcher)

    def test_the_runtime_points_at_the_module_that_owns_the_detector(self):
        """The detector script moved into kilix-object-detect.

        The installer used to resolve it out of the recorder's checkout and
        record KILIX_NVR_DETECT. Both are wrong now, and both fail silently:
        a wrapper pointing at a deleted script, and a setting nothing reads.
        """
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("install-kilix-object-detect.sh", installer)
        self.assertIn("kilix-look-detect", installer)
        self.assertIn("KILIX_OBJECT_DETECTOR=", installer)
        self.assertNotIn("tools/kilix-nvr-detect", installer)


class YamnetRuntime(unittest.TestCase):
    """The sound half, which had no installer at all until it bit.

    The classifier was recorded in kilix.env by hand as an interpreter and a
    script separated by a space. The launcher's own parser survives that;
    nothing else does — `set -a; . kilix.env` treats the first word as an
    assignment and *runs* the second, so a service or a plain shell got no
    classifier, fell back to a bundled tool that is not installed, and
    reported a broken pipe seconds later against an empty log. These hold the
    shape that prevents it.
    """

    def test_the_list_offers_the_runtime(self):
        rows = _install_rows()
        yamnet = next((r for r in rows
                       if r.get("kind") == "runtime" and r.get("id") == "yamnet"),
                      None)
        self.assertIsNotNone(yamnet, "no yamnet runtime row")
        for field in ("id", "label", "kind", "description", "installed"):
            self.assertIn(field, yamnet)
        self.assertIsInstance(yamnet["installed"], bool)

    def test_it_is_executable(self):
        self.assertTrue(YAMNET.is_file())
        self.assertTrue(os.access(YAMNET, os.X_OK))

    def test_check_reports_a_machine_it_has_never_run_on(self):
        with tempfile.TemporaryDirectory() as scratch:
            environment = sandbox_env(
                KILIX_YAMNET_DIR=os.path.join(scratch, "yamnet"))
            result = subprocess.run([str(YAMNET), "--check"], env=environment,
                                    capture_output=True, text=True,
                                    check=False, timeout=120)
            self.assertEqual(result.returncode, 1)
            self.assertIn("not installed", result.stdout)

    def test_it_refuses_a_broad_runtime_directory(self):
        for directory in (os.path.expanduser("~"), "/"):
            environment = sandbox_env(KILIX_YAMNET_DIR=directory)
            result = subprocess.run([str(YAMNET), "--check"], env=environment,
                                    capture_output=True, text=True,
                                    check=False, timeout=120)
            self.assertEqual(result.returncode, 1, directory)
            self.assertIn("refusing broad runtime path", result.stderr)

    def test_it_records_one_path_and_not_a_command_line(self):
        """A wrapper, so the recorded value has no space in it.

        This is the whole reason the installer exists rather than an
        instruction in a document.
        """
        installer = YAMNET.read_text(encoding="utf-8")
        self.assertIn("KILIX_SOUND_CLASSIFIER=%s", installer)
        self.assertIn("bin/kilix-listen-classify", installer)
        # The interpreter and the script go inside the wrapper, never into
        # the setting.
        self.assertNotIn("KILIX_SOUND_CLASSIFIER=%s %s", installer)

    def test_the_recorded_setting_has_no_space_in_it(self):
        """And on this machine, in the file that is actually there.

        A value with a space is unsourceable, which is how the classifier
        went missing for every process the launcher did not start.
        """
        root = os.environ.get("GPU_TERMINAL_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "gpu_terminal")
        env_file = Path(root) / "kilix" / "config" / "kilix.env"
        if not env_file.is_file():
            self.skipTest("no kilix.env on this machine")
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if not line.startswith("KILIX_SOUND_CLASSIFIER="):
                continue
            value = line.split("=", 1)[1]
            self.assertNotIn(" ", value.strip(),
                             "the classifier setting must be one path")


if __name__ == "__main__":
    unittest.main()
