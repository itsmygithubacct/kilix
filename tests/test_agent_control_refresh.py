"""agent-control rebinds a restored pane through the real frontend_context.

Everything runs against a fake /proc tree; frontend_context.refresh is not
mocked. Adapted from the independent review of de7c410
(research/gpu_terminal/0.2.2-rc6/review-agent-control-refresh).
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import agent_control as control  # noqa: E402
from kilix_sdk import frontend_context as context  # noqa: E402

SID = "0123456789abcdef"


class Fixture(unittest.TestCase):
    """A restored pane: original terminal 10 is gone, replacement terminal 20
    runs attach helper 40 for this pane's broker session."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.runtime = self.root / "broker-runtime"
        self.runtime.mkdir(mode=0o700)
        self.engine = self.root / "engine" / "generations" / "g2"
        self.engine.mkdir(parents=True)
        self.broker = self.root / "kitty-pty-broker"
        self.kitty = self.engine / "kitty"
        self.new_kitten = self.engine / "kitten"
        self.old_engine = self.root / "engine" / "generations" / "g1"
        self.old_engine.mkdir(parents=True)
        self.old_kitten = self.old_engine / "kitten"
        for path in (self.broker, self.kitty, self.new_kitten, self.old_kitten):
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o700)
        self.old_password = self.root / "old-rc-password"
        self.new_password = self.root / "new-rc-password"
        for path in (self.old_password, self.new_password):
            path.write_text("private")
            path.chmod(0o600)
        self.broker_identity = {
            "KITTY_PID": "10",
            "KITTY_PTY_BROKER_SESSION": SID,
            "KITTY_PTY_BROKER_RUNTIME": str(self.runtime),
            "KITTY_PTY_BROKER_EXECUTABLE": str(self.broker),
        }
        self.stale = {
            "KITTY_LISTEN_ON": "unix:@kilix-10", "KITTY_WINDOW_ID": "5",
            "KILIX_RC_PASSWORD_FILE": str(self.old_password), "KITTY_PUBLIC_KEY": "1:old-key",
        }
        self.fresh = {
            "KITTY_PID": "20", "KITTY_LISTEN_ON": "unix:@kilix-20", "KITTY_WINDOW_ID": "3",
            "KITTY_PUBLIC_KEY": "1:new-key", "KILIX_RC_PASSWORD_FILE": str(self.new_password),
            "KITTY_PTY_BROKER_SESSION": SID, "KITTY_PTY_BROKER_RUNTIME": str(self.runtime),
            "KITTY_PTY_BROKER_EXECUTABLE": str(self.broker),
        }
        self.process(20, self.kitty, ["kitty"], {}, parent=1)
        self.helper = self.process(40, self.broker,
                                   [str(self.broker), "--runtime-dir", str(self.runtime),
                                    "attach", SID], self.fresh, parent=20)
        real_readlink = os.readlink
        def readlink(path, *a, **k):          # never the live /proc
            text = os.fspath(path)
            if text.startswith("/proc/"):
                return real_readlink(self.proc / text[len("/proc/"):], *a, **k)
            return real_readlink(path, *a, **k)
        for patcher in (mock.patch.object(context, "_PROC", self.proc),
                        mock.patch.object(control, "Path",
                                          side_effect=lambda p: self.proc if p == "/proc" else Path(p)),
                        mock.patch.object(control.os, "readlink", side_effect=readlink)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def process(self, pid, executable, argv, environment, parent):
        path = self.proc / str(pid)
        path.mkdir()
        (path / "exe").symlink_to(executable)
        fields = ["S", str(parent)] + ["0"] * 17 + ["777"]
        (path / "stat").write_text(f"{pid} (fixture) " + " ".join(fields))
        (path / "status").write_text(f"Name:\tfixture\nPPid:\t{parent}\n")
        (path / "cmdline").write_bytes(b"\0".join(os.fsencode(a) for a in argv) + b"\0")
        (path / "environ").write_bytes(
            b"".join(f"{k}={v}\0".encode() for k, v in environment.items()))
        return path

    def values(self, env, ppid=100):
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(control.os, "getppid", return_value=ppid):
            return control.connection_values()

    def client(self, env, ppid=100, **kwargs):
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(control.os, "getppid", return_value=ppid):
            return control.Client(**kwargs)

    def route(self, values):
        return {k: values.get(k) for k in ("KITTY_LISTEN_ON", "KITTY_WINDOW_ID",
                                           "KITTY_PUBLIC_KEY", "KILIX_RC_PASSWORD_FILE")}

    def fresh_route(self):
        return self.route(self.fresh)


class ControlRealPath(Fixture):
    def test_env_bundle_is_refreshed_through_the_real_resolver(self):
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        values = self.values({**self.stale, **self.broker_identity})
        self.assertEqual(self.route(values), self.fresh_route())
        self.assertEqual(values["KITTY_PID"], "20")

    def test_stripped_env_recovers_route_and_broker_identity_from_own_shell(self):
        self.process(100, self.kitty, ["sh"], {**self.stale, **self.broker_identity}, parent=1)
        values = self.values({})
        self.assertEqual(self.route(values), self.fresh_route())

    def test_client_command_uses_refreshed_socket_password_key_and_caller(self):
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        env = {**self.stale, **self.broker_identity, "KILIX_KITTEN": str(self.new_kitten)}
        client = self.client(env)
        self.assertEqual(client.command, [str(self.new_kitten), "@", "--to", "unix:@kilix-20",
                                          "--password-file", str(self.new_password)])
        self.assertEqual(client.command_env["KITTY_PUBLIC_KEY"], "1:new-key")
        self.assertEqual(client.caller, 3)

    def test_refreshed_credential_still_passes_the_private_file_checks(self):
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        env = {**self.stale, **self.broker_identity, "KILIX_KITTEN": str(self.new_kitten)}
        for change in ("mode", "symlink", "hardlink"):
            with self.subTest(change=change):
                target = self.root / f"pw-{change}"
                if change == "mode":
                    target.write_text("p"); target.chmod(0o644)
                elif change == "symlink":
                    target.symlink_to(self.new_password)
                else:
                    os.link(self.new_password, target)
                self.fresh["KILIX_RC_PASSWORD_FILE"] = str(target)
                (self.helper / "environ").write_bytes(
                    b"".join(f"{k}={v}\0".encode() for k, v in self.fresh.items()))
                with self.assertRaises(control.ControlError):
                    self.client(env)
                if change == "hardlink":
                    target.unlink()

    def test_caller_pane_must_agree_with_the_refreshed_window(self):
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        env = {**self.stale, **self.broker_identity, "KILIX_KITTEN": str(self.new_kitten)}
        with self.assertRaises(control.ControlError):
            self.client(env, caller_pane=5)          # the stale window id
        self.assertEqual(self.client(env, caller_pane=3).caller, 3)


class ControlFastPathAndFailOpen(Fixture):
    def test_live_original_terminal_takes_the_fast_path_even_with_a_helper(self):
        self.process(10, self.kitty, ["kitty"], {}, parent=1)
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        with mock.patch.object(context, "_candidate", side_effect=AssertionError("scanned")):
            values = self.values({**self.stale, **self.broker_identity})
        self.assertEqual(self.route(values), self.route(self.stale))

    def test_live_original_found_via_ancestor_kitty_pid_takes_the_fast_path(self):
        # Own env stripped of everything; the shell supplies KITTY_PID too.
        self.process(10, self.kitty, ["kitty"], {}, parent=1)
        self.process(100, self.kitty, ["sh"], {**self.stale, **self.broker_identity}, parent=1)
        with mock.patch.object(context, "_candidate", side_effect=AssertionError("scanned")):
            values = self.values({})
        self.assertEqual(self.route(values), self.route(self.stale))

    def test_rebuilt_broker_binary_never_refuses(self):
        # Earlier F1: a helper whose broker binary was replaced reads "(deleted)".
        (self.helper / "exe").unlink()
        (self.helper / "exe").symlink_to(str(self.broker) + " (deleted)")
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        env = {**self.stale, **self.broker_identity, "KILIX_KITTEN": str(self.new_kitten)}
        client = self.client(env)
        self.assertEqual(client.command[3], "unix:@kilix-10")   # inherited, no refusal

    def test_live_original_with_replaced_kitty_binary_keeps_inherited_route(self):
        # Kernel-confirmed: a replaced binary reads "<path> (deleted)" and
        # resolve(strict=True) raises, so the original counts as not alive and
        # the scan runs. With the pane still attached there, no other attach
        # helper exists (the broker refuses a second attach): nothing changes.
        self.process(10, str(self.kitty) + " (deleted)", ["kitty"], {}, parent=1)
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        for name in os.listdir(self.helper):
            (self.helper / name).unlink()
        self.helper.rmdir()
        values = self.values({**self.stale, **self.broker_identity})
        self.assertEqual(self.route(values), self.route(self.stale))

    def test_two_helpers_leave_the_inherited_route(self):
        self.process(41, self.broker, [str(self.broker), "--runtime-dir", str(self.runtime),
                                       "attach", SID], self.fresh, parent=20)
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        values = self.values({**self.stale, **self.broker_identity})
        self.assertEqual(self.route(values), self.route(self.stale))

    def test_other_session_helper_is_not_used(self):
        self.fresh["KITTY_PTY_BROKER_SESSION"] = "fedcba9876543210"
        (self.helper / "environ").write_bytes(
            b"".join(f"{k}={v}\0".encode() for k, v in self.fresh.items()))
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        values = self.values({**self.stale, **self.broker_identity})
        self.assertEqual(self.route(values), self.route(self.stale))


class ControlWalkGuard(Fixture):
    def test_broker_identity_of_another_terminal_is_not_borrowed_when_walk_runs(self):
        # Unlike the author's test, the route is incomplete, so the walk really runs.
        other = {"KITTY_LISTEN_ON": "unix:@kilix-99", **self.broker_identity}
        self.process(100, self.kitty, ["sh"], {}, parent=101)
        self.process(101, self.kitty, ["sh"], other, parent=1)
        seen = []
        real = context.refresh
        with mock.patch.object(context, "refresh", side_effect=lambda v: seen.append(dict(v)) or real(v)):
            values = self.values({"KITTY_LISTEN_ON": "unix:@kilix-10"})
        self.assertNotIn("KITTY_PTY_BROKER_SESSION", seen[0])
        self.assertNotIn("KITTY_PID", seen[0])
        self.assertEqual(values.get("KITTY_LISTEN_ON"), "unix:@kilix-10")

    def test_own_env_wins_over_ancestor_for_broker_identity(self):
        ancestor = {**self.stale, **self.broker_identity,
                    "KITTY_PTY_BROKER_SESSION": "fedcba9876543210"}
        self.process(100, self.kitty, ["sh"], ancestor, parent=1)
        values = self.values({"KITTY_LISTEN_ON": "unix:@kilix-10",
                              "KITTY_PTY_BROKER_SESSION": SID})
        self.assertEqual(self.route(values), self.fresh_route())

    def test_walk_stops_at_another_users_ancestor(self):
        self.process(100, self.kitty, ["sh"], {**self.stale, **self.broker_identity}, parent=1)
        with mock.patch.object(control.os, "getuid", return_value=os.getuid() + 1):
            values = self.values({})
        self.assertNotIn("KITTY_PTY_BROKER_SESSION", values)


class FormerReviewFindings(Fixture):
    def test_broker_identity_is_recovered_when_the_env_route_is_complete(self):
        """F-A: the `needed` early exit skips the walk, so a caller whose env kept
        the route but not the broker identity is never refreshed, although its
        own shell carries that identity (README: 'recovering the broker identity
        with the route from its own process ancestors')."""
        self.process(100, self.kitty, ["sh"], {**self.stale, **self.broker_identity}, parent=1)
        values = self.values(dict(self.stale))
        self.assertEqual(self.route(values), self.fresh_route(),
                         "walk never ran: stale route kept")

    def test_a_refreshed_route_never_picks_the_kitten_of_the_process_it_names(self):
        """F-B: frontend_context accepts any `unix:` socket and pins the terminal
        only by exe basename. agent_control then derives the kitten from the
        process the refreshed socket names. The SDK never does this (its kitten
        is KILIX_KITTEN)."""
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "unrelated").write_text("x")
        (elsewhere / "kitten").write_text("#!/bin/sh\nexit 0\n")
        (elsewhere / "kitten").chmod(0o700)
        self.process(77, elsewhere / "unrelated", ["unrelated"], {}, parent=1)
        self.fresh["KITTY_LISTEN_ON"] = "unix:@kilix-77"      # not the helper's KITTY_PID
        (self.helper / "environ").write_bytes(
            b"".join(f"{k}={v}\0".encode() for k, v in self.fresh.items()))
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        real_readlink = os.readlink
        def readlink(path, *a, **k):
            text = os.fspath(path)
            if text.startswith("/proc/"):
                return real_readlink(self.proc / text[len("/proc/"):], *a, **k)
            return real_readlink(path, *a, **k)
        built = self.root / "engine/current/src/kitty/launcher/kitten"
        built.parent.mkdir(parents=True)
        built.write_text("#!/bin/sh\nexit 0\n")
        built.chmod(0o700)
        env = {**self.stale, **self.broker_identity,
               "KILIX_BUILD_DIRECTORY": str(self.root / "engine")}
        with mock.patch.object(control.os, "readlink", side_effect=readlink):
            client = self.client(env)
        self.assertNotEqual(client.command[0], str(elsewhere / "kitten"),
                            "kitten chosen from the process named by the refreshed socket")
        self.assertEqual(client.command[0], str(built), "the engine build's own kitten")
        self.assertEqual(client.command[3], "unix:@kilix-77", "the refresh itself happened")

    def test_the_kitten_child_environment_carries_the_refreshed_route(self):
        """F-C: the kitten forwards KITTY_WINDOW_ID as kitty_window_id
        (tools/cmd/at/main.go:272), and the engine resolves it to its self window
        (kitty/boss.py:791). Client.caller is refreshed, the child env is not;
        the SDK refreshes both (test_frontend_context asserts '7')."""
        self.process(100, self.kitty, ["sh"], {}, parent=1)
        env = {**self.stale, **self.broker_identity, "KILIX_KITTEN": str(self.new_kitten)}
        client = self.client(env)
        self.assertEqual(client.caller, 3)
        self.assertEqual(client.command_env.get("KITTY_WINDOW_ID"), "3",
                         "child env keeps the old terminal's window id")


class PaneGuard(Fixture):
    def test_a_sibling_panes_broker_identity_is_never_taken(self):
        # Same terminal socket, another window: that pane's session is not ours.
        sibling = {**self.stale, **self.broker_identity, "KITTY_WINDOW_ID": "6",
                   "KITTY_PTY_BROKER_SESSION": "fedcba9876543210"}
        self.process(100, self.kitty, ["sh"], sibling, parent=1)
        seen = []
        with mock.patch.object(context, "refresh", side_effect=lambda v: seen.append(dict(v)) or False):
            self.values({"KITTY_LISTEN_ON": "unix:@kilix-10", "KITTY_WINDOW_ID": "5"})
        self.assertNotIn("KITTY_PTY_BROKER_SESSION", seen[0])

    def test_an_ancestor_without_a_socket_supplies_no_broker_identity(self):
        self.process(100, self.kitty, ["sh"], self.broker_identity, parent=101)
        other_route = {"KITTY_LISTEN_ON": "unix:@kilix-30", "KITTY_WINDOW_ID": "9",
                       "KITTY_PUBLIC_KEY": "1:k30", "KILIX_RC_PASSWORD_FILE": str(self.old_password)}
        self.process(101, self.kitty, ["sh"], other_route, parent=1)
        values = self.values({})
        self.assertNotIn("KITTY_PTY_BROKER_SESSION", values)
        self.assertEqual(values["KITTY_LISTEN_ON"], "unix:@kilix-30", "main's behaviour, not rebound")


if __name__ == "__main__":
    unittest.main()
