"""Run the X-server tests inside a private mount namespace.

python-xlib resolves the display name ``:N`` to the filesystem socket
``/tmp/.X11-unix/XN`` and only falls back to the abstract socket when that
path is absent::

    address = '/tmp/.X11-unix/X%d' % dno
    if not os.path.exists(address):
        address = '\\0' + address

``/tmp/.X11-unix`` is shared with every other process on the machine, and the
display number an ``Xvfb -displayfd`` hands back says nothing about who owns
that path.  A test that starts its own server can therefore connect to -- and
inject synthetic key and button events into -- a *foreign* X server, silently
and with no error.  That is not hypothetical: it was caught happening on this
machine, with a live server belonging to someone else sitting on the number
our own server had just been given.

The remedy here is structural rather than a check the next test author can
forget.  The X tests run in a child process that first enters a private
user+mount namespace and mounts a fresh tmpfs over ``/tmp/.X11-unix``.  Inside
that namespace the directory starts *empty*: not one foreign socket is
reachable through it, because the host directory is no longer part of this
process's view of the filesystem at all.  The only socket that can ever appear
there is the one the test's own server creates.

Entering the namespace needs no privilege, no setuid helper and no
``newuidmap``: ``unshare(2)`` with ``CLONE_NEWUSER`` gives the caller a full
capability set *inside the new user namespace*, which is what makes the
subsequent mount legal, and a single-entry identity uid map keeps every uid
the tests observe exactly what it was outside.

Two consequences worth knowing:

* The server can now create a real filesystem listener, because the tmpfs is
  owned by us and mode 1777, so the abstract-socket fallback is never taken.
* Nothing the tests do is visible in the host's ``/tmp/.X11-unix``, and
  nothing in the host's ``/tmp/.X11-unix`` is visible to the tests.

How a module opts in
--------------------
::

    def load_tests(loader, tests, pattern):
        return x11_sandbox.sandbox_load_tests(loader, tests, __name__)

Outside the namespace that returns one proxy per real test; the first proxy to
run executes the whole module in a namespaced child, and every proxy then
reports its own recorded outcome, so the test count, the per-test attribution
and the sub-test count are the ones the module really produced.  Inside the
namespace it returns the real tests unchanged.

The harness of each module then calls :func:`require_private_x11` before it
starts a server and :func:`confirm_our_socket` / :func:`claim_display`
afterwards, so a run proves which server it is talking to instead of assuming
it.  Those calls are assertions, not the isolation; the isolation is the
namespace, and they fail loudly if it is ever missing.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid

SANDBOX_ENV = "KILIX_X11_SANDBOX"
X11_DIR = "/tmp/.X11-unix"
TOKEN_PROPERTY = "_KILIX_X11_SANDBOX"

_CLONE_NEWNS = 0x00020000
_CLONE_NEWUSER = 0x10000000
_MS_REC = 0x4000
_MS_PRIVATE = 1 << 18

#: the child could not build the namespace; the parent turns this into skips
_UNAVAILABLE_EXIT = 78
_CHILD_TIMEOUT = 900


class SandboxUnavailable(Exception):
    """The private mount namespace could not be created on this machine."""


class ChildProtocolError(Exception):
    """The sandboxed child did not report a usable result for a test."""


def in_sandbox() -> bool:
    return os.environ.get(SANDBOX_ENV) == "1"


# --------------------------------------------------------------------------
# entering the namespace
# --------------------------------------------------------------------------

def _libc():
    return ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6",
                       use_errno=True)


def _check(rc, what):
    if rc != 0:
        code = ctypes.get_errno()
        raise SandboxUnavailable(f"{what}: {os.strerror(code)}")


def _thread_count() -> int:
    with open("/proc/self/status", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("Threads:"):
                return int(line.split()[1])
    return 1


def enter_private_x11_namespace() -> None:
    """Put this process in a user+mount namespace with a private X11 dir.

    Irreversible for the calling process, so it is only ever called at the
    top of the sandboxed child, before the test module is imported and before
    anything has had a chance to start a thread.
    """
    if in_sandbox():
        return
    threads = _thread_count()
    if threads != 1:
        raise SandboxUnavailable(
            f"CLONE_NEWUSER needs a single-threaded process, found {threads}")
    libc = _libc()
    # Read the ids BEFORE the unshare: afterwards, and until the map below is
    # written, every id in this process reads back as the overflow uid.
    uid, gid = os.getuid(), os.getgid()
    _check(libc.unshare(_CLONE_NEWUSER | _CLONE_NEWNS), "unshare")
    try:
        with open("/proc/self/setgroups", "w", encoding="ascii") as handle:
            handle.write("deny")
    except FileNotFoundError:          # pre-3.19 kernels have no setgroups
        pass
    except OSError as error:
        raise SandboxUnavailable(f"setgroups: {error}") from error
    try:
        # An identity map: uid N stays uid N, so nothing the tests stat
        # changes owner underneath them.  A single line mapping our own uid
        # needs no privilege and no newuidmap helper.
        with open("/proc/self/uid_map", "w", encoding="ascii") as handle:
            handle.write(f"{uid} {uid} 1")
        with open("/proc/self/gid_map", "w", encoding="ascii") as handle:
            handle.write(f"{gid} {gid} 1")
    except OSError as error:
        raise SandboxUnavailable(f"uid/gid map: {error}") from error
    # Detach our mounts from the host's propagation tree first, or the tmpfs
    # below would propagate back out and hide /tmp/.X11-unix for everyone.
    _check(libc.mount(b"none", b"/", None, _MS_REC | _MS_PRIVATE, None),
           "make / rprivate")
    try:
        os.makedirs(X11_DIR, exist_ok=True)
    except OSError as error:
        raise SandboxUnavailable(f"{X11_DIR}: {error}") from error
    _check(libc.mount(b"tmpfs", X11_DIR.encode(), b"tmpfs", 0, b"mode=1777"),
           f"mount tmpfs on {X11_DIR}")
    os.environ[SANDBOX_ENV] = "1"


# --------------------------------------------------------------------------
# assertions a harness makes about the server it started
# --------------------------------------------------------------------------

def _x11_dir_mount() -> str | None:
    """Return the filesystem type mounted at X11_DIR, or None."""
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        found = None
        for line in handle:
            fields = line.split()
            try:
                separator = fields.index("-")
            except ValueError:
                continue
            if fields[4] == X11_DIR:
                found = fields[separator + 1]
        return found


def require_private_x11() -> list[str]:
    """Refuse to start a server unless /tmp/.X11-unix is private and empty.

    Returns the (empty) directory listing, to be handed back to
    :func:`confirm_our_socket` as the "before" state.
    """
    if not in_sandbox():
        raise RuntimeError(
            "this test injects synthetic input into an X server and must run "
            "inside the private mount namespace, or it can reach a foreign "
            "display; run the module rather than a single class or method, "
            "e.g. python3 -m unittest tests.test_xinject_mouse")
    kind = _x11_dir_mount()
    if kind != "tmpfs":
        raise RuntimeError(
            f"{X11_DIR} is not a private tmpfs (mounted type: {kind!r})")
    listing = sorted(os.listdir(X11_DIR))
    if listing:
        raise RuntimeError(
            f"{X11_DIR} should be empty inside the sandbox, found {listing}")
    return listing


def confirm_our_socket(number: int, before: list[str]) -> str:
    """Prove the display we are about to open is the server we just started.

    ``before`` is the listing taken by :func:`require_private_x11`, which is
    empty by construction: the directory is a tmpfs this process mounted.  So
    any socket in it now was created by a server this process started, and the
    one we are about to connect to exists as a *filesystem* socket, which is
    the path python-xlib prefers -- the abstract fallback cannot be taken.
    """
    if before:
        raise RuntimeError(f"{X11_DIR} was not empty before the server started")
    if _x11_dir_mount() != "tmpfs":
        raise RuntimeError(f"{X11_DIR} is no longer the private tmpfs")
    path = os.path.join(X11_DIR, f"X{int(number)}")
    listing = sorted(os.listdir(X11_DIR))
    if not os.path.exists(path):
        raise RuntimeError(
            f"{path} does not exist; python-xlib would fall back to the "
            f"abstract socket. {X11_DIR} holds {listing}")
    if not stat.S_ISSOCK(os.stat(path).st_mode):
        raise RuntimeError(f"{path} is not a socket")
    return path


def claim_display(xd) -> bytes:
    """Mark the connected server with a token unique to this connection.

    The isolation is what stops a foreign server being reachable; this token
    is what makes a cross-connection *detectable after the fact*.  A server
    that was never touched by a sandboxed run carries no such property, so an
    external check on a decoy server can prove, positively, that the tests
    never reached it.
    """
    from Xlib import Xatom

    token = f"{uuid.uuid4().hex}:{os.getpid()}".encode("ascii")
    atom = xd.intern_atom(TOKEN_PROPERTY)
    root = xd.screen().root
    root.change_property(atom, Xatom.STRING, 8, token)
    xd.sync()
    echoed = root.get_full_property(atom, Xatom.STRING)
    if echoed is None or bytes(echoed.value) != token:
        raise RuntimeError(
            "the display did not echo back the token this run just wrote")
    return token


# --------------------------------------------------------------------------
# running a module's tests in the sandboxed child
# --------------------------------------------------------------------------

def _iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def _jsonable(value):
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


class _RecordingResult(unittest.TestResult):
    """Collect one record per test id, sub-test outcomes included."""

    def __init__(self):
        super().__init__()
        self.records = {}

    def _record(self, test):
        return self.records.setdefault(
            test.id(), {"outcome": "success", "detail": "", "subtests": []})

    def startTest(self, test):
        super().startTest(test)
        self._record(test)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        record = self._record(test)
        record["outcome"] = "failure"
        record["detail"] = self._exc_info_to_string(err, test)

    def addError(self, test, err):
        super().addError(test, err)
        record = self._record(test)
        record["outcome"] = "error"
        record["detail"] = self._exc_info_to_string(err, test)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        record = self._record(test)
        record["outcome"] = "skip"
        record["detail"] = reason

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        record = self._record(test)
        record["outcome"] = "unsupported"
        record["detail"] = (
            "x11_sandbox cannot replay an expected failure; give this module "
            "its own runner or drop the decorator")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        record = self._record(test)
        record["outcome"] = "unsupported"
        record["detail"] = (
            "x11_sandbox cannot replay an unexpected success; give this "
            "module its own runner or drop the decorator")

    def addSubTest(self, test, subtest, outcome):
        super().addSubTest(test, subtest, outcome)
        if outcome is None:
            return
        record = self._record(test)
        params = {str(key): _jsonable(value)
                  for key, value in (subtest.params or {}).items()}
        record["subtests"].append({
            "params": params,
            "message": subtest._message if subtest._message is not
            unittest.case._subtest_msg_sentinel else None,
            "failure": issubclass(outcome[0], test.failureException),
            "detail": self._exc_info_to_string(outcome, test),
        })


def _run_module_in_child(module_name: str) -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    env = dict(os.environ)
    env.pop(SANDBOX_ENV, None)
    env["PYTHONPATH"] = os.pathsep.join(
        [here, root] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    with tempfile.TemporaryDirectory(prefix="x11-sandbox-") as work:
        out = os.path.join(work, "results.json")
        log = os.path.join(work, "child.err")
        with open(log, "w+b") as stream:
            child = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), module_name, out],
                cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT)
            try:
                status = child.wait(timeout=_CHILD_TIMEOUT)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
                status = None
            stream.flush()
            stream.seek(0)
            captured = stream.read().decode("utf-8", "replace")
        results = None
        if os.path.exists(out):
            with open(out, encoding="utf-8") as handle:
                results = json.load(handle)
    # The child's own output is the module's output; keep showing it.
    if captured:
        sys.stderr.write(captured)
        sys.stderr.flush()
    tail = "".join(captured.splitlines(keepends=True)[-12:]).strip()
    if status is None:
        raise ChildProtocolError(
            f"sandboxed run of {module_name} timed out after "
            f"{_CHILD_TIMEOUT}s\n{tail}")
    if status == _UNAVAILABLE_EXIT:
        raise SandboxUnavailable(tail or "namespace unavailable")
    if results is None:
        raise ChildProtocolError(
            f"sandboxed run of {module_name} exited {status} without "
            f"results\n{tail}")
    return results


_RESULTS: dict[str, object] = {}


def _module_results(module_name: str) -> dict:
    if module_name not in _RESULTS:
        try:
            _RESULTS[module_name] = _run_module_in_child(module_name)
        except (SandboxUnavailable, ChildProtocolError) as error:
            _RESULTS[module_name] = error
    results = _RESULTS[module_name]
    if isinstance(results, SandboxUnavailable):
        raise unittest.SkipTest(
            f"private X11 mount namespace unavailable: {results}")
    if isinstance(results, ChildProtocolError):
        raise results
    return results


class _SandboxProxy(unittest.TestCase):
    """Stands in, outside the namespace, for one test run inside it."""

    _sandbox_module = ""

    def _replay(self):
        results = _module_results(self._sandbox_module)
        record = results.get(self.id())
        if record is None:
            # A setUpClass/setUpModule failure is recorded by unittest against
            # a description, not a test id, so carry it across rather than
            # reporting a bare "no outcome" for every test in the class.
            holders = [f"{key}: {value['detail']}"
                       for key, value in results.items()
                       if key.endswith(")") and value["outcome"] != "success"]
            raise ChildProtocolError(
                f"the sandboxed run reported no outcome for {self.id()}"
                + ("\n" + "\n".join(holders) if holders else ""))
        for event in record["subtests"]:
            with self.subTest(**event["params"]):
                if event["failure"]:
                    raise self.failureException(event["detail"])
                raise ChildProtocolError(event["detail"])
        outcome = record["outcome"]
        if outcome == "success":
            return
        if outcome == "skip":
            self.skipTest(record["detail"])
        if outcome == "failure":
            raise self.failureException(record["detail"])
        raise ChildProtocolError(record["detail"])


def _proxy_method(name):
    def method(self):
        self._replay()
    method.__name__ = name
    method.__qualname__ = name
    return method


def sandbox_load_tests(loader, standard_tests, module_name):
    """``load_tests`` body for a module whose tests need a private display."""
    if in_sandbox():
        return standard_tests
    groups = {}
    for test in _iter_tests(standard_tests):
        groups.setdefault(type(test), []).append(test._testMethodName)
    suite = unittest.TestSuite()
    for original, names in groups.items():
        namespace = {
            "__module__": original.__module__,
            "__qualname__": original.__qualname__,
            "_sandbox_module": module_name,
        }
        # Carry a whole-class skip across, so a module that would be skipped
        # anyway (no Xvfb, no python-xlib) never spawns a child.
        for attribute in ("__unittest_skip__", "__unittest_skip_why__"):
            if getattr(original, attribute, None):
                namespace[attribute] = getattr(original, attribute)
        for name in names:
            namespace[name] = _proxy_method(name)
        proxy = type(original.__name__, (_SandboxProxy,), namespace)
        for name in names:
            suite.addTest(proxy(name))
    return suite


def main(argv):
    module_name, out_path = argv[1], argv[2]
    try:
        enter_private_x11_namespace()
    except SandboxUnavailable as error:
        sys.stderr.write(f"x11_sandbox: {error}\n")
        return _UNAVAILABLE_EXIT
    import importlib

    here = os.path.dirname(os.path.abspath(__file__))
    for entry in (os.path.dirname(here), here):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    module = importlib.import_module(module_name)
    suite = unittest.TestLoader().loadTestsFromModule(module)
    result = _RecordingResult()
    suite.run(result)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result.records, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
