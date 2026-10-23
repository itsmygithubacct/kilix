"""Bounded coding-agent delivery with durable, at-most-once input attempts.

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
import unicodedata
import uuid

from agent_control import BROKER, ControlError, KEYS, SCHEMA, describe, plain_text
from agent_skills import Conflict, directory

MESSAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SGR = re.compile(r"\x1b\[([0-9;:]*)m")
OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
MODEL = re.compile(r"^  (?:GPT-|gpt-|codex|o[1-9])[\w.-]*\b.*[·•]")
CODEX_QUEUE_FOOTER = re.compile(r"  tab to queue message +(?:100|[0-9]{1,2})% context left *")
MAX_WIRE = 900
DELIVERY_AGENTS = ("codex", "claude")
_SHELL_COUNT = r"(?:1 shell|(?:[2-9]|[1-9][0-9]+) shells)"
_MONITOR_COUNT = r"(?:1 monitor|(?:[2-9]|[1-9][0-9]+) monitors)"
_COUNT = r"(?:" + _SHELL_COUNT + r"(?:, " + _MONITOR_COUNT + r")?|" + _MONITOR_COUNT + r")"
CLAUDE_COUNT_FOOTER = re.compile(
    # Bypass mode shows the count directly. Normal auto mode additionally shows
    # Claude's fixed "install gh" hint (and no shift+tab text); only that exact
    # hint is accepted, never an arbitrary segment.
    r"  ⏵⏵ (?:bypass permissions on · |auto mode on · (?:install gh for PR status · )?)"
    + _COUNT +
    r"(?: · (?:esc to interrupt · )?← for agents · ↓ to manage)?\s*"
)


class ScreenError(ControlError):
    """A partial redraw or unsupported layout; never authorize input from it."""


def normalized(text):
    # Codex/terminal wrapping inserts whitespace; payloads are single-line.
    return "".join(text.split())


def composer_matches(screen, wire):
    """Compare the wire reconstructed from the parser's framed screen rows."""
    return not screen["empty"] and screen.get("wire_text") == wire


def wrap_diagnostic(screen, wire):
    """Why a visibly equal wrapped draft is still not exact proof (diagnostic only)."""
    if (screen and not screen.get("empty") and screen.get("wire_text") != wire and
            normalized(screen.get("text", "")) == normalized(wire)):
        return ("; the composer shows the same words but its wrapped rows drop seam whitespace, "
                "so a newline or space there would look identical and exact input cannot be proven: "
                "use --envelope (a short exact one-row wire) or a message that fits one row")
    return ""


def cell_width(text):
    return sum(0 if unicodedata.combining(c) else
               2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def store_envelope(value, *, create):
    """Content-addressed private copy of the caller's exact text; returns (path, sha256, bytes)."""
    data = value.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "kilix/envelopes"
    name = sha[:32] + ".txt"
    try:
        with directory(root, create=create) as fd:
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                raise ControlError("envelope directory must be private (0700)")
            if create:
                try:
                    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(out, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.fsync(fd)
            try:
                handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            except FileNotFoundError:
                raise ControlError("envelope file is missing; no input sent") from None
            with os.fdopen(handle, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                        info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or
                        stream.read(len(data) + 1) != data):
                    raise ControlError("envelope file does not hold the exact message text")
    except Conflict as exc:
        raise ControlError(str(exc)) from exc
    return str(root / name), sha, len(data)


def framed_wire_text(rows, *, prompt_gap=" "):
    """Return observed row characters after removing only identified framing."""
    rows = list(rows)
    if prompt_gap not in (" ", "\N{NO-BREAK SPACE}"):
        return None
    if not rows or not rows[0].startswith(prompt_gap):
        return None
    # Screen APIs may keep spare blank rows after the final composer row. A
    # blank row inside wrapped content is ambiguous and cannot prove input.
    while len(rows) > 1 and not rows[-1].strip():
        rows.pop()
    if any(not row.startswith("  ") for row in rows[1:]):
        return None
    if any(not row.strip() for row in rows[1:]):
        return None
    return rows[0][len(prompt_gap):] + "".join(row[2:] for row in rows[1:])


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


def claude_composer(screen):
    """Recognize Claude's framed input and permission-mode footer."""
    rows = styled_lines(screen)
    text = ["".join(c for c, _, _ in row) for row in rows]
    rules = [i for i, line in enumerate(text)
             if len(line.strip()) >= 10 and set(line.strip()) == {"─"}]
    if len(rules) < 2:
        raise ScreenError("Claude composer frame is not visible; inspect the target")
    top, bottom = rules[-2:]
    if bottom <= top + 1 or not text[top + 1].startswith("❯"):
        raise ScreenError("unrecognized Claude composer layout")
    if not any("shift+tab to cycle" in line or CLAUDE_COUNT_FOOTER.fullmatch(line)
               for line in text[bottom + 1:]):
        raise ScreenError("unrecognized Claude footer; inspect the target")
    if any(line.strip() and not line.startswith("  ")
           for line in text[top + 2:bottom]):
        raise ScreenError("unrecognized Claude composer continuation")
    first = "".join(c for c, _, _ in rows[top + 1][1:])
    continuation = ["".join(c for c, _, _ in row) for row in rows[top + 2:bottom]]
    content = rows[top + 1][1:] + [c for row in rows[top + 2:bottom] for c in row]
    value = "".join(c for c, _, _ in content).strip()
    # Claude renders an idle empty prompt with NBSP. Do not treat an unknown
    # placeholder or a faint draft as empty, and never inspect history prompts.
    if not value and (not content or content[0][0] != "\N{NO-BREAK SPACE}"):
        raise ScreenError("Claude empty composer is not recognized")
    # Claude's prompt spacer is NBSP in the observed client, while older
    # supported screens use ASCII space. Remove exactly that one known gap;
    # any other leading or repeated whitespace remains part of the proof.
    prompt_gap = ("\N{NO-BREAK SPACE}" if first.startswith("\N{NO-BREAK SPACE}")
                  else " " if first.startswith(" ") else None)
    wire_text = (framed_wire_text([first, *continuation], prompt_gap=prompt_gap)
                 if value else None)
    faint_wire_text = None
    if wire_text is not None and prompt_gap is not None:
        wire_cells = (rows[top + 1][1 + len(prompt_gap):] +
                      [cell for row in rows[top + 2:bottom] for cell in row[2:]])
        visible_cells = [(char, faint) for char, faint, _ in wire_cells
                         if not char.isspace()]
        # Claude can display faint suggestion text in the composer. Preserve
        # it as occupied, but do not use faint glyphs as evidence of a typed
        # wire. Ambiguous styling is not an empty composer or delivery proof.
        all_visible_faint = bool(visible_cells) and all(
            faint for _, faint in visible_cells)
        if any(faint for _, faint in visible_cells):
            wire_text = None
        # A prior completed suggestion may be named by its receipt only when
        # every payload glyph is faint and the full wire occupies one physical
        # row. Wrapped rows cannot recover dropped seam spaces exactly.
        if all_visible_faint and not continuation:
            faint_wire_text = framed_wire_text([first], prompt_gap=prompt_gap)
    return {"empty": not value, "text": value,
            "wire_text": wire_text, "faint_wire_text": faint_wire_text,
            "history": "\n".join(text[:top]), "screen": "\n".join(text)}


def composer(screen, agent="codex"):
    """Parse a supported client layout; do not infer readiness from silence."""
    if agent == "claude":
        return claude_composer(screen)
    if agent != "codex":
        raise ScreenError("unsupported delivery agent")
    rows = styled_lines(screen)
    text = ["".join(c for c, _, _ in row) for row in rows]
    candidates = [i for i, row in enumerate(rows)
                  if row and row[0] == ("›", False, True)]
    if not candidates:
        raise ScreenError("Codex composer is not visible; inspect the target")
    start = candidates[-1]
    working = any(re.match(r"^• Working \([^\n]*esc to interrupt\)", line)
                  for line in text[:start])
    # Codex replaces its model footer while a busy composer holds text. The
    # observed queue hint has a bold, non-faint Tab key; plain pasted text is
    # not a footer. Keep unknown hints and ambiguous multiple footers blocked.
    footers = [i for i in range(start + 1, len(text))
               if MODEL.match(text[i]) or (
                   working and CODEX_QUEUE_FOOTER.fullmatch(text[i]) and
                   all(not faint and bold for _, faint, bold in rows[i][2:5]))]
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
    first = "".join(c for c, _, _ in rows[start][1:])
    continuation = [text[i] for i in range(start + 1, end)]
    return {"empty": empty, "text": "" if empty else value,
            "wire_text": framed_wire_text([first, *continuation]),
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
    def __init__(self, fd, name, target=None, *, limit=16384):
        self.fd, self.name, self.target, self.limit = fd, name, target, limit

    def read(self, message_id=None):
        if message_id is None:
            name = self.name
        else:
            if self.target is None or not MESSAGE_ID.fullmatch(message_id):
                raise ControlError("invalid same-target delivery receipt lookup")
            name = self.target + "-" + hashlib.sha256(message_id.encode()).hexdigest() + ".json"
        for _ in range(5):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=self.fd)
            except FileNotFoundError:
                return None
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_nlink > 1 or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ControlError("delivery receipt must be a private owned regular file")
                if info.st_nlink == 0:
                    # A writer may replace the file between open and fstat.
                    # Reopen and validate the current file, not the retired inode.
                    continue
                raw = stream.read(self.limit + 1)
                if len(raw) > self.limit:
                    raise ControlError("delivery receipt exceeds its size limit")
                record = json.loads(raw, object_pairs_hook=unique_object)
                if not isinstance(record, dict):
                    raise ControlError("invalid delivery receipt")
                return record
        raise ControlError("delivery receipt changed repeatedly while reading; retry")

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


def unique_object(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise ControlError("delivery receipt contains a duplicate field")
        record[key] = value
    return record


def validate_record(record, message_id):
    """Reject incomplete or extended state before it can authorize a key."""
    phase = record.get("phase")
    if phase not in ("pasting", "submitting", "submitted", "deferred"):
        raise ControlError("invalid delivery receipt phase")
    expected = {"digest", "phase", "message_id", "verification", "process_identity"}
    if phase in ("submitted", "deferred"):
        expected.add("verified_at")
    if set(record) != expected:
        raise ControlError("invalid delivery receipt fields")
    for name in ("digest", "process_identity"):
        value = record[name]
        if (not isinstance(value, str) or len(value) != 64 or
                any(char not in "0123456789abcdef" for char in value)):
            raise ControlError("invalid delivery receipt digest")
    if (record["message_id"] != message_id or
            record["verification"] not in tuple(agent + "-screen-v1" for agent in DELIVERY_AGENTS)):
        raise ControlError("invalid delivery receipt identity")
    if phase in ("submitted", "deferred"):
        verified_at = record["verified_at"]
        if (type(verified_at) not in (int, float) or not math.isfinite(verified_at)):
            raise ControlError("invalid delivery receipt verification time")


@contextmanager
def ledger(client, args, *, create=True):
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    path = root / "kilix/agent-delivery"
    # The socket is public connection metadata. Password paths/contents are not stored.
    socket = client.command[client.command.index("--to") + 1]
    target = hashlib.sha256((socket + "\0" + args.expect_broker).encode()).hexdigest()
    message = hashlib.sha256(args.message_id.encode()).hexdigest()
    try:
        with directory(path, create=create) as fd:
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                raise ControlError("delivery state directory must be private (0700)")
            lock = os.open(target + ".lock", os.O_RDWR |
                           (os.O_CREAT if create else 0) | os.O_NOFOLLOW |
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
                yield Ledger(fd, target + "-" + message + ".json", target)
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
    finish_staged = getattr(args, "finish_staged", False)
    previous_suggestion_id = getattr(args, "previous_completed_suggestion_id", None)
    try:
        if not MESSAGE_ID.fullmatch(args.message_id):
            raise ControlError("message ID must be 1–64 ASCII letters, digits, dots, underscores or hyphens")
        if not BROKER.fullmatch(args.expect_broker):
            raise ControlError("invalid expected broker")
        if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 60:
            raise ControlError("timeout must be between 1 and 60 seconds")
        if previous_suggestion_id is not None:
            if (not isinstance(previous_suggestion_id, str) or
                    not MESSAGE_ID.fullmatch(previous_suggestion_id) or
                    previous_suggestion_id == args.message_id):
                raise ControlError("previous completed suggestion ID must identify a different valid message")
            if args.mode != "steer" or finish_staged:
                raise ControlError("completed-suggestion authorization applies only to a fresh Claude steer delivery")
            if not getattr(args, "_previous_completed_suggestion_scope_verified", False):
                raise ControlError("previous suggestion lacks matching submitted source-scope proof")
            source = getattr(args, "_previous_completed_suggestion_source_identity", None)
            if (not isinstance(source, dict) or set(source) != {"pane_id", "broker"} or
                    type(source.get("pane_id")) is not int or source["pane_id"] <= 0 or
                    not isinstance(source.get("broker"), str) or
                    not BROKER.fullmatch(source["broker"]) or
                    getattr(client, "caller", None) != source["pane_id"]):
                raise ControlError("previous suggestion source identity is unavailable")
        value = read_payload(args)
        marker = f"[kilix-message:{args.message_id}]"
        if getattr(args, "envelope", False):
            # Opt-in lossless route: the caller's exact text lives in a private
            # content-addressed file and only a short one-row pointer is typed.
            path, sha, size = store_envelope(value, create=not finish_staged)
            receipt["envelope"] = {"path": path, "sha256": sha, "bytes": size}
            value = f"envelope {path} bytes={size}"
        wire = plain_text(marker + " " + value, "message with delivery ID", MAX_WIRE)
        digest = hashlib.sha256((str(args.pane) + "\0" + args.mode + "\0" + wire).encode()).hexdigest()
        client.deadline = min(previous_deadline, time.monotonic() + args.timeout)

        def target():
            pane = client.resolve(args.pane, args.expect_broker, input_target=True)
            agent = describe(pane)["agent"]
            if agent not in DELIVERY_AGENTS:
                raise ControlError("verified delivery supports foreground Codex or Claude only")
            if previous_suggestion_id is not None and agent != "claude":
                raise ControlError("completed-suggestion authorization is Claude-only")
            if agent == "claude" and args.mode == "defer":
                raise ControlError("Claude delivery supports steer only; Tab changes permission mode")
            receipt["verification"] = agent + "-screen-v1"
            processes = pane.get("foreground_processes") or []
            return [(p.get("pid"), p.get("cmdline")) for p in processes]

        def read_screen(identity):
            if target() != identity:
                raise ControlError("foreground process changed; inspect before retrying")
            raw = client.run(["get-text", "--match", f"env:KITTY_PTY_BROKER_SESSION={args.expect_broker}",
                              "--extent", "screen", "--ansi"])
            if target() != identity:
                raise ControlError("foreground process changed during screen read")
            pane = client.resolve(args.pane, args.expect_broker, input_target=True)
            return composer(raw, describe(pane)["agent"])

        def identity_digest(identity):
            return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

        def send(identity, payload):
            if target() != identity:
                raise ControlError("foreground process changed before input")
            if previous_suggestion_id is not None:
                source = args._previous_completed_suggestion_source_identity
                if getattr(client, "caller", None) != source["pane_id"]:
                    raise ControlError("source pane changed before input")
                client.resolve(source["pane_id"], source["broker"])
            client.input(args.pane, args.expect_broker, payload)

        def prior_wire(screen, store, identity):
            if receipt["verification"] != "claude-screen-v1":
                raise ControlError("completed-suggestion authorization is Claude-only")
            wire_value = screen.get("faint_wire_text")
            prefix = f"[kilix-message:{previous_suggestion_id}] "
            if not isinstance(wire_value, str) or not wire_value.startswith(prefix):
                raise ControlError("composer is not the exact faint suggestion named by the prior ID")
            body = wire_value[len(prefix):]
            plain_text(wire_value, "previous suggestion wire", MAX_WIRE)
            if not body.strip() or body.lstrip()[0] in "/!#":
                raise ControlError("prior suggestion is not a valid delivered message wire")
            old = store.read(previous_suggestion_id)
            if old is None:
                raise ControlError("prior suggestion has no existing private delivery receipt")
            validate_record(old, previous_suggestion_id)
            if (old["phase"] != "submitted" or
                    old["verification"] != "claude-screen-v1" or
                    old["process_identity"] != identity_digest(identity)):
                raise ControlError("prior suggestion receipt is not a submitted Claude message for this process")
            old_digest = hashlib.sha256(
                (str(args.pane) + "\0steer\0" + wire_value).encode()).hexdigest()
            if old["digest"] != old_digest:
                raise ControlError("prior suggestion does not match its exact target/mode/body digest")
            return wire_value

        def submitted(screen):
            found = screen["empty"] and normalized(marker) in normalized(screen["history"])
            if args.mode == "defer":
                found = found and "Queued follow-up" in screen["history"]
            return found

        def poll(identity, predicate, wire=None):
            last = None
            while time.monotonic() < client.deadline:
                try:
                    screen = last = read_screen(identity)
                    if predicate(screen):
                        return screen
                except ScreenError:
                    pass  # A redraw is not proof of either delivery or failure.
                time.sleep(min(0.15, max(0, client.deadline - time.monotonic())))
            raise ControlError("delivery verification timed out; inspect before retrying" +
                               (wrap_diagnostic(last, wire) if wire else ""))

        # Finishing an earlier staged message is strictly read/transition-only:
        # it must find the original private directory, lock and receipt.
        with ledger(client, args, create=not finish_staged) as store:
            record = store.read()
            if finish_staged and record is None:
                raise ControlError("finish-staged requires an existing delivery receipt; no input sent")
            if record is not None:
                validate_record(record, args.message_id)
                if record.get("digest") != digest:
                    record = None  # A collision must not mutate the original receipt.
                    raise ControlError("message ID already belongs to a different payload, pane or mode")
                receipt["duplicate"] = True
                if record.get("phase") in ("submitted", "deferred"):
                    return {**receipt, "status": record["phase"], "delivery_verified": True,
                            "verification": record.get("verification", receipt["verification"]),
                            "verified_at": record["verified_at"]}
                # Recovery is observation-only even if the previous process died mid-input.
                identity = target()
                if record["verification"] != receipt["verification"]:
                    raise ControlError("foreground provider changed since the previous attempt")
                if record.get("process_identity") != identity_digest(identity):
                    raise ControlError("foreground process changed since the previous attempt")
                screen = read_screen(identity)
                if finish_staged and record.get("phase") == "pasting":
                    if normalized(marker) in normalized(screen["history"]):
                        raise ControlError("message ID is already visible in history; no key sent")
                    if not composer_matches(screen, wire):
                        raise ControlError("staged composer does not match the original message; no key sent" +
                                           wrap_diagnostic(screen, wire))
                    if args.mode == "defer" and "esc to interrupt" not in screen["screen"].lower():
                        raise ControlError("defer requires a visibly working Codex session; no key sent")
                    # The state transition is durable before we consider a key.
                    # A crash or redraw from here on never permits a key retry.
                    record["phase"] = "submitting"
                    store.write(record)
                    screen = read_screen(identity)
                    if normalized(marker) in normalized(screen["history"]):
                        raise ControlError("message ID appeared in history before submission; no key sent")
                    if not composer_matches(screen, wire):
                        raise ControlError("staged composer changed before submission; no key sent" +
                                           wrap_diagnostic(screen, wire))
                    if args.mode == "defer" and "esc to interrupt" not in screen["screen"].lower():
                        raise ControlError("defer requires a visibly working Codex session; no key sent")
                    send(identity, KEYS["enter" if args.mode == "steer" else "tab"])
                    poll(identity, submitted)
                elif not submitted(screen):
                    raise ControlError("previous attempt is unresolved; no text or key was resent")
            else:
                identity = target()
                screen = read_screen(identity)
                if normalized(marker) in normalized(screen["screen"]):
                    raise ControlError("message ID is already visible without a receipt; inspect it")
                if not screen["empty"]:
                    if previous_suggestion_id is None:
                        raise ControlError("target composer is occupied; existing input was preserved")
                    prefix = f"[kilix-message:{previous_suggestion_id}] "
                    faint_wire = screen.get("faint_wire_text")
                    if faint_wire is None:
                        return {**receipt, "status": "uncertain",
                                "error": "composer is not proven to be the named single-row faint suggestion; preserved",
                                "retry": "reuse the same message ID; never blind-resend"}
                    if not faint_wire.startswith(prefix):
                        raise ControlError("faint suggestion does not identify the requested previous message")
                    old_wire = prior_wire(screen, store, identity)
                    # Re-observe immediately before writing intent. Any changed,
                    # bright, mixed-style or newly wrapped composer is preserved.
                    current = read_screen(identity)
                    if (current.get("faint_wire_text") != old_wire or
                            normalized(marker) in normalized(current["screen"]) or
                            current["history"] != screen["history"]):
                        return {**receipt, "status": "uncertain",
                                "error": "named suggestion changed before paste; composer preserved",
                                "retry": "reuse the same message ID; never blind-resend"}
                    screen = current
                elif previous_suggestion_id is not None:
                    raise ControlError("the named completed suggestion is not visible; no input sent")
                if args.mode == "defer" and "esc to interrupt" not in screen["screen"].lower():
                    raise ControlError("defer requires a visibly working Codex session")
                columns = describe(client.resolve(args.pane, args.expect_broker,
                                                  input_target=True)).get("columns")
                if (getattr(args, "envelope", False) and type(columns) is int and
                        cell_width(wire) > columns - 4):
                    raise ControlError("envelope wire would wrap on this pane; use a shorter message ID "
                                       "or state directory; no input sent")
                # Persist intent BEFORE the first side effect; uncertain retries never resend.
                record = {"digest": digest, "phase": "pasting", "message_id": args.message_id,
                          "verification": receipt["verification"],
                          "process_identity": identity_digest(identity)}
                store.write(record)
                send(identity, wire.encode())
                poll(identity, lambda s: composer_matches(s, wire), wire)
                # Re-read immediately before the key. A competing sender's input is not cleared.
                screen = read_screen(identity)
                if not composer_matches(screen, wire):
                    raise ControlError("composer changed before submission; no submit key sent" +
                                       wrap_diagnostic(screen, wire))
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
