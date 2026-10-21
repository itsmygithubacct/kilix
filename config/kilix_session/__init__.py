"""Managed coding runs for Kilix. No installation or mutation on import.

Public API: Launch, plan, launch, inspect_run, list_runs, stop, wait.
Records use the versioned kilix.session/v1 schema. See docs/session.md.
"""
from .manager import Launch, inspect_run, launch, list_runs, plan, stop, wait

__all__ = ["Launch", "plan", "launch", "inspect_run", "list_runs", "stop", "wait"]
__version__ = "0.2.2"
