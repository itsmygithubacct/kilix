"""Regression tests for the A2 round-5 review (reviews/A2-r5/REVIEW.md): N5 and N6.

N5: the helper run directly validates --timeout before building a Broker, for every verb. (N6, the shared
fallback fixture directory, is superseded by tests/test_pty_a2r6_fixes.py, which gives each run its own.)
Each fails on 67152aa2.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_cli import LAUNCHER, PtyCliCase  # noqa: E402
import kilix_pty  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "config" / "kilix_pty.py"
INVALID = ["abc", "nan", "inf", "-inf", "3000000", "9" * 400, "0", "0.0", "-1", "", "0.09", "60.01", "1e1", "+1",
           " 1", "1 ", "1,5", ".5", "5.", "5s", "1\n"]
VALID = ["0.1", "0.10", "1", "2.5", "60", "60.0", "00060", "007"]


class DirectTimeoutTests(PtyCliCase):
    def helper(self, value, *verb):
        return subprocess.run(["python3", str(HELPER), "--runtime", str(self.runtime), "--timeout", value, *verb],
                              env=self.env(), capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

    def test_n5_an_invalid_timeout_is_a_usage_error_for_a_verb_that_reads_only_the_disk(self):
        for value in INVALID:
            with self.subTest(value=value[:12]):
                result = self.helper(value, "capabilities")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("--timeout is in seconds, 0.1-60", result.stderr)
                self.assertEqual(result.stdout, "", "nothing may be printed as a result")

    def test_n5_the_same_values_are_refused_for_a_verb_that_would_reach_the_broker(self):
        for value in ("abc", "nan", "3000000", "0"):
            with self.subTest(value=value):
                result = subprocess.run(
                    ["python3", str(HELPER), "--broker", str(self.broker), "--runtime", str(self.runtime),
                     "--timeout", value, "list"], env=self.env(), capture_output=True, text=True, timeout=30,
                    stdin=subprocess.DEVNULL)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse((self.tmp / "calls").exists() and (self.tmp / "calls").read_text(),
                                 "the broker must not have been called")

    def test_n5_a_valid_timeout_is_kept_and_reported_as_standard_json(self):
        for value in VALID:
            with self.subTest(value=value):
                result = self.helper(value, "capabilities", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                document = json.loads(result.stdout, parse_constant=lambda name: self.fail(name))
                self.assertEqual(document["timeout_seconds"], float(value))

    def test_n5_the_public_launcher_and_the_helper_agree_on_every_value(self):
        for value in INVALID + VALID:
            with self.subTest(value=value[:12]):
                public = subprocess.run(["bash", str(LAUNCHER), "pty", "--timeout", value, "capabilities", "--json"],
                                        env=self.env(), capture_output=True, text=True, timeout=30,
                                        stdin=subprocess.DEVNULL)
                direct = self.helper(value, "capabilities", "--json")
                self.assertEqual(public.returncode == 0, direct.returncode == 0, public.stderr + direct.stderr)
                if public.returncode:
                    self.assertEqual(public.returncode, 2)
                    self.assertEqual(direct.returncode, 2)

    def test_n5_the_validator_itself(self):
        for value in VALID:
            self.assertTrue(kilix_pty.valid_timeout(value), value)
        for value in INVALID + [None]:
            self.assertFalse(kilix_pty.valid_timeout(value), repr(value))


if __name__ == "__main__":
    unittest.main()
