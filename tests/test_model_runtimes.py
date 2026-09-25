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
YOLOX = ROOT / "scripts" / "install-yolox.sh"


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


class YoloxInstaller(unittest.TestCase):
    def _run(self, *args, **extra):
        with tempfile.TemporaryDirectory() as scratch:
            environment = sandbox_env(
                KILIX_YOLOX_DIR=os.path.join(scratch, "yolox"),
                KILIX_YOLOX_SRC=os.path.join(scratch, "no-such-module"),
                KILIX_YOLOX_AUTO_INSTALL="0",
                **extra)
            return subprocess.run([str(YOLOX), *args], env=environment,
                                  capture_output=True, text=True,
                                  check=False, timeout=120)

    def test_the_list_offers_the_runtime(self):
        row = next((r for r in _install_rows() if r.get("id") == "yolox"), None)
        self.assertIsNotNone(row, "no yolox runtime row")
        self.assertEqual(row["kind"], "runtime")
        self.assertIsInstance(row["installed"], bool)

    def test_it_is_executable(self):
        self.assertTrue(os.access(YOLOX, os.X_OK))

    def test_check_reports_a_machine_it_has_never_run_on(self):
        result = self._run("--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not installed", result.stdout)
        self.assertIn("missing", result.stdout)

    def test_it_refuses_a_broad_or_relative_directory(self):
        for directory, message in ((os.path.expanduser("~"), "broad"),
                                   ("/", "broad"),
                                   ("runtimes/yolox", "absolute path")):
            environment = sandbox_env(KILIX_YOLOX_DIR=directory)
            result = subprocess.run([str(YOLOX), "--check"], env=environment,
                                    capture_output=True, text=True,
                                    check=False, timeout=120)
            self.assertEqual(result.returncode, 1, directory)
            self.assertIn(message, result.stderr)

    def test_the_model_name_is_restricted_to_the_checksummed_three(self):
        result = self._run("--check", KILIX_YOLOX_MODEL="../evil")
        self.assertEqual(result.returncode, 1)
        self.assertIn("KILIX_YOLOX_MODEL must be", result.stderr)

    def test_a_missing_module_is_refused_before_anything_is_built(self):
        result = self._run("--install", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not installed", result.stderr)

    def test_remove_on_a_missing_runtime_is_not_an_error(self):
        self.assertEqual(self._run("--remove").returncode, 0)

    def test_upgrade_before_install_says_so(self):
        result = self._run("--upgrade")
        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing installed", result.stderr)

    def test_the_verb_documents_itself(self):
        result = subprocess.run([str(ROOT / "kilix"), "yolox", "--help"],
                                capture_output=True, text=True, check=False,
                                timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        for word in ("install", "check", "update", "remove", "Apache-2.0"):
            self.assertIn(word, result.stdout)


def _fake_yolox_module(scratch: str) -> str:
    """A kilix-yolox tree with just the files the installer looks for."""
    module = os.path.join(scratch, "kilix-yolox")
    os.makedirs(os.path.join(module, "tools"))
    os.makedirs(os.path.join(module, "models"))
    for tool in ("kilix-yolox-detect", "kilix-yolox-cut"):
        with open(os.path.join(module, "tools", tool), "w") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
    return module


class YoloxLicence(unittest.TestCase):
    """The weights are a kilix-content asset; consent is the authority's."""

    def test_the_licence_cannot_be_passed_with_yes(self):
        with tempfile.TemporaryDirectory() as scratch:
            environment = sandbox_env(
                HOME=os.path.join(scratch, "home"),
                KILIX_YOLOX_DIR=os.path.join(scratch, "yolox"),
                KILIX_YOLOX_SRC=_fake_yolox_module(scratch),
                KILIX_YOLOX_TRUST_EXISTING_CHECKOUT="1")
            result = subprocess.run(
                [str(YOLOX), "--install", "--yes"], env=environment,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                check=False, timeout=120)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("the YOLOX licence needs your typed agreement", result.stderr)
            # Refused before anything was built: no virtualenv exists.
            self.assertFalse(
                os.path.exists(os.path.join(scratch, "yolox", "venv")))

    def test_it_no_longer_fetches_weights_itself(self):
        installer = YOLOX.read_text(encoding="utf-8")
        self.assertNotIn("kilix-yolox-fetch", installer)
        self.assertIn('models install "$KILIX_YOLOX_MODEL"', installer)


class LookOffersTheDetector(unittest.TestCase):
    def _look(self, *args, detector=None):
        """Run `kilix look` on a pty, answering the offer with 'n'."""
        import pty
        import select
        with tempfile.TemporaryDirectory() as scratch:
            bin_dir = os.path.join(scratch, "bin")
            os.makedirs(bin_dir)
            fake = os.path.join(bin_dir, "kilix-look")
            with open(fake, "w") as handle:
                handle.write("#!/bin/sh\necho FAKE-LOOK-RAN\n")
            os.chmod(fake, 0o755)
            extra = {} if detector is None else {"KILIX_OBJECT_DETECTOR": detector}
            environment = sandbox_env(
                HOME=os.path.join(scratch, "home"),
                PATH=bin_dir + os.pathsep + os.environ["PATH"], **extra)
            os.makedirs(environment["HOME"])
            pid, master = pty.fork()
            if pid == 0:
                os.execve(str(ROOT / "kilix"),
                          [str(ROOT / "kilix"), "look", *args], environment)
            output = b""
            answered = False
            while True:
                ready, _, _ = select.select([master], [], [], 30)
                if not ready:
                    break
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                if b"[y/N]" in output and not answered:
                    os.write(master, b"n\n")
                    answered = True
            os.waitpid(pid, 0)
            return output.decode("utf-8", "replace")

    def test_a_bare_machine_is_offered_yolox(self):
        output = self._look("image", "photo.jpg")
        self.assertIn("install YOLOX now?", output)
        # Declining changes nothing: the analyzer still runs.
        self.assertIn("FAKE-LOOK-RAN", output)

    def test_a_configured_detector_is_not_second_guessed(self):
        output = self._look("image", "photo.jpg", detector="/bin/true")
        self.assertNotIn("install YOLOX now?", output)
        self.assertIn("FAKE-LOOK-RAN", output)

    def test_subcommands_that_run_no_model_are_not_offered_it(self):
        output = self._look("classes")
        self.assertNotIn("install YOLOX now?", output)


class YoloxPin(unittest.TestCase):
    """The module is delivered at an immutable commit, first use and update."""

    def _git(self, *argv, cwd=None):
        environment = sandbox_env(
            GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        return subprocess.run(["git", *argv], cwd=cwd, env=environment,
                              check=True, capture_output=True,
                              text=True).stdout.strip()

    def _origin(self, scratch):
        origin = os.path.join(scratch, "origin")
        self._git("init", "-q", "-b", "main", origin)
        refs = []
        for name in ("old", "new"):
            with open(os.path.join(origin, "marker"), "w") as handle:
                handle.write(name)
            self._git("add", "-A", cwd=origin)
            self._git("commit", "-qm", name, cwd=origin)
            refs.append(self._git("rev-parse", "HEAD", cwd=origin))
        return origin, refs

    def _run(self, scratch, origin, ref, **extra):
        environment = sandbox_env(
            HOME=os.path.join(scratch, "home"),
            KILIX_YOLOX_DIR=os.path.join(scratch, "yolox"),
            KILIX_YOLOX_SRC=os.path.join(scratch, "sources", "kilix-yolox"),
            GPU_TERMINAL_SOURCE_HOME=os.path.join(scratch, "sources"),
            KILIX_YOLOX_REPO=origin, KILIX_YOLOX_REF=ref, **extra)
        # --check never resolves the source; --install would go on to the
        # licence.  A non-terminal --install stops there, after resolving.
        return subprocess.run(
            [str(YOLOX), "--install", "--yes"], env=environment,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=False, timeout=120)

    def test_the_default_is_a_full_commit_sha(self):
        result = subprocess.run([str(YOLOX), "--print-ref"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout.strip(), r"^[0-9a-f]{40}$")
        self.assertRegex(YOLOX.read_text(encoding="utf-8"),
                         r"(?m)^KILIX_YOLOX_DEFAULT_REF=[0-9a-f]{40}$")

    def test_a_mutable_ref_is_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            result = self._run(scratch, scratch, "main")
            self.assertEqual(result.returncode, 1)
            self.assertIn("full 40-character commit SHA", result.stderr)

    def test_first_use_lands_on_the_pinned_commit(self):
        with tempfile.TemporaryDirectory() as scratch:
            origin, (old, new) = self._origin(scratch)
            result = self._run(scratch, origin, old)
            # Stops at the licence, which is after the source was prepared.
            self.assertIn("typed agreement", result.stderr, result.stderr)
            checkout = os.path.join(scratch, "sources", "kilix-yolox")
            self.assertEqual(self._git("rev-parse", "HEAD", cwd=checkout), old)

    def test_an_existing_clean_checkout_is_moved_to_the_pin(self):
        with tempfile.TemporaryDirectory() as scratch:
            origin, (old, new) = self._origin(scratch)
            self._run(scratch, origin, old)
            result = self._run(scratch, origin, new)
            checkout = os.path.join(scratch, "sources", "kilix-yolox")
            self.assertEqual(self._git("rev-parse", "HEAD", cwd=checkout), new)
            self.assertIn("advanced", result.stderr)

    def test_a_dirty_checkout_is_kept_and_says_so(self):
        with tempfile.TemporaryDirectory() as scratch:
            origin, (old, new) = self._origin(scratch)
            self._run(scratch, origin, old)
            checkout = os.path.join(scratch, "sources", "kilix-yolox")
            with open(os.path.join(checkout, "marker"), "w") as handle:
                handle.write("edited")
            result = self._run(scratch, origin, new)
            self.assertEqual(self._git("rev-parse", "HEAD", cwd=checkout), old)
            self.assertIn("was NOT installed", result.stderr)

    def test_a_foreign_origin_is_refused(self):
        with tempfile.TemporaryDirectory() as scratch:
            origin, (old, new) = self._origin(scratch)
            self._run(scratch, origin, old)
            result = self._run(scratch, os.path.join(scratch, "elsewhere"), old)
            self.assertEqual(result.returncode, 1)
            self.assertIn("expected", result.stderr)
