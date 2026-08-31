#!/usr/bin/python3
"""Fail-closed Steam orchestration for ``kilix steam``.

This public-base consumer intentionally stops before tab creation, privilege,
or Steam launch until the named private-display provider and accepted install
authority are available.  Read-only SDK probes remain useful in the meantime.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from typing import Mapping


CLIENT = Path("/usr/bin/kilix-valve-client")
OUTPUT_LIMIT = 64 * 1024
PROBE_TIMEOUT_SECONDS = 15.0
PROCESS_GROUP_STOP_GRACE_SECONDS = 1.0
PROCESS_GROUP_KILL_WAIT_SECONDS = 1.0
STEAM_SESSION_PROFILE = "steam-v1"

_CLASSIFICATIONS = frozenset((
    "unknown",
    "absent",
    "exact",
    "partial",
    "conflicting",
    "unsupported-architecture",
    "unrelated-running",
))

_STATUS_BOOLEAN_FIELDS = (
    "helper_verified",
    "policy_verified",
    "i386_enabled",
    "package_installed",
    "launcher_verified",
)
_STATUS_FIELDS = frozenset(("classification", *_STATUS_BOOLEAN_FIELDS))


@dataclass(frozen=True)
class ConsentMoment:
    position: str
    title: str
    schema: str
    body: str
    affirmative_action: str
    decline_action: str


CONSENT_MOMENTS = (
    ConsentMoment(
        position="1/2",
        title="Valve terms",
        schema="kilix.install.license/v1",
        body=(
            "Review the authoritative Valve terms through the accepted "
            "license presenter. This decision covers only those terms; it "
            "grants 0/1 package-manager authorizations."
        ),
        affirmative_action=(
            "Supplied by the accepted F100/F107 presenter (0/1 available)"
        ),
        decline_action="Not now",
    ),
    ConsentMoment(
        position="2/2",
        title="Valve software source and i386",
        schema="kilix.install.authorization/v2",
        body=(
            "Allow Valve's signed software source and 32-bit packages? This "
            "adds a dedicated Valve APT source and signing key under an exact "
            "package pin. It gives Valve standing authority to provide the "
            "admitted packages as root now and in later system updates. It "
            "also enables i386 for system-wide dependency resolution. "
            "Removing Steam does not necessarily remove i386 when other "
            "32-bit packages remain."
        ),
        affirmative_action="Enable Valve source and i386",
        decline_action="Not now",
    ),
)

TRUST_DISCLOSURE_ATOMS = (
    "vendor: Valve",
    "standing scope: this trust persists for admitted later updates",
    "root effect: admitted packages are installed with system privilege",
    "archive/key: dedicated signed Valve APT source and signing key",
    "pin/package boundary: exact pin and admitted package allowlist",
    "i386 effect: system-wide foreign-architecture dependency resolution",
    "rollback: i386 may remain while other 32-bit packages are installed",
)


@dataclass(frozen=True)
class ClientResult:
    returncode: int
    stdout: str
    stderr: str


class SteamUnavailable(RuntimeError):
    """A fail-closed prerequisite result safe to show to the user."""


def _probe_environment() -> dict[str, str]:
    return {
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "TZ": "UTC",
    }


def _stop_client_process_group(process: subprocess.Popen[bytes]) -> bool:
    """Bound cleanup to the new process group created for the fixed client."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass

    deadline = time.monotonic() + PROCESS_GROUP_STOP_GRACE_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.05, remaining))

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass

    try:
        process.wait(timeout=PROCESS_GROUP_KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=PROCESS_GROUP_KILL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            return False
    return True


def _run_client(command: str) -> ClientResult:
    """Run one fixed read-only SDK command with bounded time and output."""
    if command not in ("status", "doctor", "plan-install"):
        raise ValueError("only fixed read-only client commands are admitted")
    if not CLIENT.is_file() or not os.access(CLIENT, os.X_OK):
        return ClientResult(127, "", "packaged kilix-valve-client is absent")
    try:
        process = subprocess.Popen(
            (str(CLIENT), command),
            cwd="/",
            env=_probe_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return ClientResult(70, "", type(error).__name__)

    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    failure = ""
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "TimeoutExpired"
                break
            for key, _events in selector.select(remaining):
                total = len(outputs["stdout"]) + len(outputs["stderr"])
                read_limit = min(8192, OUTPUT_LIMIT + 1 - total)
                chunk = os.read(key.fd, read_limit)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                outputs[key.data].extend(chunk)
                if (len(outputs["stdout"]) + len(outputs["stderr"])
                        > OUTPUT_LIMIT):
                    failure = "bounded client output exceeded"
                    break
            if failure:
                break
    except OSError as error:
        failure = type(error).__name__
    finally:
        selector.close()

    returncode = None
    if not failure:
        try:
            returncode = process.wait(
                timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            failure = "TimeoutExpired"

    if failure:
        if not _stop_client_process_group(process):
            failure = "fixed client process group could not be reaped"
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
        return ClientResult(70, "", failure)

    assert returncode is not None
    return ClientResult(
        returncode,
        outputs["stdout"].decode("utf-8", "replace").strip(),
        outputs["stderr"].decode("utf-8", "replace").strip(),
    )


def _unique_json_object(
        pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, member in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = member
    return value


def _probe() -> tuple[Mapping[str, object], ClientResult]:
    result = _run_client("status")
    if not result.stdout:
        raise SteamUnavailable(
            _bounded_reason(result.stderr or "Steam system probe failed"))
    try:
        status = json.loads(
            result.stdout, object_pairs_hook=_unique_json_object)
    except (RecursionError, TypeError, ValueError) as error:
        raise SteamUnavailable(
            "Steam system probe returned invalid output") from error
    if not isinstance(status, dict) or frozenset(status) != _STATUS_FIELDS:
        raise SteamUnavailable(
            "Steam system probe returned an invalid status schema")
    if any(type(status[field]) is not bool
           for field in _STATUS_BOOLEAN_FIELDS):
        raise SteamUnavailable(
            "Steam system probe returned an invalid status schema")
    classification = status["classification"]
    if type(classification) is not str:
        raise SteamUnavailable(
            "Steam system probe returned an invalid classification")
    if classification not in _CLASSIFICATIONS:
        raise SteamUnavailable(
            "Steam system probe returned an invalid classification")
    if (classification == "exact"
            and not all(status[field] for field in _STATUS_BOOLEAN_FIELDS)):
        raise SteamUnavailable(
            "Steam system probe contradicted its exact evidence")
    expected_returncode = 0 if classification == "exact" else 3
    if result.returncode != expected_returncode:
        raise SteamUnavailable(
            "Steam system probe contradicted its classification")
    return status, result


def _bounded_reason(reason: object) -> str:
    text = "".join(
        character if character.isprintable() else " "
        for character in str(reason)
    )
    clean = " ".join(text.split())
    return clean[:512] or "provider returned no reason"


def _provider_status() -> tuple[bool, str]:
    """Query only the named provider API; absence or drift is not capability."""
    try:
        from kilix_sdk import gpu_session  # type: ignore[attr-defined]
    except ImportError:
        return False, (
            "steam-v1 provider unavailable: named profile module 0/1"
        )

    profile = getattr(gpu_session, "STEAM_SESSION_PROFILE", None)
    query = getattr(gpu_session, "session_profile_status", None)
    if profile != STEAM_SESSION_PROFILE or not callable(query):
        return False, (
            "steam-v1 provider unavailable: exact capability query 0/1"
        )
    try:
        answer = query(STEAM_SESSION_PROFILE)
    except Exception as error:  # provider boundary: expose type, not details
        return False, f"steam-v1 provider query failed: {type(error).__name__}"
    if (not isinstance(answer, tuple) or len(answer) != 2
            or not isinstance(answer[0], bool)
            or not isinstance(answer[1], str)):
        return False, "steam-v1 provider returned an invalid capability result"
    return answer[0], _bounded_reason(answer[1])


def _system_layer_fraction(classification: str) -> str:
    return "1/1" if classification == "exact" else "0/1"


def print_plan() -> None:
    print("Required Steam consent moments described: 2/2")
    print("Combined confirmation allowed: 0/1")
    for moment in CONSENT_MOMENTS:
        print()
        print(f"Moment {moment.position} — {moment.title}")
        print(f"Record family: {moment.schema}")
        print(moment.body)
        print(f"Affirmative action: {moment.affirmative_action}")
        print(f"Decline action: {moment.decline_action}")
    print()
    print("Valve terms reproduced here: 0/N words")
    print("Trust disclosure atoms: 7/7")
    for atom in TRUST_DISCLOSURE_ATOMS:
        print(f"- {atom}")
    print("Accepted pre-mutation authorization-v2 mediator: 0/1")
    print("System mutations authorized by this plan: 0/1")


def print_status(doctor: bool = False) -> int:
    try:
        status, _ = _probe()
        classification = str(status["classification"])
        print(
            "Steam system layer: "
            f"{_system_layer_fraction(classification)} ({classification})"
        )
    except SteamUnavailable as error:
        print(f"Steam system layer: 0/1 ({error})")

    supported, reason = _provider_status()
    print(f"Steam-private display profile: {int(supported)}/1 ({reason})")
    print("Fixed Steam presentation runner: 0/1 (public-base staging only)")
    print("Accepted authorization-v2 mediator: 0/1")
    if doctor:
        result = _run_client("doctor")
        diagnostic = _bounded_reason(
            result.stdout or result.stderr or "diagnostic unavailable")
        print(f"Bounded SDK diagnostic: {diagnostic}")
    return 1


def install() -> int:
    print_plan()
    print()
    print("Installation stopped before consent collection:")
    print("Accepted pre-mutation authorization-v2 mediator: 0/1")
    print("Positive authorization-v2 records created: 0/1")
    print("Privileged helper executions: 0/1")
    print("System mutations: 0/1")
    print(
        "The authority owner has not supplied the opaque, policy-bound "
        "pre-mutation handle. A license decision cannot replace it."
    )
    return 1


def preflight() -> int:
    if os.geteuid() == 0:
        print(
            "kilix steam: Steam must run as the unprivileged desktop user; "
            "no tab opened",
            file=sys.stderr,
        )
        return 1
    try:
        status, _ = _probe()
    except SteamUnavailable as error:
        print(f"kilix steam: {error}; no tab opened", file=sys.stderr)
        return 1
    classification = str(status["classification"])
    if classification == "unrelated-running":
        print(
            "kilix steam: Steam is already running outside this Kilix tab; "
            "it was left untouched. Exit that instance, then retry.",
            file=sys.stderr,
        )
        return 1
    if classification != "exact":
        print(
            "kilix steam: packaged Steam system layer is "
            f"{_system_layer_fraction(classification)} ({classification}); "
            "run 'kilix steam install'; no tab opened",
            file=sys.stderr,
        )
        return 1
    supported, reason = _provider_status()
    if not supported:
        print(f"kilix steam: {reason}; no tab opened", file=sys.stderr)
        return 1
    print(
        "kilix steam: fixed Steam presentation runner is 0/1 on this "
        "public-base staging branch; no tab opened",
        file=sys.stderr,
    )
    return 1


def run() -> int:
    """Refuse before presentation until the clean provider base is admitted."""
    return preflight()


def print_help() -> None:
    print("usage: kilix steam [run|status|doctor|plan|install]")
    print("Steam is optional; status, doctor, and plan are read-only.")


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = arguments.pop(0) if arguments else "run"
    if arguments:
        print_help()
        return 2
    if command in ("help", "-h", "--help"):
        print_help()
        return 0
    if command == "status":
        return print_status()
    if command == "doctor":
        return print_status(doctor=True)
    if command in ("plan", "plan-install"):
        print_plan()
        return 0
    if command == "install":
        return install()
    if command == "preflight":
        return preflight()
    if command == "run":
        return run()
    print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
