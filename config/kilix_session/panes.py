"""Explicit, focus-preserving placement using existing host control primitives."""
import hashlib
from types import SimpleNamespace

import agent_control as control
from agent_actions import GuardedClient

from .store import SessionError, now, storage


def submit(files, spec, record, *, client=None):
    client = client or control.Client(spec.source_pane)
    if not client.caller:
        raise SessionError("no identified Kilix caller; use foreground/background or an explicit source pane")
    if spec.source_pane and client.caller != spec.source_pane:
        raise SessionError("source pane conflicts with the inherited caller")
    source = control.describe(client.resolve(client.caller, spec.expect_broker))
    if not control.BROKER.fullmatch(source["broker"]):
        raise SessionError("source pane lacks a broker identity")
    identity = {"pane_id": source["pane_id"], "broker": source["broker"]}
    request = {"source": identity, "target": identity, "timeout": 30}
    receipt = {"status": "prepared", "source": identity, "keep_focus": True}

    def intent():
        receipt.update(status="uncertain", attempted_at=now())
        files.save("launch.json", receipt)

    class SessionClient(GuardedClient):
        def run(self, args, payload=None):
            if args[0] == "launch" and "--help" not in args:
                actual = list(args)
                actual[actual.index("--match") + 1] = "env:KITTY_PTY_BROKER_SESSION=" + request["target"]["broker"]
                receipt["argv"] = actual
            return super().run(args, payload)

    guarded = SessionClient(client, request, intent)
    files.save("launch.json", receipt)
    if spec.placement == "new-tab":
        args = SimpleNamespace(action="new-tab", pane=identity["pane_id"], expect_broker=identity["broker"],
                               cwd=spec.cwd, title=spec.title, trust_folder=False, dry_run=False,
                               prompt=None, resume=None)
        result = control.launch(guarded, args, explicit_argv=record["worker_argv"])
        pane = result["pane"]
        if not control.BROKER.fullmatch(pane["broker"]):
            raise SessionError("created pane has no verified broker; inspect before retrying")
        guarded.resolve(pane["pane_id"], pane["broker"])
        receipt.update(status="submitted", pane=pane, submitted_at=now())
        files.save("launch.json", receipt)
        return

    # Only panes bearing this store's and this group's marker count as ours.
    # Include manual panes in capacity, and never reuse a user-changed layout.
    group = hashlib.sha256((str(files.path.parent) + "\0" + spec.grid).encode()).hexdigest()
    with storage(files.path.parent) as root, root.lock("grid.lock"):
        guarded.verify()
        panes = guarded.snapshot()
        tabs = {}
        for pane in panes:
            if pane["os_window_id"] == source["os_window_id"]:
                tabs.setdefault(pane["tab_id"], []).append(pane)
        eligible = [members for members in tabs.values() if len(members) < 8
                    and all(p.get("layout") == "grid" for p in members)
                    and any((p.get("user_vars") or {}).get("kilix_session_group") == group for p in members)]
        members = eligible[-1] if eligible else None
        anchor = next(p for p in members if (p.get("user_vars") or {}).get("kilix_session_group") == group) if members else client.resolve(client.caller, source["broker"])
        target = control.describe(anchor)
        if not control.BROKER.fullmatch(target["broker"]):
            raise SessionError("grid anchor lacks a broker identity")
        request["target"] = {"pane_id": target["pane_id"], "broker": target["broker"]}
        receipt["target"] = request["target"]
        help_text = guarded.run(["launch", "--help"])
        if any(option not in help_text for option in ("--keep-focus", "--source-window", "--next-to", "--var")):
            raise SessionError("running engine lacks required focus-preserving grid options")
        argv = ["launch", "--match", f"window_id:{target['pane_id']}",
                "--next-to", f"id:{target['pane_id']}", "--source-window", f"id:{target['pane_id']}",
                "--keep-focus", "--hold", "--cwd", spec.cwd, "--title", spec.title,
                "--var", "kilix_session_group=" + group,
                "--var", "kilix_session_run=" + record["run_id"],
                "--type", "window" if members else "tab"]
        if not members:
            argv += ["--tab-title", spec.grid]
        argv += ["--", *record["worker_argv"]]
        receipt["argv"] = argv
        before = {p["id"] for p in panes}
        guarded.verify()
        current = guarded.resolve(target["pane_id"], target["broker"])
        if current["tab_id"] != target["tab_id"] or current["layout"] != target["layout"]:
            raise SessionError("grid anchor changed; inspect before launching")
        raw = guarded.run(argv).strip()
        if not raw.isdecimal() or int(raw) in before or int(raw) <= 0:
            raise SessionError("uncertain grid creation; inspect this run before retrying")
        created = control.describe(guarded.resolve(int(raw)))
        receipt["pane"] = created
        files.save("launch.json", receipt)
        if (created["os_window_id"] != source["os_window_id"]
                or not control.BROKER.fullmatch(created["broker"])
                or (members is not None and created["tab_id"] != target["tab_id"])
                or (members is None and created["tab_id"] == target["tab_id"])):
            raise SessionError("pane created in unexpected geometry; retained for inspection")
        if members is None:
            guarded.verify()
            guarded.resolve(created["pane_id"], created["broker"])
            guarded.run(["goto-layout", "--match", f"window_id:{created['pane_id']}", "grid"])
        receipt.update(status="submitted", submitted_at=now(), grid_capacity=8)
        files.save("launch.json", receipt)
