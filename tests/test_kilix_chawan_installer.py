"""`kilix chawan` installs a pinned text browser, and asks before it grows.

The properties worth pinning down here are the ones a first-run install can
get quietly wrong.

The pin must be an immutable commit: this installer builds a browser from
source, and a branch name would compile whatever HEAD happened to be at
install time on every machine, differently.

Probing must stay free. `open-url` consults Chawan before it falls back to the
in-pane Chrome renderer, and that consultation happens on a path other
programs call — so `--print-installed` must answer without a toolchain, a
network, or a build, and must fail rather than install when nothing is there.

Growing must stay consented-to. Missing libssh2 costs SFTP and nothing else,
so the fallback order is: the packaged library, then a locally built one, then
a build without SFTP — and the middle step never happens unattended.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-chawan.sh"
POLICY = ROOT / "config" / "browser.sh"
SEED_CONFIG = ROOT / "config" / "chawan" / "config.toml"


def run(argv, **environment):
    env = dict(os.environ, **environment)
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          timeout=120)


class InstallerTests(unittest.TestCase):
    def test_help_is_free_of_side_effects(self):
        result = run([str(INSTALLER), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)

    def test_print_ref_reports_the_pin_without_installing(self):
        with tempfile.TemporaryDirectory() as home:
            sources = os.path.join(home, "sources")
            result = run([str(INSTALLER), "--print-ref"],
                         GPU_TERMINAL_SOURCE_HOME=sources)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stdout.strip(), r"^[0-9a-f]{40}$")
            self.assertFalse(os.path.exists(sources))

    def test_the_default_ref_is_a_pinned_commit(self):
        # A tag or branch here would make every machine's browser a different
        # build; only a full SHA makes the closure reproducible.
        body = INSTALLER.read_text()
        match = re.search(r"^KILIX_CHAWAN_DEFAULT_REF=(\S+)$", body,
                          re.MULTILINE)
        self.assertIsNotNone(match, "no KILIX_CHAWAN_DEFAULT_REF found")
        self.assertRegex(match.group(1), r"^[0-9a-f]{40}$")
        self.assertNotEqual(match.group(1), "0" * 40,
                            "the placeholder ref was never replaced with the "
                            "published commit")

    def test_the_nim_toolchain_is_pinned_and_checksummed(self):
        # The browser needs a newer Nim than distributions ship, so the
        # installer downloads one. An unverified download would be a remote
        # code path into every first run.
        body = INSTALLER.read_text()
        version = re.search(r"^KILIX_CHAWAN_NIM_VERSION=(\S+)$", body,
                            re.MULTILINE)
        self.assertIsNotNone(version, "no pinned Nim version found")
        self.assertRegex(version.group(1), r"^\d+\.\d+\.\d+$")
        checksums = re.findall(r"^nim_sha256_\w+=([0-9a-f]+)$", body,
                               re.MULTILINE)
        self.assertTrue(checksums, "no Nim checksums found")
        for checksum in checksums:
            self.assertEqual(len(checksum), 64, checksum)

    def test_print_installed_fails_quietly_when_nothing_is_built(self):
        # This is the branch `open-url` takes. It must not build, must not
        # download, and must not print a path it cannot honour.
        with tempfile.TemporaryDirectory() as home:
            directory = os.path.join(home, "sources", "kilix-apps",
                                     "kilix-chawan")
            result = run([str(INSTALLER), "--print-installed"],
                         KILIX_CHAWAN_DIR=directory,
                         GPU_TERMINAL_SOURCE_HOME=os.path.join(home, "sources"))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")
            self.assertFalse(os.path.exists(directory))

    def test_print_installed_reports_a_built_browser(self):
        with tempfile.TemporaryDirectory() as home:
            directory = os.path.join(home, "kilix-chawan")
            binary = os.path.join(directory, "target", "release", "bin", "cha")
            os.makedirs(os.path.dirname(binary))
            Path(binary).write_text("#!/bin/sh\nexit 0\n")
            os.chmod(binary, 0o755)
            result = run([str(INSTALLER), "--print-installed"],
                         KILIX_CHAWAN_DIR=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), binary)

    def test_auto_install_off_refuses_rather_than_downloading(self):
        with tempfile.TemporaryDirectory() as home:
            directory = os.path.join(home, "kilix-chawan")
            result = run([str(INSTALLER), "--print-path"],
                         KILIX_CHAWAN_DIR=directory,
                         KILIX_CHAWAN_AUTO_INSTALL="0")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not installed", result.stderr)
            self.assertFalse(os.path.exists(directory))

    def test_a_mutable_ref_is_refused_without_an_explicit_override(self):
        with tempfile.TemporaryDirectory() as home:
            result = run([str(INSTALLER), "--print-path"],
                         KILIX_CHAWAN_DIR=os.path.join(home, "kilix-chawan"),
                         KILIX_CHAWAN_REF="main")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("40-character commit SHA", result.stderr)

    def test_sftp_is_optional_and_never_built_unattended(self):
        # Non-interactive callers get the smallest install, not a surprise
        # second source build. The prompt is the only path that grows it.
        body = INSTALLER.read_text()
        self.assertIn("KILIX_CHAWAN_BUILD_LIBSSH2", body)
        self.assertIn("CHA_SFTP=0", body)
        # The packaged library is consulted before anything is built.
        self.assertLess(body.index("pkg-config --exists libssh2"),
                        body.index("download and build libssh2"))


class SeedConfigTests(unittest.TestCase):
    def test_images_are_on_because_kilix_can_draw_them(self):
        # Chawan ships images off. Turning them on is the whole reason this
        # browser suits a Kilix pane, so the seed must not inherit the default.
        body = SEED_CONFIG.read_text()
        self.assertRegex(body, r"(?m)^images = true$")

    def test_scripting_stays_off(self):
        body = SEED_CONFIG.read_text()
        self.assertRegex(body, r"(?m)^scripting = false$")

    def test_the_colour_mode_is_one_chawan_accepts(self):
        # "24bit" reads like a valid answer and is not one; Chawan rejects the
        # whole config file over it, which would break every launch.
        body = SEED_CONFIG.read_text()
        match = re.search(r"(?m)^color-mode = \"(.+)\"$", body)
        self.assertIsNotNone(match, "no color-mode found")
        self.assertIn(match.group(1),
                      ("monochrome", "ansi", "eight-bit", "true-color"))


class BrowserPolicyTests(unittest.TestCase):
    def test_chawan_sits_below_the_desktop_browsers(self):
        # The documented policy is that an installed desktop browser always
        # wins; Chawan only answers when none is present.
        launcher = (ROOT / "kilix").read_text()
        self.assertLess(launcher.index("_kilix_find_real_browser"),
                        launcher.index("_kilix_find_installed_chawan"))

    def test_the_probe_never_installs(self):
        # browser.sh must ask the cheap question. If it ever called
        # --print-path, opening a URL could block on a first-run compile.
        body = POLICY.read_text()
        self.assertIn("--print-installed", body)
        self.assertNotIn("--print-path", body)

    def test_an_absent_browser_falls_through(self):
        with tempfile.TemporaryDirectory() as home:
            script = (f'. "{POLICY}"\n'
                      '_kilix_find_installed_chawan && echo FOUND || echo NONE\n')
            result = run(["/bin/bash", "-c", script],
                         KILIX_HOME=str(ROOT),
                         KILIX_CHAWAN_DIR=os.path.join(home, "absent"))
            self.assertEqual(result.stdout.strip().splitlines()[-1], "NONE")


if __name__ == "__main__":
    unittest.main()
