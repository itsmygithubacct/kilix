"""Fake logind, fprintd and polkit on a private dbus-daemon, and agent scenarios.

Used by test_fingerlock_bus. Never touches the system bus: every process here
gets DBUS_SYSTEM_BUS_ADDRESS pointing at a bus the test started itself.

    fingerlock_bus_fixture.py service UID     serve until stdin closes
    fingerlock_bus_fixture.py scenario NAME   run one agent scenario, print JSON
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

LOGIND = "org.freedesktop.login1"
FPRINT = "net.reactivated.Fprint"
POLKIT = "org.freedesktop.PolicyKit1"
FIXTURE = "org.kilix.FingerlockFixture"
SESSION_PATH = "/org/freedesktop/login1/session/_33"
DEVICE_PATH = "/net/reactivated/Fprint/Device/0"

XML = """
<node>
  <interface name="org.freedesktop.login1.Manager">
    <method name="GetSession"><arg type="s" direction="in"/><arg type="o" direction="out"/></method>
    <method name="GetSessionByPID"><arg type="u" direction="in"/><arg type="o" direction="out"/></method>
    <method name="Inhibit">
      <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
      <arg type="s" direction="in"/><arg type="h" direction="out"/>
    </method>
    <signal name="PrepareForSleep"><arg type="b"/></signal>
    <signal name="PrepareForShutdown"><arg type="b"/></signal>
  </interface>
  <interface name="org.freedesktop.login1.Session">
    <property name="Id" type="s" access="read"/>
    <property name="User" type="(uo)" access="read"/>
    <property name="Seat" type="(so)" access="read"/>
    <property name="State" type="s" access="read"/>
    <property name="Active" type="b" access="read"/>
    <property name="LockedHint" type="b" access="read"/>
    <property name="Remote" type="b" access="read"/>
    <signal name="Lock"/>
  </interface>
  <interface name="net.reactivated.Fprint.Manager">
    <method name="GetDefaultDevice"><arg type="o" direction="out"/></method>
    <method name="GetDevices"><arg type="ao" direction="out"/></method>
  </interface>
  <interface name="net.reactivated.Fprint.Device">
    <method name="Claim"><arg type="s" direction="in"/></method>
    <method name="Release"/>
    <method name="ListEnrolledFingers"><arg type="s" direction="in"/><arg type="as" direction="out"/></method>
    <method name="VerifyStart"><arg type="s" direction="in"/></method>
    <method name="VerifyStop"/>
    <signal name="VerifyStatus"><arg type="s"/><arg type="b"/></signal>
    <signal name="VerifyFingerSelected"><arg type="s"/></signal>
    <property name="name" type="s" access="read"/>
    <property name="scan-type" type="s" access="read"/>
    <property name="num-enroll-stages" type="i" access="read"/>
    <property name="finger-present" type="b" access="read"/>
    <property name="finger-needed" type="b" access="read"/>
  </interface>
  <interface name="org.freedesktop.PolicyKit1.Authority">
    <method name="CheckAuthorization">
      <arg type="(sa{sv})" direction="in"/><arg type="s" direction="in"/><arg type="a{ss}" direction="in"/>
      <arg type="u" direction="in"/><arg type="s" direction="in"/><arg type="(bba{ss})" direction="out"/>
    </method>
    <method name="CancelCheckAuthorization"><arg type="s" direction="in"/></method>
  </interface>
  <interface name="org.kilix.FingerlockFixture">
    <method name="Reset"><arg type="s" direction="in"/></method>
    <method name="Set"><arg type="s" direction="in"/></method>
    <method name="Emit"><arg type="s" direction="in"/><arg type="b" direction="in"/></method>
    <method name="Events"><arg type="s" direction="out"/></method>
  </interface>
</node>
"""


def now():
    return time.clock_gettime(time.CLOCK_MONOTONIC)


class Service:
    def __init__(self, uid):
        self.uid = uid
        self.info = Gio.DBusNodeInfo.new_for_xml(XML)
        address = os.environ["DBUS_SYSTEM_BUS_ADDRESS"]
        self.conn = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.reset("{}")
        for path, iface in (("/org/freedesktop/login1", "org.freedesktop.login1.Manager"),
                            (SESSION_PATH, "org.freedesktop.login1.Session"),
                            ("/net/reactivated/Fprint/Manager", "net.reactivated.Fprint.Manager"),
                            (DEVICE_PATH, "net.reactivated.Fprint.Device"),
                            ("/org/freedesktop/PolicyKit1/Authority", "org.freedesktop.PolicyKit1.Authority"),
                            ("/fixture", "org.kilix.FingerlockFixture")):
            self.conn.register_object(path, self.info.lookup_interface(iface), self.method, self.prop, None)
        for name in (LOGIND, FPRINT, POLKIT, FIXTURE):
            self.conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "RequestName", GLib.Variant("(su)", (name, 4)), None,
                                Gio.DBusCallFlags.NONE, 5000, None)

    def reset(self, config):
        self.events = []
        self.locked = False
        self.active = True
        self.config = {"scans": [[1.0, "verify-match"]], "polkit": "approve", "enrolled": ["right-index-finger"]}
        self.config.update(json.loads(config))
        self.scan_source = None
        self.pending = {}
        self.inhibitors = []

    def record(self, event, **fields):
        self.events.append({"t": now(), "event": event, **fields})

    def emit(self, path, iface, member, params=None):
        self.conn.emit_signal(None, path, iface, member, params)

    def prop(self, _conn, _sender, path, iface, name):
        if iface == "org.freedesktop.login1.Session":
            self.record("session-props")
            return {"Id": GLib.Variant("s", "3"),
                    "User": GLib.Variant("(uo)", (self.uid, "/org/freedesktop/login1/user/_1")),
                    "Seat": GLib.Variant("(so)", ("seat0", "/org/freedesktop/login1/seat/seat0")),
                    "State": GLib.Variant("s", "active" if self.active else "online"),
                    "Active": GLib.Variant("b", self.active),
                    "LockedHint": GLib.Variant("b", self.locked),
                    "Remote": GLib.Variant("b", False)}[name]
        return {"name": GLib.Variant("s", "Fixture Sensor"), "scan-type": GLib.Variant("s", "press"),
                "num-enroll-stages": GLib.Variant("i", 9), "finger-present": GLib.Variant("b", False),
                "finger-needed": GLib.Variant("b", False)}[name]

    def inhibitor(self, invocation, params):
        what, who, why, mode = params.unpack()
        read, write = os.pipe()
        fds = Gio.UnixFDList.new()
        fds.append(write)
        os.close(write)
        index = len(self.inhibitors)
        self.inhibitors.append(read)
        self.record("inhibit", what=what, mode=mode, index=index)

        def released(fd, _condition):
            self.record("inhibitor-released", index=index)
            os.close(fd)
            return False
        GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, read, GLib.IOCondition.HUP | GLib.IOCondition.IN, released)
        invocation.return_value_with_unix_fd_list(GLib.Variant("(h)", (0,)), fds)

    def verify_next(self):
        scans = self.config["scans"]
        if not scans:
            return
        seconds, verdict = scans.pop(0)

        def deliver():
            self.scan_source = None
            self.record("verify-status", result=verdict)
            self.emit(DEVICE_PATH, "net.reactivated.Fprint.Device", "VerifyStatus",
                      GLib.Variant("(sb)", (verdict, True)))
            return False
        self.scan_source = GLib.timeout_add(int(seconds * 1000), deliver)

    def method(self, _conn, sender, path, iface, member, params, invocation):
        value = None
        if member == "GetSession" or member == "GetSessionByPID":
            self.record(member.lower())
            value = GLib.Variant("(o)", (SESSION_PATH,))
        elif member == "Inhibit":
            return self.inhibitor(invocation, params)
        elif member == "GetDefaultDevice":
            value = GLib.Variant("(o)", (DEVICE_PATH,))
        elif member == "GetDevices":
            value = GLib.Variant("(ao)", ([DEVICE_PATH],))
        elif member in ("Claim", "Release"):
            self.record(member.lower())
        elif member == "ListEnrolledFingers":
            value = GLib.Variant("(as)", (self.config["enrolled"],))
        elif member == "VerifyStart":
            finger = params.unpack()[0]
            self.record("verify-start", finger=finger)
            self.emit(DEVICE_PATH, "net.reactivated.Fprint.Device", "VerifyFingerSelected",
                      GLib.Variant("(s)", (finger,)))
            self.verify_next()
        elif member == "VerifyStop":
            self.record("verify-stop")
            if self.scan_source is not None:
                GLib.source_remove(self.scan_source)
                self.scan_source = None
        elif member == "CheckAuthorization":
            subject, action, details, flags, cancel_id = params.unpack()
            interactive = bool(flags & 1)
            self.record("polkit-check", action=action, details=details, interactive=interactive,
                        subject=[subject[0], sorted(subject[1])], cancel_id=cancel_id)
            if not interactive:
                value = GLib.Variant("((bba{ss}))", ((False, True, {}),))
            elif self.config["polkit"] == "approve":
                value = GLib.Variant("((bba{ss}))", ((True, False, {}),))
            elif self.config["polkit"] == "dismiss":
                value = GLib.Variant("((bba{ss}))", ((False, False, {"polkit.dismissed": "true"}),))
            else:   # hold: the dialog stays open until cancelled
                self.pending[(sender, cancel_id)] = invocation
                return None
        elif member == "CancelCheckAuthorization":
            cancel_id = params.unpack()[0]
            pending = self.pending.pop((sender, cancel_id), None)
            self.record("polkit-cancel", cancel_id=cancel_id, matched=pending is not None)
            if pending is not None:
                pending.return_dbus_error("org.freedesktop.PolicyKit1.Error.Cancelled", "cancelled")
        elif member == "Reset":
            self.reset(params.unpack()[0])
        elif member == "Set":
            change = json.loads(params.unpack()[0])
            self.locked = change.get("locked", self.locked)
            self.active = change.get("active", self.active)
            self.record("set", **change)
        elif member == "Emit":
            signal, flag = params.unpack()
            self.record("emit", signal=signal, value=flag)
            if signal == "Lock":
                self.emit(SESSION_PATH, "org.freedesktop.login1.Session", "Lock")
            else:
                self.emit("/org/freedesktop/login1", "org.freedesktop.login1.Manager", signal,
                          GLib.Variant("(b)", (flag,)))
        elif member == "Events":
            value = GLib.Variant("(s)", (json.dumps(self.events),))
        invocation.return_value(value)
        return None


def serve(uid):
    service = Service(uid)
    loop = GLib.MainLoop()
    GLib.io_add_watch(sys.stdin.fileno(), GLib.IOCondition.HUP | GLib.IOCondition.IN, lambda *_: loop.quit())
    print("ready", flush=True)
    loop.run()
    del service


# --- agent scenarios ----------------------------------------------------------

class Fixture:
    def __init__(self):
        self.conn = Gio.DBusConnection.new_for_address_sync(
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)

    def call(self, member, *args, signature=None):
        reply = self.conn.call_sync(FIXTURE, "/fixture", "org.kilix.FingerlockFixture", member,
                                    GLib.Variant(signature, args) if signature else None, None,
                                    Gio.DBusCallFlags.NONE, 5000, None)
        return reply.unpack() if reply is not None else None

    def reset(self, **config):
        self.call("Reset", json.dumps(config), signature="(s)")

    def set(self, **change):
        self.call("Set", json.dumps(change), signature="(s)")

    def emit(self, signal, flag=False):
        self.call("Emit", signal, flag, signature="(sb)")

    def events(self, name=None):
        found = json.loads(self.call("Events")[0])
        return [event for event in found if name is None or event["event"] == name]

    def wait(self, name, count=1, seconds=10.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if len(self.events(name)) >= count:
                return True
            time.sleep(0.02)
        return False


def _until(predicate, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    return predicate()


def scenario(name):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
    import hashlib
    import subprocess
    import unittest.mock

    import fingerlock_agent as agentmod
    import fingerlock_policy as policy

    def no_process(*args, **kwargs):
        raise AssertionError("no helper process may be started (pkcheck argv would expose the request)")
    subprocess.Popen = no_process

    fixture = Fixture()
    config = {"lock-hint": {"scans": [[8.0, "verify-match"]]},
              "lock-signal": {"scans": [[8.0, "verify-match"]]},
              "withdraw": {"polkit": "hold"},
              "dismiss": {"polkit": "dismiss"},
              "timeout": {"polkit": "hold"}}.get(name, {})
    fixture.reset(**config)
    out = {"scenario": name}
    uid = os.getuid()
    lookups = []
    real_state = agentmod.session_by_id

    def counted(session_id):
        lookups.append(time.clock_gettime(time.CLOCK_MONOTONIC))
        return real_state(session_id)

    if name == "timeout":
        request = {"action": policy.ACTION, "subject": {"pid": os.getpid(), "start": 1, "uid": uid},
                   "details": {"polkit.message": "m"}, "interactive": True}
        try:
            policy._run_check(request, lambda: True, 0.3)
            out["error"] = None
        except Exception as exc:
            out["error"] = exc.__class__.__name__
        fixture.wait("polkit-cancel")
        out["events"] = fixture.events()
        return out

    agent = agentmod.Agent(allowed_uid=uid, session_state=counted, session_interval=0.05)
    agent.log = lambda line: out.setdefault("log", []).append(line)
    wipes = []
    real_forget_all = agent.custody.forget_all

    def forget_all(reason="forget"):
        count = real_forget_all(reason)
        wipes.append({"t": time.clock_gettime(time.CLOCK_MONOTONIC), "reason": reason, "count": count})
        return count
    agent.custody.forget_all = forget_all
    agent.start_monitor()
    out["watch_ready"] = _until(lambda: agent.watch_problem() is None)

    def put():
        agent.custody.deposit(uid, "owner", bytes(range(32)), "disposable", bytes(32), 600, session_id="3")

    blob = b"container bytes"
    binding = {"kind": "unlock", "name": "secret-entry-name", "size": len(blob),
               "sha256": hashlib.sha256(blob).hexdigest()}

    def operate():
        with unittest.mock.patch.object(agentmod, "unlock_bytes", return_value=(None, b"plain")):
            reply, plain = agent.handle({"protocol": 1, "op": "operate", "locker_id": "disposable",
                                         "finger": "any", "body_bytes": len(blob), "binding": binding},
                                        uid, os.getpid(), blob)
        return reply, plain.decode()

    def status():
        return agent.handle({"protocol": 1, "op": "status"}, uid, os.getpid())[0]

    try:
        if name in ("scan", "lock-hint", "lock-signal", "withdraw", "dismiss"):
            put()
            if name == "lock-hint":
                threading.Thread(target=lambda: fixture.wait("verify-start") and fixture.set(locked=True)).start()
            elif name == "lock-signal":
                threading.Thread(target=lambda: fixture.wait("verify-start") and fixture.emit("Lock")).start()
            elif name == "withdraw":
                threading.Thread(target=lambda: fixture.wait("polkit-check", 2) and fixture.set(locked=True)).start()
            started = time.monotonic()
            out["reply"], out["plain"] = operate()
            out["seconds"] = round(time.monotonic() - started, 3)
            out["lookups"] = lookups
            out["status"] = status()
        elif name in ("sleep", "shutdown"):
            put()
            signal = "PrepareForSleep" if name == "sleep" else "PrepareForShutdown"
            fixture.emit(signal, True)
            out["forgotten"] = _until(lambda: agent.custody.held() == [])
            fixture.wait("inhibitor-released")
            out["status_asleep"] = status()
            out["deposit_asleep"] = agent.handle({"protocol": 1, "op": "deposit", "key": "00" * 32,
                                                  "locker_id": "disposable"}, uid, os.getpid())[0]
            fixture.emit(signal, False)
            out["inhibitors_after_resume"] = fixture.wait("inhibit", 2)
            out["ready_after_resume"] = _until(lambda: agent.watch_problem() is None)
            out["status_awake"] = status()
        out["wipes"] = wipes
        out["events"] = fixture.events()
    finally:
        agent.close()
    return out


def main():
    if sys.argv[1] == "service":
        serve(int(sys.argv[2]))
        return 0
    result = scenario(sys.argv[2])
    print(json.dumps(result, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
