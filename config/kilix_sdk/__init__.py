"""Stable, versioned host API for external Kilix clients.

Kilix 95 and other hosted tools should import through this package instead of
reaching into implementation modules such as ``browse`` and ``gfx`` directly.
The first SDK layer is intentionally thin; it names the contract while the
underlying implementations continue to live in the existing host modules.
Providers may use :func:`require_compatible` during import so an unsupported
host fails with an actionable message instead of a later attribute error.
The compatibility promise follows semantic-version major/minor rules: SDK 1.x
keeps the 1.0 contract, while a provider may require a newer 1.y minor.
"""

SDK_VERSION = "1.2.0"
SDK_API_VERSION = (1, 2)
__version__ = SDK_VERSION


class IncompatibleSDKError(RuntimeError):
    """Raised when a provider requires an incompatible Kilix host SDK."""


def require_compatible(required: str = "1.0") -> None:
    """Require ``MAJOR.MINOR`` compatibility with this host SDK."""
    try:
        parts = required.split(".")
        wanted = int(parts[0]), int(parts[1])
    except (AttributeError, IndexError, ValueError) as exc:
        raise IncompatibleSDKError(f"invalid Kilix SDK requirement: {required!r}") from exc
    have = SDK_API_VERSION
    if wanted[0] != have[0] or wanted > have:
        raise IncompatibleSDKError(
            f"desktop provider requires kilix_sdk {wanted[0]}.{wanted[1]}; "
            f"host provides {have[0]}.{have[1]}"
        )


__all__ = [
    "SDK_API_VERSION",
    "SDK_VERSION",
    "IncompatibleSDKError",
    "content",
    "graphics",
    "paths",
    "require_compatible",
    "settings",
    "term",
    "xapp",
]
