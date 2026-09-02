"""The fingerlock locker and agent: the two halves that make a fingerprint
unlock something.

Locker: a passphrase-wrapped seed, a stable X25519 keypair, sealed chunked
containers that fail closed on any byte flip, truncation or chunk reorder, and
a passphrase change that touches no container. Agent: per-uid custody with a
TTL, session gating, and a release that happens only on `verify-match`, tested
against the real socket server in a thread with a fake fprintd client.
"""
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

try:
    import nacl  # noqa: F401
    HAVE_NACL = True
except ImportError:
    HAVE_NACL = False


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "config" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = load("fingerlock_core")
lockermod = load("fingerlock_locker")
agentmod = load("fingerlock_agent")
fl = load("fingerlock")

FAST = {"opslimit": 1, "memlimit": 8192}   # Argon2id minimums: tests, never production


@unittest.skipUnless(HAVE_NACL, "python3-nacl is not installed")
class LockerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "locker"
        self.addCleanup(self.tmp.cleanup)
        self.locker = lockermod.Locker(self.home)
        self.info = self.locker.init("correct horse battery", **FAST)

    def test_init_is_private_and_keys_stay_off_disk(self):
        self.assertEqual(oct(self.home.stat().st_mode & 0o777), "0o700")
        self.assertEqual(oct(self.locker.path.stat().st_mode & 0o777), "0o600")
        data = json.loads(self.locker.path.read_text())
        self.assertEqual(set(data), {"version", "locker_id", "created", "kdf", "public_key", "wrapped_seed"})
        private = self.locker.derive_private_key("correct horse battery")
        self.assertEqual(len(private), 32)
        self.assertNotIn(private.hex(), self.locker.path.read_text())
        with self.assertRaises(lockermod.WrongPassphrase):
            self.locker.derive_private_key("wrong")
        with self.assertRaises(lockermod.LockerError):
            self.locker.init("again again", **FAST)

    def _round_trip(self, payload, chunk_size):
        src = Path(self.tmp.name) / "photo.jpg"
        src.write_bytes(payload)
        container = lockermod.container_for(src)
        header = lockermod.lock_file(src, container, self.info, chunk_size=chunk_size, remove_source=True)
        self.assertFalse(src.exists())
        self.assertEqual(header["size"], len(payload))
        private = self.locker.derive_private_key("correct horse battery")
        out = lockermod.original_for(container)
        lockermod.unlock_file(container, out, private, remove_container=True)
        self.assertEqual(out.read_bytes(), payload)
        self.assertFalse(container.exists())
        return src, container

    def test_round_trip_small_and_multichunk(self):
        self._round_trip(b"", 1024)
        self._round_trip(b"hello", 1024)
        self._round_trip(os.urandom(1024 * 3 + 7), 1024)
        self._round_trip(os.urandom(1024 * 2), 1024)   # exact chunk boundary

    def _sealed(self, payload=None, chunk_size=1024):
        src = Path(self.tmp.name) / "doc.txt"
        src.write_bytes(payload if payload is not None else os.urandom(chunk_size * 3 + 5))
        container = lockermod.container_for(src)
        lockermod.lock_file(src, container, self.info, chunk_size=chunk_size)
        return src, container

    def test_tampering_fails_closed(self):
        src, container = self._sealed()
        private = self.locker.derive_private_key("correct horse battery")
        original = container.read_bytes()
        header_len = original.index(b"\n") + 1
        body = original[header_len:]
        sealed_chunk = 1024 + 16
        cases = {
            "flipped body byte": original[:header_len + 100] + bytes([original[header_len + 100] ^ 1]) + original[header_len + 101:],
            "truncated": original[:-sealed_chunk],
            "reordered chunks": original[:header_len] + body[sealed_chunk:2 * sealed_chunk] + body[:sealed_chunk] + body[2 * sealed_chunk:],
            "dropped first chunk": original[:header_len] + body[sealed_chunk:],
            "edited header name": original.replace(b'"name":"doc.txt"', b'"name":"evil.txt"'),
        }
        for label, mutated in cases.items():
            with self.subTest(label=label):
                bad = Path(self.tmp.name) / "bad.flk"
                bad.write_bytes(mutated)
                with self.assertRaises(lockermod.LockerError):
                    lockermod.unlock_file(bad, Path(self.tmp.name) / "bad.out", private)
                self.assertFalse((Path(self.tmp.name) / "bad.out").exists())

    def test_other_locker_key_cannot_open(self):
        _src, container = self._sealed(b"secret")
        other = lockermod.Locker(Path(self.tmp.name) / "other")
        other.init("another passphrase", **FAST)
        with self.assertRaises(lockermod.LockerError):
            lockermod.unlock_file(container, Path(self.tmp.name) / "x", other.derive_private_key("another passphrase"))

    def test_passphrase_change_keeps_containers_openable(self):
        _src, container = self._sealed(b"payload")
        self.locker.change_passphrase("correct horse battery", "new passphrase here")
        with self.assertRaises(lockermod.WrongPassphrase):
            self.locker.derive_private_key("correct horse battery")
        private = self.locker.derive_private_key("new passphrase here")
        sink = io.BytesIO()
        lockermod.unlock_file(container, None, private, sink=sink)
        self.assertEqual(sink.getvalue(), b"payload")
        self.assertEqual(self.locker.info().locker_id, self.info.locker_id)

    def test_password_names_are_validated(self):
        with self.assertRaises(core.UsageError):
            self.locker.password_path("../etc/passwd")
        with self.assertRaises(core.UsageError):
            self.locker.password_path("")
        self.assertEqual(self.locker.password_path("mail@example").name, "mail@example.flk")


@unittest.skipUnless(HAVE_NACL, "python3-nacl is not installed")
class LockerCommandTests(unittest.TestCase):
    """The verb's lock/unlock/password actions through main(), passphrase path only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.home = self.root / "locker"
        self.sock = str(self.root / "no-agent.sock")
        lockermod.Locker(self.home).init("correct horse battery", **FAST)

    def run_main(self, argv, passphrase="correct horse battery", stdin=None):
        read_end, write_end = os.pipe()
        os.write(write_end, (passphrase + "\n").encode())
        os.close(write_end)
        argv = [*argv, "--locker-home", str(self.home), "--agent-socket", self.sock, "--passphrase-fd", str(read_end)]
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                if stdin is not None:
                    with unittest.mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin))):
                        status = fl.main(argv)
                else:
                    status = fl.main(argv)
        finally:
            os.close(read_end)
        return status, out.getvalue(), err.getvalue()

    def test_lock_then_unlock(self):
        photo = self.root / "cat.png"
        photo.write_bytes(b"\x89PNG not really")
        status, out, _ = self.run_main(["lock", str(photo)])
        self.assertEqual(status, 0, out)
        self.assertFalse(photo.exists())
        container = self.root / "cat.png.flk"
        self.assertTrue(container.exists())
        status, out, err = self.run_main(["unlock", str(container), "--json"])
        self.assertEqual(status, 0, out + err)
        payload = json.loads(out)
        self.assertEqual(payload["data"]["key_source"], "passphrase")
        self.assertEqual(photo.read_bytes(), b"\x89PNG not really")
        self.assertFalse(container.exists())

    def test_wrong_passphrase_is_exit_11(self):
        doc = self.root / "doc"
        doc.write_bytes(b"x")
        self.run_main(["lock", str(doc)])
        status, out, _ = self.run_main(["unlock", str(self.root / "doc.flk"), "--json"], passphrase="nope nope")
        self.assertEqual(status, 11)
        self.assertEqual(json.loads(out)["code"], "EPASSPHRASE")

    def test_password_set_get_list_delete(self):
        status, out, _ = self.run_main(["password", "set", "wifi", "--stdin"], stdin=b"hunter2")
        self.assertEqual(status, 0, out)
        status, out, _ = self.run_main(["password", "list"])
        self.assertEqual(out, "wifi\n")
        status, out, _ = self.run_main(["password", "get", "wifi", "--json"])
        self.assertEqual(json.loads(out)["data"]["secret"], "hunter2")
        status, _, _ = self.run_main(["password", "set", "wifi", "--stdin"], stdin=b"again")
        self.assertEqual(status, 2)
        status, out, _ = self.run_main(["password", "delete", "wifi"])
        self.assertEqual(status, 0)
        self.assertEqual(self.run_main(["password", "list"])[1], "(no passwords stored)\n")

    def test_passwd_and_init_twice(self):
        status, _, _ = self.run_main(["init"])
        self.assertEqual(status, 10)
        read_end, write_end = os.pipe()
        os.write(write_end, b"correct horse battery\nbrand new passphrase\n")
        os.close(write_end)
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            status = fl.main(["passwd", "--locker-home", str(self.home), "--agent-socket", self.sock,
                              "--passphrase-fd", str(read_end)])
        os.close(read_end)
        self.assertEqual(status, 0, out.getvalue())
        self.assertEqual(self.run_main(["password", "list"], passphrase="brand new passphrase")[0], 0)

    def test_agent_absent_is_reported(self):
        status, out, _ = self.run_main(["agent"])
        self.assertEqual(status, 1)
        self.assertIn("not installed", out)
        status, out, _ = self.run_main(["forget", "--json"])
        self.assertEqual(json.loads(out)["data"], {"forgotten": False, "agent": False})


class FakeVerifyClient:
    def __init__(self, result="verify-match", timed_out=False):
        self.result, self.timed_out = result, timed_out
        self.calls = []

    def default_device(self):
        return core.DeviceInfo("/dev/0", "Synaptics Sensors", "press", 9, False, False)

    def verify(self, path, finger, *, username, timeout, **kwargs):
        self.calls.append((finger, username))
        session = core.Session("verify", finger, path)
        if not self.timed_out:
            session.handle(self.result, True)
            session.selected_finger = "right-index-finger"
        session.timed_out = self.timed_out
        return session


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        self.custody = agentmod.Custody(now=lambda: self.clock[0])
        self.client = FakeVerifyClient()
        self.agent = agentmod.Agent(custody=self.custody, client_factory=lambda: self.client,
                                    session_check=lambda sid: (sid == "2", f"session {sid}"),
                                    proc=Path(tempfile.mkdtemp()))
        (self.agent.proc / "4242").mkdir()
        (self.agent.proc / "4242" / "sessionid").write_text("2\n")
        (self.agent.proc / "4343").mkdir()
        (self.agent.proc / "4343" / "sessionid").write_text("9\n")
        self.uid = os.getuid()
        self.key = os.urandom(32)

    def handle(self, op, pid=4242, **fields):
        return self.agent.handle({"protocol": 1, "op": op, **fields}, self.uid, pid)

    def test_deposit_release_forget(self):
        self.assertEqual(self.handle("status"), {"ok": True, "held": False})
        reply = self.handle("deposit", key=self.key.hex(), locker_id="L1", ttl=3600)
        self.assertTrue(reply["held"])
        reply = self.handle("release", locker_id="L1")
        self.assertEqual(bytes.fromhex(reply["key"]), self.key)
        self.assertEqual(self.client.calls, [("any", os.getlogin() if False else __import__("pwd").getpwuid(self.uid).pw_name)])
        self.assertTrue(self.handle("forget")["forgotten"])
        self.assertEqual(self.handle("release", locker_id="L1")["code"], "EACCESS")

    def test_ttl_expiry_and_wrong_locker(self):
        self.handle("deposit", key=self.key.hex(), locker_id="L1", ttl=600)
        self.assertEqual(self.handle("release", locker_id="OTHER")["code"], "EACCESS")
        self.clock[0] += 601
        self.assertEqual(self.handle("status"), {"ok": True, "held": False})

    def test_inactive_session_is_refused_for_key_ops(self):
        reply = self.handle("deposit", pid=4343, key=self.key.hex(), locker_id="L1")
        self.assertEqual(reply["code"], "EACCESS")
        self.assertIn("active seat", reply["error"])
        self.assertEqual(self.handle("status", pid=4343), {"ok": True, "held": False})

    def test_no_match_and_timeout_release_nothing(self):
        self.handle("deposit", key=self.key.hex(), locker_id="L1")
        self.client.result = "verify-no-match"
        self.assertEqual(self.handle("release", locker_id="L1")["code"], "ENOMATCH")
        self.client.timed_out = True
        self.assertEqual(self.handle("release", locker_id="L1")["code"], "ETIMEOUT")

    def test_bad_requests(self):
        self.assertEqual(self.handle("deposit", key="zz", locker_id="L1")["code"], "ESERVICE")
        self.assertEqual(self.agent.handle({"protocol": 2, "op": "status"}, self.uid, 4242)["code"], "ESERVICE")
        self.assertEqual(self.handle("nope")["code"], "ESERVICE")

    def test_socket_round_trip(self):
        sock_path = str(self.agent.proc / "agent.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(sock_path)
        listener.listen(4)
        threading.Thread(target=self.agent.serve, args=(listener,), daemon=True).start()
        me = os.getpid()
        (self.agent.proc / str(me)).mkdir()
        (self.agent.proc / str(me) / "sessionid").write_text("2\n")
        client = agentmod.AgentClient(sock_path, timeout=5)
        self.assertTrue(client.available())
        self.assertEqual(client.status()["held"], False)
        client.deposit(self.key, "L1", ttl=3600)
        self.assertEqual(client.release("L1"), self.key)
        self.client.result = "verify-no-match"
        with self.assertRaises(core.NoMatch):
            client.release("L1")
        self.assertTrue(client.forget())
        with self.assertRaises(core.AccessDenied):
            client.release("L1")
        listener.close()
        with self.assertRaises(agentmod.AgentUnavailable):
            agentmod.AgentClient(str(self.agent.proc / "missing.sock"), timeout=1).status()


class SessionGateTests(unittest.TestCase):
    def test_logind_record_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp)
            (sessions / "7").write_text("UID=1000\nACTIVE=1\nSTATE=active\nSEAT=seat0\n")
            (sessions / "8").write_text("UID=1000\nACTIVE=0\nSTATE=online\nSEAT=\n")
            self.assertTrue(agentmod.session_is_active_seat("7", sessions_dir=sessions)[0])
            self.assertFalse(agentmod.session_is_active_seat("8", sessions_dir=sessions)[0])
            self.assertFalse(agentmod.session_is_active_seat("", sessions_dir=sessions)[0])

            def run(argv, **kwargs):
                return SimpleNamespace(returncode=0, stdout="Active=yes\nSeat=seat0\n")
            self.assertTrue(agentmod.session_is_active_seat("9", run=run, sessions_dir=sessions)[0])


class InstallPlanTests(unittest.TestCase):
    def test_agent_install_is_fixed_root_argvs(self):
        argvs = fl.agent_install_argvs(Path("/src"))
        self.assertEqual(argvs[0][:4], ["install", "-D", "-m", "0644"])
        self.assertIn("/usr/local/lib/kilix-fingerlock/fingerlock_agent.py", argvs[1])
        self.assertEqual(argvs[-1], ["systemctl", "enable", "--now", "kilix-fingerlock-agent.socket"])
        self.assertTrue(all(argv[0] in ("install", "systemctl") for argv in argvs))
        self.assertIn("ProtectHome=yes", agentmod.SERVICE_UNIT_TEXT)
        self.assertIn("SocketMode=0666", agentmod.SOCKET_UNIT_TEXT)


if __name__ == "__main__":
    unittest.main()
