"""kilix desktop — Media Player: kilix-amp (Winamp 2.x clone) in an XPane.

Unlike the games (which live in kilix tabs), the media player opens INSIDE
the desktop: an SDL2 app on a private X server, streamed into a kilix 95
window. First run clones and builds github.com/itsmygithubacct/kilix-amp
via the InstallerWindow; the layout/config it saves is kept private under
its install dir so it never fights the user's own kilix-amp setup.
"""
import os

import wm
from . import xpane


def open_amp(desk, path=None):
    import games
    exe = games.amp_ready()
    if exe:
        _spawn(desk, exe, path)
        return

    def answered(ans):
        if ans == "Install":
            desk.wm.add(xpane.InstallerWindow(
                desk, "kilix-amp", "Media Player",
                on_ok=lambda: _spawn(desk, games.amp_ready(), path)))

    wm.msgbox(desk, "Media Player",
              "The media player isn't built yet.\n\n"
              "Clone and build kilix-amp (a Winamp 2.x clone,\n"
              "github.com/itsmygithubacct/kilix-amp) into\n"
              "~/.local/share/kilix/apps?\n"
              "(Needs libsdl2-dev, libsdl2-image-dev,\n"
              "libsndfile1-dev and zlib1g-dev to compile.)",
              icon="amp", buttons=("Install", "Cancel"), cb=answered)


def _spawn(desk, exe, path=None):
    if not exe:
        return
    d = os.path.dirname(exe)
    cmd = [exe] + ([os.path.abspath(os.path.expanduser(path))] if path
                   else [])
    desk.wm.add(xpane.XPane(
        desk, cmd, "Media Player", icon="amp",
        # no app_size: the region fills the desktop working area, so the
        # skin can be dragged anywhere like Winamp and its stacked windows
        # (EQ / playlist) are never clipped
        # private, persistent config: window layout survives sessions and
        # never collides with a user-level kilix-amp install
        env={"XDG_CONFIG_HOME": os.path.join(d, ".xpane-config")},
        cwd=d))
