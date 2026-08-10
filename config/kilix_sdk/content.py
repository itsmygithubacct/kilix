"""Pinned application/game content exposed by the Kilix host SDK."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ._packages import load_pinned_package


def _load_shared_package():
    root = Path(__file__).resolve().parents[2]
    return load_pinned_package(
        "kilix_content",
        (
            root / "third_party" / "kilix-content" / "src",
            root.parent / "kilix-modules" / "kilix-content" / "src",
        ),
        "kilix-content is unavailable; initialize Kilix submodules with: "
        "git submodule update --init --recursive",
    )


_shared = _load_shared_package()

Catalog = _shared.Catalog
CatalogError = _shared.CatalogError
ContentSpec = _shared.ContentSpec
InstallError = _shared.InstallError
Installer = _shared.Installer
default_catalog = _shared.default_catalog
download = _shared.download
safe_extract_tar = _shared.safe_extract_tar
safe_extract_zip = _shared.safe_extract_zip
sha256_file = _shared.sha256_file
verify_git_checkout = _shared.verify_git_checkout

_APPLICATION_SURFACES = frozenset(("current", "pane", "window"))
_CUSTOM_APPLICATION_VERBS = {
    # ``custom`` intentionally carries no executable metadata. DOSBox is the
    # one host-owned custom application in the catalog; desktop/games.py owns
    # its verified payload and display-aware launcher.
    "dosbox": ("games", "play"),
}


@dataclass(frozen=True)
class ApplicationPlan:
    """A catalog application's host command and presentation metadata."""

    content_id: str
    label: str
    icon: str
    surface: str
    argv: tuple[str, ...]
    preferred_size: tuple[int, int] | None


def _preferred_size(value: str) -> tuple[int, int] | None:
    if not value:
        return None
    width, height = value.split("x", 1)
    return int(width), int(height)


def application_plan(
    content_id: str,
    surface: str = "current",
    arguments: Iterable[str] = (),
    *,
    catalog=None,
    launcher: str | None = None,
) -> ApplicationPlan:
    """Plan one application for an in-place, pane, or desktop-window host.

    Pane creation belongs to the desktop because Kilix 95 uses XPane while
    Cap, Land, and the TUI use terminal tabs. Once that surface exists, every
    provider executes the same catalog-aware host verb returned here.
    """
    if surface not in _APPLICATION_SURFACES:
        raise ValueError(f"unsupported application surface: {surface!r}")
    if catalog is None:
        catalog = default_catalog()
    spec = catalog.require(content_id)
    if spec.kind != "app":
        raise CatalogError(f"{content_id} is {spec.kind!r} content, not an application")
    if isinstance(arguments, (str, bytes)):
        raise TypeError("application arguments must be an iterable of arguments")
    forwarded = tuple(str(argument) for argument in arguments)
    host = launcher or str(Path(__file__).resolve().parents[2] / "kilix")

    if spec.source_type in ("git", "archive"):
        action = (
            "window"
            if surface == "window" and spec.launch_mode == "terminal"
            else "run"
        )
        argv = (host, "app", action, spec.content_id)
        if forwarded:
            argv += ("--", *forwarded)
    elif spec.source_type == "system":
        argv = (spec.binary or spec.content_id, *forwarded)
    elif spec.source_type == "custom" and spec.content_id in _CUSTOM_APPLICATION_VERBS:
        argv = (
            host,
            *_CUSTOM_APPLICATION_VERBS[spec.content_id],
            spec.content_id,
            *forwarded,
        )
    else:
        raise CatalogError(
            f"{content_id} has no shared launch contract for {spec.source_type!r}"
        )
    return ApplicationPlan(
        content_id=spec.content_id,
        label=spec.label or spec.content_id,
        icon=spec.icon or "app",
        surface=surface,
        argv=argv,
        preferred_size=_preferred_size(spec.preferred_size),
    )


def entries(catalog=None):
    """Every catalog record, ordered by label.

    ``Catalog`` is already iterable, but nothing offered desktops a settled
    order or a safe way to ask for the default catalog, so each one grew its
    own loop -- and Kilix 95 grew a hand-written ID table instead. A desktop
    that has to name its content by hand silently omits anything added later,
    which is exactly the drift this exists to remove.

    Passing ``catalog`` is for tests and for a desktop that has loaded a
    specific catalog file; the default resolves the host's own.
    """
    if catalog is None:
        catalog = default_catalog()
    return sorted(catalog, key=lambda spec: (spec.label or spec.content_id).casefold())


def grouped(catalog=None):
    """Catalog records bucketed by ``kind``, each bucket ordered by label.

    Returns a plain ``dict`` of ``{kind: [ContentSpec, ...]}``. Kinds are
    whatever the catalog declares rather than a fixed set here, so a new kind
    upstream reaches menus without a matching change in every desktop.
    """
    buckets: dict[str, list] = {}
    for spec in entries(catalog):
        buckets.setdefault(spec.kind or "app", []).append(spec)
    return buckets


def menu_records(catalog=None):
    """Catalog records as plain dicts, for desktops that render text menus.

    Deliberately not ``ContentSpec`` objects: a menu generator should be
    testable without importing the content module, and it has no business
    reaching the install machinery hanging off a spec.
    """
    return [
        {
            "id": spec.content_id,
            "label": spec.label or spec.content_id,
            "kind": spec.kind or "app",
            "icon": spec.icon or "",
            "description": spec.description or "",
            "source_type": getattr(spec, "source_type", ""),
            "binary": getattr(spec, "binary", ""),
            "launch_mode": getattr(spec, "launch_mode", "terminal"),
            "preferred_size": getattr(spec, "preferred_size", ""),
            "capabilities": list(getattr(spec, "capabilities", ())),
        }
        for spec in entries(catalog)
    ]

__all__ = [
    "Catalog",
    "CatalogError",
    "ContentSpec",
    "ApplicationPlan",
    "InstallError",
    "Installer",
    "application_plan",
    "default_catalog",
    "download",
    "safe_extract_tar",
    "safe_extract_zip",
    "sha256_file",
    "verify_git_checkout",
]
