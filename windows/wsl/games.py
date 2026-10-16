"""Installed WSL game readiness, used by Needle's existing apps safety check."""
from pathlib import Path
import os


def game_ready(name):
    if '/' in name or '\\' in name:
        return False
    binary = name if name.startswith('kilix-') else 'kilix-' + name
    path = Path.home() / '.local/share/kilix-wsl/bin' / binary
    return path.is_file() and os.access(path, os.X_OK)


def game_enabled(name):
    return game_ready(name)
