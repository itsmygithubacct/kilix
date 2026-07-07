"""Start menu surfaces discovered freedesktop apps under the right buckets.

Points XDG_DATA_HOME at a throwaway temp dir with a few sample .desktop files
(no real system paths, no personal data), opens the Start menu offscreen, and
walks the built MenuItem tree to assert placement + callable actions.
"""
import os
import tempfile

import harness
import widgets as W
import xdgapps


def _write_app(apps_dir, fname, name, cats, exec_="/usr/bin/true"):
    with open(os.path.join(apps_dir, fname), "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=%s\nExec=%s\n"
                "Categories=%s;\n" % (name, exec_, cats))


def _find(items, label):
    for it in items:
        if it.label == label:
            return it
    return None


def _labels(items):
    return [it.label for it in items if it.label != "-"]


def main():
    home = tempfile.mkdtemp(prefix="kilix95-xdgmenu-home-")
    sysd = tempfile.mkdtemp(prefix="kilix95-xdgmenu-sys-")
    apps = os.path.join(home, "applications")
    os.makedirs(apps, exist_ok=True)
    os.makedirs(os.path.join(sysd, "applications"), exist_ok=True)
    _write_app(apps, "web.desktop", "Sample Browser", "Network")
    _write_app(apps, "pix.desktop", "Sample Paint", "Graphics")
    _write_app(apps, "gam.desktop", "Sample Game", "Game")
    _write_app(apps, "acc.desktop", "Sample Tool", "Utility")
    os.environ["XDG_DATA_HOME"] = home
    os.environ["XDG_DATA_DIRS"] = sysd
    xdgapps.scan(force=True)

    desk = harness.make_desk()
    desk.taskbar.open_start_menu()
    top = desk.menus.stack[0].items

    programs = _find(top, "Programs")
    assert programs is not None and programs.submenu, "no Programs submenu"
    prog = programs.submenu

    # Games app rides in the Games submenu
    games = _find(prog, "Games").submenu
    g = _find(games, "Sample Game")
    assert g is not None, _labels(games)
    assert callable(g.action)

    # Accessories app rides in the Accessories submenu
    acc = _find(prog, "Accessories").submenu
    a = _find(acc, "Sample Tool")
    assert a is not None, _labels(acc)
    assert a.icon == "app", a.icon        # generic fallback icon

    # Graphics + Internet each get their own Programs submenu
    graphics = _find(prog, "Graphics")
    assert graphics is not None and graphics.submenu, _labels(prog)
    assert _find(prog, "Internet").submenu[0].label == "Sample Browser"
    net = _find(prog, "Internet").submenu[0]
    assert net.icon == "browser", net.icon
    assert callable(net.action)

    # empty buckets never appear
    assert _find(prog, "Office") is None
    assert _find(prog, "Development") is None
    desk.menus.close_all()

    # with nothing discovered, the menu looks exactly as before
    empty = tempfile.mkdtemp(prefix="kilix95-xdgmenu-empty-")
    os.environ["XDG_DATA_HOME"] = empty
    os.environ["XDG_DATA_DIRS"] = "/kilix/does/not/exist"
    xdgapps.scan(force=True)
    desk.taskbar.menu_open = -1
    desk.taskbar.open_start_menu()
    prog2 = _find(desk.menus.stack[0].items, "Programs").submenu
    assert _find(prog2, "Graphics") is None
    assert _find(prog2, "Internet") is None
    # built-ins still there; discovered games/accessories gone
    assert _find(_find(prog2, "Games").submenu, "Sample Game") is None
    assert _find(prog2, "Media Player") is not None
    print("ok")


main()
