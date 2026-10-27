"""Bounded inert platform protocols; imports real stdlib production modules.

All handles are non-integer model identities. No D-Bus extension, native bus,
kernel filesystem, service, SDK app or signalling operation is executed.
"""
import errno
import array
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import types
import unittest

TOKEN = "a" * 32
ROOT_GROUP = "/user.slice/user-1001.slice/user@1001.service"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Clock:
    def __init__(self): self.value = 0
    def monotonic(self): return self.value
    def sleep(self, amount): self.value += amount


class Manager:
    """Independent manager model: scope creation moves only the supplied child."""
    def __init__(self, clock):
        self.clock, self.events, self.units = clock, [], {}
        self.authenticated = True
        self.costs, self.overrides = {}, {}
        self.after_create_error = self.close_error = None
        self.member_path = None
    def event(self, name, *args):
        self.events.append((name, *args))
        self.clock.value += self.costs.get(name, 0)
    def connect(self, _deadline): self.event("connect"); return ROOT_GROUP
    def attest(self, _deadline):
        self.event("attest")
        if not self.authenticated: raise PermissionError("inert stale manager authority")
    def unit(self, name, _deadline):
        self.event("unit", name)
        value = self.units.get(name)
        return dict(value, **self.overrides) if value is not None else None
    def create(self, name, description, pid, _deadline):
        self.event("create", name, pid)
        if name in self.units: raise FileExistsError(name)
        self.units[name] = {"Id": name, "Description": description, "Transient": True,
                            "Delegate": True, "KillMode": "control-group", "SendSIGKILL": True,
                            "TimeoutStopUSec": 1500000, "CollectMode": "inactive-or-failed",
                            "InvocationID": b"I" * 16, "ControlGroup": ROOT_GROUP + "/app.slice/" + name,
                            "ActiveState": "active", "object_path": "/inert/owned_scope"}
        if self.after_create_error: raise self.after_create_error
    def member(self, pid, _deadline): self.event("member", pid); return self.member_path or "/inert/owned_scope"
    def stop(self, name, _deadline):
        self.event("stop", name)
        self.units[name].update(ActiveState="inactive", ControlGroup="")
    def close(self):
        self.event("connections-close")
        if self.close_error: raise self.close_error


class Boundary:
    identity = (11, 101, 11, 102)
    def __init__(self, events): self.events, self.retired, self.fail = events, False, None
    def fds(self): return tuple(H.FD("boundary-" + str(i)) for i in range(8))
    def retire(self):
        self.events.append(("boundary-retire",))
        if self.fail: raise self.fail
        self.retired = True
    def empty(self): return True
    def kill(self): self.events.append(("boundary-cull",))


class Filesystem:
    def __init__(self, events):
        self.events, self.fail, self.close_error = events, None, None
        self.value = Boundary(events)
    def open(self, path, pid):
        self.events.append(("open-root", path, pid))
        if self.fail == "open": raise PermissionError("inert root permission")
        return H.FD("delegated-root")
    def boundary(self, root, instance):
        self.events.append(("create-boundary", root, instance))
        if self.fail == "boundary": raise OSError("inert missing cgroup.kill")
        return self.value
    def close(self, root):
        self.events.append(("root-close", root))
        if self.close_error: raise self.close_error


class Ops:
    def __init__(self): self.dead = False
    def ready(self, _fd): return self.dead


def fixture(source):
    p = source.platform
    clock = Clock()
    manager = Manager(clock)
    fs = Filesystem(manager.events)
    scope = p.PlatformScope(TOKEN, manager, fs)
    return scope, manager, fs, Ops(), clock


class PlatformTests(unittest.TestCase):
    source = None
    __unittest_skip__ = True
    __unittest_skip_why__ = "dedicated env-i inert runner only"
    def run_scope(self, scope, ops, clock):
        with H.replace(self.source.platform, time=clock):
            scope.prepare()
            return scope.attach(types.SimpleNamespace(pid=1000000001), H.FD("exact-owner"), ops)

    def test_automatic_default_scope(self):
        scope, manager, fs, ops, clock = fixture(self.source)
        self.assertIs(self.run_scope(scope, ops, clock), fs.value)
        self.assertEqual([e[2] for e in manager.events if e[0] == "create"], [1000000001])
        self.assertIsNotNone(scope.identity)
        self.assertIn(("create-boundary", H.FD("delegated-root"), TOKEN), manager.events)
        self.assertLess([e[0] for e in manager.events].index("create"), [e[0] for e in manager.events].index("create-boundary"))

    def test_ambient_directory_cannot_grant_authority(self):
        for env in ({}, {"KILIX_PRIVATE_BUS_CGROUP_ROOT": "/inert/writable"},
                    {"KILIX_PRIVATE_BUS_CGROUP_ROOT": ROOT_GROUP}):
            with self.subTest(env=env), self.assertRaises(RuntimeError):
                self.source.own.CgroupBoundary.create(env, TOKEN)

    def test_scope_properties_are_required(self):
        for key, value in (("Delegate", False), ("Transient", False), ("KillMode", "process"),
                           ("SendSIGKILL", False), ("Id", "unrelated.scope"), ("Description", "unbound"),
                           ("InvocationID", b""), ("InvocationID", b"\0" * 16),
                           ("TimeoutStopUSec", 90000000), ("CollectMode", "inactive"),
                           ("ControlGroup", "/user.slice/other-user/attacker.scope")):
            scope, manager, fs, ops, clock = fixture(self.source)
            manager.overrides[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(self.source.platform.PlatformUnsupported):
                self.run_scope(scope, ops, clock)
            self.assertNotIn("create-boundary", [e[0] for e in manager.events])

    def test_wrong_owner_membership_refuses_handoff(self):
        scope, manager, _fs, ops, clock = fixture(self.source)
        manager.member_path = "/inert/unrelated_scope"
        with self.assertRaises(self.source.platform.PlatformUnsupported): self.run_scope(scope, ops, clock)
        self.assertNotIn("create-boundary", [e[0] for e in manager.events])

    def test_dead_gated_owner_never_creates_scope(self):
        scope, manager, _fs, ops, clock = fixture(self.source)
        ops.dead = True
        with self.assertRaises(self.source.platform.PlatformUnsupported): self.run_scope(scope, ops, clock)
        self.assertFalse(scope.attempted)
        self.assertNotIn("create", [e[0] for e in manager.events])

    def test_stale_manager_blocks_bootstrap(self):
        scope, manager, _fs, ops, clock = fixture(self.source)
        manager.authenticated = False
        with self.assertRaises(self.source.platform.PlatformUnsupported): self.run_scope(scope, ops, clock)
        self.assertNotIn("create", [e[0] for e in manager.events])

    def test_bootstrap_deadline_does_not_release_boundary(self):
        for phase in ("connect", "create", "member"):
            scope, manager, _fs, ops, clock = fixture(self.source)
            manager.costs[phase] = 5
            with self.subTest(phase=phase), self.assertRaises(self.source.platform.PlatformUnsupported):
                self.run_scope(scope, ops, clock)

    def test_permission_and_interface_failures_are_retryable_resources(self):
        for failure in ("open", "boundary"):
            scope, manager, fs, ops, clock = fixture(self.source)
            fs.fail = failure
            with self.subTest(failure=failure), self.assertRaises(self.source.platform.PlatformUnsupported) as raised:
                self.run_scope(scope, ops, clock)
            self.assertIs(raised.exception.resource, scope)
            with H.replace(self.source.platform, time=clock): scope.retire()
            self.assertTrue(scope.closed)
            self.assertEqual([e for e in manager.events if e[0] == "stop"], [("stop", scope.name)])

    def test_ambiguous_create_response_cleans_only_this_scope(self):
        scope, manager, _fs, ops, clock = fixture(self.source)
        manager.after_create_error = TimeoutError("inert lost successful creation response")
        with self.assertRaises(self.source.platform.PlatformUnsupported): self.run_scope(scope, ops, clock)
        manager.units["unrelated.scope"] = {"live": True}
        with H.replace(self.source.platform, time=clock): scope.retire()
        self.assertTrue(manager.units["unrelated.scope"]["live"])
        self.assertEqual([e for e in manager.events if e[0] == "stop"], [("stop", scope.name)])

    def test_preexisting_scope_is_not_owned_or_stopped(self):
        scope, manager, _fs, _ops, clock = fixture(self.source)
        manager.units[scope.name] = {"existing": True}
        with H.replace(self.source.platform, time=clock):
            with self.assertRaises(self.source.platform.PlatformUnsupported): scope.prepare()
            scope.retire()
        self.assertFalse(scope.attempted)
        self.assertNotIn("stop", [e[0] for e in manager.events])

    def test_replaced_invocation_never_stops_replacement(self):
        scope, manager, _fs, ops, clock = fixture(self.source)
        self.run_scope(scope, ops, clock)
        manager.overrides["InvocationID"] = b"R" * 16
        with H.replace(self.source.platform, time=clock), self.assertRaises(self.source.own.CleanupIncomplete): scope.retire()
        self.assertFalse(scope.closed)
        self.assertNotIn("stop", [e[0] for e in manager.events])
        manager.overrides.clear()
        with H.replace(self.source.platform, time=clock): scope.retire()
        self.assertTrue(scope.closed)

    def test_scope_path_replacement_retains_resources(self):
        scope, manager, _fs, ops, clock = fixture(self.source)
        self.run_scope(scope, ops, clock)
        manager.overrides["ControlGroup"] = ROOT_GROUP + "/replacement.scope"
        with H.replace(self.source.platform, time=clock), self.assertRaises(self.source.own.CleanupIncomplete): scope.retire()
        self.assertIsNotNone(scope.root)
        self.assertNotIn("stop", [e[0] for e in manager.events])

    def test_partial_retirement_keeps_handles_and_retries(self):
        for phase in ("boundary", "root", "connections"):
            scope, manager, fs, ops, clock = fixture(self.source)
            self.run_scope(scope, ops, clock)
            error = OSError("inert pre-operation release failure")
            if phase == "boundary": fs.value.fail = error
            if phase == "root": fs.close_error = error
            if phase == "connections": manager.close_error = error
            with self.subTest(phase=phase), H.replace(self.source.platform, time=clock):
                with self.assertRaises(self.source.own.CleanupIncomplete): scope.retire()
                self.assertFalse(scope.closed)
                fs.value.fail = fs.close_error = manager.close_error = None
                scope.retire()
                self.assertTrue(scope.closed)
                events = list(manager.events)
                scope.retire()
                self.assertEqual(manager.events, events)

    def test_inactive_with_live_group_is_not_retirement(self):
        scope, manager, _fs, ops, clock = fixture(self.source)
        self.run_scope(scope, ops, clock)
        manager.overrides["ActiveState"] = "failed"
        with H.replace(self.source.platform, time=clock), self.assertRaises(self.source.own.CleanupIncomplete): scope.retire()
        self.assertFalse(scope.closed)
        self.assertIsNotNone(scope.root)

    def test_manager_disappearance_keeps_resources_for_retry(self):
        scope, manager, _fs, ops, clock = fixture(self.source)
        self.run_scope(scope, ops, clock)
        manager.authenticated = False
        with H.replace(self.source.platform, time=clock), self.assertRaises(self.source.own.CleanupIncomplete): scope.retire()
        self.assertIsNotNone(scope.root)
        self.assertFalse(scope.closed)

    def test_path_walk_does_not_accept_aliases(self):
        for value in ("", "/", "relative", "/a/../b", "/a//b", "/a/./b", "/a\0b", "/" + "x" * 4097):
            with self.subTest(value=value[:40]), self.assertRaises(ValueError): self.source.platform.components(value)

    def test_no_scope_name_input_injection(self):
        for token in ("x", "a" * 31, "a" * 33, "../scope", "A" * 32, "a" * 31 + "\n"):
            with self.assertRaises(ValueError): self.source.platform.PlatformScope(token, Manager(Clock()), Filesystem([]))

    def transport(self):
        p = self.source.platform
        value = p.SystemdTransport.__new__(p.SystemdTransport)
        value.uid = 1001
        value.record = (":root.manager", 1000000007, b"U" * 16, ROOT_GROUP)
        value.user = types.SimpleNamespace(get_unix_fd=lambda: H.FD("manager-peer"))
        return value

    def test_kernel_peer_credentials_match_root_manager_record(self):
        p = self.source.platform
        for pid, uid, invocation in ((1000000007, 1001, b"U" * 16), (1000000008, 1001, b"U" * 16),
                                     (1000000007, 1002, b"U" * 16), (1000000007, 1001, b"R" * 16)):
            value = self.transport()
            value._root_record = lambda _d, inv=invocation: (":root.manager", 1000000007, inv, ROOT_GROUP)
            class Peer:
                def __enter__(self): return self
                def __exit__(self, *_a): pass
                def getsockopt(self, *_a): return struct.pack("3i", pid, uid, uid)
            fake_socket = types.SimpleNamespace(fromfd=lambda *_a: Peer(), AF_UNIX=1, SOCK_STREAM=1, SOL_SOCKET=1, SO_PEERCRED=17)
            with self.subTest(pid=pid, uid=uid, invocation=invocation), H.replace(p, socket=fake_socket):
                if (pid, uid, invocation) == (1000000007, 1001, b"U" * 16): value.attest(1)
                else:
                    with self.assertRaises(p.PlatformUnsupported): value.attest(1)

    def test_root_manager_owner_and_user_invocation_are_authenticated(self):
        p = self.source.platform
        for root_uid, root_pid, delegated, active, second_owner in (
                (0, 1, True, "active", ":root.manager"), (1001, 1, True, "active", ":root.manager"),
                (0, 8, True, "active", ":root.manager"), (0, 1, False, "active", ":root.manager"),
                (0, 1, True, "inactive", ":root.manager"), (0, 1, True, "active", ":replaced")):
            value = self.transport()
            value.system = object()
            calls = []
            def call(_c, _dest, _path, _iface, method, _args, _deadline):
                calls.append(method)
                if method == "GetNameOwner": return ":root.manager" if calls.count(method) == 1 else second_owner
                if method == "GetConnectionUnixUser": return root_uid
                if method == "GetConnectionUnixProcessID": return root_pid
                if method == "GetUnit": return "/inert/user_service"
                raise AssertionError(method)
            value._call = call
            value._properties = lambda _c, _dest, _path, interface, _d: (
                {"Id": "user@1001.service", "ActiveState": active, "InvocationID": b"U" * 16}
                if interface == p.UNIT else {"Delegate": delegated, "MainPID": 1000000007, "ControlGroup": ROOT_GROUP})
            good = (root_uid, root_pid, delegated, active, second_owner) == (0, 1, True, "active", ":root.manager")
            if good: self.assertEqual(value._root_record(1), value.record)
            else:
                with self.assertRaises(p.PlatformUnsupported): value._root_record(1)

    def test_connect_uses_only_installed_manager_endpoints(self):
        p = self.source.platform
        value = self.transport()
        events = []
        value._safe_directory = lambda path, uid: events.append((path, uid))
        value.BusConnection = lambda address: events.append(address) or object()
        value.Connection = lambda address: events.append(address) or value.user
        value._root_record = lambda _d: value.record
        value.attest = lambda _d: None
        value._call = lambda *_a: "252.40-1"
        with H.replace(p, os=types.SimpleNamespace(stat=lambda *_a, **_kw: types.SimpleNamespace(st_mode=stat.S_IFSOCK))):
            self.assertEqual(value.connect(1), ROOT_GROUP)
        self.assertIn("unix:path=/run/dbus/system_bus_socket", events)
        self.assertIn("unix:path=/run/user/1001/systemd/private", events)
        self.assertEqual([e for e in events if isinstance(e, str)], ["unix:path=/run/dbus/system_bus_socket", "unix:path=/run/user/1001/systemd/private"])

    def test_unsafe_runtime_directory_is_unsupported(self):
        value = self.transport()
        for mode, uid in ((stat.S_IFLNK | 0o777, 1001), (stat.S_IFDIR | 0o777, 1001), (stat.S_IFDIR | 0o700, 1002)):
            with H.replace(self.source.platform, os=types.SimpleNamespace(stat=lambda *_a, **_kw: types.SimpleNamespace(st_mode=mode, st_uid=uid))):
                with self.assertRaises(self.source.platform.PlatformUnsupported): value._safe_directory("/inert/runtime", 1001)

    def test_cgroup_scope_prune_is_dead_object_evidence(self):
        own = self.source.own
        closed, touched = [], []
        def dead(*_a): raise OSError(errno.ENODEV, "inert removed kernfs node")
        fake_os = types.SimpleNamespace(fstat=lambda _f: types.SimpleNamespace(st_dev=11, st_ino=101),
            lseek=lambda *_a: None, SEEK_SET=0, read=dead, write=dead, close=closed.append,
            stat=lambda *_a, **_kw: touched.append("path-stat"), rmdir=lambda *_a, **_kw: touched.append("path-delete"))
        with H.replace(own, os=fake_os):
            boundary = own.CgroupBoundary(H.FD("directory"), H.FD("join"), H.FD("kill"), H.FD("events"), parent=H.FD("root"), name="pruned")
            self.assertTrue(boundary.empty())
            boundary.kill()
            boundary.retire()
            self.assertTrue(boundary.retired)
        self.assertEqual(touched, [], "dead pinned object never grants authority over a replacement pathname")
        self.assertEqual(len(closed), 5)

    def test_permission_or_missing_path_is_not_dead_group(self):
        own = self.source.own
        for code in (errno.EACCES, errno.ENOENT, errno.EIO, errno.EBADF):
            def error(*_a): raise OSError(code, "inert read failure")
            fake_os = types.SimpleNamespace(fstat=lambda _f: types.SimpleNamespace(st_dev=11, st_ino=101),
                lseek=lambda *_a: None, SEEK_SET=0, read=error)
            with self.subTest(code=code), H.replace(own, os=fake_os):
                boundary = own.CgroupBoundary(H.FD("directory"), H.FD("join"), H.FD("kill"), H.FD("events"))
                with self.assertRaises(OSError): boundary.empty()
                self.assertFalse(boundary.removed)

    def test_delivery_preflight_runs_without_platform_side_effects(self):
        p = self.source.platform
        for bad in (None, *p.PRIVATE_BUS_MODULES):
            calls = []
            def file_stat(path, **_kw):
                calls.append(path)
                if path.endswith("/" + str(bad)): raise FileNotFoundError(path)
                return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644)
            if bad is None: p.validate_source_delivery("/inert/installed/config/kilix_sdk", types.SimpleNamespace(stat=file_stat))
            else:
                with self.assertRaises(FileNotFoundError): p.validate_source_delivery("/inert/installed/config/kilix_sdk", types.SimpleNamespace(stat=file_stat))
            self.assertGreater(len(calls), 0)
        for mode in (stat.S_IFLNK, stat.S_IFDIR):
            with self.assertRaises(RuntimeError): p.validate_source_delivery("/inert", types.SimpleNamespace(stat=lambda *_a, **_kw: types.SimpleNamespace(st_mode=mode)))

    def test_installer_dependency_and_source_delivery_model(self):
        # Parse inert source data; this checks delivery intent, not installed apt
        # or execution of the shell installer. Runtime acceptance stays open.
        pleb = (PLEB / "lib/install.sh").read_text()
        os_source = (OS_ROOT / "provision/install-deps.sh").read_text()
        for package in ("systemd", "libpam-systemd", "python3-dbus", "dbus-user-session"):
            self.assertIn(package, pleb.split("ensure_system_deps() {", 1)[1].split("deps=(", 1)[1].split(")", 1)[0].split())
            group = next(line for line in os_source.splitlines() if '"desktop notifications + portal|' in line)
            self.assertIn(package, group.strip().strip('"').split("|", 1)[1].split())
        install = pleb.split("do_install() {", 1)[1].split("\ndo_uninstall", 1)[0]
        self.assertLess(install.index("--check-source-delivery"), install.index('"$PLEB_BIN_SRC" "$SESSION_BIN_DST"'))
        self.assertNotIn("KILIX_PRIVATE_BUS_CGROUP_ROOT=", pleb)

    def test_typed_transient_request_moves_one_owned_pid(self):
        p=self.source.platform
        value=self.transport()
        class Typed:
            def __init__(self,value,signature=None): self.value,self.signature=value,signature
        value.dbus=types.SimpleNamespace(Array=Typed,UInt32=Typed,UInt64=Typed,String=Typed,Boolean=Typed)
        calls=[]
        value._call=lambda *args:calls.append(args)
        value.create('owned.scope','instance-description',1000000001,1)
        connection,destination,path,interface,method,args,deadline=calls[0]
        self.assertIs(connection,value.user)
        self.assertIsNone(destination)
        self.assertEqual((path,interface,method),(p.PATH,p.MANAGER,'StartTransientUnit'))
        name,mode,properties,aux=args
        self.assertEqual((name,mode),('owned.scope','fail'))
        self.assertEqual(properties.signature,'(sv)')
        values=dict(properties.value)
        self.assertEqual(values['PIDs'].signature,'u')
        self.assertEqual([v.value for v in values['PIDs'].value],[1000000001])
        self.assertTrue(values['Delegate'].value)
        self.assertEqual(values['KillMode'].value,'control-group')
        self.assertEqual(aux.value,[])
        self.assertNotIn('User',values)
        self.assertNotIn('Environment',values)

    def delegation(self,mode='normal'):
        p=self.source.platform
        value=p.LinuxDelegation()
        opened,closed=[],[]
        fail=True
        def open_file(name,flags,**kw):
            if mode=='permission' and name=='app.slice': raise PermissionError('inert path permission')
            fd=H.FD(name,len(opened));opened.append((fd,flags,kw));return fd
        def close(fd):
            closed.append(fd)
            if mode=='preop' and fail and fd.identity=='app.slice':
                raise PermissionError('inert pre-operation directory close')
            if mode=='late' and fd.identity=='/sys/fs/cgroup':
                raise self.source.own.DescriptorReleased('inert late directory close')
        def read(*_a):
            return b'1000000001\n' if mode!='foreign-member' else b'1000000001\n1000000002\n'
        fs=types.SimpleNamespace(open=open_file,read=read,
            fstat=lambda fd:types.SimpleNamespace(st_dev=11,st_ino=fd.serial+1,st_mode=0o100600),
            O_RDONLY=0,O_DIRECTORY=1,O_NOFOLLOW=2,O_CLOEXEC=4)
        error=root=None
        original=self.source.own.CgroupBoundary.__dict__['_filesystem']
        try:
            self.source.own.CgroupBoundary._filesystem=staticmethod(lambda _fd:None)
            with H.replace(p,os=fs,close_descriptor=close):
                try: root=value.open(ROOT_GROUP+'/app.slice/owned.scope',1000000001)
                except Exception as problem: error=problem
                retained=list(value.pending)
                fail=False
                value.close_pending()
        finally: self.source.own.CgroupBoundary._filesystem=original
        return value,opened,closed,root,error,retained

    def test_delegation_walk_uses_pinned_relative_nofollow_handles(self):
        value,opened,closed,root,error,_retained=self.delegation()
        self.assertIsNone(error)
        self.assertEqual(root,opened[-2][0])
        self.assertFalse(value.pending)
        self.assertNotIn(root,closed)
        self.assertEqual(len(closed),len(opened)-1)
        self.assertEqual(opened[0][0].identity,'/sys/fs/cgroup')
        self.assertTrue(all(flags&2 and flags&4 for _fd,flags,_kw in opened))
        for index,(_fd,_flags,kwargs) in enumerate(opened[1:],1):
            self.assertEqual(kwargs['dir_fd'],opened[index-1][0])

    def test_delegation_permission_and_extra_member_fail_without_leaks(self):
        for mode in ('permission','foreign-member'):
            _value,opened,closed,root,error,retained=self.delegation(mode)
            with self.subTest(mode=mode):
                self.assertIsNotNone(error)
                self.assertIsNone(root)
                self.assertEqual(retained,[])
                self.assertEqual(set(closed),{fd for fd,_flags,_kw in opened})
                self.assertEqual(len(closed),len(set(closed)))

    def test_delegation_late_close_never_reuses_number(self):
        _value,opened,closed,root,error,retained=self.delegation('late')
        self.assertIsInstance(error,self.source.own.DescriptorReleased)
        self.assertIsNone(root)
        self.assertEqual(retained,[])
        self.assertEqual(set(closed),{fd for fd,_flags,_kw in opened})
        self.assertEqual(len(closed),len(set(closed)))

    def test_delegation_preoperation_close_retains_retry_authority(self):
        value,_opened,closed,root,error,retained=self.delegation('preop')
        self.assertIsInstance(error,self.source.own.CleanupIncomplete)
        self.assertIs(error.resource,value)
        self.assertIsNone(root)
        self.assertEqual([fd.identity for fd in retained],['app.slice'])
        self.assertFalse(value.pending)
        self.assertEqual([fd.identity for fd in closed].count('app.slice'),3)

    def bootstrap(self,mode='normal'):
        bus=self.source.bus
        fds=list(range(400001,400009))  # Protocol integers; never native handles.
        handed,closed,events=[],[],[]
        envelope={'instance':TOKEN,'boundary':[11,101,11,102]}
        if mode=='nonce': envelope['instance']='b'*32
        if mode=='identity': envelope['boundary'][-1]=999
        transfer=fds[:7] if mode=='count' else fds
        class Control:
            closed=False
            def recvmsg(self,_size,_space,flags):
                events.append(('recv-flags',flags))
                return json.dumps(envelope).encode(),[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',transfer).tobytes())],(socket.MSG_CTRUNC if mode=='truncated' else 0),None
            def close(self): self.closed=True
        control=Control()
        def select_values(*_a):
            return ([control,H.FD('parent')] if mode=='sdk-dead' else [] if mode=='timeout' else [control],[],[])
        def owner(*args): handed.append(args);events.append(('owner',));return 'inert-handoff'
        def prctl(*_a): events.append(('clear-parent-death',));return int(mode=='parent-death-failure')
        fake_os=types.SimpleNamespace(close=closed.append)
        own_os=types.SimpleNamespace(fstat=lambda fd:types.SimpleNamespace(st_dev=11,st_ino=101 if fd==fds[0] else 102))
        error=result=None
        with H.replace(bus,bind_parent=lambda _pid:events.append(('bind-parent',)),bus_owner=owner,os=fake_os,
                socket=types.SimpleNamespace(socket=lambda **_kw:control,SOL_SOCKET=socket.SOL_SOCKET,SCM_RIGHTS=socket.SCM_RIGHTS,
                    CMSG_SPACE=lambda n:n,MSG_CMSG_CLOEXEC=socket.MSG_CMSG_CLOEXEC,MSG_TRUNC=socket.MSG_TRUNC,MSG_CTRUNC=socket.MSG_CTRUNC),
                select=types.SimpleNamespace(select=select_values),ctypes=types.SimpleNamespace(CDLL=lambda *_a,**_kw:types.SimpleNamespace(prctl=prctl),get_errno=lambda:1)),H.replace(self.source.own,os=own_os):
            try: result=bus.bootstrap_owner(1000000099,H.FD('parent'),31,32,33,34,TOKEN)
            except Exception as problem:error=problem
        return handed,closed,events,control,error,result

    def test_bootstrap_nonce_identity_cloexec_and_owner_handoff(self):
        handed,closed,events,control,error,result=self.bootstrap()
        self.assertIsNone(error)
        self.assertEqual(result,'inert-handoff')
        self.assertEqual(len(handed),1)
        self.assertEqual(handed[0][5:13],tuple(range(400001,400009)))
        self.assertEqual(handed[0][-1],TOKEN)
        self.assertEqual(closed,[],'accepted FDs transfer to the owner rather than being released twice')
        self.assertTrue(control.closed)
        self.assertEqual(events[0],('bind-parent',))
        self.assertIn(('recv-flags',socket.MSG_CMSG_CLOEXEC),events)
        self.assertLess(events.index(('clear-parent-death',)),events.index(('owner',)))

    def test_bootstrap_rejects_invalid_transfer_and_releases_only_received_fds(self):
        for mode in ('nonce','identity','count','truncated','parent-death-failure','sdk-dead','timeout'):
            handed,closed,events,control,error,_result=self.bootstrap(mode)
            with self.subTest(mode=mode):
                self.assertIsNotNone(error)
                self.assertEqual(handed,[])
                expected=[] if mode in ('sdk-dead','timeout') else list(range(400001,400008 if mode=='count' else 400009))
                self.assertEqual(closed,expected)
                self.assertTrue(control.closed)

    def test_boundary_sender_binds_nonce_identity_and_eight_fds(self):
        calls=[]
        class Control:
            short=False
            def sendmsg(self,data,ancillary):
                calls.append((data,ancillary))
                return len(data[0])-int(self.short)
        boundary=types.SimpleNamespace(identity=(11,101,11,102),fds=lambda:tuple(range(400001,400009)))
        control=Control()
        self.source.bus.send_boundary(control,TOKEN,boundary)
        self.assertEqual(json.loads(calls[0][0][0]),{'instance':TOKEN,'boundary':[11,101,11,102]})
        self.assertEqual(list(calls[0][1][0][2]),list(range(400001,400009)))
        control.short=True
        with self.assertRaises(RuntimeError):self.source.bus.send_boundary(control,TOKEN,boundary)

    def test_default_shared_sdk_routes_provision_without_ambient_delegation(self):
        # Model supported PAM platform availability for each caller. This
        # exercises production XApp launch/PrivateBus/PlatformScope methods;
        # it is not a real login, display, D-Bus or kernel launch.
        routes=[('pleb',{'PLEB_DESKTOP_BUS_ADDRESS':'unix:path=/inert/desktop','XDG_SESSION_ID':'pleb7'}),
                ('other-desktop',{'DBUS_SESSION_BUS_ADDRESS':'unix:path=/inert/host','XDG_SESSION_ID':'other7'}),
                ('startx',{'XDG_SESSION_ID':'tty7'}),('nested',{'DISPLAY':':99','XDG_SESSION_ID':'parent7'}),
                ('shared-sdk',{})]
        for route,env in routes:
            with self.subTest(route=route): self.launch_route(route,env)

    def launch_route(self,route,env):
        bus,p=self.source.bus,self.source.platform
        model=H.Model()
        model.node('unrelated','sdk',scope='other')
        clock,events=Clock(),[]
        manager=Manager(clock)
        fs=Filesystem(manager.events)
        boundary=fs.value=H.Boundary(model,outer=True)
        boundary.fds=lambda:tuple(H.FD('cg-'+str(i)) for i in range(8))
        scope=p.PlatformScope(TOKEN,manager,fs)
        def launch_owner(supervisor, environment, parent, child, info, finish, bootstrap, _source):
            inherited=(parent,child.fileno(),info.fileno(),finish.fileno(),bootstrap.fileno())
            process=supervisor.spawn('app-bus-owner',[sys.executable,'inert','--bootstrap-owner','1000000099',*map(str,inherited),TOKEN],pass_fds=inherited)
            return process,H.FD('cleanup-owner'),scope.attach(process,H.FD('cleanup-owner'),model)
        scope.launch_owner=launch_owner
        scope.cleanup=lambda *args:self.source.own.SDKCleanup(*args)
        scope.primary_fd=scope.primary_pid=scope.primary_process=None
        scope.primary_request=scope.receipt=None
        scope.primary_committed=scope.primary_bound=False
        def launch_primary(command,environment,cwd,stream_fds,request):
            events.append(('primary-launch',tuple(command),dict(environment),cwd,request))
            fd=model.node('primary','root',scope='inner',resistant=False)
            scope.primary_fd=fd
            scope.primary_pid=model.nodes['primary'].pid
            scope.primary_request={'request':request}
            return scope.primary_pid,fd
        def commit_primary(request):
            if scope.primary_request is None or scope.primary_request['request']!=request:
                raise AssertionError('primary commit did not bind the retained request')
            events.append('primary-commit')
            scope.primary_committed=scope.primary_bound=True
        scope.launch_primary=launch_primary
        scope.commit_primary=commit_primary
        sockets=[]
        class Control:
            def __init__(self,name,data=b''):self.name,self.data,self.closed=name,data,False
            def fileno(self):return H.FD(self.name)
            def close(self):
                if self.name=='control-0' and 'primary' in model.nodes:
                    model.exit(model.nodes['primary'],0)
                    model.reap(model.nodes['primary'].pid)
                self.closed=True
            def recv(self,_size):data,self.data=self.data,b'';return data
            def recvmsg(self,*_a):data,self.data=self.data,b'';return data,[],0,None
        def pair(**_kw):
            index=len(sockets)
            data=b'{"address":"unix:path=/inert/private","pid":1000000088}\n' if index==0 else b'info' if index==1 else b''
            value=(Control('control-'+str(index),data),Control('child-'+str(index)))
            sockets.append(value);return value
        class Watcher:
            def __init__(self,*,target,args,**_kw):self.target,self.args,self.live=target,args,False
            def start(self):events.append('watcher');self.live=True
            def join(self,**_kw):
                if self.live:self.target(*self.args)
                self.live=False
            def is_alive(self):return self.live
        births=[]
        class RemoteProcess:
            def __init__(self,pid,fd,status_fd,argv,**_kwargs):
                self.pid,self.fd,self.status_fd,self.args=pid,fd,status_fd,argv
                self.returncode=None
                self.stdin=self.stdout=self.stderr=None
                self._kilix_primary_pin=self
            def enable_user_signals(self):self.signals_enabled=True
            def poll(self):
                node=model.nodes['primary']
                if node.reaped:self.returncode=node.status
                return self.returncode
            def release(self):
                if self.poll() is None:raise AssertionError('live inert primary release')
                if self.fd is not None:model.close(self.fd);self.fd=None
                if self.status_fd is not None:model.close(self.status_fd);self.status_fd=None
        def spawn(name,argv,**kwargs):
            births.append((name,argv,kwargs))
            if name=='app-bus-owner':
                model.node('cleanup-owner','sdk',scope='outer',resistant=False)
                return model.nodes['cleanup-owner']
            if name=='app':
                launcher=kwargs.pop('_kilix_remote_launcher',None)
                if launcher is None:raise AssertionError('isolated app bypassed ancestor launch')
                streams=tuple(H.FD(name) for name in ('stream-stdin','stream-stdout','stream-stderr'))
                return launcher(argv,env=kwargs['env'],cwd=kwargs.get('cwd'),stream_fds=streams)
            raise AssertionError('unexpected SDK birth '+name)
        def choose(fds,*_a):return ([fd for fd in fds if getattr(fd,'data',None)],[],[])
        def pidfd(pid):
            return next((H.FD(n.identity) for n in model.nodes.values() if n.pid==pid),H.FD('sdk-parent'))
        def handoff(control,_data,_fd):
            events.append('owner-ack')
            control.data=b'A'
            sockets[2][0].data=json.dumps(self.source.own.completion(TOKEN,boundary,True,complete=True)).encode()
        fake_os=types.SimpleNamespace(getpid=lambda:1000000099,pidfd_open=pidfd,
            dup=lambda fd:H.FD(fd.identity,1),close=model.close,fstat=model.fstat,O_CLOEXEC=1,pipe2=lambda _f:(H.FD('gate-read'),H.FD('gate-write')),
            O_NONBLOCK=2,fsdecode=os.fsdecode,fspath=os.fspath,
            write=lambda _fd,data:len(data),path=types.SimpleNamespace(basename=lambda value:value,abspath=os.path.abspath))
        value,_calls=H.session(self.source)
        value._app_bus=None
        value.environment=lambda extra:dict(env,**(extra or {}))
        value.supervisor.spawn=spawn
        namespace=value.launch_app.__func__.__globals__
        previous=dict(namespace)
        namespace.update(PrivateBus=bus.PrivateBus,shutil=types.SimpleNamespace(which=lambda _n:'/inert/dbus-daemon'),
            os=fake_os,Path=Path,sys=sys,__file__=str(self.source.root/'config/kilix_sdk/xapp.py'),
            prepare_browser=lambda argv,environment:(argv,environment),prepare_player=lambda argv,_environment:argv)
        try:
            with H.replace(p,time=clock),H.replace(bus,PlatformScope=lambda _token:scope,os=fake_os,LinuxProcesses=lambda:model,
                    RemoteProcess=RemoteProcess,
                    socket=types.SimpleNamespace(socketpair=pair,SOCK_SEQPACKET=5),select=types.SimpleNamespace(select=choose),
                    threading=types.SimpleNamespace(RLock=H.DummyLock,Event=H.DummyEvent,Thread=Watcher),
                    secrets=types.SimpleNamespace(token_hex=lambda _n:TOKEN),time=types.SimpleNamespace(monotonic=model.now),
                    send_boundary=lambda *_a:events.append('boundary-handoff'),send_pidfd=handoff,
                    receive_pidfd=lambda _control,_n:(b'1000000089',H.FD('guardian'))),H.replace(self.source.own,select=types.SimpleNamespace(select=choose)):
                value.launch_app(['inert-primary'],isolate_bus=True,clipboard=False,capture_source=False)
                self.assertEqual([name for name,_argv,_kwargs in births],['app-bus-owner','app'])
                self.assertTrue(scope.attempted)
                self.assertLess(events.index('watcher'),events.index('boundary-handoff'))
                self.assertLess(events.index('owner-ack'),events.index('primary-commit'))
                delivered=births[-1][2]['env']
                self.assertEqual(delivered['DBUS_SESSION_BUS_ADDRESS'],'unix:path=/inert/private')
                for key,datum in env.items():
                    if key!='DBUS_SESSION_BUS_ADDRESS':self.assertEqual(delivered[key],datum)
                self.assertNotIn('KILIX_PRIVATE_BUS_CGROUP_ROOT',delivered)
                value._app_bus.close()
                self.assertTrue(value._app_bus.closed and scope.closed)
                self.assertFalse(model.nodes['primary'].live)
                self.assertTrue(model.nodes['unrelated'].live)
                self.assertEqual([e for e in manager.events if e[0]=='stop'],[('stop',scope.name)])
        finally:
            namespace.clear();namespace.update(previous)

    def test_platform_handle_failures_continue_independent_cleanup(self):
        scope,manager,fs,ops,clock=fixture(self.source)
        self.run_scope(scope,ops,clock)
        fs.close_error=PermissionError('inert root release refusal')
        pending=[]
        fs.close_pending=lambda:pending.append('attempted')
        with H.replace(self.source.platform,time=clock):
            with self.assertRaises(self.source.own.CleanupIncomplete):scope.retire()
            self.assertEqual(pending,['attempted'])
            self.assertIn(('connections-close',),manager.events)
            self.assertIsNotNone(scope.root)
            self.assertFalse(scope.closed)
            fs.close_error=None
            scope.retire()
        self.assertTrue(scope.closed)


def run_case(case, source, path, names=None):
    case.source = source
    case.__unittest_skip__ = False
    try: return H.run_suite(case, source, path, names)
    finally: case.__unittest_skip__ = True


def main():
    global H, PLEB, OS_ROOT
    root, base, PLEB, OS_ROOT, output = map(Path, sys.argv[1:])
    output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location("inert_ownership_library", root / "tests/test_bus_ownership_inert.py")
    H = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = H
    spec.loader.exec_module(H)
    H.install_denials()
    def no_manager_files(event, args):
        if event in ("open", "os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.fsdecode(args[0])
            if path.startswith(("/run/dbus", "/run/user", "/run/systemd")): H.deny()
        if event == "import" and args and str(args[0]).startswith(("dbus", "gi", "Xlib")): H.deny()
    sys.addaudithook(no_manager_files)
    assert socket.socket is H.deny and socket.fromfd is H.deny
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        assert Path(os.environ[key]).is_relative_to(output.parent.parent)
    assert "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ
    old = H.load_source(base, "platform_frozen")
    class Baseline(unittest.TestCase):
        def test_default_delegation_is_not_provisioned(self):
            try:
                old.own.InstanceBoundary.create({}, TOKEN)
                provided = True
            except RuntimeError:
                provided = False
            self.assertTrue(provided, "frozen fix3 has no automatic default delegation")
    before, _ = H.run_suite(Baseline, old, output / "before.log")
    assert len(before.failures) == 1 and not before.errors
    candidate = H.load_source(root, "platform_candidate")
    candidate.platform = sys.modules["platform_candidate.bus_platform"]
    result, log = run_case(PlatformTests, candidate, output / "after.log")
    if not result.wasSuccessful(): raise AssertionError(log)
    mutations = [
        ("accept-nondelegated-scope", "bus_platform.py", 'unit.get("Delegate") is not True', 'False', "test_scope_properties_are_required"),
        ("ignore-owner-membership", "bus_platform.py", 'self.transport.member(process.pid, deadline) != unit["object_path"]', 'False', "test_wrong_owner_membership_refuses_handoff"),
        ("ignore-scope-replacement", "bus_platform.py", 'ident != self.identity', 'False', "test_replaced_invocation_never_stops_replacement"),
        ("ignore-manager-peer-uid", "bus_platform.py", 'uid != self.uid or pid != self.record[1]', 'pid != self.record[1]', "test_kernel_peer_credentials_match_root_manager_record"),
        ("ignore-manager-invocation", "bus_platform.py", 'self._root_record(deadline) != self.record', 'False', "test_kernel_peer_credentials_match_root_manager_record"),
        ("allow-scope-outside-user-delegation", "bus_platform.py", 'not path.startswith(self.manager_root + "/")', 'False', "test_scope_properties_are_required"),
        ("forget-partial-retirement", "bus_platform.py", '        for key in ("boundary", "partial_boundary"):', '        self.closed = True\n        for key in ("boundary", "partial_boundary"):', "test_partial_retirement_keeps_handles_and_retries"),
        ("accept-inactive-live-group", "bus_platform.py", 'and not unit.get("ControlGroup")', '', "test_inactive_with_live_group_is_not_retirement"),
        ("treat-permission-error-as-empty", "bus_ownership.py", 'if error.errno != errno.ENODEV:\n                raise', 'if False:\n                raise', "test_permission_or_missing_path_is_not_dead_group"),
        ("handoff-before-watcher", "private_bus.py", '            self._start_watcher()\n            send_boundary(bootstrap, self.instance, self.boundary)',
         '            send_boundary(bootstrap, self.instance, self.boundary)\n            self._start_watcher()', "test_default_shared_sdk_routes_provision_without_ambient_delegation"),
        ("ignore-bootstrap-binding", "private_bus.py", 'if json.loads(data) != {"instance": instance, "boundary": list(boundary.identity)}:',
         'if False:', "test_bootstrap_rejects_invalid_transfer_and_releases_only_received_fds"),
        ("omit-bootstrap-cloexec", "private_bus.py", 'handles.packet(control, 1024, "boundary", cloexec=socket.MSG_CMSG_CLOEXEC)',
         'handles.packet(control, 1024, "boundary", cloexec=0)', "test_bootstrap_nonce_identity_cloexec_and_owner_handoff"),
        ("accept-extra-delegation-member", "bus_platform.py", 'if os.read(procs, 4097) != f"{pid}\\n".encode():',
         'if False:', "test_delegation_permission_and_extra_member_fail_without_leaks"),
        ("attach-numeric-sender-default", "bus_platform.py", 'd.Array([d.UInt32(pid)], signature="u")',
         'd.Array([d.UInt32(0)], signature="u")', "test_typed_transient_request_moves_one_owned_pid"),
    ]
    guards = []
    for index, (name, file, old_text, new_text, test) in enumerate(mutations):
        target = output / "mutants" / name
        config = target / "config/kilix_sdk"
        config.mkdir(parents=True)
        for item in ("bus_platform.py", "bus_ownership.py", "private_bus.py", "bus_supervision.py", "bus_supervisor.py", "process_pin.py", "xapp.py"):
            (config / item).write_bytes((root / "config/kilix_sdk" / item).read_bytes())
        path = config / file
        source = path.read_text()
        assert source.count(old_text) == 1, name
        path.write_text(source.replace(old_text, new_text))
        source = H.load_source(target, "platform_mutant_" + str(index))
        source.platform = sys.modules["platform_mutant_" + str(index) + ".bus_platform"]
        check, _log = run_case(PlatformTests, source, output / (name + ".log"), [test])
        assert check.failures and not check.errors, name + ": " + _log
        guards.append({"name": name, "test": test, "source": str(path), "sha256": sha(path),
                       "failures": len(check.failures), "errors": len(check.errors)})
    report = {"scope": "source/inert only; no installed or native qualification", "baseline_expected_failures": 1,
              "candidate_tests": result.testsRun, "candidate_failures": len(result.failures), "candidate_errors": len(result.errors),
              "guards": guards, "bindings": H.BINDINGS, "runner_sha256": sha(Path(__file__)),
              "delivery_bindings": {str(p): sha(p) for p in (PLEB / "lib/install.sh", OS_ROOT / "provision/install-deps.sh")},
              "native_effects": 0, "process_census": False,
              "denials": "subprocess/signals/pidfds/waits/sockets/ctypes/threads/sleep/proc/sys/run-manager paths/native dbus imports",
              "environment": {key: os.environ.get(key) for key in ("HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS")}}
    (output / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("scope", "candidate_tests", "candidate_failures", "candidate_errors")}))
    print(str(len(guards)) + " behavioral source guard controls rejected; all effects inert")


if __name__ == "__main__": main()
