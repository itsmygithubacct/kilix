"""One claimed run and one directly owned provider process group.

The leader remains an unreaped child until group cleanup finishes. No signal
target is loaded from disk, and interactive job control never signals the
caller's process group. This needs Linux waitid and Python 3.11+.
"""
from contextlib import ExitStack
import json
import os
import signal
import subprocess
import sys
import termios
import time

from agent_trust import trust

from .providers import observe
from .store import SCHEMA, SessionError, now, process_identity, run_files


class Events:
    def __init__(self, stream, provider, observed):
        self.stream, self.provider, self.observed = stream, provider, observed
        self.pending, self.discarding, self.final = b"", False, None

    def read(self, *, final=False):
        # Bounded parsing even when a provider emits a huge/non-JSON line.
        # Raw bytes are written directly to disk, independently of parsing.
        for _ in range(16):
            block = self.stream.read(65536)
            if not block:
                break
            parts = (self.pending + block).split(b"\n")
            self.pending = parts.pop()
            for line in parts:
                if self.discarding:
                    self.discarding = False
                    continue
                self.event(line)
            if len(self.pending) > 1024 * 1024:
                self.pending = b""
                self.discarding = True
        if final:
            if self.pending and not self.discarding:
                self.event(self.pending)
            self.pending = b""

    def event(self, line):
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            return
        result = observe(self.provider, event, self.observed)
        if result is not None:
            self.final = result


def _group_signal(process, signum):
    # Called only before wait()/poll() can reap our own group leader.
    # An application-global waiter must not turn a collected child into a
    # numeric-PID signal target. Refuse if wait ownership has been lost.
    _exited(process)
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def _exited(process):
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def run(root, run_id):
    with run_files(root, run_id) as files:
        # Creation is exclusive and persists after exit: a run cannot be replayed.
        with files.open("worker.claim", "wb") as claim:
            claim.write(str(os.getpid()).encode("ascii"))
            claim.flush()
            os.fsync(claim.fileno())
        record = files.json("run.json")
        if not record or record.get("schema") != SCHEMA or record.get("run_id") != run_id:
            raise SessionError("invalid worker record")
        record.update(status="starting", runner=process_identity(os.getpid()), started_at=now(),
                      pane_id=os.environ.get("KITTY_WINDOW_ID"),
                      broker=os.environ.get("KITTY_PTY_BROKER_SESSION"))
        files.save("run.json", record)
        proc, original_group, interrupted = None, None, None
        terminal_taken, terminal_settings = False, None
        handlers = {}
        client_environment = record.get("client_environment", {})
        previous_environment = {key: os.environ.get(key) for key in client_environment}

        def receive(signum, _frame):
            nonlocal interrupted
            if record["interactive"] and signum == signal.SIGINT:
                return  # Terminal Ctrl-C belongs to the foreground provider UI.
            interrupted = interrupted or signum

        def cancelled():
            nonlocal interrupted
            request = files.json("stop.json")
            if request is not None:
                if request.get("schema") != SCHEMA or request.get("run_id") != run_id:
                    raise SessionError("invalid stop request")
                interrupted = interrupted or signal.SIGTERM
            return interrupted

        try:
            for key, value in client_environment.items():
                if key not in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
                    raise SessionError("invalid client environment path")
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                handlers[sig] = signal.signal(sig, receive)
            if cancelled():
                raise InterruptedError("cancelled before provider startup")
            if record["requested"]["trust_folder"]:
                record["folder_trust"] = trust(record["provider"], record["cwd"])
                files.save("run.json", record)
            if cancelled():
                raise InterruptedError("cancelled before provider startup")
            env = os.environ.copy()
            env.update(KILIX_AGENT_RUN_DIR=str(files.path), AGENT_SESSION_DIR=str(files.path),
                       TMPDIR=str(files.path / "tmp"), PYTHONUNBUFFERED="1")
            with ExitStack() as stack:
                events = None
                kwargs = {}
                if record["interactive"]:
                    if not (sys.stdin.isatty() and sys.stdout.isatty()):
                        raise SessionError("interactive worker needs terminal stdin and stdout")
                    original_group = os.tcgetpgrp(sys.stdin.fileno())
                    if original_group != os.getpgrp():
                        raise SessionError("interactive worker must start in the terminal's foreground group")
                    terminal_settings = termios.tcgetattr(sys.stdin.fileno())
                    handlers[signal.SIGTTOU] = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
                    kwargs["process_group"] = 0
                else:
                    output = stack.enter_context(files.open("events.jsonl", "wb"))
                    errors = stack.enter_context(files.open("stderr.log", "wb"))
                    prompt = stack.enter_context(files.open("prompt.txt", "rb"))
                    kwargs.update(stdin=prompt if record["provider"] != "grok" else subprocess.DEVNULL,
                                  stdout=output, stderr=errors, start_new_session=True)
                    reader = stack.enter_context(files.open("events.jsonl", "rb"))
                    events = Events(reader, record["provider"], record["observed"])
                proc = subprocess.Popen(record["argv"], cwd=record["cwd"], env=env, umask=0o077, **kwargs)
                if record["interactive"]:
                    os.tcsetpgrp(sys.stdin.fileno(), proc.pid)
                    terminal_taken = True
                    _group_signal(proc, signal.SIGCONT)  # A fast reader may have received SIGTTIN.
                record.update(status="running", provider_process=process_identity(proc.pid),
                              process_group=proc.pid)
                files.save("run.json", record)
                sent, kill_at = False, None
                previous = dict(record["observed"])
                while True:
                    cancelled()
                    if interrupted and not sent:
                        _group_signal(proc, interrupted)
                        sent, kill_at = True, time.monotonic() + 3
                        record.update(status="stopping", signal=interrupted)
                        files.save("run.json", record)
                    if sent and time.monotonic() >= kill_at:
                        _group_signal(proc, signal.SIGKILL)
                    if events:
                        events.read()
                        if record["observed"] != previous:
                            files.save("run.json", record)
                            previous = dict(record["observed"])
                    if _exited(proc):
                        break
                    time.sleep(0.05)
                # Close only this directly-created process group, while its
                # original leader is still unreaped and cannot be recycled.
                _group_signal(proc, signal.SIGTERM)
                time.sleep(0.1)
                _group_signal(proc, signal.SIGKILL)
                code = proc.wait()
                if events:
                    # Drain any remaining output, including a final partial line.
                    while reader.tell() < os.fstat(reader.fileno()).st_size:
                        events.read()
                    events.read(final=True)
                    if events.final is not None:
                        with files.open("final.txt", "wb") as final:
                            final.write(events.final.encode("utf-8"))
                status = ("interrupted" if interrupted or code < 0 else "failed"
                          if code or record["observed"]["provider_error"] else "completed")
                result_code = 128 + int(interrupted) if interrupted else 128 - code if code < 0 else code or int(record["observed"]["provider_error"])
                record.update(status=status, exit_code=code, result_code=result_code, ended_at=now())
                files.save("run.json", record)
                return result_code
        except BaseException as exc:
            # Retain errors and interruptions, including failures after spawn.
            if proc is not None and proc.returncode is None:
                try:
                    _group_signal(proc, signal.SIGTERM)
                    deadline = time.monotonic() + 1
                    while not _exited(proc) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    _group_signal(proc, signal.SIGKILL)
                    proc.wait()
                except ChildProcessError:
                    record["cleanup_error"] = "child wait ownership lost; no further signals sent"
            interrupted = interrupted or (signal.SIGINT if isinstance(exc, KeyboardInterrupt) else None)
            record.update(status="interrupted" if interrupted else "failed", error=str(exc),
                          ended_at=now(), result_code=128 + int(interrupted) if interrupted else 1,
                          exit_code=proc.returncode if proc is not None else None)
            files.save("run.json", record)
            print("kilix session: " + str(exc), file=sys.stderr)
            return record["result_code"]
        finally:
            if terminal_taken:
                try:
                    os.tcsetpgrp(sys.stdin.fileno(), original_group)
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, terminal_settings)
                except OSError:
                    pass
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            for key, value in previous_environment.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
