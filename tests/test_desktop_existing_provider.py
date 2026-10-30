"""A desktop selector must reuse only its selected provider's entry point."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ExistingDesktopProviderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="desktop-provider-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.inventory = self.base / "inventory.json"
        self.trace = self.base / "terminal.jsonl"
        self.external = self.base / "providers/kilix-95"
        self.external.mkdir(parents=True)
        # Use the real provider contract and source files. The terminal relay
        # stops at launch, so no GUI or provider code runs in this fixture.
        for name in ("provider.json", "main.py", "security.py", "taskbar.py",
                     "shell.py", "widgets.py"):
            shutil.copyfile(ROOT / "desktop" / name, self.external / name)
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
            "KILIX95_DIR": str(self.external),
            "KILIX95_AUTO_INSTALL": "0",
            "KILIX95_TRUST_EXISTING_CHECKOUT": "1",
            "KILIX_STATE_LIBRARY": str(self.base / "unused-state-library"),
            "KITTY_LISTEN_ON": "unix:/private-fixture-only",
            "KITTY_WINDOW_ID": "1234",
            "TEST_INVENTORY": str(self.inventory),
            "TEST_TRACE": str(self.trace),
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_ALLOW_PROTOCOL": "file",
        }
        self.targets = {
            "builtin": ROOT / "desktop/main.py",
            "external": self.external / "main.py",
        }

    def launch(self, provider, entries):
        windows = [{"id": wid, "foreground_processes": [
            {"cmdline": ["python3", str(path)]}]} for wid, path in entries]
        self.inventory.write_text(json.dumps([{"tabs": [{"windows": windows}]}]))
        self.trace.unlink(missing_ok=True)
        result = subprocess.run(
            [str(ROOT / "kilix"), "desktop", provider, "--app", "settings"],
            env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return ([row for row in calls if "focus-window" in row],
                [row for row in calls if "launch" in row])

    def assert_launch(self, provider, entries):
        focus, launches = self.launch(provider, entries)
        self.assertFalse(focus, "a different provider consumed the launch request")
        self.assertEqual(len(launches), 1)
        call = launches[0]
        self.assertIn("KILIX_DESKTOP_PROVIDER=" + provider, call)
        self.assertEqual(call[call.index("--") + 1:],
                         [str(ROOT / "kilix"), "desktop", "--app", "settings"])

    def test_other_python_provider_does_not_consume_explicit_selection(self):
        for selected, other in (("builtin", "external"), ("external", "builtin")):
            with self.subTest(selected=selected):
                self.assert_launch(selected, [(41, self.targets[other])])

    def test_selected_provider_is_reused_even_after_another_provider(self):
        for selected, other in (("builtin", "external"), ("external", "builtin")):
            with self.subTest(selected=selected):
                focus, launches = self.launch(selected, [
                    (41, self.targets[other]), (42, self.targets[selected])])
                self.assertFalse(launches)
                self.assertEqual(len(focus), 1)
                self.assertIn("id:42", focus[0])

    def test_same_basename_in_another_checkout_does_not_match(self):
        for selected, suffix in (("builtin", "desktop/main.py"),
                                 ("external", "kilix-95/main.py")):
            with self.subTest(selected=selected):
                self.assert_launch(selected, [(41, self.base / "other" / suffix)])

    def test_same_selected_provider_is_focused_without_duplicate_launch(self):
        for selected in self.targets:
            with self.subTest(selected=selected):
                focus, launches = self.launch(selected, [(42, self.targets[selected])])
                self.assertFalse(launches)
                self.assertEqual(len(focus), 1)
                self.assertIn("id:42", focus[0])


if __name__ == "__main__":
    unittest.main()
