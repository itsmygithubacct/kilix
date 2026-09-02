"""The launcher's wiring for the Kilix IceWM desktop provider.

Mirrors test_land_provider.py: these assert the launcher *offers* the provider
and prepares it safely. They read the launcher as text rather than running a
desktop, so they need neither an X display nor a built IceWM.
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LAUNCHER = os.path.join(ROOT, "kilix")
INSTALLER = os.path.join(ROOT, "scripts", "install-kilix-icewm.sh")


def run(argv, *, cwd=None, env=None, check=True):
    return subprocess.run(
        [str(item) for item in argv],
        cwd=cwd,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


def launcher_text():
    with open(LAUNCHER, encoding="utf-8") as fh:
        return fh.read()


class KilixIceWMProviderTests(unittest.TestCase):
    def setUp(self):
        self.text = launcher_text()

    def test_subcommand_selects_the_provider(self):
        self.assertIn("icewm|kilix-icewm)", self.text)
        self.assertIn("KILIX_DESKTOP_PROVIDER=icewm", self.text)

    def test_launcher_names_the_icewm_provider(self):
        self.assertIn(
            "use auto, builtin, external, xp, cap, tui, land, icewm, command, or none",
            self.text,
        )

    def test_provider_directory_is_under_the_desktop_umbrella(self):
        self.assertIn("kilix-desktops/kilix-icewm", self.text)

    def test_usage_advertises_the_desktop(self):
        self.assertIn("./kilix icewm", self.text)

    def test_ensure_function_exists_and_is_dispatched(self):
        self.assertIn("_kilix_icewm_ensure() {", self.text)
        self.assertRegex(self.text, r"icewm\|kilix-icewm\)\s*\n\s*_kilix_icewm_ensure")

    def test_ensure_refuses_symlinked_installer_and_entry(self):
        # Same safety shape as cap/tui/land: a symlink in either position would
        # let a writable path redirect what the desktop executes.
        body = self.text.split("_kilix_icewm_ensure() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("-L", body)
        self.assertIn("_kilix_desktop_die", body)

    def test_status_reports_the_provider(self):
        self.assertIn("icewm (X window manager in a pane)", self.text)


class KilixIceWMInstallerTests(unittest.TestCase):
    def setUp(self):
        with open(INSTALLER, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_installer_is_executable_and_not_a_symlink(self):
        self.assertTrue(os.access(INSTALLER, os.X_OK))
        self.assertFalse(os.path.islink(INSTALLER))

    def test_requires_an_immutable_ref_by_default(self):
        self.assertRegex(
            self.text,
            r"(?m)^KILIX_ICEWM_DEFAULT_REF=[0-9a-f]{40}$",
        )
        self.assertIn("KILIX_ICEWM_ALLOW_MUTABLE_REF", self.text)
        self.assertIn("--print-ref", self.text)

    def test_installer_selects_the_tested_provider_revision(self):
        self.assertIn(
            "KILIX_ICEWM_DEFAULT_REF="
            "0b9f11b45fddc5370c37b00e9cd9e42ac5a5f6d7",
            self.text,
        )

    def test_refuses_a_symlinked_entry_point(self):
        self.assertIn('[ -L "$entry" ]', self.text)
        self.assertIn("provider did not supply a regular executable", self.text)

    def test_auto_install_can_be_declined(self):
        self.assertIn("KILIX_ICEWM_AUTO_INSTALL", self.text)
        self.assertIn("KILIX_ICEWM_AUTO_INSTALL=1", self.text)

    def test_existing_checkout_is_advanced_or_explicitly_kept(self):
        self.assertIn("advance_existing_checkout()", self.text)
        self.assertIn("KILIX_ICEWM_KEEP_EXISTING_CHECKOUT", self.text)
        self.assertIn("NOT installed", self.text)

    def test_build_is_deferred_to_the_desktop_checkout(self):
        # Kilix must not know how to build IceWM; that belongs to kilix-icewm.
        self.assertIn("build-icewm.sh", self.text)


class KilixIceWMInstallerDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.source_home = self.home / "sources"
        self.home.mkdir()
        self.source_home.mkdir()
        self.remote = self.root / "kilix-icewm-origin"
        self.checkout = (
            self.source_home / "kilix-desktops" / "kilix-icewm"
        )
        self._make_remote()
        self.ref = run(
            ["git", "rev-parse", "HEAD"], cwd=self.remote
        ).stdout.strip()
        self.env = sandbox_env(
            **{
                "HOME": str(self.home),
                "GPU_TERMINAL_SOURCE_HOME": str(self.source_home),
                "KILIX_ICEWM_DIR": str(self.checkout),
                "KILIX_ICEWM_REPO": str(self.remote),
                "KILIX_ICEWM_AUTO_INSTALL": "1",
                "KILIX_ICEWM_STORAGE_HOME": str(self.home / "icewm-data"),
            }
        )

    def tearDown(self):
        self.temp.cleanup()

    def _make_remote(self):
        self.remote.mkdir()
        run(["git", "init", "-b", "main"], cwd=self.remote)
        (self.remote / "bin").mkdir()
        entry = self.remote / "bin" / "kilix-icewm"
        entry.write_text("#!/bin/sh\nprintf 'fixture provider\\n'\n")
        entry.chmod(0o755)
        (self.remote / "scripts").mkdir()
        builder = self.remote / "scripts" / "build-icewm.sh"
        builder.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            'mkdir -p "$KILIX_ICEWM_STORAGE_HOME"\n'
            'printf prepared > "$KILIX_ICEWM_STORAGE_HOME/prepared"\n'
            'printf "/fixture/icewm-session\\n"\n'
        )
        run(["git", "add", "bin", "scripts"], cwd=self.remote)
        run(
            [
                "git",
                "-c",
                "user.name=itsmygithubacct",
                "-c",
                "user.email=itsmygithubacct@users.noreply.github.com",
                "commit",
                "-m",
                "Add fixture provider",
            ],
            cwd=self.remote,
        )

    def _install(self, **updates):
        env = dict(self.env)
        env.update({key: str(value) for key, value in updates.items()})
        return run([INSTALLER, "--print-path"], env=env, check=False)

    def test_first_use_clones_exact_ref_and_prepares(self):
        result = self._install(KILIX_ICEWM_REF=self.ref)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.checkout / "bin" / "kilix-icewm"
        self.assertEqual(result.stdout.strip(), str(entry))
        self.assertEqual(
            run(["git", "rev-parse", "HEAD"], cwd=self.checkout).stdout.strip(),
            self.ref,
        )
        self.assertEqual(
            (self.home / "icewm-data" / "prepared").read_text(), "prepared"
        )

    def test_first_use_download_can_be_disabled(self):
        result = self._install(
            KILIX_ICEWM_REF=self.ref,
            KILIX_ICEWM_AUTO_INSTALL="0",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KILIX_ICEWM_AUTO_INSTALL=1", result.stderr)
        self.assertFalse(self.checkout.exists())

    def test_existing_checkout_origin_is_verified(self):
        first = self._install(KILIX_ICEWM_REF=self.ref)
        self.assertEqual(first.returncode, 0, first.stderr)
        run(
            [
                "git",
                "remote",
                "set-url",
                "origin",
                str(self.root / "different-origin"),
            ],
            cwd=self.checkout,
        )
        result = self._install(KILIX_ICEWM_REF=self.ref)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected", result.stderr)


if __name__ == "__main__":
    unittest.main()
