"""Structured discovery and request validation must precede desktop setup."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ActionLauncherTests(unittest.TestCase):
    def invoke(self, arguments, payload=None):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            home.mkdir()
            environment = {
                "PATH": "/usr/bin:/bin", "HOME": str(home),
                "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                "XDG_STATE_HOME": str(home / "state"),
                "XDG_CONFIG_HOME": str(home / "config"),
                "XDG_CACHE_HOME": str(home / "cache"),
                "XDG_DATA_HOME": str(home / "data"),
            }
            result = subprocess.run(
                [str(ROOT / "kilix"), *arguments], input=payload,
                env=environment, cwd=home, text=True, capture_output=True,
                timeout=15,
            )
            self.assertEqual(list(home.iterdir()), [], "inspection initialized user state")
            return result

    def test_capabilities_is_a_bounded_json_query_without_setup(self):
        result = self.invoke(["action", "capabilities"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsInstance(json.loads(result.stdout), dict)
        self.assertLess(len(result.stdout.encode()), 16384)

    def test_action_help_requires_no_live_terminal(self):
        result = self.invoke(["action", "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--request-json", result.stdout)

    def test_invalid_request_fails_before_setup(self):
        result = self.invoke(["action", "--request-json", "-"], "{}")
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(len(result.stdout.encode()) + len(result.stderr.encode()), 8192)

    def test_general_help_lists_structured_actions(self):
        result = self.invoke(["--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kilix action --request-json -", result.stdout)
        self.assertIn("kilix action capabilities", result.stdout)


if __name__ == "__main__":
    unittest.main()
