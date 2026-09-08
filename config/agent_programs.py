"""Coding-client executable lookup shared by setup and pane launching."""
import os
import shutil

AGENT_PREFIX_BINDIRS = ("~/.local/bin", "~/.kimi-code/bin")


def resolve_agent_command(command):
    found = shutil.which(command)
    if found:
        return found
    for bindir in AGENT_PREFIX_BINDIRS:
        candidate = os.path.join(os.path.expanduser(bindir), command)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None
