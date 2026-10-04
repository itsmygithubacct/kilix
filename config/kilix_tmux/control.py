"""Exact tmux targets, literal I/O and bounded capture.

Derived from tmux-cli lib/sessions.py and lib/targeting.py, MIT licensed.
See PROVENANCE.md and LICENSE for the source revision and copyright.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import subprocess

SCHEMA = "kilix.tmux/v1"
REQUEST_SCHEMA = "kilix.tmux.request/v1"
KEYS = frozenset("Enter Tab Escape BSpace Space Up Down Left Right Home End "
                 "PageUp PageDown Delete C-c C-d C-u C-a C-e C-l".split())
FIELDS = {
    "list": set(), "new": {"name", "cwd"}, "read": {"target", "lines"},
    "send": {"target", "text"}, "type": {"target", "text"},
    "key": {"target", "keys"}, "rename": {"target", "new_name"},
    "close": {"target"},
}
EXITS = {"EUSAGE": 2, "ENOENT": 3, "EEXIST": 4, "ETIMEDOUT": 5,
         "ENOSERVER": 6, "ETMUX": 7, "EAMBIGUOUS": 8}
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,127}\Z")
_SESSION_ID = re.compile(r"\$[0-9]+\Z")
_PANE_ID = re.compile(r"%[0-9]+\Z")
_INDEX_TARGET = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_-]{0,127}):(\d+)\.(\d+)\Z")
_NO_SERVER = ("no server running", "error connecting", "failed to connect to server")


class ControlError(Exception):
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code = code
        self.details = details


def _usage(message):
    raise ControlError("EUSAGE", message)


def _name(value, field):
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        _usage(f"{field} must be an exact session name: letters, digits, underscore or hyphen")


def _validate(request):
    if not isinstance(request, dict):
        _usage("request must be an object")
    op = request.get("operation")
    if not isinstance(op, str) or op not in FIELDS:
        _usage("operation must be list/new/read/send/type/key/rename/close")
    if set(request) - (FIELDS[op] | {"schema", "operation", "socket", "dry_run"}):
        _usage("request contains unsupported fields")
    if "schema" in request and request["schema"] != REQUEST_SCHEMA:
        _usage(f"request schema must be {REQUEST_SCHEMA}")
    socket = request.get("socket")
    if (not isinstance(socket, str) or not socket or not os.path.isabs(socket)
            or any(ord(c) < 32 or ord(c) == 127 for c in socket)):
        _usage("socket must be an explicit absolute path")
    if "dry_run" in request and type(request["dry_run"]) is not bool:
        _usage("dry_run must be a boolean")
    req = dict(request, dry_run=request.get("dry_run", False))
    if op == "new":
        _name(req.get("name"), "name")
        if "cwd" in req:
            cwd = req["cwd"]
            if (not isinstance(cwd, str) or not os.path.isabs(cwd)
                    or any(ord(c) < 32 or ord(c) == 127 for c in cwd)):
                _usage("cwd must be an existing absolute directory")
            if not Path(cwd).is_dir():
                if cwd == socket or Path(cwd).is_socket():
                    _usage("cwd is a socket path; use socket/--socket for the server. "
                           "Omit --cwd (or the cwd field) unless a working directory was requested")
                _usage("cwd must be an existing absolute directory")
    if op not in {"list", "new"}:
        target = req.get("target")
        if (not isinstance(target, str) or not (
                _NAME.fullmatch(target) or _SESSION_ID.fullmatch(target)
                or (op not in {"close", "rename"} and (
                    _PANE_ID.fullmatch(target) or _INDEX_TARGET.fullmatch(target))))):
            _usage("target must be an exact session name/ID or an explicit I/O pane target")
    if op == "read":
        lines = req.get("lines", 80)
        if type(lines) is not int or not 1 <= lines <= 2000:
            _usage("lines must be an integer from 1 to 2000")
        req["lines"] = lines
    if op in {"send", "type"}:
        value = req.get("text")
        if (not isinstance(value, str) or not value or len(value) > 65536
                or any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in value)):
            _usage("text must be nonempty literal text, at most 65536 characters, without control characters except tab")
    if op == "key":
        keys = req.get("keys")
        if (not isinstance(keys, list) or not 1 <= len(keys) <= 16
                or any(not isinstance(k, str) or k not in KEYS for k in keys)):
            _usage("keys must contain 1 to 16 allowed named keys")
    if op == "rename":
        _name(req.get("new_name"), "new_name")
    return req


def _run(socket, *args, allow_absent=False, input_text=None):
    # Never inherit a client context, and never choose a server from TMUX.
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        io = {"stdin": subprocess.DEVNULL} if input_text is None else {"input": input_text}
        result = subprocess.run(["tmux", "-S", socket, *args],
                                capture_output=True, text=True,
                                errors="replace", timeout=5,
                                env=env, **io)
    except FileNotFoundError:
        raise ControlError("ETMUX", "tmux executable not found") from None
    except subprocess.TimeoutExpired:
        raise ControlError("ETIMEDOUT", "tmux exceeded its 5 second deadline") from None
    except OSError as exc:
        raise ControlError("ETMUX", f"cannot invoke tmux: {exc}") from None
    if result.returncode:
        error = (result.stderr or result.stdout).strip()
        absent = any(marker in error.lower() for marker in _NO_SERVER)
        if absent and allow_absent:
            return None
        raise ControlError("ENOSERVER" if absent else "ETMUX", error or "tmux command failed")
    return result.stdout


def _send_literal(socket, target, text):
    # Derived from tmux-cli paste_buffer. tmux's command parser strips a
    # trailing semicolon even from send-keys -l argv. Stdin buffer loading
    # keeps payload bytes out of that parser. Never share the latest buffer.
    buffer = f"kilix-tmux-{os.getpid()}-{secrets.token_hex(16)}"
    _run(socket, "load-buffer", "-b", buffer, "-", input_text=text)
    try:
        _run(socket, "paste-buffer", "-d", "-b", buffer, "-t", target)
    except ControlError:
        try:
            _run(socket, "delete-buffer", "-b", buffer)
        except ControlError:
            pass
        raise


def _snapshot(socket):
    # Adapted from tmux-cli list_sessions/list_panes. IDs are authoritative;
    # malformed snapshots fail rather than silently hiding work.
    text = _run(socket, "list-sessions", "-F",
                "#{session_id}\t#{session_name}\t#{session_windows}\t#{session_attached}",
                allow_absent=True)
    if text is None:
        return []
    sessions = []
    try:
        for line in text.splitlines():
            ident, name, windows, attached = line.split("\t")
            if not _SESSION_ID.fullmatch(ident):
                raise ValueError("invalid session ID")
            sessions.append({"id": ident, "name": name, "windows": int(windows),
                             "attached": int(attached), "panes": []})
        by_id = {row["id"]: row for row in sessions}
        panes = _run(socket, "list-panes", "-a", "-F",
                     "#{session_id}\t#{window_index}\t#{pane_index}\t#{pane_id}\t#{pane_pid}")
        for line in panes.splitlines():
            session, window, pane, ident, pid = line.split("\t")
            if not _PANE_ID.fullmatch(ident):
                raise ValueError("invalid pane ID")
            by_id[session]["panes"].append({"id": ident, "window": int(window),
                                           "index": int(pane), "pid": int(pid)})
    except (ValueError, KeyError) as exc:
        raise ControlError("ETMUX", f"invalid or changed tmux snapshot: {exc}") from None
    return sorted(sessions, key=lambda s: s["name"])


def _resolve(sessions, target, io):
    if _PANE_ID.fullmatch(target):
        for session in sessions:
            for pane in session["panes"]:
                if pane["id"] == target:
                    return pane["id"], session
        raise ControlError("ENOENT", f"no exact pane: {target}")
    indexed = _INDEX_TARGET.fullmatch(target)
    name = indexed[1] if indexed else target
    matches = [s for s in sessions if s["id"] == name or s["name"] == name]
    if not matches:
        raise ControlError("ENOENT", f"no exact session: {name}")
    if len(matches) != 1:
        raise ControlError("EAMBIGUOUS", f"multiple exact sessions: {name}")
    session = matches[0]
    if not io:
        return session["id"], session
    panes = session["panes"]
    if indexed:
        panes = [p for p in panes if p["window"] == int(indexed[2])
                 and p["index"] == int(indexed[3])]
    if not panes:
        raise ControlError("ENOENT", f"no exact pane: {target}")
    if len(panes) != 1:
        raise ControlError("EAMBIGUOUS", f"session {name} has multiple panes; use a stable %ID or NAME:WINDOW.PANE")
    return panes[0]["id"], session


def _execute(req):
    op, socket = req["operation"], req["socket"]
    sessions = _snapshot(socket)
    data = {"operation": op, "dry_run": req["dry_run"]}
    if op == "list":
        return dict(data, sessions=sessions)
    resolved = None
    session = None
    if op == "new":
        if any(s["name"] == req["name"] for s in sessions):
            raise ControlError("EEXIST", f"session already exists: {req['name']}")
    else:
        if not sessions:
            # Distinguish an absent server from an absent target on a live one.
            _run(socket, "list-sessions")
        resolved, session = _resolve(sessions, req["target"], op not in {"rename", "close"})
        if op == "rename" and any(s["name"] == req["new_name"] for s in sessions):
            raise ControlError("EEXIST", f"session already exists: {req['new_name']}")
    if req["dry_run"]:
        data["request"] = req
        if resolved is not None:
            data["target"] = resolved
        return data
    if op == "new":
        args = ["new-session", "-d", "-s", req["name"], "-x", "200", "-y", "50",
                "-P", "-F", "#{session_id}"]
        if "cwd" in req:
            args.extend(["-c", req["cwd"]])
        ident = _run(socket, *args).strip()
        fresh = _snapshot(socket)
        created = next((s for s in fresh if s["id"] == ident), None)
        if created is None:
            raise ControlError("ETMUX", "created session disappeared before verification")
        return dict(data, session=created)
    if op == "close":
        _run(socket, "kill-session", "-t", resolved)
        return dict(data, closed=resolved)
    if op == "rename":
        _run(socket, "rename-session", "-t", resolved, req["new_name"])
        return dict(data, session=dict(session, name=req["new_name"]))
    data["target"] = resolved
    if op == "read":
        text = _run(socket, "capture-pane", "-t", resolved, "-p", "-J", "-S", f"-{req['lines']}")
        # tmux-cli bounded capture: remove blank viewport padding and retain
        # the requested final lines, including a trailing newline if nonempty.
        kept = text.split("\n")
        while kept and not kept[-1].strip():
            kept.pop()
        kept = kept[-req["lines"]:]
        return dict(data, text="\n".join(kept) + ("\n" if kept else ""), lines=req["lines"])
    if op in {"send", "type"}:
        _send_literal(socket, resolved, req["text"])
        data.update(sent=len(req["text"]), submitted=False)
        if op == "type":
            try:
                _run(socket, "send-keys", "-t", resolved, "Enter")
            except ControlError as exc:
                exc.details = dict(data, completion="unknown")
                raise
            data.update(submitted=True, completion="unknown")
        return data
    _run(socket, "send-keys", "-t", resolved, "--", *req["keys"])
    return dict(data, keys=req["keys"], submitted="Enter" in req["keys"], completion="unknown")


def dispatch(request):
    """Return a versioned JSON-compatible response; never select a socket."""
    try:
        return {"schema": SCHEMA, "ok": True, "data": _execute(_validate(request))}
    except ControlError as exc:
        result = {"schema": SCHEMA, "ok": False, "error": str(exc),
                  "code": exc.code, "exit": EXITS[exc.code]}
        if exc.details is not None:
            result["details"] = exc.details
        return result
