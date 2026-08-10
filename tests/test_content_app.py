"""Shared catalog application launch contract."""

from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import content_app  # noqa: E402


class ContentAppTests(unittest.TestCase):
    def test_ref_is_read_only_and_matches_the_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ)
            environment.update({
                "HOME": temporary,
                "GPU_TERMINAL_HOME": str(Path(temporary) / "gpu-terminal"),
            })
            result = subprocess.run(
                [
                    "python3",
                    str(ROOT / "config" / "content_app.py"),
                    "ref",
                    "kilix-pdf-conversion",
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.strip(),
                content_app.application_spec("kilix-pdf-conversion").ref,
            )
            self.assertFalse((Path(temporary) / "gpu-terminal").exists())

    def test_game_id_is_not_accepted_as_an_application(self):
        with self.assertRaises(content_app.content.CatalogError):
            content_app.application_spec("kilix-pong")

    def test_terminal_window_gets_its_own_pty_and_title(self):
        spec = SimpleNamespace(launch_mode="terminal", label="PDF Conversion")
        with mock.patch.object(content_app, "_xterm", return_value="/usr/bin/xterm"):
            argv = content_app.window_argv(spec, "/apps/kilix-pdf", ["report.pdf"])
        self.assertEqual(
            argv,
            [
                "/usr/bin/xterm",
                "-T",
                "PDF Conversion",
                "-e",
                "/apps/kilix-pdf",
                "report.pdf",
            ],
        )

    def test_native_window_app_is_not_wrapped_in_a_terminal(self):
        spec = SimpleNamespace(launch_mode="xpane", label="Media Player")
        self.assertEqual(
            content_app.window_argv(spec, "/apps/kilix-amp", ["song.ogg"]),
            ["/apps/kilix-amp", "song.ogg"],
        )


if __name__ == "__main__":
    unittest.main()
