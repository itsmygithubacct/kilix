"""Real private buses and exact primary/guardian/subreaper/daemon ownership."""
import ctypes
import json
import os
from _env_support import sandbox_env
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import dbus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import stream
from kilix_sdk.private_bus import PrivateBus

PRIMARY = '''import dbus,json,os,signal,sys,time
bus=dbus.bus.BusConnection(os.environ['DBUS_SESSION_BUS_ADDRESS'])
identity=bus.call_blocking('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','GetId','',(),timeout=2)
print(json.dumps({'name':str(bus.get_unique_name()),'id':str(identity)}),flush=True)
if sys.argv[1]=='early':raise SystemExit(7)
if sys.argv[1]=='resist':signal.signal(signal.SIGTERM,signal.SIG_IGN)
while True:time.sleep(.03)
'''
OWNER = '''import json,os,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
sys.path.insert(0,os.path.join(sys.argv[1],'..','tests'))
from _env_support import sandbox_env
import stream
from kilix_sdk.private_bus import PrivateBus
supervisor=stream.StreamSupervisor('killed-sdk-owner')
bus=PrivateBus(supervisor,sandbox_env())
app=supervisor.spawn('app',['/usr/bin/python3','-c','import time;time.sleep(30)'],
 env=bus.environment(sandbox_env()),_kilix_remote_launcher=bus.launch_primary)
if sys.argv[3]!='unbound':bus.bind(app)
owner_pid=int(Path(f'/proc/{bus.guardian_pid}/stat').read_text().rsplit(')',1)[1].split()[1])
print(json.dumps({'primary':app.pid,'guardian':bus.guardian_pid,'daemon':bus.daemon_pid,'bus-owner':owner_pid,
 'wait-owner':bus.platform.wait_owner[1],'supervisor':bus.owner_process.pid}),flush=True)
while True:time.sleep(.03)
'''


class PrivateBusOwnership(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from kilix_sdk.bus_supervision import installed_program
        program = Path(installed_program(ROOT / 'config/kilix_sdk'))
        if not program.is_file():
            message = 'install this checkout with scripts/install-private-bus-supervisor.py --install'
            if os.environ.get('KILIX_REQUIRE_PRIVATE_BUS_TESTS') == '1':
                raise AssertionError(message)
            raise unittest.SkipTest(message)

    def setUp(self):
        libc = ctypes.CDLL(None)
        previous_subreaper = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(previous_subreaper), 0, 0, 0), 0)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        # This process runs other suites too. Leaving it as a subreaper makes
        # later cancellation tests adopt children they do not own or reap.
        self.addCleanup(lambda: self.assertEqual(
            libc.prctl(36, previous_subreaper.value, 0, 0, 0), 0))
        self.resources = []
        self.parents = []
        self.pidfds = []
        self.tmp = tempfile.TemporaryDirectory(prefix="owned-app-bus-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.cleanup)

    def start(self, name, mode="hold", env=None, bind=True):
        supervisor = stream.StreamSupervisor(name)
        self.resources.append([supervisor, None])
        bus = PrivateBus(supervisor, sandbox_env() if env is None else env)
        self.resources[-1][1] = bus
        private = bus.environment(sandbox_env() if env is None else env)
        command = ["/usr/bin/python3", "-c", PRIMARY, mode]
        app = supervisor.spawn("app", command, env=private, stdout=subprocess.PIPE,
                               _kilix_remote_launcher=bus.launch_primary)
        # Pin every identity before opening the gate, including fast exits.
        owner_pid = int(Path(f"/proc/{bus.guardian_pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        identities = {}
        for role, pid in (("primary", app.pid), ("guardian", bus.guardian_pid),
                          ("daemon", bus.daemon_pid), ("bus-owner", owner_pid),
                          ("wait-owner", bus.platform.wait_owner[1]),
                          ("supervisor", bus.owner_process.pid)):
            fd = os.pidfd_open(pid)
            self.pidfds.append((pid, fd))
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            identities[role] = {"pid": pid, "fd": fd, "start": stat[19]}
        self.assertEqual(int(Path(f"/proc/{bus.daemon_pid}/stat").read_text().rsplit(")", 1)[1].split()[1]), bus.guardian_pid)
        if bind:
            bus.bind(app)
            self.assertTrue(select.select([app.stdout], [], [], 5)[0])
            ready = json.loads(app.stdout.readline())
        else:
            self.assertFalse(select.select([app.stdout], [], [], .15)[0], "unowned primary execed before handoff")
            ready = {}
        print(json.dumps({"instance_started": {role: {k: v for k, v in row.items() if k != "fd"}
                                              for role, row in identities.items()}}), flush=True)
        return supervisor, bus, app, identities, ready

    def exited(self, identities, seconds=3):
        deadline = time.monotonic() + seconds
        for role, record in identities.items():
            self.assertTrue(select.select([record["fd"]], [], [], max(0, deadline - time.monotonic()))[0],
                            "owned " + role + " survived")
        print(json.dumps({"exited": {role: {k: v for k, v in row.items() if k != "fd"}
                                     for role, row in identities.items()}}), flush=True)

    def activation_environment(self, label, daemonize=False):
        root = Path(self.tmp.name) / label
        services = root / "dbus-1/services"
        services.mkdir(parents=True)
        name = "org.example.Owned" + label.replace("-", "")
        script = root / "activated.py"
        script.write_text("import dbus,os,signal,time\n"
                          "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n" +
                          ("if os.fork():os._exit(0)\nos.setsid()\nif os.fork():os._exit(0)\n" if daemonize else "") +
                          "bus=dbus.bus.BusConnection(os.environ['DBUS_SESSION_BUS_ADDRESS'])\n"
                          "bus.set_exit_on_disconnect(False)\n"
                          "bus.request_name(" + repr(name) + ")\n"
                          "while True:time.sleep(.03)\n")
        (services / (name + ".service")).write_text(
            "[D-BUS Service]\nName=" + name + "\nExec=/usr/bin/python3 " + str(script) + "\n")
        return sandbox_env( XDG_DATA_HOME=str(root)), name

    def activate(self, bus, name):
        client = dbus.bus.BusConnection(bus.address)
        self.addCleanup(client.close)
        client.start_service_by_name(name)
        owner = client.get_name_owner(name)
        pid = int(client.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (owner,), timeout=2))
        fd = os.pidfd_open(pid)
        self.pidfds.append((pid, fd))
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return client, {"pid": pid, "fd": fd, "start": stat[19], "parent": int(stat[1])}

    def activation_failure(self, mode, daemonize=False):
        env, name = self.activation_environment(mode, daemonize)
        supervisor, bus, app, owned, ready = self.start("activated-" + mode, "resist", env)
        client, owned["activated-service"] = self.activate(bus, name)
        self.assertEqual(owned["guardian"]["pid"], int(Path(f"/proc/{bus.daemon_pid}/stat").read_text().rsplit(")", 1)[1].split()[1]))
        self.assertEqual(int(Path(f"/proc/{bus.guardian_pid}/stat").read_text().rsplit(")", 1)[1].split()[1]), owned["bus-owner"]["pid"])
        self.assertEqual(int(Path(f"/proc/{owned['bus-owner']['pid']}/stat").read_text().rsplit(")", 1)[1].split()[1]), owned["wait-owner"]["pid"])
        if daemonize:
            self.assertEqual(owned["activated-service"]["parent"], owned["bus-owner"]["pid"])
        print(json.dumps({"activation_started": {role: {k: v for k, v in row.items() if k != "fd"}
                                                for role, row in owned.items()}}), flush=True)
        started = time.monotonic()
        if mode == "stopped-close":
            signal.pidfd_send_signal(owned["guardian"]["fd"], signal.SIGSTOP)
            bus.close()
            for role, row in owned.items():
                self.assertTrue(select.select([row["fd"]], [], [], 0)[0], "close returned before owned " + role + " exited")
            self.assertLess(time.monotonic() - started, 4.5)
        elif mode == "daemon-crash":
            signal.pidfd_send_signal(owned["daemon"]["fd"], signal.SIGKILL)
        elif mode == "primary-crash":
            signal.pidfd_send_signal(owned["primary"]["fd"], signal.SIGKILL)
        elif mode == "owner-term":
            signal.pidfd_send_signal(owned["bus-owner"]["fd"], signal.SIGTERM)
        else:
            signal.pidfd_send_signal(owned["guardian"]["fd"], signal.SIGKILL)
        self.exited(owned)
        bus.close()
        bus.close()
        supervisor.cleanup()
        supervisor.cleanup()
        print(json.dumps({"activation_failure": mode, "double_fork": daemonize,
                          "elapsed_seconds": round(time.monotonic() - started, 3)}), flush=True)

    def test_guardian_crash_reaps_resistant_activated_service_and_primary(self):
        self.activation_failure("guardian-crash")

    def test_stopped_guardian_close_returns_after_resistant_service_cleanup(self):
        self.activation_failure("stopped-close")

    def test_guardian_crash_reaps_double_forked_activated_service(self):
        self.activation_failure("forked-crash", daemonize=True)

    def test_daemon_crash_reaps_resistant_activated_service(self):
        self.activation_failure("daemon-crash")

    def test_primary_crash_reaps_resistant_activated_service(self):
        self.activation_failure("primary-crash")

    def test_supervisor_owner_term_reaps_resistant_activated_service(self):
        self.activation_failure("owner-term")

    def test_sdk_hard_death_reaps_resistant_activated_service_and_primary(self):
        env, name = self.activation_environment("sdk-death")
        command = OWNER.replace("'supervisor':bus.owner_process.pid}", "'supervisor':bus.owner_process.pid,'address':bus.address}")
        command = command.replace("import time;time.sleep(30)", "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)")
        parent = subprocess.Popen([sys.executable, "-c", command, str(ROOT / "config"),
                                   str(ROOT / "config/kilix_sdk/private_bus.py"), "bound"],
                                  env=env, stdout=subprocess.PIPE, text=True)
        self.parents.append(parent)
        parent_fd = os.pidfd_open(parent.pid)
        self.pidfds.append((parent.pid, parent_fd))
        self.assertTrue(select.select([parent.stdout], [], [], 5)[0])
        pids = json.loads(parent.stdout.readline())
        address = pids.pop("address")
        owned = {}
        for role, pid in pids.items():
            fd = os.pidfd_open(pid)
            self.pidfds.append((pid, fd))
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            owned[role] = {"pid": pid, "fd": fd, "start": stat[19]}
        client = dbus.bus.BusConnection(address)
        self.addCleanup(client.close)
        client.start_service_by_name(name)
        pid = int(client.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (client.get_name_owner(name),), timeout=2))
        fd = os.pidfd_open(pid)
        self.pidfds.append((pid, fd))
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        owned["activated-service"] = {"pid": pid, "fd": fd, "start": stat[19]}
        signal.pidfd_send_signal(parent_fd, signal.SIGKILL)
        self.assertEqual(parent.wait(timeout=2), -signal.SIGKILL)
        self.exited(owned)

    def test_crashed_activated_instance_preserves_other_service_and_unrelated_process(self):
        first_env, first_name = self.activation_environment("first")
        second_env, second_name = self.activation_environment("second")
        first = self.start("activated-first", "resist", first_env)
        second = self.start("activated-second", "resist", second_env)
        first_client, first[3]["activated-service"] = self.activate(first[1], first_name)
        second_client, second[3]["activated-service"] = self.activate(second[1], second_name)
        unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], stdout=subprocess.PIPE, text=True)
        self.parents.append(unrelated)
        fd = os.pidfd_open(unrelated.pid)
        self.pidfds.append((unrelated.pid, fd))
        start = Path(f"/proc/{unrelated.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        signal.pidfd_send_signal(first[3]["guardian"]["fd"], signal.SIGKILL)
        self.exited(first[3])
        first[1].close()
        first[1].close()
        first[0].cleanup()
        first[0].cleanup()
        for role, row in second[3].items():
            self.assertFalse(select.select([row["fd"]], [], [], 0)[0], "unrelated " + role + " exited")
        self.assertFalse(select.select([fd], [], [], 0)[0], "unrelated SDK child exited")
        self.assertEqual(str(second_client.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetId", "", (), timeout=2)), second[4]["id"])
        self.assertTrue(second_client.get_name_owner(second_name))
        print(json.dumps({"independent_activated_bus_survived": True,
                          "unrelated_process": {"pid": unrelated.pid, "start": start}}), flush=True)
        second[1].close()
        self.exited(second[3])

    def cleanup(self):
        for supervisor, bus in reversed(self.resources):
            if bus is not None:
                bus.close()
            supervisor.cleanup()
        for parent in self.parents:
            if parent.poll() is None:
                # Parent identities were acquired at spawn, before mutation.
                row = next((fd for pid, fd in self.pidfds if pid == parent.pid), None)
                if row is not None:
                    try:
                        signal.pidfd_send_signal(row, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            parent.wait(timeout=3)
            parent.stdout.close()
        for pid, fd in self.pidfds:
            try:
                # Root-owned supervisors are observation-only fixtures. All
                # identities already proven exited need no further signal.
                if not select.select([fd], [], [], 0)[0]:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                    select.select([fd], [], [], 2)
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                pass
            os.close(fd)

    def test_close_kills_resistant_primary_and_reaps_exact_daemon(self):
        supervisor, bus, app, owned, ready = self.start("resistant-bus", "resist")
        bus.close()
        self.exited(owned)
        self.assertEqual(app.wait(timeout=2), -signal.SIGKILL)
        supervisor.cleanup()

    def test_normal_primary_completion_preserves_status_and_ends_bus(self):
        supervisor, bus, app, owned, ready = self.start("early-bus", "early")
        self.assertEqual(app.wait(timeout=2), 7)
        self.exited(owned)

    def test_immediate_close_after_normal_exit_keeps_the_cleanup_receipt(self):
        supervisor, bus, app, owned, ready = self.start("early-close-bus", "early")
        self.assertEqual(app.wait(timeout=2), 7)
        bus.close()
        self.exited(owned)
        supervisor.cleanup()

    def test_hard_primary_death_ends_its_exact_bus(self):
        supervisor, bus, app, owned, ready = self.start("primary-kill")
        signal.pidfd_send_signal(owned["primary"]["fd"], signal.SIGKILL)
        self.exited(owned)
        self.assertEqual(app.wait(timeout=2), -signal.SIGKILL)

    def test_repeated_close_preserves_an_independent_same_user_bus(self):
        first = self.start("first-owned-bus")
        second = self.start("second-owned-bus")
        self.assertNotEqual(first[4]["id"], second[4]["id"])
        first[1].close()
        first[0].cleanup()
        first[1].close()
        first[0].cleanup()
        self.exited(first[3])
        for role, record in second[3].items():
            self.assertFalse(select.select([record["fd"]], [], [], 0)[0], "unrelated " + role + " was killed")
        second[1].close()
        self.exited(second[3])

    def test_guardian_hard_death_cannot_orphan_its_daemon(self):
        supervisor, bus, app, owned, ready = self.start("guardian-kill")
        signal.pidfd_send_signal(owned["guardian"]["fd"], signal.SIGKILL)
        self.exited({role: row for role, row in owned.items() if role != "primary"})
        self.exited(owned)
        supervisor.cleanup()
        self.exited(owned)

    def test_close_reaps_bus_activated_service_that_ignores_disconnect_and_term(self):
        root = Path(self.tmp.name)
        services = root / "dbus-1/services"
        services.mkdir(parents=True)
        script = root / "activated.py"
        script.write_text("import dbus,os,signal,time\n"
                          "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                          "bus=dbus.bus.BusConnection(os.environ['DBUS_SESSION_BUS_ADDRESS'])\n"
                          "bus.set_exit_on_disconnect(False)\n"
                          "bus.request_name('org.example.OwnedBusService')\n"
                          "while True:time.sleep(.03)\n")
        (services / "org.example.OwnedBusService.service").write_text(
            "[D-BUS Service]\nName=org.example.OwnedBusService\nExec=/usr/bin/python3 " + str(script) + "\n")
        supervisor, bus, app, owned, ready = self.start("activated-owned-bus", env=sandbox_env( XDG_DATA_HOME=str(root)))
        client = dbus.bus.BusConnection(bus.address)
        self.addCleanup(client.close)
        client.start_service_by_name("org.example.OwnedBusService")
        owner = client.get_name_owner("org.example.OwnedBusService")
        pid = int(client.call_blocking("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetConnectionUnixProcessID", "s", (owner,), timeout=2))
        fd = os.pidfd_open(pid)
        self.pidfds.append((pid, fd))
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        owned["activated-service"] = {"pid": pid, "fd": fd, "start": stat[19]}
        bus.close()
        self.exited(owned)

    def test_hard_sdk_parent_death_ends_primary_guardian_and_exact_bus(self):
        self.parent_death("bound")

    def test_hard_sdk_parent_death_before_handoff_cannot_orphan_primary_or_bus(self):
        self.parent_death("unbound")

    def parent_death(self, mode):
        owner = subprocess.Popen([sys.executable, "-c", OWNER, str(ROOT / "config"),
                                  str(ROOT / "config/kilix_sdk/private_bus.py"), mode], stdout=subprocess.PIPE, text=True)
        self.parents.append(owner)
        owner_fd = os.pidfd_open(owner.pid)
        self.pidfds.append((owner.pid, owner_fd))
        self.assertTrue(select.select([owner.stdout], [], [], 5)[0])
        pids = json.loads(owner.stdout.readline())
        owned = {}
        for role, pid in pids.items():
            fd = os.pidfd_open(pid)
            self.pidfds.append((pid, fd))
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            owned[role] = {"pid": pid, "fd": fd, "start": stat[19]}
        signal.pidfd_send_signal(owner_fd, signal.SIGKILL)
        self.assertEqual(owner.wait(timeout=2), -signal.SIGKILL)
        self.exited(owned)

    def test_close_before_handoff_refuses_primary_exec_and_ends_bus(self):
        supervisor, bus, app, owned, ready = self.start("before-handoff-close", bind=False)
        bus.close()
        self.exited(owned)
        self.assertNotEqual(app.wait(timeout=2), 0)

    def test_guardian_death_before_handoff_refuses_primary_exec(self):
        supervisor, bus, app, owned, ready = self.start("before-handoff-guardian-death", bind=False)
        signal.pidfd_send_signal(owned["guardian"]["fd"], signal.SIGKILL)
        self.assertTrue(select.select([owned["guardian"]["fd"]], [], [], 2)[0])
        with self.assertRaises((OSError, RuntimeError)):
            bus.bind(app)
        self.exited(owned)

    def test_control_socket_eof_ends_primary_and_bus_without_close_signal(self):
        supervisor, bus, app, owned, ready = self.start("control-eof-bus")
        bus.control.close()
        bus.control = None
        self.exited(owned)

    def test_daemon_hard_death_ends_bound_primary_and_guardian(self):
        supervisor, bus, app, owned, ready = self.start("bus-daemon-kill")
        signal.pidfd_send_signal(owned["daemon"]["fd"], signal.SIGKILL)
        self.exited(owned)

    def test_launch_thread_completion_does_not_end_a_live_sdk_instance(self):
        result, errors = [], []
        def launch():
            try:
                result.append(self.start("threaded-owned-bus"))
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=launch)
        thread.start()
        thread.join(timeout=8)
        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]
        supervisor, bus, app, owned, ready = result[0]
        time.sleep(.15)
        for role, row in owned.items():
            self.assertFalse(select.select([row["fd"]], [], [], 0)[0], role + " died with its launch thread")
        bus.close()
        self.exited(owned)


if __name__ == "__main__":
    unittest.main()
