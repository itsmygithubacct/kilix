import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly
# rather than relying on either style's import roots.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402



ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-cap.sh"
LAUNCHER = ROOT / "kilix"
DESKTOP_SETTINGS = ROOT / "desktop" / "apps" / "settings.py"


def run(argv, *, cwd=None, env=None, check=True):
    return subprocess.run(
        [str(item) for item in argv],
        cwd=cwd,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )


class KilixCapProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.source_home = self.home / "gpu_terminal"
        self.home.mkdir()
        self.source_home.mkdir()
        self.remote = self.root / "kilix-cap-origin"
        self.checkout = (
            self.source_home / "kilix-desktops" / "kilix-cap"
        )
        self._make_remote()
        self.ref = run(
            ["git", "rev-parse", "HEAD"], cwd=self.remote
        ).stdout.strip()
        self.env = sandbox_env(**{
            "HOME": str(self.home),
            "GPU_TERMINAL_SOURCE_HOME": str(self.source_home),
            "KILIX95_DIR": str(
                self.source_home / "kilix-desktops" / "kilix-95"
            ),
            "KILIX95_PROJECT_HOME": "",
            "KILIX_CAP_DIR": str(self.checkout),
            "KILIX_CAP_REPO": str(self.remote),
            "KILIX_CAP_AUTO_INSTALL": "1",
        })

    def tearDown(self):
        self.temp.cleanup()

    def _make_remote(self):
        self.remote.mkdir()
        run(["git", "init", "-b", "main"], cwd=self.remote)
        runner = self.remote / "runner"
        runner.write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = environment ]; then\n"
            "  printf '%s|%s\\n' \"${KILIX95_PROJECT_HOME:-}\" \"${PATH%%:*}\"\n"
            "  exit 0\n"
            "fi\n"
            "printf 'kilix-cap fixture:%s\\n' \"$*\"\n"
        )
        runner.chmod(0o755)
        (self.remote / "Makefile").write_text(
            "all: bin/kilix-cap\n\n"
            "bin/kilix-cap: runner\n"
            "\tmkdir -p bin\n"
            "\tcp runner $@\n"
            "\tchmod 0755 $@\n"
        )
        run(["git", "add", "Makefile", "runner"], cwd=self.remote)
        run([
            "git",
            "-c", "user.name=itsmygithubacct",
            "-c", "user.email=itsmygithubacct@users.noreply.github.com",
            "commit", "-m", "Add fixture desktop",
        ], cwd=self.remote)

    def _install(self, **updates):
        env = dict(self.env)
        env.update({key: str(value) for key, value in updates.items()})
        return run([INSTALLER, "--print-path"], env=env, check=False)

    def test_first_use_clones_exact_ref_and_builds(self):
        result = self._install(KILIX_CAP_REF=self.ref)
        self.assertEqual(result.returncode, 0, result.stderr)
        binary = self.checkout / "bin" / "kilix-cap"
        self.assertEqual(result.stdout.strip(), str(binary))
        self.assertTrue(os.access(binary, os.X_OK))
        self.assertEqual(
            run(["git", "rev-parse", "HEAD"], cwd=self.checkout).stdout.strip(),
            self.ref,
        )
        self.assertEqual(
            run([binary, "hello"]).stdout.strip(),
            "kilix-cap fixture:hello",
        )

    def test_existing_development_checkout_is_not_reset(self):
        first = self._install(KILIX_CAP_REF=self.ref)
        self.assertEqual(first.returncode, 0, first.stderr)
        runner = self.checkout / "runner"
        runner.write_text(
            "#!/bin/sh\n"
            "printf 'local development:%s\\n' \"$*\"\n"
        )
        runner.chmod(0o755)

        env = dict(self.env)
        env.pop("KILIX_CAP_REF", None)
        result = run([INSTALLER, "--print-path"], env=env, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("local development", runner.read_text())
        self.assertEqual(
            run([self.checkout / "bin" / "kilix-cap", "kept"]).stdout.strip(),
            "local development:kept",
        )

    def test_first_use_download_can_be_disabled(self):
        result = self._install(
            KILIX_CAP_REF=self.ref,
            KILIX_CAP_AUTO_INSTALL="0",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KILIX_CAP_AUTO_INSTALL=1", result.stderr)
        self.assertFalse(self.checkout.exists())

    def test_missing_former_default_is_rehomed_to_desktop_umbrella(self):
        legacy = self.source_home / "kilix-cap"
        result = self._install(
            KILIX_CAP_DIR=legacy,
            KILIX_CAP_REF=self.ref,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(legacy.exists())
        self.assertTrue((self.checkout / "bin" / "kilix-cap").is_file())

    def test_existing_checkout_origin_is_verified(self):
        first = self._install(KILIX_CAP_REF=self.ref)
        self.assertEqual(first.returncode, 0, first.stderr)
        run([
            "git", "remote", "set-url", "origin",
            str(self.root / "different-origin"),
        ], cwd=self.checkout)
        result = self._install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected", result.stderr)

    def test_default_download_ref_is_immutable(self):
        result = run([INSTALLER, "--print-ref"], env=self.env)
        self.assertRegex(result.stdout.strip(), r"^[0-9a-f]{40}$")

    def test_settings_offer_cap_provider(self):
        settings = DESKTOP_SETTINGS.read_text()
        self.assertIn(
            '["auto", "builtin", "external", "cap", "tui", "land", "command",',
            settings,
        )

    def test_kilix_cap_shortcut_uses_native_provider(self):
        storage = self.home / ".local" / "gpu_terminal" / "kilix"
        engine_bin = storage / "prebuilt" / "kitty.app" / "bin"
        engine_bin.mkdir(parents=True)
        for name in ("kitty", "kitten"):
            path = engine_bin / name
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        env = dict(self.env)
        env.update({
            "GPU_TERMINAL_HOME": str(self.home / ".local" / "gpu_terminal"),
            "GPU_TERMINAL_SETTINGS_FILE": str(
                self.home / ".local" / "gpu_terminal" / "settings.conf"
            ),
            "KILIX_STORAGE_HOME": str(storage),
            "KILIX_CONFIG_HOME": str(storage / "config"),
            "KILIX_STATE_DIRECTORY": str(storage / "state"),
            "KILIX_CACHE_HOME": str(storage / "cache"),
            "KILIX_SESSION_HOME": str(storage / "session"),
            "KILIX_DATA_HOME": str(storage / "data"),
            "KILIX_BUILD_DIRECTORY": str(storage / "build"),
            "KILIX_PREBUILT_HOME": str(storage / "prebuilt" / "kitty.app"),
            "KILIX_CONFIG_DIRECTORY": str(storage / "config"),
            "KILIX_CAP_REF": self.ref,
            "KILIX_IN_OVERLAY": "1",
        })
        result = run([LAUNCHER, "cap", "environment"], env=env, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            f"{self.source_home / 'kilix-desktops' / 'kilix-95'}|{ROOT}",
        )


if __name__ == "__main__":
    unittest.main()
