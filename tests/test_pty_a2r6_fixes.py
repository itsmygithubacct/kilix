"""Regression tests for the A2 round-6 review (reviews/A2-r6/REVIEW.md): N7 and N8.

N8: every repeated --timeout is validated by the launcher and helper.
The separate historical multiplexer fixture review is outside this port.
"""
import gc
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import weakref
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_cli import LAUNCHER, PtyCliCase  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "config" / "kilix_pty.py"
CASE = "test_missing_or_mutated_package_refuses_without_disabled_build"
SEQUENCES = [("nan", "1"), ("abc", "1"), ("60.01", "1"), ("0", "1"), ("", "1"), ("1", "nan"), ("1", "0.1"),
             ("60", "2.5"), ("2.5", "60"), ("1", "abc", "2"), ("1", "2", "3"), ("0.1", "0.1")]


class RepeatedTimeoutTests(PtyCliCase):
    def run_both(self, values):
        flags = [item for value in values for item in ("--timeout", value)]
        public = subprocess.run(["bash", str(LAUNCHER), "pty", *flags, "capabilities", "--json"],
                                env=self.env(), capture_output=True, text=True, timeout=30,
                                stdin=subprocess.DEVNULL)
        direct = subprocess.run(["python3", str(HELPER), "--runtime", str(self.runtime), *flags,
                                 "capabilities", "--json"], env=self.env(), capture_output=True, text=True,
                                timeout=30, stdin=subprocess.DEVNULL)
        return public, direct

    def test_n8_any_invalid_occurrence_is_refused_and_all_valid_means_the_last_wins(self):
        for values in SEQUENCES:
            with self.subTest(values=values):
                public, direct = self.run_both(values)
                valid = all(multiplexer_valid(value) for value in values)
                if valid:
                    for result in (public, direct):
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(result.stdout)["timeout_seconds"], float(values[-1]))
                else:
                    self.assertEqual((public.returncode, direct.returncode), (2, 2), public.stderr + direct.stderr)
                    for result in (public, direct):
                        self.assertNotIn("Traceback", result.stderr)
                        self.assertEqual(result.stdout, "")
                self.assertEqual(public.returncode, direct.returncode, "launcher and helper must agree")


def multiplexer_valid(value):
    import kilix_pty
    return kilix_pty.valid_timeout(value)
