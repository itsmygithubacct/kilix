"""Message a coding session through its client's own input channel, never its screen.

Routes, chosen per pane:

* Codex TUI attached to the shared app-server daemon: the thread's native idle
  queue (``thread/queue/add``), through kilix ralph's Codex adapter. The pane is
  matched to its thread by the footer's thread name and the process working
  directory; anything ambiguous is refused, never guessed.
* Claude Code: no shell-side channel. Report the session's peer name so another
  Claude Code session can use its SendMessage tool.
* Anything else, including a Codex TUI running its app-server in-process:
  report the screen fallback, ``kilix agent-control deliver``.

``send --thread`` and ``threads`` need no pane access, so a Codex session whose
tools run under the shared app-server (which Kilix's pane guard refuses) can
still reach another Codex thread.

This command never types into a pane.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

SCHEMA = "kilix.agent-message/v1"
HOME = Path(__file__).resolve().parents[1]
FOOTER = re.compile(r"^  (?:GPT-|gpt-|codex|o[1-9])[\w.-]*\b.*[·•]")
SEPARATOR = re.compile(r" [·•] ")
ELLIPSIS = ("…", "...")
DELIVERED = ("provider_queued", "accepted", "completed")
ENDED = ("cancelled", "expired")
MESSAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class MessageError(RuntimeError):
    pass


def ralph_home():
    return Path(os.environ.get("KILIX_RALPH_HOME", HOME / "third_party/kilix-ralph"))


def load_ralph():
    module = ralph_home()
    if not (module / "kilix_ralph/__main__.py").is_file():
        raise MessageError("kilix ralph module unavailable; initialize third_party/kilix-ralph")
    if str(module) not in sys.path:
        sys.path.insert(0, str(module))
    import kilix_ralph.codex
    import kilix_ralph.native
    import kilix_ralph.store
    return kilix_ralph


def footer(screen):
    """(cwd, name) from the lowest Codex footer row; name is None when not shown."""
    for line in reversed(screen.splitlines()):
        if not FOOTER.match(line):
            continue
        parts = [part.strip() for part in SEPARATOR.split(line.strip())]
        for index, part in enumerate(parts[1:], 1):
            if part.startswith(("~", "/")):
                name = " · ".join(parts[index + 1:]) or None
                return part, name
        return None, None
    return None, None


def expand(path):
    if path == "~" or path.startswith("~/"):
        return str(Path.home()) + path[1:]
    return path


def name_matches(shown, actual):
    if not shown or not actual:
        return False
    if shown == actual:
        return True
    for mark in ELLIPSIS:
        if shown.endswith(mark):
            prefix = shown[:-len(mark)].rstrip()
            return bool(prefix) and actual.startswith(prefix)
    return False


def match_threads(threads, cwd, shown_cwd, shown_name):
    """Threads a Codex pane can be showing; the caller refuses anything but one."""
    if shown_cwd and not shown_cwd.endswith(ELLIPSIS) and expand(shown_cwd) != cwd:
        return []  # the footer and the process disagree; trust neither
    return [t for t in threads if t.get("cwd") == cwd and name_matches(shown_name, t.get("name"))]


def daemon_threads(ralph, socket=None):
    endpoint = socket or ralph.codex.default_socket()
    if not Path(endpoint).exists():
        return None
    client = ralph.native.CodexSocket(endpoint)
    try:
        client.call("initialize", {"clientInfo": {"name": "kilix-agent-message", "version": "1"},
                                   "capabilities": {"experimentalApi": True}})
        client.send({"method": "initialized"})
        ids, cursor, seen = [], None, set()
        for _ in range(32):
            page = client.call("thread/loaded/list", {"cursor": cursor} if cursor else {})
            ids.extend(page.get("data") or [])
            cursor = page.get("nextCursor")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        threads = []
        for tid in ids:
            info = client.call("thread/read", {"threadId": tid, "includeTurns": False})["thread"]
            threads.append({"id": info.get("id"), "name": info.get("name"), "cwd": info.get("cwd"),
                            "status": (info.get("status") or {}).get("type")})
        return threads
    finally:
        client.close()


def process_cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def claude_session(pid, sessions=None):
    root = Path(sessions or Path.home() / ".claude/sessions")
    try:
        record = json.loads((root / f"{int(pid)}.json").read_text())
    except (OSError, ValueError):
        return None
    if record.get("pid") != int(pid):
        return None
    return {"name": record.get("name"), "pid": record["pid"], "session_id": record.get("sessionId")}


def agent_process(pane, wanted):
    for process in pane.get("foreground_processes") or []:
        argv = process.get("cmdline") or []
        names = [Path(arg.split(" ", 1)[0]).name for arg in argv[:2]]
        if wanted in names and Path(argv[0]).name != "node":
            return process
    for process in pane.get("foreground_processes") or []:
        if wanted in [Path(arg).name for arg in (process.get("cmdline") or [])[:2]]:
            return process
    return None


def screen_fallback(pane_id, reason, **extra):
    return {"schema": SCHEMA, "pane_id": pane_id, "route": "screen", "reason": reason,
            "next": f"kilix agent-control deliver --pane {pane_id} --message-id ID --text TEXT "
                    "(screen-verified typing; check the composer is empty first)", **extra}


def classify(pane, screen, threads, sessions=None):
    """Pick a route for one pane snapshot. Pure: all inputs are passed in."""
    pane_id = pane["id"]
    codex = agent_process(pane, "codex")
    claude = agent_process(pane, "claude")
    if codex and not claude:
        shown_cwd, shown_name = footer(screen)
        if threads is None:
            return screen_fallback(pane_id, "no shared Codex app-server socket", agent="codex")
        if not shown_name:
            return screen_fallback(pane_id, "the Codex footer shows no thread name (a new thread "
                                   "appears only after its first message); pass --thread",
                                   agent="codex")
        cwd = process_cwd(codex.get("pid")) or pane.get("cwd")
        found = match_threads(threads, cwd, shown_cwd, shown_name)
        if len(found) == 1:
            return {"schema": SCHEMA, "pane_id": pane_id, "agent": "codex", "route": "codex-native",
                    "thread": found[0]["id"], "thread_status": found[0].get("status"),
                    "reason": "footer thread name and working directory match one loaded thread"}
        if found:
            raise MessageError(f"pane {pane_id} matches {len(found)} loaded Codex threads "
                               f"({', '.join(t['id'] for t in found)}); pass --thread")
        return screen_fallback(pane_id, "no loaded thread on the shared app-server matches this "
                               "pane; its TUI may run the app-server in-process", agent="codex")
    if claude and not codex:
        peer = claude_session(claude.get("pid"), sessions)
        if peer:
            return {"schema": SCHEMA, "pane_id": pane_id, "agent": "claude",
                    "route": "claude-sendmessage", "peer": peer,
                    "reason": "Claude Code sessions take messages from another Claude Code "
                              "session's SendMessage tool; ListAgents shows this name with "
                              "its pid as [ref]"}
        return screen_fallback(pane_id, "Claude Code session record not found", agent="claude")
    return screen_fallback(pane_id, "no native message channel for this program")


def pane_snapshot(pane_id):
    sys.path.insert(0, str(HOME / "config"))
    from agent_control import Client, ControlError
    try:
        client = Client()
        panes = [p for p in client.snapshot() if p.get("id") == pane_id]
        if len(panes) != 1:
            raise MessageError(f"pane {pane_id} not found")
        screen = client.run(["get-text", "--match", f"id:{pane_id}", "--extent", "screen"])
    except ControlError as exc:
        raise MessageError(str(exc)) from exc
    return panes[0], screen


def resolve(pane_id, socket=None):
    pane, screen = pane_snapshot(pane_id)
    threads = None
    if agent_process(pane, "codex"):
        threads = daemon_threads(load_ralph(), socket)
    return classify(pane, screen, threads)


def target_for(ralph, thread, socket):
    """One ralph target per thread and installed adapter; bindings are immutable."""
    adapter = Path(ralph.codex.__file__).with_name("adapter_entry.py")
    key = json.dumps([sys.executable, str(adapter), str(Path(socket).absolute())])
    return f"codex:{thread}@{hashlib.sha256(key.encode()).hexdigest()[:8]}"


def ralph_cli(state_dir, *args):
    command = [sys.executable, "-B", str(HOME / "config/ralph.py")]
    if state_dir:
        command += ["--state-dir", str(state_dir)]
    done = subprocess.run([*command, *args], capture_output=True, text=True, timeout=60)
    if done.returncode:
        detail = (done.stderr or done.stdout).strip()
        try:
            detail = json.loads(detail)["error"]
        except (ValueError, KeyError, TypeError):
            pass
        raise MessageError(f"kilix ralph {args[0]}: {detail}")
    return json.loads(done.stdout)


def resume(ralph, state_dir, message_id, target, text):
    """True when this ID is already in ralph's store for the same thread and text.

    A retry then only waits for it. Re-enqueueing would be refused when the
    retry runs in another pane or shell, because ralph binds the sender too.
    """
    store = ralph.store.Store(state_dir)
    try:
        try:
            old = store.message(message_id)
        except ralph.store.Error:
            return False
    finally:
        store.close()
    if old["target"] != target or old["text"] != text:
        raise MessageError("--message-id is already used for a different message or thread; "
                           "use a new ID only for a new message")
    return True


def deliver(thread, text, message_id, wait, socket=None, state_dir=None):
    ralph = load_ralph()
    socket = str(socket or ralph.codex.default_socket())
    target = target_for(ralph, thread, socket)
    bound = ralph_cli(state_dir, "target", "codex", "--target", target, "--thread", thread,
                      "--socket", socket)
    store = ralph.store.Store(state_dir)
    try:
        running = store.worker_running()
    finally:
        store.close()
    worker = None
    command = [sys.executable, "-B", str(HOME / "config/ralph.py")]
    if state_dir:
        command += ["--state-dir", str(state_dir)]
    try:
        if not running:
            # A transient worker; a user's long-lived `kilix ralph serve` is left alone.
            worker = subprocess.Popen([*command, "serve", "--poll", "0.5"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      start_new_session=True)
            deadline = time.monotonic() + 10
            while True:
                store = ralph.store.Store(state_dir)
                try:
                    if store.worker_running():
                        break
                finally:
                    store.close()
                if worker.poll() is not None or time.monotonic() > deadline:
                    raise MessageError("the transient ralph worker did not start")
                time.sleep(0.1)
        if not resume(ralph, state_dir, message_id, target, text):
            ralph_cli(state_dir, "enqueue", "--target", target, "--text", text,
                      "--message-id", message_id)
        deadline = time.monotonic() + wait
        while True:
            message = ralph_cli(state_dir, "message", "status", message_id)
            status = message["status"]
            if status in DELIVERED or status in ENDED or status == "uncertain":
                break
            if time.monotonic() > deadline:
                break
            time.sleep(0.5)
    finally:
        if worker and worker.poll() is None:
            worker.send_signal(signal.SIGTERM)
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
    delivered = status in DELIVERED
    result = {"schema": SCHEMA, "route": "codex-native", "thread": thread, "target": target,
              "thread_status_at_bind": bound.get("state"), "message_id": message_id,
              "status": status, "delivered": delivered,
              "receipt": (message.get("receipt") or {}).get("evidence")}
    if status == "queued":
        result["next"] = ("the thread was not idle; the message stays queued and a running "
                          "`kilix ralph serve` delivers it at the next idle boundary"
                          if running else
                          "the thread was not idle before --wait ran out; the message stays "
                          "queued: run `kilix ralph serve` to deliver it at the next idle boundary")
    elif status == "uncertain":
        result["next"] = (f"inspect with `kilix ralph message status {message_id}`; "
                          "never resend under a new ID")
    return result


def read_text(args):
    if args.file:
        value = Path(args.file).read_text(encoding="utf-8")
    else:
        value = args.text
    if not value.strip():
        raise MessageError("message is empty")
    return value


def parser():
    p = argparse.ArgumentParser(prog="kilix agent-message", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="action", required=True)
    resolve_p = sub.add_parser("resolve", help="read-only: which channel reaches this pane")
    resolve_p.add_argument("--pane", type=int, required=True)
    threads_p = sub.add_parser("threads", help="read-only: Codex threads loaded in the shared "
                               "app-server (no pane access; works from a daemon-run Codex)")
    send = sub.add_parser("send", help="deliver natively, or report the fallback without typing")
    where = send.add_mutually_exclusive_group(required=True)
    where.add_argument("--pane", type=int)
    where.add_argument("--thread", help="a loaded Codex thread ID; no pane lookup")
    body = send.add_mutually_exclusive_group(required=True)
    body.add_argument("--text")
    body.add_argument("--file")
    send.add_argument("--message-id", help="stable ID; reuse it to retry, never a new one")
    send.add_argument("--wait", type=float, default=30,
                      help="seconds to wait for the thread to go idle (default 30)")
    for command in (resolve_p, threads_p, send):
        command.add_argument("--socket", help="Codex app-server control socket")
    send.add_argument("--state-dir", help="ralph state directory (default: ralph's own)")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == "resolve":
            result = resolve(args.pane, args.socket)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.action == "threads":
            threads = daemon_threads(load_ralph(), args.socket)
            if threads is None:
                raise MessageError("no shared Codex app-server socket")
            print(json.dumps({"schema": SCHEMA, "threads": threads}, ensure_ascii=False))
            return 0
        message_id = args.message_id or "am-" + uuid.uuid4().hex[:16]
        if not MESSAGE_ID.fullmatch(message_id):
            raise MessageError("--message-id must be 1-64 letters, digits, _ . -")
        if not 0 <= args.wait <= 3600:
            raise MessageError("--wait must be between 0 and 3600 seconds")
        text = read_text(args)
        thread = args.thread
        if thread is None:
            route = resolve(args.pane, args.socket)
            if route["route"] != "codex-native":
                print(json.dumps({**route, "delivered": False}, ensure_ascii=False))
                return 2
            thread = route["thread"]
        result = deliver(thread, text, message_id, args.wait, args.socket, args.state_dir)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["delivered"] else 1
    except (MessageError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"schema": SCHEMA, "error": str(exc)[:400]}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
