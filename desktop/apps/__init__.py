"""kilix desktop — built-in apps.

apps.open(desk, name, arg) is the only entry point the shell uses; each app
is a wm.Window subclass in its own module. Settings is a singleton (opening
it again focuses the existing window); everything else opens fresh.
"""


def open(desk, name, arg=None):
    if name == "filemgr":
        from . import filemgr
        desk.wm.add(filemgr.FileWindow(desk, arg or "~"))
    elif name == "notepad":
        from . import notepad
        desk.wm.add(notepad.Notepad(desk, arg))
    elif name == "viewer":
        from . import viewer
        desk.wm.add(viewer.Viewer(desk, arg))
    elif name == "amp":
        from . import amp
        amp.open_amp(desk, arg)
    elif name == "settings":
        from . import settings
        for w in desk.wm.windows:
            if isinstance(w, settings.SettingsWin):
                desk.wm.activate(w)
                return
        desk.wm.add(settings.SettingsWin(desk))
    else:
        raise ValueError(f"unknown app {name!r}")
