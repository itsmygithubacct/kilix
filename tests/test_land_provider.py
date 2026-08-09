import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-land-desktop.sh"
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


class KilixLandProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.source_home = self.home / "gpu_terminal"
        self.home.mkdir()
        self.source_home.mkdir()
        self.remote = self.root / "kilix-land-desktop-origin"
        self.checkout = (
            self.source_home / "kilix-desktops" / "kilix-land-desktop"
        )
        self._make_remote()
        self.ref = run(
            ["git", "rev-parse", "HEAD"], cwd=self.remote
        ).stdout.strip()
        self.env = os.environ.copy()
        for key in tuple(self.env):
            if key.startswith("KILIX_LAND_DESKTOP_"):
                self.env.pop(key)
        self.env.update({
            "HOME": str(self.home),
            "GPU_TERMINAL_SOURCE_HOME": str(self.source_home),
            "KILIX95_DIR": str(
                self.source_home / "kilix-desktops" / "kilix-95"
            ),
            "KILIX95_PROJECT_HOME": "",
            "KILIX_LAND_DESKTOP_DIR": str(self.checkout),
            "KILIX_LAND_DESKTOP_REPO": str(self.remote),
            "KILIX_LAND_DESKTOP_AUTO_INSTALL": "1",
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
            "  printf '%s|%s\\n' \"${KILIX_LAND_DESKTOP_ASSETS:-}\" "
            "\"${PATH%%:*}\"\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"${1:-}\" = --version ]; then\n"
            "  printf 'kilix-land-desktop fixture\\n'\n"
            "  exit 0\n"
            "fi\n"
            "printf 'kilix-land-desktop fixture:%s\\n' \"$*\"\n"
        )
        runner.chmod(0o755)
        (self.remote / "Makefile").write_text(
            "all: kilix-land-desktop\n\n"
            "kilix-land-desktop: runner\n"
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
        result = self._install(KILIX_LAND_DESKTOP_REF=self.ref)
        self.assertEqual(result.returncode, 0, result.stderr)
        binary = self.checkout / "kilix-land-desktop"
        self.assertEqual(result.stdout.strip(), str(binary))
        self.assertTrue(os.access(binary, os.X_OK))
        self.assertEqual(
            run(["git", "rev-parse", "HEAD"], cwd=self.checkout).stdout.strip(),
            self.ref,
        )
        self.assertEqual(
            run([binary, "hello"]).stdout.strip(),
            "kilix-land-desktop fixture:hello",
        )

    def test_existing_development_checkout_is_not_reset(self):
        first = self._install(KILIX_LAND_DESKTOP_REF=self.ref)
        self.assertEqual(first.returncode, 0, first.stderr)
        runner = self.checkout / "runner"
        runner.write_text(
            "#!/bin/sh\n"
            "printf 'local development:%s\\n' \"$*\"\n"
        )
        runner.chmod(0o755)
        rebuilt = self.checkout / "kilix-land-desktop"
        newer = rebuilt.stat().st_mtime_ns + 1_000_000_000
        os.utime(runner, ns=(newer, newer))

        env = dict(self.env)
        env.pop("KILIX_LAND_DESKTOP_REF", None)
        result = run([INSTALLER, "--print-path"], env=env, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("local development", runner.read_text())
        self.assertEqual(
            run([
                self.checkout / "kilix-land-desktop", "kept"
            ]).stdout.strip(),
            "local development:kept",
        )

    def test_first_use_download_can_be_disabled(self):
        result = self._install(
            KILIX_LAND_DESKTOP_REF=self.ref,
            KILIX_LAND_DESKTOP_AUTO_INSTALL="0",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KILIX_LAND_DESKTOP_AUTO_INSTALL=1", result.stderr)
        self.assertFalse(self.checkout.exists())

    def test_missing_former_default_is_rehomed_to_desktop_umbrella(self):
        legacy = self.source_home / "kilix-land-desktop"
        result = self._install(
            KILIX_LAND_DESKTOP_DIR=legacy,
            KILIX_LAND_DESKTOP_REF=self.ref,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(legacy.exists())
        self.assertTrue((self.checkout / "kilix-land-desktop").is_file())

    def test_existing_checkout_origin_is_verified(self):
        first = self._install(KILIX_LAND_DESKTOP_REF=self.ref)
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

    def test_settings_offer_all_named_native_providers(self):
        settings = DESKTOP_SETTINGS.read_text()
        for provider in ('"cap"', '"tui"', '"land"'):
            self.assertIn(provider, settings)

    def test_launcher_names_the_land_provider(self):
        launcher = LAUNCHER.read_text()
        self.assertIn("land|kilix-land|kilix-land-desktop)", launcher)
        self.assertIn("_kilix_land_ensure", launcher)
        self.assertIn(
            "use auto, builtin, external, xp, cap, tui, land, icewm, command, or none",
            launcher,
        )

    def test_kilix_land_shortcut_uses_native_provider_and_asset_root(self):
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
            "KILIX_LAND_DESKTOP_REF": self.ref,
            "KILIX_IN_OVERLAY": "1",
        })
        result = run([LAUNCHER, "land", "environment"], env=env, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip(),
            f"{self.checkout}|{ROOT}",
        )


if __name__ == "__main__":
    unittest.main()
