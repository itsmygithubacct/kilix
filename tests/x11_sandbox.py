"""Run the X-server tests in private mount and network namespaces.

A client can name an X server three ways, and ``/tmp/.X11-unix`` is only one
of them.  python-xlib resolves ``:N`` to the filesystem socket
``/tmp/.X11-unix/XN`` and falls back to the *abstract* socket of the same name
when that path is absent::

    address = '/tmp/.X11-unix/X%d' % dno
    if not os.path.exists(address):
        address = '\\0' + address

libX11/xcb (which ``config/xcapture.py`` reaches through ``XOpenDisplay``)
tries the abstract name *first*.  A ``host:N`` name goes over TCP.  And a
client that reads ``$DISPLAY`` connects to whatever the invoking shell pointed
it at, with the cookie ``$XAUTHORITY`` names.

``/tmp/.X11-unix`` is shared with every other process on the machine, abstract
sockets are shared with every process in the same *network* namespace, and the
display number an ``Xvfb -displayfd`` hands back says nothing about who owns
either name.  A test that starts its own server can therefore connect to --
and inject synthetic key and button events into -- a *foreign* X server,
silently and with no error.  That is not hypothetical: it was caught happening
on this machine, with a live server belonging to someone else sitting on the
number our own server had just been given.

The remedy here is structural rather than a check the next test author can
forget.  The X tests run in a child process that, before the test module is
even imported:

* enters a private **user + mount + network** namespace in one ``unshare(2)``;
* mounts a fresh tmpfs over ``/tmp/.X11-unix``, so the directory starts
  *empty* -- the host directory is no longer part of this process's view of
  the filesystem, so no foreign socket can be named *by path*;
* is now in a network namespace of its own, which has no abstract X11
  listener at all until the test's own server binds one, so no foreign socket
  can be named *by abstract name* either; the namespace's only interface is a
  loopback that stays down, so no foreign server can be reached *over TCP*,
  whatever the host's server configuration;
* has ``DISPLAY`` and ``XAUTHORITY`` removed from its environment, so nothing
  it runs inherits a pointer to, or a cookie for, the invoking session's
  display.  :func:`confirm_our_socket` sets ``DISPLAY`` again, and only to the
  number it has just proved is the private server.

If any of that cannot be built the child refuses to run the tests at all.
The parent then reports every test of the module as an **error** naming
:class:`SandboxUnavailable`, not a skip: a module reaches the child only when
its own skip conditions (Xvfb and friends) were satisfied, so an unbuildable
sandbox means coverage that would otherwise have run has been lost.  Set
``KILIX_X11_SANDBOX_OPTIONAL=1`` to turn that into skips, each reason starting
``X11 SANDBOX UNAVAILABLE``; nothing in this repository sets it.

Entering the namespaces needs no privilege, no setuid helper and no
``newuidmap``: ``unshare(2)`` with ``CLONE_NEWUSER`` gives the caller a full
capability set *inside the new user namespace*, which is what makes the mount
and the network namespace legal, and a single-entry identity uid map keeps
every uid the tests observe exactly what it was outside.

What this does *not* cover, named so it is not mistaken for covered: a client
that connects to an explicit filesystem path *outside* ``/tmp/.X11-unix``
(an X proxy's socket somewhere else) is not stopped by any of the above, and
the server's ``/tmp/.X<n>-lock`` file is still created in the shared ``/tmp``.

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
namespaces, and the assertions fail loudly if any of them is ever missing.
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
#: the network namespace the child was started in, recorded before leaving it
OUTER_NETNS_ENV = "KILIX_X11_SANDBOX_OUTER_NETNS"
#: explicit opt-out: an unbuildable sandbox skips instead of erroring
OPTIONAL_ENV = "KILIX_X11_SANDBOX_OPTIONAL"
#: never inherited by the child; DISPLAY is set again only to our own server
SCRUBBED_ENV = ("DISPLAY", "XAUTHORITY")
X11_DIR = "/tmp/.X11-unix"
TOKEN_PROPERTY = "_KILIX_X11_SANDBOX"

_CLONE_NEWNS = 0x00020000
_CLONE_NEWUSER = 0x10000000
_CLONE_NEWNET = 0x40000000
_MS_REC = 0x4000
_MS_PRIVATE = 1 << 18

#: the child could not build the namespaces; the parent reports it loudly
_UNAVAILABLE_EXIT = 78
_CHILD_TIMEOUT = 900


class SandboxUnavailable(Exception):
    """The private namespaces could not be created on this machine."""


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


def _netns_id() -> int:
    return os.stat("/proc/self/ns/net").st_ino


def x11_listeners() -> list[str]:
    """Every listening X11 socket visible in this network namespace.

    ``/proc/net/unix`` lists the unix sockets of the *reading* process's
    network namespace; abstract names are shown with a leading ``@``.
    Reading it opens no connection to anything.
    """
    found = set()
    with open("/proc/net/unix", encoding="utf-8", errors="replace") as handle:
        next(handle, None)
        for line in handle:
            fields = line.split()
            # Num RefCount Protocol Flags Type St Inode Path; the
            # __SO_ACCEPTCON flag (0x10000) marks a listening socket
            if len(fields) >= 8 and int(fields[3], 16) & 0x10000 and \
                    "/tmp/.X11-unix/" in fields[7]:
                found.add(fields[7])
    return sorted(found)


def enter_private_x11_namespace() -> None:
    """Put this process in user+mount+network namespaces with a private X11 dir.

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
    outer_netns = _netns_id()
    # One call, all three: there is no state in which the mount is private
    # and the abstract socket namespace is still the host's.
    _check(libc.unshare(_CLONE_NEWUSER | _CLONE_NEWNS | _CLONE_NEWNET),
           "unshare(CLONE_NEWUSER|CLONE_NEWNS|CLONE_NEWNET)")
    if _netns_id() == outer_netns:
        raise SandboxUnavailable("the network namespace did not change")
    visible = x11_listeners()
    if visible:
        raise SandboxUnavailable(
            f"X11 listeners still visible in the new network namespace: "
            f"{visible}")
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
    for name in SCRUBBED_ENV:
        os.environ.pop(name, None)
    os.environ[OUTER_NETNS_ENV] = str(outer_netns)
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


#: the display number confirm_our_socket last pointed DISPLAY at
_POINTED_DISPLAY: str | None = None


def require_private_x11() -> list[str]:
    """Refuse to start a server unless every route to a foreign one is shut.

    ``/tmp/.X11-unix`` must be a private, empty tmpfs; the network namespace
    must be a private one with no X11 listener in it; and the environment
    must carry no ``XAUTHORITY`` and no ``DISPLAY`` other than one this module
    itself pointed at a server it proved was its own.

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
    outer = os.environ.get(OUTER_NETNS_ENV)
    if not outer or str(_netns_id()) == outer:
        raise RuntimeError(
            "not in a private network namespace: a foreign display's "
            "abstract socket would be reachable by number")
    listeners = x11_listeners()
    if listeners:
        raise RuntimeError(
            f"X11 listeners are visible in this network namespace before "
            f"any server was started: {listeners}")
    if "XAUTHORITY" in os.environ:
        raise RuntimeError("XAUTHORITY was inherited into the sandbox")
    display = os.environ.get("DISPLAY")
    if display is not None and display != _POINTED_DISPLAY:
        raise RuntimeError(
            f"DISPLAY={display!r} was inherited into the sandbox")
    return listing


def confirm_our_socket(number: int, before: list[str]) -> str:
    """Prove the display we are about to open is the server we just started.

    ``before`` is the listing taken by :func:`require_private_x11`, which is
    empty by construction: the directory is a tmpfs this process mounted.  So
    any socket in it now was created by a server this process started, and the
    one we are about to connect to exists as a *filesystem* socket, which is
    the path python-xlib prefers.  The network namespace is private too, so
    every X11 listener visible in it -- abstract names included, which is
    what libX11/xcb try first -- must be this server's own.

    On success ``DISPLAY`` is pointed at this server, and only this one.
    """
    global _POINTED_DISPLAY
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
    ours = {path, "@" + path}
    strangers = [name for name in x11_listeners() if name not in ours]
    if strangers:
        raise RuntimeError(
            f"X11 listeners other than our own are visible: {strangers}")
    _POINTED_DISPLAY = f":{int(number)}"
    os.environ["DISPLAY"] = _POINTED_DISPLAY
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
    env.pop(OUTER_NETNS_ENV, None)
    for name in SCRUBBED_ENV:
        env.pop(name, None)
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
        # This module's own skip conditions held (the proxy class would
        # otherwise have carried a skip across and never got here), so these
        # tests would have run.  Losing them silently is how a CI job stays
        # green while its X coverage vanishes; say so, per test.
        message = (
            f"X11 SANDBOX UNAVAILABLE for {module_name}: the private "
            f"user+mount+network namespaces could not be built, so this "
            f"test was not run: {results}")
        if os.environ.get(OPTIONAL_ENV) == "1":
            raise unittest.SkipTest(f"{message} ({OPTIONAL_ENV}=1)")
        raise SandboxUnavailable(
            f"{message}. Allow unprivileged user namespaces on this host, "
            f"or set {OPTIONAL_ENV}=1 to record these as skips instead")
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
