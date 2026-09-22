"""`kilix bonsai` resolves the model store, and installs only a pinned one.

Two failure modes worth closing here.

The installer must refuse an unpinned tree. Its shipped ref is the published
commit Kilix selected; a branch name would install whatever HEAD happened to
be at install time and would do it silently. The explicit `unset` test keeps
the refusal path covered.

The dispatch must never download weights as a side effect. `kilix bonsai` opens
a UI; the eleven gigabytes of models behind it are a separate, confirmed
decision, so no path through the launcher may start a transfer.
"""
import os
from pathlib import Path
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402



ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-bonsai.sh"
LAUNCHER = ROOT / "kilix"


def run(argv, **environment):
    env = sandbox_env(**environment)
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

    def test_the_default_ref_is_a_pinned_commit(self):
        # The closure is only immutable if the shipped default is a full SHA;
        # `unset` remains reachable as an explicit override, but shipping it
        # would mean every machine falls back to refusing to install.
        body = INSTALLER.read_text()
        match = re.search(r'KILIX_BONSAI_REF="\$\{KILIX_BONSAI_REF:-(.+?)\}"',
                          body)
        self.assertIsNotNone(match, "no KILIX_BONSAI_REF default found")
        self.assertRegex(match.group(1), r"^[0-9a-f]{40}$")

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

    def test_a_source_checkout_never_shadows_the_pinned_closure(self):
        # A working tree is not the pinned closure. This is the one tool here
        # whose job is fetching verified multi-gigabyte artifacts, so a machine
        # that happens to have a source directory must not quietly run
        # different code from every installed system.
        with tempfile.TemporaryDirectory() as home:
            source_home = Path(home) / "source home [literal]"
            entry = (
                source_home
                / "kilix-apps"
                / "kilix-bonsai"
                / "tools"
                / "kilix-bonsai"
            )
            entry.mkdir(parents=True)
            (entry / "main.py").write_text(
                "import sys\nprint('checkout', *sys.argv[1:])\n")
            # KILIX_BONSAI_REF=unset stops the installer before it clones, so
            # this asserts the branch taken without reaching the network.
            # KILIX_BONSAI_PREFIX points into the sandbox because the
            # resolver checks the install prefix after PATH — the machine
            # running this suite may really have the tool installed.
            result = run([str(LAUNCHER), "bonsai", "marker"],
                         GPU_TERMINAL_SOURCE_HOME=str(source_home),
                         GPU_TERMINAL_HOME=os.path.join(home, "state"),
                         KILIX_BONSAI_PREFIX=os.path.join(home, "prefix"),
                         KILIX_BONSAI_REF="unset", PATH="/usr/bin:/bin")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("checkout marker", result.stdout)
            self.assertIn("no published commit", result.stderr)

    def test_without_an_installed_command_it_reaches_the_installer(self):
        with tempfile.TemporaryDirectory() as home:
            # GPU_TERMINAL_HOME rather than KILIX_STORAGE_HOME: the launcher
            # derives every writable root from it and refuses a set that is not
            # self-consistent.
            result = run([str(LAUNCHER), "bonsai"],
                         GPU_TERMINAL_SOURCE_HOME=os.path.join(home, "src"),
                         GPU_TERMINAL_HOME=os.path.join(home, "state"),
                         KILIX_BONSAI_PREFIX=os.path.join(home, "prefix"),
                         KILIX_BONSAI_REF="unset", PATH="/usr/bin:/bin")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no published commit", result.stderr)

    def test_a_prefix_install_off_path_is_run_not_reinstalled(self):
        # Desktop launch contexts run without ~/.local/bin on PATH; an
        # installed closure PATH cannot see must be run, not shadowed by a
        # reinstall — the explicit installed-first Bonsai resolution order.
        with tempfile.TemporaryDirectory() as home:
            bindir = Path(home) / "prefix" / "bin"
            bindir.mkdir(parents=True)
            binary = bindir / "kilix-bonsai"
            binary.write_text("#!/bin/sh\nprintf 'prefix %s\\n' \"$*\"\n")
            binary.chmod(0o755)
            result = run([str(LAUNCHER), "bonsai", "marker"],
                         KILIX_BONSAI_PREFIX=os.path.join(home, "prefix"),
                         PATH="/usr/bin:/bin")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "prefix marker")

    def test_the_launcher_documents_the_subcommand(self):
        header = LAUNCHER.read_text().split("set -euo pipefail")[0]
        self.assertRegex(header, r"kilix bonsai")


if __name__ == "__main__":
    unittest.main()
