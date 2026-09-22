"""The path guards' foreign-owner refusal, held by a test instead of a brief.

Three product guards walk a path and refuse it when something on that path
belongs to a user who is neither root nor the caller:

* ``agent_skills.directory`` -- every component of an agent skills root below
  the filesystem root, and the root itself.
* ``app_profiles.prepare_app_command`` -> ``_persistent_profile`` -- every
  ancestor of the operator's persistent browser profile, and the profile.
* ``multiplexer_build.data`` -- the shared native package file it opens.

Until this module none of the three owner clauses was exercised by anything.
Two independent reviews deleted the clause from each guard in turn and the
whole suite stayed green, and a fourth planted defect that merely *widened* one
clause by a single uid passed as well.  Both shapes have to fail here, and two
rules follow from that.

*Assert the capability, not a value.*  Each owner test computes the **set** of
owners a guard accepts and requires it to be exactly the owners that guard is
allowed to trust.  A guard that stops checking accepts every owner swept and
fails; a guard widened to trust one further uid accepts one owner too many and
also fails.  No test here reads a refusal message and no uid is written down:
the owners tried are derived from the kernel, from the surrounding filesystem,
from the local account database and from the calling process.

*Do not require a clean namespace.*  This suite is also run inside
``unshare -c``, where every root-owned directory reports the kernel's overflow
owner, so a guard walking a real absolute path refuses it long before it
reaches anything a test built.  The owner sweeps therefore give every
directory on the walk a safe, caller-owned picture and change the reported
owner of exactly one of them.  The path, the descriptors, the flags, the walk
and the clause are all the product's own; only the ownership the kernel
reports for the named inodes is supplied.

*And then check the instrument against the kernel.*  A sweep built on supplied
ownership can only be trusted as far as the supply is faithful, so
``RealForeignOwnerTests`` runs each guard against ownership nothing supplied:
a child in a new user namespace maps only its own uid, which leaves every
root-owned directory on the machine reporting a genuinely foreign owner
through the guards' own ``fstat`` calls.  Each of those tests carries a
positive control on the same machinery -- a path with no foreign ancestor that
the same guard must accept -- so a sandbox that refused everything could not
read as a pass.

Nothing here relaxes a guard, and no test here passes or fails differently
inside and outside the namespace.
"""
import contextlib
import ctypes
import json
import os
from pathlib import Path
import pwd
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import agent_skills  # noqa: E402
import app_profiles  # noqa: E402
import multiplexer_build  # noqa: E402

PRIVATE = 0o700
GROUP_WRITABLE = 0o770
WORLD_WRITABLE_STICKY = stat.S_ISVTX | 0o777


# --------------------------------------------------------------------------
# Supplied ownership: the instrument the owner sweeps run on.
# --------------------------------------------------------------------------

class _ReportedInfo:
    """A stat result with a different owner and mode; every other field real."""

    __slots__ = ("_info", "st_uid", "st_mode")

    def __init__(self, info, uid, mode):
        self._info = info
        self.st_uid = uid
        self.st_mode = mode

    def __getattr__(self, name):
        return getattr(self._info, name)


@contextlib.contextmanager
def reported_ownership(overrides):
    """Report ``(uid, mode bits)`` for the named inodes and nothing else.

    Keyed by ``(st_dev, st_ino)``, so only the entries a test named are
    affected and every other stat in the process passes straight through.
    """
    real_fstat, real_stat = os.fstat, os.stat

    def reported(info):
        try:
            uid, mode_bits = overrides[(info.st_dev, info.st_ino)]
        except KeyError:
            return info
        return _ReportedInfo(info, uid, (info.st_mode & ~0o7777) | mode_bits)

    os.fstat = lambda *args, **kwargs: reported(real_fstat(*args, **kwargs))
    os.stat = lambda *args, **kwargs: reported(real_stat(*args, **kwargs))
    try:
        yield
    finally:
        os.fstat, os.stat = real_fstat, real_stat


def _components(path):
    current = Path(os.sep)
    yield current
    for part in Path(path).parts[1:]:
        current = current / part
        yield current


def walk_ownership(path, owner, *reported):
    """A neutral ownership picture for everything on *path*, then overrides.

    Every component reports *owner* and a private mode, so no part of the walk
    can be refused for any reason other than the one under test -- which is
    what keeps these tests independent of the namespace the suite runs in.
    Each entry of *reported* is ``(component, uid, mode bits)`` and replaces
    the neutral picture for that one component.
    """
    overrides = {}
    for component in _components(path):
        info = os.stat(component)
        overrides[(info.st_dev, info.st_ino)] = (owner, PRIVATE)
    for component, uid, mode_bits in reported:
        info = os.stat(component)
        overrides[(info.st_dev, info.st_ino)] = (uid, mode_bits)
    return overrides


def unmapped_owner():
    """What the kernel reports for an owner absent from our uid map.

    Inside ``unshare -c`` this is the owner of every root-owned directory on
    the machine, which makes it the one value a guard is most likely to be
    widened to trust in order to make a sandboxed suite go green.  Read from
    the kernel; the fallback is the 16-bit ``uid_t`` sentinel it defaults to.
    """
    try:
        with open("/proc/sys/kernel/overflowuid", encoding="ascii") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return (1 << 16) - 2


def owners_to_try(caller):
    """Every owner these tests put on a path -- derived, never written down.

    A guard may trust root and the caller.  The sweep has to be wide enough
    that widening one by a single further uid is visible, so it carries the
    values a widening would plausibly name: the owner an unmapped user is
    reported as, the owners the surrounding filesystem actually shows, every
    account this machine knows of, and the ``uid_t`` sentinels.
    """
    owners = {0, caller, caller + 1, caller + 2, unmapped_owner()}
    for path in (os.sep, tempfile.gettempdir(), "/usr", os.path.expanduser("~")):
        try:
            owners.add(os.stat(path).st_uid)
        except OSError:
            pass
    try:
        owners.update(entry.pw_uid for entry in pwd.getpwall())
    except (KeyError, OSError):
        pass
    for width in (16, 32):
        owners.update({(1 << width) - 1, (1 << width) - 2})
    return owners


class GuardTestCase(unittest.TestCase):
    """The shape every owner sweep shares: sweep owners, compare the set."""

    def accepted_owners(self, accepts):
        owners = owners_to_try(self.caller)
        # A sweep that tried nothing foreign would accept everything it tried
        # and still look like a pass, so say what it must contain.
        self.assertTrue(owners - {0, self.caller},
                        "the sweep offered the guard no foreign owner")
        self.assertIn(unmapped_owner(), owners,
                      "the sweep must offer the owner an unmapped user gets")
        return {owner for owner in owners if accepts(owner)}

    def assert_trusts_root_and_the_caller_only(self, accepts):
        self.assertEqual(self.accepted_owners(accepts), {0, self.caller})

    def assert_trusts_nobody_but(self, accepts, *trusted):
        self.assertEqual(self.accepted_owners(accepts), set(trusted))

    def assert_no_descriptors_leaked(self, call):
        before = len(os.listdir("/proc/self/fd"))
        call()
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)


class AgentSkillsDirectoryTests(GuardTestCase):
    """``agent_skills.directory`` walking to an agent skills root."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(os.path.realpath(self.temporary.name))
        self.ancestor = self.base / "agents"
        self.ancestor.mkdir(mode=PRIVATE)
        self.root = self.ancestor / "skills"
        self.root.mkdir(mode=PRIVATE)
        self.caller = os.getuid()

    def _accepts(self, overrides):
        with reported_ownership(overrides):
            try:
                with agent_skills.directory(str(self.root)):
                    return True
            except agent_skills.Conflict:
                return False

    def accepts_ancestor_owned_by(self, owner):
        return self._accepts(
            walk_ownership(self.root, self.caller, (self.ancestor, owner, PRIVATE)))

    def accepts_group_writable_ancestor_owned_by(self, owner):
        return self._accepts(walk_ownership(
            self.root, self.caller, (self.ancestor, owner, GROUP_WRITABLE)))

    def accepts_sticky_ancestor_owned_by(self, owner):
        return self._accepts(walk_ownership(
            self.root, self.caller,
            (self.ancestor, owner, WORLD_WRITABLE_STICKY)))

    def accepts_root_owned_by(self, owner):
        return self._accepts(
            walk_ownership(self.root, self.caller, (self.root, owner, PRIVATE)))

    def test_an_ancestor_of_an_agent_root_may_belong_only_to_root_or_the_caller(self):
        self.assert_trusts_root_and_the_caller_only(self.accepts_ancestor_owned_by)

    def test_a_group_writable_ancestor_is_tolerated_for_the_caller_alone(self):
        # The guard documents one tolerance: a caller-owned 0775-style agent
        # root keeps its mode. That tolerance is the caller's own and nobody
        # else's -- not root's, and certainly not a foreign owner's, who would
        # be refused by the owner clause and the write clause together.
        self.assert_trusts_nobody_but(
            self.accepts_group_writable_ancestor_owned_by, self.caller)

    def test_the_sticky_world_writable_exemption_belongs_to_root_alone(self):
        # The exemption exists so that a root-owned sticky directory such as
        # the system temporary directory stays usable. It must not launder any
        # other owner, and it must not launder a world-writable directory the
        # caller happens to own.
        self.assert_trusts_nobody_but(self.accepts_sticky_ancestor_owned_by, 0)

    def test_the_agent_root_itself_must_belong_to_the_caller(self):
        self.assert_trusts_nobody_but(self.accepts_root_owned_by, self.caller)

    def test_the_walk_closes_every_descriptor_it_opened(self):
        self.assert_no_descriptors_leaked(
            lambda: self.accepts_ancestor_owned_by(unmapped_owner()))


class PersistentBrowserProfileTests(GuardTestCase):
    """``app_profiles.prepare_app_command`` walking to a persistent profile."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(os.path.realpath(self.temporary.name))
        self.ancestor = self.base / "browser"
        self.ancestor.mkdir(mode=PRIVATE)
        self.profile = self.ancestor / "profile"
        self.profile.mkdir(mode=PRIVATE)
        self.caller = os.geteuid()

    def _accepts(self, overrides):
        environment = {app_profiles.PERSISTENT_PROFILE_ENV: str(self.profile)}
        with mock.patch.dict(os.environ, environment):
            with reported_ownership(overrides):
                try:
                    argv, disposable = app_profiles.prepare_app_command(
                        ["google-chrome"])
                except RuntimeError:
                    return False
        # Accepting means the configured profile is actually the one handed to
        # the browser, not merely that nothing was raised.
        self.assertIn(f"--user-data-dir={self.profile}", argv)
        self.assertIsNone(disposable)
        return True

    def accepts_ancestor_owned_by(self, owner):
        return self._accepts(walk_ownership(
            self.profile, self.caller, (self.ancestor, owner, PRIVATE)))

    def accepts_group_writable_ancestor_owned_by(self, owner):
        return self._accepts(walk_ownership(
            self.profile, self.caller, (self.ancestor, owner, GROUP_WRITABLE)))

    def accepts_sticky_ancestor_owned_by(self, owner):
        return self._accepts(walk_ownership(
            self.profile, self.caller,
            (self.ancestor, owner, WORLD_WRITABLE_STICKY)))

    def accepts_profile_owned_by(self, owner):
        return self._accepts(walk_ownership(
            self.profile, self.caller, (self.profile, owner, PRIVATE)))

    def test_an_ancestor_of_the_profile_may_belong_only_to_root_or_the_caller(self):
        self.assert_trusts_root_and_the_caller_only(self.accepts_ancestor_owned_by)

    def test_a_group_writable_ancestor_of_the_profile_is_refused(self):
        # Unlike the agent-root walk, this one refuses a group-writable
        # ancestor whoever owns it: another member of that group could swap a
        # component between this check and the browser launch.
        self.assert_trusts_nobody_but(self.accepts_group_writable_ancestor_owned_by)

    def test_the_sticky_world_writable_exemption_belongs_to_root_alone(self):
        self.assert_trusts_nobody_but(self.accepts_sticky_ancestor_owned_by, 0)

    def test_the_profile_itself_must_belong_to_the_caller(self):
        self.assert_trusts_nobody_but(self.accepts_profile_owned_by, self.caller)

    def test_the_walk_closes_every_descriptor_it_opened(self):
        self.assert_no_descriptors_leaked(
            lambda: self.accepts_ancestor_owned_by(unmapped_owner()))


class MultiplexerPackageFileTests(GuardTestCase):
    """``multiplexer_build.data`` opening a shared native package file."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(os.path.realpath(self.temporary.name))
        self.package = self.base / "native-package.json"
        self.package.write_bytes(b'{"schema": "fixture"}')
        self.package.chmod(0o600)
        self.caller = os.geteuid()

    def _accepts(self, overrides):
        with reported_ownership(overrides):
            try:
                content = multiplexer_build.data(str(self.package))
            except ValueError:
                return False
        # Accepting means the bytes came back, not merely that nothing raised.
        self.assertEqual(content, self.package.read_bytes())
        return True

    def accepts_package_owned_by(self, owner):
        return self._accepts(walk_ownership(
            self.package, self.caller, (self.package, owner, 0o600)))

    def test_a_package_file_may_belong_only_to_root_or_the_caller(self):
        self.assert_trusts_root_and_the_caller_only(self.accepts_package_owned_by)

    def test_a_writable_package_file_is_refused_whoever_owns_it(self):
        for writable in (stat.S_IWGRP, stat.S_IWOTH, stat.S_IWGRP | stat.S_IWOTH):
            with self.subTest(writable=oct(writable)):
                self.assert_trusts_nobody_but(lambda owner: self._accepts(
                    walk_ownership(self.package, self.caller,
                                   (self.package, owner, 0o600 | writable))))

    def test_the_read_closes_every_descriptor_it_opened(self):
        self.assert_no_descriptors_leaked(
            lambda: self.accepts_package_owned_by(unmapped_owner()))


class ReportedOwnershipTests(unittest.TestCase):
    """The instrument above, checked before anything is concluded from it.

    Every owner sweep is a set comparison, so a fixture that planted nothing
    would make those tests fail rather than pass. These make the reason legible
    instead of leaving it to be inferred from a failure somewhere else.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(os.path.realpath(self.temporary.name))
        self.named = self.base / "named"
        self.named.mkdir(mode=PRIVATE)
        self.other = self.base / "other"
        self.other.mkdir(mode=PRIVATE)

    def test_the_named_entry_reports_the_owner_asked_for_and_others_do_not(self):
        wanted = unmapped_owner()
        real = os.stat(self.named).st_uid
        self.assertNotEqual(wanted, real, "pick an owner the entry does not have")
        info = os.stat(self.named)
        with reported_ownership({(info.st_dev, info.st_ino): (wanted, PRIVATE)}):
            self.assertEqual(os.stat(self.named).st_uid, wanted)
            self.assertEqual(os.stat(self.other).st_uid, real)
            # The guards read descriptors, not paths, so fstat has to carry the
            # supplied owner too -- and only for the entry that was named.
            descriptor = os.open(self.named, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assertEqual(os.fstat(descriptor).st_uid, wanted)
            finally:
                os.close(descriptor)
            descriptor = os.open(self.other, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assertEqual(os.fstat(descriptor).st_uid, real)
            finally:
                os.close(descriptor)

    def test_every_field_but_the_owner_and_the_mode_survives(self):
        info = os.stat(self.named)
        with reported_ownership({(info.st_dev, info.st_ino): (info.st_uid + 1, PRIVATE)}):
            reported = os.stat(self.named)
            self.assertEqual(
                (reported.st_dev, reported.st_ino, reported.st_size,
                 reported.st_mtime_ns, reported.st_gid),
                (info.st_dev, info.st_ino, info.st_size,
                 info.st_mtime_ns, info.st_gid))
            self.assertTrue(stat.S_ISDIR(reported.st_mode))

    def test_the_real_stat_is_back_once_the_block_ends(self):
        info = os.stat(self.named)
        real_stat, real_fstat = os.stat, os.fstat
        with reported_ownership({(info.st_dev, info.st_ino): (info.st_uid + 1, PRIVATE)}):
            self.assertIsNot(os.stat, real_stat)
        self.assertIs(os.stat, real_stat)
        self.assertIs(os.fstat, real_fstat)
        self.assertEqual(os.stat(self.named).st_uid, info.st_uid)


# --------------------------------------------------------------------------
# Real ownership: the same clause, with nothing supplied.
# --------------------------------------------------------------------------

CLONE_NEWNS = 0x00020000
CLONE_NEWUSER = 0x10000000
MS_REC = 0x4000
MS_PRIVATE = 0x40000

# Somewhere below a directory the caller does not own, and somewhere directly
# below the filesystem root. Both are covered with a private tmpfs inside the
# child's own mount namespace, so the child owns the leaves of both walks and
# the host sees no change at all.
NESTED_MOUNT_POINTS = ("/usr/local", "/usr/share", "/var/lib")
TOP_LEVEL_MOUNT_POINTS = ("/mnt", "/srv", "/opt")


class SandboxUnavailable(Exception):
    """The kernel would not give this child its own ownership picture."""


def _libc():
    handle = ctypes.CDLL(None, use_errno=True)
    handle.unshare.argtypes = [ctypes.c_int]
    handle.unshare.restype = ctypes.c_int
    handle.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                             ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]
    handle.mount.restype = ctypes.c_int
    return handle


def _write_once(path, text):
    """One write, one syscall: the id map files reject a second one."""
    descriptor = os.open(path, os.O_WRONLY)
    try:
        os.write(descriptor, text.encode("ascii"))
    finally:
        os.close(descriptor)


def _mount_private_tmpfs(libc, candidates):
    for point in candidates:
        if not os.path.isdir(point):
            continue
        if libc.mount(b"tmpfs", point.encode(), b"tmpfs", 0,
                      b"mode=0700") == 0:
            return point
    raise SandboxUnavailable(f"no mount point available among {candidates}")


def _enter_a_namespace_of_our_own(outer_uid, outer_gid):
    """Map only this uid, then give the child private mounts to work in.

    A user namespace that maps one uid reports every file owned by anybody
    else -- every root-owned directory on the machine -- as the kernel's
    overflow owner. That is a real foreign owner arriving through the guards'
    own ``fstat`` calls. The mount namespace is what lets the child put a
    directory it owns *underneath* one of those, without touching the host.
    """
    libc = _libc()
    if libc.unshare(CLONE_NEWUSER | CLONE_NEWNS) != 0:
        raise SandboxUnavailable(
            "unshare: " + os.strerror(ctypes.get_errno()))
    _write_once("/proc/self/setgroups", "deny")
    _write_once("/proc/self/uid_map", f"{outer_uid} {outer_uid} 1")
    _write_once("/proc/self/gid_map", f"{outer_gid} {outer_gid} 1")
    if os.getuid() != outer_uid:
        raise SandboxUnavailable("the uid map did not take effect")
    if libc.mount(b"none", b"/", None, MS_REC | MS_PRIVATE, None) != 0:
        raise SandboxUnavailable(
            "could not make mounts private: " + os.strerror(ctypes.get_errno()))
    under_foreign = _mount_private_tmpfs(libc, NESTED_MOUNT_POINTS)
    beside_foreign = _mount_private_tmpfs(libc, TOP_LEVEL_MOUNT_POINTS)
    foreign = os.path.dirname(under_foreign)
    caller = os.getuid()
    sandbox = {
        "caller": caller,
        "foreign_ancestor": foreign,
        "under_foreign": under_foreign,
        "beside_foreign": beside_foreign,
        "owners": {path: os.stat(path).st_uid
                   for path in (os.sep, foreign, under_foreign, beside_foreign)},
        "modes": {path: stat.S_IMODE(os.stat(path).st_mode)
                  for path in (os.sep, foreign, under_foreign, beside_foreign)},
    }
    return sandbox


def in_a_namespace_of_our_own(body):
    """Run *body(sandbox)* in such a child and bring its result back.

    The child never raises into the parent and never writes to the parent's
    streams: it reports through a pipe and leaves with ``os._exit``.
    """
    outer_uid, outer_gid = os.getuid(), os.getgid()
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:                                     # pragma: no cover - child
        os.close(read_fd)
        try:
            payload = ["ok", body(_enter_a_namespace_of_our_own(
                outer_uid, outer_gid))]
        except SandboxUnavailable as error:
            payload = ["unavailable", str(error)]
        except BaseException as error:               # noqa: BLE001 - reported
            payload = ["error", f"{type(error).__name__}: {error}"]
        try:
            os.write(write_fd, json.dumps(payload).encode())
        finally:
            os._exit(0)
    os.close(write_fd)
    chunks = []
    try:
        while True:
            chunk = os.read(read_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read_fd)
        _, status = os.waitpid(pid, 0)
    if status != 0:
        raise AssertionError(f"the sandbox child left with status {status}")
    if not chunks:
        raise AssertionError("the sandbox child reported nothing")
    kind, value = json.loads(b"".join(chunks).decode())
    if kind == "unavailable":
        raise unittest.SkipTest(f"no private user namespace here: {value}")
    if kind != "ok":
        raise AssertionError(f"the sandbox child failed: {value}")
    return value


def _a_foreign_owned_regular_file(caller):
    """A real file on this machine that belongs to neither root nor us.

    Called inside the child, where every root-owned file qualifies. Group and
    other write bits are excluded so that the owner clause is the only reason
    the guard could refuse it.
    """
    candidates = ["/usr/lib/os-release", "/etc/hostname", "/etc/passwd",
                  "/etc/fstab", "/etc/hosts"]
    with contextlib.suppress(OSError):
        candidates.extend(sorted(os.path.join("/etc", name)
                                 for name in os.listdir("/etc"))[:200])
    for candidate in candidates:
        try:
            info = os.stat(candidate, follow_symlinks=False)
        except OSError:
            continue
        if (stat.S_ISREG(info.st_mode) and info.st_uid not in (0, caller)
                and not info.st_mode & 0o022 and 0 < info.st_size < 1 << 20):
            return candidate
    raise SandboxUnavailable("no foreign-owned readable regular file found")


class RealForeignOwnerTests(unittest.TestCase):
    """Each guard against ownership the kernel reports and nothing supplies.

    Every test here is a pair. The refusal is the finding; the acceptance
    beside it is the control, because a sandbox that had broken every path
    would refuse everything and read as a pass without it.
    """

    def assert_sandbox_is_what_it_claims(self, sandbox):
        owners, modes = sandbox["owners"], sandbox["modes"]
        caller = sandbox["caller"]
        foreign = sandbox["foreign_ancestor"]
        self.assertNotIn(owners[foreign], (0, caller),
                         "the sandbox produced no foreign-owned ancestor")
        self.assertEqual(owners[sandbox["under_foreign"]], caller)
        self.assertEqual(owners[sandbox["beside_foreign"]], caller)
        # Nothing on either walk may be refusable for a reason other than the
        # owner, or the pair below would not isolate the clause under test.
        for path in (os.sep, foreign):
            self.assertFalse(modes[path] & 0o022,
                             f"{path} is writable by others in the sandbox")
        for path in (sandbox["under_foreign"], sandbox["beside_foreign"]):
            self.assertEqual(modes[path], PRIVATE)

    def test_the_agent_root_walk_refuses_a_really_foreign_ancestor(self):
        def body(sandbox):
            verdicts = {}
            for key, parent in (("below_a_foreign_ancestor", sandbox["under_foreign"]),
                                ("below_no_foreign_ancestor", sandbox["beside_foreign"])):
                target = os.path.join(parent, "skills")
                os.mkdir(target, PRIVATE)
                try:
                    with agent_skills.directory(target):
                        verdicts[key] = "accepted"
                except agent_skills.Conflict:
                    verdicts[key] = "refused"
            return {"sandbox": sandbox, "verdicts": verdicts}

        result = in_a_namespace_of_our_own(body)
        self.assert_sandbox_is_what_it_claims(result["sandbox"])
        self.assertEqual(result["verdicts"]["below_a_foreign_ancestor"], "refused")
        self.assertEqual(result["verdicts"]["below_no_foreign_ancestor"], "accepted")

    def test_the_browser_profile_walk_refuses_a_really_foreign_ancestor(self):
        def body(sandbox):
            def verdict(path):
                os.environ[app_profiles.PERSISTENT_PROFILE_ENV] = path
                try:
                    argv, disposable = app_profiles.prepare_app_command(
                        ["google-chrome"])
                except RuntimeError:
                    return "refused"
                if disposable is not None:
                    return "fell back to a disposable profile"
                if f"--user-data-dir={path}" not in argv:
                    return "accepted a different profile"
                return "accepted"

            verdicts = {"below_a_foreign_ancestor": verdict(
                os.path.join(sandbox["under_foreign"], "profile"))}
            # Every absolute path in this namespace has a foreign filesystem
            # root above it, and this walk checks that root too, so the
            # control needs a root of the caller's own to stand on.
            os.chroot(sandbox["beside_foreign"])
            os.chdir(os.sep)
            verdicts["below_no_foreign_ancestor"] = verdict("/profile")
            return {"sandbox": sandbox, "verdicts": verdicts}

        result = in_a_namespace_of_our_own(body)
        self.assert_sandbox_is_what_it_claims(result["sandbox"])
        self.assertEqual(result["verdicts"]["below_a_foreign_ancestor"], "refused")
        self.assertEqual(result["verdicts"]["below_no_foreign_ancestor"], "accepted")

    def test_the_package_read_refuses_a_really_foreign_file(self):
        def body(sandbox):
            foreign_file = _a_foreign_owned_regular_file(sandbox["caller"])
            ours = os.path.join(sandbox["under_foreign"], "native-package.json")
            with open(ours, "wb") as handle:
                handle.write(b'{"schema": "fixture"}')
            os.chmod(ours, 0o600)

            def verdict(path):
                try:
                    return ("accepted"
                            if multiplexer_build.data(path) == _read(path)
                            else "accepted the wrong bytes")
                except ValueError:
                    return "refused"

            def _read(path):
                with open(path, "rb") as handle:
                    return handle.read()

            return {
                "sandbox": sandbox,
                "foreign_file": foreign_file,
                "foreign_file_owner": os.stat(foreign_file).st_uid,
                "foreign_file_mode": stat.S_IMODE(os.stat(foreign_file).st_mode),
                "verdicts": {"a_foreign_file": verdict(foreign_file),
                             "our_own_file": verdict(ours)},
            }

        result = in_a_namespace_of_our_own(body)
        self.assert_sandbox_is_what_it_claims(result["sandbox"])
        self.assertNotIn(result["foreign_file_owner"],
                         (0, result["sandbox"]["caller"]),
                         "the file offered to the guard was not foreign")
        self.assertFalse(result["foreign_file_mode"] & 0o022,
                         "the file offered to the guard was writable anyway")
        self.assertEqual(result["verdicts"]["a_foreign_file"], "refused")
        self.assertEqual(result["verdicts"]["our_own_file"], "accepted")


class NamespaceSandboxTests(unittest.TestCase):
    """The second instrument, checked the same way as the first."""

    def test_the_child_sees_a_foreign_root_and_the_parent_sees_no_change(self):
        before = {path: (os.stat(path).st_uid,
                         stat.S_IMODE(os.stat(path).st_mode))
                  for path in (os.sep,) + NESTED_MOUNT_POINTS
                  + TOP_LEVEL_MOUNT_POINTS if os.path.isdir(path)}
        result = in_a_namespace_of_our_own(lambda sandbox: sandbox)
        self.assertNotIn(result["owners"][result["foreign_ancestor"]],
                         (0, result["caller"]))
        # The child mounted over real directories. Nothing it did may survive
        # into this process's view of the filesystem.
        after = {path: (os.stat(path).st_uid,
                        stat.S_IMODE(os.stat(path).st_mode))
                 for path in before}
        self.assertEqual(after, before)

    def test_the_child_reports_a_failure_instead_of_passing_quietly(self):
        def body(sandbox):
            raise ZeroDivisionError("planted")

        with self.assertRaises(AssertionError):
            in_a_namespace_of_our_own(body)


if __name__ == "__main__":
    unittest.main()
