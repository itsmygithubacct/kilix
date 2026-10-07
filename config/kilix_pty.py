#!/usr/bin/env python3
"""Helpers behind `kilix pty`: JSON envelopes, kill receipts and snapshots.

The launcher resolves the broker and the runtime and hands them over:

    kilix_pty.py [--broker B --runtime R --timeout S --guard S] COMMAND ...

    pane PANE_ID                      the broker session behind a kilix pane
    attached                          exit 0 when broker status JSON (stdin) says attached
    list|status ID|reaped --json      the broker's JSON, wrapped in the kilix.pty/v1 envelope
    kill ID [--yes] [--expect-started MILLIS] [--json]
    observe ID --once [--from C] [bounds] [--json]
    journals list DIR [--json] | path DIR ID | show DIR ID [bounds] [--json]
    capabilities | request [--yes] --request-json -|FILE   (see kilix_pty_request.py)

Every broker call is bounded by --guard and gets no stdin. Output read from a
session is untrusted data: it is whatever the program in the pane printed.
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import time

SCHEMA = "kilix.pty/v1"
SESSION_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
CURSOR = re.compile(r"cursor=(\d+):(\d+)")
#: The broker sends SIGTERM, then SIGKILL 1.5 s later; a kill is verified for that
#: long plus two seconds of slack.
KILL_GRACE = 1.5
VERIFY_SLACK = 2.0
DEFAULT_LINES = 200
DEFAULT_BYTES = 64 * 1024
#: The broker replays at most 1 MiB to an observer; a journal can be larger.
SNAPSHOT_LIMIT = 512 * 1024 * 1024
EXIT_USAGE, EXIT_REFUSED, EXIT_NOT_FOUND = 2, 3, 4
#: `ID.STARTED_MILLIS.journal.zst`; the ID may itself contain dots.
ARCHIVE_NAME = re.compile(r"(?P<id>[A-Za-z0-9._-]{1,64})\.(?P<started>[0-9]{1,20})\.journal\.zst")
META_INTEGERS = ("broker_pid", "child_pid", "reaped_millis", "archived_millis",
                 "raw_bytes", "compressed_bytes")


def fail(message: str, code: int = 1) -> int:
    print(f"kilix pty: {message}", file=sys.stderr)
    return code


def cmd_pane(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].isdecimal():
        return fail("usage: kilix pty pane PANE_ID", 2)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    try:
        from kilix_sdk import panes
        workspace = panes.snapshot(timeout=5.0)
    except Exception as error:  # the SDK raises its own PaneError family
        return fail(f"could not list panes: {error}")
    for pane in workspace.panes():
        if str(pane.id) == argv[0]:
            session = pane.broker_session or ""
            if not SESSION_ID.fullmatch(session) or session in {".", ".."}:
                return fail(f"pane {argv[0]} has no persistent session"
                            " (it is an overlay, or KILIX_PTY_BROKER=0)")
            print(session)
            return 0
    return fail(f"no kilix pane with id {argv[0]}")


def cmd_attached(argv: list[str]) -> int:
    """Exit 0 attached, 1 detached, 2 not a status document."""
    if argv:
        return 2
    try:
        document = json.load(sys.stdin)
        return 0 if document["attached"] is True else 1 if document["attached"] is False else 2
    except (ValueError, KeyError, TypeError):
        return 2


def read_meta(path: str, name: "re.Match[str]") -> dict:
    entry = {"id": name["id"], "started_millis": int(name["started"]),
             "reaped_millis": None, "archived_millis": None, "broker_pid": None,
             "child_pid": None, "raw_bytes": None, "compressed_bytes": None}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return entry
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key in META_INTEGERS and value.isdecimal():
            entry[key] = int(value)
    return entry


def journals(directory: str) -> list[dict]:
    """Archived journals, newest session first."""
    found = []
    try:
        names = os.listdir(directory)
    except OSError:
        return found
    for filename in names:
        name = ARCHIVE_NAME.fullmatch(filename)
        if not name or name["id"] in {".", ".."}:
            continue
        path = os.path.join(directory, filename)
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        meta = os.path.join(directory, f"{name['id']}.{name['started']}.meta")
        entry = read_meta(meta, name)
        if entry["compressed_bytes"] is None:
            entry["compressed_bytes"] = info.st_size
        entry["path"] = path
        found.append(entry)
    found.sort(key=lambda entry: (entry["started_millis"], entry["id"]), reverse=True)
    return found


def stamp(millis: int) -> str:
    try:
        moment = datetime.datetime.fromtimestamp(millis / 1000)
    except (OverflowError, OSError, ValueError):
        return "?"
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def size(count) -> str:
    if count is None:
        return "?"
    value = float(count)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return "?"


def emit(document: dict) -> None:
    print(json.dumps(document, indent=2))


def json_of(data: bytes):
    try:
        return json.loads(data)
    except ValueError:
        return None


def is_not_found(message: str) -> bool:
    return "not found" in message


class Broker:
    """One runtime's broker CLI, called with a deadline and no stdin."""

    def __init__(self, path: str, runtime: str, timeout: str | None, guard: float):
        self.path, self.runtime, self.timeout, self.guard = path, runtime, timeout, guard
        self.default_timeout = 2.0  # the broker's per-operation default; list lowers it

    @property
    def timeout_seconds(self) -> float:
        return float(self.timeout) if self.timeout else self.default_timeout

    def call(self, *args: str) -> tuple[int | None, bytes, str]:
        """(exit status, stdout, stderr); the status is None when the guard expired."""
        command = [self.path, "--runtime-dir", self.runtime]
        if self.timeout:
            command += ["--timeout", self.timeout]
        process = subprocess.Popen(command + list(args), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            out, err = process.communicate(timeout=self.guard)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.communicate()
            return None, b"", ""
        return process.returncode, out, err.decode(errors="replace")

    def envelope(self, **fields) -> dict:
        document = {"schema": SCHEMA, "runtime": self.runtime,
                    "timeout_seconds": self.timeout_seconds}
        document.update(fields)
        return document

    def silent(self) -> None:
        print(f"kilix pty: the broker did not answer within {self.guard:g}s", file=sys.stderr)


def reply(broker: Broker, *args: str):
    """The parsed JSON of a broker call, or (None, exit status) after reporting why."""
    status, out, err = broker.call(*args)
    if err:
        sys.stderr.write(err)
    if status is None:
        broker.silent()
        return None, 1
    if status != 0:
        return None, status
    document = json_of(out)
    if document is None:
        print("kilix pty: the broker's reply was not JSON", file=sys.stderr)
        return None, 1
    return document, 0


def cmd_list(broker: Broker, argv: list[str]) -> int:
    # Always --all: the envelope's unreachable array is how a caller learns that
    # a session did not answer, instead of finding it missing.
    broker.default_timeout = 1.0  # list asks every session under one 1 s deadline
    document, status = reply(broker, "list", "--json", "--all")
    if document is None:
        return status
    if not isinstance(document, list):
        return fail("the broker's list was not an array")
    emit(broker.envelope(
        sessions=[e for e in document if e.get("reachable") is not False],
        unreachable=[e for e in document if e.get("reachable") is False]))
    return 0


def cmd_status(broker: Broker, argv: list[str]) -> int:
    ident = argv[0]
    status, out, err = broker.call("status", ident, "--json")
    if status is None:
        broker.silent()
        return 1
    if status != 0:
        if is_not_found(err):
            emit(broker.envelope(result="not_found", id=ident))
            return EXIT_NOT_FOUND
        sys.stderr.write(err)
        return status
    session = json_of(out)
    if not isinstance(session, dict):
        return fail("the broker's status was not an object")
    emit(broker.envelope(session=session))
    return 0


def cmd_reaped(broker: Broker, argv: list[str]) -> int:
    document, status = reply(broker, "reaped", "--json")
    if document is None:
        return status
    emit(broker.envelope(reaped=document))
    return 0


def session_line(session: dict) -> str:
    state = "attached" if session.get("attached") else "detached"
    return (f"{session.get('id')}  {state}  pid={session.get('child_pid')}"
            f"  {session.get('command', '')}")


def cmd_kill(broker: Broker, argv: list[str]) -> int:
    ident, yes, as_json, expect = argv[0], False, False, None
    rest = argv[1:]
    while rest:
        flag = rest.pop(0)
        if flag == "--yes":
            yes = True
        elif flag == "--json":
            as_json = True
        elif flag == "--expect-started" and rest and rest[0].isdecimal():
            expect = int(rest.pop(0))
        else:
            return fail("usage: kilix pty kill ID [--yes] [--expect-started MILLIS] [--json]",
                        EXIT_USAGE)
    sent = False

    def done(result: str, code: int, message: str, reason: str | None = None, **extra) -> int:
        if as_json:
            emit(broker.envelope(result=result, id=ident, request_sent=sent,
                                 reason=reason, message=message, **extra))
        else:
            print(f"kilix pty kill {ident}: {result}: {message}",
                  file=sys.stdout if code == 0 else sys.stderr)
        return code

    if os.environ.get("KITTY_PTY_BROKER_SESSION") == ident:
        return done("refused", EXIT_REFUSED, "that is this pane's own session; ending it would"
                    " end this program. Run the kill from another pane", "own_session")
    status, out, err = broker.call("status", ident, "--json")
    if status is None:
        return done("uncertain", 1, "the broker did not answer; nothing was sent", "status_timeout")
    if status != 0:
        if is_not_found(err):
            return done("not_found", EXIT_NOT_FOUND, "no such session")
        return done("uncertain", 1, "could not look the session up; nothing was sent: "
                    + err.strip(), "status_failed")
    seen = json_of(out)
    seen = seen if isinstance(seen, dict) else {}
    started = seen.get("started_millis")
    if expect is not None and expect != started:
        return done("refused", EXIT_REFUSED,
                    f"started_millis is {started}, not the expected {expect}: another session"
                    " now has this ID", "started_mismatch",
                    started_millis=started, expected_started_millis=expect)
    if not yes:
        if not (os.isatty(0) and os.isatty(2)):
            return fail(f"refusing to end {ident} without --yes (not a terminal)", EXIT_USAGE)
        sys.stderr.write(f"{session_line(seen)}\nEnd this session and the program in it? [y/N] ")
        sys.stderr.flush()
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            return done("refused", EXIT_REFUSED, "not ended", "declined", started_millis=started)
    status, out, err = broker.call("kill", ident)
    sent = True
    began = time.monotonic()
    deadline = began + KILL_GRACE + VERIFY_SLACK
    listed = False
    while True:
        status, out, err = broker.call("list", "--json", "--all")
        entries = json_of(out) if status == 0 else None
        if isinstance(entries, list):
            listed = True
            if not [e for e in entries if e.get("id") == ident
                    and e.get("started_millis") in (None, started)]:
                return done("verified_absent", 0, "the session is gone", started_millis=started,
                            waited_ms=int((time.monotonic() - began) * 1000))
        if time.monotonic() >= deadline:
            break
        time.sleep(0.15)
    return done("uncertain", 1, "the request was sent but the session's absence was not verified"
                + ("" if listed else " (the broker's list did not answer)")
                + "; re-list before retrying", "still_listed" if listed else "list_failed",
                started_millis=started, waited_ms=int((time.monotonic() - began) * 1000))


def render(data: bytes) -> str:
    """What a person saw, replayed on a virtual screen by kilix-transcript-clean."""
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "third_party",
                          "kilix-transcript-clean", "src")
    env = dict(os.environ, PYTHONPATH=source)
    try:
        result = subprocess.run([sys.executable, "-m", "kilix_transcript_clean", "-", "--no-header"],
                                input=data, capture_output=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit(fail(f"could not render the text: {error}"))
    if result.returncode:
        raise SystemExit(fail("kilix-transcript-clean failed: "
                              + result.stderr.decode(errors="replace").strip()))
    return result.stdout.decode("utf-8", "replace")


def bounded(body: bytes, lines: int | None, nbytes: int | None) -> tuple[bytes, bool]:
    """The last `lines` lines and last `nbytes` bytes of body, and whether any were dropped."""
    if lines is None and nbytes is None:
        lines, nbytes = DEFAULT_LINES, DEFAULT_BYTES
    truncated = False
    if lines is not None:
        kept = body.splitlines(keepends=True)
        if len(kept) > lines:
            body, truncated = b"".join(kept[len(kept) - lines:] if lines else []), True
    if nbytes is not None and len(body) > nbytes:
        body, truncated = body[len(body) - nbytes:] if nbytes else b"", True
        while body and 0x80 <= body[0] < 0xC0:  # do not start inside a UTF-8 character
            body = body[1:]
    return body, truncated


class Snapshot:
    """The flags `observe --once` and `journals show` share."""

    def __init__(self, argv: list[str]):
        self.lines = self.nbytes = None
        self.text = self.as_json = self.force = False
        self.cursor = None
        self.rest: list[str] = []
        argv = list(argv)
        while argv:
            flag = argv.pop(0)
            if flag in ("--lines", "--bytes") and argv and argv[0].isdecimal():
                setattr(self, "lines" if flag == "--lines" else "nbytes", int(argv.pop(0)))
            elif flag == "--text":
                self.text = True
            elif flag == "--json":
                self.as_json = True
            elif flag == "--force":
                self.force = True
            elif flag == "--from" and argv:
                self.cursor = argv.pop(0)
            elif flag == "--once":
                pass
            else:
                self.rest.append(flag)
        self.valid = self.lines is None or self.nbytes is None

    def emit(self, broker: Broker, data: bytes, **fields) -> int:
        body = render(data).encode() if self.text else data
        body, truncated = bounded(body, self.lines, self.nbytes)
        if self.as_json:
            document = broker.envelope(**fields, total_bytes=len(data), truncated=truncated,
                                       untrusted=True)
            if self.text:
                document["text"] = body.decode("utf-8", "replace")
            else:
                document["bytes_b64"] = base64.b64encode(body).decode()
            emit(document)
        elif self.text:
            sys.stdout.write(body.decode("utf-8", "replace"))
        else:
            if os.isatty(1) and not self.force:
                return fail("this writes raw terminal bytes; pipe it, or add --text, --json or"
                            " --force", EXIT_USAGE)
            sys.stdout.buffer.write(body)
        return 0


def cmd_observe(broker: Broker, argv: list[str]) -> int:
    ident, flags = argv[0], Snapshot(argv[1:])
    if flags.rest or not flags.valid:
        return fail("usage: kilix pty observe ID --once [--from EPOCH:OFFSET]"
                    " [--lines N | --bytes N] [--text] [--json]", EXIT_USAGE)
    command = ["observe", ident] + (["--from", flags.cursor] if flags.cursor else [])
    status, out, err = broker.call(*command)
    if status is None:
        broker.silent()
        return 1
    if status != 0:
        sys.stderr.write(err)
        return status
    # With no stdin the broker replays what the session holds, prints where it
    # stopped, and leaves; that is the whole snapshot.
    found = CURSOR.search(err)
    return flags.emit(broker, out, id=ident,
                      journal_epoch=int(found[1]) if found else None,
                      cursor=f"{found[1]}:{found[2]}" if found else None)


def cmd_journals(broker: Broker, argv: list[str]) -> int:
    """journals list|path|show DIR ...; DIR is the archive directory."""
    action, directory, rest = argv[0], argv[1], argv[2:]
    if action == "list":
        if rest not in ([], ["--json"]):
            return fail("usage: kilix pty journals list [--json]", EXIT_USAGE)
        entries = journals(directory)
        if rest:
            emit(broker.envelope(journals=entries))
            return 0
        for entry in entries:
            print(f"{stamp(entry['started_millis'])}  {size(entry['raw_bytes']):>7}"
                  f"  {size(entry['compressed_bytes']):>7}  {entry['id']}"
                  f".{entry['started_millis']}")
        return 0
    if not rest or action not in ("path", "show"):
        return fail(f"usage: kilix pty journals {action} ID", EXIT_USAGE)
    wanted = rest[0]
    for entry in journals(directory):
        if wanted in (entry["id"], f"{entry['id']}.{entry['started_millis']}"):
            break
    else:
        return fail(f"no archived journal for {wanted}")
    if action == "path":
        print(entry["path"])
        return 0
    flags = Snapshot(rest[1:])
    if flags.rest or not flags.valid:
        return fail("usage: kilix pty journals show ID [--lines N | --bytes N] [--text] [--json]",
                    EXIT_USAGE)
    try:
        data = subprocess.run(["zstd", "-dcq", "--long=27", "--", entry["path"]],
                              capture_output=True, check=True, timeout=300).stdout
    except (OSError, subprocess.SubprocessError) as error:
        return fail(f"could not read {entry['path']}: {error}")
    if len(data) > SNAPSHOT_LIMIT:
        return fail(f"{entry['path']} is larger than {SNAPSHOT_LIMIT} bytes; use journals path")
    return flags.emit(broker, data, id=entry["id"], started_millis=entry["started_millis"],
                      journal_epoch=None, cursor=None)


def cmd_capabilities(broker: Broker, argv: list[str]) -> int:
    import kilix_pty_request
    return kilix_pty_request.cmd_capabilities(broker, argv)


def cmd_request(broker: Broker, argv: list[str]) -> int:
    import kilix_pty_request
    return kilix_pty_request.cmd_request(broker, argv)


COMMANDS = {"list": cmd_list, "status": cmd_status, "reaped": cmd_reaped,
            "kill": cmd_kill, "observe": cmd_observe, "journals": cmd_journals,
            "capabilities": cmd_capabilities, "request": cmd_request}


def main(argv: list[str]) -> int:
    if argv and argv[0] == "pane":
        return cmd_pane(argv[1:])
    if argv and argv[0] == "attached":
        return cmd_attached(argv[1:])
    options = {"--broker": None, "--runtime": None, "--timeout": None, "--guard": "10"}
    argv = list(argv)
    while argv and argv[0] in options and len(argv) > 1:
        flag = argv.pop(0)
        options[flag] = argv.pop(0)
    # The archive is read from disk: journals needs the runtime for its envelope, not a broker.
    if not argv or argv[0] not in COMMANDS or not options["--runtime"] \
            or (argv[0] not in ("journals", "capabilities") and not options["--broker"]) \
            or len(argv) < (1 if argv[0] in ("list", "reaped", "capabilities", "request") else 2):
        return fail("usage: kilix_pty.py [--broker B --runtime R] pane|attached|list|status|"
                    "reaped|kill|observe|journals|capabilities|request ...", EXIT_USAGE)
    broker = Broker(options["--broker"] or "", options["--runtime"], options["--timeout"],
                    float(options["--guard"]))
    return COMMANDS[argv[0]](broker, argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
