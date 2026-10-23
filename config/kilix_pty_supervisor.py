#!/usr/bin/env python3
"""Direct-user terminal selection followed by a constrained Linux actor.

Launch only from a trusted user terminal. No --yes, selection argv or user-FD
argument exists. The actor never inherits that terminal or the issuance channel.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import secrets
import select
import selectors
import socket
import signal
import threading
import stat
import subprocess
import tempfile
import time

import kilix_pty as pty
import kilix_pty_controller as authority
import kilix_pty_request as legacy
import kilix_pty_selector as selector

BWRAP = "/usr/bin/bwrap"
MAX_LINE = 16384
MAX_OUTPUT = 65536
MAX_CALLS = 64


def runtime_assets():
    """Preflight and identify the trusted host-side renderer, not an actor mount."""
    source = Path(__file__).resolve().parent.parent / 'third_party/kilix-transcript-clean/src/kilix_transcript_clean'
    hashes = {}
    for name in ('__init__.py', '__main__.py', 'providers.py', 'screen.py', 'sources.py', 'stitch.py'):
        path = source / name
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
                    raise ValueError('unbounded renderer dependency')
                data = stream.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError('unbounded renderer dependency')
        except OSError:
            raise ValueError('missing trusted transcript-clean dependency; materialize the pinned submodule') from None
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def read_line(fd, timeout=300, limit=8192):
    """No buffered read-ahead across the user-selection/revocation boundary."""
    deadline = time.monotonic() + timeout
    line = bytearray()
    while len(line) <= limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise ValueError("user input timed out")
        byte = os.read(fd, 1)
        if not byte:
            raise ValueError("user input closed")
        if byte == b"\n":
            return line.decode("utf-8")
        line += byte
    raise ValueError("user input exceeds its bound")


def tell(fd, message):
    # Only fixed prompts and ASCII JSON reach the trusted terminal.
    os.write(fd, (message + "\n").encode("ascii"))


def user_selection(controller, broker, ui_fd, lifetime):
    if not os.isatty(ui_fd):
        raise ValueError("direct user selection requires a controlling terminal")
    tell(ui_fd, 'Selector JSON (one id/id_prefix/startup_words; optional attached):')
    original = selector.validate_selector(selector.parse_json(read_line(ui_fd).encode()))
    request = {"schema": selector.REQUEST_SCHEMA, "verb": "end", "selector": original,
               "operation_id": secrets.token_hex(16)}
    context = selector.capture(broker)
    doc, code, _ = selector.broker_document(broker, request, "resolve")
    if (doc is None or code or not doc["complete"] or not doc["actionable"]
            or doc["resolved"]["id"] == context["caller"]["id"]):
        return request, {"issued": False, "reason": "selection_not_actionable"}
    tell(ui_fd, json.dumps({"original_selector": original, "resolved": doc["resolved"]}, ensure_ascii=True))
    tell(ui_fd, 'Type END to authorize this selection; anything else cancels:')
    if read_line(ui_fd, limit=16) != "END":
        return request, {"issued": False, "reason": "declined"}
    issued = controller.select(request, lifetime_seconds=lifetime, expected_binding=doc["resolved"])
    return request, issued


def raw_request(controller, broker, request, issued, argv):
    """R1-compatible facade, NOT unrestricted native broker access.

    A raw kill can only consume this terminal-issued selection. The controller
    retains the original discriminator/qualifiers/key, never an actor-picked ID.
    """
    if not isinstance(argv, list) or not argv or len(argv) > 8 or any(type(x) is not str for x in argv):
        raise ValueError("invalid broker argv")
    verb, *args = argv
    if verb in ("journals", "pane"):
        return controller.read_cli(argv)
    if verb == "kill":
        if not issued["issued"]:
            return {"code": 3, "receipt": {"result": "refused", "reason": "authority_required"}}
        valid = len(args) == 1 or (len(args) == 3 and args[1] == "--expect-started"
                                               and args[2].isascii() and args[2].isdecimal())
        if (not valid or args[0] != issued["resolved"]["id"]
                or (len(args) == 3 and int(args[2]) != issued["resolved"]["started_millis"])):
            controller.revoke()
            raise ValueError("raw kill does not match the user-selected incarnation")
        code, receipt = controller.dispatch(request)
        return {"code": code, "receipt": receipt}
    allowed = ((verb == "list" and len(args) <= 2 and len(set(args)) == len(args)
                and set(args) <= {"--json", "--all"})
               or (verb == "reaped" and args in ([], ["--json"]))
               or (verb == "status" and len(args) in (1, 2) and selector.valid_id(args[0])
                   and (len(args) == 1 or args[1] == "--json"))
               or (verb == "observe" and len(args) == 1 and selector.valid_id(args[0])))
    if not allowed:
        raise ValueError("only bounded broker reads and selected kill are admitted")
    code, out, _ = broker.call(*argv)  # DEVNULL stdin; existing bounded broker transport.
    if len(out) > MAX_OUTPUT // 2:
        raise ValueError("broker read exceeds response bound")
    return {"code": code if code is not None else 1, "stdout": out.decode("utf-8", errors="replace")}


def dispatch(controller, broker, request, issued, message):
    if not isinstance(message, dict):
        raise ValueError("actor envelope must be an object")
    if set(message) == {"route", "request"} and message["route"] == "selector":
        code, receipt = controller.dispatch(message["request"])
        return {"code": code, "receipt": receipt}
    if set(message) == {"route", "request"} and message["route"] == "read":
        code, receipt = controller.read(message["request"])
        return {"code": code, "receipt": receipt}
    if set(message) == {"route", "argv"} and message["route"] == "broker":
        return raw_request(controller, broker, request, issued, message["argv"])
    if set(message) == {"route", "argv"} and message["route"] == "host_read":
        return controller.read_cli(message["argv"])
    raise ValueError("no issuance, revocation or arbitrary command route is exposed")


def snapshot_client(source, root):
    """Freeze each trusted client dependency for this launch; no mutable bind source."""
    destination = root / "clients" / source.name
    destination.parent.mkdir(exist_ok=True)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
            raise ValueError("client dependency must be a bounded regular file")
        data = stream.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("client dependency exceeds bound")
    destination.write_bytes(data)
    destination.chmod(0o555)
    return destination


class WorkDeadline(BaseException):
    """Not an ordinary tool error: unwind broker cleanup without continuing work."""


class CleanupDeadline(BaseException):
    pass


class WorkTimer:
    """Main-thread Linux work cutoff; the second alarm bounds cleanup only."""
    def __init__(self, deadline, process):
        self.deadline, self.process = deadline, process
        self.cleaning = False
        self.cleanup_deadline = deadline + 5

    def __enter__(self):
        self.previous = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, self.expired)
        return self

    def start(self):
        signal.setitimer(signal.ITIMER_REAL, max(.0001, self.deadline - time.monotonic()))

    def expired(self, *unused):
        if self.cleaning:
            raise CleanupDeadline()
        self.cleaning = True
        # Kill only the owned Popen child; its PID namespace owns actor descendants.
        self.process.kill()
        signal.setitimer(signal.ITIMER_REAL, max(.0001, self.cleanup_deadline - time.monotonic()))
        raise WorkDeadline()

    def finish(self):
        self.cleaning = True
        self.cleanup_deadline = min(self.cleanup_deadline, time.monotonic() + 5)
        signal.setitimer(signal.ITIMER_REAL, max(.0001, self.cleanup_deadline - time.monotonic()))

    def __exit__(self, *unused):
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.previous)


def model_endpoint(path):
    """Only an explicit trusted per-case inference endpoint; never actor supplied."""
    path = Path(path).absolute()
    if path.resolve(strict=True) != path:
        raise ValueError("model socket path must not contain symlinks")
    info, parent = path.lstat(), path.parent.lstat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700):
        raise ValueError("model endpoint requires owned0700 directory and0600 socket")
    return path


def sandbox_argv(root, actor, caller, needle_source=None, model_channel=None):
    info = os.stat(BWRAP)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("bubblewrap must be a root-owned, non-writable system executable")
    source = Path(__file__).parent
    client = snapshot_client(source / "kilix_pty_agent_client.py", root)
    argv = [BWRAP, "--unshare-all", "--unshare-user", "--disable-userns", "--assert-userns-disabled",
            "--new-session", "--die-with-parent", "--cap-drop", "ALL",
            "--clearenv", "--setenv", "PATH", "/tools:/usr/bin:/bin",
            "--setenv", "KITTY_PTY_BROKER_SESSION", caller,
            "--ro-bind", "/usr/bin", "/usr/bin", "--ro-bind", "/usr/lib", "/usr/lib",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/run", "--dir", "/tools",
            "--ro-bind", str(root / "dispatch.sock"), "/run/dispatch.sock",
            "--ro-bind", str(root / "selection.json"), "/run/selection.json",
            "--ro-bind", str(client), "/tools/kilix_pty_agent_client.py",
            "--ro-bind", str(client), "/tools/kitty-pty-broker",
            "--ro-bind", str(actor), "/actor.py", "--chdir", "/tmp"]
    for name in ("kilix_pty.py", "kilix_pty_request.py", "kilix_pty_selector.py",
                 "kilix_pty_selector_validation.py", "kilix_pty_host_client.py"):
        argv += ["--ro-bind", str(snapshot_client(source / name, root)), "/tools/" + name]
    argv += ["--ro-bind", str(client.with_name("kilix_pty_host_client.py")), "/tools/kilix"]
    argv += ["--ro-bind", str(snapshot_client(source / "pty_actor_guidance.txt", root)),
             "/tools/PTY-GUIDANCE.txt"]
    skill = source.parent / "skills/kilix-pty"
    argv += ["--dir", "/tools/kilix-pty", "--dir", "/tools/kilix-pty/references",
             "--ro-bind", str(snapshot_client(skill / "SKILL.md", root)), "/tools/kilix-pty/SKILL.md",
             "--ro-bind", str(snapshot_client(skill / "references/receipts.md", root)),
             "/tools/kilix-pty/references/receipts.md"]
    if needle_source is not None:
        # Only the reviewed PTY client surface; no controller implementation,
        # launcher, user home, storage, model weights or credentials are mounted.
        for name in ("pty_cli.py", "pty_job.py", "pty_backend.py", "pty_selector_receipt.py", "json_request.py",
                     "pty_controller_client.py", "mcp_server.py", "engine.py", "libengine.py",
                     "asset.py", "action_cli.py", "action_backend.py"):
            argv += ["--ro-bind", str(snapshot_client(needle_source / name, root)), "/tools/" + name]
        argv += ["--ro-bind", str(root / "clients/pty_controller_client.py"), "/tools/kilix-needle"]
    if model_channel is not None:
        argv += ["--ro-bind", str(model_endpoint(model_channel)), "/run/model.sock"]
    return argv + ["/usr/bin/python3", "-I", "/actor.py"]


def limits(profile="fixture"):
    # Local bounded actor fixtures, not a model-weight runtime or a DoS proof.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    memory, cpu = (512 * 1024 * 1024, 30) if profile == "model180" else (256 * 1024 * 1024, 5)
    resource.setrlimit(resource.RLIMIT_AS, (memory,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))



def observe_tree(pid, observations):
    """Read only descendants of the unreaped Popen child; never signal these IDs."""
    todo = [(pid, None)]
    visited = set()
    while todo and len(visited) < 128:
        current, parent = todo.pop()
        if current in visited:
            continue
        visited.add(current)
        try:
            fields = Path(f"/proc/{current}/stat").read_text().rsplit(")", 1)[1].split()
            if parent is not None and int(fields[1]) != parent:
                continue
            observations.add((current, int(fields[19])))
            children = Path(f"/proc/{current}/task/{current}/children").read_text().split()
            todo.extend((int(child), current) for child in children[:128])
        except (OSError, ValueError, IndexError):
            pass  # Exiting observations cannot authorize a signal or a success claim.


def run_actor(controller, broker, request, issued, actor, ui_fd, lifetime, needle_source=None,
              *, profile="fixture", model_channel=None, model_deadline=None):
    """Actor stdout is data, never forwarded as terminal UI or interpreted as RPC."""
    if profile not in ("fixture", "model180") or ((profile == "model180") != (model_channel is not None)):
        raise ValueError("model180 requires a dedicated model channel; fixture has none")
    if profile == "model180":
        if (type(model_deadline) not in (float, int) or not math.isfinite(model_deadline)
                or not 0 < model_deadline - time.monotonic() <= 180):
            raise ValueError("model180 requires the shared trusted deadline within180 seconds")
        if threading.current_thread() is not threading.main_thread() or any(signal.getitimer(signal.ITIMER_REAL)):
            raise ValueError("model180 needs the main thread with no existing alarm")
    elif model_deadline is not None:
        raise ValueError("fixture profile has no model deadline")
    if model_channel is not None:
        model_channel = model_endpoint(model_channel)
    calls, output, observations = [], bytearray(), set()
    with tempfile.TemporaryDirectory(prefix="kpc-", dir="/tmp") as directory:
        root = Path(directory)
        (root / "selection.json").write_text(json.dumps({"request": request, "issued": issued,
                                                       "caller": broker.caller}, ensure_ascii=True))
        # Snapshot the untrusted actor to a bounded plain file before launch.
        actor_fd = os.open(actor, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(actor_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
                raise ValueError("actor source must be a bounded regular file")
            code = stream.read(1024 * 1024 + 1)
        if len(code) > 1024 * 1024:
            raise ValueError("actor source exceeds 1 MiB")
        (root / "actor.py").write_bytes(code)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(root / "dispatch.sock"))
            os.chmod(root / "dispatch.sock", 0o600)
            listener.listen(4)
            argv = sandbox_argv(root, root / "actor.py", broker.caller["id"] or "", needle_source, model_channel)
            launch_started = time.monotonic()
            if model_deadline is not None and launch_started >= model_deadline:
                raise ValueError("model deadline expired before actor launch")
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, close_fds=True, preexec_fn=functools.partial(limits, profile))
            observe_tree(process.pid, observations)
            wall_seconds = 185 if profile == "model180" else min(lifetime + 5, 65)
            actor_started = time.monotonic()
            deadline = model_deadline if model_deadline is not None else actor_started + wall_seconds
            stop = "actor_exited"
            call_inflight = False
            interrupted_call = False
            with (WorkTimer(deadline, process) if model_deadline is not None else contextlib.nullcontext(None)) as timer:
                try:
                    if timer is not None:
                        timer.start()
                    tell(ui_fd, 'ACTOR_RUNNING; type REVOKE and Enter to revoke authority.')
                    with selectors.DefaultSelector() as events:
                        events.register(ui_fd, selectors.EVENT_READ, "user")
                        events.register(listener, selectors.EVENT_READ, "request")
                        events.register(process.stdout, selectors.EVENT_READ, "output")
                        while process.poll() is None:
                            if time.monotonic() >= deadline:
                                stop = "model_work_deadline" if model_deadline is not None else "actor_deadline"
                                break
                            ready = events.select(min(.1, deadline - time.monotonic()))
                            # Revocation has priority over pending requests in this sample.
                            if any(key.data == "user" for key, _ in ready):
                                os.read(ui_fd, 8192)
                                controller.revoke()
                                stop = "user_revoked"
                                break
                            for key, _ in ready:
                                if key.data == "output":
                                    data = os.read(process.stdout.fileno(), 4096)
                                    output += data
                                    if not data:
                                        events.unregister(process.stdout)
                                    if len(output) > MAX_OUTPUT:
                                        stop = "actor_output_limit"
                                        break
                                elif key.data == "request":
                                    with listener.accept()[0] as connection:
                                        connection.settimeout(.5)
                                        try:
                                            with connection.makefile("rb") as stream:
                                                raw = stream.readline(MAX_LINE + 1)
                                            if len(raw) > MAX_LINE or not raw.endswith(b"\n"):
                                                raise ValueError("actor request exceeds bound or is incomplete")
                                            message = selector.parse_json(raw)
                                            call_inflight = True
                                            reply = dispatch(controller, broker, request, issued, message)
                                        except (ValueError, OSError, legacy.Refusal, RecursionError):
                                            reply = {"code": 3, "receipt": {"result": "refused", "reason": "invalid_actor_request"}}
                                        encoded = json.dumps(reply, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
                                        if len(encoded) > MAX_OUTPUT:
                                            encoded = b'{"code":3,"receipt":{"result":"refused","reason":"response_limit"}}\n'
                                        calls.append(reply)
                                        call_inflight = False
                                        observe_tree(process.pid, observations)
                                        try:
                                            connection.sendall(encoded)
                                        except OSError:
                                            pass  # A lost receipt never rearms authority.
                                    tell(ui_fd, "REQUEST_COMPLETE")
                                    if len(calls) >= MAX_CALLS:
                                        stop = "actor_request_limit"
                                        break
                            if stop != "actor_exited":
                                break
                except WorkDeadline:
                    stop = "model_work_deadline"
                    interrupted_call = call_inflight
                    if interrupted_call:
                        calls.append({"code": 1, "receipt": {"result": "uncertain", "reason": "work_deadline",
                                                            "completion": "unknown"}})
                finally:
                    if timer is not None:
                        timer.finish()
                    controller.revoke()
                    if process.poll() is None:
                        process.kill()  # Owned unreaped bwrap child; PID namespace dies with it.
                    try:
                        tail, _ = process.communicate(timeout=min(3, max(.0001, timer.cleanup_deadline-time.monotonic())) if timer else 3)
                        output += tail
                    except subprocess.TimeoutExpired:
                        process.stdout.close()
                        if timer is not None:
                            raise CleanupDeadline()
                        raise RuntimeError("sandbox cleanup did not complete within its bound")
                cleanup = [{"pid": pid, "start_ticks": ticks, "absent_or_exited": False}
                           for pid, ticks in sorted(observations)]
                cleanup_deadline = min(time.monotonic() + 3, timer.cleanup_deadline) if timer else time.monotonic() + 3
                while True:
                    for row in cleanup:
                        if not row["absent_or_exited"]:
                            row["absent_or_exited"] = selector.process_absent(row["pid"], row["start_ticks"])
                    if all(row["absent_or_exited"] for row in cleanup) or time.monotonic() >= cleanup_deadline:
                        break
                    time.sleep(.01)
                end_receipts = [call["receipt"] for call in calls if isinstance(call.get("receipt"), dict)
                                and call["receipt"].get("schema") == selector.SCHEMA
                                and call["receipt"].get("operation") == "end"]
                outcome = ("verified_absent" if any(row["result"] == "verified_absent" for row in end_receipts)
                           else end_receipts[-1]["result"] if end_receipts else "not_requested")
                return {"stop": stop, "actor_exit": process.returncode, "calls": calls,
                        "profile": profile, "actor_wall_seconds": wall_seconds,
                        "model_channel_exposed": model_channel is not None,
                        "operation_outcome": "uncertain" if interrupted_call else outcome,
                        "interrupted_call": interrupted_call,
                        "launch_started_monotonic": launch_started,
                        "actor_started_monotonic": actor_started,
                        "work_deadline_monotonic": deadline,
                        "cleanup_finished_monotonic": time.monotonic(),
                        "cleanup_deadline_monotonic": timer.cleanup_deadline if timer else cleanup_deadline,
                        "cleanup_observations": cleanup,
                        "actor_output": bytes(output[:MAX_OUTPUT]).decode("utf-8", errors="replace"),
                        "actor_sha256": hashlib.sha256(code).hexdigest(),
                        "client_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                          for p in sorted((root / "clients").iterdir())},
                        "bwrap_sha256": hashlib.sha256(Path(BWRAP).read_bytes()).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--actor", required=True, type=Path)
    parser.add_argument("--needle-source", type=Path)
    parser.add_argument("--lifetime", type=float, default=30)
    parser.add_argument("--profile", choices=("fixture", "model180"), default="fixture")
    parser.add_argument("--model-channel", type=Path)
    parser.add_argument("--model-deadline", type=float, help="trusted shared monotonic work deadline; never actor input")
    parser.add_argument("--read-only", action="store_true", help="launch without any end authority")
    parser.add_argument("--owned-pane", type=int, action="append", default=[],
                        help="trusted owned-pane read admission; repeat for each fixture pane, never actor input")
    args = parser.parse_args(argv)
    if not math.isfinite(args.lifetime) or not 0 < args.lifetime <= 60:
        parser.error("lifetime must be positive and at most 60 seconds")
    broker = pty.Broker(str(args.broker.resolve()), str(args.runtime.resolve()), "0.3", 5)
    controller = authority.Controller(broker, str(args.state.resolve()))
    result = {"schema": "kilix.pty.supervisor/v1", "issued": False, "read_only": args.read_only}
    try:
        result['runtime_asset_sha256'] = runtime_assets()
        if args.owned_pane:
            from kilix_pty_pane_lookup import PaneLookup
            controller._pane_lookup = PaneLookup(args.owned_pane)
        result['owned_pane_read_admission'] = sorted(set(args.owned_pane))
        # No stdin fallback or caller-supplied descriptor: the trusted launcher's
        # controlling terminal is a separate channel never inherited by the actor.
        fd = os.open("/dev/tty", os.O_RDWR | os.O_CLOEXEC | os.O_NOCTTY)
        try:
            if args.read_only:
                request, issued = None, {"issued": False, "reason": "read_only"}
            else:
                request, issued = user_selection(controller, broker, fd, args.lifetime)
            result.update(issued=issued["issued"], selection=issued)
            if issued["issued"] or args.read_only:
                result.update(run_actor(controller, broker, request, issued, args.actor, fd,
                                        args.lifetime, args.needle_source.resolve() if args.needle_source else None,
                                        profile=args.profile, model_channel=args.model_channel, model_deadline=args.model_deadline))
        finally:
            os.close(fd)
    except CleanupDeadline:
        result.update(result="uncertain", error="owned cleanup deadline exceeded", reason="cleanup_deadline")
    except (OSError, ValueError, legacy.Refusal) as error:
        result.update(error=str(error), result="refused")
    finally:
        controller.revoke()
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if (result.get("actor_exit") == 0 and result.get("stop") == "actor_exited"
                 and all(row["absent_or_exited"] for row in result["cleanup_observations"])
                 and (args.read_only or result.get("operation_outcome") == "verified_absent")) else 3


if __name__ == "__main__":
    raise SystemExit(main())
