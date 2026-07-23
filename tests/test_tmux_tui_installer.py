from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-tmux-tui.sh"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c", "user.name=Fixture",
            "-c", "user.email=fixture@example.invalid",
            "-C", str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class TmuxTuiInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cli = self.root / "tmux-cli"
        self.tui = self.root / "tmux-tui"
        self.source = self.root / "sources"
        self.state = self.root / "state"
        self.prefix = self.root / "prefix"

        self.cli.mkdir()
        git(self.cli, "init", "-q", "-b", "main")
        tb = self.cli / "tb.py"
        tb.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "if '--version' in sys.argv:\n"
            "    print('tb fixture')\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(0)\n"
        )
        tb.chmod(0o755)
        git(self.cli, "add", "tb.py")
        git(self.cli, "commit", "-qm", "fixture cli")
        self.cli_ref = git(self.cli, "rev-parse", "HEAD")

        self.tui.mkdir()
        git(self.tui, "init", "-q", "-b", "main")
        entry = self.tui / "tmux_tui.py"
        entry.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "if '--version' in sys.argv:\n"
            "    print('tmux-tui fixture')\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(0)\n"
        )
        entry.chmod(0o755)
        subprocess.run(
            [
                "git", "-c", "protocol.file.allow=always",
                "-c", "user.name=Fixture",
                "-c", "user.email=fixture@example.invalid",
                "-C", str(self.tui), "submodule", "add",
                str(self.cli), "tmux-cli",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        git(self.tui, "add", ".")
        git(self.tui, "commit", "-qm", "fixture tui")
        self.tui_ref = git(self.tui, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def run_installer(self, *args: str, check: bool = True):
        env = dict(os.environ)
        env.update({
            "GPU_TERMINAL_SOURCE_HOME": str(self.source),
            "KILIX_STATE_DIRECTORY": str(self.state),
            "TMUX_TUI_PREFIX": str(self.prefix),
            "TMUX_TUI_REPO": str(self.tui),
            "TMUX_TUI_REF": self.tui_ref,
            "TMUX_CLI_REF": self.cli_ref,
        })
        return subprocess.run(
            [str(INSTALLER), *args],
            env=env,
            capture_output=True,
            text=True,
            check=check,
        )

    def test_installs_manager_without_implicit_tb_alias(self):
        self.run_installer()
        manager = self.prefix / "bin" / "tmux-tui"
        alias = self.prefix / "bin" / "tb"
        self.assertTrue(manager.is_symlink())
        self.assertFalse(alias.exists())
        self.assertEqual(stat.S_IMODE(manager.resolve().stat().st_mode), 0o755)
        self.assertIn(self.tui_ref, str(manager.resolve()))

    def test_with_tb_publishes_nested_cli_and_is_idempotent(self):
        self.run_installer("--with-tb")
        self.run_installer("--with-tb")
        alias = self.prefix / "bin" / "tb"
        self.assertTrue(alias.is_symlink())
        self.assertEqual(alias.resolve().name, "tb.py")
        self.assertEqual(
            git(alias.resolve().parent, "rev-parse", "HEAD"),
            self.cli_ref,
        )

    def test_without_tb_only_removes_managed_alias(self):
        self.run_installer("--with-tb")
        self.run_installer("--without-tb")
        self.assertFalse((self.prefix / "bin" / "tb").exists())
        self.assertTrue((self.prefix / "bin" / "tmux-tui").is_symlink())

    def test_ref_must_be_immutable(self):
        env = dict(os.environ)
        env.update({
            "GPU_TERMINAL_SOURCE_HOME": str(self.source),
            "KILIX_STATE_DIRECTORY": str(self.state),
            "TMUX_TUI_PREFIX": str(self.prefix),
            "TMUX_TUI_REPO": str(self.tui),
            "TMUX_TUI_REF": "main",
            "TMUX_CLI_REF": self.cli_ref,
        })
        result = subprocess.run(
            [str(INSTALLER)],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("full 40-character commit SHA", result.stderr)


if __name__ == "__main__":
    unittest.main()
