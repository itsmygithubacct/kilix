"""Model tests for kilix_sdk.panes, against a recorded `kitten @ ls`.

The fixture is a real recording, not hand-written, because the payload is
large and a hand-written one drifts from what the engine emits.  It was
sanitised at source before being committed -- foreign home-directory paths
and agent process names were replaced -- with the schema (every os-window,
tab and window id, and every key set) asserted unchanged by that edit.

Note for other streams writing tests against this module: `panes.py` uses
dataclasses, and `importlib.util.module_from_spec` + `exec_module` fails on
them unless the module is registered in `sys.modules` first.  See `_load`.
"""

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "kitten_ls.json"


def _load():
    spec = importlib.util.spec_from_file_location(
        "kilix_sdk_panes", ROOT / "config" / "kilix_sdk" / "panes.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their own module during class creation, so the
    # module has to be findable before exec_module runs.
    sys.modules["kilix_sdk_panes"] = module
    spec.loader.exec_module(module)
    return module


panes = _load()


class WorkspaceModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = json.loads(FIXTURE.read_text())
        cls.ws = panes.parse(cls.state)

    def test_fixture_parses_to_the_recorded_shape(self):
        self.assertEqual(len(self.ws.os_windows), 1)
        self.assertEqual([t.id for t in self.ws.tabs()],
                         [2, 23, 27, 30, 32, 35, 40, 48, 51, 53, 74, 77])
        self.assertEqual(
            [p.id for p in self.ws.panes()],
            [4, 49, 153, 95, 99, 102, 104, 107, 112, 120, 123, 125,
             151, 149, 154, 155])

    def test_pane_fields_come_from_the_payload(self):
        pane = self.ws.find_pane(49)
        self.assertEqual(pane.tab_id, 2)
        self.assertEqual(pane.os_window_id, 1)
        self.assertEqual(pane.columns, 105)
        self.assertEqual(pane.lines, 57)
        self.assertTrue(pane.is_self)
        self.assertEqual(pane.env.get("KITTY_WINDOW_ID"), "49")
        self.assertIsInstance(pane.cmdline, tuple)

    def test_tab_carries_its_panes_as_the_render_branch(self):
        tab = self.ws.find_tab(2)
        self.assertEqual([p.id for p in tab.panes], [4, 49, 153])
        self.assertEqual(tab.layout, "splits")
        self.assertEqual(tab.title, "orchestrator")
        self.assertTrue(tab.is_active)

    def test_frozen(self):
        pane = self.ws.find_pane(49)
        with self.assertRaises(Exception):
            pane.id = 1                      # type: ignore[misc]

    # --- me() and focus -------------------------------------------------

    def test_me_is_the_self_pane(self):
        me = self.ws.me()
        self.assertIsNotNone(me)
        self.assertEqual(me.id, 49)

    def test_focused_is_one_pane_though_the_engine_marks_twelve(self):
        """The trap this library exists to absorb.

        `kitten @ ls` marks a pane is_focused *within its own tab*, so this
        twelve-tab recording reports twelve focused panes.  A front end that
        filters on the flag gets twelve; the real answer is one.
        """
        flagged = [p.id for p in self.ws.panes() if p.is_focused]
        self.assertEqual(len(flagged), 12)
        focused = self.ws.focused()
        self.assertIsNotNone(focused)
        self.assertEqual(focused.id, 49)
        self.assertEqual([t.id for t in self.ws.tabs() if t.is_active], [2])

    # --- find(), every target syntax ------------------------------------

    def test_find_every_pane_syntax(self):
        for target in ("pane:49", "window:49", "win:49", 49, "49"):
            with self.subTest(target=target):
                found = self.ws.find(target)
                self.assertIsInstance(found, panes.Pane)
                self.assertEqual(found.id, 49)

    def test_find_every_tab_syntax(self):
        for target in ("tab:2", "page:2", "session:2", 2, "2"):
            with self.subTest(target=target):
                found = self.ws.find(target)
                self.assertIsInstance(found, panes.Tab)
                self.assertEqual(found.id, 2)

    def test_find_pane_and_find_tab_do_not_make_the_caller_type_switch(self):
        self.assertEqual(self.ws.find_pane("pane:153").id, 153)
        self.assertEqual(self.ws.find_tab("tab:77").id, 77)
        with self.assertRaises(panes.NoSuchTarget):
            self.ws.find_pane("tab:2")
        with self.assertRaises(panes.NoSuchTarget):
            self.ws.find_tab("pane:49")

    def test_unknown_target_raises(self):
        with self.assertRaises(panes.NoSuchTarget):
            self.ws.find("pane:999999")
        with self.assertRaises(panes.NoSuchTarget):
            self.ws.find("999999")

    def test_ambiguous_bare_id_raises_rather_than_guessing(self):
        """No id in the recording collides, so the collision is constructed.

        This is a model-level assertion, not a second fixture: the engine
        payload is untouched.
        """
        self.assertEqual(
            sorted({t.id for t in self.ws.tabs()}
                   & {p.id for p in self.ws.panes()}), [],
            "the recording has no natural collision; hence the construction")
        pane = panes.Pane(
            id=7, tab_id=7, os_window_id=1, title="", cwd="", pid=0,
            cmdline=(), process="", is_focused=False, is_self=False)
        tab = panes.Tab(id=7, os_window_id=1, title="", layout="splits",
                        is_active=True, panes=(pane,))
        collided = panes.Workspace(
            os_windows=(panes.OSWindow(id=1, is_focused=True, tabs=(tab,)),))
        with self.assertRaises(panes.AmbiguousTarget) as caught:
            collided.find(7)
        self.assertIn("tab:7", str(caught.exception))
        self.assertIn("pane:7", str(caught.exception))
        # ...and the explicit forms still resolve.
        self.assertIsInstance(collided.find("tab:7"), panes.Tab)
        self.assertIsInstance(collided.find("pane:7"), panes.Pane)

    # --- tree() ---------------------------------------------------------

    def test_tree_shape(self):
        lines = self.ws.tree().splitlines()
        # one os-window + 12 tabs + 16 panes
        self.assertEqual(len(lines), 1 + 12 + 16)
        self.assertEqual(lines[0], "os-window 1 *")
        self.assertEqual(lines[1], "|-- tab 2 * [splits] orchestrator")
        self.assertTrue(lines[2].startswith("|   |-- pane 4 "))
        self.assertIn("pane 49 * (self)", lines[3])
        self.assertTrue(lines[-1].startswith("    `-- "),
                        "the last tab and its last pane both close the tree")

    def test_tree_marks_exactly_one_focused_pane(self):
        pane_line = re.compile(r"^[|` ]*[|`]-- pane (\d+)( \*)?")
        marked = [m.group(1) for m in
                  (pane_line.match(l) for l in self.ws.tree().splitlines())
                  if m and m.group(2)]
        self.assertEqual(marked, ["49"],
                         "twelve panes carry is_focused; exactly one is focused")

    def test_tree_is_one_renderer(self):
        self.assertEqual(self.ws.tree(), panes.parse(self.state).tree())

    # --- helpers absorbed from remote.py --------------------------------

    def test_normalize_target(self):
        self.assertEqual(panes.normalize_target("pane:12"), ("pane", "12"))
        self.assertEqual(panes.normalize_target("win:12"), ("pane", "12"))
        self.assertEqual(panes.normalize_target("tab:12"), ("tab", "12"))
        self.assertEqual(panes.normalize_target("session:12"), ("tab", "12"))
        self.assertEqual(panes.normalize_target("12"), (None, "12"))
        self.assertEqual(panes.normalize_target(12), (None, "12"))
        self.assertEqual(panes.normalize_target("nope:12"), (None, "nope:12"))

    def test_resolve_target_against_the_recording(self):
        self.assertEqual(panes.resolve_target("pane:49", self.state), ("pane", "49"))
        self.assertEqual(panes.resolve_target("tab:2", self.state), ("tab", "2"))
        self.assertEqual(panes.resolve_target(153, self.state), ("pane", "153"))

    def test_process_name_takes_the_innermost_foreground_process(self):
        raw = self.state[0]["tabs"][0]["windows"][1]
        self.assertEqual(panes.process_name(raw), self.ws.find_pane(49).process)
        self.assertEqual(panes.process_name({}), "")

    def test_tab_is_active_falls_back_when_the_key_is_absent(self):
        os_window = {"is_focused": True}
        self.assertTrue(panes.tab_is_active(
            os_window, {}, [{"is_focused": True}]))
        self.assertFalse(panes.tab_is_active(
            {"is_focused": False}, {}, [{"is_focused": True}]))
        self.assertFalse(panes.tab_is_active(
            os_window, {"is_active": False}, [{"is_focused": True}]))

    def test_focused_window_helper(self):
        windows = [{"id": 1}, {"id": 2, "is_focused": True}]
        self.assertEqual(panes.focused_window(windows)["id"], 2)
        self.assertEqual(panes.focused_window([])  , {})


class SplitArgvContractTests(unittest.TestCase):
    """The argv contract this module owns.

    The exhaustive direction/cwd/hold/porcelain matrix is test_panes_split.py;
    what is asserted here is the part the model layer promises: anchor= maps
    onto --next-to, and split() is specified to return an int.
    """

    def test_anchor_maps_to_next_to(self):
        argv = panes.split_argv("right", anchor=118)
        self.assertIn("--next-to=id:118", argv)
        self.assertEqual(argv[0], "launch")
        self.assertIn("--location=vsplit", argv)

    def test_no_anchor_emits_no_next_to(self):
        self.assertNotIn(
            "--next-to", " ".join(panes.split_argv("right")))

    def test_direction_words(self):
        for direction, location in (("right", "vsplit"), ("left", "vsplit-before"),
                                    ("down", "hsplit"), ("up", "hsplit-before"),
                                    ("vsplit", "vsplit"), ("hsplit", "hsplit")):
            with self.subTest(direction=direction):
                self.assertIn(f"--location={location}",
                              panes.split_argv(direction, anchor=1))

    def test_unknown_direction_refuses(self):
        with self.assertRaises(panes.PaneError):
            panes.split_argv("sideways")

    def test_command_goes_after_a_double_dash(self):
        argv = panes.split_argv("down", anchor=5, command=["make", "test"])
        self.assertEqual(argv[-3:], ["--", "make", "test"])

    def test_split_returns_int(self):
        self.assertEqual(
            panes.split.__annotations__.get("return"), "int",
            "split() returning the new pane id is the point of the module")


class PaneIdMapperTests(unittest.TestCase):
    """`index`/`tab_index`/`pane_for_pid`/`locate`.

    The ancestry walk is exercised against a stubbed `_parent_pid` rather than
    the live `/proc`, so the test states the process tree it depends on instead
    of inheriting whatever the machine happens to be running.
    """

    @classmethod
    def setUpClass(cls):
        cls.ws = panes.parse(json.loads(FIXTURE.read_text()))

    def setUp(self):
        self._real_parent = panes._parent_pid
        self.addCleanup(setattr, panes, "_parent_pid", self._real_parent)

    def _tree(self, edges):
        panes._parent_pid = lambda pid: edges.get(pid, 0)

    def test_index_keys_every_live_pane(self):
        index = self.ws.index()
        self.assertEqual(sorted(index), sorted(p.id for p in self.ws.panes()))
        self.assertIs(index[49], self.ws.find_pane(49))

    def test_tab_index_keys_every_live_tab(self):
        index = self.ws.tab_index()
        self.assertEqual(sorted(index), sorted(t.id for t in self.ws.tabs()))
        self.assertIs(index[2], self.ws.find_tab(2))

    def test_pane_for_pid_finds_the_pane_that_owns_a_descendant(self):
        # 999 -> 998 -> 57891, and 57891 is pane 49's launched process.
        self._tree({999: 998, 998: 57891})
        self.assertEqual(self.ws.pane_for_pid(999).id, 49)

    def test_pane_for_pid_accepts_the_pane_process_itself(self):
        self._tree({})
        self.assertEqual(self.ws.pane_for_pid(57891).id, 49)

    def test_pane_for_pid_is_none_outside_every_pane(self):
        # a chain that reaches init without passing through any pane
        self._tree({4242: 4241, 4241: 1})
        self.assertIsNone(self.ws.pane_for_pid(4242))

    def test_pane_for_pid_is_bounded_when_ancestry_cycles(self):
        # a pid whose parent chain loops must terminate, not hang
        self._tree({500: 501, 501: 500})
        self.assertIsNone(self.ws.pane_for_pid(500, max_hops=8))

    def test_pane_for_pid_stops_at_max_hops(self):
        # a chain longer than the bound must not resolve, proving the bound
        # is enforced rather than merely present
        self._tree({10: 11, 11: 12, 12: 57891})
        self.assertIsNone(self.ws.pane_for_pid(10, max_hops=2))
        self.assertEqual(self.ws.pane_for_pid(10, max_hops=3).id, 49)

    def test_locate_keeps_unresolved_pids_as_none(self):
        self._tree({999: 57891})
        found = self.ws.locate([999, 4242])
        self.assertEqual(sorted(found), [999, 4242])
        self.assertEqual(found[999].id, 49)
        self.assertIsNone(found[4242])


if __name__ == "__main__":
    unittest.main()
