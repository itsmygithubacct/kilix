"""Small, credentialed pane primitives for the bundled agent skills.

This is a client of the existing terminal authorizer, not a permissions layer
or an agent supervisor. Input acceptance does not prove application delivery.
No model menus are guessed and no shell command strings are evaluated.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import sys
import time

SCHEMA = "kilix.agent-control/v1"
BROKER = re.compile(r"[0-9a-f]{16,64}\Z")
KEYS = {"enter": b"\r", "escape": b"\x1b", "up": b"\x1b[A",
        "down": b"\x1b[B", "right": b"\x1b[C", "left": b"\x1b[D",
        "tab": b"\t", "ctrl-c": b"\x03"}
LOCATIONS = {"right": "vsplit", "left": "vsplit-before",
             "down": "hsplit", "up": "hsplit-before"}


class ControlError(RuntimeError):
    pass


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("expected a positive pane ID")
    return number


def plain_text(value, label, limit=1024):
    if not value or len(value.encode("utf-8")) > limit:
        raise ControlError(f"{label} must contain 1–{limit} UTF-8 bytes")
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
        raise ControlError(f"{label} must be a single line without control characters")
    return value


def connection_values():
    """Recover only pane connection metadata from this process's ancestors."""
    names = ("KITTY_LISTEN_ON", "KITTY_WINDOW_ID", "KILIX_RC_PASSWORD_FILE",
             "KILIX_KITTEN", "KILIX_BUILD_DIRECTORY", "KILIX_PREBUILT_HOME")
    values = {name: os.environ[name] for name in names if os.environ.get(name)}
    needed = names[:3]
    pid = os.getppid()
    for _ in range(24):
        if all(values.get(name) for name in needed) or pid <= 1:
            break
        proc = Path("/proc") / str(pid)
        try:
            if proc.stat().st_uid != os.getuid():
                break
            entries = (proc / "environ").read_bytes().split(b"\0")
            parent_values = {}
            for entry in entries:
                key, sep, value = entry.partition(b"=")
                name = key.decode("ascii", errors="ignore")
                if sep and name in names:
                    parent_values[name] = os.fsdecode(value)
            # Never combine metadata belonging to different terminal instances.
            if (not values.get("KITTY_LISTEN_ON") or
                    parent_values.get("KITTY_LISTEN_ON") == values["KITTY_LISTEN_ON"]):
                for name, value in parent_values.items():
                    values.setdefault(name, value)
            status = (proc / "status").read_text()
            pid = int(next(line.split()[1] for line in status.splitlines()
                           if line.startswith("PPid:")))
        except (OSError, ValueError, StopIteration):
            break
    return values


def kitten_path(values):
    candidates = [values.get("KILIX_KITTEN", "")]
    match = re.fullmatch(r"unix:@kilix-(\d+)", values.get("KITTY_LISTEN_ON", ""))
    if match:
        try:
            candidates.append(str(Path(os.readlink(f"/proc/{match[1]}/exe")).with_name("kitten")))
        except OSError:
            pass
    if build := values.get("KILIX_BUILD_DIRECTORY"):
        candidates.append(str(Path(build) / "current/src/kitty/launcher/kitten"))
    if prebuilt := values.get("KILIX_PREBUILT_HOME"):
        candidates.append(str(Path(prebuilt) / "bin/kitten"))
    candidates.append(shutil.which("kitten") or "")
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ControlError("matching kitten is unavailable; inspect the installed Kilix engine")


class Client:
    def __init__(self, caller_pane=None):
        values = connection_values()
        socket = values.get("KITTY_LISTEN_ON", "")
        credential = values.get("KILIX_RC_PASSWORD_FILE", "")
        if not socket or not credential:
            raise ControlError("no inherited Kilix connection; select the intended instance explicitly")
        info = Path(credential).lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ControlError("Kilix credential must be a private, owned 0600 regular file")
        inherited = int(values.get("KITTY_WINDOW_ID", "0"))
        if caller_pane and inherited and caller_pane != inherited:
            raise ControlError("--caller-pane conflicts with the inherited caller identity")
        self.caller = caller_pane or inherited
        self.command = [kitten_path(values), "@", "--to", socket,
                        "--password-file", credential]

    def run(self, args, payload=None):
        process = None
        try:
            process = subprocess.Popen(self.command + args,
                                       stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if payload is not None:
                if len(payload) > 1024:
                    raise ControlError("input exceeds 1024 bytes")
                process.stdin.write(payload)
                process.stdin.close()
            output = {"stdout": bytearray(), "stderr": bytearray()}
            total = 0
            deadline = time.monotonic() + 10
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ControlError("terminal request timed out; inspect state before retrying")
                    for key, _ in selector.select(remaining):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        total += len(block)
                        if total > 1024 * 1024:
                            raise ControlError("terminal response exceeded 1 MiB; inspect state before retrying")
                        output[key.data].extend(block)
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            if code:
                raise ControlError(output["stderr"].decode("utf-8", errors="replace").strip()
                                   or f"terminal client exited {code}")
            return output["stdout"].decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ControlError("terminal request failed; inspect state before retrying") from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

    def snapshot(self):
        result = []
        state = json.loads(self.run(["ls"]))
        for os_window in state:
            for tab in os_window.get("tabs", []):
                for pane in tab.get("windows", []):
                    result.append({**pane, "tab_id": tab["id"],
                                   "tab_title": tab.get("title", ""),
                                   "layout": tab.get("layout", ""),
                                   "tab_focused": bool(tab.get("is_focused")),
                                   "os_window_focused": bool(os_window.get("is_focused")),
                                   "os_window_id": os_window["id"]})
        return result

    def resolve(self, pane_id, expected=None, input_target=False):
        panes = self.snapshot()
        matches = [pane for pane in panes if pane.get("id") == pane_id]
        if len(matches) != 1:
            raise ControlError("pane is absent or ambiguous; take a new snapshot")
        pane = matches[0]
        broker = (pane.get("env") or {}).get("KITTY_PTY_BROKER_SESSION", "")
        if expected is not None:
            if not BROKER.fullmatch(expected) or broker != expected:
                raise ControlError("pane/broker identity changed; no input or launch attempted")
            if sum((p.get("env") or {}).get("KITTY_PTY_BROKER_SESSION") == broker
                   for p in panes) != 1:
                raise ControlError("broker session does not identify exactly one pane")
        if input_target:
            if not self.caller or not any(p.get("id") == self.caller for p in panes):
                raise ControlError("caller identity unknown; provide the verified --caller-pane")
            if pane_id == self.caller:
                raise ControlError("refusing input into the controlling agent's own pane")
        return pane

    def input(self, pane_id, expected, payload):
        self.resolve(pane_id, expected, input_target=True)
        if not 0 < len(payload) <= 1024:
            raise ControlError("input exceeds the existing 1024-byte authorization boundary")
        self.run(["send-text", "--match", f"env:KITTY_PTY_BROKER_SESSION={expected}",
                  "--stdin", "--bracketed-paste=disable"], payload)


def describe(pane):
    """Do not publish full environments or prompt-bearing process arguments."""
    processes = []
    agents = set()
    for process in pane.get("foreground_processes") or []:
        argv = process.get("cmdline") or []
        if argv:
            # Some processes overwrite argv[0] with their entire display title.
            # Keep only a bounded program token, never that argument-like title.
            program = Path(argv[0].split(" ", 1)[0]).name[:80]
            processes.append({"pid": process.get("pid"), "program": program})
            for arg in argv[:2]:
                name = Path(arg).name
                if name in ("codex", "codex.js", "claude", "kimi"):
                    agents.add(name.removesuffix(".js"))
    return {"pane_id": pane["id"], "tab_id": pane["tab_id"],
            "os_window_id": pane["os_window_id"], "title": pane.get("title", ""),
            "tab_title": pane["tab_title"], "cwd": pane.get("cwd", ""),
            "layout": pane["layout"], "active_in_tab": bool(pane.get("is_focused")),
            "focused": bool(pane.get("is_focused") and pane.get("tab_focused")
                            and pane.get("os_window_focused")),
            "broker": (pane.get("env") or {}).get("KITTY_PTY_BROKER_SESSION", ""),
            "agent": next(iter(agents)) if len(agents) == 1 else None,
            "foreground_processes": processes,
            "lines": pane.get("lines"), "columns": pane.get("columns"),
            "neighbors": pane.get("neighbors", {})}


def launch(client, args):
    target = client.resolve(args.pane, args.expect_broker)
    if not client.caller or not any(p["id"] == client.caller for p in client.snapshot()):
        raise ControlError("caller identity unknown; provide the verified --caller-pane")
    cwd = Path(args.cwd).expanduser()
    if not cwd.is_absolute() or not cwd.is_dir():
        raise ControlError("--cwd must be an existing absolute directory")
    title = plain_text(args.title, "title", 200)
    # Share the coding-agent installer's executable resolution, including
    # vendor locations that desktop PATH values may omit.
    from agent_programs import resolve_agent_command
    executable = resolve_agent_command(args.agent)
    if not executable:
        raise ControlError(f"{args.agent} is not installed; use kilix install explicitly")
    argv = [executable]
    if args.model:
        argv.extend(["--model", plain_text(args.model, "model", 200)])
    argv.extend(args.agent_arg)
    options = ["launch", "--match", f"window_id:{args.pane}",
               "--next-to", f"id:{args.pane}", "--source-window", f"id:{args.pane}",
               "--keep-focus", "--hold", "--cwd", str(cwd), "--title", title]
    required = ["--next-to", "--source-window", "--keep-focus"]
    if args.action == "new-tab":
        options.extend(["--type", "tab", "--tab-title", title])
    else:
        if target["layout"] != "splits":
            raise ControlError("target tab is not in splits layout; no layout or permission changes made")
        if not 0 < args.bias < 100:
            raise ControlError("split bias must be greater than 0 and less than 100")
        location = LOCATIONS[args.direction]
        options.extend(["--location", location, "--bias", str(args.bias)])
        required.extend(["--bias", location])
    help_text = client.run(["launch", "--help"])
    if any(option not in help_text for option in required):
        raise ControlError("running engine lacks required explicit-target launch options")
    if args.dry_run:
        return {"schema": SCHEMA, "status": "dry_run", "target": describe(target),
                "launch_argv": options + ["--"] + argv}
    before = {pane["id"] for pane in client.snapshot()}
    current = client.resolve(args.pane, args.expect_broker)
    if any(current[key] != target[key] for key in ("tab_id", "os_window_id", "layout")):
        raise ControlError("anchor geometry changed; inspect state before launching")
    raw_id = client.run(options + ["--"] + argv).strip()
    if not raw_id.isdecimal() or int(raw_id) in before or int(raw_id) <= 0:
        raise ControlError("launch returned no new pane ID; inspect state, do not blindly relaunch")
    pane = client.resolve(int(raw_id))
    if ((args.action == "split" and pane["tab_id"] != target["tab_id"])
            or (args.action == "new-tab" and pane["tab_id"] == target["tab_id"])
            or pane["os_window_id"] != target["os_window_id"]):
        raise ControlError(f"created pane {raw_id} in unexpected geometry; retained for inspection")
    return {"schema": SCHEMA, "status": "created", "pane": describe(pane),
            "agent_startup_verified": False, "prompt_submitted": False}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--caller-pane", type=positive,
                        help="verified source pane, only when its environment was stripped")
    commands = result.add_subparsers(dest="action", required=True)
    commands.add_parser("list", help="redacted JSON snapshot; no inferred idle state")
    dump = commands.add_parser("dump", help="read a pane's visible screen")
    dump.add_argument("pane", type=positive)
    dump.add_argument("--lines", type=int, default=80)
    for name in ("send", "key", "new-tab", "split"):
        command = commands.add_parser(name)
        command.add_argument("pane", type=positive,
                             help="input target, or explicit anchor for a new tab/split")
        command.add_argument("--expect-broker", required=True,
                             help="exact broker identity from the inspected snapshot")
        if name == "send":
            text = command.add_mutually_exclusive_group(required=True)
            text.add_argument("--text")
            text.add_argument("--file", type=Path,
                              help="single-line UTF-8 input, at most 1024 bytes")
            command.add_argument("--submit", action="store_true",
                                 help="send a separate Enter after text; verify the result")
        elif name == "key":
            command.add_argument("key", choices=KEYS)
        else:
            command.add_argument("--agent", required=True, choices=("codex", "claude", "kimi"))
            command.add_argument("--cwd", required=True)
            command.add_argument("--title", required=True)
            command.add_argument("--model")
            command.add_argument("--agent-arg", action="append", default=[],
                                 help="one explicit agent argv item; use --agent-arg=--flag")
            command.add_argument("--dry-run", action="store_true")
            if name == "split":
                command.add_argument("--direction", choices=LOCATIONS, default="right")
                command.add_argument("--bias", type=float, default=50,
                                     help="percentage of anchor area allocated to the NEW pane")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        client = Client(args.caller_pane)
        if args.action == "list":
            result = {"schema": SCHEMA, "caller_pane": client.caller,
                      "panes": [describe(pane) for pane in client.snapshot()]}
        elif args.action == "dump":
            if not 1 <= args.lines <= 500:
                raise ControlError("--lines must be between 1 and 500")
            client.resolve(args.pane)
            output = client.run(["get-text", "--match", f"id:{args.pane}", "--extent", "screen"])
            print("\n".join(output.splitlines()[-args.lines:]))
            return 0
        elif args.action in ("new-tab", "split"):
            result = launch(client, args)
        else:
            if args.action == "key":
                payload = KEYS[args.key]
            else:
                if args.file:
                    descriptor = os.open(args.file, os.O_RDONLY | os.O_NONBLOCK)
                    with os.fdopen(descriptor, "rb") as handle:
                        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                            raise ControlError("input file must be a regular file")
                        data = handle.read(1025)
                    value = data.decode("utf-8")
                else:
                    value = args.text
                payload = plain_text(value, "input").encode("utf-8")
            client.input(args.pane, args.expect_broker, payload)
            if args.action == "send" and args.submit:
                time.sleep(0.3)
                client.input(args.pane, args.expect_broker, KEYS["enter"])
            result = {"schema": SCHEMA, "status": "request_sent", "pane_id": args.pane,
                      "delivery_verified": False, "completion_verified": False}
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ControlError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"kilix agent-control: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
