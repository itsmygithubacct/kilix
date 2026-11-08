"""GParted leaves the private bus and asks for the root password in a dialog."""
import os
from pathlib import Path
import stat
import sys
import unittest
from io import StringIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from kilix_sdk.elevation import (
    ElevationRefused, askpass_path, plan_gparted_elevation, trusted_sudo_target,
)
from kilix_sdk import graphical_askpass


ROOT = Path(__file__).resolve().parents[1]
SECRET = "s3cret-not-typed-here"


class Nodes:
    def __init__(self):
        self.nodes = {}

    def add(self, path, mode, uid=0):
        self.nodes[path] = (mode, uid)
        current = os.path.dirname(path)
        while current not in self.nodes:
            self.nodes[current] = (stat.S_IFDIR | 0o755, 0)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

    def lstat(self, path):
        if path not in self.nodes:
            raise FileNotFoundError(path)
        mode, uid = self.nodes[path]
        return type("Info", (), {"st_mode": mode, "st_uid": uid})()

    def stat(self, path):
        return self.lstat(path)

    def realpath(self, path):
        return path


def installed_tree():
    nodes = Nodes()
    nodes.add("/usr/bin/sudo", stat.S_IFREG | stat.S_ISUID | 0o755, 0)
    nodes.add("/usr/bin/env", stat.S_IFREG | 0o755, 0)
    nodes.add("/usr/sbin/gparted", stat.S_IFREG | 0o755, 0)
    nodes.add("/opt/kilix/config", stat.S_IFDIR | 0o755, 1000)
    nodes.add("/opt/kilix/config/kilix_askpass", stat.S_IFREG | 0o755, 1000)
    nodes.add("/var/lib/kilix-session/.Xauthority", stat.S_IFREG | 0o600, 1000)
    return nodes


def session_env(**extra):
    env = {
        "DISPLAY": ":0",
        "XAUTHORITY": "/var/lib/kilix-session/.Xauthority",
        "HOME": "/var/lib/kilix-session",
        "PATH": "/tmp/evil:/usr/bin",
        "LD_PRELOAD": "/tmp/evil.so",
        "PYTHONPATH": "/tmp/evil",
        "GTK_MODULES": "evil",
        "SUDO_ASKPASS": "/tmp/steal",
    }
    env.update(extra)
    return env


class GPartedElevationTests(unittest.TestCase):
    def hooks(self, nodes):
        return dict(stat_fn=nodes.stat, lstat_fn=nodes.lstat, realpath_fn=nodes.realpath,
                    config_dir="/opt/kilix/config")

    def plan(self, argv=None, env=None, nodes=None):
        nodes = installed_tree() if nodes is None else nodes
        argv = ["/usr/sbin/gparted", "/dev/sda"] if argv is None else argv
        env = session_env() if env is None else env
        return plan_gparted_elevation(argv, env, **self.hooks(nodes))

    def parent_for(self, plan, nodes, argv=None):
        info = nodes.lstat("/usr/bin/sudo")
        return {
            "exe": "/usr/bin/sudo",
            "mode": info.st_mode,
            "uid": 0,
            "argv": list(plan.argv if argv is None else argv),
        }

    def test_installed_gparted_execs_sudo_with_a_graphical_askpass(self):
        nodes = installed_tree()
        plan = self.plan(nodes=nodes)
        self.assertEqual(plan.argv[:4], (
            "/usr/bin/sudo", "--askpass", "--", "/usr/bin/env"))
        self.assertEqual(plan.argv[4:], (
            "DISPLAY=:0", "XAUTHORITY=/var/lib/kilix-session/.Xauthority",
            "/usr/sbin/gparted", "/dev/sda"))
        self.assertEqual(plan.env["SUDO_ASKPASS"], "/opt/kilix/config/kilix_askpass")
        self.assertEqual(plan.env["SUDO_PROMPT"],
                         "Authentication is required to run /usr/sbin/gparted as root.")
        self.assertEqual(plan.env["PATH"], "/usr/bin:/bin")
        self.assertNotIn("LD_PRELOAD", plan.env)
        self.assertNotIn("PYTHONPATH", plan.env)
        self.assertNotIn("GTK_MODULES", plan.env)
        self.assertNotIn(SECRET, plan.argv)
        self.assertNotIn("/tmp/steal", plan.env.values())
        self.assertEqual(
            trusted_sudo_target(self.parent_for(plan, nodes), stat_fn=nodes.stat,
                                lstat_fn=nodes.lstat, realpath_fn=nodes.realpath),
            "/usr/sbin/gparted")

    def test_untrusted_or_incomplete_launches_are_refused(self):
        nodes = installed_tree()
        self.assertIsNone(plan_gparted_elevation(["/usr/bin/xterm"], session_env(), **self.hooks(nodes)))
        for argv, env, mutate in (
            (["gparted"], None, None),
            (["/tmp/gparted"], None, None),
            (["/usr/sbin/gparted", "bad\narg"], None, None),
            (["/usr/sbin/gparted"], {"HOME": "/var/lib/kilix-session"}, None),
            (["/usr/sbin/gparted"], None, "writable"),
            (["/usr/sbin/gparted"], None, "nosudo"),
            (["/usr/sbin/gparted"], None, "askpass"),
        ):
            tree = installed_tree()
            if mutate == "writable":
                tree.add("/usr/sbin/gparted", stat.S_IFREG | 0o775, 0)
            elif mutate == "nosudo":
                tree.add("/usr/bin/sudo", stat.S_IFREG | 0o755, 0)
            elif mutate == "askpass":
                tree.add("/opt/kilix/config/kilix_askpass", stat.S_IFREG | 0o777, 1000)
            with self.subTest(argv=argv, mutate=mutate, display=env.get("DISPLAY") if env else None):
                with self.assertRaises(ElevationRefused):
                    self.plan(argv, session_env() if env is None else env, tree)

    def test_sudo_running_another_command_cannot_read_the_password(self):
        nodes = installed_tree()
        plan = self.plan(nodes=nodes)
        info = nodes.lstat("/usr/bin/sudo")
        attacks = [
            ["/usr/bin/sudo", "--askpass", "--", "/bin/bash"],
            ["/usr/bin/sudo", "-s"],
            ["/usr/bin/sudo", "--askpass", "--", "/usr/bin/env", "DISPLAY=:0",
             "XAUTHORITY=/var/lib/kilix-session/.Xauthority", "/bin/bash"],
            ["/usr/bin/sudo", "--askpass", "--", "/usr/bin/env", "DISPLAY=:0",
             "XAUTHORITY=/var/lib/kilix-session/.Xauthority", "/tmp/gparted"],
            ["/usr/bin/sudo", "--askpass", "-u", "root", "--", "/usr/bin/env",
             "DISPLAY=:0", "XAUTHORITY=/var/lib/kilix-session/.Xauthority", "/usr/sbin/gparted"],
        ]
        for argv in attacks:
            parent = {"exe": "/usr/bin/sudo", "mode": info.st_mode, "uid": 0, "argv": argv}
            with self.subTest(argv=argv):
                self.assertIsNone(trusted_sudo_target(
                    parent, stat_fn=nodes.stat, lstat_fn=nodes.lstat, realpath_fn=nodes.realpath))
        user_sudo = dict(self.parent_for(plan, nodes))
        user_sudo["uid"] = 1000
        user_sudo["mode"] = stat.S_IFREG | 0o755
        self.assertIsNone(trusted_sudo_target(
            user_sudo, stat_fn=nodes.stat, lstat_fn=nodes.lstat, realpath_fn=nodes.realpath))

    def test_askpass_writes_one_line_for_the_verified_command_only(self):
        nodes = installed_tree()
        plan = self.plan(nodes=nodes)
        parent = self.parent_for(plan, nodes)
        seen = []

        def dialog(message):
            seen.append(message)
            return SECRET

        stdout = StringIO()
        code = graphical_askpass.main(
            ["kilix_askpass", "Password for an unrelated program:"],
            dialog=dialog, parent_reader=lambda: parent, stdout=stdout, stderr=StringIO(),
            stat_fn=nodes.stat, lstat_fn=nodes.lstat, realpath_fn=nodes.realpath)
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), SECRET + "\n")
        self.assertEqual(seen, ["Authentication is required to run /usr/sbin/gparted as root."])

        def cancelled(message):
            return None

        stdout = StringIO()
        code = graphical_askpass.main(
            ["kilix_askpass"], dialog=cancelled, parent_reader=lambda: parent,
            stdout=stdout, stderr=StringIO(),
            stat_fn=nodes.stat, lstat_fn=nodes.lstat, realpath_fn=nodes.realpath)
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")

        def forbidden(message):
            raise AssertionError("dialog shown for an untrusted parent")

        stdout = StringIO()
        stderr = StringIO()
        code = graphical_askpass.main(
            ["kilix_askpass"], dialog=forbidden,
            parent_reader=lambda: {"exe": "/bin/bash", "mode": stat.S_IFREG | 0o755, "uid": 1000, "argv": ["bash"]},
            stdout=stdout, stderr=stderr,
            stat_fn=nodes.stat, lstat_fn=nodes.lstat, realpath_fn=nodes.realpath)
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("refused", stderr.getvalue())

    def test_askpass_rechecks_the_parent_before_writing(self):
        nodes = installed_tree()
        plan = self.plan(nodes=nodes)
        good = self.parent_for(plan, nodes)
        calls = {"n": 0}

        def reader():
            calls["n"] += 1
            if calls["n"] == 1:
                return good
            return {"exe": "/bin/bash", "mode": stat.S_IFREG | 0o755, "uid": 1000, "argv": ["bash"]}

        stdout = StringIO()
        code = graphical_askpass.main(
            ["kilix_askpass"], dialog=lambda message: SECRET, parent_reader=reader,
            stdout=stdout, stderr=StringIO(),
            stat_fn=nodes.stat, lstat_fn=nodes.lstat, realpath_fn=nodes.realpath)
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(calls["n"], 2)

    def test_shipped_helper_is_executable_and_apprun_diverts_first(self):
        path = Path(askpass_path())
        self.assertEqual(path, ROOT / "config" / "kilix_askpass")
        self.assertTrue(os.access(path, os.X_OK))
        text = path.read_text()
        self.assertTrue(text.startswith("#!/usr/bin/python3 -I\n"))
        self.assertIn("graphical_askpass", text)
        source = (ROOT / "config" / "apprun.py").read_text()
        call = source.index("plan_gparted_elevation(")
        pane = source.index("AppPane(")
        self.assertLess(call, pane)
        self.assertLess(call, source.index("prepare_app_command(args)"))
        self.assertIn("os.execve(plan.argv[0], list(plan.argv), plan.env)", source)
        dialog = (ROOT / "config" / "kilix_sdk" / "graphical_askpass.py").read_text()
        self.assertIn('title="Authentication Required"', dialog)
        self.assertIn("set_visibility(False)", dialog)
        self.assertIn("Authenticate", dialog)
        self.assertIn("Cancel", dialog)


if __name__ == "__main__":
    unittest.main()
