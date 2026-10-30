"""Temporary-tree installation, recovery and privilege-boundary checks."""
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import fingerlock_install as install
import fingerlock_policy as policy

SOURCE = Path(__file__).resolve().parents[1] / "config"


class FakeSystem:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.fail = None
        self.enabled = True
        self.active = True

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if self.fail and self.fail(argv):
            self.fail = None
            return SimpleNamespace(returncode=1, stdout="", stderr="injected failure")
        output = ""
        if "show" in argv:
            if "UnitFileState" in argv:
                output = "enabled" if self.enabled else "disabled"
            elif "DropInPaths" in argv:
                output = ""
            elif "LoadState" in argv:
                unit = self.root / "etc/systemd/system" / argv[2]
                output = "loaded" if unit.is_symlink() else "not-found"
            else:
                output = "active" if self.active else "inactive"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.raw = install.build_package(SOURCE, 1000, policy.POLICY_XML)

    def test_package_is_deterministic_and_has_the_complete_agent_imports(self):
        self.assertEqual(self.raw, install.build_package(SOURCE, 1000, policy.POLICY_XML))
        files = install.decode_package(self.raw, 1000)
        self.assertIn("fingerlock_locker.py", files)
        self.assertIn("fingerlock_policy.py", files)
        self.assertIn(b"SocketMode=0600", files[install.unit_names(1000)[0]])
        self.assertIn(b"SocketUser=1000", files[install.unit_names(1000)[0]])
        self.assertIn(b"python3 -I -B", files[install.unit_names(1000)[1]])
        self.assertNotIn(b"auth_self_keep", files[install.POLICY])

    def test_other_uid_extra_path_bad_base64_and_duplicates_are_rejected(self):
        with self.assertRaises(install.InstallError):
            install.decode_package(self.raw, 1001)
        for name, value in (("../outside", "eA=="), ("fingerlock_core.py", "!")):
            data = json.loads(self.raw)
            data["files"][name] = value
            with self.assertRaises(install.InstallError):
                install.decode_package(json.dumps(data).encode(), 1000)
        with self.assertRaises(install.InstallError):
            install.decode_package(b'{"uid":1000,"uid":1000}', 1000)

    def test_root_bootstrap_checks_digest_before_package_execution(self):
        # This child only substitutes a harmless receipt function. No Installer
        # code, root filesystem access or real elevation is executed.
        code = b'def privileged_main(raw, uid, operation): print("verified", uid, operation)\n'
        raw = json.dumps({"files": {"fingerlock_install.py": base64.b64encode(code).decode()}}).encode()
        bootstrap = 'import os; os.geteuid = lambda: 0\n' + install.BOOTSTRAP
        argv = [sys.executable, "-I", "-c", bootstrap, hashlib.sha256(raw).hexdigest(), "1000", "install"]
        valid = subprocess.run(argv, input=raw, capture_output=True)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(valid.stdout, b"verified 1000 install\n")
        tampered = subprocess.run(argv, input=raw + b" ", capture_output=True)
        self.assertNotEqual(tampered.returncode, 0)
        self.assertEqual(tampered.stdout, b"")
        self.assertIn(b"digest mismatch", tampered.stderr)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.system = FakeSystem(self.root)
        self.health_calls = []
        self.installer = install.Installer(self.root, owner=os.getuid(), run=self.system,
                                           health=lambda uid: self.health_calls.append(uid))
        self.uid = 1000
        self.raw = install.build_package(SOURCE, self.uid, policy.POLICY_XML)
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.user = self.root / install.STORE / "users/1000"
        self.units = self.root / "etc/systemd/system"

    def next_package(self):
        data = json.loads(self.raw)
        name = "fingerlock_core.py"
        data["files"][name] = base64.b64encode(base64.b64decode(data["files"][name]) + b"\n# next version\n").decode()
        return json.dumps(data, sort_keys=True).encode()

    def current(self):
        return self.installer._target(self.user / "current")

    def test_install_upgrade_and_explicit_rollback(self):
        self.assertEqual(self.installer.apply(self.raw, self.uid), self.digest)
        self.assertEqual(self.current(), self.digest)
        self.assertFalse((self.user / "pending.json").exists())
        directory = self.root / install.STORE / "versions" / self.digest
        self.assertEqual(directory.stat().st_mode & 0o777, 0o555)
        for path in directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)
        later = self.next_package()
        later_digest = self.installer.apply(later, self.uid)
        self.assertNotEqual(later_digest, self.digest)
        self.assertEqual(self.installer._target(self.user / "previous"), self.digest)
        self.assertEqual(self.installer.apply(later, self.uid, rollback=True), self.digest)
        self.assertEqual(self.current(), self.digest)
        self.assertEqual(self.installer._target(self.user / "previous"), later_digest)
        for name in install.unit_names(self.uid):
            self.assertTrue((self.units / name).is_symlink())

    def test_failed_upgrade_restores_current_and_activation_state(self):
        self.installer.apply(self.raw, self.uid)
        self.system.enabled = False
        self.system.active = False
        self.system.fail = lambda argv: "start" in argv
        self.system.calls.clear()
        with self.assertRaisesRegex(install.InstallError, "previous installation restored"):
            self.installer.apply(self.next_package(), self.uid)
        self.assertEqual(self.current(), self.digest)
        self.assertFalse((self.user / "pending.json").exists())
        self.assertIn(["/usr/bin/systemctl", "disable", install.unit_names(self.uid)[0]], self.system.calls)
        self.assertEqual(sum("start" in argv for argv in self.system.calls), 1)

    def test_failed_first_activation_removes_current_and_unit_links(self):
        self.system.fail = lambda argv: "enable" in argv
        with self.assertRaisesRegex(install.InstallError, "previous installation restored"):
            self.installer.apply(self.raw, self.uid)
        self.assertIsNone(self.current())
        for name in install.unit_names(self.uid):
            self.assertFalse(os.path.lexists(self.units / name))
        self.assertFalse((self.user / "pending.json").exists())

    def test_crash_journal_is_recovered_before_the_next_attempt(self):
        self.installer.apply(self.raw, self.uid)
        normal_run = self.installer.run
        def crash(argv, **kwargs):
            if "enable" in argv:
                raise KeyboardInterrupt()
            return normal_run(argv, **kwargs)
        self.installer.run = crash
        with self.assertRaises(KeyboardInterrupt):
            self.installer.apply(self.next_package(), self.uid)
        self.assertTrue((self.user / "pending.json").exists())
        self.assertNotEqual(self.current(), self.digest)
        self.installer.run = normal_run
        self.installer.apply(self.raw, self.uid)
        self.assertEqual(self.current(), self.digest)
        self.assertFalse((self.user / "pending.json").exists())

    def test_failed_recovery_keeps_the_journal(self):
        self.installer.apply(self.raw, self.uid)
        normal_run = self.installer.run
        def fail(argv, **kwargs):
            if "enable" in argv or "stop" in argv:
                return SimpleNamespace(returncode=1, stdout="", stderr="persistent error")
            return normal_run(argv, **kwargs)
        self.installer.run = fail
        with self.assertRaisesRegex(install.InstallError, "recovery incomplete"):
            self.installer.apply(self.next_package(), self.uid)
        self.assertTrue((self.user / "pending.json").exists())

    def test_modified_stored_code_is_refused_before_service_changes(self):
        self.installer.apply(self.raw, self.uid)
        path = self.root / install.STORE / "versions" / self.digest / "fingerlock_agent.py"
        path.chmod(0o644)
        path.write_text("changed")
        self.system.calls.clear()
        with self.assertRaisesRegex(install.InstallError, "differs from its package"):
            self.installer.apply(self.next_package(), self.uid)
        self.assertTrue(all("show" in argv for argv in self.system.calls))

    def test_symlink_parent_and_unmanaged_units_are_refused(self):
        (self.root / "usr").symlink_to(self.root / "elsewhere", target_is_directory=True)
        with self.assertRaises(install.InstallError):
            self.installer.apply(self.raw, self.uid)
        (self.root / "usr").unlink()
        self.units.mkdir(parents=True)
        unit = self.units / install.unit_names(self.uid)[0]
        unit.write_text("owned by another installer")
        with self.assertRaisesRegex(install.InstallError, "unmanaged"):
            self.installer.apply(self.raw, self.uid)
        self.assertEqual(unit.read_text(), "owned by another installer")
        self.assertEqual(self.system.calls, [])

    def tearDown(self):
        # Test fixtures use the real immutable modes, so permit their cleanup.
        for path in self.root.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.binding = {"kind": "unlock", "name": "photo\u202e.txt", "size": 5, "sha256": "a" * 64}

    def test_fresh_approval_names_exact_operation_and_process(self):
        calls = []
        def check(argv, alive, timeout):
            calls.append(argv)
            return 0 if "--allow-user-interaction" in argv else 2
        policy.authorize_operation(os.getuid(), os.getpid(), self.binding, lambda: True, run_check=check)
        self.assertEqual(len(calls), 2)
        self.assertIn("--allow-user-interaction", calls[1])
        self.assertIn("a" * 64, calls[0])
        subject = calls[0][calls[0].index("--process") + 1].split(",")
        self.assertEqual(subject[0], str(os.getpid()))
        self.assertEqual(subject[2], str(os.getuid()))
        self.assertGreater(int(subject[1]), 0)
        self.assertIn("\\u202e", calls[0][calls[0].index("polkit.message") + 1])

    def test_cached_denied_missing_agent_cancel_and_error_never_approve(self):
        from fingerlock_core import AccessDenied
        for preflight in (0, 1, 3, 126, 127):
            with self.subTest(preflight=preflight):
                check = unittest.mock.Mock(return_value=preflight)
                with self.assertRaises(AccessDenied):
                    policy.authorize_operation(os.getuid(), os.getpid(), self.binding, lambda: True, run_check=check)
                self.assertEqual(check.call_count, 1)
        for response in (1, 2, 3, 126, 127):
            with self.subTest(response=response), self.assertRaises(AccessDenied):
                policy.authorize_operation(os.getuid(), os.getpid(), self.binding, lambda: True,
                                           run_check=unittest.mock.Mock(side_effect=[2, response]))

    def test_disconnect_terminates_the_approval_process(self):
        from fingerlock_core import AccessDenied
        real_popen = subprocess.Popen
        children = []
        def start(argv, **kwargs):
            child = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(child)
            return child
        with patch.object(policy.subprocess, "Popen", side_effect=start), self.assertRaises(AccessDenied):
            policy._run_check(["unused"], lambda: False, 5)
        self.assertIsNotNone(children[0].poll())

    def test_kernel_peer_token_notices_original_process_exit(self):
        import fingerlock_agent as agent
        with tempfile.TemporaryDirectory() as tmp, socket.socket(socket.AF_UNIX) as listener:
            path = str(Path(tmp) / "peer.sock")
            listener.bind(path)
            listener.listen(1)
            listener.settimeout(3)
            code = "import socket,sys,time; s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); time.sleep(30)"
            child = subprocess.Popen([sys.executable, "-c", code, path])
            try:
                conn, _ = listener.accept()
                with conn:
                    fd = agent._peer_pidfd(conn)
                    try:
                        self.assertTrue(agent._process_alive(fd))
                        child.terminate()
                        child.wait(timeout=3)
                        self.assertFalse(agent._process_alive(fd))
                    finally:
                        os.close(fd)
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()


if __name__ == "__main__":
    unittest.main()
