#!/usr/bin/env python3
"""kilix fingerlock — the fingerprint reader as a host verb.

`kilix fingerlock` finds the machine's fingerprint reader, installs what
Debian needs the first time it is asked to, and then enrolls, verifies, lists
and deletes fingers through the distribution's fingerprint daemon. It is the
one implementation every Kilix surface delegates to, so a reader that works
from a typed command works the same from a desktop's lock widget.

Access model. A libfprint-driven reader keeps a root-only USB node on
purpose; this module never opens it. User access goes through fprintd over
the system D-Bus, behind polkit: verifying is allowed from an active seat
session, enrolling and deleting also need the caller's password through a
polkit authentication agent, and acting for another user needs an
administrator's. `--tty-agent` runs pkttyagent for the duration of one
command when the session has no resident agent.

Lazy installation. The only packages needed are Debian's own (`fprintd`, and
`libpam-fprintd` when fingerprint login is explicitly requested). They are
installed with one fixed argv, `apt-get install -y --no-install-recommends`,
elevated through sudo in the caller's terminal, or through xterm when the
caller has none. Plebian OS points apt at a dated snapshot, so the versions
this installs are the release's, not today's. No repository is added and no
recommends are pulled, so installing fprintd never changes how login works.

Locking. `init` makes a locker: an X25519 keypair whose seed is wrapped under
an Argon2id key from a passphrase (config/fingerlock_locker.py). `lock` seals
files into `.flk` containers with the public key, so it never asks for
anything; `unlock` and `password get` need the private key, which comes from
the passphrase or, once deposited for this boot, from the root agent
(config/fingerlock_agent.py) after a fingerprint match. The agent is what
makes the fingerprint mean something: fprintd only ever says match or no
match, so a privileged process must hold the key and release it on a match.
Without the agent everything still works with the passphrase.

Exit statuses are stable: 0 success, 1 not usable, 2 usage, 3 no reader or
no fprintd device, 4 a dependency is missing or was refused, 5 polkit, fprintd
or the agent refused the caller, 6 fprintd or the agent failed, 7 an
enrollment ended without completing, 8 verification found no match, 9 a
timeout, 10 a locker or container problem, 11 a wrong passphrase. `--json`
emits one `kilix.fingerlock/v1` object on stdout; prompts and scan progress
go to stderr in both modes.
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
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fingerlock_core import (  # noqa: E402
    ANY_FINGER, AUTH_TIMEOUT_SECONDS, BINDINGS_HINT, DEVICE_IFACE, ENROLL_COMPLETED,
    ERROR_PREFIX, MANAGER_PATH, NO_REPLY, PROG, VERIFY_MATCH, VERIFY_NO_MATCH,
    AccessDenied, DependencyUnavailable, DeviceInfo, DeviceNotFound, FingerlockError,
    FprintdClient, NoMatch, OperationFailed, OperationTimeout, ServiceFailure, Session,
    StatusEvent, TtyAgent, UsageError, _read, bindings_available, describe, polkit_agents,
    translate_error,
)

import fingerlock_agent as agentmod  # noqa: E402
import fingerlock_locker as lockermod  # noqa: E402
from fingerlock_locker import Locker, LockerError, WrongPassphrase  # noqa: E402

SCHEMA = "kilix.fingerlock/v1"
REDACTED = "<redacted>"

# --- packages ---------------------------------------------------------------

PACKAGES: tuple[str, ...] = ("fprintd",)
LOCKER_PACKAGES: tuple[str, ...] = lockermod.LOCKER_PACKAGES   # ("python3-nacl",)
PAM_PACKAGES: tuple[str, ...] = ("libpam-fprintd",)
AGENT_FILES: tuple[str, ...] = ("fingerlock_core.py", "fingerlock_agent.py")
INSTALL_ARGV: tuple[str, ...] = ("apt-get", "install", "-y", "--no-install-recommends")
INSTALL_HINT = f"{PROG} install"

FINGERS: tuple[str, ...] = (
    "left-thumb", "left-index-finger", "left-middle-finger", "left-ring-finger",
    "left-little-finger", "right-thumb", "right-index-finger",
    "right-middle-finger", "right-ring-finger", "right-little-finger",
)
DEFAULT_ENROLL_FINGER = "right-index-finger"


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
PAM_MODULE_GLOBS = ("usr/lib/*/security/pam_fprintd.so", "usr/lib/security/pam_fprintd.so")
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
               access: Callable[[str, int], bool] = os.access,
               with_pam: bool = False, with_locker: bool = True,
               locker_home: Path | None = None) -> Report:
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

    wanted = PACKAGES + (LOCKER_PACKAGES if with_locker else ()) + (PAM_PACKAGES if with_pam else ())
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

    pam = _first_glob(base, PAM_MODULE_GLOBS)
    checks.append(Check("pam", "info", f"pam_fprintd present ({pam}); fingerprint login is possible") if pam else
                  Check("pam", "info", "libpam-fprintd not installed; login stays password-only (optional: install --pam)"))

    locker = Locker(locker_home)
    checks.append(Check("locker", "info", f"locker at {locker.path}") if locker.exists() else
                  Check("locker", "info", f"no locker yet ({PROG} init creates one at {locker.path})"))
    unit = base / "etc/systemd/system" / agentmod.SERVICE_UNIT
    sock = base / agentmod.SOCKET_PATH.lstrip("/")
    if not unit.is_file():
        checks.append(Check("agent", "info", "fingerprint-gated unlock agent not installed "
                            f"({PROG} install --agent); unlocking asks for the passphrase"))
    elif not sock.exists():
        checks.append(Check("agent", "warn", "agent installed but its socket is absent",
                            f"sudo systemctl enable --now {agentmod.SOCKET_UNIT}"))
    else:
        checks.append(Check("agent", "ok", f"agent socket present at {agentmod.SOCKET_PATH}"))
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
    report = run_doctor(with_pam=args.pam)
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
    report = run_doctor(with_pam=args.pam)
    if args.json:
        emit_json("doctor", report.as_dict(redact=args.redact))
    else:
        _print_report(report)
    return 0 if report.usable else 1


def agent_install_argvs(source_dir: Path) -> list[list[str]]:
    """The fixed root commands that stage the agent: files, units, enable."""
    argvs: list[list[str]] = []
    for name in AGENT_FILES:
        argvs.append(["install", "-D", "-m", "0644", "-o", "root", "-g", "root",
                      str(source_dir / name), f"{agentmod.INSTALL_DIR}/{name}"])
    argvs.append(["install", "-D", "-m", "0644", "-o", "root", "-g", "root",
                  str(source_dir / "units" / agentmod.SOCKET_UNIT), f"/etc/systemd/system/{agentmod.SOCKET_UNIT}"])
    argvs.append(["install", "-D", "-m", "0644", "-o", "root", "-g", "root",
                  str(source_dir / "units" / agentmod.SERVICE_UNIT), f"/etc/systemd/system/{agentmod.SERVICE_UNIT}"])
    argvs.append(["systemctl", "daemon-reload"])
    argvs.append(["systemctl", "enable", "--now", agentmod.SOCKET_UNIT])
    return argvs


def install_agent(*, yes: bool, want_json: bool, run: Callable[..., Any] = subprocess.run,
                  ask: Callable[[str], str] = input, source_dir: Path | None = None) -> list[list[str]]:
    """Stage the agent as root through one sudo argv per step, shown first."""
    source_dir = source_dir or Path(__file__).resolve().parent
    units = source_dir / "units"
    _private_units(units)
    argvs = agent_install_argvs(source_dir)
    if not yes:
        if want_json or not _interactive_terminal():
            raise DependencyUnavailable("installing the agent needs a terminal, or --yes")
        sys.stderr.write(f"{PROG}: about to run as root:\n" + "".join(f"  {' '.join(a)}\n" for a in argvs))
        try:
            answer = ask("proceed? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            raise DependencyUnavailable("agent installation declined")
    for argv in argvs:
        full = elevated_argv(argv, title="Install the fingerlock agent")
        sys.stderr.write(f"{PROG}: running {' '.join(full)}\n")
        proc = run(full)
        if proc.returncode != 0:
            raise DependencyUnavailable(f"agent installation failed at {' '.join(argv)} (exit {proc.returncode})")
    return argvs


def _private_units(units: Path) -> None:
    """Write the two unit files beside the module, from the agent's own text."""
    units.mkdir(mode=0o755, exist_ok=True)
    for name, text in ((agentmod.SOCKET_UNIT, agentmod.SOCKET_UNIT_TEXT),
                       (agentmod.SERVICE_UNIT, agentmod.SERVICE_UNIT_TEXT)):
        path = units / name
        if not path.exists() or path.read_text() != text:
            path.write_text(text)


def cmd_install(args: argparse.Namespace) -> int:
    wanted = PACKAGES + LOCKER_PACKAGES + (PAM_PACKAGES if args.pam else ())
    missing = missing_packages(wanted)
    if missing:
        install_packages(missing, yes=args.yes, want_json=args.json)
    agent_steps: list[list[str]] = []
    if args.agent:
        agent_steps = install_agent(yes=args.yes, want_json=args.json)
    report = run_doctor(with_pam=args.pam)
    data = {"requested": list(wanted), "installed_now": missing, "agent_steps": agent_steps,
            "report": report.as_dict(redact=args.redact)}
    if args.json:
        emit_json("install", data)
    else:
        sys.stdout.write(("installed " + ", ".join(missing) if missing else "nothing to install; " + ", ".join(wanted) + " present") + "\n")
        if agent_steps:
            sys.stdout.write("agent installed and enabled\n")
        _print_report(report)
    return 0 if report.usable else 1


def cmd_ensure(args: argparse.Namespace) -> int:
    """The default: access the reader if present, installing on first use."""
    report = run_doctor(with_pam=args.pam)
    if not report.readers:
        raise DeviceNotFound("no USB fingerprint reader found on this machine")
    if report.missing_packages:
        sys.stderr.write(f"{PROG}: {report.readers[0].label} found; "
                         f"{', '.join(report.missing_packages)} not installed yet\n")
        install_packages(report.missing_packages, yes=args.yes, want_json=args.json)
        report = run_doctor(with_pam=args.pam)
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
    locker = Locker()
    sys.stdout.write(f"locker: {locker.path}\n" if locker.exists() else f"locker: none yet ({PROG} init)\n")
    agent = agentmod.AgentClient()
    sys.stdout.write("unlock: fingerprint touch (agent installed)\n" if agent.available() else
                     f"unlock: passphrase ({PROG} install --agent makes it a touch)\n")
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


# --- locker commands ------------------------------------------------------------------

def _read_passphrase(args: argparse.Namespace, prompt: str) -> str:
    fd = getattr(args, "passphrase_fd", None)
    if fd is not None:
        # Unbuffered, one byte at a time, so a second passphrase on the same
        # descriptor is still there for the next call.
        line = bytearray()
        try:
            while len(line) < 4096:
                byte = os.read(int(fd), 1)
                if not byte or byte == b"\n":
                    break
                line += byte
        except OSError as exc:
            raise UsageError(f"cannot read --passphrase-fd {fd}: {exc}") from exc
        return line.decode("utf-8", "replace")
    if not _interactive_terminal():
        raise AccessDenied("the locker needs its passphrase; run from a terminal or pass --passphrase-fd")
    import getpass
    return getpass.getpass(prompt)


def _agent(args: argparse.Namespace) -> agentmod.AgentClient:
    return agentmod.AgentClient(getattr(args, "agent_socket", None) or agentmod.SOCKET_PATH)


def obtain_private_key(args: argparse.Namespace, locker: Locker, info: lockermod.LockerInfo) -> tuple[bytes, str]:
    """The locker's private key: from the agent after a touch, else the passphrase."""
    agent = _agent(args)
    use_agent = not getattr(args, "passphrase", False) and agent.available()
    if use_agent:
        try:
            held = agent.status()
        except agentmod.AgentUnavailable:
            held = {"held": False}
        if held.get("held") and held.get("locker_id") == info.locker_id:
            sys.stderr.write("touch the sensor to unlock\n")
            return agent.release(info.locker_id, getattr(args, "finger", ANY_FINGER)), "fingerprint"
    passphrase = _read_passphrase(args, "locker passphrase: ")
    private = locker.derive_private_key(passphrase, info)
    if use_agent and not getattr(args, "no_deposit", False):
        try:
            reply = agent.deposit(private, info.locker_id, float(getattr(args, "ttl", agentmod.DEFAULT_TTL_SECONDS)))
            hours = int(reply.get("expires_in", 0)) // 3600
            sys.stderr.write(f"fingerprint unlock enabled for about {hours} h; {PROG} forget ends it early\n")
        except FingerlockError as exc:
            sys.stderr.write(f"{PROG}: fingerprint unlock not enabled ({exc.message})\n")
    return private, "passphrase"


def _locker(args: argparse.Namespace) -> Locker:
    home = getattr(args, "locker_home", None)
    return Locker(Path(home) if home else None)


def cmd_init(args: argparse.Namespace) -> int:
    locker = _locker(args)
    if locker.exists():
        raise LockerError(f"a locker already exists at {locker.path}")
    first = _read_passphrase(args, "new locker passphrase: ")
    if getattr(args, "passphrase_fd", None) is None:
        if _read_passphrase(args, "again: ") != first:
            raise UsageError("the passphrases do not match")
    info = locker.init(first, kdf=args.kdf)
    data = {"locker_id": info.locker_id, "path": str(locker.path), "kdf": args.kdf}
    if args.json:
        emit_json("init", data)
    else:
        sys.stdout.write(f"locker {info.locker_id} created at {locker.path}\n")
        sys.stdout.write(f"lock files with: {PROG} lock FILE...; unlock with a touch once the agent is installed\n")
    return 0


def cmd_passwd(args: argparse.Namespace) -> int:
    locker = _locker(args)
    locker.info()
    old = _read_passphrase(args, "current locker passphrase: ")
    new = _read_passphrase(args, "new locker passphrase: ")
    if getattr(args, "passphrase_fd", None) is None and _read_passphrase(args, "again: ") != new:
        raise UsageError("the passphrases do not match")
    info = locker.change_passphrase(old, new)
    agent = _agent(args)
    if agent.available():
        try:
            agent.forget()
        except FingerlockError:
            pass
    if args.json:
        emit_json("passwd", {"locker_id": info.locker_id})
    else:
        sys.stdout.write("passphrase changed; every container stays openable\n")
    return 0


def cmd_lock(args: argparse.Namespace) -> int:
    locker = _locker(args)
    info = locker.info()
    results = []
    for raw in args.paths:
        source = Path(raw)
        if not source.is_file():
            raise UsageError(f"{source} is not a file")
        if source.name.endswith(lockermod.SUFFIX):
            raise UsageError(f"{source} is already a container")
        container = lockermod.container_for(source)
        header = lockermod.lock_file(source, container, info, remove_source=not args.keep)
        results.append({"source": str(source), "container": str(container), "size": header["size"],
                        "removed_source": not args.keep})
        if not args.json:
            sys.stdout.write(f"locked {source} -> {container}" + ("" if args.keep else " (original removed)") + "\n")
    if args.json:
        emit_json("lock", {"locker_id": info.locker_id, "files": results})
    return 0


def cmd_unlock(args: argparse.Namespace) -> int:
    locker = _locker(args)
    info = locker.info()
    containers = [Path(raw) for raw in args.paths]
    for container in containers:
        lockermod.read_header(container)
    if args.stdout and len(containers) != 1:
        raise UsageError("--stdout takes exactly one container")
    private, source = obtain_private_key(args, locker, info)
    results = []
    try:
        for container in containers:
            if args.stdout:
                header = lockermod.unlock_file(container, None, private, sink=sys.stdout.buffer)
                sys.stdout.buffer.flush()
                results.append({"container": str(container), "to": "-", "size": header["size"]})
                continue
            destination = lockermod.original_for(container)
            header = lockermod.unlock_file(container, destination, private, overwrite=args.force,
                                           remove_container=not args.keep)
            results.append({"container": str(container), "to": str(destination), "size": header["size"],
                            "removed_container": not args.keep})
            if not args.json:
                sys.stdout.write(f"unlocked {container} -> {destination}\n")
    finally:
        private = b"\0" * len(private)
    if args.json and not args.stdout:
        emit_json("unlock", {"locker_id": info.locker_id, "key_source": source, "files": results})
    return 0


def cmd_password(args: argparse.Namespace) -> int:
    locker = _locker(args)
    info = locker.info()
    action = args.password_action
    if action != "list" and not args.name:
        raise UsageError(f"password {action} needs a NAME")
    if action == "list":
        names = locker.password_names()
        if args.json:
            emit_json("password", {"names": names})
        else:
            sys.stdout.write("".join(f"{n}\n" for n in names) if names else "(no passwords stored)\n")
        return 0
    path = locker.password_path(args.name)
    if action == "delete":
        if not path.exists():
            raise UsageError(f"no password named {args.name}")
        os.unlink(path)
        if args.json:
            emit_json("password", {"deleted": args.name})
        else:
            sys.stdout.write(f"deleted {args.name}\n")
        return 0
    if action == "set":
        if path.exists() and not args.force:
            raise UsageError(f"{args.name} exists (use --force to replace)")
        if args.stdin:
            secret = sys.stdin.buffer.read()
        else:
            import getpass
            if not _interactive_terminal():
                raise UsageError("pass --stdin, or run from a terminal")
            secret = getpass.getpass(f"secret for {args.name}: ").encode("utf-8")
        import tempfile
        with tempfile.NamedTemporaryFile(dir=str(locker.passwords_dir), prefix=".plain-", delete=False) as handle:
            os.chmod(handle.name, 0o600)
            handle.write(secret)
            plain = Path(handle.name)
        try:
            if path.exists():
                os.unlink(path)
            lockermod.lock_file(plain, path, info, name=args.name, remove_source=True)
        finally:
            try:
                os.unlink(plain)
            except FileNotFoundError:
                pass
        if args.json:
            emit_json("password", {"set": args.name, "bytes": len(secret)})
        else:
            sys.stdout.write(f"stored {args.name} ({len(secret)} bytes)\n")
        return 0
    if action == "get":
        if not path.exists():
            raise UsageError(f"no password named {args.name}")
        private, source = obtain_private_key(args, locker, info)
        try:
            import io
            buffer = io.BytesIO()
            lockermod.unlock_file(path, None, private, sink=buffer)
        finally:
            private = b"\0" * len(private)
        secret = buffer.getvalue()
        if args.json:
            emit_json("password", {"name": args.name, "secret": secret.decode("utf-8", "replace"), "key_source": source})
        else:
            sys.stdout.buffer.write(secret)
            if not secret.endswith(b"\n"):
                sys.stdout.buffer.write(b"\n")
            sys.stdout.buffer.flush()
        return 0
    raise UsageError("password: set NAME | get NAME | list | delete NAME")


def cmd_forget(args: argparse.Namespace) -> int:
    agent = _agent(args)
    if not agent.available():
        if args.json:
            emit_json("forget", {"forgotten": False, "agent": False})
        else:
            sys.stdout.write("no agent installed; nothing was held\n")
        return 0
    forgotten = agent.forget()
    if args.json:
        emit_json("forget", {"forgotten": forgotten, "agent": True})
    else:
        sys.stdout.write("locker key forgotten; the next unlock asks for the passphrase\n" if forgotten
                         else "nothing was held\n")
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    agent = _agent(args)
    data: dict[str, Any] = {"socket": agent.path, "available": agent.available()}
    if agent.available():
        try:
            data.update(agent.status())
        except FingerlockError as exc:
            data.update({"ok": False, "error": exc.message})
    if args.json:
        emit_json("agent", data)
        return 0
    if not data["available"]:
        sys.stdout.write(f"agent: not installed ({PROG} install --agent)\n")
        return 1
    if data.get("held"):
        sys.stdout.write(f"agent: holding the locker key for {int(data.get('expires_in', 0)) // 60} more minutes\n")
    else:
        sys.stdout.write("agent: running, no key held; the next unlock asks for the passphrase\n")
    return 0


# --- argument parsing ----------------------------------------------------------------

class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def _locker_options(parser: argparse.ArgumentParser, *, key: bool = True) -> None:
    parser.add_argument("--locker-home", metavar="DIR", help=argparse.SUPPRESS)
    parser.add_argument("--agent-socket", metavar="PATH", help=argparse.SUPPRESS)
    parser.add_argument("--passphrase-fd", type=int, metavar="FD",
                        help="read the passphrase from this descriptor instead of the terminal")
    if key:
        parser.add_argument("--passphrase", action="store_true",
                            help="use the passphrase even when the agent could ask for a touch")
        parser.add_argument("--finger", default=ANY_FINGER, choices=(ANY_FINGER,) + FINGERS, metavar="FINGER",
                            help="finger to verify for the touch; default any")
        parser.add_argument("--no-deposit", action="store_true",
                            help="do not hand the key to the agent after a passphrase unlock")
        parser.add_argument("--ttl", type=float, default=agentmod.DEFAULT_TTL_SECONDS, metavar="SECONDS",
                            help="how long the agent may keep the key after a passphrase unlock")


def _common(parser: argparse.ArgumentParser, *, auth: bool = False, install: bool = False) -> None:
    parser.add_argument("--json", action="store_true", help="emit one kilix.fingerlock/v1 object")
    parser.add_argument("--redact", action="store_true", help="remove serial numbers and USB topology")
    if install:
        parser.add_argument("--pam", action="store_true",
                            help="also want libpam-fprintd, which enables fingerprint login and sudo")
        parser.add_argument("--yes", action="store_true", help="install without asking (sudo may still prompt)")
        parser.add_argument("--agent", action="store_true",
                            help="also install the root agent that makes unlocking a fingerprint touch")
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
    status.add_argument("--pam", action="store_true", help="count libpam-fprintd as required")
    status.set_defaults(handler=cmd_status)

    doctor = subparsers.add_parser("doctor", help="every check between this session and the reader")
    _common(doctor)
    doctor.add_argument("--pam", action="store_true", help="count libpam-fprintd as required")
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

    init = subparsers.add_parser("init", help="create the locker: a passphrase-rooted keypair for locking")
    init.add_argument("--kdf", default="moderate", choices=tuple(lockermod.KDF_PRESETS),
                      help="Argon2id cost preset (default moderate)")
    _common(init)
    _locker_options(init, key=False)
    init.set_defaults(handler=cmd_init)

    passwd = subparsers.add_parser("passwd", help="change the locker passphrase (containers stay openable)")
    _common(passwd)
    _locker_options(passwd, key=False)
    passwd.set_defaults(handler=cmd_passwd)

    lock = subparsers.add_parser("lock", help="seal files into .flk containers; needs no unlock")
    lock.add_argument("paths", nargs="+", metavar="FILE")
    lock.add_argument("--keep", action="store_true", help="keep the plaintext file beside the container")
    _common(lock)
    _locker_options(lock, key=False)
    lock.set_defaults(handler=cmd_lock)

    unlock = subparsers.add_parser("unlock", help="open .flk containers: a touch, or the passphrase")
    unlock.add_argument("paths", nargs="+", metavar="CONTAINER")
    unlock.add_argument("--keep", action="store_true", help="keep the container beside the file")
    unlock.add_argument("--force", action="store_true", help="overwrite an existing file")
    unlock.add_argument("--stdout", action="store_true", help="write one container's content to stdout")
    _common(unlock)
    _locker_options(unlock)
    unlock.set_defaults(handler=cmd_unlock)

    password = subparsers.add_parser("password", help="store and fetch named secrets in the locker")
    password.add_argument("password_action", choices=("set", "get", "list", "delete"), metavar="ACTION",
                          help="set NAME | get NAME | list | delete NAME")
    password.add_argument("name", nargs="?", metavar="NAME")
    password.add_argument("--stdin", action="store_true", help="read the secret from stdin when setting")
    password.add_argument("--force", action="store_true", help="replace an existing name")
    _common(password)
    _locker_options(password)
    password.set_defaults(handler=cmd_password)

    forget = subparsers.add_parser("forget", help="make the agent drop the locker key (lock everything)")
    _common(forget)
    _locker_options(forget, key=False)
    forget.set_defaults(handler=cmd_forget)

    agent = subparsers.add_parser("agent", help="whether the fingerprint-unlock agent is installed and holding a key")
    _common(agent)
    _locker_options(agent, key=False)
    agent.set_defaults(handler=cmd_agent)
    return parser


ACTIONS = ("ensure", "status", "doctor", "install", "list", "enroll", "verify", "delete",
           "init", "passwd", "lock", "unlock", "password", "forget", "agent")


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
