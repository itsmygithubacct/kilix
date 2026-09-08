"""Behavioral boundaries for credentialed skill input and explicit launches."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import agent_control as control
import agent_programs

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
        result = control.launch(self.client, self.args("new-tab", "--model", "model-name",
                                                       "--agent-arg=--config", "--agent-arg=key=value"))
        argv = self.client.calls[-1][0]
        self.assertEqual(argv[argv.index("--match") + 1], "window_id:1")
        self.assertEqual(argv[argv.index("--next-to") + 1], "id:1")
        self.assertEqual(argv[argv.index("--cwd") + 1], self.directory.name)
        self.assertEqual(argv[argv.index("--") + 1:], ["/opt/bin/codex", "--model", "model-name", "--config", "key=value"])
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
