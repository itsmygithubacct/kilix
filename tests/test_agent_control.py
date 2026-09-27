"""Behavioral boundaries for credentialed skill input and explicit launches."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import agent_control as control
import agent_programs
import agent_trust

SOURCE_BROKER = "a" * 16
TARGET_BROKER = "b" * 16


def pane(number, broker, tab=11):
    return {"id": number, "tab_id": tab, "tab_title": "workspace",
            "os_window_id": 1, "title": "duplicate title", "cwd": "/workspace",
            "layout": "splits", "env": {"KITTY_PTY_BROKER_SESSION": broker,
                                           "SECRET": "do not expose"},
            "foreground_processes": [{"pid": 123, "cmdline": ["/bin/codex", "private prompt"]}]}


class FakeClient(control.Client):
    def __init__(self, caller_pane=None):
        self.caller = caller_pane or 1
        self.panes = [pane(1, SOURCE_BROKER), pane(2, TARGET_BROKER)]
        self.calls = []
        self.help_text = "--next-to --source-window --keep-focus --bias vsplit hsplit vsplit-before hsplit-before"
        self.after_input = None

    def snapshot(self):
        return copy.deepcopy(self.panes)

    def run(self, args, payload=None):
        self.calls.append((args, payload))
        if args == ["launch", "--help"]:
            return self.help_text
        if args[0] == "launch":
            tab = 22 if "--type" in args else 11
            self.panes.append(pane(3, "c" * 16, tab))
            return "3\n"
        if args[0] == "send-text" and self.after_input:
            self.after_input()
        if args[0] == "get-text":
            return "line1\nline2\nline3\n"
        return ""


class InputTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()

    def invoke(self, argv):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(control, "Client", return_value=self.client), \
                mock.patch.object(control.time, "sleep"), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = control.main(argv)
        return result, output.getvalue(), errors.getvalue()

    def test_exact_broker_input_and_separate_enter(self):
        result, output, _ = self.invoke(["send", "2", "--expect-broker", TARGET_BROKER,
                                       "--text", "hello 世界", "--submit"])
        self.assertEqual(result, 0)
        calls = self.client.calls
        self.assertEqual([p for _, p in calls], ["hello 世界".encode(), b"\r"])
        for args, _ in calls:
            self.assertEqual(args, ["send-text", "--match",
                                   f"env:KITTY_PTY_BROKER_SESSION={TARGET_BROKER}",
                                   "--stdin", "--bracketed-paste=disable"])
        self.assertFalse(json.loads(output)["delivery_verified"])
        self.assertFalse(json.loads(output)["completion_verified"])

    def test_revalidate_between_text_and_submission(self):
        self.client.after_input = lambda: self.client.panes[1]["env"].update(
            KITTY_PTY_BROKER_SESSION="d" * 16)
        result, _, error = self.invoke(["send", "2", "--expect-broker", TARGET_BROKER,
                                       "--text", "hello", "--submit"])
        self.assertEqual(result, 1)
        self.assertIn("identity changed", error)
        self.assertEqual(len(self.client.calls), 1)

    def test_input_size_counts_utf8_bytes(self):
        self.assertEqual(control.plain_text("é" * 512, "input"), "é" * 512)
        for value in ("é" * 513, "", "hello\nworld", "hello\rworld", "\x1b[A", "a\x7f", "a\x85"):
            with self.subTest(value=value[:30]):
                result, _, _ = self.invoke(["send", "2", "--expect-broker", TARGET_BROKER,
                                            "--text", value])
                self.assertEqual(result, 1)
                self.assertEqual(self.client.calls, [])

    def test_send_refuses_client_commands_without_explicit_override(self):
        for value in ("/model", "  /logout now", "!pwd", "   ! git status", "# remember x"):
            with self.subTest(value=value):
                result, _, error = self.invoke(
                    ["send", "2", "--expect-broker", TARGET_BROKER, "--text", value])
                self.assertEqual(result, 1)
                self.assertIn("client command", error)
                self.assertEqual(self.client.calls, [])
        result, _, _ = self.invoke(["send", "2", "--expect-broker", TARGET_BROKER,
                                    "--text", " /model", "--allow-command", "--submit"])
        self.assertEqual(result, 0)
        self.assertEqual([payload for _, payload in self.client.calls], [b" /model", b"\r"])

    def test_stale_ambiguous_self_and_unknown_targets_refuse_without_input(self):
        for target, broker in ((2, "e" * 16), (9, TARGET_BROKER), (1, SOURCE_BROKER),
                               (2, "id:2"), (2, TARGET_BROKER + " or id:1")):
            with self.subTest(target=target, broker=broker):
                with self.assertRaises(control.ControlError):
                    self.client.input(target, broker, b"x")
        self.client.panes.append(pane(4, TARGET_BROKER))
        with self.assertRaises(control.ControlError):
            self.client.input(2, TARGET_BROKER, b"x")
        self.assertEqual(self.client.calls, [])

    def test_unknown_caller_refuses_input(self):
        self.client.caller = 0
        with self.assertRaises(control.ControlError):
            self.client.input(2, TARGET_BROKER, b"x")
        self.assertEqual(self.client.calls, [])

    def test_named_key_is_not_a_shell_command(self):
        result, _, _ = self.invoke(["key", "2", "--expect-broker", TARGET_BROKER, "down"])
        self.assertEqual(result, 0)
        self.assertEqual(self.client.calls[0][1], b"\x1b[B")

    def test_dump_is_read_only_and_line_bounded(self):
        result, output, _ = self.invoke(["dump", "2", "--lines", "2"])
        self.assertEqual((result, output), (0, "line2\nline3\n"))
        self.assertEqual(self.client.calls, [(["get-text", "--match", "id:2", "--extent", "screen"], None)])

    def test_snapshot_does_not_disclose_environment_or_prompt_arguments(self):
        self.client.panes[1]["foreground_processes"] = [
            {"cmdline": ["/bin/browser --token=private-value", "other secret"]}]
        result, output, _ = self.invoke(["list"])
        self.assertEqual(result, 0)
        for secret in ("SECRET", "do not expose", "private prompt", "private-value", "other secret"):
            self.assertNotIn(secret, output)
        self.assertEqual(json.loads(output)["panes"][1]["foreground_processes"][0]["program"], "browser")

    def test_file_input_and_fifo_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prompt"
            path.write_text("literal 'quote' $value", encoding="utf-8")
            result, _, _ = self.invoke(["send", "2", "--expect-broker", TARGET_BROKER,
                                       "--file", str(path)])
            self.assertEqual(result, 0)
            self.assertEqual(self.client.calls[0][1], b"literal 'quote' $value")
            fifo = Path(directory) / "fifo"
            os.mkfifo(fifo)
            result, _, error = self.invoke(["send", "2", "--expect-broker", TARGET_BROKER,
                                           "--file", str(fifo)])
            self.assertEqual(result, 1)
            self.assertIn("regular file", error)
            self.assertEqual(len(self.client.calls), 1)


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.directory = tempfile.TemporaryDirectory(prefix="kilix skill 'quoted' ")
        self.addCleanup(self.directory.cleanup)
        self.program = mock.patch.object(agent_programs, "resolve_agent_command", return_value="/opt/bin/codex")
        self.program.start()
        self.addCleanup(self.program.stop)

    def args(self, action="new-tab", *extra):
        return control.parser().parse_args([action, "1", "--expect-broker", SOURCE_BROKER,
                                           "--agent", "codex", "--title", "coding 'literal'",
                                           "--cwd", self.directory.name, *extra])

    def test_new_tab_is_anchored_argv_and_startup_remains_unverified(self):
        result = control.launch(self.client, self.args("new-tab", "--model", "model-name"))
        argv = self.client.calls[-1][0]
        self.assertEqual(argv[argv.index("--match") + 1], "window_id:1")
        self.assertEqual(argv[argv.index("--next-to") + 1], "id:1")
        self.assertEqual(argv[argv.index("--cwd") + 1], self.directory.name)
        self.assertEqual(argv[argv.index("--") + 1:], ["/opt/bin/codex", "--model", "model-name"])
        self.assertIn("--keep-focus", argv)
        self.assertNotIn("--allow-remote-control", argv)
        self.assertEqual(result["pane"]["tab_id"], 22)
        self.assertFalse(result["agent_startup_verified"])
        self.assertFalse(result["prompt_submitted"])

    def test_each_split_direction_and_bias(self):
        for direction, location in control.LOCATIONS.items():
            client = FakeClient()
            args = self.args("split", "--direction", direction, "--bias", "40")
            result = control.launch(client, args)
            argv = client.calls[-1][0]
            self.assertEqual(argv[argv.index("--location") + 1], location)
            self.assertEqual(argv[argv.index("--bias") + 1], "40.0")
            self.assertEqual(result["pane"]["tab_id"], 11)

    def test_dry_run_performs_no_launch(self):
        result = control.launch(self.client, self.args("new-tab", "--dry-run"))
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(self.client.calls, [(["launch", "--help"], None)])
        self.assertEqual(len(self.client.panes), 2)

    def test_missing_client_bad_directory_and_unsupported_geometry_do_not_launch(self):
        with mock.patch.object(agent_programs, "resolve_agent_command", return_value=None):
            with self.assertRaises(control.ControlError):
                control.launch(self.client, self.args())
        args = self.args()
        args.cwd = "relative"
        with self.assertRaises(control.ControlError):
            control.launch(self.client, args)
        self.client.panes[0]["layout"] = "grid"
        with self.assertRaises(control.ControlError):
            control.launch(self.client, self.args("split"))
        self.assertEqual(self.client.calls, [])

    def test_no_mutation_for_invalid_bias_or_missing_engine_feature(self):
        for bias in ("0", "100", "nan", "inf"):
            with self.assertRaises(control.ControlError):
                control.launch(self.client, self.args("split", "--bias", bias))
        self.client.help_text = "older engine"
        with self.assertRaises(control.ControlError):
            control.launch(self.client, self.args())
        self.assertEqual(self.client.calls, [(["launch", "--help"], None)])

    def test_changed_anchor_geometry_refuses_launch(self):
        original = self.client.resolve
        calls = 0
        def resolve(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.client.panes[0]["tab_id"] = 33
            return original(*args, **kwargs)
        self.client.resolve = resolve
        with self.assertRaisesRegex(control.ControlError, "geometry changed"):
            control.launch(self.client, self.args())
        self.assertEqual(self.client.calls, [(["launch", "--help"], None)])


class AgentArgvTests(unittest.TestCase):
    """grok and qwen-omp, a launch prompt, resume, coding-yolo (2026-09-27)."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="kilix-agents-")
        self.addCleanup(self.directory.cleanup)

    def argv(self, agent, *extra, yolo_setting=False):
        client = FakeClient()
        args = control.parser().parse_args(["new-tab", "1", "--expect-broker", SOURCE_BROKER,
                                            "--agent", agent, "--title", "t",
                                            "--cwd", self.directory.name, *extra])
        exe = {"qwen-omp": "/bin/omp"}.get(agent, f"/bin/{agent}")
        with mock.patch.object(agent_programs, "resolve_agent_command",
                               side_effect=lambda name: f"/bin/{name}"), \
                mock.patch("kilix_sdk.settings.coding_yolo", return_value=yolo_setting), \
                mock.patch.object(control, "client_subcommands",
                                  return_value=frozenset({"update", "logout", "mcp", "a"})):
            control.launch(client, args)
        argv = client.calls[-1][0]
        return argv[argv.index("--") + 1:], exe

    def test_each_client_resumes_and_takes_a_prompt_its_own_way(self):
        cases = {"claude": ["/bin/claude", "--resume", "abc-123", "review the diff"],
                 "codex": ["/bin/codex", "resume", "abc-123", "review the diff"],  # options would sit before the id
                 "grok": ["/bin/grok", "--resume", "abc-123", "review the diff"],
                 "qwen-omp": ["/bin/omp", "--model", "qwen3.8-max", "--approval-mode=always-ask",
                              "--tools=" + ",".join(control.OMP_NON_YOLO_TOOLS),
                              "--resume=abc-123", "review the diff"]}
        for agent, want in cases.items():
            argv, _ = self.argv(agent, "--resume", "abc-123", "--prompt", "review the diff")
            self.assertEqual(argv, want, agent)

    def test_codex_options_come_between_resume_and_its_session_id(self):
        self.assertEqual(self.argv("codex", "--resume", "abc-123", "--model", "m",
                                   "--prompt", "go on")[0],
                         ["/bin/codex", "resume", "--model", "m", "abc-123", "go on"])

    def test_qwen_omp_runs_omp_with_qwen_unless_a_model_is_given(self):
        self.assertEqual(self.argv("qwen-omp")[0], ["/bin/omp", "--model", "qwen3.8-max",
                                                    "--approval-mode=always-ask",
                                                    "--tools=" + ",".join(control.OMP_NON_YOLO_TOOLS)])
        self.assertEqual(self.argv("qwen-omp", "--model", "qwen3.7-max")[0],
                         ["/bin/omp", "--model", "qwen3.7-max", "--approval-mode=always-ask",
                          "--tools=" + ",".join(control.OMP_NON_YOLO_TOOLS)])

    def test_coding_yolo_follows_the_setting_and_only_when_asked(self):
        for agent, flag in (("claude", "--dangerously-skip-permissions"),
                            ("codex", "--dangerously-bypass-approvals-and-sandbox"),
                            ("grok", "--always-approve"), ("qwen-omp", "--auto-approve")):
            self.assertIn(flag, self.argv(agent, "--coding-yolo", yolo_setting=True)[0], agent)
            self.assertNotIn(flag, self.argv(agent, "--coding-yolo", yolo_setting=False)[0], agent)
            self.assertNotIn(flag, self.argv(agent, yolo_setting=True)[0], agent)

    def test_bad_prompts_resumes_and_kimi_refuse_without_launching(self):
        for agent, extra in (("claude", ["--prompt=--dangerously-skip-permissions"]),
                             ("claude", ["--prompt", "two\nlines"]),
                             ("claude", ["--resume", "../../etc"]),
                             ("claude", ["--resume", "a b"]),
                             ("kimi", ["--prompt", "hello"]),
                             ("qwen-omp", ["--prompt", "stats"]),
                             ("claude", ["--prompt", "rc"]),
                             ("codex", ["--prompt", "cloud-tasks"]),
                             ("grok", ["--prompt", "share"]),
                             ("claude", ["--prompt", "   "]),
                             ("claude", ["--prompt", "update now please"]),              # KX-R13-01
                             ("codex", ["--prompt", "Update the readme"]),
                             ("qwen-omp", ["--prompt", "read @secrets.txt please"]),    # KX-R13-09
                             ("claude", ["--model=--dangerously-skip-permissions"]),    # KX-R13-10
                             ("claude", ["--agent-arg=--dangerously-skip-permissions"]),  # KX-R13-03
                             ("claude", ["--agent-arg=--permission-mode=bypassPermissions"]),
                             ("codex", ["--agent-arg=-c", "--agent-arg=approval_policy=never"]),
                             ("codex", ["--agent-arg=-a", "--agent-arg=never"]),
                             ("codex", ["--agent-arg=-anever"]),
                             ("codex", ["--agent-arg=-p", "--agent-arg=PROFILE"]),
                             ("claude", ["--agent-arg=--allowedTools", "--agent-arg=Bash"]),
                             ("claude", ["--agent-arg=--settings", "--agent-arg=FILE"]),
                             ("grok", ["--agent-arg=--allow", "--agent-arg=Bash(*)"]),
                             ("grok", ["--agent-arg=--agent", "--agent-arg=FILE"]),
                             ("qwen-omp", ["--agent-arg=--config=FILE"]),
                             ("qwen-omp", ["--agent-arg=--profile"]),
                             ("grok", ["--agent-arg=--trust"])):
            client = FakeClient()
            args = control.parser().parse_args(["new-tab", "1", "--expect-broker", SOURCE_BROKER,
                                                "--agent", agent, "--title", "t",
                                                "--cwd", self.directory.name, *extra])
            with mock.patch.object(agent_programs, "resolve_agent_command", return_value="/bin/x"), \
                    mock.patch.object(control, "client_subcommands",
                                      return_value=frozenset({"update"})):
                with self.assertRaises(control.ControlError, msg=extra):
                    control.launch(client, args)
            self.assertFalse(any(c[0][0] == "launch" and c[0] != ["launch", "--help"]
                                 for c in client.calls), extra)

    def test_every_client_refuses_command_like_launch_prompts(self):
        for agent in control.AGENTS:
            for prefix in "/!#@-":
                client = FakeClient()
                args = control.parser().parse_args([
                    "new-tab", "1", "--expect-broker", SOURCE_BROKER,
                    "--agent", agent, "--title", "t", "--cwd", self.directory.name,
                    "--prompt", f"  {prefix}command with arguments"])
                with mock.patch.object(agent_programs, "resolve_agent_command",
                                       return_value="/bin/x"):
                    with self.assertRaises(control.ControlError, msg=(agent, prefix)):
                        control.launch(client, args)
                self.assertFalse(any(call[0][0] == "launch" for call in client.calls),
                                 (agent, prefix))

    def test_empty_prompt_is_refused_before_launch(self):
        client = FakeClient()
        args = control.parser().parse_args([
            "new-tab", "1", "--expect-broker", SOURCE_BROKER, "--agent", "claude",
            "--title", "t", "--cwd", self.directory.name, "--prompt="])
        with mock.patch.object(agent_programs, "resolve_agent_command", return_value="/bin/x"):
            with self.assertRaises(control.ControlError):
                control.launch(client, args)
        self.assertFalse(any(call[0][0] == "launch" for call in client.calls))

    def test_omp_asks_unless_the_yolo_setting_applies(self):                     # KX-R13-02
        safe = self.argv("qwen-omp")[0]
        self.assertIn("--approval-mode=always-ask", safe)
        tools = next(arg for arg in safe if arg.startswith("--tools="))
        self.assertNotIn("task", tools.split("=", 1)[1].split(","))
        yolo = self.argv("qwen-omp", "--coding-yolo", yolo_setting=True)[0]
        self.assertIn("--auto-approve", yolo)
        self.assertNotIn("--approval-mode=always-ask", yolo)
        self.assertFalse(any(arg.startswith("--tools=") for arg in yolo))

    def test_omp_non_yolo_tools_match_the_source_fixture(self):                  # KX-R13-32
        fixture = Path(__file__).with_name("fixtures") / "omp-18.3.2-default-tools.txt"
        expected = tuple(line for line in fixture.read_text().splitlines()
                         if line and not line.startswith("#"))
        self.assertEqual(control.OMP_NON_YOLO_TOOLS, expected)

    def test_only_each_clients_documented_extra_arguments_are_allowed(self):
        cases = (("grok", "--effort=high"), ("qwen-omp", "--thinking=high"))
        for agent, item in cases:
            self.assertIn(item, self.argv(agent, f"--agent-arg={item}")[0])

    def test_directory_widening_and_unsupported_codex_effort_are_refused(self):
        for agent, item in (("claude", "--add-dir=/tmp/also"),
                            ("claude", "--add-dir"),
                            ("codex", "--add-dir=/tmp/also"),
                            ("codex", "--effort=high"),
                            ("kimi", "--yolo")):
            with self.subTest(agent=agent, item=item):
                with self.assertRaises(control.ControlError):
                    self.argv(agent, f"--agent-arg={item}")

    def test_grok_trusts_its_own_folder_when_asked(self):                         # KX-R13-11
        self.assertIn("--trust", self.argv("grok", "--trust-folder")[0])
        self.assertNotIn("--trust", self.argv("grok")[0])

    def test_grok_trust_requires_the_repository_root(self):                      # KX-R13-31
        root = Path(self.directory.name) / "repo"
        nested = root / "nested"
        nested.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        base = ["new-tab", "1", "--expect-broker", SOURCE_BROKER, "--agent", "grok",
                "--title", "t", "--trust-folder", "--dry-run"]
        with mock.patch.object(agent_programs, "resolve_agent_command", return_value="/bin/grok"):
            accepted = control.launch(FakeClient(), control.parser().parse_args(
                [*base, "--cwd", str(root)]))
            self.assertIn("--trust", accepted["launch_argv"])
            with self.assertRaisesRegex(control.ControlError, "repository root"):
                control.launch(FakeClient(), control.parser().parse_args(
                    [*base, "--cwd", str(nested)]))
            unasked = control.launch(FakeClient(), control.parser().parse_args(
                [item for item in base if item != "--trust-folder"] +
                ["--cwd", str(nested)]))
            self.assertNotIn("--trust", unasked["launch_argv"])

    def test_grok_trust_ignores_git_env_and_refuses_inside_jj(self):             # KX-R13-39
        root = Path(self.directory.name) / "repo"
        nested = root / "sub"
        nested.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(root / ".git"),
                                          "GIT_WORK_TREE": str(nested)}):
            with self.assertRaisesRegex(control.ControlError, "repository root"):
                control.grok_trust_is_exact(nested)
        workspace = Path(self.directory.name) / "jjws"
        (workspace / ".jj").mkdir(parents=True)
        (workspace / "inner").mkdir()
        with self.assertRaisesRegex(control.ControlError, "jj workspace"):
            control.grok_trust_is_exact(workspace / "inner")
        control.grok_trust_is_exact(workspace)          # its root is fine

    def test_omp_tools_are_only_ones_on_by_default(self):                        # KX-R13-38
        for off in ("ast_grep", "find", "task"):
            self.assertNotIn(off, control.OMP_NON_YOLO_TOOLS)

    def test_client_subcommands_are_read_from_help(self):                         # KX-R13-01
        help_text = ("Usage: x\n\nCommands:\n  exec    Run [aliases: e]\n  plugin|plugins  Manage\n"
                     "  logout  Sign out\n\nOptions:\n  -h  help\n")
        with mock.patch.object(control.subprocess, "run",
                               return_value=mock.Mock(stdout=help_text, stderr="")):
            control._SUBCOMMANDS.clear()
            self.assertEqual(control.client_subcommands("/bin/fake"),
                             {"exec", "e", "plugin", "plugins", "logout"})
        with mock.patch.object(control.subprocess, "run",
                               return_value=mock.Mock(stdout="no commands here", stderr="")):
            control._SUBCOMMANDS.clear()
            with self.assertRaises(control.ControlError):
                control.client_subcommands("/bin/other")
        control._SUBCOMMANDS.clear()

    def test_grok_and_omp_are_recognised_in_a_snapshot(self):
        for program, agent in (("/home/u/.local/bin/grok", "grok"), ("/bin/omp", "qwen-omp")):
            p = pane(5, "d" * 16)
            p["foreground_processes"] = [{"pid": 9, "cmdline": [program]}]
            p.update(tab_id=11, tab_title="t", layout="splits", os_window_id=1)
            self.assertEqual(control.describe(p)["agent"], agent)


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="kilix-home-"))
        self.addCleanup(__import__("shutil").rmtree, self.home)
        self.project = self.home / "work" / "repo"
        self.project.mkdir(parents=True)

    def test_each_client_records_exactly_that_directory(self):
        (self.home / ".claude.json").write_text(json.dumps(
            {"projects": {"/elsewhere": {"hasTrustDialogAccepted": False}}, "other": 1}))
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text('model = "m"\n')
        d = str(self.project)
        for agent in ("claude", "codex"):
            self.assertEqual(agent_trust.trust(agent, d, self.home), "trusted", agent)
            self.assertEqual(agent_trust.trust(agent, d, self.home), "already trusted", agent)
        self.assertEqual(agent_trust.trust("grok", d, self.home), "trusted by grok --trust")
        self.assertFalse((self.home / ".grok").exists())
        claude = json.loads((self.home / ".claude.json").read_text())
        self.assertTrue(claude["projects"][d]["hasTrustDialogAccepted"])
        self.assertFalse(claude["projects"]["/elsewhere"]["hasTrustDialogAccepted"])
        self.assertEqual(claude["other"], 1)
        import tomllib
        codex = tomllib.loads((self.home / ".codex" / "config.toml").read_text())
        self.assertEqual(codex["projects"][d]["trust_level"], "trusted")
        self.assertEqual(codex["model"], "m")
        self.assertEqual(agent_trust.trust("qwen-omp", d, self.home), "no trust prompt")

    def test_a_decision_already_made_is_left_alone(self):
        (self.home / ".codex").mkdir()
        d = str(self.project)
        (self.home / ".codex" / "config.toml").write_text(
            f'[projects."{d}"]\ntrust_level = "untrusted"\n')
        with self.assertRaises(agent_trust.TrustError):
            agent_trust.trust("codex", d, self.home)
        self.assertIn("untrusted", (self.home / ".codex" / "config.toml").read_text())

    def test_only_an_owned_real_directory_below_home(self):
        link = self.home / "link"
        link.symlink_to(self.project)
        quoted = self.home / 'we"ird'
        quoted.mkdir()
        for bad in ("relative/dir", str(self.home / "missing"), str(link), str(quoted),
                    str(self.home), "/"):
            with self.assertRaises(agent_trust.TrustError, msg=bad):
                agent_trust.trust("claude", bad, self.home)
        self.assertFalse((self.home / ".claude.json").exists())

    def test_files_that_cannot_take_the_entry_are_left_alone(self):              # KX-R13-04, -06, -07
        d = str(self.project)
        codex = self.home / ".codex" / "config.toml"
        codex.parent.mkdir()
        for text in ('projects = { "/elsewhere" = { trust_level = "trusted" } }\n', "not = [toml\n"):
            codex.write_text(text)
            with self.assertRaises(agent_trust.TrustError):
                agent_trust.trust("codex", d, self.home)
            self.assertEqual(codex.read_text(), text)
        (self.home / ".claude.json").write_text("{not json")
        with self.assertRaises(agent_trust.TrustError):
            agent_trust.trust("claude", d, self.home)
        real = self.home / "real.json"
        real.write_text("{}")
        (self.home / ".claude.json").unlink()
        (self.home / ".claude.json").symlink_to(real)
        with self.assertRaises(agent_trust.TrustError):
            agent_trust.trust("claude", d, self.home)
        self.assertTrue((self.home / ".claude.json").is_symlink())

    def test_claudes_own_lock_is_honoured_and_the_mode_kept(self):                # KX-R13-05
        d = str(self.project)
        config = self.home / ".claude.json"
        config.write_text("{}")
        config.chmod(0o640)
        (self.home / ".claude.json.lock").mkdir()
        with mock.patch.object(agent_trust._ClaudeLock, "__init__",
                               lambda lock, path, wait=5.0: (setattr(lock, "lock", path.with_name(
                                   path.name + ".lock")), setattr(lock, "wait", 0.2))[0]):
            with self.assertRaisesRegex(agent_trust.TrustError, "held"):
                agent_trust.trust("claude", d, self.home)
        self.assertEqual(json.loads(config.read_text()), {})
        (self.home / ".claude.json.lock").rmdir()
        agent_trust.trust("claude", d, self.home)
        self.assertEqual(oct(config.stat().st_mode & 0o777), "0o640")
        self.assertFalse((self.home / ".claude.json.lock").exists())

    def test_claude_waits_for_a_live_lock_then_succeeds(self):
        lock = self.home / ".claude.json.lock"
        lock.mkdir()
        releaser = threading.Thread(target=lambda: (time.sleep(0.25), lock.rmdir()))
        releaser.start()
        try:
            self.assertEqual(agent_trust.trust("claude", str(self.project), self.home), "trusted")
        finally:
            releaser.join()

    def test_replace_itself_refuses_a_symlink(self):
        real = self.home / "real"
        real.write_text("old")
        link = self.home / "link"
        link.symlink_to(real)
        with self.assertRaises(agent_trust.TrustError):
            agent_trust._replace(link, "new")
        self.assertEqual(real.read_text(), "old")

    def test_codex_home_is_used_instead_of_the_default(self):
        alternate = self.home / "alternate-codex"
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(alternate)}):
            agent_trust.trust("codex", str(self.project), self.home)
        self.assertTrue((alternate / "config.toml").is_file())
        self.assertFalse((self.home / ".codex" / "config.toml").exists())

    def test_non_utf8_directory_name_is_a_trust_error(self):
        raw = os.path.join(os.fsencode(self.home), b"bad-\xff")
        os.mkdir(raw)
        with self.assertRaisesRegex(agent_trust.TrustError, "UTF-8"):
            agent_trust.trust("codex", os.fsdecode(raw), self.home)

    def test_the_resolved_path_is_recorded_and_config_homes_honoured(self):       # KX-R13-08
        odd = str(self.project) + "/./"
        other = self.home / "claude-config"
        other.mkdir()
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(other)}):
            agent_trust.trust("claude", odd, self.home)
        data = json.loads((other / ".claude.json").read_text())
        self.assertEqual(list(data["projects"]), [str(self.project.resolve())])
        self.assertFalse((self.home / ".claude.json").exists())

    def test_ownership_and_root_are_checked(self):                               # KX-R13-20
        with mock.patch.object(agent_trust.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(agent_trust.TrustError, "own"):
                agent_trust.trust("claude", str(self.project), self.home)
        with self.assertRaisesRegex(agent_trust.TrustError, "never trusted"):
            agent_trust.trust("claude", "/", Path("/nonexistent-home"))

    def test_a_launch_trusts_only_when_asked_and_never_on_a_dry_run(self):
        client = FakeClient()
        base = ["new-tab", "1", "--expect-broker", SOURCE_BROKER, "--agent", "claude",
                "--title", "t", "--cwd", str(self.project)]
        with mock.patch.object(agent_programs, "resolve_agent_command", return_value="/bin/claude"), \
                mock.patch.object(agent_trust, "trust", return_value="trusted") as trust:
            control.launch(client, control.parser().parse_args(base))
            control.launch(FakeClient(), control.parser().parse_args(base + ["--trust-folder", "--dry-run"]))
            self.assertEqual(trust.call_count, 0)
            result = control.launch(FakeClient(), control.parser().parse_args(base + ["--trust-folder"]))
        trust.assert_called_once_with("claude", str(self.project))
        self.assertEqual(result["folder_trust"], "trusted")


class ConnectionTests(unittest.TestCase):
    def test_process_output_is_bounded_before_collecting_it_all(self):
        client = control.Client.__new__(control.Client)
        client.command = [sys.executable, "-c"]
        self.assertEqual(client.run(["print('ready')"]), "ready\n")
        with self.assertRaisesRegex(control.ControlError, "exceeded 1 MiB"):
            client.run(["import sys; sys.stdout.write('x' * (2 * 1024 * 1024))"])

    def test_credential_rejects_symlink_hardlink_or_wrong_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            password = root / "password"
            password.write_text("private")
            values = {"KITTY_LISTEN_ON": "unix:@kilix-123", "KITTY_WINDOW_ID": "1",
                      "KILIX_RC_PASSWORD_FILE": str(password)}
            with mock.patch.object(control, "connection_values", return_value=values), \
                    mock.patch.object(control, "kitten_path", return_value="/kitten"):
                password.chmod(0o600)
                client = control.Client()
                self.assertEqual(client.command, ["/kitten", "@", "--to", "unix:@kilix-123",
                                                   "--password-file", str(password)])
                with self.assertRaises(control.ControlError):
                    control.Client(caller_pane=2)
                password.chmod(0o644)
                with self.assertRaises(control.ControlError):
                    control.Client()
                password.chmod(0o600)
                link = root / "link"
                os.link(password, link)
                with self.assertRaises(control.ControlError):
                    control.Client()
                link.unlink()
                link.symlink_to(password)
                values["KILIX_RC_PASSWORD_FILE"] = str(link)
                with self.assertRaises(control.ControlError):
                    control.Client()


if __name__ == "__main__":
    unittest.main()
