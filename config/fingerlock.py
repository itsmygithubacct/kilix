#!/usr/bin/env python3
"""kilix fingerlock — the fingerprint reader as a host verb.

`kilix fingerlock` finds the machine's fingerprint reader, installs what
Debian needs the first time it is asked to, and then enrolls, verifies, lists
and deletes fingers through the distribution's fingerprint daemon. This module
is that host verb. A successful verify checks the reader; it is not a
screen-lock or login token.

Access model. A libfprint-driven reader keeps a root-only USB node on
purpose; this module never opens it. User access goes through fprintd over
the system D-Bus, behind polkit: verifying is allowed from an active seat
session, enrolling and deleting also need the caller's password through a
polkit authentication agent, and acting for another user needs an
administrator's. `--tty-agent` runs pkttyagent for the duration of one
command when the session has no resident agent.

Lazy installation. The only package this verb installs is Debian's `fprintd`,
with one fixed argv, `apt-get install -y --no-install-recommends`, elevated
through sudo in the caller's terminal, or through xterm when the caller has
none, and only after an explicit yes. Plebian OS points apt at a dated
snapshot, so the versions this installs are the release's, not today's. No
repository is added and no recommends are pulled, and installing `fprintd`
does not change how login works.

Exit statuses are stable: 0 success, 1 not usable, 2 usage, 3 no reader or
no fprintd device, 4 a dependency is missing or was refused, 5 polkit or
fprintd refused the caller, 6 fprintd failed, 7 an enrollment ended without
completing, 8 verification found no match, 9 a timeout. `--json` emits one
`kilix.fingerlock/v1` object on stdout; prompts and scan progress go to
stderr in both modes.
"""

from __future__ import annotations

import argparse
import glob
import grp
import json
import os
import pwd
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

SCHEMA = "kilix.fingerlock/v1"
REDACTED = "<redacted>"
PROG = "kilix fingerlock"

# --- packages ---------------------------------------------------------------

PACKAGES: tuple[str, ...] = ("fprintd",)
INSTALL_ARGV: tuple[str, ...] = ("apt-get", "install", "-y", "--no-install-recommends")
INSTALL_HINT = f"{PROG} install"
BINDINGS_HINT = "apt: python3-dbus python3-gi"

FINGERS: tuple[str, ...] = (
    "left-thumb", "left-index-finger", "left-middle-finger", "left-ring-finger",
    "left-little-finger", "right-thumb", "right-index-finger",
    "right-middle-finger", "right-ring-finger", "right-little-finger",
)
ANY_FINGER = "any"
DEFAULT_ENROLL_FINGER = "right-index-finger"


# --- errors -------------------------------------------------------------------

class FingerlockError(Exception):
    code = "EUNKNOWN"
    exit_status = 1
    dbus_name = ""
    session: Any = None

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message or self.code


class UsageError(FingerlockError):
    code, exit_status = "EUSAGE", 2


class DeviceNotFound(FingerlockError):
    code, exit_status = "ENODEVICE", 3


class DependencyUnavailable(FingerlockError):
    code, exit_status = "EDEPENDENCY", 4


class AccessDenied(FingerlockError):
    code, exit_status = "EACCESS", 5


class ServiceFailure(FingerlockError):
    code, exit_status = "ESERVICE", 6


class OperationFailed(FingerlockError):
    code, exit_status = "EOPERATION", 7


class NoMatch(FingerlockError):
    code, exit_status = "ENOMATCH", 8


class OperationTimeout(FingerlockError):
    code, exit_status = "ETIMEOUT", 9


# --- USB discovery (read-only) ------------------------------------------------

FINGERPRINT_VENDORS: Mapping[str, str] = {
    "04f3": "Elan Microelectronics", "05ba": "DigitalPersona", "06cb": "Synaptics",
    "08ff": "AuthenTec", "10a5": "Fingerprint Cards", "138a": "Validity Sensors",
    "147e": "Upek", "1491": "Futronic", "1c7a": "LighTuning", "27c6": "Goodix",
    "2808": "Focal-systems", "298d": "Next Biometrics",
}
OBSERVED_PRODUCTS: Mapping[tuple[str, str], str] = {
    ("06cb", "00bd"): "Synaptics Prometheus MIS Touch Fingerprint Reader",
}
HWDB_RELATIVE_PATHS = (
    "usr/lib/udev/hwdb.d/60-autosuspend-libfprint-2.hwdb",
    "etc/udev/hwdb.d/60-autosuspend-libfprint-2.hwdb",
)
LIBFPRINT_LIBRARY_GLOBS = (
    "usr/lib/*/libfprint-2.so.2", "usr/lib/libfprint-2.so.2", "usr/lib64/libfprint-2.so.2",
)
FPRINTD_SERVICE_FILE = "usr/share/dbus-1/system-services/net.reactivated.Fprint.service"
FPRINTD_UNIT_FILE = "usr/lib/systemd/system/fprintd.service"
VENDOR_SPECIFIC_CLASS = "ff"
HWDB_DRIVER_MARKER = "# Supported by libfprint driver "


@dataclass(frozen=True)
class UsbReader:
    sysfs_name: str
    busnum: int
    devnum: int
    vendor_id: str
    product_id: str
    vendor_name: str | None
    product_name: str | None
    product: str | None
    serial: str | None
    speed_mbps: float | None
    device_class: str | None
    interface_classes: tuple[str, ...]
    node: str
    node_mode: str | None
    node_owner: str | None
    node_openable: bool | None
    runtime_status: str | None
    libfprint_driver: str | None

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id}:{self.product_id}"

    @property
    def label(self) -> str:
        if self.product_name:
            return self.product_name
        if self.product:
            return self.product
        return f"{self.vendor_name or self.vendor_id} device {self.usb_id}"

    def as_dict(self, *, redact: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "usb_id": self.usb_id, "vendor_id": self.vendor_id, "product_id": self.product_id,
            "vendor_name": self.vendor_name, "product_name": self.product_name,
            "label": self.label, "product": self.product, "serial": self.serial,
            "speed_mbps": self.speed_mbps, "device_class": self.device_class,
            "interface_classes": list(self.interface_classes),
            "sysfs_name": self.sysfs_name, "busnum": self.busnum, "devnum": self.devnum,
            "node": self.node, "node_mode": self.node_mode, "node_owner": self.node_owner,
            "node_openable": self.node_openable, "runtime_status": self.runtime_status,
            "libfprint_driver": self.libfprint_driver,
        }
        if redact:
            for key in ("serial", "sysfs_name", "busnum", "devnum", "node"):
                data[key] = REDACTED
        return data


def parse_hwdb(text: str) -> dict[tuple[str, str], str]:
    """Map (vendor, product) to the libfprint driver named in its hwdb."""
    driver: str | None = None
    table: dict[tuple[str, str], str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(HWDB_DRIVER_MARKER):
            driver = line[len(HWDB_DRIVER_MARKER):].strip() or None
            continue
        if not line.startswith("usb:v") or driver is None:
            continue
        vendor, _sep, product = line[len("usb:v"):].rstrip("*").partition("p")
        if len(vendor) == 4 and len(product) == 4:
            table[(vendor.lower(), product.lower())] = driver
    return table


def hwdb_path(root: str | os.PathLike[str] = "/") -> Path | None:
    for relative in HWDB_RELATIVE_PATHS:
        candidate = Path(root) / relative
        if candidate.is_file():
            return candidate
    return None


def load_hwdb(root: str | os.PathLike[str] = "/") -> tuple[Path | None, dict[tuple[str, str], str]]:
    path = hwdb_path(root)
    if path is None:
        return None, {}
    try:
        return path, parse_hwdb(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return path, {}


def _first_glob(root: str | os.PathLike[str], patterns: Iterable[str]) -> Path | None:
    for pattern in patterns:
        matches = sorted(glob.glob(str(Path(root) / pattern)))
        if matches:
            return Path(matches[0])
    return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _speed(text: str | None) -> float | None:
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def _owner_name(lookup: Callable[[int], tuple], ident: int) -> str:
    try:
        return str(lookup(ident)[0])
    except KeyError:
        return str(ident)


def _node_facts(node: Path, access: Callable[[str, int], bool]) -> tuple[str | None, str | None, bool | None]:
    try:
        st = node.stat()
    except OSError:
        return None, None, None
    mode = f"{stat.S_IMODE(st.st_mode):04o}"
    owner = f"{_owner_name(pwd.getpwuid, st.st_uid)}:{_owner_name(grp.getgrgid, st.st_gid)}"
    try:
        openable: bool | None = bool(access(str(node), os.R_OK | os.W_OK))
    except OSError:
        openable = None
    return mode, owner, openable


def list_readers(
    root: str | os.PathLike[str] = "/",
    *,
    hwdb: Mapping[tuple[str, str], str] | None = None,
    access: Callable[[str, int], bool] = os.access,
) -> list[UsbReader]:
    """Every USB device libfprint lists, or that looks like a reader."""
    base = Path(root)
    if hwdb is None:
        hwdb = load_hwdb(base)[1]
    devices_dir = base / "sys/bus/usb/devices"
    if not devices_dir.is_dir():
        return []
    readers: list[UsbReader] = []
    for entry in sorted(devices_dir.iterdir(), key=lambda p: p.name):
        name = entry.name
        if ":" in name:
            continue
        vendor = (_read(entry / "idVendor") or "").lower()
        product = (_read(entry / "idProduct") or "").lower()
        if not vendor or not product:
            continue
        device_class = (_read(entry / "bDeviceClass") or "").lower() or None
        interface_classes = tuple(
            (_read(iface / "bInterfaceClass") or "?").lower()
            for iface in sorted(entry.glob(f"{name}:*"), key=lambda p: p.name)
        )
        driver = hwdb.get((vendor, product))
        vendor_specific = device_class == VENDOR_SPECIFIC_CLASS or VENDOR_SPECIFIC_CLASS in interface_classes
        if driver is None and not (vendor in FINGERPRINT_VENDORS and vendor_specific):
            continue
        busnum = int(_read(entry / "busnum") or 0)
        devnum = int(_read(entry / "devnum") or 0)
        node = base / "dev/bus/usb" / f"{busnum:03d}" / f"{devnum:03d}"
        mode, owner, openable = _node_facts(node, access)
        readers.append(UsbReader(
            sysfs_name=name, busnum=busnum, devnum=devnum,
            vendor_id=vendor, product_id=product,
            vendor_name=FINGERPRINT_VENDORS.get(vendor),
            product_name=OBSERVED_PRODUCTS.get((vendor, product)),
            product=_read(entry / "product") or None,
            serial=_read(entry / "serial") or None,
            speed_mbps=_speed(_read(entry / "speed")),
            device_class=device_class, interface_classes=interface_classes,
            node="/" + str(node.relative_to(base)),
            node_mode=mode, node_owner=owner, node_openable=openable,
            runtime_status=_read(entry / "power/runtime_status") or None,
            libfprint_driver=driver,
        ))
    return readers


# --- kernel log ---------------------------------------------------------------

@dataclass(frozen=True)
class PortHistory:
    port: str
    enumerations: int | None
    last_enumeration: str | None
    note: str | None

    def as_dict(self, *, redact: bool = False) -> dict[str, Any]:
        return {"port": REDACTED if redact else self.port,
                "enumerations_this_boot": self.enumerations,
                "last_enumeration": self.last_enumeration, "note": self.note}


def port_history(port: str, *, run: Callable[..., Any] = subprocess.run) -> PortHistory:
    """How often the kernel enumerated a USB port since boot."""
    try:
        proc = run(["journalctl", "-k", "-b", "-o", "short-iso", "--no-pager"],
                   capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return PortHistory(port, None, None, "journalctl is not available")
    except subprocess.TimeoutExpired:
        return PortHistory(port, None, None, "journalctl timed out")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return PortHistory(port, None, None, detail[0] if detail else f"journalctl exit {proc.returncode}")
    pattern = re.compile(rf"^(\S+)\s.*\busb {re.escape(port)}: new .*device number \d+")
    count, last = 0, None
    for line in proc.stdout.splitlines():
        match = pattern.match(line)
        if match:
            count += 1
            last = match.group(1)
    return PortHistory(port, count, last, None)


# --- Debian packages ------------------------------------------------------------

def package_installed(package: str, *, run: Callable[..., Any] = subprocess.run) -> bool | None:
    """True/False from dpkg, None when dpkg cannot say."""
    try:
        proc = run(["dpkg-query", "-W", "-f=${db:Status-Status}", package],
                   capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return False
    return proc.stdout.strip() == "installed"


def missing_packages(packages: Iterable[str], *, run: Callable[..., Any] = subprocess.run) -> list[str]:
    return [package for package in packages if package_installed(package, run=run) is not True]


def _interactive_terminal() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)()
                and getattr(sys.stderr, "isatty", lambda: False)())


def elevated_argv(argv: Sequence[str], *, title: str,
                  which: Callable[[str], str | None] = shutil.which,
                  environ: Mapping[str, str] | None = None,
                  euid: int | None = None,
                  interactive: bool | None = None) -> list[str]:
    """One fixed argv, run as root: directly, via sudo here, or via sudo in xterm."""
    environ = os.environ if environ is None else environ
    euid = os.geteuid() if euid is None else euid
    if euid == 0:
        return list(argv)
    sudo = which("sudo")
    if not sudo:
        raise DependencyUnavailable("sudo is required to install packages")
    interactive = _interactive_terminal() if interactive is None else interactive
    if interactive:
        return [sudo, "--", *argv]
    xterm = which("xterm")
    if not environ.get("DISPLAY") or not xterm:
        raise DependencyUnavailable("installing needs a terminal, or an X display with xterm")
    clean_title = "".join(c for c in title if c >= " " and c not in "\x7f\n\r").strip() or "Install"
    return [xterm, "-T", clean_title, "-e", sudo, "--", *argv]


def install_packages(packages: Sequence[str], *, yes: bool, want_json: bool,
                     run: Callable[..., Any] = subprocess.run,
                     ask: Callable[[str], str] = input) -> None:
    """Install Debian packages after an explicit confirmation."""
    if not packages:
        return
    argv = [*INSTALL_ARGV, *packages]
    shown = " ".join(argv)
    if not yes:
        if want_json or not _interactive_terminal():
            raise DependencyUnavailable(
                f"{', '.join(packages)} not installed; rerun with --yes to run: sudo {shown}")
        sys.stderr.write(f"{PROG}: about to run as root: {shown}\n")
        try:
            answer = ask("proceed? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            raise DependencyUnavailable("installation declined")
    full = elevated_argv(argv, title=f"Install {' '.join(packages)}")
    sys.stderr.write(f"{PROG}: running {' '.join(full)}\n")
    proc = run(full)
    if proc.returncode != 0:
        raise DependencyUnavailable(f"package installation failed (exit {proc.returncode})")


# --- fprintd over D-Bus ---------------------------------------------------------

BUS_NAME = "net.reactivated.Fprint"
MANAGER_PATH = "/net/reactivated/Fprint/Manager"
MANAGER_IFACE = "net.reactivated.Fprint.Manager"
DEVICE_IFACE = "net.reactivated.Fprint.Device"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
ERROR_PREFIX = "net.reactivated.Fprint.Error."
NO_REPLY = "org.freedesktop.DBus.Error.NoReply"
SERVICE_ABSENT = ("org.freedesktop.DBus.Error.ServiceUnknown",
                  "org.freedesktop.DBus.Error.NameHasNoOwner")
ENROLL_COMPLETED = "enroll-completed"
VERIFY_MATCH = "verify-match"
VERIFY_NO_MATCH = "verify-no-match"
ENROLL_TERMINAL = frozenset({"enroll-completed", "enroll-failed", "enroll-data-full",
                             "enroll-duplicate", "enroll-disconnected", "enroll-unknown-error"})
VERIFY_TERMINAL = frozenset({"verify-match", "verify-no-match", "verify-disconnected",
                             "verify-unknown-error"})
STATUS_TEXT = {
    "enroll-stage-passed": "stage passed; lift the finger and touch again",
    "enroll-retry-scan": "scan not usable; touch again",
    "enroll-swipe-too-short": "swipe too short; try again",
    "enroll-finger-not-centered": "finger not centered; try again",
    "enroll-remove-and-retry": "remove the finger, then touch again",
    "enroll-completed": "enrollment completed",
    "enroll-failed": "enrollment failed",
    "enroll-data-full": "the reader's print storage is full",
    "enroll-duplicate": "this finger is already enrolled",
    "enroll-disconnected": "the reader disconnected",
    "enroll-unknown-error": "unknown reader error",
    "verify-match": "match",
    "verify-no-match": "no match",
    "verify-retry-scan": "scan not usable; touch again",
    "verify-swipe-too-short": "swipe too short; try again",
    "verify-finger-not-centered": "finger not centered; try again",
    "verify-remove-and-retry": "remove the finger, then touch again",
    "verify-disconnected": "the reader disconnected",
    "verify-unknown-error": "unknown reader error",
}
# Calls fprintd may hold while a polkit agent collects a password: Claim and
# ListEnrolledFingers when acting for another user, EnrollStart and the delete
# calls always. They are issued asynchronously with this timeout while the
# GLib loop runs, which keeps Ctrl-C working. VerifyStart never prompts.
AUTH_TIMEOUT_SECONDS = 600.0


def describe(result: str) -> str:
    return STATUS_TEXT.get(result, result)


def bindings_available() -> tuple[bool, str]:
    try:
        import dbus  # noqa: F401
        import dbus.mainloop.glib  # noqa: F401
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib  # noqa: F401
    except Exception as exc:  # ImportError, ValueError from require_version
        return False, f"{exc.__class__.__name__}: {exc}"
    return True, "python3-dbus and python3-gi are importable"


class BusLike(Protocol):
    def interface(self, path: str, name: str) -> Any: ...

    def run_until(self, predicate: Callable[[], bool], timeout: float) -> bool: ...


class SystemBus:
    """dbus-python system bus with a GLib loop for signals and replies."""

    def __init__(self) -> None:
        ok, reason = bindings_available()
        if not ok:
            raise DependencyUnavailable(f"D-Bus bindings unavailable ({reason}); {BINDINGS_HINT}")
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
        DBusGMainLoop(set_as_default=True)
        self._dbus, self._glib = dbus, GLib
        self._bus = dbus.SystemBus()

    def interface(self, path: str, name: str) -> Any:
        return self._dbus.Interface(self._bus.get_object(BUS_NAME, path), name)

    def run_until(self, predicate: Callable[[], bool], timeout: float) -> bool:
        if predicate():
            return True
        loop = self._glib.MainLoop()
        state = {"timed_out": False, "interrupted": False, "finished": False}

        def on_timeout() -> bool:
            state["timed_out"] = True
            loop.quit()
            return False

        def poll() -> bool:
            if predicate():
                state["finished"] = True
                loop.quit()
                return False
            return True

        def on_sigint() -> bool:
            state["interrupted"] = True
            loop.quit()
            return False

        timeout_id = self._glib.timeout_add(max(1, int(timeout * 1000)), on_timeout)
        poll_id = self._glib.timeout_add(100, poll)
        sig_id = self._glib.unix_signal_add(self._glib.PRIORITY_DEFAULT, signal.SIGINT, on_sigint)
        try:
            loop.run()
        finally:
            # A callback that returns false has already removed its source.
            if not state["timed_out"]:
                self._glib.source_remove(timeout_id)
            if not state["finished"]:
                self._glib.source_remove(poll_id)
            if not state["interrupted"]:
                self._glib.source_remove(sig_id)
        if state["interrupted"]:
            raise KeyboardInterrupt
        return not state["timed_out"]


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    name: str
    scan_type: str
    num_enroll_stages: int
    finger_present: bool | None
    finger_needed: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "name": self.name, "scan_type": self.scan_type,
                "num_enroll_stages": self.num_enroll_stages,
                "finger_present": self.finger_present, "finger_needed": self.finger_needed}


@dataclass(frozen=True)
class StatusEvent:
    result: str
    done: bool
    elapsed: float

    def as_dict(self) -> dict[str, Any]:
        return {"result": self.result, "done": self.done, "elapsed": self.elapsed,
                "text": describe(self.result)}


@dataclass
class Session:
    """One enrollment or verification, fed by fprintd status signals."""

    kind: str
    finger: str
    device: str
    events: list[StatusEvent] = field(default_factory=list)
    result: str | None = None
    done: bool = False
    timed_out: bool = False
    selected_finger: str | None = None
    started: float = field(default_factory=time.monotonic)

    @property
    def succeeded(self) -> bool:
        return self.result == (ENROLL_COMPLETED if self.kind == "enroll" else VERIFY_MATCH)

    @property
    def stages_passed(self) -> int:
        return sum(1 for event in self.events if event.result == "enroll-stage-passed")

    def handle(self, result: str, done: bool) -> bool:
        result, done = str(result), bool(done)
        self.events.append(StatusEvent(result, done, round(time.monotonic() - self.started, 3)))
        terminal = ENROLL_TERMINAL if self.kind == "enroll" else VERIFY_TERMINAL
        if done or result in terminal:
            self.done, self.result = True, result
        return self.done

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "finger": self.finger, "device": self.device,
                "result": self.result, "result_text": describe(self.result) if self.result else None,
                "succeeded": self.succeeded, "done": self.done, "timed_out": self.timed_out,
                "stages_passed": self.stages_passed, "selected_finger": self.selected_finger,
                "events": [event.as_dict() for event in self.events]}


def translate_error(exc: BaseException) -> FingerlockError:
    """Map a D-Bus exception onto a typed failure carrying the D-Bus name."""
    name_getter = getattr(exc, "get_dbus_name", None)
    name = (name_getter() if callable(name_getter) else None) or ""
    message_getter = getattr(exc, "get_dbus_message", None)
    message = (message_getter() if callable(message_getter) else str(exc)) or ""
    message = message.strip()
    if name in SERVICE_ABSENT:
        error: FingerlockError = DependencyUnavailable(
            "fprintd is not installed or cannot be activated on the system bus")
    elif name == "org.freedesktop.DBus.Error.AccessDenied":
        error = AccessDenied(message or name)
    elif name.startswith(ERROR_PREFIX):
        short = name[len(ERROR_PREFIX):]
        cls = {"PermissionDenied": AccessDenied, "NoSuchDevice": DeviceNotFound,
               "InvalidFingername": UsageError}.get(short, ServiceFailure)
        error = cls(f"{short}: {message}" if message else short)
    elif name:
        error = ServiceFailure(f"{name}: {message}" if message else name)
    else:
        error = ServiceFailure(message or exc.__class__.__name__)
    error.dbus_name = name
    return error


StatusCallback = Callable[[StatusEvent, Session], None]
StartCallback = Callable[[Session], None]


class FprintdClient:
    def __init__(self, bus: BusLike | None = None, *, auth_timeout: float = AUTH_TIMEOUT_SECONDS) -> None:
        self._bus = bus
        self.auth_timeout = auth_timeout

    @property
    def bus(self) -> BusLike:
        if self._bus is None:
            self._bus = SystemBus()
        return self._bus

    @staticmethod
    def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except FingerlockError:
            raise
        except Exception as exc:
            if hasattr(exc, "get_dbus_name"):
                raise translate_error(exc) from exc
            raise

    def _call_gated(self, fn: Callable[..., Any], *args: Any) -> Any:
        outcome: dict[str, Any] = {}
        self._call(fn, *args,
                   reply_handler=lambda *values: outcome.__setitem__("values", values),
                   error_handler=lambda exc: outcome.__setitem__("error", exc),
                   timeout=self.auth_timeout)
        if not self.bus.run_until(lambda: bool(outcome), self.auth_timeout + 5.0):
            raise OperationTimeout(f"fprintd did not answer within {self.auth_timeout:g}s")
        if "error" in outcome:
            error = translate_error(outcome["error"])
            if error.dbus_name == NO_REPLY:
                raise OperationTimeout(f"fprintd did not answer within {self.auth_timeout:g}s; "
                                       "an authentication prompt may be unanswered") from outcome["error"]
            raise error
        values = outcome["values"]
        return values[0] if len(values) == 1 else (values or None)

    def _iface(self, path: str, name: str) -> Any:
        return self._call(self.bus.interface, path, name)

    @staticmethod
    def _quiet(fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception:
            pass

    def _device_info(self, path: str) -> DeviceInfo:
        props = self._call(self._iface(path, PROPERTIES_IFACE).GetAll, DEVICE_IFACE)
        return DeviceInfo(
            path=str(path), name=str(props.get("name", "")),
            scan_type=str(props.get("scan-type", "")),
            num_enroll_stages=int(props.get("num-enroll-stages", 0)),
            finger_present=bool(props["finger-present"]) if "finger-present" in props else None,
            finger_needed=bool(props["finger-needed"]) if "finger-needed" in props else None,
        )

    def list_devices(self) -> list[DeviceInfo]:
        manager = self._iface(MANAGER_PATH, MANAGER_IFACE)
        return [self._device_info(str(path)) for path in self._call(manager.GetDevices)]

    def default_device(self) -> DeviceInfo:
        manager = self._iface(MANAGER_PATH, MANAGER_IFACE)
        try:
            path = self._call(manager.GetDefaultDevice)
        except ServiceFailure as exc:
            raise DeviceNotFound(exc.message) from exc
        return self._device_info(str(path))

    def enrolled_fingers(self, device_path: str, username: str = "") -> list[str]:
        device = self._iface(device_path, DEVICE_IFACE)
        try:
            fingers = self._call_gated(device.ListEnrolledFingers, username)
        except ServiceFailure as exc:
            if exc.dbus_name == ERROR_PREFIX + "NoEnrolledPrints":
                return []
            raise
        return [str(finger) for finger in fingers or ()]

    def _run_action(self, kind: str, device_path: str, finger: str, *, username: str,
                    timeout: float, on_start: StartCallback | None,
                    on_status: StatusCallback | None) -> Session:
        device = self._iface(device_path, DEVICE_IFACE)
        session = Session(kind=kind, finger=finger, device=device_path)
        try:
            self._perform(device, session, username=username, timeout=timeout,
                          on_start=on_start, on_status=on_status)
        except FingerlockError as exc:
            exc.session = session
            raise
        return session

    def _perform(self, device: Any, session: Session, *, username: str, timeout: float,
                 on_start: StartCallback | None, on_status: StatusCallback | None) -> None:
        enrolling = session.kind == "enroll"
        stop = device.EnrollStop if enrolling else device.VerifyStop

        def on_signal(result: Any, done: Any) -> None:
            session.handle(result, done)
            if on_status is not None:
                on_status(session.events[-1], session)

        def on_selected(selected: Any) -> None:
            session.selected_finger = str(selected)

        self._call_gated(device.Claim, username)
        matches: list[Any] = []
        try:
            matches.append(device.connect_to_signal(
                "EnrollStatus" if enrolling else "VerifyStatus", on_signal))
            if enrolling:
                self._call_gated(device.EnrollStart, session.finger)
            else:
                matches.append(device.connect_to_signal("VerifyFingerSelected", on_selected))
                self._call(device.VerifyStart, session.finger)
            if on_start is not None:
                on_start(session)
            if not self.bus.run_until(lambda: session.done, timeout):
                session.timed_out = True
        finally:
            for match in matches:
                self._quiet(match.remove)
            self._quiet(stop)
            self._quiet(device.Release)

    def enroll(self, device_path: str, finger: str, *, username: str = "", timeout: float = 120.0,
               on_start: StartCallback | None = None, on_status: StatusCallback | None = None) -> Session:
        return self._run_action("enroll", device_path, finger, username=username,
                                timeout=timeout, on_start=on_start, on_status=on_status)

    def verify(self, device_path: str, finger: str = ANY_FINGER, *, username: str = "",
               timeout: float = 60.0, on_start: StartCallback | None = None,
               on_status: StatusCallback | None = None) -> Session:
        return self._run_action("verify", device_path, finger, username=username,
                                timeout=timeout, on_start=on_start, on_status=on_status)

    def delete(self, device_path: str, finger: str | None = None, *, username: str = "") -> None:
        device = self._iface(device_path, DEVICE_IFACE)
        self._call_gated(device.Claim, username)
        try:
            if finger:
                self._call_gated(device.DeleteEnrolledFinger, finger)
            else:
                self._call_gated(device.DeleteEnrolledFingers2)
        finally:
            self._quiet(device.Release)


# --- polkit tty agent -------------------------------------------------------------

POLKIT_AGENT_PREFIXES = (
    "polkit-gnome", "polkit-kde", "polkit-mate", "mate-polkit", "lxpolkit", "lxqt-policykit",
    "xfce-polkit", "hyprpolkitagent", "pkttyagent", "gnome-shell", "ukui-polkit", "deepin-polkit",
)


def polkit_agents(proc: str | os.PathLike[str] = "/proc") -> set[str]:
    found: set[str] = set()
    try:
        entries = list(Path(proc).iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        comm = _read(entry / "comm") or ""
        if comm.startswith(POLKIT_AGENT_PREFIXES):
            found.add(comm)
    return found


class TtyAgent:
    """pkttyagent for one command; waits for its registration before use."""

    REGISTER_TIMEOUT = 5.0

    def __init__(self, enabled: bool, *, spawn: Callable[..., Any] = subprocess.Popen) -> None:
        self.enabled = enabled
        self.spawn = spawn
        self.process: Any = None
        self.registered = False

    def __enter__(self) -> "TtyAgent":
        if not self.enabled:
            return self
        read_end, write_end = os.pipe()
        try:
            self.process = self.spawn(
                ["pkttyagent", "--process", str(os.getpid()), "--notify-fd", str(write_end)],
                pass_fds=(write_end,))
        except OSError as exc:
            os.close(read_end)
            os.close(write_end)
            sys.stderr.write(f"{PROG}: pkttyagent unavailable ({exc})\n")
            return self
        os.close(write_end)
        try:
            ready, _, _ = select.select([read_end], [], [], self.REGISTER_TIMEOUT)
            if ready:
                os.read(read_end, 1)
                self.registered = True
            else:
                sys.stderr.write(f"{PROG}: pkttyagent did not register in time\n")
        finally:
            os.close(read_end)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()


# --- doctor -------------------------------------------------------------------------

REENUMERATION_WARN_THRESHOLD = 10
NO_SESSION_ID = "4294967295"


@dataclass(frozen=True)
class Check:
    id: str
    status: str  # ok | warn | fail | info | skip
    summary: str
    hint: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "status": self.status, "summary": self.summary, "hint": self.hint}


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    readers: list[UsbReader] = field(default_factory=list)
    missing_packages: list[str] = field(default_factory=list)
    fprintd_devices: list[DeviceInfo] = field(default_factory=list)
    enrolled: dict[str, list[str]] = field(default_factory=dict)
    ports: list[PortHistory] = field(default_factory=list)

    REQUIRED = ("reader", "packages", "libfprint", "support", "fprintd-installed",
                "fprintd-reachable", "session")

    @property
    def usable(self) -> bool:
        statuses: dict[str, list[str]] = {name: [] for name in self.REQUIRED}
        for check in self.checks:
            if check.id in statuses:
                statuses[check.id].append(check.status)
        return all(found and all(status == "ok" for status in found) for found in statuses.values())

    def as_dict(self, *, redact: bool = False) -> dict[str, Any]:
        return {"usable": self.usable, "checks": [c.as_dict() for c in self.checks],
                "readers": [r.as_dict(redact=redact) for r in self.readers],
                "missing_packages": list(self.missing_packages),
                "fprintd_devices": [d.as_dict() for d in self.fprintd_devices],
                "enrolled": self.enrolled,
                "ports": [p.as_dict(redact=redact) for p in self.ports]}


def _session_check(environ: Mapping[str, str], run: Callable[..., Any], proc: Path) -> Check:
    session_id = environ.get("XDG_SESSION_ID", "").strip()
    if not session_id:
        session_id = _read(proc / "self/sessionid") or ""
        if session_id == NO_SESSION_ID:
            session_id = ""
    if not session_id:
        return Check("session", "warn", "no logind session id for this process",
                     "polkit only allows fingerprint operations from an active local session")
    try:
        result = run(["loginctl", "show-session", session_id, "-p", "Active", "-p", "Seat",
                      "-p", "Remote", "-p", "Type"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return Check("session", "warn", f"loginctl unavailable ({exc.__class__.__name__})")
    if result.returncode != 0:
        return Check("session", "warn", f"loginctl could not describe session {session_id}")
    facts = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    seat = facts.get("Seat", "")
    if facts.get("Active") == "yes" and seat:
        return Check("session", "ok", f"session {session_id} is active on {seat} ({facts.get('Type', '?')})")
    return Check("session", "warn",
                 f"session {session_id} is not an active seat session "
                 f"(Active={facts.get('Active', '?')}, Seat={seat or '-'})",
                 "run from the local graphical or console session, not over SSH")


def run_doctor(*, root: str | os.PathLike[str] = "/", client: FprintdClient | None = None,
               environ: Mapping[str, str] | None = None, run: Callable[..., Any] = subprocess.run,
               proc: str | os.PathLike[str] = "/proc",
               history: Callable[..., PortHistory] = port_history,
               access: Callable[[str, int], bool] = os.access) -> Report:
    base, proc_path = Path(root), Path(proc)
    environ = os.environ if environ is None else environ
    report = Report()
    checks = report.checks

    hwdb_file, hwdb = load_hwdb(base)
    readers = list_readers(base, hwdb=hwdb, access=access)
    report.readers = readers
    if readers:
        labels = ", ".join(f"{r.label} ({r.usb_id})" for r in readers)
        checks.append(Check("reader", "ok", f"{len(readers)} USB fingerprint reader(s): {labels}"))
    else:
        checks.append(Check("reader", "fail", "no USB fingerprint reader found",
                            "compare lsusb; SPI and embedded readers are not covered yet"))

    wanted = PACKAGES
    report.missing_packages = missing_packages(wanted, run=run)
    if report.missing_packages:
        checks.append(Check("packages", "fail",
                            "not installed: " + ", ".join(report.missing_packages), INSTALL_HINT))
    else:
        checks.append(Check("packages", "ok", "installed: " + ", ".join(wanted)))

    library = _first_glob(base, LIBFPRINT_LIBRARY_GLOBS)
    if hwdb_file is not None or library is not None:
        checks.append(Check("libfprint", "ok", f"libfprint present ({hwdb_file or library})"))
    else:
        checks.append(Check("libfprint", "fail", "libfprint is not installed", INSTALL_HINT))

    if not readers:
        checks.append(Check("support", "skip", "no reader to check against libfprint"))
    for reader in readers:
        if reader.libfprint_driver:
            checks.append(Check("support", "ok", f"{reader.usb_id} is handled by libfprint driver {reader.libfprint_driver}"))
        elif hwdb_file is not None:
            checks.append(Check("support", "fail", f"libfprint does not list {reader.usb_id}",
                                "this reader needs a driver outside libfprint"))
        else:
            checks.append(Check("support", "warn", f"{reader.usb_id} support is unknown until libfprint is installed", INSTALL_HINT))

    fprintd_installed = (base / FPRINTD_SERVICE_FILE).is_file() or (base / FPRINTD_UNIT_FILE).is_file()
    checks.append(Check("fprintd-installed", "ok", "fprintd is installed (D-Bus activatable)")
                  if fprintd_installed else
                  Check("fprintd-installed", "fail", "fprintd is not installed", INSTALL_HINT))

    bindings_ok, bindings_reason = bindings_available()
    if client is not None or bindings_ok:
        checks.append(Check("python-dbus", "ok", "D-Bus client supplied" if client is not None else bindings_reason))
    else:
        checks.append(Check("python-dbus", "warn", f"D-Bus bindings unavailable ({bindings_reason})", BINDINGS_HINT))

    if client is None and (not fprintd_installed or not bindings_ok):
        checks.append(Check("fprintd-reachable", "skip", "fprintd not queried"))
    else:
        active = client or FprintdClient()
        try:
            devices = active.list_devices()
        except FingerlockError as exc:
            checks.append(Check("fprintd-reachable", "fail", f"fprintd: {exc.message}"))
        else:
            report.fprintd_devices = devices
            if not devices:
                checks.append(Check("fprintd-reachable", "fail", "fprintd is running but reports no devices",
                                    "libfprint did not claim the reader; check its hwdb entry"))
            else:
                checks.append(Check("fprintd-reachable", "ok",
                                    f"fprintd reports {len(devices)} device(s): " + ", ".join(d.name for d in devices)))
                for device in devices:
                    try:
                        fingers = active.enrolled_fingers(device.path)
                    except FingerlockError as exc:
                        checks.append(Check("enrolled", "warn", f"{device.name}: {exc.message}"))
                        continue
                    report.enrolled[device.path] = fingers
                    checks.append(Check("enrolled", "info", f"{device.name}: {len(fingers)} enrolled finger(s)"
                                        + (": " + ", ".join(fingers) if fingers else "")))

    checks.append(_session_check(environ, run, proc_path))
    agents = polkit_agents(proc_path)
    checks.append(Check("polkit-agent", "ok", "polkit authentication agent: " + ", ".join(sorted(agents)))
                  if agents else
                  Check("polkit-agent", "warn", "no polkit authentication agent process found",
                        "enroll and delete need one; pass --tty-agent to run pkttyagent for the command"))

    for reader in readers:
        checks.append(Check("node-access", "info",
                            f"{reader.node} is directly openable by this user" if reader.node_openable else
                            f"{reader.node} is {reader.node_owner or '?'} mode {reader.node_mode or '?'}; access goes through fprintd"))
        port = history(reader.sysfs_name, run=run)
        report.ports.append(port)
        if port.enumerations is None:
            checks.append(Check("usb-stability", "skip", f"kernel log unavailable ({port.note})"))
        elif port.enumerations > REENUMERATION_WARN_THRESHOLD:
            checks.append(Check("usb-stability", "warn",
                                f"port re-enumerated {port.enumerations} times this boot (last {port.last_enumeration})",
                                "the reader resets on the bus; watch whether it persists once a driver holds it"))
        else:
            checks.append(Check("usb-stability", "ok", f"port enumerated {port.enumerations} time(s) this boot"))

    return report


# --- output ---------------------------------------------------------------------------

def emit_json(action: str, data: Any) -> None:
    sys.stdout.write(json.dumps({"ok": True, "schema": SCHEMA, "action": action, "data": data},
                                sort_keys=True) + "\n")


def emit_error_json(action: str, error: FingerlockError) -> None:
    payload: dict[str, Any] = {"ok": False, "schema": SCHEMA, "action": action, "code": error.code,
                               "error": error.message, "exit": error.exit_status}
    data = getattr(error, "data", None)
    if data is not None:
        payload["data"] = data
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")


def emit_table(rows: Iterable[Mapping[str, object]], columns: Sequence[tuple[str, str]], *, empty: str = "(none)") -> None:
    materialized = list(rows)
    if not materialized:
        sys.stdout.write(empty + "\n")
        return
    widths = [len(label) for _key, label in columns]
    cells: list[list[str]] = []
    for row in materialized:
        current = []
        for index, (key, _label) in enumerate(columns):
            value = row.get(key)
            text = "-" if value is None or value == "" else ("yes" if value is True else "no" if value is False else str(value))
            widths[index] = max(widths[index], len(text))
            current.append(text)
        cells.append(current)
    line = lambda values: "  ".join(v.ljust(widths[i]) for i, v in enumerate(values)).rstrip()  # noqa: E731
    sys.stdout.write(line([label for _key, label in columns]) + "\n")
    for row in cells:
        sys.stdout.write(line(row) + "\n")


def _print_report(report: Report) -> None:
    emit_table([c.as_dict() for c in report.checks], [("status", "STATUS"), ("id", "CHECK"), ("summary", "SUMMARY")])
    hints = [c for c in report.checks if c.hint and c.status != "ok"]
    if hints:
        sys.stdout.write("\nnext steps:\n")
        for check in hints:
            sys.stdout.write(f"  {check.id}: {check.hint}\n")
    sys.stdout.write(f"\nusable: {'yes' if report.usable else 'no'}\n")


# --- commands ----------------------------------------------------------------------

class _ErrorWithData(FingerlockError):
    def __init__(self, inner: FingerlockError, data: Any) -> None:
        super().__init__(inner.message)
        self.code, self.exit_status, self.dbus_name = inner.code, inner.exit_status, inner.dbus_name
        self.data = data


def _with_data(error: FingerlockError, data: Any, want_json: bool) -> FingerlockError:
    return _ErrorWithData(error, data) if want_json else error


def _client(auth_timeout: float) -> FprintdClient:
    return FprintdClient(auth_timeout=auth_timeout)


def _resolve_device(client: FprintdClient, path: str | None) -> DeviceInfo:
    if path:
        for device in client.list_devices():
            if device.path == path:
                return device
        raise UsageError(f"fprintd has no device at {path}")
    return client.default_device()


def _progress(event: StatusEvent, session: Session) -> None:
    suffix = f" [{session.stages_passed}]" if session.kind == "enroll" and event.result == "enroll-stage-passed" else ""
    sys.stderr.write(f"  {describe(event.result)}{suffix}\n")


def _session_data(session: Session, device: DeviceInfo) -> dict[str, Any]:
    return {**session.as_dict(), "device_info": device.as_dict(), "num_enroll_stages": device.num_enroll_stages}


def _run_scan(args: argparse.Namespace, kind: str) -> tuple[Session, DeviceInfo]:
    client = _client(args.auth_timeout)
    with TtyAgent(args.tty_agent):
        device = _resolve_device(client, args.device)
        prompt = " (a password prompt may appear)" if kind == "enroll" or args.user else ""
        sys.stderr.write(f"claiming {device.name} for {kind}{prompt}\n")

        def on_start(_session: Session) -> None:
            if kind == "enroll":
                sys.stderr.write(f"scanning: touch the sensor with your {args.finger} about "
                                 f"{device.num_enroll_stages} times, lifting between touches\n")
            else:
                sys.stderr.write("scanning: touch the sensor\n")

        try:
            action = client.enroll if kind == "enroll" else client.verify
            session = action(device.path, args.finger, username=args.user, timeout=args.timeout,
                             on_start=on_start, on_status=_progress)
        except FingerlockError as exc:
            if exc.session is not None:
                raise _with_data(exc, _session_data(exc.session, device), args.json) from exc
            raise
    return session, device


def cmd_status(args: argparse.Namespace) -> int:
    report = run_doctor()
    data = report.as_dict(redact=args.redact)
    if args.json:
        emit_json("status", data)
        return 0 if report.usable else 1
    if not report.readers:
        sys.stdout.write("fingerprint reader: none found\n")
        return 1
    for reader in report.readers:
        sys.stdout.write(f"reader: {reader.label} ({reader.usb_id})"
                         + (f", libfprint driver {reader.libfprint_driver}" if reader.libfprint_driver else "") + "\n")
    if report.missing_packages:
        sys.stdout.write("packages: missing " + ", ".join(report.missing_packages) + f"  ({INSTALL_HINT})\n")
    for device in report.fprintd_devices:
        fingers = report.enrolled.get(device.path, [])
        sys.stdout.write(f"fprintd: {device.name} ({device.scan_type}, {device.num_enroll_stages} stages)\n")
        sys.stdout.write("enrolled: " + (", ".join(fingers) if fingers else "none") + "\n")
    sys.stdout.write(f"usable: {'yes' if report.usable else 'no'}\n")
    return 0 if report.usable else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    report = run_doctor()
    if args.json:
        emit_json("doctor", report.as_dict(redact=args.redact))
    else:
        _print_report(report)
    return 0 if report.usable else 1


def cmd_install(args: argparse.Namespace) -> int:
    wanted = PACKAGES
    missing = missing_packages(wanted)
    if missing:
        install_packages(missing, yes=args.yes, want_json=args.json)
    report = run_doctor()
    data = {"requested": list(wanted), "installed_now": missing, "report": report.as_dict(redact=args.redact)}
    if args.json:
        emit_json("install", data)
    else:
        sys.stdout.write(("installed " + ", ".join(missing) if missing else "nothing to install; " + ", ".join(wanted) + " present") + "\n")
        _print_report(report)
    return 0 if report.usable else 1


def cmd_ensure(args: argparse.Namespace) -> int:
    """The default: access the reader if present, installing on first use."""
    report = run_doctor()
    if not report.readers:
        raise DeviceNotFound("no USB fingerprint reader found on this machine")
    if report.missing_packages:
        sys.stderr.write(f"{PROG}: {report.readers[0].label} found; "
                         f"{', '.join(report.missing_packages)} not installed yet\n")
        install_packages(report.missing_packages, yes=args.yes, want_json=args.json)
        report = run_doctor()
    data = report.as_dict(redact=args.redact)
    if args.json:
        emit_json("ensure", data)
        return 0 if report.usable else 1
    if not report.usable:
        _print_report(report)
        return 1
    device = report.fprintd_devices[0] if report.fprintd_devices else None
    fingers = report.enrolled.get(device.path, []) if device else []
    sys.stdout.write(f"reader: {report.readers[0].label} via fprintd" + (f" ({device.name})" if device else "") + "\n")
    sys.stdout.write("enrolled: " + (", ".join(fingers) if fingers else
                                     f"none yet; run: {PROG} enroll {DEFAULT_ENROLL_FINGER}") + "\n")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    client = _client(args.auth_timeout)
    with TtyAgent(args.tty_agent):
        device = _resolve_device(client, args.device)
        fingers = client.enrolled_fingers(device.path, args.user)
    data = {"device": device.as_dict(), "user": args.user or None, "fingers": fingers}
    if args.json:
        emit_json("list", data)
        return 0
    sys.stdout.write(f"{device.name} ({device.scan_type}, {device.num_enroll_stages} stages)\n")
    sys.stdout.write("".join(f"  {finger}\n" for finger in fingers) if fingers else "  (no enrolled fingers)\n")
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    session, device = _run_scan(args, "enroll")
    data = _session_data(session, device)
    if session.timed_out:
        raise _with_data(OperationTimeout(f"no scan result within {args.timeout:g}s"), data, args.json)
    if not session.succeeded:
        raise _with_data(OperationFailed(describe(session.result or "enroll-unknown-error")), data, args.json)
    if args.json:
        emit_json("enroll", data)
    else:
        sys.stdout.write(f"enrolled {args.finger} on {device.name}\n")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    session, device = _run_scan(args, "verify")
    data = _session_data(session, device)
    if session.timed_out:
        raise _with_data(OperationTimeout(f"no scan result within {args.timeout:g}s"), data, args.json)
    if session.result == VERIFY_NO_MATCH:
        raise _with_data(NoMatch("no match"), data, args.json)
    if not session.succeeded:
        raise _with_data(OperationFailed(describe(session.result or "verify-unknown-error")), data, args.json)
    if args.json:
        emit_json("verify", data)
    else:
        sys.stdout.write("match" + (f" ({session.selected_finger})" if session.selected_finger else "") + "\n")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    if bool(args.finger) == bool(args.all):
        raise UsageError("give exactly one finger, or --all")
    client = _client(args.auth_timeout)
    with TtyAgent(args.tty_agent):
        device = _resolve_device(client, args.device)
        before = client.enrolled_fingers(device.path, args.user)
        client.delete(device.path, args.finger, username=args.user)
        after = client.enrolled_fingers(device.path, args.user)
    data = {"device": device.as_dict(), "user": args.user or None,
            "requested": args.finger or "all", "before": before, "after": after}
    if args.json:
        emit_json("delete", data)
    else:
        removed = sorted(set(before) - set(after))
        sys.stdout.write(f"deleted {', '.join(removed) if removed else 'nothing'}; {len(after)} finger(s) remain\n")
    return 0


# --- argument parsing ----------------------------------------------------------------

class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def _common(parser: argparse.ArgumentParser, *, auth: bool = False, install: bool = False) -> None:
    parser.add_argument("--json", action="store_true", help="emit one kilix.fingerlock/v1 object")
    parser.add_argument("--redact", action="store_true", help="remove serial numbers and USB topology")
    if install:
        parser.add_argument("--yes", action="store_true", help="install without asking (sudo may still prompt)")
    if auth:
        parser.add_argument("--user", default="", metavar="NAME",
                            help="act for another user (needs administrative polkit approval)")
        parser.add_argument("--device", metavar="PATH", help="fprintd device object path; default is fprintd's")
        parser.add_argument("--tty-agent", action="store_true",
                            help="run pkttyagent on this terminal so polkit can ask for a password")
        parser.add_argument("--auth-timeout", type=float, default=AUTH_TIMEOUT_SECONDS, metavar="SECONDS",
                            help="how long to wait for a polkit authentication prompt to be answered")


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(prog=PROG, description="the fingerprint reader: find it, install fprintd on first use, "
                                                   "enroll, verify, list and delete fingers")
    subparsers = parser.add_subparsers(dest="action")

    ensure = subparsers.add_parser("ensure", help="access the reader if present, installing on first use (default)")
    _common(ensure, install=True)
    ensure.set_defaults(handler=cmd_ensure)

    status = subparsers.add_parser("status", help="one-screen state: reader, packages, daemon, enrolled fingers")
    _common(status)
    status.set_defaults(handler=cmd_status)

    doctor = subparsers.add_parser("doctor", help="every check between this session and the reader")
    _common(doctor)
    doctor.set_defaults(handler=cmd_doctor)

    install = subparsers.add_parser("install", help="install the Debian packages the reader needs")
    _common(install, install=True)
    install.set_defaults(handler=cmd_install)

    listing = subparsers.add_parser("list", help="list enrolled fingers through fprintd")
    _common(listing, auth=True)
    listing.set_defaults(handler=cmd_list)

    enroll = subparsers.add_parser("enroll", help="enroll one finger through fprintd")
    enroll.add_argument("finger", nargs="?", default=DEFAULT_ENROLL_FINGER, choices=FINGERS, metavar="FINGER",
                        help=f"default {DEFAULT_ENROLL_FINGER}; one of " + ", ".join(FINGERS))
    enroll.add_argument("--timeout", type=float, default=120, help="seconds to wait for the scans once they start")
    _common(enroll, auth=True)
    enroll.set_defaults(handler=cmd_enroll)

    verify = subparsers.add_parser("verify", help="verify a finger through fprintd")
    verify.add_argument("finger", nargs="?", default=ANY_FINGER, choices=(ANY_FINGER,) + FINGERS, metavar="FINGER",
                        help="finger to verify; default any")
    verify.add_argument("--timeout", type=float, default=60, help="seconds to wait for a scan once scanning starts")
    _common(verify, auth=True)
    verify.set_defaults(handler=cmd_verify)

    delete = subparsers.add_parser("delete", help="delete enrolled fingers through fprintd")
    delete.add_argument("finger", nargs="?", choices=FINGERS, metavar="FINGER", help="finger to delete")
    delete.add_argument("--all", action="store_true", help="delete every enrolled finger")
    _common(delete, auth=True)
    delete.set_defaults(handler=cmd_delete)
    return parser


ACTIONS = ("ensure", "status", "doctor", "install", "list", "enroll", "verify", "delete")


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["ensure", *argv]
    want_json = "--json" in argv
    action = argv[0] if argv and argv[0] in ACTIONS else "ensure"
    try:
        try:
            args = build_parser().parse_args(argv)
        except SystemExit as exc:  # argparse's own --help path
            return exc.code if isinstance(exc.code, int) else 0
        if getattr(args, "handler", None) is None:
            raise UsageError("choose an action: " + ", ".join(ACTIONS))
        return int(args.handler(args))
    except FingerlockError as error:
        if want_json:
            emit_error_json(action, error)
        else:
            sys.stderr.write(f"{PROG}: {error.message}\n")
        return error.exit_status
    except KeyboardInterrupt:
        sys.stderr.write(f"{PROG}: interrupted\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
