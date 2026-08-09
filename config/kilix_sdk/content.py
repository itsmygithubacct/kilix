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
