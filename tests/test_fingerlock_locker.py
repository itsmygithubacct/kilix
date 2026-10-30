"""The fingerlock locker and its custodian.

Locker: a passphrase-wrapped seed, a stable X25519 keypair, sealed chunked
containers that fail closed on any byte flip, truncation or chunk reorder, and
a passphrase change that rewraps the same key. Agent: the master key stays in
the process. A verify-match authorizes one unseal of a bound container. Forget
and expiry refuse a later operation. Tests use a fake fprintd client and a
private socket; they do not touch a reader, a D-Bus daemon, apt, or systemd.
"""
import hashlib
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
        self.locker = lockermod.Locker(self.home, kdf_policy="test")
        self.info = self.locker.init("correct horse battery", **FAST)
        self.private = self.locker.derive_private_key("correct horse battery")

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
        header = lockermod.lock_file(src, container, self.info, private_key=self.private,
                                     chunk_size=chunk_size, remove_source=True)
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
        lockermod.lock_file(src, container, self.info, private_key=self.private, chunk_size=chunk_size)
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
        other = lockermod.Locker(Path(self.tmp.name) / "other", kdf_policy="test")
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
        lockermod.Locker(self.home, kdf_policy="test").init("correct horse battery", **FAST)
        # The verb defaults to the production KDF policy. These tests open a
        # FAST locker, so the verb's constructor is pointed at the test policy.
        real = fl.Locker

        def opening(home=None):
            return real(home, kdf_policy="test")

        fl.Locker = opening
        self.addCleanup(setattr, fl, "Locker", real)

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
        self.gate = threading.Event()
        self.gate.set()
        self.entered = threading.Event()
        self.alive = True

    def default_device(self):
        return core.DeviceInfo("/dev/0", "Synaptics Sensors", "press", 9, False, False)

    def verify(self, path, finger, *, username, timeout, **kwargs):
        self.calls.append((finger, username))
        self.entered.set()
        if not self.gate.wait(5):
            session = core.Session("verify", finger, path)
            session.timed_out = True
            return session
        session = core.Session("verify", finger, path)
        if not self.timed_out:
            session.handle(self.result, True)
            session.selected_finger = "right-index-finger"
        session.timed_out = self.timed_out
        return session


def write_cgroup(proc: Path, pid: int, session: str | None) -> None:
    """A cgroup scope is the session id. A sessionid file is an audit id and is ignored."""
    directory = proc / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    if session is None:
        text = "0::/user.slice/user-1.slice/user@1.service\n"
    else:
        text = f"0::/user.slice/user-1.slice/session-{session}.scope\n"
    (directory / "cgroup").write_text(text)
    (directory / "sessionid").write_text("999\n")


@unittest.skipUnless(HAVE_NACL, "python3-nacl is not installed")
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.clock = [1000.0]
        self.custody = agentmod.Custody(now=lambda: self.clock[0], min_ttl=0, start_timer=False)
        self.client = FakeVerifyClient()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.custody.close)
        proc = Path(self.tmp.name) / "proc"
        self.uid = os.getuid()
        self.agent = agentmod.Agent(
            custody=self.custody, client_factory=lambda: self.client,
            session_check=lambda sid: (sid == "2", f"session {sid}"),
            session_owner=lambda sid: self.uid if sid == "2" else None,
            proc=proc)
        write_cgroup(proc, 4242, "2")
        write_cgroup(proc, 4343, "9")
        home = Path(self.tmp.name) / "locker"
        self.locker = lockermod.Locker(home, kdf_policy="test")
        self.info = self.locker.init("correct horse battery", **FAST)
        self.key = self.locker.derive_private_key("correct horse battery")
        self.public = self.info.public_key
        self.payload = b"payload-bytes"
        self.blob = lockermod.encrypt_container(self.payload, self.info, self.public, name="doc.flk")
        self.binding = lockermod.binding_for("unlock", "doc.flk", self.blob)
        self.listener = None

    def handle(self, op, pid=4242, body=b"", **fields):
        reply, plain = self.agent.handle({"protocol": 1, "op": op, **fields}, self.uid, pid, body)
        dumped = json.dumps(reply)
        self.assertNotIn("key", reply)
        self.assertNotIn("private_key", reply)
        self.assertNotIn("seed", reply)
        self.assertNotIn(self.key.hex(), dumped)
        self.assertNotEqual(plain, self.key)
        return reply, plain

    def deposit(self, ttl=3600):
        return self.handle("deposit", key=self.key.hex(), locker_id=self.info.locker_id, ttl=ttl)

    def operate(self, **fields):
        binding = fields.pop("binding", self.binding)
        body = fields.pop("body", self.blob)
        merged = {"locker_id": self.info.locker_id, "finger": "any",
                  "body_bytes": len(body), "binding": binding}
        merged.update(fields)
        return self.handle("operate", body=body, **merged)

    def test_deposit_operate_forget_never_returns_the_key(self):
        status, plain = self.handle("status")
        self.assertFalse(status["held"])
        self.assertEqual(plain, b"")
        self.assertIn("hardening", status)
        reply, _plain = self.deposit()
        self.assertTrue(reply["held"])
        reply, plain = self.operate()
        self.assertTrue(reply["ok"])
        self.assertEqual(plain, self.payload)
        self.assertEqual(reply["binding"], self.binding)
        self.assertEqual(reply["plain_sha256"], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(self.client.calls, [("any", __import__("pwd").getpwuid(self.uid).pw_name)])
        forgotten, _plain = self.handle("forget")
        self.assertTrue(forgotten["forgotten"])
        refused, plain = self.operate()
        self.assertEqual(refused["code"], "EACCESS")
        self.assertEqual(plain, b"")

    def test_release_op_exports_nothing(self):
        self.deposit()
        reply, plain = self.handle("release", locker_id=self.info.locker_id)
        self.assertEqual(reply["code"], "ESERVICE")
        self.assertIn("does not export", reply["error"])
        self.assertEqual(plain, b"")
        self.assertNotIn(self.key.hex(), json.dumps(reply))

    def test_confirm_returns_only_the_public_key(self):
        self.deposit()
        reply, plain = self.handle("confirm", locker_id=self.info.locker_id, public_key=self.public.hex())
        self.assertEqual(reply["public_key"], self.public.hex())
        self.assertNotEqual(reply["public_key"], self.key.hex())
        self.assertEqual(plain, b"")
        refused, _plain = self.handle("confirm", locker_id=self.info.locker_id, public_key=os.urandom(32).hex())
        self.assertEqual(refused["code"], "EACCESS")
        self.assertIn("nothing was sealed", refused["error"])

    def test_ttl_expiry_and_wrong_locker(self):
        self.deposit(ttl=600)
        refused, plain = self.operate(locker_id="OTHER")
        self.assertEqual(refused["code"], "EACCESS")
        self.assertEqual(plain, b"")
        self.clock[0] += 601
        status, _plain = self.handle("status")
        self.assertFalse(status["held"])
        self.assertIsNone(self.custody.peek(self.uid))

    def test_sweep_expires_without_a_peek(self):
        self.deposit(ttl=600)
        self.clock[0] += 601
        self.custody.sweep()
        self.assertIsNone(self.custody.peek(self.uid))

    def test_inactive_session_is_refused_for_key_ops(self):
        reply, _plain = self.handle("deposit", pid=4343, key=self.key.hex(), locker_id=self.info.locker_id)
        self.assertEqual(reply["code"], "EACCESS")
        self.assertIn("active seat", reply["error"])
        status, _plain = self.handle("status", pid=4343)
        self.assertFalse(status["held"])

    def test_audit_sessionid_is_not_a_seat(self):
        write_cgroup(self.agent.proc, 4242, None)
        reply, plain = self.handle("deposit", key=self.key.hex(), locker_id=self.info.locker_id)
        self.assertEqual(reply["code"], "EACCESS")
        self.assertEqual(plain, b"")
        self.assertIsNone(self.custody.peek(self.uid))

    def test_no_match_and_timeout_return_no_plaintext(self):
        self.deposit()
        self.client.result = "verify-no-match"
        reply, plain = self.operate()
        self.assertEqual(reply["code"], "ENOMATCH")
        self.assertEqual(plain, b"")
        self.client.result = "verify-match"
        self.client.timed_out = True
        reply, plain = self.operate()
        self.assertEqual(reply["code"], "ETIMEOUT")
        self.assertEqual(plain, b"")
        status, _plain = self.handle("status")
        self.assertTrue(status["held"])

    def test_bad_requests(self):
        reply, _plain = self.handle("deposit", key="zz", locker_id=self.info.locker_id)
        self.assertEqual(reply["code"], "ESERVICE")
        reply, _plain = self.agent.handle({"protocol": 2, "op": "status"}, self.uid, 4242)
        self.assertEqual(reply["code"], "ESERVICE")
        reply, _plain = self.handle("nope")
        self.assertEqual(reply["code"], "ESERVICE")

    def _during_verify(self, mutate):
        self.deposit()
        self.client.gate.clear()
        self.client.entered.clear()
        box = {}

        def worker():
            self.agent._tls.alive = lambda: self.client.alive
            box["out"] = self.operate()

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(self.client.entered.wait(2))
        mutate()
        self.client.gate.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        return box["out"]

    def test_forget_during_verify_returns_no_plaintext(self):
        reply, plain = self._during_verify(lambda: self.custody.forget(self.uid))
        self.assertEqual(reply["code"], "EACCESS")
        self.assertEqual(plain, b"")
        self.assertFalse(reply["ok"])

    def test_expiry_during_verify_returns_no_plaintext(self):
        def jump():
            self.clock[0] += 10_000
        reply, plain = self._during_verify(jump)
        self.assertEqual(reply["code"], "EACCESS")
        self.assertEqual(plain, b"")

    def test_replacement_during_verify_returns_no_plaintext(self):
        def replace():
            other = os.urandom(32)
            _private, public = lockermod.Locker._keypair(other)
            self.custody.deposit(self.uid, "owner", other, "other-locker", public, 600)
        reply, plain = self._during_verify(replace)
        self.assertEqual(reply["code"], "EACCESS")
        self.assertEqual(plain, b"")

    def test_session_switch_during_verify_returns_no_plaintext(self):
        def switch():
            write_cgroup(self.agent.proc, 4242, "4")
            self.agent.session_check = lambda sid: (sid in ("2", "4"), f"session {sid}")
            self.agent.session_owner = lambda sid: self.uid if sid in ("2", "4") else None
        reply, plain = self._during_verify(switch)
        self.assertEqual(reply["code"], "EACCESS")
        self.assertIn("session changed", reply["error"])
        self.assertEqual(plain, b"")

    def test_lock_during_verify_returns_no_plaintext(self):
        def lock_seat():
            self.agent.session_check = lambda sid: (False, "session locked")
        reply, plain = self._during_verify(lock_seat)
        self.assertEqual(reply["code"], "EACCESS")
        self.assertEqual(plain, b"")

    def test_disconnect_during_verify_returns_no_plaintext(self):
        def drop():
            self.client.alive = False
        reply, plain = self._during_verify(drop)
        self.assertEqual(reply["code"], "EACCESS")
        self.assertIn("disconnected", reply["error"])
        self.assertEqual(plain, b"")

    def test_forget_after_decrypt_discards_plaintext(self):
        self.deposit()
        self.agent.before_commit = lambda: self.custody.forget(self.uid)
        reply, plain = self.operate()
        self.assertEqual(reply["code"], "EACCESS")
        self.assertEqual(plain, b"")

    def test_binding_is_checked_before_verify(self):
        self.deposit()
        bad = dict(self.binding)
        bad["sha256"] = "0" * 64
        reply, plain = self.operate(binding=bad)
        self.assertEqual(reply["code"], "ESERVICE")
        self.assertEqual(plain, b"")
        self.assertEqual(self.client.calls, [])

    def _serve(self):
        sock_path = str(Path(self.tmp.name) / "agent.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(sock_path)
        listener.listen(4)
        thread = threading.Thread(target=self.agent.serve, args=(listener,), daemon=True)
        thread.start()
        self.listener = listener
        self.addCleanup(listener.close)
        self.addCleanup(thread.join, 2)
        write_cgroup(self.agent.proc, os.getpid(), "2")
        return sock_path

    def test_socket_round_trip_returns_plaintext_not_the_key(self):
        sock_path = self._serve()
        client = agentmod.AgentClient(sock_path, timeout=5, trusted_uid=self.uid)
        self.assertEqual(client.trusted_uid, self.uid)
        self.assertFalse(client.status()["held"])
        client.deposit(self.key, self.info.locker_id, ttl=3600)
        self.assertEqual(client.confirm(self.info.locker_id, self.public)["public_key"], self.public.hex())
        self.assertEqual(client.unseal(self.info.locker_id, self.blob, self.binding), self.payload)
        self.client.result = "verify-no-match"
        with self.assertRaises(core.NoMatch):
            client.unseal(self.info.locker_id, self.blob, self.binding)
        with self.assertRaises(core.ServiceFailure) as raised:
            client.call("release", locker_id=self.info.locker_id)
        self.assertIn("does not export", str(raised.exception))
        self.assertTrue(client.forget())
        with self.assertRaises(core.AccessDenied):
            client.unseal(self.info.locker_id, self.blob, self.binding)
        self.listener.close()
        with self.assertRaises(agentmod.AgentUnavailable):
            agentmod.AgentClient(str(Path(self.tmp.name) / "missing.sock"), timeout=1, trusted_uid=self.uid).status()

    def test_untrusted_peer_receives_no_key(self):
        sock_path = str(Path(self.tmp.name) / "fake.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(sock_path)
        listener.listen(1)
        received = []

        def reader():
            conn, _addr = listener.accept()
            conn.settimeout(1)
            try:
                received.append(conn.recv(1024))
            except socket.timeout:
                received.append(b"<timeout>")
            conn.close()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        with self.assertRaises(core.AccessDenied):
            agentmod.AgentClient(sock_path, timeout=2, trusted_uid=0).deposit(self.key, self.info.locker_id)
        thread.join(2)
        listener.close()
        self.assertEqual(received, [b""])

    def test_server_refuses_to_send_a_key_field(self):
        sock_path = self._serve()

        def leak(*_args, **_kwargs):
            return {"ok": True, "key": self.key.hex(), "held": True}, self.key

        self.agent.handle = leak
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(2)
        raw.connect(sock_path)
        raw.sendall(b'{"protocol":1,"op":"status"}\n')
        data = raw.recv(4096)
        raw.close()
        self.assertIn(b"refusing to send the locker key", data)
        self.assertNotIn(self.key.hex().encode(), data)

    def test_malformed_request_and_saturation(self):
        self.agent.max_concurrent = 1
        self.agent.max_per_uid = 1
        self.agent.request_deadline = 0.4
        sock_path = self._serve()
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(2)
        raw.connect(sock_path)
        raw.sendall(b"not-json\n")
        self.assertIn(b"ESERVICE", raw.recv(4096))
        raw.close()
        held = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        held.settimeout(2)
        held.connect(sock_path)
        time.sleep(0.05)
        second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        second.settimeout(2)
        second.connect(sock_path)
        refused = second.recv(4096)
        self.assertIn(b"EBUSY", refused)
        second.close()
        held.close()
        time.sleep(0.05)
        client = agentmod.AgentClient(sock_path, timeout=2, trusted_uid=self.uid)
        self.assertFalse(client.status()["held"])
        slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        slow.settimeout(2)
        slow.connect(sock_path)
        time.sleep(0.15)
        slow.sendall(b"{")
        time.sleep(0.45)
        self.assertIn(b"ETIMEOUT", slow.recv(4096))
        slow.close()

    def test_per_uid_admit_bound(self):
        self.assertIsNone(self.agent._admit(self.uid))
        self.assertIsNone(self.agent._admit(self.uid))
        self.assertIsNotNone(self.agent._admit(self.uid))
        self.agent._release_slot(self.uid)
        self.assertIsNone(self.agent._admit(self.uid))

    def test_secret_buffer_wipe_does_not_clear_other_copies(self):
        buf = agentmod.SecretBuffer(self.key)
        self.assertTrue(buf.error or buf.locked)
        copy = buf.copy()
        buf.wipe()
        self.assertEqual(bytes(buf._buf), b"\x00" * 32)
        self.assertEqual(bytes(copy), self.key)
        self.assertFalse(buf.locked)

    def test_idle_timer_expires_custody(self):
        ticks = [0.0]
        custody = agentmod.Custody(now=lambda: ticks[0], min_ttl=0, start_timer=True, sweep_interval=0.05)
        self.addCleanup(custody.close)
        custody.deposit(self.uid, "owner", self.key, self.info.locker_id, self.public, 1)
        ticks[0] = 10
        deadline = time.monotonic() + 2
        while custody.peek(self.uid) is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNone(custody.peek(self.uid))


class SessionGateTests(unittest.TestCase):
    def test_active_seat_requires_owner_seat_and_unlocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = Path(tmp)
            (sessions / "7").write_text("UID=1000\nACTIVE=1\nSTATE=active\nSEAT=seat0\n")
            (sessions / "8").write_text("UID=1000\nACTIVE=0\nSTATE=online\nSEAT=\n")
            (sessions / "1").write_text("UID=1000\nACTIVE=1\nSTATE=active\nSEAT=\n")
            (sessions / "5").write_text("UID=1000\nACTIVE=1\nSTATE=active\nSEAT=seat0\nLOCKED_HINT=1\n")
            self.assertTrue(agentmod.session_is_active_seat("7", sessions_dir=sessions)[0])
            self.assertFalse(agentmod.session_is_active_seat("8", sessions_dir=sessions)[0])
            self.assertFalse(agentmod.session_is_active_seat("1", sessions_dir=sessions)[0])
            locked_ok, locked_why = agentmod.session_is_active_seat("5", sessions_dir=sessions)
            self.assertFalse(locked_ok)
            self.assertIn("locked", locked_why)
            self.assertFalse(agentmod.session_is_active_seat("", sessions_dir=sessions)[0])
            self.assertEqual(agentmod.session_owner_uid("7", sessions_dir=sessions), 1000)
            self.assertIsNone(agentmod.session_owner_uid("missing", sessions_dir=sessions))

            def run(argv, **kwargs):
                return SimpleNamespace(returncode=0, stdout="Active=yes\nState=active\nSeat=seat0\nLockedHint=no\n")
            self.assertTrue(agentmod.session_is_active_seat("9", run=run, sessions_dir=sessions)[0])

            def locked(argv, **kwargs):
                return SimpleNamespace(returncode=0, stdout="Active=yes\nState=active\nSeat=seat0\nLockedHint=yes\n")
            self.assertFalse(agentmod.session_is_active_seat("10", run=locked, sessions_dir=sessions)[0])

    def test_cgroup_scope_ignores_sessionid(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            write_cgroup(proc, 42, None)
            self.assertEqual(agentmod.session_of_pid(42, proc=proc), "")
            write_cgroup(proc, 42, "3")
            self.assertEqual((proc / "42" / "sessionid").read_text().strip(), "999")
            self.assertEqual(agentmod.session_of_pid(42, proc=proc), "3")
            self.assertEqual(agentmod.session_of_pid(True, proc=proc), "")


class CustodyDocsTests(unittest.TestCase):
    def test_docs_state_the_custody_limits(self):
        agent = " ".join((ROOT / "config" / "fingerlock_agent.py").read_text().split())
        locker = " ".join((ROOT / "config" / "fingerlock_locker.py").read_text().split())
        verb = " ".join((ROOT / "config" / "fingerlock.py").read_text().split())
        for phrase in (
            "does not export the master private key",
            "one bounded operation",
            "Forget and expiry refuse a later operation",
            "Plaintext already returned stays",
            "does not rotate the key",
            "does not revoke plaintext already returned",
            "cannot guarantee erasure of every copy",
            "mlock",
            "Swap and hibernation are not a secrecy boundary",
            "a Python assignment is not evidence of erasure",
            "session-<id>.scope",
            "monotonic deadline",
            "ACTIVE alone is not enough",
            "unlocked",
        ):
            self.assertIn(phrase, agent, phrase)
        for phrase in (
            "does not rotate the key",
            "does not revoke plaintext already returned",
            "not a streaming decrypt",
            "not secure deletion",
            "not a trusted recipient by itself",
        ):
            self.assertIn(phrase, locker, phrase)
        self.assertIn("it is not a screen-lock or login token", verb)
        self.assertNotIn("os.open(", verb)
        self.assertNotIn("trusted_uid=", verb)
        self.assertIn('if session.result != "verify-match":', agent)


@unittest.skipUnless(HAVE_NACL, "python3-nacl is not installed")
class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "locker"
        self.locker = lockermod.Locker(self.home, kdf_policy="test")
        self.info = self.locker.init("correct horse battery", **FAST)
        self.private = self.locker.derive_private_key("correct horse battery")
        self.src = Path(self.tmp.name) / "photo.jpg"
        self.src.write_bytes(b"picture")
        self.dest = lockermod.container_for(self.src)

    def test_unverified_and_tampered_recipient_preserve_the_source(self):
        with self.assertRaises(lockermod.LockerError):
            lockermod.lock_file(self.src, self.dest, self.info)
        self.assertEqual(self.src.read_bytes(), b"picture")
        self.assertFalse(self.dest.exists())
        raw = json.loads(self.locker.path.read_text())
        import nacl.public
        raw["public_key"] = bytes(nacl.public.PrivateKey.generate().public_key).hex()
        raw["locker_id"] = "attacker"
        self.locker.path.write_text(json.dumps(raw))
        tampered = self.locker.info()
        with self.assertRaises(lockermod.LockerError):
            self.locker.derive_private_key("correct horse battery")
        with self.assertRaises(lockermod.LockerError):
            lockermod.lock_file(self.src, self.dest, tampered, private_key=self.private)
        self.assertEqual(self.src.read_bytes(), b"picture")
        self.assertFalse(self.dest.exists())
        with self.assertRaises(lockermod.LockerError):
            lockermod.lock_file(self.src, self.dest, tampered, confirmed_public=self.info.public_key)
        self.assertFalse(self.dest.exists())

    def test_symlink_source_and_stale_part_and_existing_dest(self):
        secret = Path(self.tmp.name) / "secret"
        secret.write_bytes(b"do-not-touch")
        link = Path(self.tmp.name) / "link.jpg"
        link.symlink_to(self.src)
        with self.assertRaises(lockermod.LockerError):
            lockermod.lock_file(link, self.dest, self.info, private_key=self.private)
        self.assertFalse(self.dest.exists())
        self.assertEqual(secret.read_bytes(), b"do-not-touch")
        part = self.dest.parent / f".{self.dest.name}.part"
        part.symlink_to(secret)
        other = Path(self.tmp.name) / "other.jpg"
        other.write_bytes(b"other")
        container = lockermod.container_for(other)
        lockermod.lock_file(other, container, self.info, private_key=self.private)
        self.assertEqual(secret.read_bytes(), b"do-not-touch")
        self.assertTrue(part.is_symlink())
        self.dest.symlink_to(secret)
        with self.assertRaises(lockermod.LockerError):
            lockermod.lock_file(self.src, self.dest, self.info, private_key=self.private)
        self.assertEqual(secret.read_bytes(), b"do-not-touch")

    def test_fsync_failure_and_source_replacement(self):
        def boom(_fd):
            raise OSError("fsync failed")

        with unittest.mock.patch.object(lockermod.os, "fsync", boom):
            with self.assertRaises(OSError):
                lockermod.lock_file(self.src, self.dest, self.info, private_key=self.private, remove_source=True)
        self.assertEqual(self.src.read_bytes(), b"picture")
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.src.parent.glob(".flk-*")), [])
        replacement = {}

        def swap():
            self.src.unlink()
            self.src.write_bytes(b"replacement")
            replacement["ino"] = self.src.stat().st_ino

        with self.assertRaises(lockermod.LockerError):
            lockermod.lock_file(self.src, lockermod.container_for(Path(self.tmp.name) / "fresh.jpg"), self.info,
                                private_key=self.private, remove_source=True, before_commit=swap)
        self.assertEqual(self.src.read_bytes(), b"replacement")
        self.assertEqual(self.src.stat().st_ino, replacement["ino"])

    def test_password_replace_failure_keeps_the_old_entry(self):
        self.locker.store_password("wifi", b"old-secret", private_key=self.private)
        decoy = self.locker.passwords_dir / ".flk-decoy"
        decoy.write_bytes(b"leave-me")
        def boom(_fd):
            raise OSError("fsync failed")

        with unittest.mock.patch.object(lockermod.os, "fsync", boom):
            with self.assertRaises(OSError):
                self.locker.store_password("wifi", b"new-secret", replace=True, private_key=self.private)
        _header, plain = lockermod.unlock_bytes(self.locker.password_path("wifi").read_bytes(), self.private)
        self.assertEqual(plain, b"old-secret")
        self.assertEqual(decoy.read_bytes(), b"leave-me")
        self.assertEqual(list(self.locker.passwords_dir.glob(".flk-*")), [decoy])
        self.assertEqual(list(self.locker.passwords_dir.glob("*.plain-*")), [])
        self.locker.store_password("wifi", b"new-secret", replace=True, private_key=self.private)
        _header, plain = lockermod.unlock_bytes(self.locker.password_path("wifi").read_bytes(), self.private)
        self.assertEqual(plain, b"new-secret")

    def test_kdf_and_chunk_bounds(self):
        import nacl.pwhash
        argon = nacl.pwhash.argon2id
        lockermod.validate_kdf(int(argon.OPSLIMIT_INTERACTIVE), int(argon.MEMLIMIT_INTERACTIVE), policy="production")
        with self.assertRaises(lockermod.LockerError):
            lockermod.validate_kdf(1, 8192, policy="production")
        with self.assertRaises(lockermod.LockerError):
            lockermod.validate_kdf(True, int(argon.MEMLIMIT_INTERACTIVE), policy="production")
        with self.assertRaises(lockermod.LockerError):
            lockermod.validate_kdf(int(argon.OPSLIMIT_INTERACTIVE), 10**18, policy="production")
        lockermod.validate_kdf(1, 8192, policy="test")
        with self.assertRaises(lockermod.LockerError):
            lockermod.validate_kdf(10**9, 8192, policy="test")
        data = json.loads(self.locker.path.read_text())
        with self.assertRaises(lockermod.LockerError):
            lockermod.LockerInfo.from_dict(data, policy="production")
        self.assertEqual(lockermod.LockerInfo.from_dict(data, policy="test").locker_id, self.info.locker_id)
        for bad in (-1, True, "1024", 2**32):
            with self.assertRaises(lockermod.LockerError):
                lockermod.lock_file(self.src, self.dest, self.info, private_key=self.private, chunk_size=bad)
            self.assertEqual(self.src.read_bytes(), b"picture")
            self.assertFalse(self.dest.exists())
        header = {"version": 1, "chunk_size": -1, "size": 4, "nonce": "ab" * 24,
                  "sealed_key": "cd" * 80, "name": "x", "locker_id": "abc"}
        path = Path(self.tmp.name) / "bad.flk"
        path.write_bytes(b"FLK1 " + json.dumps(header).encode() + b"\n" + b"yyyy")
        reads = []
        real_read = os.read

        def wrapped(fd, count):
            if count < 0:
                raise AssertionError("negative read")
            reads.append(count)
            return real_read(fd, count)

        with unittest.mock.patch.object(lockermod.os, "read", wrapped):
            with self.assertRaises(lockermod.LockerError):
                lockermod.read_header(path)
        self.assertTrue(all(count > 0 for count in reads))
        self.assertEqual(path.read_bytes()[:5], b"FLK1 ")

    def test_last_chunk_corruption_writes_nothing(self):
        payload = os.urandom(2500)
        self.src.write_bytes(payload)
        lockermod.lock_file(self.src, self.dest, self.info, private_key=self.private, chunk_size=1024)
        original = bytearray(self.dest.read_bytes())
        original[-1] ^= 0x01
        self.dest.write_bytes(original)
        sink = io.BytesIO()
        with self.assertRaises(lockermod.LockerError):
            lockermod.unlock_file(self.dest, None, self.private, sink=sink)
        self.assertEqual(sink.getvalue(), b"")
        out = Path(self.tmp.name) / "out.jpg"
        with self.assertRaises(lockermod.LockerError):
            lockermod.unlock_file(self.dest, out, self.private)
        self.assertFalse(out.exists())
        self.assertTrue(self.dest.exists())

    def test_hardlink_unlink_leaves_the_other_name(self):
        alias = Path(self.tmp.name) / "alias.jpg"
        os.link(self.src, alias)
        lockermod.lock_file(self.src, self.dest, self.info, private_key=self.private, remove_source=True)
        self.assertFalse(self.src.exists())
        self.assertEqual(alias.read_bytes(), b"picture")
        sink = io.BytesIO()
        lockermod.unlock_file(self.dest, None, self.private, sink=sink)
        self.assertEqual(sink.getvalue(), b"picture")

    def test_one_destination_accepts_one_writer(self):
        left = Path(self.tmp.name) / "left.bin"
        right = Path(self.tmp.name) / "right.bin"
        left.write_bytes(b"left-payload")
        right.write_bytes(b"right-payload")
        dest = Path(self.tmp.name) / "same.flk"
        errors = []

        def seal(source):
            try:
                lockermod.lock_file(source, dest, self.info, private_key=self.private, remove_source=True)
            except lockermod.LockerError:
                errors.append(source)

        threads = [threading.Thread(target=seal, args=(left,)), threading.Thread(target=seal, args=(right,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].exists())
        winner = right if errors[0] == left else left
        self.assertFalse(winner.exists())
        sink = io.BytesIO()
        lockermod.unlock_file(dest, None, self.private, sink=sink)
        self.assertIn(sink.getvalue(), (b"left-payload", b"right-payload"))

    def test_concurrent_locks_do_not_clobber(self):
        left = Path(self.tmp.name) / "left.bin"
        right = Path(self.tmp.name) / "right.bin"
        left.write_bytes(b"left-payload")
        right.write_bytes(b"right-payload")
        errors = []

        def seal(source):
            try:
                lockermod.lock_file(source, lockermod.container_for(source), self.info,
                                    private_key=self.private, remove_source=True)
            except lockermod.LockerError as exc:
                errors.append((source.name, str(exc)))

        threads = [threading.Thread(target=seal, args=(left,)), threading.Thread(target=seal, args=(right,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertFalse(left.exists())
        self.assertFalse(right.exists())
        for source, payload in ((left, b"left-payload"), (right, b"right-payload")):
            container = lockermod.container_for(source)
            sink = io.BytesIO()
            lockermod.unlock_file(container, None, self.private, sink=sink)
            self.assertEqual(sink.getvalue(), payload)


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
