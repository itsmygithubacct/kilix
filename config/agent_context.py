"""Compact, read-only startup facts for an agent's actual tool runner."""
from __future__ import annotations

import json
from pathlib import Path

import agent_capabilities
import agent_control

SCHEMA = "kilix.agent-context/v1"
MAX_BYTES = 4096
RULES = [
    "Use this snapshot for discovery, not as authorization or an action preflight.",
    "Pane/broker identities can change; reacquire them after restart or a stale-identity error.",
    "Pass the JSON body in the same invocation; timeout is seconds, default 15 (1-60).",
    "Retain operation_id; query status only for a missing or uncertain receipt.",
    "Creation/submission does not prove readiness, acknowledgment or completion.",
]


def _short(value, limit=64):
    return value if isinstance(value, str) and len(value) <= limit and value.isascii() else None


def _component(value, program="kilix"):
    backend = value.get("action_backend", {})
    known = ("pane.open", "agent.launch", "agent.deliver", "operation.status")
    command = value.get("command")
    if command is None and value.get("root"):
        command = str(Path(value["root"]) / program)
    compact_command = (command if isinstance(command, str) and
                       len(json.dumps(command, ensure_ascii=True)) <= 512 else None)
    return {"status": _short(value.get("status")),
            "command": compact_command,
            "command_omitted": command is not None and compact_command is None,
            "revision": _short(value.get("revision")),
            "revision_status": _short(value.get("revision_status")),
            "version": _short(value.get("version")),
            "revision_matches_expected": value.get("revision_matches_expected"),
            "action_schema": _short(backend.get("schema")),
            "action_metadata_status": _short(backend.get("status")),
            "actions_in_source": [name for name in known if name in backend.get("operations", {})]}


def _identity(panes, pane_id, expected=None):
    if type(pane_id) is not int or not 0 < pane_id < 2**63:
        return {"status": "unknown"}
    matches = [pane for pane in panes if pane.get("id") == pane_id]
    if len(matches) != 1:
        return {"status": "absent" if not matches else "ambiguous", "pane_id": pane_id}
    broker = (matches[0].get("env") or {}).get("KITTY_PTY_BROKER_SESSION")
    if not isinstance(broker, str) or not agent_control.BROKER.fullmatch(broker):
        return {"status": "broker_unavailable", "pane_id": pane_id}
    if sum((pane.get("env") or {}).get("KITTY_PTY_BROKER_SESSION") == broker
           for pane in panes) != 1:
        return {"status": "ambiguous", "pane_id": pane_id}
    if expected is not None and broker != expected:
        return {"status": "stale", "pane_id": pane_id}
    return {"status": "observed", "pane_id": pane_id, "broker": broker}


def collect(*, caller_pane=None, target=None, expected=None,
            client_factory=None, discover=None):
    """One terminal snapshot; inspect component files without running them.

    This reports execution identity, which may differ from the visible UI pane.
    It never supplies an identity override, input, launch or automatic target.
    """
    discover = discover or agent_capabilities.discover
    client_factory = client_factory or agent_control.Client
    facts = discover(source_root=Path(__file__).resolve().parents[1])
    result = {"schema": SCHEMA, "read_only": True, "status": "partial",
              "identity_basis": "explicit_caller" if caller_pane is not None else "tool_runner_connection",
              "caller": {"status": "unavailable"},
              "target": {"status": "not_requested"},
              "installation": {
                  "invoked": _component(facts["kilix"]["invoked_source"]),
                  "path_selected": _component(facts["kilix"]["path_selected"]),
                  "same_root": facts["kilix"]["same_root"],
                  "needle": _component(facts["needle"], "kilix-needle"),
                  "needle_action_adapter_present": facts["needle"].get("action_adapter_present", False),
                  "needle_mcp_actions_present": facts["needle"].get("action_mcp_tools_present", False),
                  "backend_override_status": _short(facts["action_module_override"].get("status")),
                  "needle_host_matches_invoked": (
                      facts["needle_kilix_selection"].get("resolved_command") ==
                      str(Path(facts["kilix"]["invoked_source"]["root"]) / "kilix")
                      if facts["kilix"]["invoked_source"].get("root") and
                      facts["needle_kilix_selection"].get("resolved_command") else None),
              }, "rules": RULES, "limit_bytes": MAX_BYTES}
    try:
        client = client_factory(caller_pane)
        panes = client.snapshot()
        result["caller"] = _identity(panes, client.caller)
        if target is not None:
            result["target"] = _identity(panes, target, expected)
        if result["caller"]["status"] == "observed" and (
                target is None or result["target"]["status"] == "observed"):
            result["status"] = "observed"
    except (agent_control.ControlError, OSError, ValueError, TypeError, KeyError):
        # Do not echo credential paths, arbitrary stderr or process metadata.
        result["connection_status"] = "unavailable"
        if target is not None:
            result["target"] = {"status": "unavailable"}
    return result


def encode(result):
    output = json.dumps(result, ensure_ascii=True, separators=(",", ":")) + "\n"
    if len(output.encode("ascii")) > MAX_BYTES:
        output = json.dumps({"schema": SCHEMA, "read_only": True,
                             "status": "partial", "error": "response_limit",
                             "limit_bytes": MAX_BYTES}, separators=(",", ":")) + "\n"
    return output
