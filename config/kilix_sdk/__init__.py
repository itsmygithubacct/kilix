"""Stable host API for external Kilix clients.

Kilix 95 and other hosted tools should import through this package instead of
reaching into implementation modules such as ``browse`` and ``gfx`` directly.
The first SDK layer is intentionally thin; it names the contract while the
underlying implementations continue to live in the existing host modules.
"""

__all__ = ["graphics", "paths", "term"]
