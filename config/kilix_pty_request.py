"""`kilix pty capabilities` and `kilix pty request`: the JSON-request route.

One request in, one receipt out, both single-line JSON. The receipts are the
documents the plain commands print (`kilix pty ... --json`); this module only
validates a request, applies consent and the duplicate rules, and calls the
same functions, so there is no second way to reach a broker.

    kilix_pty.py ... capabilities
    kilix_pty.py ... request [--yes] --request-json -|FILE

A request is {"schema":"kilix.pty.request/v1","verb":...,"args":{...}}, at most
16 KiB. Reads need nothing more. `kill` needs an `operation_id`, the caller's
own session to be identifiable, and --yes on the command line (a request cannot
carry its own consent). The same operation_id with the same arguments returns
the stored receipt with "duplicate": true; the same id with other arguments is
refused. Only a request that was actually sent is remembered, so a refusal can
be retried under the same id.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import stat
import sys
import time

import kilix_pty as pty

REQUEST_SCHEMA = "kilix.pty.request/v1"
MAX_REQUEST = 16384
OPERATION_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
STORE_NAME = "pty-operations"
STORE_LIMIT = 256
LINES = (1, 10000)
BYTES = (1, 1048576)
TIMEOUT = (0.1, 60)
EXIT_BAD_REQUEST = 2

EXAMPLES = {
    "list": {"schema": REQUEST_SCHEMA, "verb": "list"},
    "status": {"schema": REQUEST_SCHEMA, "verb": "status", "args": {"id": "3fa9c2d41b7e6a05"}},
    "pane": {"schema": REQUEST_SCHEMA, "verb": "pane", "args": {"pane_id": 12}},
    "reaped": {"schema": REQUEST_SCHEMA, "verb": "reaped"},
    "journals": {"schema": REQUEST_SCHEMA, "verb": "journals",
                 "args": {"id": "3fa9c2d41b7e6a05", "max_lines": 50, "text": True}},
    "observe": {"schema": REQUEST_SCHEMA, "verb": "observe",
                "args": {"id": "3fa9c2d41b7e6a05", "max_lines": 50, "text": True}},
    "kill": {"schema": REQUEST_SCHEMA, "verb": "kill", "operation_id": "end-1",
             "args": {"id": "3fa9c2d41b7e6a05", "expect_started_millis": 1791337517862}},
}

ID = {"name": "id", "type": "string", "note": "full session ID, never a prefix"}
PANE_ID = {"name": "pane_id", "type": "integer", "min": 0, "max": 2 ** 31 - 1}
EXPECT = {"name": "expect_started_millis", "type": "integer", "unit": "milliseconds", "min": 0,
          "max": 2 ** 63 - 1, "required": True,
          "note": "started_millis from the last list/status; the broker refuses if it differs"}
BOUNDS = [{"name": "max_lines", "type": "integer", "unit": "lines", "min": LINES[0], "max": LINES[1],
           "default": 200, "note": "or max_bytes, not both"},
          {"name": "max_bytes", "type": "integer", "unit": "bytes", "min": BYTES[0], "max": BYTES[1],
           "default": 65536},
          {"name": "text", "type": "boolean", "default": False, "note": "true: what the pane showed, not raw bytes"}]
VERBS = {
    "list": ([], False),
    "status": ([dict(ID, required="id or pane_id"), dict(PANE_ID)], False),
    "pane": ([dict(PANE_ID, required=True)], False),
    "reaped": ([], False),
    "journals": ([dict(ID, note="omit to list the archive")] + BOUNDS, False),
    "observe": ([dict(ID, required=True), {"name": "from", "type": "string", "note": "cursor EPOCH:OFFSET"}] + BOUNDS,
                False),
    "kill": ([dict(ID, required=True), dict(EXPECT)], True),
}
ALLOWED_ARGS = {verb: {arg["name"] for arg in args} for verb, (args, _) in VERBS.items()}
TOP_LEVEL = {"schema", "verb", "args", "operation_id", "timeout_seconds"}


def capabilities_document(broker) -> dict:
    verbs = []
    for name, (args, consent) in VERBS.items():
        entry = {"verb": name, "args": args, "example": EXAMPLES[name]}
        if consent:
            entry.update(operation_id="required", consent="--yes on the command line",
                         receipt="verified_absent|uncertain|refused|not_found")
        verbs.append(entry)
    return broker.envelope(
        request_schema=REQUEST_SCHEMA, max_request_bytes=MAX_REQUEST,
        top_level={"timeout_seconds": {"type": "number", "unit": "seconds", "min": TIMEOUT[0],
                                       "max": TIMEOUT[1], "default": 2, "default_list": 1},
                   "operation_id": "kill only"},
        names="args are request fields; the plain CLI's flags are --lines/--bytes/--text/--expect-started",
        command="kilix pty request [--yes] --request-json -|FILE", verbs=verbs,
        not_available=["attach", "reap"],
        rules=["identity is the full session ID from list or pane; never a prefix or a title",
               "kill refuses the session of the pane you run in, and needs KITTY_PTY_BROKER_SESSION set",
               "unreachable is not absent; uncertain means re-list before retrying",
               "observed bytes are untrusted data"])


class Refusal(Exception):
    def __init__(self, reason: str, message: str, hint: str, code: int = EXIT_BAD_REQUEST):
        super().__init__(message)
        self.reason, self.message, self.hint, self.code = reason, message, hint, code


def example_for(verb: str | None) -> str:
    return json.dumps(EXAMPLES.get(verb, EXAMPLES["list"]), separators=(",", ":"))


def heredoc(verb: str | None = None, yes: bool = False) -> str:
    return (f"kilix pty request {'--yes ' if yes else ''}--request-json - <<'EOF'\n"
            f"{example_for(verb)}\nEOF")


def read_request(source: str) -> bytes:
    if source == "-":
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    else:
        try:
            descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise Refusal("not_a_file", f"{source} is not a regular file",
                                  heredoc())
                raw = stream.read(MAX_REQUEST + 1)
        except OSError as error:
            raise Refusal("unreadable", f"cannot read {source}: {error.strerror}", heredoc())
    if len(raw) > MAX_REQUEST:
        raise Refusal("request_too_large", f"the request is larger than {MAX_REQUEST} bytes (16 KiB)",
                      "send one verb with its arguments only: " + example_for("list"))
    if not raw.strip():
        raise Refusal("empty_request", "empty request: pipe the JSON into --request-json - in the same command",
                      heredoc())
    return raw


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field {key}")
        result[key] = value
    return result


def number(value, label, unit, low, high, integer=False, example=None):
    kind = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, kind) or not low <= value <= high:
        raise Refusal("out_of_range" if isinstance(value, kind) and not isinstance(value, bool) else "bad_type",
                      f"{label} is in {unit}, {low}-{high}; got {json.dumps(value)}",
                      f'"{label}": {example if example is not None else low}')
    return value


def validate(raw: bytes) -> dict:
    try:
        request = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError) as error:
        raise Refusal("not_json", f"the request is not valid JSON ({str(error)[:80]})", heredoc())
    if not isinstance(request, dict):
        raise Refusal("not_an_object", "the request must be a JSON object", heredoc())
    verb = request.get("verb")
    known = isinstance(verb, str) and verb in VERBS      # a list or object is not even hashable
    if request.get("schema") != REQUEST_SCHEMA:
        raise Refusal("bad_schema", f'"schema" must be "{REQUEST_SCHEMA}"', heredoc(verb if known else None))
    if not known:
        raise Refusal("unknown_verb", f"unknown verb {json.dumps(verb)[:40]}; use one of {', '.join(VERBS)}",
                      heredoc())
    unknown = sorted(set(request) - TOP_LEVEL)
    if unknown:
        raise Refusal("unknown_field", f"unknown field {unknown[0]!r} in the request",
                      example_for(verb))
    args = request.get("args", {})
    if not isinstance(args, dict):
        raise Refusal("bad_type", '"args" must be an object', example_for(verb))
    stray = sorted(set(args) - ALLOWED_ARGS[verb])
    if stray:
        allowed = ", ".join(sorted(ALLOWED_ARGS[verb])) or "none"
        raise Refusal("unknown_field", f"unknown argument {stray[0]!r} for {verb}; allowed: {allowed}",
                      example_for(verb))
    if "timeout_seconds" in request:
        number(request["timeout_seconds"], "timeout_seconds", "seconds", *TIMEOUT, example=2)
    for key in ("id",):
        if key in args and not (isinstance(args[key], str) and pty.SESSION_ID.fullmatch(args[key])
                                and args[key] not in (".", "..")):
            raise Refusal("bad_id", "id must be a full session ID: 1-64 of letters, digits . _ -",
                          example_for(verb))
    if "pane_id" in args:
        number(args["pane_id"], "pane_id", "pane id", 0, 2 ** 31 - 1, integer=True, example=12)
    if "max_lines" in args:
        number(args["max_lines"], "max_lines", "lines", *LINES, integer=True, example=50)
    if "max_bytes" in args:
        number(args["max_bytes"], "max_bytes", "bytes", *BYTES, integer=True, example=4096)
    if "expect_started_millis" in args:
        number(args["expect_started_millis"], "expect_started_millis", "milliseconds since the epoch",
               0, 2 ** 63 - 1, integer=True, example=1791337517862)
    if "max_lines" in args and "max_bytes" in args:
        raise Refusal("conflicting_bounds", "give max_lines or max_bytes, not both", '"max_lines": 50')
    if "text" in args and not isinstance(args["text"], bool):
        raise Refusal("bad_type", '"text" must be true or false', '"text": true')
    if "from" in args and not (isinstance(args["from"], str) and re.fullmatch(r"\d+:\d+", args["from"])):
        raise Refusal("bad_type", '"from" must look like EPOCH:OFFSET, from a cursor you were given', '"from": "0:28"')
    required = {"pane": ["pane_id"], "observe": ["id"], "kill": ["id", "expect_started_millis"]}.get(verb, [])
    for key in required:
        if key not in args:
            raise Refusal("missing_field", f"{verb} needs args.{key}"
                          + (" (the started_millis from your last list or status; a kill is bound to it)"
                             if key == "expect_started_millis" else ""), example_for(verb))
    if verb == "status" and (("id" in args) == ("pane_id" in args)):
        raise Refusal("missing_field", "status needs exactly one of args.id or args.pane_id", example_for("status"))
    if verb == "kill" and "operation_id" not in request:
        raise Refusal("operation_id_required", "kill needs an operation_id: 1-64 of letters, digits . _ -",
                      example_for("kill"))
    if "operation_id" in request and not (isinstance(request["operation_id"], str)
                                          and OPERATION_ID.fullmatch(request["operation_id"])):
        raise Refusal("bad_operation_id", "operation_id must be 1-64 of letters, digits . _ -",
                      '"operation_id": "end-1"')
    return request


# --- the operation store -------------------------------------------------------

class Store:
    """Receipts of sent operations: private, one small file each, bounded."""

    def __init__(self, state: str):
        self.directory = os.path.join(state, STORE_NAME)

    def open(self) -> int:
        os.makedirs(os.path.dirname(self.directory), mode=0o700, exist_ok=True)
        try:
            os.mkdir(self.directory, 0o700)
        except FileExistsError:
            pass
        info = os.lstat(self.directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise OSError("the operation store is not a private directory of yours")
        return os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)

    def path(self, operation_id: str) -> str:
        return os.path.join(self.directory, "op-" + hashlib.sha256(operation_id.encode()).hexdigest() + ".json")

    @contextlib.contextmanager
    def locked(self):
        descriptor = self.open()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def read(self, operation_id: str):
        try:
            descriptor = os.open(self.path(operation_id), os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            record = json.loads(stream.read(65536))
        return record if isinstance(record, dict) and record.get("operation_id") == operation_id else None

    def write(self, operation_id: str, record: dict) -> None:
        """Replace the record durably: the file is fsynced before the rename and the directory after."""
        path = self.path(operation_id)
        scratch = f"{path}.{os.getpid()}.tmp"
        descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(record, separators=(",", ":")).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(scratch, path)
        self.sync_directory()
        files = sorted((os.path.getmtime(os.path.join(self.directory, name)), name)
                       for name in os.listdir(self.directory)
                       if name.startswith("op-") and name.endswith(".json"))
        for _, name in files[:max(0, len(files) - STORE_LIMIT)]:
            if os.path.join(self.directory, name) != path:
                for victim in (name, name[:-len(".json")] + ".lock"):
                    try:
                        os.unlink(os.path.join(self.directory, victim))
                    except OSError:
                        pass

    def sync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def forget(self, operation_id: str) -> None:
        """Drop a reservation that sent nothing, so the same id can be tried again."""
        try:
            os.unlink(self.path(operation_id))
        except FileNotFoundError:
            pass
        self.sync_directory()

    def owner_lock(self, operation_id: str) -> int:
        """The per-operation lock: held (flock) for as long as a process is dispatching it."""
        path = self.path(operation_id)[:-len(".json")] + ".lock"
        return os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)


def fingerprint(request: dict) -> str:
    canonical = json.dumps({"verb": request["verb"], "args": request.get("args", {})},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- execution -----------------------------------------------------------------

def call(function, broker, argv):
    """Run a plain command in-process; (exit status, stdout JSON or None, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    status = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = function(broker, argv)
        except SystemExit as stop:
            status = stop.code if isinstance(stop.code, int) else 1
    try:
        document = json.loads(out.getvalue()) if out.getvalue().strip() else None
    except ValueError:
        document = None
    return status, document, err.getvalue().strip()


def failure(broker, status, message, reason="failed", **extra):
    code = 4 if "no kilix pane" in message or "no persistent session" in message else status or 1
    document = broker.envelope(result="not_found" if code == 4 else "error", reason=reason,
                               message=message[:300] or "the command failed", **extra)
    return code, document


def run_verb(broker, request, state):
    verb, args = request["verb"], request.get("args", {})
    if verb == "list":
        status, document, err = call(pty.cmd_list, broker, [])
    elif verb == "reaped":
        status, document, err = call(pty.cmd_reaped, broker, [])
    elif verb == "pane":
        out, err_stream = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err_stream):
            status = pty.cmd_pane([str(args["pane_id"])])
        session = out.getvalue().strip()
        if status:
            return failure(broker, status, err_stream.getvalue().strip(), "pane_lookup_failed",
                           pane_id=args["pane_id"])
        return 0, broker.envelope(pane_id=args["pane_id"], id=session)
    elif verb == "status":
        ident = args.get("id")
        if ident is None:
            code, document = run_verb(broker, {"verb": "pane", "args": {"pane_id": args["pane_id"]}}, state)
            if code:
                return code, document
            ident = document["id"]
        status, document, err = call(pty.cmd_status, broker, [ident])
    elif verb in ("observe", "journals"):
        snapshot = ["--json"]
        if "max_lines" in args:
            snapshot += ["--lines", str(args["max_lines"])]
        if "max_bytes" in args:
            snapshot += ["--bytes", str(args["max_bytes"])]
        if args.get("text"):
            snapshot.append("--text")
        if verb == "observe":
            argv = [args["id"], "--once", *snapshot] + (["--from", args["from"]] if "from" in args else [])
            status, document, err = call(pty.cmd_observe, broker, argv)
        elif "id" in args:
            status, document, err = call(pty.cmd_journals, broker,
                                         ["show", os.path.join(state, "pty-journals"), args["id"], *snapshot])
        else:
            status, document, err = call(pty.cmd_journals, broker,
                                         ["list", os.path.join(state, "pty-journals"), "--json"])
    else:
        raise AssertionError(verb)
    if document is None:
        return failure(broker, status, err, "no_receipt")
    return status, document


def run_kill(broker, request, state):
    args = request["args"]
    status, document, err = call(pty.cmd_kill, broker, [args["id"], "--yes", "--json", "--expect-started",
                                                         str(args["expect_started_millis"])])
    if document is None:
        status, document = failure(broker, status, err, "no_receipt", id=args["id"], request_sent=False)
    return status, document


def refusal_document(broker, error: Refusal) -> dict:
    return broker.envelope(result="refused", reason=error.reason, message=error.message[:300],
                           hint=error.hint[:600])


def sent(document) -> bool:
    return document.get("request_sent") is True


WAIT_FOR_OWNER = 30.0     # seconds a caller waits for another process dispatching the same operation


def interrupted(broker, request, operation_id):
    return broker.envelope(
        result="uncertain", id=request["args"]["id"], request_sent=True, reason="interrupted",
        message="an earlier attempt with this operation_id began and did not finish, so the terminate request "
                "may or may not have been sent; re-list before retrying, with a new operation_id",
        operation_id=operation_id)


def reserve(broker, request, store, key):
    """Take the operation, or return the receipt to replay.

    Returns (owner_fd, None) when this process now owns it, with a durable `intent` on disk
    before anything is done; or (None, (status, receipt)) for a replay or a refusal.
    """
    operation_id = request["operation_id"]
    deadline = time.monotonic() + WAIT_FOR_OWNER
    while True:
        with store.locked():
            record = store.read(operation_id)
            if record is None:
                owner = store.owner_lock(operation_id)
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)      # nobody can hold it: no record
                store.write(operation_id, {"fingerprint": key, "phase": "intent", "operation_id": operation_id,
                                           "receipt": None, "pid": os.getpid(),
                                           "updated_millis": int(time.time() * 1000)})
                return owner, None
            if record.get("fingerprint") != key:
                raise Refusal("operation_id_reused", f"operation_id {operation_id!r} was used for a different request",
                              "use a new operation_id for different arguments", 3)
            receipt = record.get("receipt")
            if record.get("phase") == "done" and isinstance(receipt, dict):
                return None, (pty_exit(receipt), {**receipt, "duplicate": True})
            probe = store.owner_lock(operation_id)
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass                # a live process owns it: wait for it below
            else:               # an intent that no process owns: its dispatcher died
                gone = {**interrupted(broker, request, operation_id)}
                store.write(operation_id, {**record, "phase": "done", "receipt": gone,
                                           "updated_millis": int(time.time() * 1000)})
                return None, (1, {**gone, "duplicate": True})
            finally:
                os.close(probe)
        # Another process is dispatching this very operation: let it finish, then replay its receipt.
        waiting = store.owner_lock(operation_id)
        try:
            while time.monotonic() < deadline:
                try:
                    fcntl.flock(waiting, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.05)
            else:
                return None, (1, {**broker.envelope(
                    result="uncertain", id=request["args"]["id"], request_sent=True, reason="in_progress",
                    message="another call with this operation_id is still running; ask again with the same "
                            "operation_id to get its receipt", operation_id=operation_id), "duplicate": True})
        finally:
            os.close(waiting)


def execute(broker, request, yes, state):
    if request["verb"] != "kill":
        return run_verb(broker, request, state)
    if not yes:
        raise Refusal("consent_required", "kill needs consent, and a request cannot carry its own",
                      heredoc("kill", yes=True), 3)
    if not os.environ.get("KITTY_PTY_BROKER_SESSION"):
        raise Refusal("caller_unidentified", "KITTY_PTY_BROKER_SESSION is not set, so this cannot tell "
                      "whether the target is your own session; kill refuses",
                      "run it from a Kilix pane; if you cannot, stop and report it: a person can end the session "
                      "with kilix pty kill ID --yes", 3)
    operation_id, store, key = request["operation_id"], Store(state), fingerprint(request)
    try:
        owner, replay = reserve(broker, request, store, key)
    except OSError as error:
        raise Refusal("store_unavailable", f"the operation store cannot be used: {error.strerror or error}",
                      "fix the state directory's permissions; a person can end the session with "
                      "kilix pty kill ID --yes", 3)
    if replay is not None:
        return replay
    try:
        # The intent is on disk before this runs; a crash from here on replays as `interrupted`.
        status, document = run_kill(broker, request, state)
        document = {**document, "operation_id": operation_id, "duplicate": False}
        with store.locked():
            if sent(document):
                store.write(operation_id, {"fingerprint": key, "phase": "done", "operation_id": operation_id,
                                           "receipt": document, "pid": os.getpid(),
                                           "updated_millis": int(time.time() * 1000)})
            else:
                store.forget(operation_id)     # nothing was sent: the same id may be tried again
    finally:
        os.close(owner)                        # releases the per-operation lock
    return status, document


def pty_exit(receipt: dict) -> int:
    return {"verified_absent": 0, "uncertain": 1, "refused": 3, "not_found": 4}.get(receipt.get("result"), 1)


def cmd_capabilities(broker, argv):
    if argv not in ([], ["--json"]):
        print(json.dumps(broker.envelope(result="refused", reason="usage",
                                         message="usage: kilix pty capabilities [--json]",
                                         hint="kilix pty capabilities --json"), separators=(",", ":")))
        return EXIT_BAD_REQUEST
    print(json.dumps(capabilities_document(broker), separators=(",", ":")))
    return 0


def cmd_request(broker, argv):
    yes, source, rest = False, None, list(argv)
    while rest:
        flag = rest.pop(0)
        if flag == "--yes":
            yes = True
        elif flag == "--request-json" and rest:
            source = rest.pop(0)
        else:
            source = None
            break
    state = os.environ.get("KILIX_STATE_DIRECTORY", "")
    try:
        if source is None or not state:
            raise Refusal("usage", "usage: kilix pty request [--yes] --request-json -|FILE", heredoc())
        request = validate(read_request(source))
        if "timeout_seconds" in request:
            broker.timeout = format(request["timeout_seconds"], "g")
        status, document = execute(broker, request, yes, state)
    except Refusal as error:
        status, document = error.code, refusal_document(broker, error)
    print(json.dumps(document, ensure_ascii=True, separators=(",", ":")))
    return status
