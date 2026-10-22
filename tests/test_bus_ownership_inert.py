"""Source-bound inert fixtures. Never starts a service or uses a native handle.

Run with a source root and disposable packet directory. Imports only the two
stdlib-only ownership modules; extracts unchanged selected XApp AST nodes.
The runner installs native-operation denials before importing candidate code.
Before/after and mutation results describe this model, not Linux execution.
"""
import ast
import errno
import builtins
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import types
import unittest


class NativeOperationForbidden(BaseException):
    pass


def deny(*_args, **_kwargs):
    raise NativeOperationForbidden("native operation forbidden in inert validation")


def install_denials():
    real_stat = os.stat
    def inert_stat(path, *args, **kwargs):
        if isinstance(path, int):
            deny()
        value = os.fsdecode(path)
        if value == "/proc" or value == "/sys" or value.startswith(("/proc/", "/sys/", "/run/dbus", "/run/user", "/run/systemd")):
            deny()
        return real_stat(path, *args, **kwargs)
    os.stat = inert_stat
    for owner, names in ((subprocess, ("Popen", "run", "call", "check_call", "check_output")),
                         (os, ("kill", "killpg", "pidfd_open", "fork", "forkpty", "posix_spawn",
                               "posix_spawnp", "waitpid", "waitid", "system", "open", "close", "dup", "dup2",
                               "read", "write", "lseek", "fstat")),
                         (signal, ("pidfd_send_signal", "signal")),
                         (socket, ("socket", "socketpair", "create_connection", "fromfd")),
                         (select, ("select",)), (ctypes, ("CDLL",)),
                         (threading.Thread, ("start",)), (time, ("sleep",))):
        for name in names:
            if hasattr(owner, name):
                setattr(owner, name, deny)
    def audit(event, args):
        if event.startswith(("subprocess.", "socket.", "os.exec", "os.spawn", "os.kill", "ctypes.dlopen")):
            deny()
        if event in ("open", "os.listdir", "os.scandir", "os.mkdir", "os.rmdir", "os.remove", "os.rename") and args:
            path = args[0]
            if isinstance(path, (str, bytes, os.PathLike)):
                path = os.fsdecode(path)
                if path == "/proc" or path.startswith("/proc/") or path.startswith("/sys/"):
                    deny()
    sys.addaudithook(audit)


def digest(data):
    return hashlib.sha256(data).hexdigest()


BINDINGS = []


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    BINDINGS.append({"path": str(path), "sha256": digest(path.read_bytes()), "mode": "full-stdlib-module"})
    return module


def load_source(root, label, baseline=False):
    config = root / "config/kilix_sdk"
    if baseline:
        bus = load_module(label + "_bus", config / "private_bus.py")
        ownership = None
    else:
        package = types.ModuleType(label)
        package.__path__ = [str(config)]
        sys.modules[label] = package
        ownership = load_module(label + ".bus_ownership", config / "bus_ownership.py")
        platform = load_module(label + ".bus_platform", config / "bus_platform.py") if (config / "bus_platform.py").exists() else None
        for name in ("bus_supervision", "process_pin", "bus_supervisor"):
            path = config / (name + ".py")
            if path.exists(): load_module(label + "." + name, path)
        bus = load_module(label + ".private_bus", config / "private_bus.py")
    path = config / "xapp.py"
    source = ast.parse(path.read_text(), filename=str(path))
    selected = {"__init__", "stop_capture", "release_input", "close", "_close_resources", "_cleanup_supervisor", "_require_open",
                "start_xvfb", "start_xvnc", "connect", "start_window_manager", "launch_app", "start_clipboard",
                "make_injector", "start_capture"}
    nodes = []
    originals = {}
    for node in source.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in ("_stop_process", "SessionCleanupError"):
            nodes.append(node)
            originals[node.name] = ast.dump(node, include_attributes=True)
        if isinstance(node, ast.ClassDef) and node.name == "XAppSession":
            methods = [n for n in node.body if isinstance(n, ast.FunctionDef) and n.name in selected]
            for n in methods:
                originals[n.name] = ast.dump(n, include_attributes=True)
            nodes.append(ast.ClassDef(name=node.name, bases=node.bases, keywords=node.keywords,
                                     body=methods, decorator_list=node.decorator_list))
    extracted = ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[]))
    actual = {}
    for n in nodes:
        if isinstance(n, ast.ClassDef) and n.name == "XAppSession":
            actual.update({m.name: ast.dump(m, include_attributes=True) for m in n.body})
        else:
            actual[n.name] = ast.dump(n, include_attributes=True)
    if originals != actual:
        raise AssertionError("XApp extraction changed a production AST node")
    namespace = {"__name__": label + "_xapp", "subprocess": subprocess, "stream": types.SimpleNamespace(),
                 "time": time, "Lock": threading.Lock, "RLock": threading.RLock, "get_ident": threading.get_ident}
    exec(compile(extracted, str(path), "exec"), namespace)
    if namespace["XAppSession"].close.__code__.co_filename != str(path):
        raise AssertionError("lost production filename binding")
    BINDINGS.append({"path": str(path), "sha256": digest(path.read_bytes()), "mode": "exact-AST-selection",
                     "nodes": {k: digest(v.encode()) for k, v in originals.items()},
                     "excluded": "all native imports and other XApp methods"})
    return types.SimpleNamespace(bus=bus, own=ownership, xapp=types.SimpleNamespace(**namespace), root=root)


@dataclass(frozen=True)
class FD:
    # Non-integer handles cannot reach a native pidfd API accidentally.
    identity: str
    serial: int = 0


@dataclass
class Node:
    identity: str
    pid: int
    parent: str
    scope: str = "inner"
    instance: str = "A"
    live: bool = True
    reaped: bool = False
    resistant: bool = True
    adoption: float | None = None
    status: int | None = None


class Model:
    """Independent graph model: live parents hide children until delayed adoption.

    Kernel culling is modeled separately from direct-child pidfd accounting.
    This does not implement or validate the kernel itself.
    """
    def __init__(self):
        self.t = 0.0
        self.nodes = {}
        self.signals, self.closed, self.waited = [], [], []
        self.counts, self.costs, self.failures = {}, {}, {}
        self.culls = []
        self.adoption_delay = .03
        self.late = []

    def node(self, name, parent="owner", **kw):
        node = Node(name, 1_000_000_000 + len(self.nodes), parent, **kw)
        self.nodes[name] = node
        return FD(name)

    def chain(self, levels=12):
        parent = "owner"
        for i in range(levels):
            name = f"chain-{i}"
            self.node(name, parent)
            parent = name

    def phase(self, name):
        self.counts[name] = self.counts.get(name, 0) + 1
        if sum(self.counts.values()) > 50000:
            raise NativeOperationForbidden("inert operation budget exceeded")
        key = (name, self.counts[name])
        self.advance(self.costs.pop(key, 0))
        error = self.failures.pop(key, None)
        if error:
            raise error

    def advance(self, delta):
        self.t += max(0, delta)
        for node in self.nodes.values():
            if node.adoption is not None and node.adoption <= self.t:
                node.parent, node.adoption = "owner", None
        for at, callback in list(self.late):
            if at <= self.t:
                self.late.remove((at, callback))
                callback()

    def exit(self, node, status=-9):
        if not node.live:
            return
        node.live, node.status = False, status
        for child in self.nodes.values():
            if child.parent == node.identity:
                child.adoption = self.t + self.adoption_delay

    def now(self):
        return self.t

    def sleep(self, delay):
        self.advance(delay)

    def ready(self, fd, timeout=0):
        self.phase("ready")
        if fd is None:
            return True
        if fd in self.closed:
            raise AssertionError("use after inert FD close")
        node = self.nodes[fd.identity]
        if node.live and timeout:
            self.advance(timeout)
        return not node.live

    def signal(self, fd, sig):
        self.phase("signal")
        if fd is None:
            raise AssertionError("missing FD is not signal authority")
        if fd in self.closed:
            raise AssertionError("signal after inert FD close")
        self.signals.append((fd, sig))
        node = self.nodes[fd.identity]
        if sig == signal.SIGKILL:
            self.exit(node)
        elif not node.resistant:
            self.exit(node, 0)  # Explicitly cooperative exit in this model.

    def children(self):
        self.phase("children")
        return {n.pid for n in self.nodes.values() if n.parent == "owner" and not n.reaped}

    def acquire(self, pid):
        self.phase("acquire")
        for n in self.nodes.values():
            if n.pid == pid and n.parent == "owner" and not n.reaped:
                return FD(n.identity)
        return None

    def reap(self, pid):
        self.phase("reap")
        node = next(n for n in self.nodes.values() if n.pid == pid)
        if node.live:
            return False
        node.reaped = True
        self.waited.append(node.identity)
        return True

    def empty_children(self):
        self.phase("child-empty")
        # Independent adoption queue: exited descendants still awaiting
        # reparenting prevent completion even during a direct-list gap.
        return not any(n.instance == "A" and n.scope in ("inner", "outer") and not n.reaped
                       for n in self.nodes.values())

    def close(self, fd):
        self.phase("fd-close")
        if fd in self.closed:
            raise AssertionError("duplicate inert FD close")
        self.closed.append(fd)

    def status(self, process):
        self.phase("status")
        return process.status

    def pidfd_pid(self, fd):
        return self.nodes[fd.identity].pid if not self.nodes[fd.identity].reaped else -1

    def reaped(self, fd):
        return fd is None or self.nodes[fd.identity].reaped

    def wait_exact(self, fd):
        node = self.nodes[fd.identity]
        if node.live: return None
        if not node.reaped:
            node.reaped = True
            self.waited.append(node.identity)
        return types.SimpleNamespace(si_code=os.CLD_EXITED if node.status >= 0 else os.CLD_KILLED,
                                     si_status=abs(node.status))

    def fstat(self, fd):
        if fd in self.closed: raise OSError(errno.EBADF, "inert descriptor released")
        return types.SimpleNamespace(st_dev=11, st_ino=sum((i+1)*ord(c) for i,c in enumerate(fd.identity)), st_mode=0o100600)

    def survivors(self, instance="A", scopes=("inner", "outer")):
        return {n.identity for n in self.nodes.values() if n.live and n.instance == instance and n.scope in scopes}


class Boundary:
    def __init__(self, model, instance="A", *, outer=False):
        self.model, self.instance, self.outer = model, instance, outer
        self.identity = (11, 101, 11, 102) if instance == "A" else (11, 201, 11, 202)
        self.retired = False
        self.empty_hook = None

    def empty(self):
        self.model.phase("empty")
        result = not self.model.survivors(self.instance, ("inner", "outer") if self.outer else ("inner",))
        if self.empty_hook:
            self.empty_hook()
            self.empty_hook = None
        return result

    def kill(self):
        self.model.phase("cull")
        self.model.culls.append((self.instance, self.outer))
        for n in list(self.model.nodes.values()):
            if n.instance == self.instance and n.scope in (("inner", "outer") if self.outer else ("inner",)):
                self.model.exit(n)

    def retire(self):
        self.model.phase("retire")
        if not self.empty():
            raise RuntimeError("cannot retire live boundary")
        self.retired = True


class Transport:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def receive(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def close(self):
        self.closed = True


class Resource:
    def __init__(self, name, calls, fail=False):
        self.name, self.calls, self.fail = name, calls, fail
        self.closed = False

    def close(self):
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError("inert failure " + self.name)
        self.closed = True

    release_all = close


class Process:
    def __init__(self, calls, name, fail=False):
        self.calls, self.name, self.fail = calls, name, fail
        self.dead = False
        self.stdin = self.stdout = self.stderr = None

    def poll(self):
        return 0 if self.dead else None

    def terminate(self):
        self.calls.append(self.name)
        if not self.fail:
            self.dead = True

    def kill(self):
        if not self.fail:
            self.dead = True

    def wait(self, timeout=0):
        if self.fail:
            raise subprocess.TimeoutExpired("inert", timeout)
        return 0


class Supervisor(Resource):
    def __init__(self, calls, fail=False):
        super().__init__("supervisor", calls, fail)
        self.children = []

    def cleanup(self):
        self.close()
        self.children.clear()


def session(source, failures=()):
    calls = []
    supervisor = Supervisor(calls, "supervisor" in failures)
    value = source.xapp.XAppSession("inert-session", 10, 10, supervisor=supervisor)
    for attr, name in (("_app_bus", "bus"), ("_capture_publication", "publication"),
                       ("injector", "input"), ("capture", "capture"), ("xd", "X-connection")):
        setattr(value, attr, Resource(name, calls, name in failures))
    value.capture_process = Process(calls, "capture-process", "capture-process" in failures)
    value.clipboard_process = Process(calls, "clipboard", "clipboard" in failures)
    return value, calls


def sdk(source, *, status=0, payload=None, owner_live=False, primary_live=False, child_live=False):
    model = Model()
    owner = model.node("cleanup-owner", "sdk", scope="outer", live=owner_live, status=status)
    primary = model.node("primary", "sdk", scope="primary", live=primary_live)
    model.node("unrelated", "sdk", scope="other")
    model.node("sibling-service", "sibling-owner", instance="B")
    if child_live:
        model.chain()
    boundary = Boundary(model, outer=True)
    if payload is None:
        payload = json.dumps(source.own.completion("A", boundary, True, complete=True)).encode()
    process = model.nodes[owner.identity]
    state = source.own.SDKCleanup("A", boundary, model, process, owner, Transport(payload))
    state.primary = primary
    return state, model, boundary


@unittest.skipUnless(__name__ == "__main__", "requires the dedicated env-i inert runner")
class Baseline(unittest.TestCase):
    source = None

    def test_live_chain_requires_complete_accounting(self):
        source = self.source
        model = Model()
        model.chain()
        module = source.bus
        class FakePath:
            def __init__(self, value): self.value = str(value)
            def iterdir(self): return [FakePath("task")]
            def __truediv__(self, value): return FakePath(self.value + "/" + value)
            def read_text(self):
                if self.value.endswith("children"):
                    return " ".join(map(str, model.children()))
                pid = int(self.value.split("/")[2])
                return f"{pid} (inert) S 900000001 0"
        fake_os = types.SimpleNamespace(pidfd_open=model.acquire, getpid=lambda: 900000001,
                                        close=model.close, waitpid=lambda pid, _flags: (pid if model.reap(pid) else 0, 0),
                                        WNOHANG=1)
        fake_select = types.SimpleNamespace(select=lambda fds, _w, _e, timeout: (
            [fd for fd in fds if model.ready(fd, timeout)], [], []))
        with replace(module, Path=FakePath, os=fake_os, select=fake_select,
                     time=types.SimpleNamespace(monotonic=model.now, sleep=model.sleep), signal_owned=model.signal):
            module.reap_bus_children()
        self.assertEqual(model.survivors(), set(), "S1: normal return abandoned live descendants")

    def test_abnormal_owner_exit_requires_failure(self):
        value = self.source.bus.PrivateBus.__new__(self.source.bus.PrivateBus)
        value.closed = False
        value.app_fd = value.gate_write = value.control = value.pidfd = None
        value.owner_pidfd = FD("dead-owner")
        value.owner_process = types.SimpleNamespace(wait=lambda **_kw: -9)
        with replace(self.source.bus, signal_owned=lambda *_a: None,
                     os=types.SimpleNamespace(close=lambda *_a: None)):
            with self.assertRaises(RuntimeError, msg="S2: signal exit accepted without completion"):
                value.close()

    def test_bus_error_does_not_skip_independent_cleanup(self):
        value, calls = session(self.source, ("bus",))
        try:
            value.close()
        except RuntimeError:
            pass
        self.assertEqual(set(calls), {"bus", "publication", "input", "capture", "capture-process",
                                     "clipboard", "X-connection", "supervisor"},
                         "S2: bus error skipped independent cleanup")


@contextmanager
def replace(module, **values):
    previous = {k: getattr(module, k) for k in values}
    try:
        for key, value in values.items(): setattr(module, key, value)
        yield
    finally:
        for key, value in previous.items(): setattr(module, key, value)


@unittest.skipUnless(__name__ == "__main__", "requires the dedicated env-i inert runner")
class Candidate(unittest.TestCase):
    source = None

    def test_chain_all_levels_and_delayed_adoption(self):
        model = Model()
        model.chain()
        boundary = Boundary(model)
        drain = self.source.own.DescendantDrain(boundary, model)
        drain.run(deadline=1.5)
        self.assertEqual(model.survivors(), set())
        self.assertEqual(set(model.waited), set(model.nodes))
        self.assertFalse(drain.owned)
        self.assertLess(model.t, 1.5)
        self.assertTrue(model.culls)

    def test_primary_and_unrelated_remain_distinct(self):
        model = Model()
        model.chain()
        primary = model.node("primary", "sdk", scope="primary")
        model.node("unrelated", "sdk", scope="other")
        model.node("sibling", "sibling-owner", instance="B")
        drain = self.source.own.DescendantDrain(Boundary(model), model)
        drain.run(deadline=1.5)
        self.assertTrue(model.nodes[primary.identity].live)
        self.assertTrue(model.nodes["unrelated"].live)
        self.assertTrue(model.nodes["sibling"].live)
        self.assertFalse(any(fd.identity in ("primary", "unrelated", "sibling") for fd, _sig in model.signals))

    def test_late_child_after_empty_probe(self):
        model = Model()
        boundary = Boundary(model)
        boundary.empty_hook = lambda: model.node("late")
        drain = self.source.own.DescendantDrain(boundary, model)
        drain.run(deadline=1.5)
        self.assertFalse(model.nodes["late"].live)
        self.assertTrue(model.nodes["late"].reaped)

    def test_late_adoption_retained_until_waited(self):
        model = Model()
        model.chain(2)
        model.adoption_delay = .6
        drain = self.source.own.DescendantDrain(Boundary(model), model)
        drain.run(deadline=1.5)
        self.assertEqual(set(model.waited), {"chain-0", "chain-1"})

    def test_expiry_each_drain_phase(self):
        for phase in ("children", "acquire", "signal", "ready", "reap", "empty", "child-empty", "cull", "fd-close"):
            with self.subTest(phase=phase):
                model = Model()
                # Make readiness/reap/fd-close reachable without kernel killing.
                model.node("child", live=phase not in ("reap", "fd-close"))
                model.costs[(phase, 1)] = 2
                drain = self.source.own.DescendantDrain(Boundary(model), model)
                with self.assertRaises(self.source.own.CleanupIncomplete):
                    drain.run(deadline=1.5)
                self.assertTrue(model.culls, "expiry must preserve finite boundary action")

    def test_expired_empty_is_incomplete(self):
        model = Model()
        model.costs[("children", 2)] = 2
        drain = self.source.own.DescendantDrain(Boundary(model), model)
        with self.assertRaises(self.source.own.CleanupIncomplete):
            drain.run(deadline=1.5)

    def test_pending_fd_is_retained_and_retry_finishes(self):
        model = Model()
        model.node("child")
        model.failures[("reap", 1)] = RuntimeError("inert wait failure")
        drain = self.source.own.DescendantDrain(Boundary(model), model)
        with self.assertRaises(self.source.own.CleanupIncomplete):
            drain.run(deadline=1.5)
        self.assertEqual(list(drain.owned.values()), [FD("child")])
        self.assertNotIn(FD("child"), model.closed)
        drain.run(deadline=model.t + 1.5)
        self.assertFalse(drain.owned)
        self.assertEqual(model.closed.count(FD("child")), 1)

    def test_released_descriptor_error_is_not_retained_authority(self):
        model = Model()
        model.node("child", live=False)
        original_close = model.close
        def late_close_error(fd):
            original_close(fd)
            raise self.source.own.DescriptorReleased("inert late close error")
        model.close = late_close_error
        drain = self.source.own.DescendantDrain(Boundary(model), model)
        with self.assertRaises(self.source.own.CleanupIncomplete):
            drain.run(deadline=1.5)
        self.assertFalse(drain.owned, "released descriptor must never authorize a later signal")
        self.assertEqual(model.closed, [FD("child")])
        model.close = original_close
        drain.run(deadline=model.t + 1.5)

    def test_live_boundary_prevents_empty_direct_child_success(self):
        model = Model()
        model.node("hidden", "still-parented")
        boundary = Boundary(model)
        # Independent kernel membership evidence is stronger than the owner's
        # direct wait set. Model an unknown member with no current wait target.
        model.empty_children = lambda: True
        # Kernel adapter cannot complete: retain instance, report deadline.
        boundary.kill = lambda: model.culls.append(("A", False))
        drain = self.source.own.DescendantDrain(boundary, model)
        with self.assertRaises(self.source.own.CleanupIncomplete):
            drain.run(deadline=.1)
        self.assertTrue(model.nodes["hidden"].live)

    def test_foreign_adoption_candidate_is_not_signal_authority(self):
        model = Model()
        pid = model.node("foreign", "sdk", scope="other")
        number = model.nodes[pid.identity].pid
        model.children = lambda: {number} if model.t < .03 else set()
        drain = self.source.own.DescendantDrain(Boundary(model), model)
        drain.run(deadline=.1)
        self.assertEqual(model.signals, [])
        self.assertTrue(model.nodes["foreign"].live)

    def test_kernel_error_is_not_empty_proof(self):
        for phase in ("children", "acquire", "empty", "cull", "reap", "fd-close"):
            with self.subTest(phase=phase):
                model = Model()
                model.node("child", live=phase not in ("reap", "fd-close"))
                model.failures[(phase, 1)] = PermissionError("inert adapter failure")
                drain = self.source.own.DescendantDrain(Boundary(model), model)
                with self.assertRaises(self.source.own.CleanupIncomplete):
                    drain.run(deadline=1.5)

    def test_owner_primary_failure_still_drains(self):
        model = Model()
        primary = model.node("primary", "sdk", scope="primary")
        model.chain()
        model.failures[("signal", 1)] = PermissionError("inert primary stop failure")
        cleanup = self.source.own.OwnerCleanup("A", Boundary(model), model)
        cleanup.primary = primary
        with self.assertRaises(self.source.own.CleanupIncomplete):
            cleanup.attempt()
        self.assertFalse(model.survivors())
        self.assertEqual(len(model.waited), 12)
        self.assertTrue(model.nodes["primary"].live)
        receipt = cleanup.attempt()
        self.assertEqual(receipt["state"], "complete")
        self.assertFalse(model.nodes["primary"].live)

    def test_normal_owner_requires_ack_and_proof(self):
        state, model, _b = sdk(self.source)
        receipt = state.finish()
        self.assertEqual(receipt["state"], "complete")
        self.assertEqual(model.culls, [])
        self.assertEqual(state.finish(), receipt)

    def test_invalid_acknowledgements_are_failures(self):
        state, _m, boundary = sdk(self.source)
        good = self.source.own.completion("A", boundary, True, complete=True)
        variants = [("missing", b""), ("partial-json", b"{"), ("oversized", b"x" * 8193),
                    ("transport-truncation", ValueError("inert MSG_TRUNC")),
                    ("unknown-key", json.dumps({**good, "other": True}).encode()),
                    ("duplicate-key", json.dumps(good).replace('"version": 1', '"version": 1, "version": 1').encode())]
        for key, value in (("instance", "B"), ("boundary", [11, 201, 11, 202]), ("bound", False),
                           ("version", True), ("state", "incomplete"), ("primary_exited", False),
                           ("descendants_empty", False), ("adoption_drained", False),
                           ("pending", ["child"]), ("problems", ["failed"])):
            variants.append((key, json.dumps({**good, key: value}).encode()))
        variants.append(("partial-fields", json.dumps({"state": "complete"}).encode()))
        for name, payload in variants:
            with self.subTest(name=name):
                state, model, _b = sdk(self.source, payload=payload)
                with self.assertRaises(self.source.own.CleanupIncomplete):
                    state.finish()
                self.assertFalse(state.receipt)
                self.assertFalse(model.survivors())
                self.assertFalse(model.nodes["primary"].live)
                self.assertTrue(model.nodes["unrelated"].live)
                self.assertTrue(model.nodes["sibling-service"].live)

    def test_owner_statuses_are_not_completion(self):
        for status, live in ((1, False), (-9, False), (-15, False), (None, False), (None, True)):
            with self.subTest(status=status, live=live):
                state, model, _b = sdk(self.source, status=status, owner_live=live)
                with self.assertRaises(self.source.own.CleanupIncomplete):
                    state.finish()
                self.assertFalse(model.survivors())
                self.assertFalse(model.nodes["primary"].live)
                self.assertTrue(model.nodes["unrelated"].live)
                receipt = state.finish()
                self.assertEqual(receipt["source"], "sdk-recovery")
                self.assertIn("owner_fault", receipt)

    def test_missing_ack_recovers_live_nested_tree_and_primary(self):
        state, model, _b = sdk(self.source, payload=b"", primary_live=True, child_live=True)
        with self.assertRaises(self.source.own.CleanupIncomplete):
            state.finish()
        self.assertFalse(model.survivors())
        self.assertFalse(model.nodes["primary"].live)
        self.assertTrue(model.nodes["sibling-service"].live)

    def test_stopped_owner_timeout_culls_entire_instance(self):
        state, model, _b = sdk(self.source, status=None, owner_live=True, primary_live=True, child_live=True)
        with self.assertRaises(self.source.own.CleanupIncomplete):
            state.finish()
        self.assertFalse(model.survivors())
        self.assertFalse(model.nodes["primary"].live)
        self.assertTrue(model.nodes["unrelated"].live)

    def test_stopped_owner_before_migration_uses_exact_pidfd(self):
        state, model, _b = sdk(self.source, status=None, owner_live=True)
        # No child can be born before owner migration. Model its stopped
        # bootstrap outside the group; pidfd recovery must still end it.
        model.nodes["cleanup-owner"].scope = "bootstrap"
        with self.assertRaises(self.source.own.CleanupIncomplete):
            state.finish()
        self.assertFalse(model.nodes["cleanup-owner"].live)
        self.assertTrue(model.nodes["unrelated"].live)
        self.assertEqual(state.finish()["source"], "sdk-recovery")

    def test_ack_without_live_exit_proof_is_incomplete(self):
        for primary, child in ((True, False), (False, True)):
            with self.subTest(primary=primary, child=child):
                state, model, _b = sdk(self.source, primary_live=primary, child_live=child)
                with self.assertRaises(self.source.own.CleanupIncomplete):
                    state.finish()
                self.assertFalse(model.survivors())
                self.assertFalse(model.nodes["primary"].live)

    def test_hard_loss_watcher_recovers_before_close(self):
        state, model, _b = sdk(self.source, status=-9, primary_live=True, child_live=True)
        bus = self.bus_object(state, model)
        watcher = FD("cleanup-owner", 1)
        bus._watch_owner(watcher)
        self.assertFalse(model.survivors(), "watcher must cull hidden live descendants")
        self.assertFalse(model.nodes["primary"].live)
        self.assertTrue(model.nodes["unrelated"].live)
        self.assertFalse(state.fault_reported)
        with self.assertRaises(self.source.own.CleanupIncomplete):
            bus.close()
        self.assertFalse(bus.closed)
        self.assertIsNotNone(bus.owner_pidfd)
        bus.close()
        self.assertTrue(bus.closed)

    def bus_object(self, state, model):
        bus = self.source.bus.PrivateBus.__new__(self.source.bus.PrivateBus)
        bus._lock = DummyLock()
        bus._watch_stop = DummyEvent()
        bus.closed = False
        bus._closing = bus._cleanup_verified = False
        bus._watcher = None
        bus._watch_fd = None
        bus._watch_errors = []
        bus._watch_errors_reported = 0
        bus._finalizer = None
        bus._primary_status_read = bus._primary_status_write = None
        bus._pending_primary = bus._unverified_primary = None
        bus.ops = model
        bus.cleanup_state, bus.boundary = state, state.boundary
        bus.platform = None
        bus.owner_process, bus.owner_pidfd = state.process, state.owner_fd
        bus.pidfd = bus.app_fd = bus.gate_write = None
        bus.control = Transport(b"")
        bus.completion_control = state.transport
        return bus

    def test_close_retries_and_is_idempotent(self):
        state, model, boundary = sdk(self.source)
        bus = self.bus_object(state, model)
        bus.close()
        self.assertTrue(bus.closed)
        self.assertTrue(boundary.retired)
        self.assertTrue(bus.control is None and bus.completion_control is None)
        signals, closed = list(model.signals), list(model.closed)
        bus.close()
        self.assertEqual(model.signals, signals)
        self.assertEqual(model.closed, closed)

    def test_close_retirement_failure_retains_authority(self):
        state, model, boundary = sdk(self.source)
        bus = self.bus_object(state, model)
        model.failures[("retire", 1)] = PermissionError("inert retirement failure")
        with self.assertRaises(PermissionError):
            bus.close()
        self.assertFalse(bus.closed)
        self.assertIsNotNone(bus.owner_pidfd)
        self.assertNotIn(state.primary, model.closed)
        bus.close()
        self.assertTrue(bus.closed and boundary.retired)

    def test_failed_recovery_retains_handles_and_is_retryable(self):
        state, model, boundary = sdk(self.source, status=-9, child_live=True, primary_live=True)
        model.failures[("cull", 1)] = PermissionError("inert cgroup kill refusal")
        bus = self.bus_object(state, model)
        with self.assertRaises(self.source.own.CleanupIncomplete):
            bus.close()
        self.assertFalse(bus.closed)
        self.assertTrue(model.survivors())
        self.assertIs(bus.boundary, boundary)
        self.assertNotIn(state.owner_fd, model.closed)
        bus.close()
        self.assertTrue(bus.closed)
        self.assertFalse(model.survivors())

    def test_close_partial_release_does_not_reuse_closed_owner_fd(self):
        state, model, _b = sdk(self.source)
        bus = self.bus_object(state, model)
        # Owner closes first, primary release fails. Retrying must use the
        # existing verified receipt, without probing the retired owner fd.
        model.failures[("fd-close", 2)] = PermissionError("inert primary FD release failure")
        with self.assertRaises(PermissionError):
            bus.close()
        self.assertFalse(bus.closed)
        self.assertTrue(bus._cleanup_verified)
        self.assertIsNone(bus.owner_pidfd)
        bus.close()
        self.assertTrue(bus.closed)

    def test_watcher_join_failure_remains_retryable(self):
        state, model, _b = sdk(self.source)
        bus = self.bus_object(state, model)
        class Watcher:
            def __init__(self): self.live, self.joins = True, 0
            def join(self, timeout): self.joins += 1
            def is_alive(self): return self.live
        watcher = bus._watcher = Watcher()
        with self.assertRaises(self.source.own.CleanupIncomplete):
            bus.close()
        self.assertFalse(bus.closed)
        self.assertIs(bus._watcher, watcher)
        watcher.live = False
        bus.close()
        self.assertTrue(bus.closed)
        self.assertIsNone(bus._watcher)
        self.assertEqual(watcher.joins, 2)

    def test_recovery_budget_each_phase_is_explicit(self):
        for phase in ("cull", "signal", "ready", "empty"):
            with self.subTest(phase=phase):
                state, model, _b = sdk(self.source, status=-9, child_live=True, primary_live=True)
                # observe_exit checks owner readiness once before recovery.
                model.costs[(phase, 2 if phase == "ready" else 1)] = 2
                state.observe_exit()
                self.assertFalse(state.recovered, "expired recovery cannot be reported complete")
                self.assertIsNotNone(state.fault)
                self.assertEqual(model.closed, [])

    def test_completion_evidence_budget_each_phase_is_explicit(self):
        for phase in ("ready", "status", "empty"):
            with self.subTest(phase=phase):
                state, model, _b = sdk(self.source)
                model.costs[(phase, 2 if phase == "ready" else 1)] = 4
                with self.assertRaises(self.source.own.CleanupIncomplete): state.finish()
                self.assertIsNone(state.receipt)
                self.assertIsNotNone(state.fault)

    def test_missing_owner_pidfd_requires_recovery(self):
        state, model, _b = sdk(self.source, status=-9, child_live=True)
        state.owner_fd = None
        with self.assertRaises(self.source.own.CleanupIncomplete):
            state.finish()
        self.assertFalse(model.survivors())
        self.assertEqual(state.finish()["source"], "sdk-recovery")

    def test_events_are_strict(self):
        self.assertTrue(self.source.own.parse_events(b"populated 0\nfrozen 0\n"))
        self.assertFalse(self.source.own.parse_events(b"populated 1\n"))
        for data in (b"", b"populated 0", b"frozen 0\n", b"populated 2\n",
                     b"populated 0\npopulated 1\n", b"populated 0\n" + b"x" * 4096):
            with self.subTest(data=data[:50]), self.assertRaises(ValueError):
                self.source.own.parse_events(data)

    def test_unconfigured_platform_fails_before_native_access(self):
        for env in ({}, {"KILIX_PRIVATE_BUS_CGROUP_ROOT": "relative"}):
            with self.assertRaises(RuntimeError):
                self.source.own.InstanceBoundary.create(env, "A")

    def test_bus_constructor_requires_boundary_before_any_native_start(self):
        module = self.source.bus
        fake_threads = types.SimpleNamespace(RLock=DummyLock, Event=DummyEvent, Thread=deny)
        class Unsupported:
            def __init__(self, _instance): pass
            def prepare(self): raise RuntimeError("unsupported delegation")
            def retire(self): pass
        with replace(module, threading=fake_threads, PlatformScope=Unsupported), self.assertRaisesRegex(RuntimeError, "delegation"):
            module.PrivateBus(types.SimpleNamespace(spawn=deny), {})

    def test_constructor_watches_owner_before_readiness_wait(self):
        module = self.source.bus
        model, order = Model(), []
        boundary = Boundary(model, outer=True)
        boundary.fds = lambda: tuple(FD("cgroup-" + str(i)) for i in range(8))
        sockets = []
        class Control:
            def __init__(self, name, data=b""): self.name, self.data = name, data
            def fileno(self): return FD(self.name)
            def close(self): pass
            def recv(self, _size): data, self.data = self.data, b""; return data
            def recvmsg(self, *_a): data, self.data = self.data, b""; return data, [], 0, None
        def pair(**_kw):
            index = len(sockets)
            data = (b'{"address":"unix:path=/inert","pid":1000000001}\n' if index == 0 else
                    json.dumps(self.source.own.completion("A", boundary, False, complete=True)).encode() if index == 2 else b"info")
            result = (Control(str(index), data), Control("child-" + str(index)))
            sockets.append(result)
            return result
        class Watcher:
            def __init__(self, *, target, args, **_kw): self.target, self.args, self.live = target, args, False
            def start(self): order.append("watcher"); self.live = True
            def join(self, timeout):
                if self.live: self.target(*self.args)
                self.live = False
            def is_alive(self): return self.live
        self_source = self.source
        class Platform:
            primary_fd = receipt = None
            def __init__(self, _instance): pass
            def prepare(self): order.append("platform-ready")
            def attach(self, *_a): order.append("boundary"); return boundary
            def launch_owner(self, supervisor, env, parent_fd, child, info, finish, bootstrap, _source):
                inherited = (parent_fd, child.fileno(), info.fileno(), finish.fileno(), bootstrap.fileno())
                process = supervisor.spawn("app-bus-owner", [sys.executable, "inert", "--bootstrap-owner", "1000000099", *map(str, inherited), "A"], pass_fds=inherited)
                return process, FD("cleanup-owner"), self.attach()
            def cleanup(self, *args): return self_source.own.SDKCleanup(*args)
            def bind_primary(self, _fd): pass
            def retire(self): boundary.retire()
        def spawn(_name, argv, **kw):
            order.append("owner-birth")
            self.assertEqual(len(argv), 10)
            self.assertEqual(len(kw["pass_fds"]), 5)
            self.assertEqual(argv[2], "--bootstrap-owner")
            self.assertEqual(argv[-1], "A")
            model.node("cleanup-owner", "sdk", scope="outer", resistant=False)
            return model.nodes["cleanup-owner"]
        def choose(fds, *_a):
            if fds[0] is sockets[1][0]: order.append("readiness-wait")
            return ([fd for fd in fds if getattr(fd, "data", None)], [], [])
        fake_os = types.SimpleNamespace(getpid=lambda: 1000000099, O_CLOEXEC=0,
            pidfd_open=lambda pid: FD("sdk-parent") if pid == 1000000099 else FD("cleanup-owner"),
            dup=lambda fd: FD(fd.identity, 1), close=model.close, fstat=model.fstat,
            pipe2=lambda _flags: (FD("gate-read"), FD("gate-write")))
        with replace(module, os=fake_os, LinuxProcesses=lambda: model,
                     PlatformScope=Platform, send_boundary=lambda *_a: order.append("gate-handoff"),
                     threading=types.SimpleNamespace(RLock=DummyLock, Event=DummyEvent, Thread=Watcher),
                     secrets=types.SimpleNamespace(token_hex=lambda _n: "A"),
                     socket=types.SimpleNamespace(socketpair=pair, SOCK_SEQPACKET=5),
                     select=types.SimpleNamespace(select=choose), time=types.SimpleNamespace(monotonic=model.now),
                     receive_pidfd=lambda *_a: (b"1000000001", None)):
            # Deliberate startup failure AFTER the watcher is installed. Its
            # error is observable and the real constructor closes all handles.
            with replace(self.source.own, select=types.SimpleNamespace(select=choose)):
                with self.assertRaisesRegex(RuntimeError, "startup failed"):
                    module.PrivateBus(types.SimpleNamespace(spawn=spawn), {})
        self.assertLess(order.index("platform-ready"), order.index("owner-birth"))
        self.assertLess(order.index("owner-birth"), order.index("boundary"))
        self.assertLess(order.index("boundary"), order.index("watcher"))
        self.assertLess(order.index("owner-birth"), order.index("watcher"))
        self.assertLess(order.index("watcher"), order.index("gate-handoff"))
        self.assertLess(order.index("watcher"), order.index("readiness-wait"))
        self.assertFalse(model.nodes["cleanup-owner"].live)
        self.assertIn(FD("cleanup-owner", 1), model.closed)

    def test_linux_close_errors_invalidate_descriptor_authority(self):
        own = self.source.own
        calls = []
        def close(fd):
            calls.append(fd)
            raise InterruptedError("inert Linux EINTR after release")
        state = [FD("private")]
        with replace(own, os=types.SimpleNamespace(close=close)), self.assertRaises(own.DescriptorReleased):
            own.release(state[0], own.close_descriptor, lambda: state.clear())
        self.assertEqual(state, [])
        self.assertEqual(calls, [FD("private")])

    def test_completion_socket_datagram_guards(self):
        good = b'{"state":"complete"}'
        class Packets:
            def __init__(self, packets): self.packets = list(packets)
            def recvmsg(self, *_a): return self.packets.pop(0)
        for packets, expected in (([(good, [], 0, None)], good),
                                  ([(b"NACK", [], 0, None), (good, [], 0, None)], good),
                                  ([], b""), ([(b"", [], 0, None)], b"")):
            transport = Packets(packets)
            fake_select = types.SimpleNamespace(select=lambda *_a: ([transport] if transport.packets else [], [], []))
            with replace(self.source.own, select=fake_select):
                self.assertEqual(self.source.own.CompletionSocket(transport).receive(), expected)
        for packets in ([(good, [], socket.MSG_TRUNC, None)], [(good, [], socket.MSG_CTRUNC, None)],
                        [(good, ["unexpected rights"], 0, None)], [(good, [], 0, None)] * 3):
            transport = Packets(packets)
            fake_select = types.SimpleNamespace(select=lambda *_a: ([transport] if transport.packets else [], [], []))
            with replace(self.source.own, select=fake_select), self.assertRaises(ValueError):
                self.source.own.CompletionSocket(transport).receive()

    def test_exact_pidfd_adoption_adapter(self):
        own = self.source.own
        fd = FD("owned-adoption")
        closed = []
        class StatPath:
            text = "1000000001 (inert) S 1000000002 0"
            def __init__(self, _p): pass
            def read_text(self): return self.text
        fake_os = types.SimpleNamespace(pidfd_open=lambda _pid: fd, getpid=lambda: 1000000002,
                                        close=closed.append)
        with replace(own, os=fake_os, Path=StatPath):
            self.assertEqual(own.LinuxProcesses.acquire(1000000001), fd)
            self.assertFalse(closed)
            # Same numeric input, distinct actual parent: no authority survives.
            StatPath.text = "1000000001 (reused-inert) S 1000000003 0"
            self.assertIsNone(own.LinuxProcesses.acquire(1000000001))
            self.assertEqual(closed, [fd])

    def test_wait_proof_adapter_requires_echild(self):
        for value in (None, object(), ChildProcessError()):
            def wait(*_a):
                if isinstance(value, Exception): raise value
                return value
            fake_os = types.SimpleNamespace(waitid=wait, P_ALL=0, WEXITED=4, WNOHANG=1, WNOWAIT=0x1000000)
            with replace(self.source.own, os=fake_os):
                self.assertEqual(self.source.own.LinuxProcesses.empty_children(), isinstance(value, ChildProcessError))

    def test_pinned_kernel_handles_and_retirement(self):
        own = self.source.own
        reads, writes, closed, removed = [], [], [], []
        values = {"events": b"populated 0\n"}
        identity = [11, 101]
        def write(fd, data):
            writes.append((fd, data))
            return len(data)
        fake_os = types.SimpleNamespace(fstat=lambda _fd: types.SimpleNamespace(st_dev=11, st_ino=101),
            lseek=lambda fd, _off, _mode: reads.append(fd), SEEK_SET=0,
            read=lambda fd, _size: values[fd.identity], write=write, close=closed.append,
            stat=lambda *_a, **_kw: types.SimpleNamespace(st_dev=identity[0], st_ino=identity[1]),
            rmdir=lambda name, **_kw: removed.append(name))
        with replace(own, os=fake_os):
            boundary = own.CgroupBoundary(FD("directory"), FD("join"), FD("kill"), FD("events"),
                                         parent=FD("root"), name="private-A")
            boundary.kill()
            self.assertEqual(writes, [(FD("kill"), b"1")])
            identity[1] = 201
            with self.assertRaises(own.CleanupIncomplete): boundary.retire()
            self.assertEqual(removed, [])
            self.assertEqual(closed, [])
            identity[1] = 101
            values["events"] = b"populated 1\n"
            with self.assertRaises(own.CleanupIncomplete): boundary.retire()
            values["events"] = b"populated 0\n"
            boundary.retire()
            boundary.retire()
            self.assertEqual(removed, ["private-A"])
            self.assertEqual(len(closed), 5)

    def test_partial_boundary_release_is_retryable(self):
        own = self.source.own
        closed = []
        fail = [True]
        def close(fd):
            if fd == FD("events") and fail[0]:
                fail[0] = False
                raise PermissionError("inert FD close failure")
            if fd in closed: raise AssertionError("duplicate FD release")
            closed.append(fd)
        fake_os = types.SimpleNamespace(fstat=lambda _fd: types.SimpleNamespace(st_dev=11, st_ino=101),
            lseek=lambda *_a: None, SEEK_SET=0, read=lambda *_a: b"populated 0\n", close=close,
            stat=lambda *_a, **_kw: types.SimpleNamespace(st_dev=11, st_ino=101), rmdir=lambda *_a, **_kw: None)
        with replace(own, os=fake_os, close_descriptor=close):
            boundary = own.CgroupBoundary(FD("directory"), FD("join"), FD("kill"), FD("events"),
                                         parent=FD("root"), name="private-A")
            with self.assertRaises(PermissionError): boundary.retire()
            self.assertTrue(boundary.removed)
            self.assertIsNone(boundary.kill_fd)
            self.assertIsNotNone(boundary.events_fd)
            boundary.retire()
            self.assertTrue(boundary.retired)
            self.assertEqual(len(closed), 5)

    def test_owner_join_precedes_guardian_birth(self):
        module = self.source.bus
        model = Model()
        events = []
        outer, inner = Boundary(model, outer=True), Boundary(model)
        outer.identity, inner.identity = (11, 101), (11, 102)
        outer.fds = lambda: tuple(FD(n) for n in ("outer-dir", "outer-join", "outer-kill", "outer-events"))
        inner.fds = lambda: tuple(FD(n) for n in ("inner-dir", "inner-join", "inner-kill", "inner-events"))
        controls = {}
        class Control:
            def __init__(self, name): self.name, self.sent = name, []
            def setblocking(self, _value): pass
            def send(self, data): self.sent.append(data); return len(data)
            def close(self): pass
            def fileno(self): return FD(self.name)
        def control(fileno): return controls.setdefault(fileno, Control(str(fileno)))
        def popen(argv, **kw):
            events.append(("birth", "outer-join" in events))
            self.assertIn(FD("inner-join"), kw["pass_fds"])
            model.node("guardian", scope="outer")
            return types.SimpleNamespace(pid=model.nodes["guardian"].pid)
        def write(fd, data):
            self.assertEqual((fd, data), (FD("outer-join"), b"0"))
            events.append("outer-join")
            return 1
        fake_os = types.SimpleNamespace(getppid=lambda: 1000000099, getpid=lambda: 1000000098,
                                        write=write, close=lambda *_a: None, pidfd_open=lambda _pid: FD("guardian"))
        fake_signal = types.SimpleNamespace(SIGTERM=signal.SIGTERM, SIGHUP=signal.SIGHUP,
                                           SIGINT=signal.SIGINT, signal=lambda *_a: None)
        fake_socket = types.SimpleNamespace(SOCK_SEQPACKET=5, socket=control,
                                            socketpair=lambda **_kw: (Control("watch"), Control("child-watch")))
        fake_ctypes = types.SimpleNamespace(CDLL=lambda *_a, **_kw: types.SimpleNamespace(prctl=lambda *_a: 0))
        fake_select = types.SimpleNamespace(select=lambda fds, *_a: ([fds[0]], [], []))
        with replace(module, os=fake_os, signal=fake_signal, socket=fake_socket, ctypes=fake_ctypes,
                     select=fake_select, LinuxProcesses=lambda: model, CgroupBoundary=lambda d, *_a: outer if d == FD("outer-dir") else inner,
                     subprocess=types.SimpleNamespace(Popen=popen, DEVNULL=-3), send_pidfd=lambda *_a: None,
                     time=types.SimpleNamespace(sleep=model.sleep)):
            status = module.bus_owner(1000000099, FD("sdk"), FD("control"), FD("info"), FD("finish"),
                                      *outer.fds(), *inner.fds(), "A")
        self.assertEqual(status, 0)
        self.assertIn(("birth", True), events, "outer membership must precede child birth")
        self.assertFalse(model.survivors())
        payload = json.loads(controls[FD("finish")].sent[-1])
        self.assertEqual(payload["boundary"], [11, 101, 11, 102])
        self.assertEqual(payload["state"], "complete")

    def test_guardian_join_precedes_daemon_birth(self):
        module = self.source.bus
        model = Model()
        events = []
        class Control:
            def sendall(self, _data): pass
            def close(self): pass
        stdout = types.SimpleNamespace(fileno=lambda: FD("stdout"), close=lambda: None)
        def popen(_argv, **_kw):
            events.append(("daemon-birth", "inner-join" in events))
            model.node("daemon", "guardian")
            return types.SimpleNamespace(pid=model.nodes["daemon"].pid, stdout=stdout, wait=lambda **_kw: -9)
        def write(fd, data):
            self.assertEqual((fd, data), (FD("inner-join"), b"0"))
            events.append("inner-join")
            return 1
        def choose(fds, _w, _e, timeout):
            if timeout == 0: return [], [], []
            return [fds[0]], [], []
        fake_os = types.SimpleNamespace(getppid=lambda: 1000000098, getpid=lambda: 1000000097,
            write=write, read=lambda *_a: b"unix:path=/inert\\n".replace(b"\\n", b"\n"),
            close=lambda *_a: None, pidfd_open=lambda _pid: FD("daemon"))
        fake_signal = types.SimpleNamespace(SIGTERM=signal.SIGTERM, SIGHUP=signal.SIGHUP,
                                           SIGINT=signal.SIGINT, signal=lambda *_a: None)
        with replace(module, os=fake_os, signal=fake_signal, LinuxProcesses=lambda: model,
                     socket=types.SimpleNamespace(socket=lambda **_kw: Control()),
                     subprocess=types.SimpleNamespace(Popen=popen, DEVNULL=-3, PIPE=-1),
                     select=types.SimpleNamespace(select=choose), bind_parent=lambda *_a: None,
                     receive_pidfd=lambda *_a: (b"", None),
                     shutil=types.SimpleNamespace(which=lambda _name: "/inert/dbus-daemon"),
                     time=types.SimpleNamespace(monotonic=model.now)):
            status = module.guardian(FD("control"), FD("sdk"), 1000000098, FD("watch"), FD("inner-join"))
        self.assertEqual(status, 0)
        self.assertIn(("daemon-birth", True), events, "inner membership must precede daemon birth")
        self.assertFalse(model.nodes["daemon"].live)

    def test_all_xapp_failures_continue_and_retry(self):
        steps = {"bus", "publication", "input", "capture", "capture-process", "clipboard", "X-connection", "supervisor"}
        for failed in sorted(steps):
            with self.subTest(failed=failed):
                value, calls = session(self.source, (failed,))
                with self.assertRaises(self.source.xapp.SessionCleanupError) as raised:
                    value.close()
                self.assertIs(raised.exception.session, value)
                self.assertEqual(set(calls), steps)
                self.assertFalse(value._closed)
                counts = {n: calls.count(n) for n in steps}
                # Remove the fault from retained objects. Completed references
                # have gone; each failed reference remains available to retry.
                for attr in ("_app_bus", "_capture_publication", "injector", "capture", "capture_process",
                             "clipboard_process", "xd", "supervisor"):
                    resource = getattr(value, attr)
                    if resource is not None:
                        resource.fail = False
                value.close()
                self.assertTrue(value._closed)
                for step in steps - {failed}:
                    self.assertEqual(calls.count(step), counts[step], f"completed {step} repeated")
                before = list(calls)
                value.close()
                self.assertEqual(calls, before)

    def test_multiple_xapp_failures_are_observable(self):
        value, calls = session(self.source, ("bus", "input", "capture", "clipboard", "X-connection", "supervisor"))
        with self.assertRaises(self.source.xapp.SessionCleanupError) as raised:
            value.close()
        self.assertEqual(set(raised.exception.failures), {"bus", "input", "capture", "clipboard", "X-connection", "supervisor"})
        self.assertIn("publication", calls)
        self.assertFalse(value._closed)
        self.assertEqual(value.capture_backend, "cleanup-incomplete")
        self.assertIsNotNone(value._app_bus)
        self.assertIsNotNone(value.capture)
        self.assertIsNotNone(value.clipboard_process)
        self.assertIsNotNone(value.xd)

    def test_xapp_rejects_new_resources_after_close_begins(self):
        value, _calls = session(self.source, ("bus",))
        with self.assertRaises(self.source.xapp.SessionCleanupError): value.close()
        for name, args in (("start_xvfb", ()), ("start_xvnc", (5900, "/inert/password")),
                           ("connect", ()), ("start_window_manager", ()), ("launch_app", (["inert"],)),
                           ("start_clipboard", ()), ("make_injector", ()), ("start_capture", ())):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "session is closing"):
                getattr(value, name)(*args)

    def test_supervisor_clearing_child_list_is_not_exit_proof(self):
        value, _calls = session(self.source)
        child = Process([], "owned-child", fail=True)
        value.supervisor.children = [("child", child, [], None)]
        with self.assertRaises(self.source.xapp.SessionCleanupError):
            value.close()
        self.assertFalse(value._closed)
        self.assertEqual(value.supervisor.children, [])
        self.assertEqual(len(value._supervisor_pending), 1)
        self.assertEqual(value._supervisor_pending[0][0], child)
        child.fail = False
        value.close()
        self.assertTrue(value._closed)
        self.assertFalse(value._supervisor_pending)

    def test_process_failure_still_closes_independent_streams(self):
        calls = []
        process = Process(calls, "owned", fail=True)
        process.stdin = Resource("stdin", calls, True)
        process.stdout = Resource("stdout", calls)
        process.stderr = Resource("stderr", calls)
        with self.assertRaises(self.source.xapp.SessionCleanupError) as raised:
            self.source.xapp._stop_process(process)
        self.assertEqual(set(raised.exception.failures), {"process", "stdin"})
        self.assertTrue(process.stdout.closed and process.stderr.closed)
        process.fail = False
        process.stdin.fail = False
        self.source.xapp._stop_process(process)
        self.assertTrue(process.dead and process.stdin.closed)


class DummyLock:
    def __enter__(self): return self
    def __exit__(self, *_a): pass


class DummyEvent:
    def __init__(self): self.value = False
    def is_set(self): return self.value
    def set(self): self.value = True


def run_suite(case, source, target, names=None):
    case.source = source
    suite = (unittest.TestSuite(case(n) for n in names) if names else
             unittest.defaultTestLoader.loadTestsFromTestCase(case))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue()
    target.write_text(log)
    return result, log


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: test_bus_ownership_inert.py CANDIDATE FROZEN_BASE DISPOSABLE_RESULTS")
    source_root, base_root, output = map(lambda s: Path(s).absolute(), sys.argv[1:])
    output.mkdir(parents=True, exist_ok=False)
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        if not Path(os.environ[key]).is_relative_to(output.parent.parent):
            raise AssertionError("private env boundary missing")
    if "DISPLAY" in os.environ or "WAYLAND_DISPLAY" in os.environ:
        raise AssertionError("inert runner inherited display")
    install_denials()
    # Assertions ensure the denials are active, without attempting any native
    # operation: each function is the literal denial callable before dispatch.
    assert os.pidfd_open is deny and subprocess.Popen is deny and socket.socket is deny
    baseline = load_source(base_root, "frozen", baseline=True)
    before, before_log = run_suite(Baseline, baseline, output / "before.log")
    if before.testsRun != 3 or len(before.failures) != 3 or before.errors:
        raise AssertionError("baseline must expose exactly three behavioral completion failures: " + before_log)
    candidate = load_source(source_root, "candidate")
    after, after_log = run_suite(Candidate, candidate, output / "after.log")
    if not after.wasSuccessful():
        raise AssertionError("candidate inert failures: " + after_log)
    mutations = [
        ("omit-kernel-empty-proof", "bus_ownership.py", "and self.boundary.empty() and ops.empty_children():", ":", "test_live_boundary_prevents_empty_direct_child_success"),
        ("omit-adoption-wait-proof", "bus_ownership.py", "and ops.empty_children()", "", "test_late_adoption_retained_until_waited"),
        ("drop-pending-accounting", "bus_ownership.py", "problems = [error]", "self.owned.clear()\n            problems = [error]", "test_pending_fd_is_retained_and_retry_finishes"),
        ("accept-expired-drain", "bus_ownership.py", 'raise CleanupIncomplete(phase, pending=self.owned, problems=problems,\n                                    resource=self) from error', 'return', "test_expiry_each_drain_phase"),
        ("retain-released-fd-authority", "bus_ownership.py", "except DescriptorReleased:\n        released()", "except DescriptorReleased:\n        pass", "test_released_descriptor_error_is_not_retained_authority"),
        ("ignore-instance-binding", "bus_ownership.py", 'value["instance"] != instance', 'False', "test_invalid_acknowledgements_are_failures"),
        ("accept-abnormal-owner", "bus_ownership.py", 'type(status) is not int or status != 0', 'False', "test_owner_statuses_are_not_completion"),
        ("omit-hard-loss-recovery", "bus_ownership.py", 'self.boundary.kill()\n            check()', 'check()', "test_hard_loss_watcher_recovers_before_close"),
        ("omit-owner-migration", "private_bus.py", 'if os.write(join_fd, b"0") != 1:\n            raise RuntimeError("cannot enter private bus instance boundary")', 'if False:\n            raise RuntimeError("cannot enter private bus instance boundary")', "test_owner_join_precedes_guardian_birth"),
        ("omit-guardian-migration", "private_bus.py", 'if os.write(join_fd, b"0") != 1:\n            raise RuntimeError("cannot enter private bus boundary")', 'if False:\n            raise RuntimeError("cannot enter private bus boundary")', "test_guardian_join_precedes_daemon_birth"),
        ("early-xapp-closed", "xapp.py", '        failures = {}\n        def resource', '        self._closed = True\n        failures = {}\n        def resource', "test_all_xapp_failures_continue_and_retry"),
        ("skip-on-bus-error", "xapp.py", '        resource("publication", "_capture_publication")', '        if failures:\n            raise SessionCleanupError(failures, self)\n        resource("publication", "_capture_publication")', "test_all_xapp_failures_continue_and_retry"),
        ("forget-supervisor-pending", "xapp.py", '        if failures:\n            raise SessionCleanupError(failures, self)\n\n    def close', '        self._supervisor_pending.clear()\n        if failures:\n            raise SessionCleanupError(failures, self)\n\n    def close', "test_supervisor_clearing_child_list_is_not_exit_proof"),
    ]
    mutation_results = []
    for index, (name, file, old, new, test) in enumerate(mutations):
        root = output / "mutants" / name
        directory = root / "config/kilix_sdk"
        directory.mkdir(parents=True)
        for source in ("private_bus.py", "bus_ownership.py", "bus_platform.py", "bus_supervision.py", "bus_supervisor.py", "process_pin.py", "xapp.py"):
            (directory / source).write_bytes((source_root / "config/kilix_sdk" / source).read_bytes())
        path = directory / file
        original = path.read_text()
        expected_count = 2 if name == "omit-adoption-wait-proof" else 1
        if original.count(old) != expected_count:
            raise AssertionError(f"mutation {name} has ambiguous anchor")
        path.write_text(original.replace(old, new))
        mutant = load_source(root, "mutant_" + str(index))
        result, log = run_suite(Candidate, mutant, output / (name + ".log"), [test])
        if result.errors or not result.failures:
            raise AssertionError(f"mutation {name} did not fail behaviorally: {log}")
        mutation_results.append({"name": name, "test": test, "old": old, "new": new,
                                 "source": str(path), "sha256": digest(path.read_bytes()),
                                 "failures": len(result.failures), "errors": len(result.errors),
                                 "kind": "inert-behavioral-guard"})
    report = {"scope": "source-only fully inert; no native lifecycle evidence", "baseline_tests": before.testsRun,
              "baseline_expected_failures": len(before.failures), "candidate_tests": after.testsRun,
              "candidate_failures": len(after.failures), "candidate_errors": len(after.errors),
              "guards": mutation_results, "bindings": BINDINGS,
              "runner_sha256": digest(Path(__file__).read_bytes()),
              "native_denials": "process creation, signals, pidfds, waits, sockets, select, ctypes, threads, sleep, /proc and /sys reads",
              "environment": {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS")}}
    (output / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("scope", "baseline_tests", "baseline_expected_failures", "candidate_tests", "candidate_failures", "candidate_errors")}))
    print(f"{len(mutation_results)} inert behavioral guards failed as expected; no native tests run")


if __name__ == "__main__":
    main()
