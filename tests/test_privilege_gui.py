"""A terminal window's exit is not the result of the consent-bearing child."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from kilix_sdk import privilege


class GraphicalResultTests(unittest.TestCase):
    def invoke(self, child_result, *, terminal_status=0):
        result_paths = []

        def terminal(argv, **kwargs):
            self.assertEqual(argv[:4], ["/usr/bin/xterm", "-T", "Setup", "-e"])
            self.assertNotIn("shell", kwargs)
            if "--result-file" in argv:
                path = Path(argv[argv.index("--result-file") + 1])
                result_paths.append(path)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                if child_result is not None:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(child_result)
            return SimpleNamespace(returncode=terminal_status)

        with mock.patch.object(privilege, "_validate"), \
                mock.patch.object(privilege.sys.stdin, "isatty", return_value=False), \
                mock.patch.dict(os.environ, {"DISPLAY": ":9"}), \
                mock.patch.object(privilege.subprocess, "run", side_effect=terminal):
            result = privilege.run_helper(privilege.WAYDROID_SETUP, title="Setup")
        for path in result_paths:
            self.assertFalse(path.parent.exists(), "private result directory leaked")
        return result

    def test_child_status_survives_zero_terminal_exit(self):
        for code in (0, 1, 7, -15):
            with self.subTest(code=code):
                self.assertEqual(self.invoke(f"{code}\n".encode()), code)

    def test_missing_or_invalid_child_status_never_reports_success(self):
        for data in (None, b"", b"invalid\n", b"0\nextra", b"0" * 64, b"256\n"):
            with self.subTest(data=data):
                self.assertNotEqual(self.invoke(data), 0)

    def test_terminal_failure_is_preserved_even_with_child_success(self):
        self.assertEqual(self.invoke(b"0\n", terminal_status=7), 7)

    def test_child_records_result_only_after_consent_handler_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "status"
            def cancelled(helper, title):
                self.assertFalse(result.exists())
                self.assertEqual((helper, title), (privilege.WAYDROID_SETUP, "Setup"))
                return 1
            with mock.patch.object(privilege, "_terminal", side_effect=cancelled):
                self.assertEqual(privilege.main([
                    "--result-file", str(result), privilege.WAYDROID_SETUP, "Setup",
                ]), 1)
            self.assertEqual(result.read_bytes(), b"1\n")
            self.assertEqual(result.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
