"""`pane quad` — anchors, focus, transactionality, and the size guard.

Four panes need three splits anchored A, A, B; anchoring all three at A gives a
bisected column, not a quad. take_focus=False throughout, then focus A, so a
human running `pane quad` is left where they started. A half-built quad is
worse than none, because the caller cannot tell which panes are theirs.

These exercise the real kilix_sdk.panes.quad() with its engine primitives
replaced, and the real parser over the recorded `kitten @ ls`. The verb layer
in remote.py only parses arguments and formats output, so it is tested for
delegation and porcelain rather than for composition.
"""

import importlib.util
import io
import json
import sys
import types
import unittest

import panes_stub
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "kitten_ls.json"
sys.path.insert(0, str(ROOT / "config"))

from kilix_sdk import panes as real_panes   # noqa: E402


SELF_PANE = 49          # 105x57 in the fixture -> 52x28 per pane, fine
SHORT_PANE = 153        # 105x16 in the fixture -> 52x8 per pane, too short
WIDE_PANE = 95          # 212x57


class Recorder:
    """Replaces quad()'s engine primitives; can fail the Nth split."""

    def __init__(self, fail_on=None, exception=None):
        self.splits, self.closed, self.focused = [], [], []
        self.fail_on = fail_on
        self.exception = exception or real_panes.PaneError("engine refused")
        self._next = 200

    def install(self, case, workspace=None):
        state = json.loads(FIXTURE.read_text())
        ws = workspace if workspace is not None else real_panes.parse(state)
        case.enterContext(unittest.mock.patch.object(
            real_panes, "snapshot", lambda **k: ws))
        case.enterContext(unittest.mock.patch.object(
            real_panes, "split", self.split))
        case.enterContext(unittest.mock.patch.object(
            real_panes, "close", self.close))
        case.enterContext(unittest.mock.patch.object(
            real_panes, "focus", self.focus))
        return self

    def split(self, direction="right", **kwargs):
        self.splits.append((direction, kwargs))
        if self.fail_on is not None and len(self.splits) == self.fail_on:
            raise self.exception
        self._next += 1
        return self._next

    def close(self, target, **kwargs):
        self.closed.append(target)

    def focus(self, target):
        self.focused.append(target)


import unittest.mock  # noqa: E402


class Composition(unittest.TestCase):
    def setUp(self):
        self.rec = Recorder().install(self)

    def test_three_splits(self):
        real_panes.quad()
        self.assertEqual(len(self.rec.splits), 3)

    def test_directions_are_right_down_down(self):
        real_panes.quad()
        self.assertEqual([d for d, _ in self.rec.splits], ["right", "down", "down"])

    def test_anchors_are_A_A_B_not_A_A_A(self):
        first, _, _ = real_panes.quad()
        anchors = [kw["anchor"] for _, kw in self.rec.splits]
        self.assertEqual(anchors[0], SELF_PANE)
        self.assertEqual(anchors[1], SELF_PANE)
        self.assertEqual(anchors[2], first,
                         "the third split must anchor to B, or it is a column")
        self.assertNotEqual(anchors[2], SELF_PANE)

    def test_take_focus_is_false_throughout(self):
        real_panes.quad()
        for _, kwargs in self.rec.splits:
            self.assertIs(kwargs["take_focus"], False)

    def test_focus_returns_to_the_origin(self):
        real_panes.quad()
        self.assertEqual(self.rec.focused, [SELF_PANE])

    def test_returns_three_distinct_ids(self):
        result = real_panes.quad()
        self.assertEqual(len(result), 3)
        self.assertEqual(len(set(result)), 3)

    def test_explicit_anchor_overrides_the_calling_pane(self):
        real_panes.quad(anchor=WIDE_PANE)
        self.assertEqual(self.rec.splits[0][1]["anchor"], WIDE_PANE)
        self.assertEqual(self.rec.focused, [WIDE_PANE])

    def test_commands_are_distributed_to_the_three_new_panes(self):
        real_panes.quad(commands=[["a"], ["b"], ["c"]])
        self.assertEqual([list(kw["command"]) for _, kw in self.rec.splits],
                         [["a"], ["b"], ["c"]])


class Transactional(unittest.TestCase):
    def test_failure_on_the_third_split_closes_the_first_two(self):
        rec = Recorder(fail_on=3).install(self)
        with self.assertRaises(real_panes.PaneError):
            real_panes.quad()
        self.assertEqual(len(rec.closed), 2)

    def test_cleanup_is_in_reverse_creation_order(self):
        rec = Recorder(fail_on=3).install(self)
        with self.assertRaises(real_panes.PaneError):
            real_panes.quad()
        self.assertEqual(rec.closed, [202, 201])

    def test_failure_on_the_second_split_closes_only_the_first(self):
        rec = Recorder(fail_on=2).install(self)
        with self.assertRaises(real_panes.PaneError):
            real_panes.quad()
        self.assertEqual(rec.closed, [201])

    def test_failure_on_the_first_split_closes_nothing(self):
        rec = Recorder(fail_on=1).install(self)
        with self.assertRaises(real_panes.PaneError):
            real_panes.quad()
        self.assertEqual(rec.closed, [])

    def test_focus_is_not_restored_when_the_quad_failed(self):
        rec = Recorder(fail_on=3).install(self)
        with self.assertRaises(real_panes.PaneError):
            real_panes.quad()
        self.assertEqual(rec.focused, [])

    def test_a_failing_close_does_not_mask_the_original_error(self):
        rec = Recorder(fail_on=3).install(self)
        rec.close = lambda *a, **k: (_ for _ in ()).throw(
            real_panes.PaneError("already gone"))
        with unittest.mock.patch.object(real_panes, "close", rec.close):
            with self.assertRaises(real_panes.PaneError):
                real_panes.quad()

    @unittest.expectedFailure
    def test_a_non_PaneError_also_cleans_up(self):
        """GAP in config/kilix_sdk/panes.py:439 -- `except PaneError` only.

        The design says "on any failure close the ones this call made and
        re-raise". An OSError from the transport leaves a half-built quad and
        the caller cannot tell which panes are theirs. Widening the clause to
        `except BaseException` (or at least Exception) closes it. Task A owns
        that file; this test flips to an unexpected success when it is fixed.
        """
        rec = Recorder(fail_on=3, exception=OSError("socket died")).install(self)
        with self.assertRaises(OSError):
            real_panes.quad()
        self.assertEqual(len(rec.closed), 2)


class SizeGuard(unittest.TestCase):
    def setUp(self):
        self.rec = Recorder().install(self)

    def test_a_short_pane_is_refused_before_anything_is_created(self):
        with self.assertRaises(real_panes.PaneError):
            real_panes.quad(anchor=SHORT_PANE)
        self.assertEqual(self.rec.splits, [], "nothing may be created first")

    def test_the_refusal_names_the_actual_and_resulting_size(self):
        with self.assertRaises(real_panes.PaneError) as caught:
            real_panes.quad(anchor=SHORT_PANE)
        message = str(caught.exception)
        self.assertIn("105x16", message, "must name the pane's actual size")
        self.assertIn("52x8", message, "must name the resulting pane size")
        self.assertIn("40x12", message, "must name the threshold")

    def test_a_large_pane_is_allowed(self):
        real_panes.quad(anchor=WIDE_PANE)
        self.assertEqual(len(self.rec.splits), 3)

    def test_the_self_pane_is_large_enough(self):
        real_panes.quad()
        self.assertEqual(len(self.rec.splits), 3)

    def test_the_threshold_is_the_documented_40x12(self):
        self.assertEqual(
            (real_panes.QUAD_MIN_COLUMNS, real_panes.QUAD_MIN_LINES), (40, 12))

    @unittest.expectedFailure
    def test_unknown_size_refuses_rather_than_skipping_the_guard(self):
        """GAP in config/kilix_sdk/panes.py:419 -- `if origin.columns and ...`.

        When the engine does not report a size the fields parse to 0, the
        condition is falsy and the guard is skipped in silence: quad proceeds
        on a pane it never measured. A control that cannot fire is the defect
        class this release keeps finding. Refusing when the size is unknown is
        the safe direction. Task A owns that file.
        """
        state = json.loads(FIXTURE.read_text())
        for os_window in state:
            for tab in os_window["tabs"]:
                for window in tab["windows"]:
                    window["columns"] = 0
                    window["lines"] = 0
        Recorder().install(self, workspace=real_panes.parse(state))
        with self.assertRaises(real_panes.PaneError):
            real_panes.quad()


class Verb(unittest.TestCase):
    """remote.py delegates to the library and formats; it does not compose."""

    def load_remote(self):
        stub = types.ModuleType("kilix_sdk.panes")
        stub.quad = lambda **kwargs: (201, 202, 203)
        # The old restore here kept only keys that were already present, so a
        # stub installed where the real package had not been imported yet was
        # never removed.  panes_stub.install deletes those instead.
        panes_stub.install(self, stub)
        return panes_stub.load_remote(ROOT, "kilix_remote_quad_verb")

    def test_porcelain_prints_only_the_ids(self):
        remote = self.load_remote()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            remote.main(["pane", "quad", "--porcelain"])
        self.assertEqual(buffer.getvalue().split(), ["201", "202", "203"])

    def test_plain_output_is_prose(self):
        remote = self.load_remote()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            remote.main(["pane", "quad"])
        self.assertIn("kilix pane quad: opened", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
