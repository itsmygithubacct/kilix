"""Kitty graphics helpers for Kilix-hosted pixel applications."""

import gfx as _gfx

CHUNK = _gfx.CHUNK
build_direct = _gfx.build_direct
blit_direct = _gfx.blit_direct


def wrap_tmux_passthrough(apc: str) -> str:
    """Wrap one APC escape for tmux passthrough."""
    return _gfx._tmux_wrap(apc)


__all__ = ["CHUNK", "build_direct", "blit_direct", "wrap_tmux_passthrough"]
