"""Versioned, explicit pane actions with private durable at-most-once receipts.

An observed pane creation or screen submission never establishes agent startup,
acknowledgment or task completion. Uncertain operations are never replayed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
from types import SimpleNamespace

import agent_control as control
from agent_delivery import Ledger, MESSAGE_ID, MAX_WIRE, composer, read_payload
from agent_skills import Conflict, directory

SCHEMA = "kilix.actions/v1"
MAX_REQUEST = 16384
ACTION_OPERATIONS = {
    "pane.open": {"required": ["argv", "cwd", "title", "placement"],
                  "optional": ["direction", "bias"], "mutation": True},
    "agent.launch": {"required": ["agent", "cwd", "title", "placement"],
                     "optional": ["direction", "bias", "model", "prompt", "resume",
                                  "coding_yolo", "trust_folder", "agent_arg"], "mutation": True,
                     "unsupported": {"trust_folder": True}},
    "agent.deliver": {"required": ["text"], "optional": ["mode"], "mutation": True},
    "operation.status": {"required": [], "optional": [], "mutation": False},
}
ACTION_REQUEST = {"required": ["schema", "operation_id", "operation", "source", "target", "params"],
                  "optional": ["timeout", "dry_run"],
                  "identity": {"required": ["pane_id", "broker"]},
                  "timeout_seconds": [1, 60]}


def capabilities():
    return {"schema": SCHEMA, "operations": json.loads(json.dumps(ACTION_OPERATIONS)),
            "request": json.loads(json.dumps(ACTION_REQUEST)),
            "agents": list(control.AGENTS), "delivery_agents": ["codex"],
            "folder_trust_supported": False,
            "acknowledgment_supported": False, "completion_supported": False}


def fields(value, required, optional=(), label="object"):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise control.ControlError(f"invalid {label} fields")


def identity(value):
    fields(value, ("pane_id", "broker"), label="identity")
    if type(value["pane_id"]) is not int or value["pane_id"] <= 0:
        raise control.ControlError("pane_id must be a positive integer")
    if not isinstance(value["broker"], str) or not control.BROKER.fullmatch(value["broker"]):
        raise control.ControlError("broker must be an exact broker identity")


def text(value, label, limit=1024):
    if not isinstance(value, str):
        raise control.ControlError(f"{label} must be text")
    return control.plain_text(value, label, limit)


def validate(request):
    """Pure whole-request validation; no connection or mutation is attempted."""
    fields(request, ("schema", "operation_id", "operation", "source", "target", "params"),
           ("timeout", "dry_run"), "request")
    if request["schema"] != SCHEMA:
        raise control.ControlError("unsupported action schema")
    if not isinstance(request["operation_id"], str) or not MESSAGE_ID.fullmatch(request["operation_id"]):
        raise control.ControlError("operation_id must be 1–64 ASCII ID characters")
    operation = request["operation"]
    if not isinstance(operation, str) or operation not in ACTION_OPERATIONS:
        raise control.ControlError("unsupported operation")
    identity(request["source"])
    identity(request["target"])
    timeout = request.get("timeout", 15)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 60:
        raise control.ControlError("timeout must be between 1 and 60 seconds")
    if type(request.get("dry_run", False)) is not bool:
        raise control.ControlError("dry_run must be boolean")
    if operation == "operation.status" and request.get("dry_run", False):
        raise control.ControlError("operation.status is already read-only; dry_run does not apply")
    spec, params = ACTION_OPERATIONS[operation], request["params"]
    fields(params, spec["required"], spec["optional"], "params")
    if operation in ("pane.open", "agent.launch"):
        text(params["cwd"], "cwd", 4096)
        if not Path(params["cwd"]).is_absolute():
            raise control.ControlError("cwd must be absolute")
        text(params["title"], "title", 200)
        if params["placement"] not in ("split", "new-tab"):
            raise control.ControlError("placement must be split or new-tab")
        if params["placement"] == "new-tab" and ("direction" in params or "bias" in params):
            raise control.ControlError("direction and bias apply only to splits")
        if not isinstance(params.get("direction", "right"), str) or params.get("direction", "right") not in control.LOCATIONS:
            raise control.ControlError("invalid split direction")
        bias = params.get("bias", 50)
        if type(bias) not in (int, float) or not math.isfinite(bias) or not 0 < bias < 100:
            raise control.ControlError("bias must be greater than 0 and less than 100")
    if operation == "pane.open":
        argv = params["argv"]
        if not isinstance(argv, list) or not 1 <= len(argv) <= 64:
            raise control.ControlError("argv must contain 1–64 literal arguments")
        for item in argv:
            if not isinstance(item, str) or "\0" in item:
                raise control.ControlError("argv must contain text without NUL")
        if not argv[0] or argv[0].startswith("-") or sum(len(v.encode()) for v in argv) > 4096:
            raise control.ControlError("argv requires an executable and at most 4096 UTF-8 bytes")
    if operation == "agent.launch":
        if not isinstance(params["agent"], str) or params["agent"] not in control.AGENTS:
            raise control.ControlError("unsupported agent")
        for name in ("model", "prompt", "resume"):
            if name in params:
                text(params[name], name, 200 if name == "model" else 1024)
        if "resume" in params and not control.SESSION_ID.fullmatch(params["resume"]):
            raise control.ControlError("invalid resume session ID")
        for name in ("coding_yolo", "trust_folder"):
            if type(params.get(name, False)) is not bool:
                raise control.ControlError(f"{name} must be boolean")
        if params.get("trust_folder", False):
            raise control.ControlError("structured launch cannot grant folder trust; use explicit existing agent-control folder trust setup first")
        extras = params.get("agent_arg", [])
        if not isinstance(extras, list) or len(extras) > 16:
            raise control.ControlError("agent_arg must contain at most 16 arguments")
        for item in extras:
            text(item, "agent_arg", 200)
            if not any(p.fullmatch(item) for p in control.AGENT_ARG_PATTERNS[params["agent"]]):
                raise control.ControlError("agent_arg is not allowed for this agent")
    if operation == "agent.deliver":
        if params.get("mode", "steer") not in ("steer", "defer"):
            raise control.ControlError("mode must be steer or defer")
        text(params["text"], "message", MAX_WIRE)
        value = read_payload(SimpleNamespace(file=None, text=params["text"]))
        text(f"[kilix-message:{request['operation_id']}] " + value, "message with ID", MAX_WIRE)
    # Bound inputs before hashing or touching private storage; reject non-JSON values.
    encoded = json.dumps(request, ensure_ascii=True, allow_nan=False)
    if len(encoded.encode()) > MAX_REQUEST:
        raise control.ControlError("request exceeds 16 KiB")
    result = json.loads(encoded)
    result.setdefault("timeout", 15)
    result.setdefault("dry_run", False)
    return result


def receipt(request, status="blocked"):
    def safe_identity(value):
        try:
            identity(value)
            return dict(value)
        except (control.ControlError, TypeError):
            return None
    operation_id = request.get("operation_id")
    if not isinstance(operation_id, str) or not MESSAGE_ID.fullmatch(operation_id):
        operation_id = None
    operation = request.get("operation")
    if not isinstance(operation, str) or operation not in ACTION_OPERATIONS:
        operation = None
    return {"schema": SCHEMA, "operation_id": operation_id,
            "operation": operation, "source": safe_identity(request.get("source")),
            "target": safe_identity(request.get("target")), "status": status,
            "dry_run": request.get("dry_run") is True, "duplicate": False,
            "identity_verified": False, "pane_created_verified": False,
            "delivery_verified": False, "agent_startup_verified": False,
            "acknowledgment_verified": False, "completion_verified": False}


@contextmanager
def ledger(client, operation_id, *, create=True):
    root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    socket = client.command[client.command.index("--to") + 1]
    scope = hashlib.sha256(socket.encode()).hexdigest()
    name = scope + "-" + hashlib.sha256(operation_id.encode()).hexdigest() + ".json"
    try:
        with directory(root / "kilix/agent-actions", create=create) as fd:
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                raise control.ControlError("action state directory must be private (0700)")
            if not create:
                # Atomic replacement means a status reader sees one complete receipt.
                # It must remain available while a mutation owns the operation lock.
                yield Ledger(fd, name)
                return
            lock = os.open(name + ".lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK |
                           os.O_CREAT, 0o600, dir_fd=fd)
            try:
                info = os.fstat(lock)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                        info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                    raise control.ControlError("action lock must be a private owned regular file")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise control.ControlError("this operation is active; inspect status with the same operation ID") from exc
                yield Ledger(fd, name)
            finally:
                os.close(lock)
    except Conflict as exc:
        raise control.ControlError(str(exc)) from exc


class GuardedClient:
    """Recheck both identities immediately before terminal side effects."""
    def __init__(self, client, request, before_mutation):
        self.client, self.request, self.before_mutation = client, request, before_mutation
        self.caller = client.caller
        self.command = client.command
        self.deadline = min(getattr(client, "deadline", float("inf")), time.monotonic() + request["timeout"])

    def verify(self, *, target=True):
        source = self.request["source"]
        if self.caller != source["pane_id"]:
            raise control.ControlError("source conflicts with inherited caller identity")
        self.resolve(source["pane_id"], source["broker"])
        if target:
            dest = self.request["target"]
            return self.resolve(dest["pane_id"], dest["broker"])

    def bounded(self, function, *args, **kwargs):
        if time.monotonic() >= self.deadline:
            raise control.ControlError("action deadline expired; inspect before retrying")
        previous = getattr(self.client, "deadline", float("inf"))
        try:
            self.client.deadline = self.deadline
            return function(*args, **kwargs)
        finally:
            self.client.deadline = previous

    def snapshot(self):
        return self.bounded(self.client.snapshot)

    def resolve(self, *args, **kwargs):
        return self.bounded(self.client.resolve, *args, **kwargs)

    def run(self, args, payload=None):
        mutation = args[0] == "send-text" or (args[0] == "launch" and "--help" not in args)
        if mutation:
            self.verify()
            self.before_mutation()
            if args[0] == "launch":
                args = list(args)
                args[args.index("--match") + 1] = "env:KITTY_PTY_BROKER_SESSION=" + self.request["target"]["broker"]
        return self.bounded(self.client.run, args, payload)

    def input(self, pane_id, expected, payload):
        return control.Client.input(self, pane_id, expected, payload)


def execute(client, request):
    params, operation = request["params"], request["operation"]
    args = SimpleNamespace(pane=request["target"]["pane_id"],
                           expect_broker=request["target"]["broker"], dry_run=request["dry_run"],
                           **params)
    if operation in ("pane.open", "agent.launch"):
        defaults = {"direction": "right", "bias": 50, "agent": None, "model": None,
                    "prompt": None, "resume": None, "coding_yolo": False,
                    "trust_folder": False, "agent_arg": []}
        for key, value in defaults.items():
            if not hasattr(args, key):
                setattr(args, key, value)
        args.action = params["placement"]
        result = control.launch(client, args, explicit_argv=params.get("argv"))
        if not request["dry_run"]:
            pane = result["pane"]
            client.resolve(pane["pane_id"], pane["broker"])
        return result
    args.message_id, args.timeout, args.file = request["operation_id"], request["timeout"], None
    args.mode = params.get("mode", "steer")
    if request["dry_run"]:
        target = client.resolve(args.pane, args.expect_broker, input_target=True)
        if control.describe(target)["agent"] != "codex":
            raise control.ControlError("verified delivery supports foreground Codex only")
        screen = composer(client.run(["get-text", "--match", f"env:KITTY_PTY_BROKER_SESSION={args.expect_broker}",
                                      "--extent", "screen", "--ansi"]))
        client.verify()
        if not screen["empty"]:
            raise control.ControlError("target composer is occupied")
        if args.mode == "defer" and "esc to interrupt" not in screen["screen"].lower():
            raise control.ControlError("defer requires a visibly working Codex session")
        return {"status": "dry_run", "verification": "codex-screen-v1"}
    from agent_delivery import deliver
    return deliver(client, args)


def finish(request, result):
    status = "planned" if request["dry_run"] else result["status"]
    out = {**receipt(request, status), "identity_verified": True}
    if status == "created":
        pane = result["pane"]
        if not control.BROKER.fullmatch(pane.get("broker", "")):
            raise control.ControlError("created pane has no verified broker identity; inspect before retrying")
        out["pane_created_verified"] = True
        out["evidence"] = {"pane": {key: pane[key] for key in ("pane_id", "broker", "tab_id", "os_window_id")},
                           "prompt_passed": result.get("prompt_passed", False)}
    elif status in ("submitted", "deferred"):
        out["delivery_verified"] = bool(result.get("delivery_verified"))
        if not out["delivery_verified"]:
            out["status"] = "uncertain"
        out["evidence"] = {"verification": result.get("verification"), "verified_at": result.get("verified_at")}
    if result.get("error"):
        out["error"] = result["error"][:240]
    return out


def validate_record(record, operation_id):
    if record is None:
        return
    fields(record, ("digest", "phase", "receipt"), label="stored action record")
    if (record["phase"] not in ("checked", "intent", "final") or
            not isinstance(record["digest"], str) or len(record["digest"]) != 64 or
            any(c not in "0123456789abcdef" for c in record["digest"])):
        raise control.ControlError("invalid stored action record")
    saved = record["receipt"]
    fields(saved, receipt({}).keys(), ("evidence", "error"), "stored receipt")
    identity(saved["source"])
    identity(saved["target"])
    if (saved["schema"] != SCHEMA or saved["operation_id"] != operation_id or
            saved["operation"] not in ("pane.open", "agent.launch", "agent.deliver") or
            saved["status"] not in ("created", "submitted", "deferred", "blocked", "uncertain") or
            any(type(saved[key]) is not bool for key in receipt({}) if key.endswith("verified") or key in ("dry_run", "duplicate")) or
            saved["dry_run"] or saved["acknowledgment_verified"] or saved["completion_verified"] or
            saved["agent_startup_verified"] or
            saved["pane_created_verified"] != (saved["status"] == "created") or
            saved["delivery_verified"] != (saved["status"] in ("submitted", "deferred")) or
            (saved["status"] == "created" and (not saved["pane_created_verified"] or not isinstance(saved.get("evidence"), dict))) or
            (saved["status"] in ("submitted", "deferred") and not saved["delivery_verified"]) or
            ("error" in saved and (not isinstance(saved["error"], str) or len(saved["error"]) > 240)) or
            len(json.dumps(saved).encode()) > 2048):
        raise control.ControlError("invalid stored action receipt")
    if saved["status"] == "created":
        evidence = saved["evidence"]
        fields(evidence, ("pane", "prompt_passed"), label="created pane evidence")
        pane = evidence["pane"]
        fields(pane, ("pane_id", "broker", "tab_id", "os_window_id"), label="created pane identity")
        identity({key: pane[key] for key in ("pane_id", "broker")})
        if (type(evidence["prompt_passed"]) is not bool or
                any(type(pane[key]) is not int or pane[key] <= 0 for key in ("tab_id", "os_window_id"))):
            raise control.ControlError("invalid created pane evidence")
    if saved["status"] in ("submitted", "deferred"):
        evidence = saved.get("evidence")
        fields(evidence, ("verification", "verified_at"), label="delivery evidence")
        if (evidence["verification"] != "codex-screen-v1" or
                type(evidence["verified_at"]) not in (int, float) or not math.isfinite(evidence["verified_at"])):
            raise control.ControlError("invalid delivery evidence")


def dispatch(request, client=None):
    """Run one bounded operation, or return a durable receipt without replay."""
    out, store, record, guarded = receipt(request if isinstance(request, dict) else {}), None, None, None
    try:
        request = validate(request)
        out = receipt(request)
        client = client or control.Client(request["source"]["pane_id"])
        guarded = GuardedClient(client, request, lambda: None)
        guarded.verify(target=request["operation"] != "operation.status")
        out["identity_verified"] = True
        if request["dry_run"] and request["operation"] != "operation.status":
            return finish(request, execute(guarded, request))
        with ledger(client, request["operation_id"], create=request["operation"] != "operation.status") as store:
            record = store.read()
            validate_record(record, request["operation_id"])
            if request["operation"] == "operation.status":
                if record is None:
                    return {**out, "status": "not_found"}
                if any(record["receipt"].get(k) != request[k] for k in ("source", "target")):
                    raise control.ControlError("operation ID belongs to a different source or target")
                saved = dict(record["receipt"])
                if record.get("phase") == "intent":
                    saved.update(status="uncertain", error="operation interrupted after intent; no action was repeated")
                elif record.get("phase") == "checked":
                    saved.update(status="pending", error="operation has no final receipt; no action was repeated")
                return saved
            digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if record is not None:
                if record.get("digest") != digest:
                    return {**out, "error": "operation ID already belongs to a different request"}
                saved = {**record["receipt"], "duplicate": True}
                if record.get("phase") == "intent":
                    saved.update(status="uncertain", error="operation interrupted after intent; no action was repeated")
                return saved
            record = {"digest": digest, "phase": "checked", "receipt": out}
            store.write(record)
            def before_mutation():
                record.update(phase="intent", receipt={**out, "status": "uncertain"})
                store.write(record)
            guarded.before_mutation = before_mutation
            try:
                out = finish(request, execute(guarded, request))
            except (control.ControlError, OSError, ValueError, TypeError, KeyError) as exc:
                out.update(status="uncertain" if record["phase"] == "intent" else "blocked", error=str(exc)[:240])
            record.update(phase="final", receipt=out)
            store.write(record)
            return out
    except FileNotFoundError as exc:
        if isinstance(request, dict) and request.get("operation") == "operation.status":
            return {**out, "status": "not_found"}
        out["error"] = str(exc)[:240]
    except (control.ControlError, OSError, ValueError, TypeError, KeyError) as exc:
        out["error"] = str(exc)[:240]
        if record is not None and record.get("phase") == "intent":
            out["status"] = "uncertain"
    return out


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise control.ControlError("duplicate JSON object field")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-json", choices=("-",))
    parser.add_argument("command", nargs="?", choices=("capabilities",))
    args = parser.parse_args(argv)
    if args.command == "capabilities" and not args.request_json:
        print(json.dumps(capabilities(), separators=(",", ":")))
        return 0
    if not args.request_json or args.command:
        parser.error("use --request-json - or capabilities")
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise control.ControlError("request exceeds 16 KiB")
        result = dispatch(json.loads(raw, object_pairs_hook=unique_object))
    except (control.ControlError, UnicodeError, ValueError) as exc:
        result = {**receipt({}), "error": str(exc)[:240]}
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result["status"] in ("planned", "created", "submitted", "deferred") else 1


if __name__ == "__main__":
    raise SystemExit(main())
