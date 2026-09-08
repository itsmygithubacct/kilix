"""Forward the host's catalog installation root to its runtime consumers.

An application's private XDG state is not the catalog root. Nor may a stale
inherited KILIX_CONTENT_ROOT select a different receipt store from the host
installer. Derive the value from the same Kilix storage configuration used
for installation, and pass it explicitly at each process-launch boundary.
"""

from __future__ import annotations

import os

from . import paths


def normalized_root(root: str) -> str:
    """Use the installer's lexical normalization, without creating anything."""
    root = os.fspath(root)
    if not isinstance(root, str) or "\x00" in root or not os.path.isabs(root):
        raise ValueError("content root must be an absolute path")
    return os.path.normpath(root)


def apps_root() -> str:
    return normalized_root(os.path.join(paths.data_dir(), "desktop-apps"))


def launch_environment(*, root: str | None = None, base=None) -> dict[str, str]:
    """Copy an environment and bind it to the actual host installer root."""
    environment = dict(os.environ if base is None else base)
    environment["KILIX_CONTENT_ROOT"] = (
        apps_root() if root is None else normalized_root(root)
    )
    return environment
