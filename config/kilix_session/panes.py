"""Explicit, focus-preserving placement using existing host control primitives."""
from types import SimpleNamespace

import agent_control as control
from agent_actions import GuardedClient
import session_grid

from .store import SessionError, now


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
                               prompt=None, resume=None, own_tab=True)
        result = control.launch(guarded, args, explicit_argv=record["worker_argv"])
        pane = result["pane"]
        if not control.BROKER.fullmatch(pane["broker"]):
            raise SessionError("created pane has no verified broker; inspect before retrying")
        guarded.resolve(pane["pane_id"], pane["broker"])
        receipt.update(status="submitted", pane=pane, submitted_at=now())
        files.save("launch.json", receipt)
        return

    # Every grid launch goes through the shared placement: the newest grid of
    # this name with room in the caller's OS window, else a new grid tab.
    def retarget(identity):
        request["target"] = identity
        receipt["target"] = identity

    result = session_grid.place(guarded, source=source, argv=record["worker_argv"], cwd=spec.cwd,
                                title=spec.title, name=spec.grid, hold=True,
                                extra_vars=(("kilix_session_run", record["run_id"]),), retarget=retarget)
    receipt.update(status="submitted", submitted_at=now(), pane=result["pane"],
                   grid={key: result[key] for key in ("grid", "tab_id", "new_tab", "layout", "fallback",
                                                     "count", "capacity", "slot")},
                   grid_capacity=result["capacity"])
    files.save("launch.json", receipt)
