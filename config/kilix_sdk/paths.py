"""Path discovery for the Kilix host checkout."""

import os
from pathlib import Path


def kilix_home() -> str:
    """Return the absolute path to the Kilix host checkout."""
    env = os.environ.get("KILIX_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return str(Path(__file__).resolve().parents[2])


def config_dir() -> str:
    """Return the Kilix host config directory."""
    return os.path.join(kilix_home(), "config")


def launcher() -> str:
    """Return the Kilix launcher path."""
    return os.path.join(kilix_home(), "kilix")


def kitten_candidates() -> tuple[str, str]:
    """Return known kitten launcher candidates for the host engine."""
    home = kilix_home()
    return (
        os.path.join(home, "src", "kitty", "launcher", "kitten"),
        os.path.join(home, "kitty.app", "bin", "kitten"),
    )


__all__ = ["config_dir", "kilix_home", "kitten_candidates", "launcher"]
