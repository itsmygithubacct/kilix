"""Launch and inspect coding sessions; new panes always preserve focus."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

import agent_control as control
from agent_programs import resolve_agent_command
from agent_skills import Conflict
import session_grid

from .manager import Launch, inspect_run, launch, list_runs, plan, stop, wait
from .providers import PROVIDERS
from .store import TERMINAL, SessionError, root_path, run_files

LOGS = {"events": "events.jsonl", "stderr": "stderr.log", "final": "final.txt",
        "runner": "runner.log", "prompt": "prompt.txt"}


def seconds(value):
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 86400:
        raise argparse.ArgumentTypeError("seconds must be finite and between 0 and 86400")
    return result


def positive(value):
    result = int(value)
    if not 1 <= result <= 10000:
        raise argparse.ArgumentTypeError("expected an integer between 1 and 10000")
    return result


def assignment(value):
    name, separator, text = value.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError("expected NAME=VALUE")
    return name, text


def parser():
    result = argparse.ArgumentParser(prog="kilix session", description=__doc__,
        epilog="Run records: $KILIX_AGENT_RUNS_HOME or $KILIX_STORAGE_HOME/agent-runs. "
               "Interactive prompts use an exact private instruction file. See docs/session.md.")
    result.add_argument("--root", help="absolute managed run directory override")
    result.add_argument("--json", action="store_true", help="JSON is the default receipt format")
    commands = result.add_subparsers(dest="command", required=True)
    start = commands.add_parser("launch", help="launch one new coding session (native UI by default)")
    start.add_argument("provider", choices=PROVIDERS)
    start.add_argument("--cwd", default=".", help="existing directory or worktree")
    start.add_argument("--model", "-m", help="exact requested model; no substitutions")
    start.add_argument("--effort", "-e", help="exact provider reasoning-effort level")
    start.add_argument("--name", "--title", dest="title", help="pane title (default: provider)")
    mode = start.add_mutually_exclusive_group()
    mode.add_argument("--interactive", "-i", action="store_true", default=True)
    mode.add_argument("--headless", action="store_true", help="logged single-turn execution")
    placement = start.add_mutually_exclusive_group()
    placement.add_argument("--new-tab", action="store_true",
                           help="a tab of its own, preserve focus (default when coding_session_grid is off)")
    placement.add_argument("--grid", metavar="NAME",
                           help="join session grid NAME (default agent-sessions): two rows, at most twelve panes per tab")
    placement.add_argument("--foreground", action="store_true", help="wait in the current terminal")
    placement.add_argument("--background", action="store_true", help="detached headless runner, no pane")
    prompt = start.add_mutually_exclusive_group()
    prompt.add_argument("--prompt", help="literal initial instructions")
    prompt.add_argument("--prompt-file", help="UTF-8 file, or '-' for explicit stdin")
    start.add_argument("--trust-folder", action="store_true",
                       help="record client folder trust for exactly cwd before starting; does not bypass tool approvals")
    start.add_argument("--coding-yolo", action="store_true",
                       help="request approval bypass only if Kilix's coding-yolo setting is also on")
    start.add_argument("--source-pane", type=control.positive, help="verified caller if its environment was stripped")
    start.add_argument("--expect-broker", help="optional exact caller broker identity")
    start.add_argument("--dry-run", action="store_true", help="validate and print a plan; no files, trust changes or panes")
    listing = commands.add_parser("list", help="list managed runs, newest first")
    listing.add_argument("--limit", type=positive, default=100)
    show = commands.add_parser("show", aliases=["status"], help="full run/launch evidence and live runner observation")
    show.add_argument("run_id")
    logs = commands.add_parser("logs", help="read retained output; native UI uses client history / kilix transcript")
    logs.add_argument("run_id")
    logs.add_argument("--stream", choices=LOGS, default="events")
    logs.add_argument("--lines", type=positive, default=200)
    logs.add_argument("--follow", "-f", action="store_true")
    for name, description, timeout in (("wait", "wait for process exit, leaving a timed-out run active", 60),
                                      ("stop", "request cancellation of this managed run; retain its pane and logs", 10)):
        command = commands.add_parser(name, help=description)
        command.add_argument("run_id")
        command.add_argument("--timeout", type=seconds, default=timeout)
    batch = commands.add_parser("batch", help="validate an explicit JSON array of launch argument arrays")
    batch.add_argument("file")
    batch.add_argument("--stagger", type=seconds, default=30, help="seconds between submissions (default: 30)")
    batch.add_argument("--dry-run", action="store_true")
    commands.add_parser("providers", help="installed provider paths, without installing or starting clients")
    grids = commands.add_parser("grid", help="session grids: open any command as a grid pane, or list grids")
    grid_commands = grids.add_subparsers(dest="grid_command", required=True)
    add = grid_commands.add_parser("add", help="open COMMAND as a pane in a session grid without taking focus",
                                   epilog="Joins the newest grid NAME with room in this OS window, else opens "
                                          "a new grid tab. Prints a JSON receipt with the pane, tab and slot.")
    add.add_argument("--name", help="grid name (default: agent-sessions, or with --into the grid of that pane)")
    where = add.add_mutually_exclusive_group()
    where.add_argument("--new", action="store_true", help="always open a new grid tab")
    where.add_argument("--into", type=control.positive, metavar="PANE", help="join the grid that holds PANE")
    add.add_argument("--title", required=True, help="pane title")
    add.add_argument("--cwd", default=".", help="existing directory (default: here)")
    add.add_argument("--no-hold", action="store_true", help="close the pane when the command exits")
    add.add_argument("--var", action="append", default=[], type=assignment, metavar="NAME=VALUE",
                     help="set a pane user variable (repeatable)")
    add.add_argument("--env", action="append", default=[], type=assignment, metavar="NAME=VALUE",
                     help="set an environment variable for the command (repeatable)")
    add.add_argument("--source-pane", type=control.positive, help="verified caller if its environment was stripped")
    add.add_argument("--expect-broker", help="optional exact caller broker identity")
    add.add_argument("program", nargs=argparse.REMAINDER, metavar="COMMAND", help="-- PROGRAM ARGS...")
    listed = grid_commands.add_parser("list", help="grid tabs, newest first, with pane slots")
    listed.add_argument("--name", help="only the grids of this name")
    grid_commands.add_parser("enabled", help="print on or off: whether coding sessions open in a grid by default "
                                             "(exit 0 when on, 1 when off)")
    worker = commands.add_parser("_worker", help="internal: execute one previously prepared run")
    worker.add_argument("run_id")
    for command in (start, listing, show, logs, batch, worker, *[commands.choices[n] for n in ("wait", "stop", "providers")]):
        command.add_argument("--root", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    return result


def specification(args, *, batch=False):
    prompt = args.prompt
    if args.prompt_file:
        if args.prompt_file == "-":
            if batch:
                raise SessionError("batch entries cannot read shared stdin")
            raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        else:
            with Path(args.prompt_file).expanduser().open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise SessionError("prompt exceeds 1 MiB")
        prompt = raw.decode("utf-8")  # Preserve CRLF, final newlines and all whitespace.
    placement = ("background" if args.background else "foreground" if args.foreground else "grid" if args.grid
                 else "new-tab" if args.new_tab else "auto")
    return Launch(provider=args.provider, cwd=args.cwd, model=args.model, effort=args.effort,
                  title=args.title, prompt=prompt, interactive=not args.headless, placement=placement,
                  grid=args.grid or "agent-sessions", trust_folder=args.trust_folder,
                  coding_yolo=args.coding_yolo, source_pane=args.source_pane, expect_broker=args.expect_broker)


def emit(record):
    print(json.dumps(record, ensure_ascii=True, indent=2), flush=True)


def result_code(record):
    if record.get("wait_timed_out"):
        return 124
    if (record.get("launch") or {}).get("status") in ("failed", "uncertain"):
        return 1
    if record.get("effective_status") in ("orphaned", "unreadable"):
        return 1
    return record.get("result_code", 0)


def batch(args):
    with Path(args.file).expanduser().open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise SessionError("batch exceeds 1 MiB")
    entries = json.loads(raw)
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise SessionError("batch must contain 1–100 explicit argument arrays")
    specs, plans = [], []
    for entry in entries:
        if (not isinstance(entry, list) or not entry or any(not isinstance(v, str) for v in entry)
                or entry[0] not in PROVIDERS or any(v in ("--root", "--dry-run", "--help", "-h") or v.startswith("--root=") for v in entry)):
            raise SessionError("each batch entry starts with a provider; root/dry-run/help belong on the batch command")
        parsed = parser().parse_args(["launch", *entry])
        spec = specification(parsed, batch=True)
        if spec.placement == "foreground":
            raise SessionError("batch entries cannot use foreground placement")
        specs.append(spec)
        plans.append(plan(spec, root=args.root))
    if args.dry_run:
        emit({"status": "planned", "stagger": args.stagger, "runs": plans})
        return 0
    results, code = [], 0
    last = None
    try:
        for spec in specs:
            if last is not None:
                time.sleep(max(0, last + args.stagger - time.monotonic()))
            last = time.monotonic()
            record = launch(spec, root=args.root)
            results.append(record)
            code = result_code(record)
            if code:
                break
    except (KeyboardInterrupt, OSError, ValueError, RuntimeError) as exc:
        emit({"status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
              "error": str(exc), "runs": results})
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    emit({"status": "failed" if code else "submitted", "runs": results})
    return code


def grid(args):
    if args.grid_command == "enabled":
        on = session_grid.enabled()
        print("on" if on else "off")
        return 0 if on else 1
    client = control.Client(getattr(args, "source_pane", None))
    if args.grid_command == "list":
        emit({"capacity": session_grid.CAPACITY, "grids": session_grid.grids(client.snapshot(), args.name)})
        return 0
    argv = args.program[1:] if args.program[:1] == ["--"] else args.program
    if not argv:
        raise SessionError("give the pane's command after --")
    cwd = Path(args.cwd).expanduser().resolve(strict=True)
    if not cwd.is_dir():
        raise SessionError("cwd must be an existing directory")
    if not client.caller:
        raise SessionError("no identified Kilix caller; use --source-pane")
    source = control.describe(client.resolve(client.caller, args.expect_broker))
    if not control.BROKER.fullmatch(source["broker"]):
        raise SessionError("source pane lacks a broker identity")
    result = session_grid.place(client, source=source, argv=argv, cwd=str(cwd), title=args.title,
                                name=args.name, new=args.new, into=args.into, hold=not args.no_hold,
                                extra_vars=args.var, extra_env=args.env)
    emit({"status": "created", **result})
    return 0


def logs(args):
    with run_files(args.root, args.run_id) as files:
        try:
            stream = files.open(LOGS[args.stream])
        except FileNotFoundError:
            raise SessionError("this output is not available; native sessions retain provider history and terminal transcripts")
        with stream:
            stream.seek(max(0, os.fstat(stream.fileno()).st_size - 1024 * 1024))
            tail = stream.read().splitlines(keepends=True)[-args.lines:]
            sys.stdout.buffer.write(b"".join(tail))
            sys.stdout.buffer.flush()
            while args.follow:
                block = stream.read(65536)
                if block:
                    sys.stdout.buffer.write(block)
                    sys.stdout.buffer.flush()
                elif inspect_run(args.run_id, root=args.root)["effective_status"] in TERMINAL | {"orphaned", "uncertain"}:
                    break
                else:
                    time.sleep(0.1)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in PROVIDERS:
        argv.insert(0, "launch")
    if not argv:
        argv = ["--help"]
    args = parser().parse_args(argv)
    try:
        if args.command == "launch":
            spec = specification(args)
            record = plan(spec, root=args.root) if args.dry_run else launch(spec, root=args.root)
            if args.dry_run:
                record["status"] = "planned"
            emit(record)
            return result_code(record)
        if args.command == "list":
            keys = ("run_id", "provider", "title", "cwd", "effective_status", "requested", "observed", "error")
            emit({"root": str(root_path(args.root)), "runs": [
                {key: r[key] for key in keys if key in r} for r in list_runs(root=args.root, limit=args.limit)]})
        elif args.command in ("show", "status", "wait", "stop"):
            function = {"show": inspect_run, "status": inspect_run, "wait": wait, "stop": stop}[args.command]
            record = function(args.run_id, root=args.root,
                              **({"timeout": args.timeout} if hasattr(args, "timeout") else {}))
            emit(record)
            return result_code(record) if args.command in ("wait", "stop") else 0
        elif args.command == "logs":
            return logs(args)
        elif args.command == "batch":
            return batch(args)
        elif args.command == "grid":
            return grid(args)
        elif args.command == "providers":
            emit({"providers": [{"provider": p, "path": resolve_agent_command(p),
                                  "interactive": True, "headless": True, "trust_folder": True} for p in PROVIDERS]})
        elif args.command == "_worker":
            from .runner import run
            return run(args.root, args.run_id)
        return 0
    except (SessionError, control.ControlError, Conflict, OSError, ValueError) as exc:
        print("kilix session: " + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
