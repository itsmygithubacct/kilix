"""A disconnected terminal must not strand the app's private display."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import apprun


class AppPaneCleanupTests(unittest.TestCase):
    def test_each_failed_stage_still_closes_the_owned_session(self):
        for failed in ("terminal", "presenter", "capture"):
            with self.subTest(failed=failed):
                calls = []
                def step(name):
                    def call():
                        calls.append(name)
                        if name == failed:
                            raise OSError("closed " + name)
                    return call
                pane = object.__new__(apprun.AppPane)
                pane.dnd = None
                pane.inj = SimpleNamespace(release_all=step("input"))
                pane.term = SimpleNamespace(restore=step("terminal"))
                pane.presenter = SimpleNamespace(close=step("presenter"))
                pane._stop_capture = step("capture")
                pane.xapp = SimpleNamespace(close=step("session"))
                with self.assertRaisesRegex(OSError, "closed " + failed):
                    pane._cleanup()
                self.assertEqual(calls, ["input", "terminal", "presenter",
                                         "capture", "session"])
