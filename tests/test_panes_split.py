"""`kilix pane <direction>` — what the verb hands to kilix_sdk.panes.

The verb layer owns argument parsing and direction normalisation; the library
owns talking to the engine. These tests pin the boundary: for every accepted
direction word, every flag, and the anchor, exactly what `split()` is called
with. kilix_sdk.panes is stubbed, because this file tests the front end.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RecordingPanes(types.ModuleType):
    """A stand-in for kilix_sdk.panes that records calls instead of splitting."""

    def __init__(self):
        super().__init__("kilix_sdk.panes")
        self.calls = []
        self.next_id = 118
        self.fail_on_split = None

    def split(self, direction="right", **kwargs):
        self.calls.append(("split", direction, kwargs))
        if self.fail_on_split is not None and \
                len([c for c in self.calls if c[0] == "split"]) == self.fail_on_split:
            raise RuntimeError("engine refused the split")
        self.next_id += 1
        return self.next_id

    def close(self, target, **kwargs):
        self.calls.append(("close", target, kwargs))

    def focus(self, target):
        self.calls.append(("focus", target, {}))

    def snapshot(self, **kwargs):
        self.calls.append(("snapshot", None, kwargs))
        return self.workspace

    def new_tab(self, **kwargs):
        self.calls.append(("new_tab", None, kwargs))
        self.next_id += 1
        return self.next_id

    def rename_tab(self, target, title):
        self.calls.append(("rename_tab", target, {"title": title}))

    def move_tab(self, offset):
        self.calls.append(("move_tab", offset, {}))

    def read(self, target, **kwargs):
        self.calls.append(("read", target, kwargs))
        return "pane contents\n"

    def send(self, target, text, **kwargs):
        self.calls.append(("send", target, dict(kwargs, text=text)))


def install_stub():
    """Put a recording kilix_sdk.panes on sys.modules and load remote.py."""
    panes = RecordingPanes()
    package = types.ModuleType("kilix_sdk")
    package.panes = panes
    package.__path__ = []
    sys.modules["kilix_sdk"] = package
    sys.modules["kilix_sdk.panes"] = panes

    spec = importlib.util.spec_from_file_location(
        "kilix_remote_under_test", ROOT / "config" / "remote.py")
    assert spec is not None and spec.loader is not None
    remote = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(remote)
    return remote, panes


class SplitDirections(unittest.TestCase):
    def setUp(self):
        self.remote, self.panes = install_stub()
        # engine_predates readlinks /proc; in a test there is no live engine.
        self.remote.engine_predates = lambda location: False

    def split_call(self):
        return [c for c in self.panes.calls if c[0] == "split"][0]

    def test_default_direction_is_right(self):
        self.assertEqual(self.remote.main(["pane"]), 0)
        self.assertEqual(self.split_call()[1], "right")

    def test_each_canonical_direction_passes_through(self):
        for direction in ("right", "left", "up", "down"):
            with self.subTest(direction=direction):
                self.remote, self.panes = install_stub()
                self.remote.engine_predates = lambda location: False
                self.assertEqual(self.remote.main(["pane", direction]), 0)
                self.assertEqual(self.split_call()[1], direction)

    def test_above_and_below_normalise_onto_existing_keys(self):
        for word, expected in (("above", "up"), ("below", "down")):
            with self.subTest(word=word):
                self.remote, self.panes = install_stub()
                self.remote.engine_predates = lambda location: False
                self.assertEqual(self.remote.main(["pane", word]), 0)
                self.assertEqual(self.split_call()[1], expected)

    def test_normalised_direction_is_a_pane_locations_key(self):
        for word in ("right", "left", "up", "down", "above", "below"):
            with self.subTest(word=word):
                self.assertIn(self.remote.normalize_direction(word),
                              self.remote.PANE_LOCATIONS)

    def test_synonyms_do_not_add_engine_locations(self):
        self.assertEqual(set(self.remote.PANE_LOCATIONS),
                         {"right", "left", "up", "down"})
        self.assertEqual(set(self.remote.PANE_DIRECTION_SYNONYMS),
                         {"above", "below"})


class SplitOptions(unittest.TestCase):
    def setUp(self):
        self.remote, self.panes = install_stub()
        self.remote.engine_predates = lambda location: False

    def kwargs(self):
        return [c for c in self.panes.calls if c[0] == "split"][0][2]

    def test_cwd_is_forwarded(self):
        self.remote.main(["pane", "right", "--cwd", "/tmp/x"])
        self.assertEqual(self.kwargs()["cwd"], "/tmp/x")

    def test_cwd_defaults_to_current(self):
        self.remote.main(["pane", "right"])
        self.assertEqual(self.kwargs()["cwd"], "current")

    def test_hold_is_exposed_and_off_by_default(self):
        self.remote.main(["pane", "right", "--hold"])
        self.assertIs(self.kwargs()["hold"], True)
        self.remote, self.panes = install_stub()
        self.remote.engine_predates = lambda location: False
        self.remote.main(["pane", "right"])
        self.assertIs(self.kwargs()["hold"], False)

    def test_anchor_is_forwarded_as_an_int(self):
        self.remote.main(["pane", "right", "--anchor", "118"])
        self.assertEqual(self.kwargs()["anchor"], 118)

    def test_anchor_defaults_to_none_meaning_the_calling_pane(self):
        self.remote.main(["pane", "right"])
        self.assertIsNone(self.kwargs()["anchor"])

    def test_command_after_double_dash_is_argv(self):
        self.remote.main(["pane", "right", "--", "./run-tests.sh", "-v"])
        self.assertEqual(list(self.kwargs()["command"]), ["./run-tests.sh", "-v"])

    def test_title_is_forwarded(self):
        self.remote.main(["pane", "right", "--title", "worker"])
        self.assertEqual(self.kwargs()["title"], "worker")


class PorcelainOutput(unittest.TestCase):
    def setUp(self):
        self.remote, self.panes = install_stub()
        self.remote.engine_predates = lambda location: False

    def test_porcelain_prints_only_the_id(self):
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.remote.main(["pane", "right", "--porcelain"])
        self.assertEqual(buffer.getvalue().strip(), "119")
        self.assertNotIn("kilix", buffer.getvalue())

    def test_without_porcelain_it_is_prose(self):
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.remote.main(["pane", "right"])
        self.assertIn("kilix pane: opened 119", buffer.getvalue())


class EnginePredatesStillGuards(unittest.TestCase):
    def test_left_is_refused_on_an_older_engine(self):
        remote, panes = install_stub()
        remote.engine_predates = lambda location: location == "vsplit-before"
        self.assertEqual(remote.main(["pane", "left"]), 2)
        self.assertEqual([c for c in panes.calls if c[0] == "split"], [])


if __name__ == "__main__":
    unittest.main()
