"""Stale socket/key/pane recovery, using fake procfs; no live terminal writes."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
sys.path.insert(0, str(ROOT / "tests"))
import terminal_connection as connection
import agent_control
from _env_support import sandbox_env


class ProcFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="kilix-connection-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.token = "a" * 16
        self.old = self.values(100, 2)
        self.fresh = self.values(200, 9)
        self.fresh["KILIX_KITTEN"] = "/old/client"  # authoritative engine sibling wins

    def values(self, pid, pane, token=None):
        return {"KITTY_LISTEN_ON": f"unix:@kilix-{pid}", "KITTY_PID": str(pid),
                "KITTY_WINDOW_ID": str(pane), "KITTY_PUBLIC_KEY": f"1:key-{pid}",
                "KITTY_PTY_BROKER_SESSION": token or self.token,
                "KITTY_PTY_BROKER_RUNTIME": str(self.root / "runtime"),
                "KILIX_RC_PASSWORD_FILE": str(self.root / "credential")}

    def process(self, pid, parent, exe, env=None, argv=None, start=123):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        fields = ["S", str(parent)] + ["0"] * 17 + [str(start)]
        (directory / "stat").write_text(f"{pid} (can have ) spaces) " + " ".join(fields))
        (directory / "comm").write_text(Path(exe).name[:15] + "\n")
        (directory / "exe").symlink_to(exe)
        (directory / "environ").write_bytes(b"\0".join(
            os.fsencode(f"{k}={v}") for k, v in (env or {}).items()) + b"\0")
        (directory / "cmdline").write_bytes(b"\0".join(
            os.fsencode(a) for a in (argv or [exe])) + b"\0")
        return directory

    def attach(self, values=None, pid=201, operation="attach", terminal=True):
        values = values or self.fresh
        parent = int(values["KITTY_PID"])
        if terminal and not (self.proc / str(parent)).exists():
            self.process(parent, 1, "/engine/kitty")
        suffix = (["attach", values["KITTY_PTY_BROKER_SESSION"]] if operation == "attach" else
                  ["run", "--id", values["KITTY_PTY_BROKER_SESSION"], "--", "/bin/bash"])
        return self.process(pid, parent, "/broker/kitty-pty-broker", values,
                            ["/broker/kitty-pty-broker", "--runtime-dir", values["KITTY_PTY_BROKER_RUNTIME"], *suffix])

    def resolve(self, environment=None):
        return connection.connection_values(self.old if environment is None else environment,
                                            proc_root=self.proc, parent=1)

    def test_complete_bundle_replaces_dead_socket_key_and_pane(self):
        self.attach()
        fresh = self.resolve()
        self.assertEqual(fresh["KITTY_LISTEN_ON"], "unix:@kilix-200")
        self.assertEqual(fresh["KITTY_PID"], "200")
        self.assertEqual(fresh["KITTY_WINDOW_ID"], "9")
        self.assertEqual(fresh["KITTY_PUBLIC_KEY"], "1:key-200")
        self.assertEqual(fresh["KILIX_KITTEN"], "/engine/kitten")
        self.assertEqual(self.old["KITTY_WINDOW_ID"], "2")

    def test_first_launch_run_frontend_also_resolves(self):
        self.attach(operation="run")
        self.assertEqual(self.resolve()["KITTY_WINDOW_ID"], "9")

    def test_a_live_old_terminal_does_not_override_exact_broker_attachment(self):
        self.process(100, 1, "/old/kitty")
        self.attach()
        self.assertEqual(self.resolve()["KITTY_PID"], "200")

    def test_unrelated_terminal_and_reused_pane_number_are_never_selected(self):
        self.attach(values=self.values(200, 2, "b" * 16))
        with self.assertRaisesRegex(connection.ConnectionError, "no live attachment"):
            self.resolve()

    def test_same_token_in_a_different_runtime_is_not_the_session(self):
        other = {**self.fresh, "KITTY_PTY_BROKER_RUNTIME": str(self.root / "other")}
        self.attach(values=other)
        with self.assertRaises(connection.ConnectionError):
            self.resolve()

    def test_multiple_attachments_are_ambiguous_even_with_same_pane_id(self):
        self.attach()
        self.attach(values=self.values(300, 9), pid=301)
        with self.assertRaisesRegex(connection.ConnectionError, "multiple live attachments"):
            self.resolve()

    def test_incomplete_or_mismatched_bundle_never_inherits_old_key(self):
        for key, value in [("KITTY_PUBLIC_KEY", ""), ("KITTY_PUBLIC_KEY", "2:unknown"),
                           ("KITTY_WINDOW_ID", "0"), ("KITTY_LISTEN_ON", "unix:@kilix-300"),
                           ("KILIX_RC_PASSWORD_FILE", "relative")]:
            with self.subTest(key=key):
                p = self.attach(values={**self.fresh, key: value})
                with self.assertRaises(connection.ConnectionError):
                    self.resolve()
                for f in p.iterdir(): f.unlink()
                p.rmdir()

    def test_imposter_executable_wrong_parent_and_wrong_argv_are_rejected(self):
        p = self.attach()
        original = (p / "exe").readlink()
        (p / "exe").unlink()
        (p / "exe").symlink_to("/usr/bin/python3")
        with self.assertRaises(connection.ConnectionError): self.resolve()
        (p / "exe").unlink()
        (p / "exe").symlink_to(original)
        stat = (p / "stat").read_text()
        (p / "stat").write_text(stat.replace("S 200 ", "S 300 "))
        with self.assertRaises(connection.ConnectionError): self.resolve()
        (p / "stat").write_text(stat)
        (p / "cmdline").write_bytes(b"kitty-pty-broker\0attach\0wrong\0")
        with self.assertRaises(connection.ConnectionError): self.resolve()

    def test_terminal_pid_reuse_and_exiting_frontends_are_rejected(self):
        p = self.attach()
        terminal_exe = self.proc / "200/exe"
        terminal_exe.unlink()
        terminal_exe.symlink_to("/usr/bin/bash")
        with self.assertRaises(connection.ConnectionError): self.resolve()
        terminal_exe.unlink()
        terminal_exe.symlink_to("/engine/kitty")
        original = connection._identity
        calls = 0
        def changed(proc, uid):
            nonlocal calls
            value = original(proc, uid)
            if proc == p:
                calls += 1
                if calls > 1: return value[0], value[1] + 1
            return value
        with mock.patch.object(connection, "_identity", side_effect=changed), self.assertRaises(connection.ConnectionError):
            self.resolve()

    def test_other_users_process_cannot_supply_connection(self):
        self.attach()
        with self.assertRaises(connection.ConnectionError):
            connection.connection_values(self.old, proc_root=self.proc, parent=1, uid=os.getuid() + 1)

    def test_missing_broker_identity_yields_actionable_stale_error(self):
        values = {k: v for k, v in self.old.items() if k != "KITTY_PTY_BROKER_SESSION"}
        with self.assertRaisesRegex(connection.ConnectionError, "without a PTY broker identity"):
            self.resolve(values)

    def test_custom_socket_is_not_retargeted(self):
        self.attach()
        values = {**self.old, "KITTY_LISTEN_ON": "unix:/custom/explicit.sock"}
        self.assertEqual(self.resolve(values), values)

    def test_ancestors_recover_stripped_values_including_public_key(self):
        self.process(400, 1, "/usr/bin/agent", self.old)
        got = connection.inherited_values({}, proc_root=self.proc, parent=400)
        self.assertEqual(got, self.old)

    def test_ancestors_cannot_mix_terminal_or_broker_bundles(self):
        self.process(400, 1, "/usr/bin/agent", self.fresh)
        values = {"KITTY_LISTEN_ON": self.old["KITTY_LISTEN_ON"]}
        self.assertEqual(connection.inherited_values(values, proc_root=self.proc, parent=400), values)
        values = {"KITTY_PTY_BROKER_SESSION": "b" * 16}
        self.assertEqual(connection.inherited_values(values, proc_root=self.proc, parent=400), values)

    def test_refresh_drops_stale_optional_values_and_preserves_unrelated_environment(self):
        self.attach()
        values = {**self.old, "KILIX_PREBUILT_HOME": "/stale", "PATH": "/bin", "OTHER": "kept"}
        got = connection.refreshed_environment(values, proc_root=self.proc, parent=1)
        self.assertNotIn("KILIX_PREBUILT_HOME", got)
        self.assertEqual((got["PATH"], got["OTHER"]), ("/bin", "kept"))

    def test_agent_client_uses_fresh_key_for_subprocess_and_fresh_self_identity(self):
        self.attach()
        credential = self.root / "credential"
        credential.write_text("fake-test-credential")
        credential.chmod(0o600)
        with mock.patch.object(agent_control, "connection_values", side_effect=self.resolve), \
                mock.patch.object(agent_control, "kitten_path", return_value=sys.executable):
            client = agent_control.Client()
        self.assertEqual(client.caller, 9)
        self.assertEqual(client.command_env["KITTY_PUBLIC_KEY"], "1:key-200")
        client.command = [sys.executable, "-c"]
        text = client.run(["import os; print(os.environ['KITTY_PUBLIC_KEY'], os.environ['KITTY_WINDOW_ID'])"])
        self.assertEqual(text.strip(), "1:key-200 9")
        with mock.patch.object(agent_control, "connection_values", side_effect=self.resolve), \
                self.assertRaisesRegex(agent_control.ControlError, "conflicts"):
            agent_control.Client(caller_pane=2)

    def test_no_mutating_subprocess_when_recovery_fails(self):
        with mock.patch.object(connection, "connection_values", side_effect=connection.ConnectionError("detached")), \
                mock.patch.object(agent_control.subprocess, "Popen") as process:
            with self.assertRaisesRegex(agent_control.ControlError, "detached"):
                agent_control.Client()
        process.assert_not_called()

    def test_diagnostics_do_not_expose_key_or_credential(self):
        output = io.StringIO()
        with mock.patch.object(connection, "refreshed_environment", return_value=self.fresh), \
                contextlib.redirect_stdout(output):
            self.assertEqual(connection.main([]), 0)
        self.assertNotIn("key-200", output.getvalue())
        self.assertNotIn("credential", output.getvalue())
        self.assertTrue(json.loads(output.getvalue())["public_key_available"])

    def test_exec_preserves_argv_and_does_not_retry(self):
        argv = ["/a launcher", "agent-control", "send", "9", "--text", "hello; $(not a command)"]
        with mock.patch.object(connection, "refreshed_environment", return_value=dict(self.fresh)), \
                mock.patch.object(connection.os, "execvpe", side_effect=OSError("unavailable")) as execute, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connection.main(["--exec", *argv]), 1)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[1], argv)
        self.assertNotIn("_KILIX_CONNECTION_CHECKED", execute.call_args.args[2])

    def test_internal_launcher_marker_is_set_only_for_launcher(self):
        with mock.patch.object(connection, "refreshed_environment", return_value=dict(self.fresh)), \
                mock.patch.object(connection.os, "execvpe", side_effect=OSError("unavailable")) as execute, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connection.main(["--launch", "/kilix", "ls"]), 1)
        self.assertEqual(execute.call_args.args[2]["_KILIX_CONNECTION_CHECKED"], "1")

    def test_no_identity_does_not_discover_an_arbitrary_terminal(self):
        self.attach()
        self.assertEqual(self.resolve({}), {})

    def test_launcher_rejects_detached_context_before_writable_setup(self):
        empty_home = self.root / "should-not-be-created"
        env = sandbox_env(**{**self.old, "GPU_TERMINAL_HOME": str(empty_home)})
        result = subprocess.run([str(ROOT / "kilix"), "agent-control", "list"],
                                env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no live attachment", result.stderr)
        self.assertFalse(empty_home.exists())

    def test_launcher_diagnostics_help_needs_no_live_terminal_or_setup(self):
        empty_home = self.root / "should-not-be-created"
        result = subprocess.run([str(ROOT / "kilix"), "connection", "--help"],
                                env=sandbox_env(GPU_TERMINAL_HOME=str(empty_home)),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--exec", result.stdout)
        self.assertFalse(empty_home.exists())

    def test_agent_help_remains_available_from_detached_session(self):
        result = subprocess.run([str(ROOT / "kilix"), "agent-control", "--help"],
                                env=sandbox_env(**self.old), capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
