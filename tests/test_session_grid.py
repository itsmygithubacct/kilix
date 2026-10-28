"""Session grids: twelve coding sessions per tab in two rows, shared by every launcher."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import io
import multiprocessing
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


def private_lock_bases(test, base):
    """Point the per-user lock folder at a private runtime directory under base."""
    runtime = Path(base) / "run" / str(os.getuid())
    runtime.mkdir(parents=True)
    runtime.chmod(0o700)
    (Path(base) / "tmp").mkdir()
    for name, value in (("RUNTIME_BASE", str(Path(base) / "run")), ("TMP_BASE", str(Path(base) / "tmp"))):
        patch = mock.patch.object(session_grid, name, value)
        patch.start()
        test.addCleanup(patch.stop)
    return runtime / "kilix-session-grid"


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
        self.locks = private_lock_bases(self, base / "locks")
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

    def test_agent_commands_are_recognised_by_executable_position(self):
        launches = {
            ("/usr/local/bin/codex", "--yolo"): "codex", ("omp",): "omp",
            ("env", "TEST=1", "codex"): "codex", ("env", "-i", "A=1", "B=2", "claude", "-p"): "claude",
            ("env", "-u", "X", "--chdir=/tmp", "--", "codex"): "codex", ("env", "env", "grok"): "grok",
            ("bash", "-lc", "codex --yolo"): "codex", ("sh", "-c", "A=1 exec env B=2 claude"): "claude",
            ("env", "-u", "sh", "sh", "-c", "codex"): "codex",
            ("bash", "-o", "errexit", "-c", "claude"): "claude",
        }
        arguments = [("htop",), (), ("printf", "%s\n", "codex"), ("echo", "claude"), ("env",),
                     ("env", "-S", "codex x"), ("env", "--bogus", "codex"), ("bash", "script.sh", "codex"),
                     ("sh", "-c", "echo codex"), ("sh", "-c", "printf '%s' codex"), ("git", "log", "--author=codex"),
                     # a shell given a script file named like a client runs that file
                     ("bash", "codex"), ("sh", "-e", "claude"), ("bash", "--norc", "codex"),
                     ("bash", "-c", "-x", "codex")]
        for argv, name in launches.items():
            self.assertEqual(session_grid.agent_program(list(argv)), name, argv)
            self.assertTrue(session_grid.is_agent_command(list(argv)), argv)
        for argv in arguments:
            self.assertIsNone(session_grid.agent_program(list(argv)), argv)
            self.assertFalse(session_grid.is_agent_command(list(argv)), argv)

    def test_shell_lines_count_only_their_first_command_word(self):
        lines = {"codex --yolo": "codex", "A=1 B='x y' claude": "claude", "exec codex": "codex",
                 "exec -a name grok": "grok", "env TEST=1 codex": "codex", "codex; echo done": "codex",
                 "  /opt/bin/claude -p hi | tee log": "claude"}
        for line, name in lines.items():
            self.assertEqual(session_grid.agent_program(shell_string=line), name, line)
        for line in ('printf "%s\n" codex', "echo claude", "cd /tmp && claude", "(codex)",
                     "'unterminated codex", "for a in codex; do :; done", "A=codex", ""):
            self.assertIsNone(session_grid.agent_program(shell_string=line), line)


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

    def test_a_grid_whose_marked_panes_all_closed_is_forgotten(self):
        # L1, documented: a grid is the panes carrying its marker.
        first = self.place()
        tab = first["tab_id"]
        self.engine.panes.append(pane(90, tab=tab, layout="sessions"))
        self.engine.close(first["pane"]["pane_id"])
        self.assertEqual(session_grid.grids(self.engine.snapshot()), [])
        again = self.place()
        self.assertTrue(again["new_tab"])
        self.assertNotEqual(again["tab_id"], tab)

    def test_a_marked_pane_moved_to_another_sessions_tab_makes_it_eligible(self):
        # L1, documented: the marker follows the pane.
        first = self.place()
        self.engine.panes.append(pane(90, tab=90, layout="sessions"))
        for p in self.engine.panes:
            if p["id"] == first["pane"]["pane_id"]:
                p["tab_id"] = 90
        joined = self.place()
        self.assertEqual((joined["tab_id"], joined["count"]), (90, 3))
        # A plain (non-sessions) tab never becomes a grid this way.
        for p in self.engine.panes:
            if p["tab_id"] == 90:
                p["layout"] = "splits"
        self.assertTrue(self.place()["new_tab"])

    def test_into_without_a_name_joins_the_grid_that_pane_belongs_to(self):
        first = self.place(name="claudex-team", new=True)
        joined = self.place(into=first["pane"]["pane_id"])
        self.assertEqual((joined["tab_id"], joined["grid"], joined["count"]), (first["tab_id"], "claudex-team", 2))
        launch = self.engine.launches()[-1]
        self.assertIn(f"{session_grid.NAME_VAR}=claudex-team", launch)
        self.assertIn(f"{session_grid.GROUP_VAR}={session_grid.group('claudex-team')}", launch)
        # An unmarked pane, or a marker whose name does not hash to it, is refused.
        with self.assertRaisesRegex(control.ControlError, "no session grid marker; give --name"):
            self.place(into=1)
        forged = pane(70, tab=first["tab_id"], layout="sessions")
        forged["user_vars"] = {session_grid.NAME_VAR: "other", session_grid.GROUP_VAR: session_grid.group("claudex-team")}
        self.engine.panes.append(forged)
        with self.assertRaisesRegex(control.ControlError, "no session grid marker"):
            self.place(into=70)
        # Without --into the default name still applies.
        self.assertEqual(self.place()["grid"], session_grid.DEFAULT_GRID)

    def test_lock_is_private_per_socket_and_ignores_every_home(self):
        self.place()
        self.assertEqual(self.locks.stat().st_mode & 0o777, 0o700)
        locks = list(self.locks.iterdir())
        self.assertEqual(len(locks), 1)
        self.assertEqual(locks[0].stat().st_mode & 0o777, 0o600)
        self.assertFalse((Path(os.environ["KILIX_SESSION_HOME"]) / "session-grid").exists())
        # Other stores, session homes and runtime variables share the lock ...
        other = Path(self.tmp.name) / "other"
        with mock.patch.dict(os.environ, {"KILIX_STORAGE_HOME": str(other / "store"),
                                          "KILIX_SESSION_HOME": str(other / "session"),
                                          "KILIX_AGENT_RUNS_HOME": str(other / "runs"),
                                          "XDG_RUNTIME_DIR": str(other / "xdg")}):
            self.assertEqual(session_grid.lock_folder(), self.locks)
            self.place()
        self.assertEqual(list(self.locks.iterdir()), locks)
        # ... and another engine has its own.
        self.engine.command = ["fake", "@", "--to", "fake:other"]
        self.place()
        self.assertEqual(len(list(self.locks.iterdir())), 2)

    def test_lock_falls_back_to_a_private_tmp_folder(self):
        self.locks.parent.chmod(0o755)
        self.addCleanup(self.locks.parent.chmod, 0o700)
        folder = session_grid.lock_folder()
        self.assertEqual(folder, Path(session_grid.TMP_BASE) / f"kilix-session-grid-{os.getuid()}")
        self.place()
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        # A folder someone else made, or a loose one, is refused, not used.
        folder.chmod(0o755)
        with self.assertRaisesRegex(control.ControlError, "private and owned"):
            self.place()
        folder.chmod(0o700)

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

    def test_the_environment_wins_over_an_existing_settings_file(self):
        from kilix_sdk import settings
        self.settings.write_text("KILIX_CODING_YOLO=on\nKILIX_CODING_SESSION_GRID=on\n")
        with mock.patch.dict(os.environ, {"KILIX_CODING_SESSION_GRID": "off"}):
            self.assertFalse(session_grid.enabled())
            # Only this setting: the file still decides the others.
            self.assertTrue(settings.coding_yolo())
            self.assertEqual(settings.load()["KILIX_CODING_SESSION_GRID"], "on")
        self.setting("off")
        with mock.patch.dict(os.environ, {"KILIX_CODING_SESSION_GRID": "on"}):
            self.assertTrue(session_grid.enabled())
        with mock.patch.dict(os.environ, {"KILIX_CODING_SESSION_GRID": ""}):
            self.assertFalse(session_grid.enabled())

    def test_the_environment_opt_out_with_a_fresh_home(self):
        self.assertFalse(self.settings.exists())
        with mock.patch.dict(os.environ, {"KILIX_CODING_SESSION_GRID": "off"}):
            self.assertFalse(session_grid.enabled())
        self.assertTrue(session_grid.enabled())

    def grid_cli(self, *argv):
        from kilix_session import cli
        out = io.StringIO()
        with mock.patch.object(control, "Client", return_value=self.engine), contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_grid_enabled_reports_the_effective_setting(self):
        self.assertEqual(self.grid_cli("grid", "enabled"), (0, "on\n"))
        self.setting("off")
        self.assertEqual(self.grid_cli("grid", "enabled"), (1, "off\n"))
        with mock.patch.dict(os.environ, {"KILIX_CODING_SESSION_GRID": "on"}):
            self.assertEqual(self.grid_cli("grid", "enabled"), (0, "on\n"))
        self.setting("on")
        with mock.patch.dict(os.environ, {"KILIX_CODING_SESSION_GRID": "off"}):
            self.assertEqual(self.grid_cli("grid", "enabled"), (1, "off\n"))
            # An explicit grid request stays available when the default is off.
            code, out = self.grid_cli("grid", "add", "--title", "w", "--cwd", self.tmp.name, "--", "codex")
            self.assertEqual(code, 0, out)
            self.assertIn('"new_tab": true', out)


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

    def test_the_environment_opt_out_keeps_one_tab_per_session(self):
        self.setting("on")
        with mock.patch.dict(os.environ, {"KILIX_CODING_SESSION_GRID": "off"}):
            self.assertNotIn("grid", control.launch(self.engine, self.args()))
        self.assertIn("grid", control.launch(self.engine, self.args()))

    def test_pane_open_classifies_by_executable_position(self):
        import agent_actions
        identity = {"pane_id": 1, "broker": f"{1:032x}"}

        def pane_open(argv):
            request = {"operation": "pane.open", "dry_run": False, "source": identity, "target": dict(identity),
                       "params": {"argv": argv, "cwd": self.tmp.name, "title": "w", "placement": "new-tab"}}
            return agent_actions.execute(self.engine, request)

        for argv in (["env", "TEST=1", "codex"], ["bash", "-lc", "exec claude"], ["/opt/bin/codex"],
                     ["env", "-u", "sh", "sh", "-c", "codex"]):
            self.assertIn("grid", pane_open(argv), argv)
        for argv in (["printf", "%s\n", "codex"], ["echo", "claude"], ["sh", "-c", "echo codex"],
                     ["bash", "-c", "-x", "codex"]):
            self.assertNotIn("grid", pane_open(argv), argv)

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

    def test_tab_new_shell_line_that_runs_a_coding_client_joins_the_grid(self):
        code, out, panes = self.run_verb("cmd_tab", ["new", "--porcelain", "TEST=1 exec claude -p 'a; b'"])
        self.assertEqual(code, 0)
        panes.new_tab.assert_not_called()
        argv = self.engine.launches()[0]
        self.assertEqual(argv[argv.index("--") + 2:], ["-lc", "TEST=1 exec claude -p 'a; b'"])
        self.assertEqual(out.strip(), str(self.engine.panes[-1]["id"]))

    def test_agent_names_as_arguments_are_not_coding_launches(self):
        for function, argv in (("cmd_tab", ["new", "--porcelain", 'printf "%s\\n" codex']),
                               ("cmd_tab", ["new", "--porcelain", "cd /tmp && claude"]),
                               ("cmd_new_tab", ["--", "echo", "codex"]),
                               ("cmd_new_tab", ["--", "sh", "-c", "echo claude"])):
            code, _, panes = self.run_verb(function, argv)
            self.assertEqual(code, 0, argv)
            panes.new_tab.assert_called_once()
        self.assertEqual(self.engine.launches(), [])

    def test_new_tab_through_an_env_wrapper_joins_the_grid(self):
        code, out, panes = self.run_verb("cmd_new_tab", ["--", "env", "TEST=1", "codex"])
        self.assertEqual(code, 0)
        panes.new_tab.assert_not_called()
        argv = self.engine.launches()[0]
        self.assertEqual(argv[argv.index("--") + 1:], ["env", "TEST=1", "codex"])
        self.assertIn("session grid 'agent-sessions'", out)

    def test_nested_shell_wrappers_preserve_executable_positions_on_public_routes(self):
        cases = (("cmd_new_tab", ["--", "env", "-u", "sh", "sh", "-c", "codex"], True),
                 ("cmd_tab", ["new", "--porcelain", 'env -u sh sh -c "codex"; true'], True),
                 ("cmd_tab", ["new", "--porcelain", 'bash -o errexit -c "claude"; true'], True),
                 ("cmd_new_tab", ["--", "bash", "-c", "-x", "codex"], False),
                 ("cmd_tab", ["new", "--porcelain", 'bash -c "-x" codex; true'], False))
        for function, argv, is_grid in cases:
            with self.subTest(argv=argv):
                code, _, panes = self.run_verb(function, argv)
                self.assertEqual(code, 0)
                self.assertEqual(panes.new_tab.call_count, 0 if is_grid else 1)

    def test_documented_shell_launch_uses_the_public_tab_route(self):
        import re
        import shlex
        text = " ".join((ROOT / "docs/session.md").read_text().split())
        example = re.search(r"`(kilix tab new [^`]+)`", text).group(1)
        words = shlex.split(example)
        code, _, panes = self.run_verb("cmd_tab", words[2:])
        self.assertEqual(code, 0)
        panes.new_tab.assert_not_called()


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

    def cli_json(self, *argv):
        import json
        from kilix_session import cli
        out = io.StringIO()
        with mock.patch.object(control, "Client", return_value=self.engine), contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        self.assertEqual(code, 0, out.getvalue())
        return json.loads(out.getvalue())

    def test_grid_list_name_and_add_into_infers_the_name(self):
        team = self.cli_json("grid", "add", "--name", "claudex-team", "--new", "--title", "lead",
                             "--cwd", self.tmp.name, "--", "codex")
        self.cli_json("grid", "add", "--title", "w", "--cwd", self.tmp.name, "--", "codex")
        self.assertEqual(len(self.cli_json("grid", "list")["grids"]), 2)
        listed = self.cli_json("grid", "list", "--name", "claudex-team")["grids"]
        self.assertEqual([g["name"] for g in listed], ["claudex-team"])
        joined = self.cli_json("grid", "add", "--into", str(team["pane"]["pane_id"]), "--title", "worker",
                               "--cwd", self.tmp.name, "--", "codex")
        self.assertEqual((joined["grid"], joined["tab_id"], joined["count"]), ("claudex-team", team["tab_id"], 2))
        self.assertEqual(self.cli_json("grid", "list", "--name", "nope")["grids"], [])

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



class DocumentedExampleTests(Isolated):
    """Every `kilix session grid` example in the docs and the bundled skill runs."""

    def examples(self, path, pattern):
        import re
        found = [m.group(1).split("#")[0].strip() for m in re.finditer(pattern, (ROOT / path).read_text(), re.M)]
        self.assertTrue(found, path)
        return found

    def run_examples(self, lines):
        import json
        import shlex
        from kilix_session import cli
        pane_id = None
        for line in lines:
            words = shlex.split(line.replace("[--name NAME]", "--name NAME"))
            self.assertEqual(words[:3], ["kilix", "session", "grid"], line)
            words = ["claudex-team" if w == "NAME" else "worker" if w == "T" else w for w in words[2:]]
            if "PANE" in words:
                self.assertIsNotNone(pane_id, f"{line}: no earlier example opened a pane")
                words[words.index("PANE")] = str(pane_id)
            if words[1] == "add" and "--cwd" not in words:
                words[2:2] = ["--cwd", self.tmp.name]
            out = io.StringIO()
            with mock.patch.object(control, "Client", return_value=self.engine), contextlib.redirect_stdout(out):
                code = cli.main(words)
            self.assertEqual(code, 0, f"{line}: {out.getvalue()}")
            if words[1] == "add":
                receipt = json.loads(out.getvalue())
                self.assertEqual(receipt["grid"], "claudex-team" if pane_id is None or "--into" in words
                                 else receipt["grid"], line)
                pane_id = pane_id or receipt["pane"]["pane_id"]
            if words[1] == "list" and "--name" in words:
                self.assertEqual({g["name"] for g in json.loads(out.getvalue())["grids"]}, {"claudex-team"}, line)

    def test_session_md_grid_examples_execute(self):
        self.run_examples(self.examples("docs/session.md", r"^(kilix session grid .*)$"))

    def test_bundled_skill_grid_examples_execute(self):
        self.run_examples(self.examples("skills/kilix-session-launch/SKILL.md", r"`(kilix session grid [^`]*)`"))

    def test_session_md_classification_examples_hold(self):
        text = " ".join((ROOT / "docs/session.md").read_text().split())
        # (quoted in the doc, argv, shell line, documented client)
        cases = [("`codex --yolo`", ["codex", "--yolo"], "", "codex"),
                 ("`/opt/bin/claude`", ["/opt/bin/claude"], "", "claude"),
                 ("`env TEST=1 codex`", ["env", "TEST=1", "codex"], "", "codex"),
                 ("`kilix tab new -- sh -c 'A=1 exec claude'`", ["sh", "-c", "A=1 exec claude"], "", "claude"),
                 ("`sh -c 'codex'`", ["sh", "-c", "codex"], "", "codex"),
                 ("`printf '%s\\n' codex`", [], "printf '%s\\n' codex", None),
                 ("`echo claude`", ["echo", "claude"], "", None),
                 ("`cd src && codex`", [], "cd src && codex", None),
                 ("`./run-agent.sh`", ["./run-agent.sh"], "", None)]
        for quoted, argv, line, name in cases:
            self.assertIn(quoted, text)
            self.assertEqual(session_grid.agent_program(argv, line), name, quoted)


def cross_home_worker(i, root, bases, state, mutex, barrier, results):
    # A separate process with its own stores, session home and runtime
    # variable, addressing the one engine socket every worker shares.
    os.environ.clear()
    os.environ.update(HOME=root, PATH="/usr/bin:/bin", LANG="C.UTF-8",
                      KILIX_STORAGE_HOME=f"{root}/store-{i}", KILIX_SESSION_HOME=f"{root}/session-{i}",
                      KILIX_AGENT_RUNS_HOME=f"{root}/runs-{i}", XDG_RUNTIME_DIR=f"{root}/xdg-{i}")
    session_grid.RUNTIME_BASE, session_grid.TMP_BASE = bases

    class SharedEngine(control.Client):
        caller = 1
        command = ["fake", "@", "--to", "fake:same-socket"]

        def snapshot(self):
            time.sleep(0.04)
            with mutex:
                fake = FakeEngine()
                fake.panes = state.panes
                return fake.snapshot()

        def run(self, args, payload=None):
            with mutex:
                fake = FakeEngine()
                fake.panes = state.panes
                out = fake.run(args, payload)
                state.panes = fake.panes
                return out

    barrier.wait()
    try:
        result = session_grid.place(SharedEngine(), source=control.describe(pane(1)), argv=["codex"],
                                    cwd=root, title=f"p{i}")
        results.put(("ok", result["tab_id"], result["count"]))
    except Exception as exc:  # reported to the parent, which fails the test
        results.put(("error", repr(exc)))


class CrossHomeProcessTests(unittest.TestCase):
    """The review's H1 case: thirteen processes, eleven panes already in the grid."""

    def test_thirteen_launchers_with_different_homes_fill_twelve_then_overflow(self):
        context = multiprocessing.get_context("fork")
        with tempfile.TemporaryDirectory() as folder, context.Manager() as manager:
            runtime = Path(folder) / "run" / str(os.getuid())
            runtime.mkdir(parents=True)
            runtime.chmod(0o700)
            bases = (str(Path(folder) / "run"), str(Path(folder) / "tmp"))
            marked = [pane(i, tab=2, layout="sessions") for i in range(2, 13)]
            for p in marked:
                p["user_vars"] = {session_grid.GROUP_VAR: session_grid.group(session_grid.DEFAULT_GRID),
                                  session_grid.NAME_VAR: session_grid.DEFAULT_GRID}
            state = manager.Namespace()
            state.panes = [pane(1), *marked]
            mutex, barrier, results = manager.Lock(), context.Barrier(13), context.Queue()
            children = [context.Process(target=cross_home_worker,
                                        args=(i, folder, bases, state, mutex, barrier, results))
                        for i in range(13)]
            for child in children:
                child.start()
            for child in children:
                child.join(60)
                if child.is_alive():
                    child.terminate()
                    child.join()
                self.assertEqual(child.exitcode, 0)
            output = [results.get(timeout=5) for _ in children]
            self.assertTrue(all(r[0] == "ok" for r in output), output)
            counts = {}
            for p in state.panes:
                if p["layout"] == "sessions":
                    counts[p["tab_id"]] = counts.get(p["tab_id"], 0) + 1
            self.assertEqual(sorted(counts.values()), [12, 12], counts)
            self.assertEqual(counts[2], 12)
            self.assertEqual(len(list((runtime / "kilix-session-grid").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
