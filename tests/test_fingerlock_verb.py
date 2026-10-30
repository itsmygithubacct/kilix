"""`kilix fingerlock` — the host-side owner of fingerprint reader access.

Three contracts are pinned here. Discovery is read-only: it reads sysfs and
asks the kernel with access(2), never opening the reader's node. The lazy
install is one fixed apt-get argv run as root only after an explicit yes,
through sudo in the caller's terminal or xterm when there is none. And the
fprintd path issues every polkit-gated call asynchronously with the long
authentication timeout, carries the partial session on failure, and gates
only the calls fprintd can actually hold. The suite builds synthetic /sys,
/dev and /proc trees and a fake bus, so it never needs a reader or a daemon.
"""
import importlib.util
import io
import json
import os
import subprocess
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
PATH = "/net/reactivated/Fprint/Device/0"


def load_module():
    spec = importlib.util.spec_from_file_location("kilix_fingerlock", ROOT / "config" / "fingerlock.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fl = load_module()

HWDB = """# Supported by libfprint driver synaptics
usb:v06CBp00BD*
 ID_AUTOSUSPEND=1
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def build_tree(root: Path, *, hwdb=False, fprintd=False):
    reader = root / "sys/bus/usb/devices/1-9"
    for name, value in {"idVendor": "06cb", "idProduct": "00bd", "bDeviceClass": "ff", "busnum": "1",
                        "devnum": "74", "serial": "TESTSERIAL", "speed": "12",
                        "power/runtime_status": "suspended"}.items():
        write(reader / name, value + "\n")
    write(reader / "1-9:1.0/bInterfaceClass", "ff\n")
    touch = root / "sys/bus/usb/devices/1-5"
    for name, value in {"idVendor": "04f3", "idProduct": "2234", "bDeviceClass": "00", "busnum": "1",
                        "devnum": "5", "product": "Touchscreen"}.items():
        write(touch / name, value + "\n")
    write(touch / "1-5:1.0/bInterfaceClass", "03\n")
    write(root / "dev/bus/usb/001/074", "")
    write(root / "dev/bus/usb/001/005", "")
    write(root / "proc/self/sessionid", "2\n")
    write(root / "proc/42/comm", "pkttyagent\n")
    if hwdb:
        write(root / "usr/lib/udev/hwdb.d/60-autosuspend-libfprint-2.hwdb", HWDB)
    if fprintd:
        write(root / "usr/share/dbus-1/system-services/net.reactivated.Fprint.service", "")


def deny(_path, _mode):
    return False


def fake_run(*, installed=(), active="yes", seat="seat0"):
    def run(argv, **kwargs):
        if argv[0] == "dpkg-query":
            ok = argv[-1] in installed
            return SimpleNamespace(returncode=0 if ok else 1, stdout="installed\n" if ok else "", stderr="")
        if argv[0] == "loginctl":
            return SimpleNamespace(returncode=0, stdout=f"Active={active}\nSeat={seat}\nType=x11\n", stderr="")
        if argv[0] == "journalctl":
            return SimpleNamespace(returncode=0, stdout="2026-01-01T00:00:00+00:00 h kernel: usb 1-9: new full-speed USB device number 3\n", stderr="")
        raise AssertionError(argv)
    return run


class FakeDBusException(Exception):
    def __init__(self, name, message=""):
        super().__init__(message)
        self._name, self._message = name, message

    def get_dbus_name(self):
        return self._name

    def get_dbus_message(self):
        return self._message

    def __str__(self):
        return f"{self._name}: {self._message}" if self._name else self._message


class FakeMatch:
    def __init__(self):
        self.removed = False

    def remove(self):
        self.removed = True


class FakeDevice:
    def __init__(self, *, script=(), enrolled=(), claim_error=None, start_error=None):
        self.script, self.enrolled = list(script), list(enrolled)
        self.claim_error, self.start_error = claim_error, start_error
        self.calls, self.gated, self.handlers, self.matches = [], [], {}, []

    def _respond(self, method, kwargs, value=None, error=None):
        if "reply_handler" in kwargs:
            self.gated.append((method, kwargs.get("timeout")))
            if error is not None:
                kwargs["error_handler"](error)
            elif value is None:
                kwargs["reply_handler"]()
            else:
                kwargs["reply_handler"](value)
            return None
        if error is not None:
            raise error
        return value

    def connect_to_signal(self, name, handler):
        self.handlers[name] = handler
        match = FakeMatch()
        self.matches.append(match)
        return match

    def _deliver(self, signal):
        for result, done in self.script:
            self.handlers[signal](result, done)

    def Claim(self, username, **kw):
        self.calls.append(("Claim", username))
        return self._respond("Claim", kw, error=self.claim_error)

    def Release(self, **kw):
        self.calls.append(("Release",))

    def EnrollStart(self, finger, **kw):
        self.calls.append(("EnrollStart", finger))
        out = self._respond("EnrollStart", kw, error=self.start_error)
        if self.start_error is None:
            self._deliver("EnrollStatus")
        return out

    def EnrollStop(self, **kw):
        self.calls.append(("EnrollStop",))

    def VerifyStart(self, finger, **kw):
        self.calls.append(("VerifyStart", finger))
        out = self._respond("VerifyStart", kw)
        self.handlers["VerifyFingerSelected"]("right-index-finger")
        self._deliver("VerifyStatus")
        return out

    def VerifyStop(self, **kw):
        self.calls.append(("VerifyStop",))

    def ListEnrolledFingers(self, username, **kw):
        if not self.enrolled:
            return self._respond("ListEnrolledFingers", kw,
                                 error=FakeDBusException(fl.ERROR_PREFIX + "NoEnrolledPrints", "none"))
        return self._respond("ListEnrolledFingers", kw, value=list(self.enrolled))

    def DeleteEnrolledFinger(self, finger, **kw):
        self.enrolled = [f for f in self.enrolled if f != finger]
        return self._respond("DeleteEnrolledFinger", kw)

    def DeleteEnrolledFingers2(self, **kw):
        self.enrolled = []
        return self._respond("DeleteEnrolledFingers2", kw)


PROPS = {"name": "Synaptics Sensors", "scan-type": "press", "num-enroll-stages": 8,
         "finger-present": False, "finger-needed": False}


class FakeBus:
    def __init__(self, device=None, *, scan_result=True):
        self.device, self.scan_result = device, scan_result

    def interface(self, path, name):
        if path == fl.MANAGER_PATH:
            paths = [PATH] if self.device is not None else []

            class Manager:
                def GetDevices(self_inner):
                    return list(paths)

                def GetDefaultDevice(self_inner):
                    if not paths:
                        raise FakeDBusException(fl.ERROR_PREFIX + "NoSuchDevice", "No devices available")
                    return paths[0]
            return Manager()
        if name == fl.DEVICE_IFACE:
            return self.device

        class Props:
            def GetAll(self_inner, iface):
                return dict(PROPS)
        return Props()

    def run_until(self, predicate, timeout):
        return True if predicate() else self.scan_result


def client_for(device, **kwargs):
    return fl.FprintdClient(FakeBus(device), **kwargs)


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        status = fl.main(argv)
    return status, out.getvalue(), err.getvalue()


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_reader_is_found_without_opening_its_node(self):
        build_tree(self.root)
        asked = []

        def access(path, mode):
            asked.append((path, mode))
            return False

        readers = fl.list_readers(self.root, access=access)
        self.assertEqual([r.usb_id for r in readers], ["06cb:00bd"])
        reader = readers[0]
        self.assertEqual(reader.label, "Synaptics Prometheus MIS Touch Fingerprint Reader")
        self.assertEqual(reader.node, "/dev/bus/usb/001/074")
        self.assertFalse(reader.node_openable)
        self.assertIsNone(reader.libfprint_driver)
        self.assertEqual(reader.speed_mbps, 12)
        self.assertEqual(asked, [(str(self.root / "dev/bus/usb/001/074"), os.R_OK | os.W_OK)])

    def test_hid_device_from_a_fingerprint_vendor_is_not_a_reader(self):
        build_tree(self.root)
        self.assertEqual([r.sysfs_name for r in fl.list_readers(self.root, access=deny)], ["1-9"])

    def test_hwdb_decides_support(self):
        build_tree(self.root, hwdb=True)
        self.assertEqual(fl.parse_hwdb(HWDB), {("06cb", "00bd"): "synaptics"})
        self.assertEqual(fl.list_readers(self.root, access=deny)[0].libfprint_driver, "synaptics")

    def test_redaction(self):
        build_tree(self.root)
        data = fl.list_readers(self.root, access=deny)[0].as_dict(redact=True)
        for key in ("serial", "sysfs_name", "busnum", "devnum", "node"):
            self.assertEqual(data[key], fl.REDACTED)
        json.dumps(data)

    def test_speed_values(self):
        self.assertEqual(fl._speed("1.5"), 1.5)
        self.assertEqual(fl._speed("480"), 480)
        self.assertIsNone(fl._speed("fast"))


class PackageTests(unittest.TestCase):
    def test_missing_packages_from_dpkg(self):
        run = fake_run(installed=("fprintd",))
        self.assertEqual(fl.missing_packages(("fprintd", "not-installed-pkg"), run=run), ["not-installed-pkg"])

        def broken(argv, **kwargs):
            raise FileNotFoundError("dpkg-query")
        self.assertEqual(fl.missing_packages(("fprintd",), run=broken), ["fprintd"])

    def test_elevated_argv_variants(self):
        argv = ["apt-get", "install", "-y", "--no-install-recommends", "fprintd"]
        which = lambda name: f"/usr/bin/{name}"  # noqa: E731
        self.assertEqual(fl.elevated_argv(argv, title="t", which=which, euid=0), argv)
        self.assertEqual(fl.elevated_argv(argv, title="t", which=which, euid=1000, interactive=True),
                         ["/usr/bin/sudo", "--", *argv])
        self.assertEqual(fl.elevated_argv(argv, title="Install fprintd\n", which=which, euid=1000,
                                          interactive=False, environ={"DISPLAY": ":0"}),
                         ["/usr/bin/xterm", "-T", "Install fprintd", "-e", "/usr/bin/sudo", "--", *argv])
        with self.assertRaises(fl.DependencyUnavailable):
            fl.elevated_argv(argv, title="t", which=which, euid=1000, interactive=False, environ={})
        with self.assertRaises(fl.DependencyUnavailable):
            fl.elevated_argv(argv, title="t", which=lambda n: None, euid=1000, interactive=True)

    def test_install_asks_then_runs_one_fixed_argv(self):
        ran = []

        def run(argv, **kwargs):
            ran.append(argv)
            return SimpleNamespace(returncode=0)
        with unittest.mock.patch.object(fl, "elevated_argv", side_effect=lambda argv, title: ["sudo", "--", *argv]):
            with redirect_stderr(io.StringIO()):
                fl.install_packages(["fprintd"], yes=True, want_json=False, run=run)
                with self.assertRaises(fl.DependencyUnavailable):
                    fl.install_packages(["fprintd"], yes=False, want_json=True, run=run)
                with unittest.mock.patch.object(fl, "_interactive_terminal", return_value=True):
                    with self.assertRaises(fl.DependencyUnavailable):
                        fl.install_packages(["fprintd"], yes=False, want_json=False, run=run, ask=lambda p: "n")
                    fl.install_packages(["fprintd"], yes=False, want_json=False, run=run, ask=lambda p: "y")
        self.assertEqual(ran, [["sudo", "--", "apt-get", "install", "-y", "--no-install-recommends", "fprintd"]] * 2)

    def test_install_failure_is_typed(self):
        with unittest.mock.patch.object(fl, "elevated_argv", side_effect=lambda argv, title: argv):
            with redirect_stderr(io.StringIO()), self.assertRaises(fl.DependencyUnavailable):
                fl.install_packages(["fprintd"], yes=True, want_json=False,
                                    run=lambda argv, **k: SimpleNamespace(returncode=100))


class ClientTests(unittest.TestCase):
    def test_enroll_gates_claim_and_start_only(self):
        device = FakeDevice(script=[("enroll-stage-passed", False), ("enroll-completed", True)])
        started = []
        session = client_for(device, auth_timeout=42).enroll(PATH, "left-thumb", on_start=lambda s: started.append(1))
        self.assertTrue(session.succeeded)
        self.assertEqual(started, [1])
        self.assertEqual(device.gated, [("Claim", 42), ("EnrollStart", 42)])
        self.assertEqual(device.calls[-2:], [("EnrollStop",), ("Release",)])
        self.assertTrue(all(m.removed for m in device.matches))

    def test_verify_start_is_not_gated(self):
        device = FakeDevice(script=[("verify-match", True)])
        session = client_for(device, auth_timeout=7).verify(PATH)
        self.assertTrue(session.succeeded)
        self.assertEqual(session.selected_finger, "right-index-finger")
        self.assertEqual(device.gated, [("Claim", 7)])

    def test_unanswered_prompt_is_a_timeout_with_the_session(self):
        device = FakeDevice(start_error=FakeDBusException(fl.NO_REPLY, "Did not receive a reply."))
        with self.assertRaises(fl.OperationTimeout) as caught:
            client_for(device).enroll(PATH, "left-thumb")
        self.assertIn("authentication prompt", caught.exception.message)
        self.assertEqual(caught.exception.session.kind, "enroll")
        self.assertEqual(device.calls[-2:], [("EnrollStop",), ("Release",)])

    def test_errors_carry_the_dbus_name_once(self):
        error = fl.translate_error(FakeDBusException(fl.ERROR_PREFIX + "PermissionDenied", "Not allowed"))
        self.assertIsInstance(error, fl.AccessDenied)
        self.assertEqual(error.message, "PermissionDenied: Not allowed")
        self.assertEqual(error.dbus_name, fl.ERROR_PREFIX + "PermissionDenied")
        self.assertIsInstance(fl.translate_error(FakeDBusException(None, "plain")), fl.ServiceFailure)
        self.assertIsInstance(fl.translate_error(FakeDBusException("org.freedesktop.DBus.Error.ServiceUnknown")),
                              fl.DependencyUnavailable)

    def test_enrolled_fingers_and_delete(self):
        device = FakeDevice(enrolled=["left-thumb", "right-thumb"])
        client = client_for(device)
        self.assertEqual(client.enrolled_fingers(PATH), ["left-thumb", "right-thumb"])
        client.delete(PATH, "left-thumb")
        client.delete(PATH)
        self.assertEqual(client.enrolled_fingers(PATH), [])
        self.assertEqual(device.gated[-1], ("ListEnrolledFingers", fl.AUTH_TIMEOUT_SECONDS))

    def test_scan_timeout(self):
        device = FakeDevice()
        client = fl.FprintdClient(FakeBus(device, scan_result=False))
        session = client.enroll(PATH, "left-thumb", timeout=1)
        self.assertTrue(session.timed_out)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def doctor(self, *, client=None, run=None, **kwargs):
        return fl.run_doctor(root=self.root, client=client, environ={"XDG_SESSION_ID": "2"},
                             run=run or fake_run(installed=("fprintd",)), proc=self.root / "proc",
                             access=deny, **kwargs)

    def statuses(self, report):
        return {c.id: c.status for c in report.checks}

    def test_bare_host_names_the_install_step(self):
        build_tree(self.root)
        report = self.doctor(run=fake_run(installed=()))
        status = self.statuses(report)
        self.assertEqual(status["reader"], "ok")
        self.assertEqual(status["packages"], "fail")
        self.assertEqual(status["libfprint"], "fail")
        self.assertEqual(status["fprintd-reachable"], "skip")
        self.assertEqual(report.missing_packages, ["fprintd"])
        self.assertFalse(report.usable)
        self.assertEqual({c.id: c.hint for c in report.checks}["packages"], "kilix fingerlock install")

    def test_full_stack_is_usable(self):
        build_tree(self.root, hwdb=True, fprintd=True)
        report = self.doctor(client=client_for(FakeDevice(enrolled=["left-thumb"])))
        status = self.statuses(report)
        self.assertEqual(status["support"], "ok")
        self.assertEqual(status["fprintd-reachable"], "ok")
        self.assertEqual(status["session"], "ok")
        self.assertEqual(status["polkit-agent"], "ok")
        self.assertTrue(report.usable)
        self.assertEqual(report.enrolled, {PATH: ["left-thumb"]})
        self.assertEqual(report.as_dict(redact=True)["readers"][0]["serial"], fl.REDACTED)

    def test_doctor_does_not_require_login_packages(self):
        build_tree(self.root, hwdb=True, fprintd=True)
        report = self.doctor(client=client_for(FakeDevice()))
        self.assertTrue(report.usable)
        self.assertEqual(report.missing_packages, [])
        self.assertNotIn("pam", self.statuses(report))

    def test_no_reader(self):
        report = self.doctor()
        self.assertEqual(self.statuses(report)["reader"], "fail")
        self.assertFalse(report.usable)


class CommandTests(unittest.TestCase):
    def test_help_and_usage(self):
        status, out, _ = run_main(["--help"])
        self.assertEqual(status, 0)
        self.assertIn("ensure", out)
        status, _, err = run_main(["install", "--pam"])
        self.assertEqual(status, 2)
        self.assertIn("unrecognized arguments", err)
        status, _, err = run_main(["enroll", "nose"])
        self.assertEqual(status, 2)
        status, out, _ = run_main(["delete", "--json"])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(out)["code"], "EUSAGE")

    def test_ensure_installs_then_reports(self):
        calls = []
        missing = {"n": 1}

        def run_doctor(**kwargs):
            report = fl.Report(readers=[fl.list_readers.__globals__["UsbReader"](
                "1-9", 1, 74, "06cb", "00bd", "Synaptics", "Reader", None, None, 12, "ff", ("ff",),
                "/dev/bus/usb/001/074", "0664", "root:root", False, "suspended", "synaptics")])
            if missing["n"]:
                report.missing_packages = ["fprintd"]
                report.checks.append(fl.Check("packages", "fail", "missing"))
            else:
                for name in fl.Report.REQUIRED:
                    report.checks.append(fl.Check(name, "ok", "ok"))
                report.fprintd_devices = [fl.DeviceInfo(PATH, "Synaptics Sensors", "press", 9, False, False)]
                report.enrolled = {PATH: []}
            return report

        def install_packages(packages, **kwargs):
            calls.append((list(packages), kwargs["yes"]))
            missing["n"] = 0

        with unittest.mock.patch.object(fl, "run_doctor", side_effect=run_doctor), \
             unittest.mock.patch.object(fl, "install_packages", side_effect=install_packages):
            status, out, err = run_main(["--yes"])
        self.assertEqual(status, 0)
        self.assertEqual(calls, [(["fprintd"], True)])
        self.assertIn("not installed yet", err)
        self.assertIn("none yet; run: kilix fingerlock enroll right-index-finger", out)

    def test_ensure_without_a_reader_is_enodevice(self):
        with unittest.mock.patch.object(fl, "run_doctor", return_value=fl.Report()):
            status, out, _ = run_main(["--json"])
        self.assertEqual(status, 3)
        payload = json.loads(out)
        self.assertEqual(payload["schema"], fl.SCHEMA)
        self.assertEqual(payload["action"], "ensure")
        self.assertEqual(payload["code"], "ENODEVICE")

    def test_enroll_and_verify_through_a_fake_client(self):
        device = FakeDevice(script=[("enroll-stage-passed", False), ("enroll-completed", True)])
        with unittest.mock.patch.object(fl, "_client", return_value=client_for(device)):
            status, out, err = run_main(["enroll"])
        self.assertEqual(status, 0)
        self.assertIn("enrolled right-index-finger", out)
        self.assertLess(err.index("claiming"), err.index("touch the sensor"))
        device = FakeDevice(script=[("verify-no-match", True)])
        with unittest.mock.patch.object(fl, "_client", return_value=client_for(device)):
            status, out, _ = run_main(["verify", "--json"])
        self.assertEqual(status, 8)
        payload = json.loads(out)
        self.assertEqual(payload["code"], "ENOMATCH")
        self.assertEqual(payload["data"]["result"], "verify-no-match")

    def test_client_error_midway_carries_session_in_json(self):
        device = FakeDevice(start_error=FakeDBusException(fl.ERROR_PREFIX + "PermissionDenied", "not allowed"))
        with unittest.mock.patch.object(fl, "_client", return_value=client_for(device)):
            status, out, _ = run_main(["enroll", "left-thumb", "--json"])
        self.assertEqual(status, 5)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "PermissionDenied: not allowed")
        self.assertEqual(payload["data"]["kind"], "enroll")

    def test_list_and_delete(self):
        device = FakeDevice(enrolled=["left-thumb", "right-thumb"])
        with unittest.mock.patch.object(fl, "_client", return_value=client_for(device)):
            status, out, _ = run_main(["list", "--json"])
            self.assertEqual(json.loads(out)["data"]["fingers"], ["left-thumb", "right-thumb"])
            status, out, _ = run_main(["delete", "left-thumb"])
            self.assertEqual(status, 0)
            self.assertIn("deleted left-thumb; 1 finger(s) remain", out)

    def test_launcher_reaches_the_module(self):
        env = {"HOME": tempfile.gettempdir(), "PATH": "/usr/bin:/bin", "TERM": "xterm", "KILIX_HOME": str(ROOT)}
        result = subprocess.run([str(ROOT / "kilix"), "fingerlock", "--help"], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("install fprintd on first use", result.stdout)


class TtyAgentTests(unittest.TestCase):
    def _spawn(self, *, registers_after):
        state = {}

        def spawn(argv, pass_fds=()):
            state["argv"] = argv
            inherited = os.dup(pass_fds[0])
            if registers_after is None:
                state["leak"] = inherited
            else:
                threading.Timer(registers_after, os.close, args=(inherited,)).start()
            return SimpleNamespace(poll=lambda: 0)
        return spawn, state

    def test_waits_for_registration(self):
        spawn, state = self._spawn(registers_after=0.15)
        started = time.monotonic()
        with fl.TtyAgent(True, spawn=spawn) as agent:
            self.assertTrue(agent.registered)
            self.assertGreaterEqual(time.monotonic() - started, 0.1)
        self.assertEqual(state["argv"][:3], ["pkttyagent", "--process", str(os.getpid())])

    def test_gives_up_in_time(self):
        spawn, state = self._spawn(registers_after=None)
        agent = fl.TtyAgent(True, spawn=spawn)
        agent.REGISTER_TIMEOUT = 0.1
        with redirect_stderr(io.StringIO()):
            with agent:
                self.assertFalse(agent.registered)
        os.close(state["leak"])

    def test_disabled_is_a_no_op(self):
        with fl.TtyAgent(False) as agent:
            self.assertIsNone(agent.process)


if __name__ == "__main__":
    unittest.main()
