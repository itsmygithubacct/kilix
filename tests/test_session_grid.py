"""Session grids: twelve coding sessions per tab in two rows, shared by every launcher."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import agent_control as control  # noqa: E402
import agent_programs  # noqa: E402
import session_grid  # noqa: E402


def pane(number, *, tab=1, layout="splits", window=1):
    return {"id": number, "tab_id": tab, "tab_title": "shell", "os_window_id": window,
            "title": f"pane {number}", "cwd": "/tmp", "layout": layout,
            "env": {"KITTY_PTY_BROKER_SESSION": f"{number:032x}"}, "user_vars": {},
            "foreground_processes": []}


class FakeEngine(control.Client):
    """Tabs, user vars, `--location last`, goto-layout and closing, like the engine."""

    def __init__(self, sessions_layout=True):
        self.caller = 1
        self.command = ["fake", "@", "--to", "fake:grid"]
        self.panes = [pane(1)]
        self.calls = []
        self.overlays = {}          # tab id -> panes that are overlays (share a group)
        self.sessions_layout = sessions_layout
        self.mutex = threading.Lock()

    def snapshot(self):
        with self.mutex:
            panes = copy.deepcopy(self.panes)
        for p in panes:
            members = sum(q["tab_id"] == p["tab_id"] for q in panes)
            p["tab_window_groups"] = members - self.overlays.get(p["tab_id"], 0)
        return panes

    def run(self, args, payload=None):
        with self.mutex:
            self.calls.append(list(args))
            if args == ["launch", "--help"]:
                return "--keep-focus --source-window --next-to --var --location --hold"
            if args[0] == "launch":
                match = args[args.index("--match") + 1]
                if match.startswith("env:"):
                    anchor = next(p for p in self.panes
                                  if p["env"]["KITTY_PTY_BROKER_SESSION"] == match.split("=", 1)[1])
                else:
                    anchor = next(p for p in self.panes if p["id"] == int(match.split(":")[1]))
                kind = args[args.index("--type") + 1]
                number = max(p["id"] for p in self.panes) + 1
                if kind == "tab":
                    new = pane(number, tab=max(p["tab_id"] for p in self.panes) + 1, window=anchor["os_window_id"])
                    new["tab_title"] = args[args.index("--tab-title") + 1]
                else:
                    new = pane(number, tab=anchor["tab_id"], layout=anchor["layout"], window=anchor["os_window_id"])
                    new["tab_title"] = anchor["tab_title"]
                new["title"] = args[args.index("--title") + 1]
                for i, arg in enumerate(args[:args.index("--")]):
                    if arg == "--var":
                        key, value = args[i + 1].split("=", 1)
                        new["user_vars"][key] = value
                self.panes.append(new)
                return str(number)
            if args[0] == "goto-layout":
                if args[-1] == session_grid.LAYOUT and not self.sessions_layout:
                    raise control.ControlError("The layout sessions is unknown or disabled")
                selected = int(args[args.index("--match") + 1].split(":")[1])
                tab = next(p["tab_id"] for p in self.panes if p["id"] == selected)
                for p in self.panes:
                    if p["tab_id"] == tab:
                        p["layout"] = args[-1]
            return ""

    def close(self, pane_id):
        with self.mutex:
            self.panes = [p for p in self.panes if p["id"] != pane_id]

    def tab_counts(self):
        counts = {}
        for p in self.panes:
            if p["user_vars"].get(session_grid.GROUP_VAR):
                counts[p["tab_id"]] = counts.get(p["tab_id"], 0) + 1
        return counts

    def launches(self):
        return [c for c in self.calls if c[0] == "launch" and c != ["launch", "--help"]]


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.settings = base / "settings.conf"
        env = {"HOME": str(base), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
               "KILIX_STORAGE_HOME": str(base / "store"), "KILIX_SESSION_HOME": str(base / "session"),
               "GPU_TERMINAL_SETTINGS_FILE": str(self.settings)}
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.engine = FakeEngine()

    def place(self, title="codex", **kwargs):
        source = control.describe(self.engine.resolve(1))
        return session_grid.place(self.engine, source=source, argv=["codex"], cwd="/tmp", title=title, **kwargs)

    def setting(self, value):
        self.settings.write_text(f"KILIX_CODING_SESSION_GRID={value}\n")


class ArithmeticTests(unittest.TestCase):
    def test_shape_is_two_rows_of_ceil_half_columns(self):
        expected = {0: [], 1: [1], 2: [2], 3: [2, 1], 4: [2, 2], 5: [2, 2, 1], 6: [2, 2, 2],
                    7: [2, 2, 2, 1], 8: [2, 2, 2, 2], 9: [2, 2, 2, 2, 1], 10: [2] * 5,
                    11: [2] * 5 + [1], 12: [2] * 6, 13: [2] * 6 + [1]}
        for n, columns in expected.items():
            self.assertEqual(session_grid.shape(n), columns, n)
            self.assertEqual(sum(columns), n)
            self.assertLessEqual(max(columns, default=0), 2)

    def test_slots_fill_columns_top_to_bottom_and_never_move_on_add(self):
        self.assertEqual(session_grid.slot(0, 1), (0, 0))
        self.assertEqual([session_grid.slot(i, 5) for i in range(5)],
                         [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)])
        for n in range(2, 13):
            for i in range(n):
                self.assertEqual(session_grid.slot(i, n), session_grid.slot(i, n + 1))
        with self.assertRaises(ValueError):
            session_grid.slot(5, 5)

    def test_group_marker_is_stable_and_name_specific(self):
        self.assertEqual(session_grid.group("agent-sessions"), session_grid.group("agent-sessions"))
        self.assertNotEqual(session_grid.group("agent-sessions"), session_grid.group("claudex-team"))

    def test_agent_commands_are_recognised_by_program_name(self):
        self.assertTrue(session_grid.is_agent_command(["/usr/local/bin/codex", "--yolo"]))
        self.assertTrue(session_grid.is_agent_command(["omp"]))
        self.assertFalse(session_grid.is_agent_command(["htop"]))
        self.assertFalse(session_grid.is_agent_command([]))


class PlacementTests(Isolated):
    def test_twelve_per_tab_then_a_new_grid_tab(self):
        results = [self.place(f"s{i}") for i in range(13)]
        self.assertEqual([r["count"] for r in results], list(range(1, 13)) + [1])
        self.assertEqual([r["new_tab"] for r in results], [True] + [False] * 11 + [True])
        self.assertEqual([r["slot"] for r in results[:5]], [[0, 0], [0, 1], [1, 0], [1, 1], [2, 0]])
        self.assertEqual(results[11]["slot"], [5, 1])
        self.assertEqual(sorted(self.engine.tab_counts().values()), [1, 12])
        titles = {p["tab_title"] for p in self.engine.panes if p["user_vars"]}
        self.assertEqual(titles, {"agent-sessions", "agent-sessions 2"})
        for argv in self.engine.launches():
            self.assertIn("--keep-focus", argv)
            if argv[argv.index("--type") + 1] == "window":
                self.assertEqual(argv[argv.index("--location") + 1], "last")
        layouts = [c for c in self.engine.calls if c[0] == "goto-layout"]
        self.assertEqual(len(layouts), 2)
        self.assertTrue(all(c[-1] == "sessions" for c in layouts))
        self.assertTrue(all(r["layout"] == "sessions" and r["fallback"] is None for r in results))

    def test_new_panes_append_in_order_so_slots_match_launch_order(self):
        for i in range(7):
            self.place(f"s{i}")
        grid = session_grid.grids(self.engine.snapshot())[0]
        self.assertEqual([p["title"] for p in grid["panes"]], [f"s{i}" for i in range(7)])
        self.assertEqual(grid["panes"][6]["slot"], [3, 0])

    def test_closed_panes_free_room_and_the_newest_grid_with_room_wins(self):
        first = [self.place() for _ in range(12)]
        overflow = self.place()
        self.engine.close(first[3]["pane"]["pane_id"])
        nxt = self.place()
        self.assertEqual(nxt["tab_id"], overflow["tab_id"])
        self.engine.close(overflow["pane"]["pane_id"])
        self.engine.close(nxt["pane"]["pane_id"])
        refill = self.place()
        self.assertEqual(refill["tab_id"], first[0]["tab_id"])
        self.assertEqual(refill["count"], 12)

    def test_manual_panes_count_and_overlays_do_not(self):
        first = self.place()
        tab = first["tab_id"]
        for i in range(100, 110):
            self.engine.panes.append(pane(i, tab=tab, layout="sessions"))
        self.assertEqual(self.place()["count"], 12)
        overflow = self.place()
        self.assertTrue(overflow["new_tab"])
        self.engine.close(overflow["pane"]["pane_id"])
        self.assertTrue(self.place()["new_tab"])
        self.engine.panes = [p for p in self.engine.panes if p["tab_id"] == tab or p["id"] == 1]
        self.engine.overlays[tab] = 1
        self.assertEqual(self.place()["tab_id"], tab)

    def test_other_names_windows_and_changed_layouts_are_not_grids(self):
        a = self.place(name="first")
        b = self.place(name="second")
        self.assertNotEqual(a["tab_id"], b["tab_id"])
        for p in self.engine.panes:
            if p["tab_id"] == a["tab_id"]:
                p["layout"] = "splits"
        self.assertNotEqual(self.place(name="first")["tab_id"], a["tab_id"])
        self.engine.panes[0]["os_window_id"] = 2
        self.engine.panes.append(pane(500, tab=99, window=2))
        moved = self.place(name="second")
        self.assertTrue(moved["new_tab"])

    def test_into_joins_that_grid_and_new_always_opens_one(self):
        a = self.place(name="team")
        b = self.place(name="team", new=True)
        self.assertTrue(b["new_tab"])
        self.assertNotEqual(a["tab_id"], b["tab_id"])
        joined = self.place(name="team", into=a["pane"]["pane_id"])
        self.assertEqual(joined["tab_id"], a["tab_id"])
        with self.assertRaisesRegex(control.ControlError, "not in a 'other' session grid"):
            self.place(name="other", into=a["pane"]["pane_id"])
        with self.assertRaisesRegex(control.ControlError, "not in a"):
            self.place(name="team", into=1)
        for _ in range(10):
            self.place(name="team", into=a["pane"]["pane_id"])
        with self.assertRaisesRegex(control.ControlError, "already holds 12"):
            self.place(name="team", into=a["pane"]["pane_id"])
        with self.assertRaises(control.ControlError):
            self.place(name="team", new=True, into=a["pane"]["pane_id"])

    def test_into_a_grid_in_another_os_window(self):
        other = pane(50, tab=50, layout="sessions", window=2)
        other["user_vars"].update({session_grid.GROUP_VAR: session_grid.group("team"),
                                   session_grid.NAME_VAR: "team"})
        self.engine.panes.append(other)
        joined = self.place(name="team", into=50)
        self.assertEqual((joined["tab_id"], joined["pane"]["os_window_id"], joined["count"]), (50, 2, 2))
        # Without --into the caller's own OS window gets a new grid instead.
        self.assertNotEqual(self.place(name="team")["tab_id"], 50)

    def test_the_receipt_describes_the_pane_after_the_layout_change(self):
        result = self.place()
        self.assertEqual((result["layout"], result["pane"]["layout"]), ("sessions", "sessions"))

    def test_engine_without_the_layout_falls_back_to_plain_tabs(self):
        self.engine.sessions_layout = False
        first = self.place()
        self.assertEqual(first["layout"], "splits")
        self.assertIn("lacks the sessions layout", first["fallback"])
        second = self.place()
        self.assertTrue(second["new_tab"])
        self.assertEqual(len(self.engine.launches()), 2)

    def test_older_engine_options_refuse_before_any_launch(self):
        self.engine.run = lambda args, payload=None: "--keep-focus"
        with self.assertRaisesRegex(control.ControlError, "lacks required"):
            self.place()
        self.assertEqual(len(self.engine.panes), 1)

    def test_thirteen_concurrent_launches_fill_twelve_plus_one(self):
        # A slow snapshot makes every unlocked placement see the same state.
        snapshot = self.engine.snapshot
        def slow():
            time.sleep(0.02)
            return snapshot()
        self.engine.snapshot = slow
        with ThreadPoolExecutor(13) as pool:
            results = list(pool.map(lambda i: self.place(f"c{i}"), range(13)))
        self.assertEqual(sorted(self.engine.tab_counts().values()), [1, 12])
        self.assertEqual(sorted(r["count"] for r in results), sorted(list(range(1, 13)) + [1]))

    def test_retarget_names_the_anchor_before_the_launch(self):
        seen = []
        first = self.place(retarget=lambda identity: seen.append((dict(identity), len(self.engine.launches()))))
        self.place(retarget=lambda identity: seen.append((dict(identity), len(self.engine.launches()))))
        self.assertEqual(seen[0], ({"pane_id": 1, "broker": f"{1:032x}"}, 0))
        self.assertEqual(seen[1], ({"pane_id": first["pane"]["pane_id"], "broker": first["pane"]["broker"]}, 1))

    def test_lock_is_private_and_per_socket(self):
        self.place()
        folder = Path(os.environ["KILIX_SESSION_HOME"]) / "session-grid"
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        locks = list(folder.iterdir())
        self.assertEqual(len(locks), 1)
        self.assertEqual(locks[0].stat().st_mode & 0o777, 0o600)
        # Another run store on the same engine shares the lock ...
        with mock.patch.dict(os.environ, {"KILIX_STORAGE_HOME": str(Path(self.tmp.name) / "other"),
                                          "KILIX_AGENT_RUNS_HOME": str(Path(self.tmp.name) / "runs")}):
            self.place()
        self.assertEqual(list(folder.iterdir()), locks)
        # ... and another engine has its own.
        self.engine.command = ["fake", "@", "--to", "fake:other"]
        self.place()
        self.assertEqual(len(list(folder.iterdir())), 2)

    def test_grids_lists_newest_first_with_counts(self):
        for _ in range(13):
            self.place()
        listed = session_grid.grids(self.engine.snapshot())
        self.assertEqual([g["count"] for g in listed], [1, 12])
        self.assertEqual(listed[0]["name"], "agent-sessions")
        self.assertEqual(session_grid.grids(self.engine.snapshot(), name="nope"), [])


class SettingTests(Isolated):
    def test_on_by_default_and_off_by_setting(self):
        self.assertTrue(session_grid.enabled())
        self.setting("off")
        self.assertFalse(session_grid.enabled())
        self.setting("on")
        self.assertTrue(session_grid.enabled())


class AgentControlTests(Isolated):
    def setUp(self):
        super().setUp()
        program = mock.patch.object(agent_programs, "resolve_agent_command", return_value="/opt/bin/codex")
        program.start()
        self.addCleanup(program.stop)

    def args(self, *extra):
        return control.parser().parse_args(["new-tab", "1", "--expect-broker", f"{1:032x}", "--agent", "codex",
                                            "--title", "work", "--cwd", self.tmp.name, *extra])

    def test_new_tab_joins_the_grid_by_default(self):
        first = control.launch(self.engine, self.args())
        second = control.launch(self.engine, self.args("--grid", "reviews"))
        third = control.launch(self.engine, self.args())
        self.assertEqual(first["status"], "created")
        self.assertEqual(first["grid"]["grid"], "agent-sessions")
        self.assertEqual(second["grid"]["grid"], "reviews")
        self.assertEqual(third["pane"]["tab_id"], first["pane"]["tab_id"])
        self.assertEqual(third["grid"]["count"], 2)
        argv = self.engine.launches()[0]
        self.assertEqual(argv[argv.index("--") + 1:], ["/opt/bin/codex"])

    def test_own_tab_and_the_setting_keep_one_tab_per_session(self):
        own = control.launch(self.engine, self.args("--own-tab"))
        self.assertNotIn("grid", own)
        self.setting("off")
        off = control.launch(self.engine, self.args())
        self.assertNotIn("grid", off)
        self.assertFalse(any(c[0] == "goto-layout" for c in self.engine.calls))

    def test_dry_run_names_the_grid_and_launches_nothing(self):
        result = control.launch(self.engine, self.args("--dry-run"))
        self.assertEqual(result["placement"]["grid"], "agent-sessions")
        self.assertEqual(self.engine.launches(), [])

    def test_explicit_argv_joins_only_when_it_is_a_coding_client(self):
        args = self.args()
        self.assertIn("grid", control.launch(self.engine, args, explicit_argv=["/usr/bin/claude"]))
        self.assertNotIn("grid", control.launch(self.engine, args, explicit_argv=["htop"]))

    def test_structured_launch_retargets_without_changing_the_request(self):
        from agent_actions import GuardedClient
        identity = {"pane_id": 1, "broker": f"{1:032x}"}
        request = {"source": identity, "target": dict(identity), "timeout": 30}
        guarded = GuardedClient(self.engine, request, lambda: None)
        control.launch(guarded, self.args())
        second = control.launch(guarded, self.args())
        self.assertEqual(request["target"], identity)
        last = self.engine.launches()[-1]
        anchor = guarded.launch_target
        self.assertNotEqual(anchor["pane_id"], 1)
        self.assertEqual(last[last.index("--match") + 1], "env:KITTY_PTY_BROKER_SESSION=" + anchor["broker"])
        self.assertEqual(second["grid"]["count"], 2)


class NewTabVerbTests(Isolated):
    def run_verb(self, function, argv, client=None):
        import remote
        fake_panes = mock.Mock()
        fake_panes.PaneError = RuntimeError
        fake_panes.new_tab.return_value = 77
        out = io.StringIO()
        with mock.patch.object(control, "Client", return_value=client or self.engine), \
                mock.patch.object(remote, "load_panes", return_value=fake_panes), \
                contextlib.redirect_stdout(out):
            code = getattr(remote, function)(argv)
        return code, out.getvalue(), fake_panes

    def test_coding_clients_join_the_grid_and_other_programs_get_a_tab(self):
        code, out, panes = self.run_verb("cmd_new_tab", ["--", "codex", "--yolo"])
        self.assertEqual(code, 0)
        self.assertIn("session grid 'agent-sessions'", out)
        panes.new_tab.assert_not_called()
        argv = self.engine.launches()[0]
        self.assertEqual(argv[argv.index("--") + 1:], ["codex", "--yolo"])
        self.assertNotIn("--hold", argv)
        code, out, panes = self.run_verb("cmd_new_tab", ["htop"])
        panes.new_tab.assert_called_once()
        self.assertIn("opened 77", out)

    def test_own_tab_setting_and_no_kilix_pane_fall_back_to_a_tab(self):
        _, _, panes = self.run_verb("cmd_new_tab", ["--own-tab", "codex"])
        panes.new_tab.assert_called_once()
        self.setting("off")
        _, _, panes = self.run_verb("cmd_new_tab", ["codex"])
        panes.new_tab.assert_called_once()
        self.setting("on")
        with mock.patch.object(control, "Client", side_effect=control.ControlError("no connection")):
            import remote
            self.assertIsNone(remote.grid_new_tab(["codex"], "current", "", False))
        # remote.py loaded without config/ on the path (as its tests load it).
        with mock.patch.dict(sys.modules, {"session_grid": None}):
            self.assertIsNone(remote.grid_new_tab(["codex"], "current", "", False))
        self.assertEqual(self.engine.launches(), [])

    def test_tab_new_shell_line_with_a_coding_client_joins_the_grid(self):
        code, out, panes = self.run_verb("cmd_tab", ["new", "--porcelain", "cd /tmp && claude"])
        self.assertEqual(code, 0)
        panes.new_tab.assert_not_called()
        argv = self.engine.launches()[0]
        self.assertEqual(argv[argv.index("--") + 2:], ["-lc", "cd /tmp && claude"])
        self.assertEqual(out.strip(), str(self.engine.panes[-1]["id"]))


class SessionCliTests(Isolated):
    def test_grid_add_and_list(self):
        from kilix_session import cli
        out = io.StringIO()
        with mock.patch.object(control, "Client", return_value=self.engine), contextlib.redirect_stdout(out):
            code = cli.main(["grid", "add", "--name", "claudex-team", "--new", "--title", "orchestrator",
                             "--cwd", self.tmp.name, "--", "bash", "-c", "exec sleep 1"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn('"new_tab": true', out.getvalue())
        out = io.StringIO()
        with mock.patch.object(control, "Client", return_value=self.engine), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["grid", "list"]), 0)
        self.assertIn('"name": "claudex-team"', out.getvalue())
        self.assertIn('"capacity": 12', out.getvalue())

    def test_grid_add_passes_user_variables_and_environment(self):
        from kilix_session import cli
        out = io.StringIO()
        with mock.patch.object(control, "Client", return_value=self.engine), contextlib.redirect_stdout(out):
            code = cli.main(["grid", "add", "--title", "w", "--cwd", self.tmp.name, "--var", "claudex_team=worker_1",
                             "--env", "PATH=/opt/bin:/usr/bin", "--", "codex"])
        self.assertEqual(code, 0, out.getvalue())
        launch = self.engine.launches()[-1]
        self.assertEqual(launch[launch.index("--env") + 1], "PATH=/opt/bin:/usr/bin")
        self.assertIn("claudex_team=worker_1", launch[:launch.index("--")])
        for bad in ([("kilix_session_group", "x")], [("1x", "y")], [("ok", "two\nlines")]):
            with self.assertRaises(control.ControlError):
                self.place(extra_vars=bad)
        with self.assertRaises(control.ControlError):
            self.place(extra_env=[("A B", "c")])
        self.assertEqual(len(self.engine.launches()), 1)
        with mock.patch.object(control, "Client", return_value=self.engine), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["grid", "add", "--title", "w", "--var", "novalue", "--", "codex"])


if __name__ == "__main__":
    unittest.main()
