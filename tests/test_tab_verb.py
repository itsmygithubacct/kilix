"""`kilix tab` — the shell-string branch, the argv branch, and `--`.

`tab new "cd ~/src && codex --yolo"` contains `&&`, which only means anything
to a shell; `tab new vim notes.txt` is an argv vector. Getting this wrong is
silent, so both branches and the `--` escape hatch are pinned here.

kilix_sdk.panes is stubbed: this file tests the verb layer.
"""

import importlib.util
import io
import json
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "kitten_ls.json"


class FakePane:
    def __init__(self, raw, tab_id):
        self.id = raw["id"]
        self.tab_id = tab_id
        self.title = raw.get("title", "")
        self.columns = raw.get("columns")
        self.lines = raw.get("lines")
        self.is_self = raw.get("is_self", False)
        self.is_focused = raw.get("is_focused", False)


class FakeTab:
    def __init__(self, raw):
        self.id = raw["id"]
        self.title = raw.get("title", "")
        self.layout = raw.get("layout", "")
        self.is_active = raw.get("is_active", False)
        self.panes = tuple(FakePane(w, self.id) for w in raw["windows"])


class FakeWorkspace:
    """Built from the recorded `kitten @ ls`, not hand-written."""

    def __init__(self):
        raw = json.loads(FIXTURE.read_text())
        self._tabs = tuple(FakeTab(t) for o in raw for t in o["tabs"])

    def tabs(self):
        return iter(self._tabs)

    def panes(self):
        for tab in self._tabs:
            yield from tab.panes

    def me(self):
        for pane in self.panes():
            if pane.is_self:
                return pane
        return None

    def tree(self):
        return "\n".join(f"tab {t.id} {t.title}" for t in self._tabs)


class RecordingPanes(types.ModuleType):
    def __init__(self):
        super().__init__("kilix_sdk.panes")
        self.calls = []
        self.workspace = FakeWorkspace()

    def new_tab(self, **kwargs):
        self.calls.append(("new_tab", kwargs))
        return 91

    def snapshot(self, **kwargs):
        return self.workspace

    def focus(self, target):
        self.calls.append(("focus", {"target": target}))

    def move_tab(self, offset):
        self.calls.append(("move_tab", {"offset": offset}))

    def close(self, target, **kwargs):
        self.calls.append(("close", dict(kwargs, target=target)))

    def rename_tab(self, target, title):
        self.calls.append(("rename_tab", {"target": target, "title": title}))

    def split(self, direction="right", **kwargs):
        self.calls.append(("split", dict(kwargs, direction=direction)))
        return 119


def install_stub():
    panes = RecordingPanes()
    package = types.ModuleType("kilix_sdk")
    package.panes = panes
    package.__path__ = []
    sys.modules["kilix_sdk"] = package
    sys.modules["kilix_sdk.panes"] = panes
    spec = importlib.util.spec_from_file_location(
        "kilix_remote_tab_under_test", ROOT / "config" / "remote.py")
    assert spec is not None and spec.loader is not None
    remote = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(remote)
    return remote, panes


class ShellStringVersusArgv(unittest.TestCase):
    def setUp(self):
        self.remote, self.panes = install_stub()

    def new_tab_kwargs(self):
        return [c for c in self.panes.calls if c[0] == "new_tab"][0][1]

    def test_single_argument_with_metacharacters_is_a_shell_string(self):
        self.remote.main(["tab", "new", "cd ~/src && codex --yolo"])
        kwargs = self.new_tab_kwargs()
        self.assertEqual(kwargs["shell_string"], "cd ~/src && codex --yolo")
        self.assertNotIn("command", kwargs)

    def test_each_metacharacter_triggers_the_shell_branch(self):
        for char in ("&", "|", ";", "<", ">", "(", ")", "$", "`", "\n"):
            with self.subTest(char=char):
                self.remote, self.panes = install_stub()
                self.remote.main(["tab", "new", f"echo x{char}y"])
                self.assertIn("shell_string", self.new_tab_kwargs())

    def test_multiple_arguments_are_argv(self):
        self.remote.main(["tab", "new", "vim", "notes.txt"])
        kwargs = self.new_tab_kwargs()
        self.assertEqual(list(kwargs["command"]), ["vim", "notes.txt"])
        self.assertNotIn("shell_string", kwargs)

    def test_single_plain_argument_is_argv(self):
        self.remote.main(["tab", "new", "htop"])
        kwargs = self.new_tab_kwargs()
        self.assertEqual(list(kwargs["command"]), ["htop"])
        self.assertNotIn("shell_string", kwargs)

    def test_double_dash_forces_argv_even_with_metacharacters(self):
        self.remote.main(["tab", "new", "--", "cat", "weird$file"])
        kwargs = self.new_tab_kwargs()
        self.assertEqual(list(kwargs["command"]), ["cat", "weird$file"])
        self.assertNotIn("shell_string", kwargs)

    def test_double_dash_forces_argv_for_a_lone_shell_looking_argument(self):
        self.remote.main(["tab", "new", "--", "a && b"])
        kwargs = self.new_tab_kwargs()
        self.assertEqual(list(kwargs["command"]), ["a && b"])
        self.assertNotIn("shell_string", kwargs)

    def test_bare_tab_opens_a_tab_with_no_command(self):
        self.remote.main(["tab"])
        kwargs = self.new_tab_kwargs()
        self.assertEqual(list(kwargs.get("command", ())), [])


class TabNavigation(unittest.TestCase):
    def setUp(self):
        self.remote, self.panes = install_stub()

    def test_left_and_right_focus_rather_than_reorder(self):
        self.remote.main(["tab", "left"])
        self.remote.main(["tab", "right"])
        kinds = [c[0] for c in self.panes.calls]
        self.assertEqual(kinds, ["focus", "focus"])
        self.assertNotIn("move_tab", kinds)

    def test_move_reorders_with_a_signed_offset(self):
        self.remote.main(["tab", "move", "left"])
        self.remote.main(["tab", "move", "right"])
        offsets = [c[1]["offset"] for c in self.panes.calls if c[0] == "move_tab"]
        self.assertEqual(offsets, [-1, 1])

    def test_rename_targets_this_tab_by_default(self):
        self.remote.main(["tab", "rename", "build"])
        call = [c for c in self.panes.calls if c[0] == "rename_tab"][0][1]
        self.assertEqual(call["title"], "build")
        self.assertEqual(call["target"], "tab:2")   # the fixture's self pane


class MachineOutput(unittest.TestCase):
    def setUp(self):
        self.remote, self.panes = install_stub()

    def test_porcelain_prints_only_the_id(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.remote.main(["tab", "new", "--porcelain"])
        self.assertEqual(buffer.getvalue().strip(), "91")

    def test_list_json_is_parseable_and_covers_the_fixture(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.remote.main(["tab", "list", "--json"])
        parsed = json.loads(buffer.getvalue())
        self.assertEqual(len(parsed), 12)
        self.assertEqual(sum(len(t["panes"]) for t in parsed), 16)
        self.assertIn("is_active", parsed[0])


if __name__ == "__main__":
    unittest.main()
