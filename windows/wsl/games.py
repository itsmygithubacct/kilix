"""Installed WSL game readiness, used by Needle's existing apps safety check."""
from pathlib import Path
import json
import os


def installation():
    root = Path.home() / '.local/share/kilix-wsl'
    return (root / 'current').resolve() if (root / 'current').is_symlink() else root


def catalog(install):
    manifest = install / 'game-catalog.json'
    if manifest.is_file():
        return json.loads(manifest.read_text())
    # Older companion generations contain only Pong and have no catalog.
    return [{'name': 'kilix-pong', 'aliases': ['pong']}]


def game_path(name, install=None):
    install = install or installation()
    for game in catalog(install):
        if name == game['name'] or name in game['aliases']:
            path = install / 'bin' / game['name']
            if path.is_file() and os.access(path, os.X_OK):
                return path
    return None


def installed_games(install=None):
    install = install or installation()
    return sorted(game['name'] for game in catalog(install) if game_path(game['name'], install))


def game_ready(name):
    return game_path(name) is not None


def game_enabled(name):
    return game_ready(name)
