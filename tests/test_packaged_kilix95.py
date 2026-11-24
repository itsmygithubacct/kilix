"""The kilix-95 Debian package's provider tree, as `kilix desktop` sees it.

The package installs Kilix 95 in KILIX_PACKAGED_KILIX95_HOME with a PACKAGED
marker and no checkout. The launcher uses it only when there is no provider
checkout at KILIX95_DIR, and trusts it without the checkout's origin and ref
checks only when the tree and its main.py are owned by root and writable by
nobody else.

Root ownership comes from a user namespace: under `unshare -r` the files this
test creates are owned by uid 0. Run without it, the same files belong to the
desktop user and must be refused.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
UNSHARE = shutil.which("unshare")


def user_namespaces_work():
    if not UNSHARE:
        return False
    probe = subprocess.run([UNSHARE, "-r", "id", "-u"], capture_output=True, text=True)
    return probe.returncode == 0 and probe.stdout.strip() == "0"


class PackagedKilix95Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="packaged-kilix95-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.inventory = self.base / "inventory.json"
        self.inventory.write_text(json.dumps([{"tabs": [{"windows": []}]}]))
        self.trace = self.base / "terminal.jsonl"
        self.packaged = self.base / "usr/share/kilix-95"
        self.packaged.mkdir(parents=True)
        # The real provider contract and source files; the terminal relay stops
        # at launch, so no provider code runs.
        for name in ("provider.json", "main.py", "security.py", "taskbar.py",
                     "shell.py", "widgets.py"):
            shutil.copyfile(ROOT / "desktop" / name, self.packaged / name)
        (self.packaged / "PACKAGED").write_text("deb\n")
        self.packaged.chmod(0o755)
        (self.packaged / "main.py").chmod(0o644)
        self.checkout = self.base / "sources/kilix-desktops/kilix-95"
        engine = self.base / "prebuilt/bin"
        engine.mkdir(parents=True)
        (engine / "kitty").write_text("#!/bin/sh\nexit 99\n")
        (engine / "kitty").chmod(0o700)
        (engine / "kitten").write_text(
            "#!/usr/bin/env python3\n"
            "import json,os,sys\nfrom pathlib import Path\n"
            "args=sys.argv[1:]\n"
            "with Path(os.environ['TEST_TRACE']).open('a') as stream:\n"
            " stream.write(json.dumps(args)+'\\n')\n"
            "if 'ls' in args:\n"
            " print(Path(os.environ['TEST_INVENTORY']).read_text())\n"
            "elif 'launch' not in args and 'focus-window' not in args:\n"
            " sys.exit(99)\n"
        )
        (engine / "kitten").chmod(0o700)
        self.env = {
            "PATH": os.defpath,
            "HOME": str(self.home),
            "GPU_TERMINAL_HOME": str(self.base / "shared"),
            "GPU_TERMINAL_SOURCE_HOME": str(self.base / "sources"),
            "GPU_TERMINAL_SETTINGS_FILE": str(self.base / "settings"),
            "KILIX_STORAGE_HOME": str(self.base / "storage"),
            "KILIX_PREBUILT_HOME": str(engine.parent),
            "KILIX_PACKAGED_ENGINE_HOME": str(self.base / "no-engine"),
            "KILIX_PACKAGED_KILIX95_HOME": str(self.packaged),
            "KILIX95_AUTO_INSTALL": "0",
            "KILIX_STATE_LIBRARY": str(self.base / "unused-state-library"),
            "KITTY_LISTEN_ON": "unix:/private-fixture-only",
            "KITTY_WINDOW_ID": "1234",
            "TEST_INVENTORY": str(self.inventory),
            "TEST_TRACE": str(self.trace),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def run_kilix(self, *args, as_root):
        command = [str(ROOT / "kilix"), *args]
        if as_root:
            command = [UNSHARE, "-r", *command]
        self.trace.unlink(missing_ok=True)
        return subprocess.run(command, env=self.env, capture_output=True, text=True,
                              timeout=60, stdin=subprocess.DEVNULL)

    def launches(self):
        if not self.trace.exists():
            return []
        rows = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return [row for row in rows if "launch" in row]

    def desktop_path(self, *, as_root):
        result = self.run_kilix("status", as_root=as_root)
        lines = [line for line in result.stdout.splitlines()
                 if line.strip().startswith("desktop path:")]
        self.assertEqual(len(lines), 1, result.stdout + result.stderr)
        return lines[0].split(":", 1)[1].strip()

    def require_namespaces(self):
        if not user_namespaces_work():
            self.skipTest("unprivileged user namespaces are not available")

    def test_the_packaged_tree_is_selected_and_launched_when_root_owned(self):
        self.require_namespaces()
        self.assertEqual(self.desktop_path(as_root=True), str(self.packaged))
        result = self.run_kilix("desktop", "external", "--app", "settings", as_root=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(len(self.launches()), 1, result.stderr[-2000:])
        self.assertNotIn("not pinned", result.stderr)

    def test_a_tree_the_desktop_user_owns_is_never_trusted_as_packaged(self):
        self.assertNotEqual(self.desktop_path(as_root=False), str(self.packaged))
        result = self.run_kilix("desktop", "external", "--app", "settings", as_root=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.launches(), [])

    def test_a_writable_tree_or_entry_point_is_refused(self):
        self.require_namespaces()
        for path, mode, restore in ((self.packaged, 0o775, 0o755),
                                    (self.packaged, 0o757, 0o755),
                                    (self.packaged / "main.py", 0o664, 0o644),
                                    (self.packaged / "main.py", 0o646, 0o644)):
            with self.subTest(path=path.name, mode=oct(mode)):
                path.chmod(mode)
                try:
                    self.assertNotEqual(self.desktop_path(as_root=True), str(self.packaged))
                    result = self.run_kilix("desktop", "external", "--app", "settings",
                                            as_root=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.launches(), [])
                finally:
                    path.chmod(restore)

    def test_a_tree_without_the_marker_or_with_a_checkout_is_not_packaged(self):
        self.require_namespaces()
        marker = self.packaged / "PACKAGED"
        marker.unlink()
        self.assertNotEqual(self.desktop_path(as_root=True), str(self.packaged))
        marker.write_text("deb\n")
        (self.packaged / ".git").mkdir()
        self.assertNotEqual(self.desktop_path(as_root=True), str(self.packaged))
        (self.packaged / ".git").rmdir()
        elsewhere = self.base / "elsewhere"
        elsewhere.write_text("deb\n")
        marker.unlink()
        marker.symlink_to(elsewhere)
        self.assertNotEqual(self.desktop_path(as_root=True), str(self.packaged))

    def test_a_provider_checkout_still_wins_over_the_package(self):
        self.require_namespaces()
        self.checkout.mkdir(parents=True)
        for name in ("provider.json", "main.py"):
            shutil.copyfile(self.packaged / name, self.checkout / name)
        self.assertEqual(self.desktop_path(as_root=True), str(self.checkout))
        # An explicit KILIX95_DIR with no provider falls back to the package.
        self.env["KILIX95_DIR"] = str(self.base / "absent")
        self.assertEqual(self.desktop_path(as_root=True), str(self.packaged))


if __name__ == "__main__":
    unittest.main()
