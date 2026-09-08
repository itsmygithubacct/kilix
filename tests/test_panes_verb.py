"""Pane Center must resolve its pinned CLI before attempting engine setup."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PanesVerbTests(unittest.TestCase):
    def test_dispatch_installs_forwards_arguments_and_refuses_old_tui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = root / "checkout"
            checkout.mkdir()
            shutil.copy2(ROOT / "kilix", checkout / "kilix")
            for name in ("config", "kilix-settings"):
                (checkout / name).symlink_to(ROOT / name)
            scripts = checkout / "scripts"
            scripts.mkdir()
            installer = scripts / "install-kilix-tui-utils.sh"
            installer.write_text(
                '#!/bin/sh\nset -eu\n'
                'echo installed >> "$HOME/install-calls"\n'
                'mkdir -p "$KILIX_TUI_UTILS_PREFIX/bin"\n'
                'cp "$HOME/panes-fixture" "$KILIX_TUI_UTILS_PREFIX/bin/kilix-panes"\n'
            )
            installer.chmod(0o700)
            home = root / "home"
            home.mkdir()
            fixture = home / "panes-fixture"
            fixture.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            fixture.chmod(0o700)
            utils = root / "utils"
            module = utils / "src/kilix_tui/pane_center.py"
            module.parent.mkdir(parents=True)
            module.touch()
            env = {"HOME": str(home), "PATH": os.environ["PATH"],
                   "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                   "KILIX_TUI_UTILS_DIR": str(utils),
                   "KILIX_TUI_UTILS_PREFIX": str(root / "prefix")}
            for alias, args in (("panes", ["--json"]),
                                ("pane-center", ["list", "--json"]),
                                ("panes", ["send", "7", "hello world"])):
                result = subprocess.run([str(checkout / "kilix"), alias, *args],
                                        env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), args)
            self.assertEqual((home / "install-calls").read_text().splitlines(),
                             ["installed"] * 3)
            # A kept/dirty legacy checkout must not turn a CLI call into curses.
            module.unlink()
            result = subprocess.run([str(checkout / "kilix"), "panes", "list"],
                                    env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertIn("does not provide Pane Center", result.stderr)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
