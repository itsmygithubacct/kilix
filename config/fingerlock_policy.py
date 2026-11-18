"""Fresh polkit approval for the exact operation, followed by a fingerprint scan."""
from __future__ import annotations

import itertools
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from fingerlock_core import AccessDenied, DependencyUnavailable, OperationTimeout

ACTION = "org.kilix.fingerlock.unseal"
APPROVAL_TIMEOUT = 120.0
# Results, numbered as pkcheck(1) numbers its exit statuses.
PKCHECK_AUTHORIZED, PKCHECK_REFUSED, PKCHECK_CHALLENGE, PKCHECK_DISMISSED = 0, 1, 2, 3
POLKIT = "org.freedesktop.PolicyKit1"
POLKIT_PATH = "/org/freedesktop/PolicyKit1/Authority"
POLKIT_AUTHORITY = "org.freedesktop.PolicyKit1.Authority"
ALLOW_USER_INTERACTION = 1
_CHECKS = itertools.count(1)
POLICY_FILE = ACTION + ".policy"
POLICY_XML = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>Kilix</vendor>
  <action id="{ACTION}">
    <description>Unlock one file or password with Fingerlock</description>
    <message>Approve this Fingerlock operation, then touch the fingerprint reader.</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>auth_self</allow_active>
    </defaults>
  </action>
</policyconfig>
'''


def _run_check(request: dict[str, Any], alive: Callable[[], bool], timeout: float, *,
               address: str | None = None) -> int:
    """polkit CheckAuthorization over a private system-bus connection.

    The operation's details travel inside the D-Bus message to polkit, never on
    a command line that other users can read in /proc. The check is cancelled
    (closing the dialog) when the caller leaves, custody goes, or it times out.
    The connection runs on its own GLib main context, never the default one a
    reader scan may iterate.
    """
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except Exception as exc:
        raise DependencyUnavailable("polkit approval requires python3-gi") from exc
    context = GLib.MainContext.new()
    context.push_thread_default()
    connection = None
    tick = None
    cancel_id = f"fingerlock-{os.getpid()}-{next(_CHECKS)}-{secrets.token_hex(8)}"
    state: dict[str, Any] = {}
    try:
        try:
            bus = address or Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None)
            connection = Gio.DBusConnection.new_for_address_sync(
                bus, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        except Exception as exc:
            raise DependencyUnavailable("polkit approval requires the system bus") from exc
        connection.set_exit_on_close(False)
        subject = request["subject"]
        params = GLib.Variant("((sa{sv})sa{ss}us)", (
            ("unix-process", {"pid": GLib.Variant("u", subject["pid"]),
                              "start-time": GLib.Variant("t", subject["start"]),
                              "uid": GLib.Variant("i", subject["uid"])}),
            request["action"], dict(request["details"]),
            ALLOW_USER_INTERACTION if request["interactive"] else 0, cancel_id))

        def finished(conn: Any, result: Any, *_user: Any) -> None:
            try:
                state["reply"] = conn.call_finish(result).unpack()[0]
            except Exception as exc:
                state["error"] = exc

        connection.call(POLKIT, POLKIT_PATH, POLKIT_AUTHORITY, "CheckAuthorization", params,
                        GLib.VariantType.new("((bba{ss}))"), Gio.DBusCallFlags.NONE,
                        int((timeout + 5) * 1000), None, finished)
        tick = GLib.timeout_source_new(100)
        tick.set_callback(lambda *_args: True)
        tick.attach(context)
        end = time.monotonic() + timeout
        while not state:
            if not alive():
                # The caller left, or the key this approval was for is no longer held.
                _cancel_check(connection, cancel_id)
                raise AccessDenied("operation approval withdrawn: the caller left or custody was forgotten")
            if end - time.monotonic() <= 0:
                _cancel_check(connection, cancel_id)
                raise OperationTimeout("operation approval timed out")
            context.iteration(True)
        if "error" in state:
            return PKCHECK_REFUSED
        authorized, challenge, details = state["reply"]
        # pkcheck's order: authorized, then challenge, then dismissed.
        if authorized:
            return PKCHECK_AUTHORIZED
        if challenge:
            return PKCHECK_CHALLENGE
        if dict(details).get("polkit.dismissed"):
            return PKCHECK_DISMISSED
        return PKCHECK_REFUSED
    finally:
        if tick is not None:
            tick.destroy()
        if connection is not None:
            try:
                connection.close_sync(None)
            except Exception:
                pass
        context.pop_thread_default()


def _cancel_check(connection: Any, cancel_id: str) -> None:
    from gi.repository import Gio, GLib
    try:
        connection.call_sync(POLKIT, POLKIT_PATH, POLKIT_AUTHORITY, "CancelCheckAuthorization",
                             GLib.Variant("(s)", (cancel_id,)), None, Gio.DBusCallFlags.NONE, 2000, None)
    except Exception:
        pass  # closing the connection also ends the check


def authorize_operation(uid: int, pid: int, binding: dict[str, Any], alive: Callable[[], bool],
                        *, run_check: Callable[..., int] = _run_check,
                        proc: Path = Path("/proc")) -> None:
    if not alive():
        raise AccessDenied("caller disconnected before operation approval")
    try:
        # Field 22, after the parenthesized command which can itself contain ')'.
        fields = (proc / str(pid) / "stat").read_text().rpartition(")")[2].split()
        started = int(fields[19])
        if started <= 0:
            raise ValueError("invalid start time")
    except (OSError, IndexError, ValueError) as exc:
        raise AccessDenied("cannot identify the caller for operation approval") from exc
    # The process pidfd remains live through both checks. Use all three subject
    # coordinates; a bare PID or PID/start-time is insufficient for polkit.
    name = json.dumps(binding["name"], ensure_ascii=True)
    message = (f'Fingerlock: approve {binding["kind"]} requested by process {pid}.\n'
               f'Requested name: {name}\nContainer bytes: {binding["size"]}\n'
               f'SHA-256: {binding["sha256"]}\n'
               'The plaintext will be returned to that process after a fingerprint match.')
    request = {"action": ACTION, "subject": {"pid": pid, "start": started, "uid": uid},
               "details": {"polkit.message": message, "fingerlock.digest": binding["sha256"]},
               "interactive": False}
    # 2 means a challenge needs interaction. An already-authorized action (0),
    # including a cached or permissive policy, cannot establish fresh consent.
    if run_check(request, alive, 5.0) != PKCHECK_CHALLENGE:
        raise AccessDenied("a fresh polkit approval is required for each locker operation")
    result = run_check({**request, "interactive": True}, alive, APPROVAL_TIMEOUT)
    if result == PKCHECK_DISMISSED:
        raise AccessDenied("locker operation approval was cancelled")
    if result != PKCHECK_AUTHORIZED or not alive():
        raise AccessDenied("locker operation approval was refused")
