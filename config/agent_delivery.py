"""Bounded Codex message delivery with durable, at-most-once input attempts.

Screen evidence establishes submission, not acknowledgment or task completion.
Unknown layouts and uncertain attempts require inspection, never blind resend.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid

from agent_control import BROKER, ControlError, KEYS, SCHEMA, describe, plain_text
from agent_skills import Conflict, directory

MESSAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SGR = re.compile(r"\x1b\[([0-9;:]*)m")
OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
MODEL = re.compile(r"^  (?:GPT-|gpt-|codex|o[1-9])[\w.-]*\b.*[·•]")
MAX_WIRE = 900


class ScreenError(ControlError):
    """A partial redraw or unsupported layout; never authorize input from it."""


def normalized(text):
    # Codex/terminal wrapping inserts whitespace; payloads are single-line.
    return "".join(text.split())


def styled_lines(screen):
    """Retain SGR faint/bold: a typed placeholder is NOT an empty composer."""
    screen = OSC.sub("", screen)
    lines, row, faint, bold, pos = [], [], False, False, 0
    while pos < len(screen):
        match = SGR.match(screen, pos)
        if match:
            codes = match[1].split(";") if match[1] else ["0"]
            i = 0
            while i < len(codes):
                code = codes[i]
                if code in ("38", "48", "58") and i + 1 < len(codes):
                    # Skip semicolon-form indexed/truecolor components.
                    i += 2 if codes[i + 1] == "5" else 4 if codes[i + 1] == "2" else 1
                elif code == "0":
                    faint = bold = False
                elif code == "1":
                    bold = True
                elif code == "2":
                    faint = True
                elif code == "22":
                    faint = bold = False
                i += 1
            pos = match.end()
            continue
        char = screen[pos]
        if char == "\n":
            lines.append(row)
            row = []
        elif char == "\r":
            pass
        elif char == "\x1b" or (ord(char) < 32 and char != "\t"):
            raise ScreenError("unsupported terminal screen encoding")
        else:
            row.append((char, faint, bold))
        pos += 1
    lines.append(row)
    return lines


def composer(screen):
    """Parse only the supported Codex layout; do not infer readiness from silence."""
    rows = styled_lines(screen)
    text = ["".join(c for c, _, _ in row) for row in rows]
    candidates = [i for i, row in enumerate(rows)
                  if row and row[0] == ("›", False, True)]
    if not candidates:
        raise ScreenError("Codex composer is not visible; inspect the target")
    start = candidates[-1]
    footers = [i for i in range(start + 1, len(text)) if MODEL.match(text[i])]
    if len(footers) != 1:
        raise ScreenError("unrecognized Codex footer; inspect the target")
    end = footers[0]
    if any(t.strip() and not t.startswith("  ") for t in text[start + 1:end]):
        raise ScreenError("unrecognized Codex composer layout")
    content = rows[start][1:] + [c for row in rows[start + 1:end] for c in row]
    visible = [(c, faint) for c, faint, _ in content if not c.isspace()]
    if not visible:
        raise ScreenError("empty composer placeholder is not visible; inspect the target")
    empty = all(faint for _, faint in visible)
    value = "".join(c for c, _, _ in content).strip()
    return {"empty": empty, "text": "" if empty else value,
            "history": "\n".join(text[:start]), "screen": "\n".join(text)}


def read_payload(args):
    if args.file:
        fd = os.open(args.file, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ControlError("input file must be a regular file")
            value = stream.read(MAX_WIRE + 1).decode("utf-8")
    else:
        value = args.text
    value = plain_text(value, "message", MAX_WIRE)
    if not value.strip() or value.lstrip()[0] in "/!#":
        raise ControlError("deliver requires a message, not a client command")
    return value


class Ledger:
    """Private durable receipts, locked per terminal/broker across senders."""
    def __init__(self, fd, name):
        self.fd, self.name = fd, name

    def read(self):
        try:
            fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=self.fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ControlError("delivery receipt must be a private owned regular file")
            raw = stream.read(16385)
            if len(raw) > 16384:
                raise ControlError("delivery receipt exceeds its size limit")
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ControlError("invalid delivery receipt")
            return record

    def write(self, record):
        temporary = ".receipt-" + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass


@contextmanager
def ledger(client, args):
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    path = root / "kilix/agent-delivery"
    # The socket is public connection metadata. Password paths/contents are not stored.
    socket = client.command[client.command.index("--to") + 1]
    target = hashlib.sha256((socket + "\0" + args.expect_broker).encode()).hexdigest()
    message = hashlib.sha256(args.message_id.encode()).hexdigest()
    try:
        with directory(path, create=True) as fd:
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                raise ControlError("delivery state directory must be private (0700)")
            lock = os.open(target + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW |
                           os.O_NONBLOCK, 0o600, dir_fd=fd)
            try:
                info = os.fstat(lock)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ControlError("delivery lock must be a private owned regular file")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ControlError("another delivery owns this target; retry with the same message ID") from exc
                yield Ledger(fd, target + "-" + message + ".json")
            finally:
                os.close(lock)
    except Conflict as exc:
        raise ControlError(str(exc)) from exc


def deliver(client, args):
    receipt = {"schema": SCHEMA, "message_id": args.message_id, "pane_id": args.pane,
               "status": "blocked", "delivery_verified": False,
               "acknowledgment_verified": False, "completion_verified": False,
               "verification": "codex-screen-v1", "duplicate": False}
    previous_deadline = getattr(client, "deadline", float("inf"))
    record = None
    try:
        if not MESSAGE_ID.fullmatch(args.message_id):
            raise ControlError("message ID must be 1–64 ASCII letters, digits, dots, underscores or hyphens")
        if not BROKER.fullmatch(args.expect_broker):
            raise ControlError("invalid expected broker")
        if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 60:
            raise ControlError("timeout must be between 1 and 60 seconds")
        value = read_payload(args)
        marker = f"[kilix-message:{args.message_id}]"
        wire = plain_text(marker + " " + value, "message with delivery ID", MAX_WIRE)
        digest = hashlib.sha256((str(args.pane) + "\0" + args.mode + "\0" + wire).encode()).hexdigest()
        client.deadline = min(previous_deadline, time.monotonic() + args.timeout)

        def target():
            pane = client.resolve(args.pane, args.expect_broker, input_target=True)
            if describe(pane)["agent"] != "codex":
                raise ControlError("verified delivery currently supports foreground Codex only")
            processes = pane.get("foreground_processes") or []
            return [(p.get("pid"), p.get("cmdline")) for p in processes]

        def read_screen(identity):
            if target() != identity:
                raise ControlError("foreground process changed; inspect before retrying")
            raw = client.run(["get-text", "--match", f"env:KITTY_PTY_BROKER_SESSION={args.expect_broker}",
                              "--extent", "screen", "--ansi"])
            if target() != identity:
                raise ControlError("foreground process changed during screen read")
            return composer(raw)

        def identity_digest(identity):
            return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

        def send(identity, payload):
            if target() != identity:
                raise ControlError("foreground process changed before input")
            client.input(args.pane, args.expect_broker, payload)

        def submitted(screen):
            found = screen["empty"] and normalized(marker) in normalized(screen["history"])
            if args.mode == "defer":
                found = found and "Queued follow-up" in screen["history"]
            return found

        def poll(identity, predicate):
            while time.monotonic() < client.deadline:
                try:
                    screen = read_screen(identity)
                    if predicate(screen):
                        return screen
                except ScreenError:
                    pass  # A redraw is not proof of either delivery or failure.
                time.sleep(min(0.15, max(0, client.deadline - time.monotonic())))
            raise ControlError("delivery verification timed out; inspect before retrying")

        with ledger(client, args) as store:
            record = store.read()
            if record is not None:
                if record.get("digest") != digest:
                    record = None  # A collision must not mutate the original receipt.
                    raise ControlError("message ID already belongs to a different payload, pane or mode")
                receipt["duplicate"] = True
                if record.get("phase") in ("submitted", "deferred"):
                    return {**receipt, "status": record["phase"], "delivery_verified": True,
                            "verified_at": record["verified_at"]}
                # Recovery is observation-only even if the previous process died mid-input.
                identity = target()
                if record.get("process_identity") != identity_digest(identity):
                    raise ControlError("foreground process changed since the previous attempt")
                screen = read_screen(identity)
                if not submitted(screen):
                    raise ControlError("previous attempt is unresolved; no text or key was resent")
            else:
                identity = target()
                screen = read_screen(identity)
                if normalized(marker) in normalized(screen["screen"]):
                    raise ControlError("message ID is already visible without a receipt; inspect it")
                if not screen["empty"]:
                    raise ControlError("target composer is occupied; existing input was preserved")
                if args.mode == "defer" and "esc to interrupt" not in screen["screen"].lower():
                    raise ControlError("defer requires a visibly working Codex session")
                # Persist intent BEFORE the first side effect; uncertain retries never resend.
                record = {"digest": digest, "phase": "pasting", "message_id": args.message_id,
                          "process_identity": identity_digest(identity)}
                store.write(record)
                send(identity, wire.encode())
                poll(identity, lambda s: not s["empty"] and normalized(s["text"]) == normalized(wire))
                # Re-read immediately before the key. A competing sender's input is not cleared.
                screen = read_screen(identity)
                if screen["empty"] or normalized(screen["text"]) != normalized(wire):
                    raise ControlError("composer changed before submission; no submit key sent")
                record["phase"] = "submitting"
                store.write(record)
                send(identity, KEYS["enter" if args.mode == "steer" else "tab"])
                poll(identity, submitted)
            record.update(phase="submitted" if args.mode == "steer" else "deferred",
                          verified_at=time.time())
            store.write(record)
            return {**receipt, "status": record["phase"], "delivery_verified": True,
                    "verified_at": record["verified_at"]}
    except (ControlError, OSError, ValueError, TypeError, KeyError) as exc:
        return {**receipt, "status": "uncertain" if record is not None else "blocked",
                "error": str(exc)[:240], "retry": "reuse the same message ID; never blind-resend"}
    finally:
        client.deadline = previous_deadline
