"""Kitty graphics helpers for Kilix-hosted pixel applications."""

import gfx as _gfx

CHUNK = _gfx.CHUNK
build_direct = _gfx.build_direct
blit_direct = _gfx.blit_direct
# damage (tiled/partial) updates: diff consecutive frames into a changed row
# band and edit just that rect of the displayed image (a=f frame edits)
diff_band = _gfx.diff_band
build_frame_edit = _gfx.build_frame_edit
blit_frame_edit = _gfx.blit_frame_edit
build_frame_edit_file = _gfx.build_frame_edit_file


def wrap_tmux_passthrough(apc: str) -> str:
    """Wrap one APC escape for tmux passthrough."""
    return _gfx._tmux_wrap(apc)


__all__ = ["CHUNK", "build_direct", "blit_direct", "diff_band",
           "build_frame_edit", "blit_frame_edit", "build_frame_edit_file",
           "wrap_tmux_passthrough"]
