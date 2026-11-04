"""Fresh polkit approval for the exact operation, followed by a fingerprint scan."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from fingerlock_core import AccessDenied, DependencyUnavailable, OperationTimeout

ACTION = "org.kilix.fingerlock.unseal"
APPROVAL_TIMEOUT = 120.0
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


def _run_check(argv: list[str], alive: Callable[[], bool], timeout: float) -> int:
    """pkcheck owns its bus connection; terminate it on disconnect or timeout."""
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True,
                                   env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    except OSError as exc:
        raise DependencyUnavailable("polkit approval requires /usr/bin/pkcheck") from exc
    end = time.monotonic() + timeout
    try:
        while True:
            if not alive():
                raise AccessDenied("caller disconnected during operation approval")
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise OperationTimeout("operation approval timed out")
            try:
                return process.wait(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


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
    argv = ["/usr/bin/pkcheck", "--action-id", ACTION, "--process", f"{pid},{started},{uid}",
            "--detail", "polkit.message", message,
            "--detail", "fingerlock.digest", binding["sha256"]]
    # 2 means a challenge needs interaction. An already-authorized action (0),
    # including a cached or permissive policy, cannot establish fresh consent.
    if run_check(argv, alive, 5.0) != 2:
        raise AccessDenied("a fresh polkit approval is required for each locker operation")
    result = run_check([*argv, "--allow-user-interaction"], alive, APPROVAL_TIMEOUT)
    if result != 0 or not alive():
        raise AccessDenied("locker operation approval was refused or cancelled")
