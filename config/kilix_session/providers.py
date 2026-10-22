"""Small adapters for native clients; authorization belongs to existing Kilix policy."""
import json
from pathlib import Path
import re
from types import SimpleNamespace

import agent_control as control
from agent_programs import resolve_agent_command

from .store import SessionError

PROVIDERS = ("codex", "claude", "grok")


def command(spec, run, session_id):
    if spec.provider not in PROVIDERS:
        raise SessionError("supported session providers: " + ", ".join(PROVIDERS))
    executable = resolve_agent_command(spec.provider)
    if not executable:
        raise SessionError(f"{spec.provider} is not installed; use kilix install {spec.provider}")
    if spec.effort and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", spec.effort):
        raise SessionError("effort must be one literal level (letters, digits, '_' or '-')")
    # Reuse model validation, executable policy, exact Grok trust scope and
    # the explicit opt-in + shared-setting check for approval bypass.
    args = SimpleNamespace(agent=spec.provider, model=spec.model, prompt=None,
                           resume=None, coding_yolo=spec.coding_yolo,
                           trust_folder=spec.trust_folder, agent_arg=[])
    argv = control.agent_argv(args, executable, Path(spec.cwd))
    if spec.effort:
        if spec.provider == "codex":
            argv += ["-c", "model_reasoning_effort=" + json.dumps(spec.effort)]
        else:
            argv += ["--effort" if spec.provider == "claude" else "--reasoning-effort", spec.effort]
    if spec.provider in ("claude", "grok"):
        argv += ["--session-id", session_id]
    if spec.interactive:
        if spec.prompt is not None:
            # Fixed bootstrap avoids CLI subcommands, file-expansion syntax,
            # prompt length limits and accidental multiline composer input.
            # The original bytes remain in prompt.txt, never rewritten.
            argv += ["--", "Please read the UTF-8 file at " + json.dumps(str(run / "prompt.txt"))
                     + " as my exact initial instructions and carry out that task."]
        return argv
    if spec.provider == "codex":
        return [argv[0], "exec", *argv[1:], "--json", "--output-last-message", str(run / "final.txt"), "-"]
    if spec.provider == "claude":
        return argv + ["--print", "--verbose", "--output-format", "stream-json"]
    return argv + ["--prompt-file", str(run / "prompt.txt"), "--verbatim", "--output-format", "streaming-json"]


def observe(provider, event, observed):
    """Only documented event evidence; requested identities are never observations."""
    if not isinstance(event, dict):
        return None
    if provider == "codex" and event.get("type") == "thread.started":
        if isinstance(event.get("thread_id"), str) and len(event["thread_id"]) <= 512:
            observed["session_id"] = event["thread_id"]
    if provider == "claude" and event.get("type") == "system" and event.get("subtype") == "init":
        for source, dest in (("session_id", "session_id"), ("model", "model")):
            if isinstance(event.get(source), str) and len(event[source]) <= 512:
                observed[dest] = event[source]
    if event.get("type") in ("error", "turn.failed") or event.get("is_error") is True:
        observed["provider_error"] = True
    if provider == "claude" and event.get("type") == "result":
        if str(event.get("subtype", "")).startswith("error"):
            observed["provider_error"] = True
        if isinstance(event.get("result"), str):
            return event["result"]
    return None
