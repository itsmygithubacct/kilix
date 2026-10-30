#!/usr/bin/env python3
"""Shared core of kilix fingerlock: typed errors, the fprintd D-Bus client and
the polkit tty agent. Imported by the verb (config/fingerlock.py), by the
locker (config/fingerlock_locker.py) and by the root agent
(config/fingerlock_agent.py), which is why it carries no argparse and no
per-user paths."""

from __future__ import annotations

import os
import select
import signal
import threading
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

PROG = "kilix fingerlock"
BINDINGS_HINT = "apt: python3-dbus python3-gi"
ANY_FINGER = "any"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


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
        sig_id = None
        # A root-agent scan runs on a worker; only the CLI's main thread owns SIGINT.
        if threading.current_thread() is threading.main_thread():
            sig_id = self._glib.unix_signal_add(self._glib.PRIORITY_DEFAULT, signal.SIGINT, on_sigint)
        try:
            loop.run()
        finally:
            # A false callback return already removed that source.
            if not state["timed_out"]:
                self._glib.source_remove(timeout_id)
            if not state["finished"]:
                self._glib.source_remove(poll_id)
            if sig_id is not None and not state["interrupted"]:
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
                finger = session.finger
                if finger == ANY_FINGER:
                    # Some readers use a different identification path for "any".
                    # With one enrolled print, explicit verification has the same
                    # scope and avoids that unnecessary multi-print lookup.
                    enrolled = self.enrolled_fingers(session.device, username)
                    if len(enrolled) == 1:
                        finger = enrolled[0]
                self._call(device.VerifyStart, finger)
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
