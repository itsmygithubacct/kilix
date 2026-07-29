"""`kilix bonsai` resolves the model store, and installs only a pinned one.

Two failure modes worth closing here.

The installer must refuse an unpinned tree. Kilix Bonsai is not published yet,
so its ref is the literal `unset`; a branch name would install whatever HEAD
happened to be at install time and would do it silently. Refusing is the louder
and cheaper failure, and it is the same position `install-kilix-voice.sh` takes.

The dispatch must never download weights as a side effect. `kilix bonsai` opens
a UI; the eleven gigabytes of models behind it are a separate, confirmed
decision, so no path through the launcher may start a transfer.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-bonsai.sh"
LAUNCHER = ROOT / "kilix"


def run(argv, **environment):
    env = dict(os.environ, **environment)
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          timeout=120)


class InstallerTests(unittest.TestCase):
    def test_help_is_free_of_side_effects(self):
        result = run([str(INSTALLER), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)

    def test_print_refs_reports_the_pin_without_installing(self):
        with tempfile.TemporaryDirectory() as home:
            result = run([str(INSTALLER), "--print-refs"],
                         KILIX_STORAGE_HOME=os.path.join(home, "storage"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("kilix-bonsai=", result.stdout)
            self.assertFalse(os.path.exists(os.path.join(home, "storage")))

    def test_an_unset_ref_refuses_rather_than_tracking_a_branch(self):
        with tempfile.TemporaryDirectory() as home:
            result = run([str(INSTALLER)],
                         KILIX_STORAGE_HOME=os.path.join(home, "storage"),
                         KILIX_BONSAI_REF="unset")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no published commit", result.stderr)

    def test_a_branch_name_is_refused_as_a_ref(self):
        with tempfile.TemporaryDirectory() as home:
            result = run([str(INSTALLER)],
                         KILIX_STORAGE_HOME=os.path.join(home, "storage"),
                         KILIX_BONSAI_REF="main")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("40-character commit SHA", result.stderr)

    def test_the_installer_never_downloads_weights(self):
        # Installing the command and fetching gigabytes of models are separate
        # decisions; a `kilix update` must not be able to start a transfer.
        body = INSTALLER.read_text()
        for token in ("huggingface", "pull.sh", "curl ", "wget "):
            self.assertNotIn(token, body)


class DispatchTests(unittest.TestCase):
    def test_bonsai_help_needs_no_install_and_no_network(self):
        result = run([str(LAUNCHER), "bonsai", "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BitNet model store", result.stdout)

    def test_the_help_names_the_shared_dictation_model(self):
        # The one thing a reader is most likely to get wrong is thinking this
        # is a second copy of the speech model.
        result = run([str(LAUNCHER), "bonsai", "--help"])
        self.assertIn("vibevoice-asr-bitnet", result.stdout)

    def test_the_resolver_prefers_an_installed_command(self):
        with tempfile.TemporaryDirectory() as home:
            binary = Path(home) / "kilix-bonsai"
            binary.write_text("#!/bin/sh\nprintf 'installed %s\\n' \"$*\"\n")
            binary.chmod(0o755)
            result = run([str(LAUNCHER), "bonsai", "marker"],
                         PATH=f"{home}:{os.environ['PATH']}")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "installed marker")

    def test_a_source_checkout_is_used_when_nothing_is_installed(self):
        with tempfile.TemporaryDirectory() as home:
            source_home = Path(home) / "source home [literal]"
            entry = source_home / "kilix-bonsai" / "tools" / "kilix-bonsai"
            entry.mkdir(parents=True)
            (entry / "main.py").write_text(
                "import sys\nprint('checkout', *sys.argv[1:])\n")
            result = run([str(LAUNCHER), "bonsai", "marker"],
                         GPU_TERMINAL_SOURCE_HOME=str(source_home),
                         PATH="/usr/bin:/bin")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "checkout marker")

    def test_with_neither_it_reaches_the_installer_and_stops_there(self):
        with tempfile.TemporaryDirectory() as home:
            # GPU_TERMINAL_HOME rather than KILIX_STORAGE_HOME: the launcher
            # derives every writable root from it and refuses a set that is not
            # self-consistent.
            result = run([str(LAUNCHER), "bonsai"],
                         GPU_TERMINAL_SOURCE_HOME=os.path.join(home, "src"),
                         GPU_TERMINAL_HOME=os.path.join(home, "state"),
                         KILIX_BONSAI_REF="unset", PATH="/usr/bin:/bin")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no published commit", result.stderr)

    def test_the_launcher_documents_the_subcommand(self):
        header = LAUNCHER.read_text().split("set -euo pipefail")[0]
        self.assertRegex(header, r"kilix bonsai")


if __name__ == "__main__":
    unittest.main()
