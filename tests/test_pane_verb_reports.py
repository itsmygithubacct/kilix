"""`kilix pane close|send|focus` say what they did.

An agent that gets only an exit status runs `kilix pane list` afterwards to
check; that is one more model round trip, about 14k tokens (2026-09-29 route
benchmark, token-cost round 2). The verbs therefore name the pane they acted
on, and a missing or shared title is one line on stderr, not a traceback.

kilix_sdk.panes is stubbed as in test_tab_verb: this file tests the verb layer.
"""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

import test_tab_verb as base


class NoSuchTarget(RuntimeError):
    pass


class Workspace(base.FakeWorkspace):
    def find_pane(self, target):
        target = str(target)
        if target.startswith("pane:"):
            target = target[5:]
        found = [p for p in self.panes()
                 if str(p.id) == target or p.title == target]
        if len(found) != 1:
            raise NoSuchTarget(f"no live pane titled {target!r}; see 'kilix pane list'")
        return found[0]


class Panes(base.RecordingPanes):
    def __init__(self):
        super().__init__()
        self.workspace = Workspace()

    def send(self, target, text, *, submit=False):
        self.calls.append(("send", {"target": target, "text": text, "submit": submit}))


def run(test, argv):
    panes = Panes()
    remote = base.panes_stub.install_and_load(
        test, panes, base.ROOT, "kilix_remote_reports_under_test")
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = remote.main(argv)
    return code, out.getvalue(), err.getvalue(), panes.calls


class Reports(unittest.TestCase):
    def test_close_by_title_names_the_pane_it_closed(self):
        code, out, _, calls = run(self, ["pane", "close", "esp32"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("close", {"target": "pane:155", "force": False})])
        self.assertEqual(out, "kilix pane close: closed pane 155 'esp32'\n")

    def test_close_without_a_target_closes_this_pane(self):
        code, out, _, calls = run(self, ["pane", "close"])
        self.assertEqual(calls[0][1]["target"], "pane:49")
        self.assertIn("closed pane 49 'orchestrator'", out)

    def test_send_submit_reports_the_text_and_the_enter(self):
        code, out, _, calls = run(
            self, ["pane", "send", "rx-a", "make test", "--submit"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("send", {"target": "pane:123",
                                           "text": "make test", "submit": True})])
        self.assertEqual(
            out, "kilix pane send: typed 9 characters into pane 123 'rx-a' "
                 "and pressed Enter\n")

    def test_focus_reports_the_pane(self):
        _, out, _, calls = run(self, ["pane", "focus", "pane:95"])
        self.assertEqual(calls, [("focus", {"target": "pane:95"})])
        self.assertIn("focused pane 95 'w-f112'", out)

    def test_a_missing_title_is_one_line_and_acts_on_nothing(self):
        code, out, err, calls = run(self, ["pane", "close", "no-such-pane"])
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertEqual(out, "")
        self.assertEqual(
            err, "kilix pane close: no live pane titled 'no-such-pane'; "
                 "see 'kilix pane list'\n")

    def test_pane_help_lists_the_whole_family(self):
        for flag in ("--help", "-h", "help"):
            with self.subTest(flag=flag):
                code, out, _, calls = run(self, ["pane", flag])
                self.assertEqual((code, calls), (0, []))
                for verb in ("right|left|up|down", "send TARGET 'TEXT' [--submit]",
                             "close [TARGET]", "read TARGET", "focus TARGET",
                             "list [--json]", "complete syntax",
                             "prints what it did"):
                    self.assertIn(verb, out)


if __name__ == "__main__":
    unittest.main()
