"""Install a fake ``kilix_sdk.panes`` for one test, and take it out again.

Three pane test modules each grew their own copy of this, and the copies
diverged: two never restored ``sys.modules`` at all and the third restored
only keys that were already present, so a stub installed where the real
package had not yet been imported was left behind for the rest of the run.
Under ``unittest discover`` that leaked a ``kilix_sdk`` with an empty
``__path__`` into every later module, and ``test_shared_settings`` and
``test_sdk_boundary`` -- which import the real SDK -- failed with
``cannot import name 'tui_shell' from 'kilix_sdk' (unknown location)``.

The fix is one helper with one restore, used by all three, so there is no
second copy to get subtly wrong.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


_KEYS = ("kilix_sdk", "kilix_sdk.panes")


def _restore(saved: dict[str, object | None]) -> None:
    """Put ``sys.modules`` back exactly, including deleting what was absent."""
    for key, value in saved.items():
        if value is None:
            sys.modules.pop(key, None)      # it was not there; it must not be
        else:
            sys.modules[key] = value        # it was there; put the real one back


#: Names a front end reads off the library rather than calling.  A double that
#: omitted them would fail for a reason the real module never would, so they
#: are copied onto the double from the real module.
_MIRRORED = ("PaneError", "EnginePredatesLocation", "NoSuchTarget",
             "AmbiguousTarget", "PANE_LOCATIONS", "PANE_DIRECTIONS",
             "PANE_DIRECTION_SYNONYMS", "normalize_direction")


ROOT = Path(__file__).resolve().parents[1]

_REAL = None


def real_module():
    """The real ``config/kilix_sdk/panes.py``, loaded by path.

    By path rather than by import: these tests deliberately do not put
    ``config`` on ``sys.path``, and even where they did, a stub may already be
    sitting on ``sys.modules`` under the real name.  Loading the file directly
    is the only way to be sure which module is being read.
    """
    global _REAL
    if _REAL is not None:
        return _REAL
    name = "kilix_sdk_panes_real"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "config" / "kilix_sdk" / "panes.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves sys.modules[cls.__module__] while the class body is
    # executing, so the module has to be registered before exec_module, not
    # after.  Registered under its own private name, so it never shadows the
    # real `kilix_sdk.panes` entry the stubs swap in and out.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    _REAL = module
    return module


def _mirror_constants(stub: object) -> None:
    """Give the double the real module's vocabulary, where it lacks its own."""
    try:
        real = real_module()
    except Exception:                            # nothing to mirror from
        return
    for name in _MIRRORED:
        if not hasattr(stub, name) and hasattr(real, name):
            setattr(stub, name, getattr(real, name))


def install(test, panes: object) -> types.ModuleType:
    """Put ``panes`` on ``sys.modules`` as ``kilix_sdk.panes`` for one test.

    Registers the restore with ``test.addCleanup``, so it runs whether the
    test passes, fails or errors.
    """
    _mirror_constants(panes)
    saved = {key: sys.modules.get(key) for key in _KEYS}
    test.addCleanup(_restore, saved)
    package = types.ModuleType("kilix_sdk")
    package.panes = panes
    package.__path__ = []
    sys.modules["kilix_sdk"] = package
    sys.modules["kilix_sdk.panes"] = panes
    return package


def load_remote(root: Path, name: str):
    """Load ``config/remote.py`` under its own module name."""
    spec = importlib.util.spec_from_file_location(name, root / "config" / "remote.py")
    assert spec is not None and spec.loader is not None
    remote = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(remote)
    return remote


def install_and_load(test, panes: object, root: Path, name: str):
    """The whole dance: stub in, remote.py loaded against it, restore booked."""
    install(test, panes)
    return load_remote(root, name)
