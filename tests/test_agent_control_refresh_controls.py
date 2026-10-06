"""agent-control after a refresh: kitten choice, private flag, child environment,
pane guard and walk, through the real frontend_context on a fake /proc.

Adapted from the second independent review of the agent-control refresh
(research/gpu_terminal/0.2.2-rc6/review-agent-control-refresh-r2).
"""
import os
from pathlib import Path
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC / "config"))
import agent_control as control  # noqa: E402
from kilix_sdk import frontend_context as context  # noqa: E402

SID = "0123456789abcdef"
OTHER_SID = "fedcba9876543210"
FLAG = "_route_refreshed"


def script(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o700)
    return path


class Fixture(unittest.TestCase):
    """Original terminal 10 is gone. Replacement terminal 20 (engine g2) runs
    attach helper 40 for this pane's broker session SID."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.runtime = self.root / "broker-runtime"
        self.runtime.mkdir(mode=0o700)
        self.empty_path = self.root / "empty-path"
        self.empty_path.mkdir()
        self.engine = self.root / "engine"
        self.g2 = self.engine / "generations" / "g2"
        self.broker = script(self.root / "kitty-pty-broker")
        self.kitty = script(self.g2 / "kitty")
        self.g2_kitten = script(self.g2 / "kitten")
        self.built_kitten = script(self.engine / "current/src/kitty/launcher/kitten")
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere_kitten = script(self.elsewhere / "kitten")
        script(self.elsewhere / "unrelated")
        script(self.elsewhere / "build/current/src/kitty/launcher/kitten")
        script(self.elsewhere / "prebuilt/bin/kitten")
        self.old_password = self.root / "old-rc-password"
        self.new_password = self.root / "new-rc-password"
        for path in (self.old_password, self.new_password):
            path.write_text("private")
            path.chmod(0o600)
        self.identity = {
            "KITTY_PID": "10", "KITTY_PTY_BROKER_SESSION": SID,
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
        self.helper = self.process(40, self.broker, [str(self.broker), "--runtime-dir",
                                                     str(self.runtime), "attach", SID],
                                   self.fresh, parent=20)
        real_readlink = os.readlink
        proc = self.proc

        def readlink(path, *a, **k):
            text = os.fspath(path)
            if isinstance(text, str) and text.startswith("/proc/"):
                return real_readlink(proc / text[len("/proc/"):], *a, **k)
            return real_readlink(path, *a, **k)
        for patcher in (mock.patch.object(context, "_PROC", self.proc),
                        mock.patch.object(control, "Path",
                                          side_effect=lambda p: proc if p == "/proc" else Path(p)),
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
        self.set_env(path, environment)
        return path

    def set_env(self, path, environment):
        (path / "environ").write_bytes(b"".join(f"{k}={v}\0".encode() for k, v in environment.items()))

    def shell(self, pid, environment, parent=1):
        return self.process(pid, self.kitty, ["sh"], environment, parent)

    def base(self, **extra):
        return {"PATH": str(self.empty_path), "HOME": str(self.root), **extra}

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

    def poison_helper(self, socket_pid=77):
        """Helper env names a socket of an unrelated process in `elsewhere`, and
        carries every kitten-selection name pointing there too."""
        self.process(socket_pid, self.elsewhere / "unrelated", ["unrelated"], {}, parent=1)
        self.fresh.update({
            "KITTY_LISTEN_ON": f"unix:@kilix-{socket_pid}",
            "KILIX_KITTEN": str(self.elsewhere_kitten),
            "KILIX_BUILD_DIRECTORY": str(self.elsewhere / "build"),
            "KILIX_PREBUILT_HOME": str(self.elsewhere / "prebuilt"),
            FLAG: "0",
        })
        self.set_env(self.helper, self.fresh)


class ControlF1Closure(Fixture):
    def test_refreshed_route_never_supplies_any_kitten_selection_name(self):
        self.poison_helper()
        self.shell(100, {})
        env = self.base(**self.stale, **self.identity, KILIX_BUILD_DIRECTORY=str(self.engine))
        client = self.client(env)
        self.assertEqual(client.command[3], "unix:@kilix-77", "the refresh happened")
        self.assertEqual(client.command[0], str(self.built_kitten))
        self.assertNotIn(str(self.elsewhere), " ".join(client.command[:1]))
        for name in ("KILIX_KITTEN", "KILIX_BUILD_DIRECTORY", "KILIX_PREBUILT_HOME"):
            self.assertNotEqual(client.command_env.get(name, ""), self.fresh[name])

    def test_refreshed_route_without_a_trusted_kitten_fails_closed(self):
        self.poison_helper()
        self.shell(100, {})
        env = self.base(**self.stale, **self.identity)
        with self.assertRaisesRegex(control.ControlError, "matching kitten is unavailable"):
            self.client(env)

    def test_refreshed_route_ignores_even_the_genuine_terminals_exe(self):
        # The socket names the real replacement terminal (pid 20, engine g2),
        # yet only configured candidates are used after a refresh.
        self.shell(100, {})
        env = self.base(**self.stale, **self.identity, KILIX_PREBUILT_HOME=str(self.root / "pb"))
        script(self.root / "pb/bin/kitten")
        client = self.client(env)
        self.assertEqual(client.command[3], "unix:@kilix-20")
        self.assertEqual(client.command[0], str(self.root / "pb/bin/kitten"))

    def test_kilix_kitten_comes_from_own_shell_not_another_instance(self):
        # The other instance's process is visited first and must be skipped.
        other = {"KITTY_LISTEN_ON": "unix:@kilix-99", "KILIX_KITTEN": str(self.elsewhere_kitten),
                 "KILIX_BUILD_DIRECTORY": str(self.elsewhere / "build")}
        self.shell(100, other, parent=101)
        self.shell(101, {**self.stale, **self.identity, "KILIX_KITTEN": str(self.g2_kitten)})
        client = self.client(self.base(KITTY_LISTEN_ON="unix:@kilix-10"))
        self.assertEqual(client.command[3], "unix:@kilix-20", "refreshed")
        self.assertEqual(client.command[0], str(self.g2_kitten))

    def test_inherited_socket_still_selects_the_terminals_own_kitten(self):
        # Control: no refresh (original 10 alive) keeps main's socket candidate.
        self.process(10, self.kitty, ["kitty"], {}, parent=1)
        self.shell(100, {})
        client = self.client(self.base(**self.stale, **self.identity,
                                       KILIX_BUILD_DIRECTORY=str(self.engine)))
        self.assertEqual(client.command[3], "unix:@kilix-10")
        self.assertEqual(client.command[0], str(self.g2_kitten))

    def test_failed_refresh_keeps_the_socket_candidate(self):
        # Control: two helpers -> fail open, inherited route, socket candidate.
        self.process(41, self.broker, [str(self.broker), "--runtime-dir", str(self.runtime),
                                       "attach", SID], self.fresh, parent=20)
        self.process(10, self.elsewhere / "unrelated", ["kitty?"], {}, parent=1)
        self.shell(100, {})
        client = self.client(self.base(**self.stale, **self.identity,
                                       KILIX_BUILD_DIRECTORY=str(self.engine)))
        self.assertEqual(client.command[3], "unix:@kilix-10")
        self.assertEqual(client.command[0], str(self.elsewhere_kitten),
                         "main's behaviour for an inherited socket (pre-existing)")


class ControlPrivateFlag(Fixture):
    def test_flag_cannot_be_injected_by_env_or_ancestor(self):
        self.process(10, self.kitty, ["kitty"], {}, parent=1)     # fast path, no refresh
        self.shell(100, {**self.stale, **self.identity, FLAG: "1"})
        values = self.values(self.base(**{FLAG: "1"}))
        self.assertNotIn(FLAG, values)
        client = self.client(self.base(**self.stale, **self.identity, **{FLAG: "1"},
                                       KILIX_BUILD_DIRECTORY=str(self.engine)))
        self.assertEqual(client.command[0], str(self.g2_kitten), "not treated as refreshed")

    def test_flag_is_set_only_by_a_real_refresh_and_never_by_the_helper(self):
        self.poison_helper()                                       # helper env says FLAG=0
        self.shell(100, {})
        values = self.values(self.base(**self.stale, **self.identity))
        self.assertEqual(values[FLAG], "1")
        self.assertEqual(set(values) - {FLAG} - set(control.ROUTE) - set(control.BROKER_IDENTITY),
                         set(), "no helper name beyond the route was copied")

    def test_flag_never_reaches_child_env_or_argv(self):
        self.shell(100, {})
        env = self.base(**self.stale, **self.identity, KILIX_BUILD_DIRECTORY=str(self.engine))
        client = self.client(env)
        self.assertEqual(client.command[3], "unix:@kilix-20")
        self.assertNotIn(FLAG, client.command_env)
        self.assertFalse(any(FLAG in item for item in client.command))


class ControlCommandEnv(Fixture):
    def test_without_refresh_the_child_env_is_exactly_the_callers(self):
        self.process(10, self.kitty, ["kitty"], {}, parent=1)
        self.shell(100, {})
        env = self.base(**self.stale, **self.identity, LANG="C.UTF-8", TERM="xterm-kitty",
                        KITTY_PTY_BROKER_AUTO_RECOVER="1", KILIX_BUILD_DIRECTORY=str(self.engine))
        client = self.client(env)
        self.assertEqual(client.command_env, env)

    def test_refresh_replaces_exactly_the_route_and_kitty_pid(self):
        self.shell(100, {})
        env = self.base(**self.stale, **self.identity, LANG="C.UTF-8",
                        KILIX_BUILD_DIRECTORY=str(self.engine))
        client = self.client(env)
        expected = dict(env)
        expected.update({k: self.fresh[k] for k in (*control.ROUTE, "KITTY_PID")})
        self.assertEqual(client.command_env, expected)
        self.assertEqual(client.command_env["KITTY_PID"], "20")


class ControlPaneGuard(Fixture):
    def test_own_env_without_window_accepts_own_shell(self):
        self.shell(100, {**self.stale, **self.identity})
        values = self.values(self.base(KITTY_LISTEN_ON="unix:@kilix-10"))
        self.assertEqual(values.get(FLAG), "1")
        self.assertEqual(values["KITTY_WINDOW_ID"], "3")

    def test_socket_only_wrapper_then_own_shell(self):
        self.shell(100, {"KITTY_LISTEN_ON": "unix:@kilix-10"}, parent=101)
        self.shell(101, {**self.stale, **self.identity})
        values = self.values(self.base(**self.stale))
        self.assertEqual(values.get(FLAG), "1")

    def test_socketless_wrapper_then_own_shell(self):
        self.shell(100, {"LANG": "C"}, parent=101)
        self.shell(101, {**self.stale, **self.identity})
        values = self.values(self.base(**self.stale))
        self.assertEqual(values.get(FLAG), "1")

    def test_window_stays_bound_to_the_first_window_seen(self):
        # Own env has no window; own shell supplies 5; a further same-socket
        # ancestor with window 6 cannot add the identity of window 6.
        self.shell(100, {"KITTY_LISTEN_ON": "unix:@kilix-10", "KITTY_WINDOW_ID": "5"}, parent=101)
        self.shell(101, {**self.stale, **self.identity, "KITTY_WINDOW_ID": "6"})
        values = self.values(self.base(KITTY_LISTEN_ON="unix:@kilix-10"))
        self.assertNotIn("KITTY_PTY_BROKER_SESSION", values)
        self.assertNotIn(FLAG, values)

    def test_sibling_identity_refused_even_when_route_is_complete(self):
        # F2's longer walk must not let a different window's identity in.
        self.shell(100, {**self.stale, **self.identity, "KITTY_WINDOW_ID": "6"})
        values = self.values(self.base(**self.stale))
        self.assertNotIn("KITTY_PTY_BROKER_SESSION", values)
        self.assertNotIn(FLAG, values)


class ControlWalk(Fixture):
    def test_walk_is_bounded_and_takes_only_same_socket_ancestors(self):
        # Non-broker pane with a complete route: the walk now runs to the bound.
        for pid in range(100, 140):
            self.shell(pid, {"KITTY_LISTEN_ON": "unix:@kilix-99", "KITTY_WINDOW_ID": "9",
                             "KILIX_KITTEN": str(self.elsewhere_kitten), **self.identity},
                       parent=pid + 1)
        reads = []
        real = pathlib.Path.read_bytes

        def counted(path):
            if path.name == "environ":
                reads.append(path.parent.name)
            return real(path)
        with mock.patch.object(pathlib.Path, "read_bytes", counted):
            values = self.values(self.base(**self.stale))
        self.assertEqual(len(reads), 24)
        self.assertEqual(values, self.stale)


class ControlResolverGaps(Fixture):
    """frontend_context is unchanged by this branch; these kill B08 and B09,
    which survive the whole author suite (a gap on main)."""

    def test_helper_parent_not_named_kitty_is_refused(self):
        (self.proc / "20" / "exe").unlink()
        (self.proc / "20" / "exe").symlink_to(script(self.g2 / "not-a-terminal"))
        self.shell(100, {})
        values = self.values(self.base(**self.stale, **self.identity))
        self.assertEqual(self.route(values), self.stale)
        self.assertNotIn(FLAG, values)

    def test_helper_of_another_user_is_refused(self):
        helper = self.helper
        real_stat = pathlib.Path.stat

        def stat(path, *a, **k):
            result = real_stat(path, *a, **k)
            if path == helper:
                fields = list(result)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return result
        self.shell(100, {})
        with mock.patch.object(pathlib.Path, "stat", stat):
            values = self.values(self.base(**self.stale, **self.identity))
        self.assertEqual(self.route(values), self.stale)


if __name__ == "__main__":
    unittest.main()
