"""Library API for preparation, submission and inspection of managed runs."""
from dataclasses import dataclass, replace
import hashlib
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import agent_control as control

from . import providers
from .store import (RUN_ID, SCHEMA, TERMINAL, SessionError, new_id, now,
                    process_identity, root_path, run_files, storage)


@dataclass(frozen=True)
class Launch:
    provider: str
    cwd: str = "."
    model: str | None = None
    effort: str | None = None
    prompt: str | None = None
    interactive: bool = True
    placement: str = "auto"          # grid unless the coding_session_grid setting is off
    title: str | None = None
    grid: str = "agent-sessions"
    trust_folder: bool = False
    coding_yolo: bool = False
    source_pane: int | None = None
    expect_broker: str | None = None


def _plan(spec, root):
    if not isinstance(spec, Launch):
        raise SessionError("expected a Launch specification")
    if spec.placement not in ("auto", "new-tab", "grid", "foreground", "background"):
        raise SessionError("invalid placement")
    if spec.placement == "auto":
        import session_grid
        spec = replace(spec, placement="grid" if session_grid.enabled() else "new-tab")
    if spec.interactive and spec.placement == "background":
        raise SessionError("interactive sessions need a terminal; use --new-tab or --grid")
    path = Path(spec.cwd).expanduser()
    if spec.trust_folder and path.is_symlink():
        raise SessionError("use the real directory with --trust-folder")
    cwd = path.resolve(strict=True)
    if not cwd.is_dir():
        raise SessionError("cwd must be an existing directory")
    prompt = spec.prompt
    if prompt is not None and (not isinstance(prompt, str) or "\0" in prompt
                               or len(prompt.encode("utf-8")) > 1024 * 1024):
        raise SessionError("prompt must be UTF-8 without NUL, at most 1 MiB")
    if not spec.interactive and (prompt is None or not prompt.strip()):
        raise SessionError("headless sessions require a nonempty prompt")
    title = control.plain_text(spec.title or spec.provider, "title", 200)
    control.plain_text(spec.grid, "grid name", 200)
    control.plain_text(str(cwd), "cwd", 4096)
    if spec.source_pane is not None and (type(spec.source_pane) is not int or spec.source_pane <= 0):
        raise SessionError("source pane must be a positive integer")
    if spec.expect_broker is not None and not control.BROKER.fullmatch(spec.expect_broker):
        raise SessionError("expected broker must be an exact broker identity")
    spec = replace(spec, cwd=str(cwd), title=title)
    run_id = new_id()
    run = root_path(root) / run_id
    session_id = str(uuid.uuid4()) if spec.provider in ("claude", "grok") else None
    argv = providers.command(spec, run, session_id)
    worker = [sys.executable, "-B", str(Path(__file__).resolve().parents[2] / "kilix-session"),
              "--root", str(run.parent), "_worker", run_id]
    # __file__ is config/kilix_session/manager.py: the entry lives above config.
    record = {"schema": SCHEMA, "run_id": run_id, "run_dir": str(run),
              "created_at": now(), "status": "prepared", "cwd": spec.cwd,
              "provider": spec.provider, "title": title, "argv": argv,
              "worker_argv": worker, "interactive": spec.interactive,
              "placement": spec.placement, "grid": spec.grid if spec.placement == "grid" else None,
              # A terminal server need not inherit this invocation's client
              # config overrides. Carry only paths, never auth or full env.
              "client_environment": {key: str(Path(os.environ[key]).expanduser().absolute())
                                     if os.environ.get(key) else None for key in
                                     ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR")},
              "requested": {"model": spec.model, "effort": spec.effort, "session_id": session_id,
                            "trust_folder": spec.trust_folder, "coding_yolo": spec.coding_yolo},
              "observed": {"model": None, "session_id": None, "provider_error": False},
              "prompt": {"present": prompt is not None,
                         "bytes": len(prompt.encode("utf-8")) if prompt is not None else 0,
                         "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt is not None else None,
                         "delivery": "instruction-file" if spec.interactive and prompt is not None
                         else "native-ui" if spec.interactive else "file" if spec.provider == "grok" else "stdin"},
              "folder_trust": "pending" if spec.trust_folder else "client-managed",
              "agent_ready_verified": False, "task_completion_verified": False}
    return spec, record


def plan(spec, *, root=None):
    """Validate and describe a launch without changing files, trust, or panes."""
    return {**_plan(spec, root)[1], "status": "planned"}


def launch(spec, *, root=None, client=None):
    """Submit exactly one new run. A submitted pane is not a ready agent."""
    spec, record = _plan(spec, root)
    root, run_id = Path(record["run_dir"]).parent, record["run_id"]
    if spec.interactive and spec.placement == "foreground":
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise SessionError("interactive foreground launch needs terminal stdin and stdout")
    with run_files(root, run_id, create=True) as files:
        os.mkdir("tmp", 0o700, dir_fd=files.fd)
        with files.open("prompt.txt", "wb") as prompt:
            prompt.write((spec.prompt or "").encode("utf-8"))
            prompt.flush()
            os.fsync(prompt.fileno())
        files.save("run.json", record)
        files.save("launch.json", {"status": "prepared", "created_at": now()})
        try:
            if spec.placement in ("new-tab", "grid"):
                from .panes import submit
                submit(files, spec, record, client=client)
            elif spec.placement == "background":
                with files.open("runner.log", "wb") as log:
                    files.save("launch.json", {"status": "uncertain", "attempted_at": now()})
                    try:
                        proc = subprocess.Popen(record["worker_argv"], cwd=spec.cwd,
                                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                                start_new_session=True)
                    except OSError:
                        # Popen failed before returning a child. A later receipt
                        # failure must instead preserve the uncertain intent.
                        files.save("launch.json", {"status": "prepared"})
                        raise
                threading.Thread(target=proc.wait, daemon=True, name="kilix-session-reaper").start()
                files.save("launch.json", {"status": "submitted", "submitted_at": now(),
                                           "runner": process_identity(proc.pid)})
            else:
                files.save("launch.json", {"status": "submitted", "submitted_at": now()})
        except (OSError, ValueError, control.ControlError, SessionError) as exc:
            receipt = files.json("launch.json")
            if receipt["status"] == "prepared":
                receipt.update(status="failed", error=str(exc))
                files.save("launch.json", receipt)
                record.update(status="failed", ended_at=now(), error=str(exc), result_code=1)
                files.save("run.json", record)
            else:
                receipt.update(error=str(exc))
                files.save("launch.json", receipt)
    if spec.placement == "foreground":
        from .runner import run
        run(root, run_id)
    return inspect_run(run_id, root=root)


def inspect_run(run_id, *, root=None):
    with run_files(root, run_id) as files:
        record = files.json("run.json")
        if not record or record.get("schema") != SCHEMA or record.get("run_id") != run_id:
            raise SessionError("invalid session record")
        record["launch"] = files.json("launch.json")
        record["stop_requested"] = files.json("stop.json") is not None
        runner = record.get("runner") or (record["launch"] or {}).get("runner")
        alive = bool(runner and process_identity(runner.get("pid")) == runner)
        record["runner_alive"] = alive
        effective = record["status"]
        if effective not in TERMINAL:
            if runner and not alive:
                effective = "orphaned"
            elif effective == "prepared":
                effective = (record["launch"] or {}).get("status", "prepared")
                if effective == "submitted":
                    effective = "queued"
        record["effective_status"] = effective
        return record


def list_runs(*, root=None, limit=100):
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise SessionError("limit must be an integer between 1 and 10000")
    try:
        with storage(root) as files:
            names = sorted((n for n in os.listdir(files.fd) if RUN_ID.fullmatch(n)), reverse=True)
    except FileNotFoundError:
        return []
    records = []
    for run_id in names[:limit]:
        try:
            records.append(inspect_run(run_id, root=root))
        except (OSError, ValueError, RuntimeError) as exc:
            records.append({"run_id": run_id, "effective_status": "unreadable", "error": str(exc)})
    return records


def wait(run_id, *, root=None, timeout=60):
    """Wait for process exit, never infer task success; a timeout leaves it running."""
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 86400:
        raise SessionError("timeout must be finite and between 0 and 86400 seconds")
    deadline = time.monotonic() + timeout
    while True:
        result = inspect_run(run_id, root=root)
        if result["effective_status"] in TERMINAL | {"orphaned", "failed", "uncertain"}:
            return result
        if time.monotonic() >= deadline:
            return {**result, "wait_timed_out": True}
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def stop(run_id, *, root=None, timeout=10):
    """Request cancellation through the run's private mailbox, never a saved PID."""
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 86400:
        raise SessionError("timeout must be finite and between 0 and 86400 seconds")
    record = inspect_run(run_id, root=root)
    if record["effective_status"] in TERMINAL:
        return record
    if record["effective_status"] == "orphaned":
        raise SessionError("runner is gone; inspect the retained run and pane before manual recovery")
    with run_files(root, run_id) as files:
        with files.lock("stop.lock"):
            if files.json("stop.json") is None:
                files.save("stop.json", {"schema": SCHEMA, "run_id": run_id, "requested_at": now()})
    return wait(run_id, root=root, timeout=timeout)
