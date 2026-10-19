"""Installed WSL game readiness, used by Needle's existing apps safety check."""
from pathlib import Path
import json
import os
import sys

# Needle imports this shim from wsl-bin/config; the catalog lives in wsl/.
_support = Path(__file__).resolve().parents[2] / "wsl"
if (_support / "catalog_store.py").is_file():
    sys.path.insert(0, str(_support))
from catalog_store import Catalog, find, read_state


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
    manager = Catalog(install=install, runtime='wsl')
    try:
        spec = find(name, 'games')
    except ValueError:
        return None
    if not manager.enabled(spec):
        return None
    generation = manager.generation(spec)
    if generation is not None:
        path = generation / 'bin' / spec['name']
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def installed_games(install=None):
    install = install or installation()
    names = {game['name'] for game in catalog(install)}
    manager = Catalog(install=install, runtime='wsl')
    names.update(read_state(manager.root)['installed'])
    return sorted(name for name in names if game_path(name, install))


def game_ready(name):
    return game_path(name) is not None


def game_enabled(name):
    try:
        return Catalog(runtime='wsl').enabled(find(name, 'games'))
    except ValueError:
        return False
