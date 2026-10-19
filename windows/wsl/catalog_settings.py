"""Use the shared Linux game preferences in both source and staged installs."""
from functools import lru_cache
import importlib.util
from pathlib import Path
import sys


@lru_cache(maxsize=1)
def shared_settings():
    here = Path(__file__).resolve().parent
    for path in (here.parent / 'wsl-bin/config/kilix_sdk/settings.py',
                 here.parents[1] / 'config/kilix_sdk/settings.py'):
        if path.is_file():
            spec = importlib.util.spec_from_file_location('_catalog_shared_settings', path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    # Older standalone companion packages did not include the shared SDK.
    return None


def game_key(settings, spec):
    if settings:
        for name in (spec['name'], *spec.get('aliases', [])):
            if name in settings.GAME_KEY_BY_ID:
                return settings.GAME_KEY_BY_ID[name]
    return None


def enabled(spec, fallback):
    settings = shared_settings()
    key = game_key(settings, spec)
    if key is None:
        return fallback
    text, exists = settings.read_text()
    # An explicit shared preference supersedes the older catalog-only state.
    # Until then, retain an existing catalog disable through this migration.
    if exists and key in settings.parse_text(text):
        return settings.enabled(key)
    return fallback and settings.enabled(key)


def set_enabled(spec, value):
    settings = shared_settings()
    key = game_key(settings, spec)
    if key is None:
        return False
    settings.update({key: '1' if value else '0'})
    return True
