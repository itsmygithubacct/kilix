"""Selector request v2 and receipt v1. Matching lives ONLY in the broker.
Retained keys bind the original selector and captured caller/runtime, and never
resolve again after admission. Separate from the unchanged v1 operation store.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
import sys
import time

import kilix_pty as pty
import kilix_pty_request as legacy
import kilix_pty_selector_validation as validation

REQUEST_SCHEMA = "kilix.pty.request/v2"
SCHEMA = "kilix.pty.selector/v1"
STORE_NAME = "pty-selector-operations-v1"
MAX_SELECTOR_JSON = 8192
PHASES = {"reserved", "resolved", "dispatch", "done"}
RESULT_CODES = {"unique": 0, "unresolved": 0, "not_found": 0, "error": 1,
                "ambiguous": 3, "refused": 3, "verified_absent": 0, "uncertain": 1}


def refuse(reason, message, code=3):
    raise legacy.Refusal(reason, message, "Inspect the result; retry a retained operation with the same operation_id.", code)


def valid_id(value):
    return isinstance(value, str) and bool(pty.SESSION_ID.fullmatch(value)) and value not in (".", "..")


def validate_selector(value):
    if not isinstance(value, dict) or set(value) - {"id", "id_prefix", "startup_words", "attached"}:
        refuse("invalid_selector", "selector accepts exactly id, id_prefix or startup_words, plus optional attached", 2)
    discriminators = set(value) & {"id", "id_prefix", "startup_words"}
    if len(discriminators) != 1:
        refuse("invalid_selector", "supply exactly one literal selector discriminator", 2)
    if "attached" in value and type(value["attached"]) is not bool:
        refuse("invalid_selector", "attached must be a boolean", 2)
    name = next(iter(discriminators))
    if name != "startup_words":
        if not (isinstance(value[name], str) and pty.SESSION_ID.fullmatch(value[name])
                and (name == "id_prefix" or value[name] not in (".", ".."))):
            refuse("invalid_selector", "IDs and prefixes are 1-64 ASCII letters, digits, dot, underscore or hyphen; not . or ..", 2)
    else:
        words = value[name]
        if not isinstance(words, list) or not 1 <= len(words) <= 64:
            refuse("invalid_selector", "startup_words is a nonempty array of at most 64 literal words", 2)
        try:
            size = len(words) - 1
            for word in words:
                if not isinstance(word, str) or not word or any(ord(c) < 33 or ord(c) == 127 for c in word):
                    refuse("invalid_selector", "each startup word is nonempty and contains no NUL or ASCII whitespace", 2)
                size += len(word.encode("utf-8"))
            if size > 4096:
                refuse("invalid_selector", "startup words exceed 4096 UTF-8 bytes including separators", 2)
        except UnicodeError:
            refuse("invalid_selector", "startup words must be valid Unicode", 2)
    if len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()) > MAX_SELECTOR_JSON:
        refuse("invalid_selector", "selector exceeds 8192 bytes of encoded JSON", 2)
    return value


def validate(request):
    if not isinstance(request, dict) or set(request) - {"schema", "verb", "selector", "operation_id", "timeout_seconds"}:
        refuse("unknown_field", "selector request v2 has schema, verb, selector, operation_id and timeout_seconds only", 2)
    if request.get("schema") != REQUEST_SCHEMA or request.get("verb") not in ("resolve", "end"):
        refuse("bad_schema", "selector request requires kilix.pty.request/v2 and verb resolve or end", 2)
    validate_selector(request.get("selector"))
    if request["verb"] == "end":
        if not valid_id(request.get("operation_id")):
            refuse("operation_id_required", "end requires an operation_id of 1-64 ID characters (not . or ..)", 2)
    elif "operation_id" in request:
        refuse("unknown_field", "a resolve preview does not take an operation_id", 2)
    if "timeout_seconds" in request:
        legacy.number(request["timeout_seconds"], "timeout_seconds", "seconds", 0.1, 60, example=2)
    return request


def selector_argv(selector):
    if "id" in selector:
        args = ["--id", selector["id"]]
    elif "id_prefix" in selector:
        args = ["--id-prefix", selector["id_prefix"]]
    else:
        args = [part for word in selector["startup_words"] for part in ("--command-word", word)]
    if "attached" in selector:
        args.append("--attached" if selector["attached"] else "--detached")
    return args


def runtime_identity(broker):
    path = os.path.realpath(broker.runtime)
    try:
        info = os.lstat(path)
        identity = [info.st_dev, info.st_ino] if stat.S_ISDIR(info.st_mode) else None
    except OSError:
        identity = None
    return {"path": path, "directory_identity": identity}


def capture(broker):
    broker.environment = os.environ.copy()
    broker.caller = pty.caller_context(broker.environment)
    return {"caller": broker.caller, "runtime": runtime_identity(broker),
            "timeout_seconds": broker.timeout_seconds}


def receipt(broker, request, context, result, *, reason=None, sent=False, rejected=False,
            resolved=None, census=None, completion=None, **extra):
    return {**broker.envelope(), "schema": SCHEMA, "operation": request["verb"],
            "selector": request["selector"], "caller": context["caller"],
            "runtime_context": context["runtime"], "result": result, "reason": reason,
            "request_sent": sent, "receiver_refused": rejected,
            "completion": completion or ("read" if request["verb"] == "resolve" else
                                          "receiver_refused" if rejected else "not_sent" if sent is False else "unknown"),
            "resolved": resolved, "census": census,
            **({"operation_id": request["operation_id"], "duplicate": False} if request["verb"] == "end" else {}),
            **extra}


def broker_document(broker, request, verb, binding=None):
    args = [verb, *selector_argv(request["selector"]), "--json"]
    if binding is not None:
        args += ["--resolved-id", binding["id"], "--expect-started", str(binding["started_millis"])]
    code, out, err = broker.call(*args)
    if len(out) > 65536:
        return None, code, "broker response exceeded 64 KiB"
    try:
        doc = json.loads(out, object_pairs_hook=legacy.unique_object)
        validation.broker(request, doc, verb, code, broker.caller, binding)
    except (ValueError, UnicodeError, RecursionError, legacy.Refusal):
        return None, code, "selector broker returned invalid or contradictory data"
    return doc, code, err[:300]


def census_of(doc):
    return {key: doc[key] for key in ("complete", "census_count", "matches", "possible",
                                     "candidates", "candidates_truncated")}


class Store(legacy.Store):
    """Reviewed owner-lock and bounded retention policy, in a new namespace."""
    def __init__(self, state):
        self.directory = os.path.join(state, STORE_NAME)

    @contextlib.contextmanager
    def locked(self):
        fd = self.open()
        deadline = time.monotonic() + legacy.WAIT_FOR_OWNER
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise OSError("selector store lock timed out")
                    time.sleep(0.05)
            yield
        finally:
            os.close(fd)

    def read(self, operation_id):
        return self.read_path(self.path(operation_id), operation_id)

    def read_path(self, path, operation_id=None):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                    or not 0 < info.st_size <= 65536):
                raise ValueError("record is not a bounded private regular file")
            raw = stream.read(65537)
            if len(raw) != info.st_size or len(raw) > 65536:
                raise ValueError("record changed size or exceeds its bound")
            try:
                record = json.loads(raw, object_pairs_hook=legacy.unique_object)
                key = record.get("operation_id") if isinstance(record, dict) else None
                if not valid_id(key) or (operation_id is not None and key != operation_id) or path != self.path(key):
                    raise ValueError("record does not bind its file/key")
                validation.record(record, key, validate, key_for)
            except (ValueError, UnicodeError, RecursionError, legacy.Refusal) as error:
                raise ValueError("malformed retained selector history") from error
        return record

    def prunable(self, name, now):
        try:
            record = self.read_path(os.path.join(self.directory, name))
            if record is None or record["phase"] != "done":
                return False
            protection = legacy.RECENT_RECEIPT if record["receipt"]["result"] == "verified_absent" else legacy.UNCERTAIN_TTL
            return now - os.path.getmtime(self.path(record["operation_id"])) > protection
        except (OSError, ValueError):
            return False

    def write(self, operation_id, record):
        if len(json.dumps(record, separators=(",", ":")).encode()) > 65536:
            raise OSError("selector operation record exceeds 64 KiB")
        super().write(operation_id, record)

    def make_room(self):
        try:
            super().make_room()
        except legacy.Refusal:
            refuse("store_full", "256 retained operations are protected; no selector request was sent")


def key_for(request, context):
    value = {"request": {k: v for k, v in request.items() if k != "operation_id"}, "context": context}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def interrupted(broker, request, context, record):
    may_send = record["phase"] == "dispatch"
    return receipt(broker, request, context, "uncertain" if may_send else "refused",
                   reason="interrupted" if may_send else "interrupted_before_dispatch", sent=may_send,
                   resolved=record.get("binding"), receipt_durable=True,
                   message="The earlier owner stopped. This key will not resolve or send again.")


def reserve(broker, request, context, store, key):
    operation_id = request["operation_id"]
    deadline = time.monotonic() + legacy.WAIT_FOR_OWNER
    seen, held = False, None
    try:
        while True:
            with store.locked():
                record = store.read(operation_id)
                if record is None and seen:
                    return None, None, receipt(broker, request, context, "uncertain", reason="receipt_evicted",
                                               sent=None, duplicate=True)
                if record is None:
                    store.make_room()
                    owner = store.owner_lock(operation_id)
                    try:
                        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        record = {"namespace": SCHEMA, "phase": "reserved", "operation_id": operation_id,
                                  "fingerprint": key, "request": request, "context": context, "binding": None,
                                  "receipt": None, "updated_millis": int(time.time() * 1000)}
                        store.write(operation_id, record)
                    except BaseException:
                        os.close(owner)
                        raise
                    return owner, record, None
                seen = True
                if record["fingerprint"] != key:
                    refuse("operation_id_reused", "This retained key belongs to another selector or captured caller/runtime")
                if record["phase"] == "done":
                    return None, None, {**record["receipt"], "duplicate": True}
                unowned = held is not None and store.lock_is_current(operation_id, held)
                if held is not None and not unowned:
                    os.close(held); held = None
                if not unowned:
                    probe = store.owner_lock(operation_id)
                    try:
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        unowned = True
                    except BlockingIOError:
                        pass
                    finally:
                        os.close(probe)
                if unowned:
                    done = interrupted(broker, request, context, record)
                    store.write(operation_id, {**record, "phase": "done", "receipt": done})
                    return None, None, {**done, "duplicate": True}
            waiting = store.owner_lock(operation_id)
            try:
                while time.monotonic() < deadline:
                    try:
                        fcntl.flock(waiting, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        held = waiting
                        break
                    except BlockingIOError:
                        time.sleep(0.05)
                else:
                    return None, None, receipt(broker, request, context, "uncertain", reason="in_progress",
                                               sent=None, duplicate=True)
            finally:
                if held is not waiting:
                    os.close(waiting)
    finally:
        if held is not None:
            os.close(held)


def save_phase(store, record, phase, **fields):
    updated = {**record, **fields, "phase": phase, "updated_millis": int(time.time() * 1000)}
    with store.locked():
        existing = store.read(record["operation_id"])
        if existing is None or existing["fingerprint"] != record["fingerprint"] or existing["phase"] != record["phase"]:
            raise OSError("operation binding changed")
        store.write(record["operation_id"], updated)
    return updated


def process_absent(pid, ticks):
    if type(pid) is not int or pid <= 0 or type(ticks) is not int or ticks < 0:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stream:
            fields = stream.read(4096).rsplit(")", 1)[1].split()
        return fields[0] == "Z" or int(fields[19]) != ticks
    except FileNotFoundError:
        return True
    except (OSError, ValueError, IndexError):
        return False


def verify_absence(binding):
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as stream:
            boot = stream.read(64).strip()
    except OSError:
        return False
    if not isinstance(binding.get("boot_id"), str):
        return False
    if boot != binding["boot_id"]:
        return True
    return (process_absent(binding.get("broker_pid"), binding.get("start_ticks"))
            and process_absent(binding.get("child_pid"), binding.get("child_start_ticks")))


def perform(broker, request, context, store, record, yes):
    if not yes:
        return receipt(broker, request, context, "refused", reason="consent_required")
    if context["caller"]["status"] != "valid":
        return receipt(broker, request, context, "refused", reason="caller_" + context["caller"]["status"])
    doc, code, _ = broker_document(broker, request, "resolve")
    if doc is None or code != 0:
        return receipt(broker, request, context, "refused", reason="census_unavailable")
    census, binding = census_of(doc), doc.get("resolved")
    if not doc["complete"] or not doc["actionable"]:
        return receipt(broker, request, context, "ambiguous" if doc["matches"] + doc["possible"] > 1 else "refused",
                       reason=doc["result"], census=census)
    if binding["id"] == context["caller"]["id"]:
        return receipt(broker, request, context, "refused", reason="own_session", resolved=binding, census=census)
    record = save_phase(store, record, "resolved", binding=binding)
    if runtime_identity(broker) != context["runtime"]:
        return receipt(broker, request, context, "refused", reason="runtime_changed", resolved=binding, census=census)
    # This fsync'd phase precedes the only possible mutation. On any crash it is
    # retained as uncertain, never a request to resolve or dispatch another target.
    record = save_phase(store, record, "dispatch")
    doc, code, _ = broker_document(broker, request, "end", binding)
    if doc is None:
        return receipt(broker, request, context, "uncertain", reason="missing_end_receipt",
                       sent=True, resolved=binding, census=census)
    if doc["receiver_refused"]:
        return receipt(broker, request, context, "refused", reason=doc["reason"], sent=doc["request_sent"],
                       rejected=True, resolved=binding, census=census_of(doc))
    if not doc["request_sent"]:
        return receipt(broker, request, context, "refused", reason=doc["reason"],
                       resolved=binding, census=census_of(doc))
    if code != 0 or doc["result"] != "accepted":
        return receipt(broker, request, context, "uncertain", reason=doc["reason"], sent=True,
                       resolved=binding, census=census_of(doc))
    deadline = time.monotonic() + pty.KILL_GRACE + pty.VERIFY_SLACK
    while time.monotonic() < deadline:
        if verify_absence(binding):
            return receipt(broker, request, context, "verified_absent", sent=True, resolved=binding,
                           census=census_of(doc), completion="verified_absent")
        time.sleep(0.05)
    return receipt(broker, request, context, "uncertain", reason="absence_unverified", sent=True,
                   resolved=binding, census=census_of(doc))


def execute(broker, request, yes, state):
    request = validate(request)
    if "timeout_seconds" in request:
        broker.timeout = format(request["timeout_seconds"], "g")
    # Bound captured references before admission, including error receipts.
    # Refuse oversized context rather than writing an unreadable retry record.
    if len(json.dumps(broker.runtime, ensure_ascii=True).encode()) > 8192:
        context = {"caller": pty.caller_context(), "runtime": None}
        return (1 if request["verb"] == "resolve" else 3), receipt(
            broker, request, context, "error" if request["verb"] == "resolve" else "refused",
            reason="runtime_reference_too_large", runtime="", runtime_unavailable=True)
    context = capture(broker)
    if len(json.dumps(context, ensure_ascii=True).encode()) > 8192:
        context = {"caller": context["caller"], "runtime": None}
        return (1 if request["verb"] == "resolve" else 3), receipt(
            broker, request, context, "error" if request["verb"] == "resolve" else "refused",
            reason="runtime_context_too_large", runtime="", runtime_unavailable=True)
    if request["verb"] == "resolve":
        doc, code, _ = broker_document(broker, request, "resolve")
        if doc is None:
            return 1, receipt(broker, request, context, "error", reason="census_unavailable")
        result = receipt(broker, request, context, doc["result"], reason=doc["reason"],
                         resolved=doc.get("resolved"), census=census_of(doc))
        # Ambiguity is a successful read, not an end refusal.
        return code if code else 0, result
    store, key, owner, record = Store(state), key_for(request, context), None, None
    try:
        owner, record, replay = reserve(broker, request, context, store, key)
        if replay is not None:
            return RESULT_CODES.get(replay["result"], 1), replay
        result = perform(broker, request, context, store, record, yes)
        with store.locked():
            current = store.read(request["operation_id"])
            if current is None or current["fingerprint"] != key:
                raise OSError("operation record disappeared")
            store.write(request["operation_id"], {**current, "phase": "done", "receipt": result,
                                                  "updated_millis": int(time.time() * 1000)})
        return RESULT_CODES[result["result"]], result
    except legacy.Refusal as error:
        return error.code, receipt(broker, request, context, "refused", reason=error.reason, message=error.message[:300])
    except Exception as error:
        # No fallback. Ownership and readable retained history determine what
        # is known; an unexpected parser/store error is not dispatch evidence.
        if owner is None:
            # An inaccessible/corrupt store may already contain this key's
            # dispatch. No send in THIS attempt does not prove an unsent operation.
            return 1, receipt(broker, request, context, "uncertain", reason="store_unavailable",
                              sent=None, attempt_sent=False, receipt_durable=False,
                              message="Prior key history could not be established. This attempt sent nothing; no fallback.")
        sent, binding = True, None
        try:
            with store.locked():
                current = store.read(request["operation_id"])
                if current is not None and current["fingerprint"] == key:
                    binding = current["binding"]
                    sent = (current["receipt"]["request_sent"] if current["phase"] == "done"
                            else current["phase"] == "dispatch")
        except Exception:
            # This owner may have progressed beyond its original reservation.
            # Missing/unreadable current history cannot prove an unsent phase.
            pass
        reason = ("store_unavailable" if isinstance(error, (OSError, ValueError)) else
                  "interrupted_before_dispatch" if sent is False else "interrupted")
        return (3 if sent is False else 1), receipt(
            broker, request, context, "refused" if sent is False else "uncertain", reason=reason,
            sent=sent, resolved=binding, receipt_durable=False,
            message="The selector attempt failed. No fallback or new resolution is permitted for this retained key.")
    finally:
        if owner is not None:
            os.close(owner)


def parse_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=legacy.unique_object)
    except (ValueError, UnicodeError, RecursionError):
        refuse("not_json", "JSON must be valid and contain no duplicate fields", 2)


def refusal_document(broker, verb, error, selector=None, operation_id=None):
    """Bound invalid input echoes even before execute captures/validates context."""
    document = broker.envelope(schema=SCHEMA, operation=verb, result="refused", reason=error.reason,
                               selector=selector, operation_id=operation_id,
                               request_sent=False, receiver_refused=False, completion="not_sent",
                               resolved=None, census=None, message=error.message[:300])
    if len(json.dumps(document["runtime"], ensure_ascii=True).encode()) > MAX_SELECTOR_JSON:
        document.update(runtime="", runtime_unavailable=True)
    if len(json.dumps(selector, ensure_ascii=True, separators=(",", ":")).encode()) > MAX_SELECTOR_JSON:
        document.update(selector=None, selector_unavailable=True)
    if operation_id is not None and not valid_id(operation_id):
        document.update(operation_id=None, operation_id_unavailable=True)
    return document


def command(broker, verb, argv):
    request = {"schema": REQUEST_SCHEMA, "verb": verb}
    yes, source = False, None
    rest = list(argv)
    try:
        while rest:
            flag = rest.pop(0)
            if flag == "--yes" and verb == "end" and not yes:
                yes = True
            elif flag == "--json":
                pass
            elif flag == "--selector-json" and rest and source is None:
                source = rest.pop(0)
            elif flag == "--operation-id" and rest and "operation_id" not in request and verb == "end":
                request["operation_id"] = rest.pop(0)
            else:
                refuse("usage", "resolve|end --selector-json JSON|-|FILE [--operation-id KEY --yes] [--json]", 2)
        if source is None:
            refuse("usage", "--selector-json is required", 2)
        try:
            raw = source.encode() if source.startswith("{") else legacy.read_request(source)
        except UnicodeError:
            refuse("not_json", "selector input must be valid Unicode", 2)
        if len(raw) > legacy.MAX_REQUEST:
            refuse("request_too_large", "selector input exceeds 16 KiB", 2)
        request["selector"] = parse_json(raw)
        validate(request)
        state = os.environ.get("KILIX_STATE_DIRECTORY", "")
        if verb == "end" and not state:
            refuse("store_unavailable", "KILIX_STATE_DIRECTORY must identify the private state directory")
        code, result = execute(broker, request, yes, state)
    except legacy.Refusal as error:
        code = error.code
        result = refusal_document(broker, verb, error, request.get("selector"), request.get("operation_id"))
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return code
