"""Pinned application/game content exposed by the Kilix host SDK."""

from __future__ import annotations

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
        }
        for spec in entries(catalog)
    ]

__all__ = [
    "Catalog",
    "CatalogError",
    "ContentSpec",
    "InstallError",
    "Installer",
    "default_catalog",
    "download",
    "safe_extract_tar",
    "safe_extract_zip",
    "sha256_file",
    "verify_git_checkout",
]
