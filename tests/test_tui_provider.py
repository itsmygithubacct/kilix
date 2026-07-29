import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-kilix-tui-utils.sh"
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


class KilixTuiProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.source_home = self.home / "gpu_terminal"
        self.home.mkdir()
        self.source_home.mkdir()
        self.remote = self.root / "kilix-tui-utils-origin"
        self.checkout = self.source_home / "kilix-tui-utils"
        self._make_remote()
        self.ref = run(
            ["git", "rev-parse", "HEAD"], cwd=self.remote
        ).stdout.strip()
        self.env = os.environ.copy()
        for key in tuple(self.env):
            if key.startswith("KILIX_TUI_UTILS_"):
                self.env.pop(key)
        self.env.update({
            "HOME": str(self.home),
            "GPU_TERMINAL_SOURCE_HOME": str(self.source_home),
            "KILIX_TUI_UTILS_DIR": str(self.checkout),
            "KILIX_TUI_UTILS_REPO": str(self.remote),
            "KILIX_TUI_UTILS_AUTO_INSTALL": "1",
            "KILIX_TUI_UTILS_PREFIX": str(self.home / ".local"),
        })

    def tearDown(self):
        self.temp.cleanup()

    def _make_remote(self):
        self.remote.mkdir()
        run(["git", "init", "-b", "main"], cwd=self.remote)
        entry = self.remote / "kilix-tui"
        entry.mkdir()
        (entry / "main.py").write_text(
            "import os, sys\n"
            "if sys.argv[1:] == ['environment']:\n"
            "    print(os.environ.get('KILIX_HOME', '') + '|'\n"
            "          + os.environ.get('PATH', '').split(':')[0])\n"
            "else:\n"
            "    print('kilix-tui fixture:' + ' '.join(sys.argv[1:]))\n"
        )
        installer = self.remote / "install.sh"
        installer.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "HERE=\"$(cd -- \"$(dirname -- \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
            "BIN=\"${KILIX_TUI_UTILS_PREFIX:-$HOME/.local}/bin\"\n"
            "mkdir -p \"$BIN\"\n"
            "printf '#!/bin/sh\\nexec python3 \"%s\" \"$@\"\\n' "
            "\"$HERE/kilix-tui/main.py\" > \"$BIN/kilix-tui\"\n"
            "chmod 0755 \"$BIN/kilix-tui\"\n"
        )
        installer.chmod(0o755)
        run(["git", "add", "install.sh", "kilix-tui/main.py"], cwd=self.remote)
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

    def test_first_use_clones_exact_ref_and_installs(self):
        result = self._install(KILIX_TUI_UTILS_REF=self.ref)
        self.assertEqual(result.returncode, 0, result.stderr)
        launcher = self.home / ".local" / "bin" / "kilix-tui"
        self.assertEqual(result.stdout.strip(), str(launcher))
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertEqual(
            run(["git", "rev-parse", "HEAD"], cwd=self.checkout).stdout.strip(),
            self.ref,
        )
        self.assertEqual(
            run([launcher, "hello"]).stdout.strip(),
            "kilix-tui fixture:hello",
        )

    def test_existing_development_checkout_is_not_reset(self):
        first = self._install(KILIX_TUI_UTILS_REF=self.ref)
        self.assertEqual(first.returncode, 0, first.stderr)
        entry = self.checkout / "kilix-tui" / "main.py"
        entry.write_text("print('local development')\n")

        env = dict(self.env)
        env.pop("KILIX_TUI_UTILS_REF", None)
        result = run([INSTALLER, "--print-path"], env=env, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        launcher = self.home / ".local" / "bin" / "kilix-tui"
        self.assertEqual(
            run([launcher]).stdout.strip(), "local development")

    def test_first_use_download_can_be_disabled(self):
        result = self._install(
            KILIX_TUI_UTILS_REF=self.ref,
            KILIX_TUI_UTILS_AUTO_INSTALL="0",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KILIX_TUI_UTILS_AUTO_INSTALL=1", result.stderr)
        self.assertFalse(self.checkout.exists())

    def test_existing_checkout_origin_is_verified(self):
        first = self._install(KILIX_TUI_UTILS_REF=self.ref)
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

    def test_settings_offer_tui_provider(self):
        settings = DESKTOP_SETTINGS.read_text()
        self.assertIn('"tui"', settings)

    def test_launcher_names_the_tui_provider(self):
        launcher = LAUNCHER.read_text()
        self.assertIn("tui|kilix-tui)", launcher)
        self.assertIn("_kilix_tui_ensure", launcher)
        self.assertIn(
            "use auto, builtin, external, xp, cap, tui, command, or none",
            launcher)

    def test_kilix_tui_shortcut_uses_text_provider(self):
        storage = self.home / ".local" / "gpu_terminal" / "kilix"
        engine_bin = storage / "prebuilt" / "kitty.app" / "bin"
        engine_bin.mkdir(parents=True)
        for name in ("kitty", "kitten"):
            path = engine_bin / name
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        env = dict(self.env)
        env.update({
            # An installed kilix-tui on the workstation must not shadow the
            # fixture: the ensure prefers PATH, so the test pins PATH.
            "PATH": "/usr/local/bin:/usr/bin:/bin",
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
            "KILIX_TUI_UTILS_REF": self.ref,
            "KILIX_IN_OVERLAY": "1",
        })
        result = run([LAUNCHER, "kilix-tui", "environment"], env=env,
                     check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{ROOT}|{ROOT}")

    def test_desktop_accepts_a_provider_argument(self):
        launcher = LAUNCHER.read_text()
        index = launcher.index('if [ "${1:-}" = "desktop" ]')
        block = launcher[index:index + 1200]
        for alias in ("95|kilix-95", "xp|kilix-xp", "cap|kilix-cap|mansion",
                      "tui|kilix-tui"):
            self.assertIn(alias, block)


if __name__ == "__main__":
    unittest.main()
