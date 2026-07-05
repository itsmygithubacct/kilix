"""kilix desktop — the desktop surface (the shell).

Owns the wallpaper, the icon grid, the launcher files and every "open
something" verb. The desktop folder is a real directory
(~/.local/share/kilix/desktop by default, override with $KILIX_DESKTOP_DIR):
plain files and directories dropped there appear as icons, and "Create
Launcher…" writes freedesktop-style .desktop files there. Programs launch
into new kilix tabs/windows over kitty remote control; X11 apps go through
`kilix run`; URLs through `kilix browse`.
"""
import configparser
import json
import os
import shutil
import stat
import subprocess

from PIL import Image

import icons
import theme as T
import widgets as W
import wm

_here = os.path.dirname(os.path.abspath(__file__))
KILIX_HOME = os.environ.get("KILIX_HOME") or os.path.dirname(_here)

OPEN_MODES = ["kilix tab", "kilix os-window", "kilix run (X11 app)",
              "web browser"]
MODE_KEYS = {"kilix tab": "tab", "kilix os-window": "window",
             "kilix run (X11 app)": "run", "web browser": "browse"}
ICON_CHOICES = ["exe", "terminal", "doc", "doc_text", "doc_image", "folder",
                "computer", "browser", "notepad", "settings", "display",
                "drive", "home", "run", "flame"]

TEXT_EXT = (".txt", ".md", ".rst", ".log", ".conf", ".cfg", ".ini", ".json",
            ".yaml", ".yml", ".toml", ".py", ".sh", ".c", ".h", ".go", ".rs",
            ".js", ".ts", ".html", ".css", ".xml", ".csv", ".diff", ".patch")
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".ppm",
           ".tiff")

WALL_COLORS = [("Teal (classic)", (0, 128, 128)), ("Navy", (0, 0, 128)),
               ("Black", (0, 0, 0)), ("Gray", (128, 128, 128)),
               ("Green", (0, 128, 0)), ("Plum", (128, 0, 128)),
               ("Maroon", (128, 0, 0)), ("Steel", (60, 90, 120))]


class Shell:
    def __init__(self, desk):
        self.desk = desk
        self.dir = os.path.expanduser(
            os.environ.get("KILIX_DESKTOP_DIR")
            or os.path.join(os.environ.get("XDG_DATA_HOME")
                            or "~/.local/share", "kilix", "desktop"))
        self.dir = os.path.expanduser(self.dir)
        os.makedirs(self.dir, exist_ok=True)
        self.state_path = os.path.join(self.dir, ".state.json")
        self.state = {"wall_color": list(T.DESKTOP), "wall_image": None,
                      "wall_mode": "stretch", "recent": []}
        try:
            with open(self.state_path) as f:
                self.state.update(json.load(f))
        except (OSError, ValueError):
            pass
        self._wall = None             # cached composited wallpaper
        sw, sh = desk.size()
        self.grid = W.IconGrid(0, 0, sw, sh - T.TASKBAR_H,
                               on_activate=self._activate,
                               on_context=self._context, desktop=True)
        self.grid.window = self       # duck-typed: needs .invalidate/.desk
        self.focus = None             # part of the window duck-type
        self.caret_on = False
        self.refresh()

    # window duck-type used by IconGrid
    def invalidate(self):
        self.desk.dirty = True

    def _save_state(self):
        try:
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=1)
        except OSError:
            pass

    # ── the icons ───────────────────────────────────────────────────────────
    def refresh(self):
        items = [
            {"label": "My Computer", "icon": "computer",
             "data": ("builtin", ("filemgr", "/"))},
            {"label": "Home", "icon": "home",
             "data": ("builtin", ("filemgr", os.path.expanduser("~")))},
            {"label": "kilix Settings", "icon": "settings",
             "data": ("builtin", ("settings", None))},
            {"label": "Terminal", "icon": "terminal",
             "data": ("builtin", ("terminal", None))},
        ]
        try:
            names = sorted(os.listdir(self.dir), key=str.lower)
        except OSError:
            names = []
        for n in names:
            if n.startswith("."):
                continue
            p = os.path.join(self.dir, n)
            if n.endswith(".desktop"):
                spec = parse_launcher(p)
                items.append({"label": spec.get("Name") or n[:-8],
                              "icon": spec.get("Icon") or "exe",
                              "shortcut": True, "data": ("launcher", p)})
            else:
                isdir = os.path.isdir(p)
                items.append({"label": n,
                              "icon": icons.for_path(p, isdir),
                              "data": ("path", p)})
        self.grid.set_items(items)
        self.invalidate()

    def on_resize(self):
        sw, sh = self.desk.size()
        self.grid.w, self.grid.h = sw, sh - T.TASKBAR_H
        self._wall = None
        self.invalidate()

    # ── drawing ─────────────────────────────────────────────────────────────
    def draw(self, fb, d):
        sw, sh = self.desk.size()
        if self._wall is None or self._wall.size != (sw, sh):
            self._wall = self._build_wall(sw, sh)
        fb.paste(self._wall, (0, 0))
        self.grid.draw(d, fb)

    def _build_wall(self, sw, sh):
        img = Image.new("RGB", (sw, sh), tuple(self.state["wall_color"]))
        path = self.state.get("wall_image")
        if path:
            try:
                pic = Image.open(os.path.expanduser(path)).convert("RGB")
                mode = self.state.get("wall_mode", "stretch")
                if mode == "stretch":
                    img.paste(pic.resize((sw, sh - T.TASKBAR_H)), (0, 0))
                elif mode == "tile":
                    for ty in range(0, sh, pic.height):
                        for tx in range(0, sw, pic.width):
                            img.paste(pic, (tx, ty))
                else:                 # center
                    img.paste(pic, ((sw - pic.width) // 2,
                                    (sh - T.TASKBAR_H - pic.height) // 2))
            except OSError:
                pass
        return img

    # ── input ───────────────────────────────────────────────────────────────
    def on_mouse(self, gev):
        return self.grid.on_mouse(gev)

    def on_key(self, ev):
        if self.grid.on_key(ev):
            return True
        if ev.key == "F5":
            self.refresh()
            return True
        if ev.key == "Delete":
            sel = self.grid.selected_items()
            if sel:
                self._delete_items(sel)
            return True
        if ev.key == "F2":
            sel = self.grid.selected_items()
            if sel:
                self._rename_item(sel[0])
            return True
        return False

    # ── activation / context menus ──────────────────────────────────────────
    def _activate(self, item):
        kind, arg = item["data"]
        if kind == "builtin":
            app, param = arg
            if app == "terminal":
                self.open_terminal()
            else:
                self.open_app(app, param)
        elif kind == "launcher":
            self.launch(parse_launcher(arg), arg)
        else:
            self.open_path(arg)

    def _context(self, item, ev):
        MI, sep = W.MenuItem, W.sep
        if item is not None:
            kind, arg = item["data"]
            items = [MI("Open", action=lambda: self._activate(item))]
            if kind == "launcher":
                items.append(MI("Edit Launcher…",
                                action=lambda: self.create_launcher_dialog(
                                    edit_path=arg)))
            if kind == "path" and not os.path.isdir(arg):
                items.append(MI("Open with Notepad", icon="notepad",
                                action=lambda: self.open_app("notepad", arg)))
            if kind in ("launcher", "path"):
                items += [sep(),
                          MI("Rename…", action=lambda: self._rename_item(item)),
                          MI("Delete…", action=lambda: self._delete_items(
                              [item]))]
            if kind == "path":
                items.append(MI("Create Launcher…", icon="exe",
                                action=lambda: self.create_launcher_dialog(
                                    prefill_cmd=arg)))
        else:
            items = [
                MI("New Launcher…", icon="exe",
                   action=self.create_launcher_dialog),
                MI("New Folder…", icon="folder", action=self._new_folder),
                MI("New Text File…", icon="doc_text", action=self._new_file),
                sep(),
                MI("Refresh", action=self.refresh),
                MI("Open Desktop Folder", icon="folder_open",
                   action=lambda: self.open_app("filemgr", self.dir)),
                sep(),
                MI("Display…", icon="display", action=self.display_properties),
                MI("About kilix 95…", icon="flame", action=self.about_dialog),
            ]
        self.desk.menus.open(items, ev.x, ev.y)

    # ── file ops on the desktop folder ──────────────────────────────────────
    def _writable(self, item):
        return item["data"][0] in ("launcher", "path")

    def _rename_item(self, item):
        if not self._writable(item):
            wm.msgbox(self.desk, "Desktop", "System icons cannot be renamed.",
                      icon="info")
            return
        kind, path = item["data"]

        def do(name):
            if not name:
                return
            if kind == "launcher":
                spec = parse_launcher(path)
                spec["Name"] = name
                write_launcher(path, spec)
            else:
                try:
                    os.rename(path, os.path.join(self.dir, name))
                except OSError as e:
                    wm.msgbox(self.desk, "Rename", str(e), icon="error")
            self.refresh()

        wm.inputbox(self.desk, "Rename", "New name:", item["label"], cb=do)

    def _delete_items(self, sel):
        real = [i for i in sel if self._writable(i)]
        if not real:
            wm.msgbox(self.desk, "Desktop", "System icons cannot be deleted.",
                      icon="info")
            return
        names = ", ".join(i["label"] for i in real[:4]) + (
            "…" if len(real) > 4 else "")

        def do(answer):
            if answer != "Yes":
                return
            for it in real:
                p = it["data"][1]
                try:
                    if os.path.isdir(p) and not os.path.islink(p):
                        shutil.rmtree(p)
                    else:
                        os.unlink(p)
                except OSError as e:
                    wm.msgbox(self.desk, "Delete", str(e), icon="error")
            self.refresh()

        wm.msgbox(self.desk, "Confirm Delete",
                  f"Delete {names}?\nThis cannot be undone.",
                  icon="warn", buttons=("Yes", "No"), default=1, cb=do)

    def _new_folder(self):
        def do(name):
            if name:
                try:
                    os.makedirs(os.path.join(self.dir, name), exist_ok=False)
                except OSError as e:
                    wm.msgbox(self.desk, "New Folder", str(e), icon="error")
                self.refresh()
        wm.inputbox(self.desk, "New Folder", "Folder name:", "New Folder",
                    cb=do, icon="folder")

    def _new_file(self):
        def do(name):
            if name:
                p = os.path.join(self.dir, name)
                try:
                    open(p, "x").close()
                except OSError as e:
                    wm.msgbox(self.desk, "New File", str(e), icon="error")
                self.refresh()
        wm.inputbox(self.desk, "New Text File", "File name:", "New File.txt",
                    cb=do, icon="doc_text")

    # ── launchers ───────────────────────────────────────────────────────────
    def launcher_menu_items(self):
        out = []
        try:
            names = sorted(os.listdir(self.dir), key=str.lower)
        except OSError:
            names = []
        for n in names:
            if not n.endswith(".desktop"):
                continue
            p = os.path.join(self.dir, n)
            spec = parse_launcher(p)
            out.append(W.MenuItem(spec.get("Name") or n[:-8],
                                  icon=spec.get("Icon") or "exe",
                                  action=lambda s=spec, p=p: self.launch(s, p)))
        return out

    def create_launcher_dialog(self, prefill_cmd=None, edit_path=None):
        """The Create Launcher wizard (also edits existing launchers)."""
        desk = self.desk
        spec = parse_launcher(edit_path) if edit_path else {}
        win = wm.Window(desk, "Edit Launcher" if edit_path
                        else "Create Launcher", 380, 262, icon="exe",
                        resizable=False, modal=True)
        cw = win.client_size()[0]
        y = 12
        win.add(W.Label(12, y + 3, "Name:"))
        f_name = win.add(W.TextField(90, y, cw - 102,
                                     spec.get("Name", "")))
        y += 28
        win.add(W.Label(12, y + 3, "Command:"))
        cmd0 = spec.get("URL") or spec.get("Exec") or prefill_cmd or ""
        f_cmd = win.add(W.TextField(90, y, cw - 102, cmd0))
        y += 28
        win.add(W.Label(12, y + 3, "Start in:"))
        f_dir = win.add(W.TextField(90, y, cw - 102, spec.get("Path", "")))
        y += 28
        win.add(W.Label(12, y + 3, "Open in:"))
        mode0 = spec.get("X-Kilix-Open", "tab")
        if spec.get("Type") == "Link":
            mode0 = "browse"
        rev = {v: k for k, v in MODE_KEYS.items()}
        d_mode = win.add(W.Dropdown(90, y, cw - 102, OPEN_MODES,
                                    OPEN_MODES.index(rev.get(mode0,
                                                             "kilix tab"))))
        y += 28
        win.add(W.Label(12, y + 3, "Icon:"))
        icon0 = spec.get("Icon", "exe")
        d_icon = win.add(W.Dropdown(90, y, cw - 150, ICON_CHOICES,
                                    ICON_CHOICES.index(icon0)
                                    if icon0 in ICON_CHOICES else 0))

        class _Preview(W.Widget):
            def __init__(self):
                super().__init__(cw - 36, y - 6, 32, 32)

            def draw(self, d, img):
                icons.paint(img, d_icon.value, self.x, self.y, 32,
                            shortcut=True)

        win.add(_Preview())
        d_icon.cb = lambda *_: win.invalidate()
        y += 34
        hint = "Command runs in a new kilix tab; pick another mode above."
        win.add(W.Label(12, y, hint, font=T.SMALL, color=T.SHADOW))

        def save():
            name = f_name.text.strip() or "Launcher"
            cmd = f_cmd.text.strip()
            if not cmd:
                wm.msgbox(desk, "Create Launcher",
                          "A command (or URL) is required.", icon="warn")
                return
            mode = MODE_KEYS[d_mode.value]
            out = {"Name": name, "Icon": d_icon.value}
            if mode == "browse":
                out.update({"Type": "Link", "URL": cmd})
            else:
                out.update({"Type": "Application", "Exec": cmd,
                            "X-Kilix-Open": mode})
                if f_dir.text.strip():
                    out["Path"] = f_dir.text.strip()
            path = edit_path or unique_path(
                os.path.join(self.dir, safe_name(name) + ".desktop"))
            write_launcher(path, out)
            win.close()
            self.refresh()

        ch = win.client_size()[1]
        win.add(W.Button(cw - 164, ch - 33, 72, 23, "OK", cb=save,
                         default=True))
        win.add(W.Button(cw - 84, ch - 33, 72, 23, "Cancel", cb=win.close))
        win.set_focus(f_name)
        desk.wm.add(win)

    def launch(self, spec, path=None):
        name = spec.get("Name") or "app"
        if spec.get("Type") == "Link" or spec.get("URL"):
            self.open_url(spec.get("URL"))
            return
        cmd = spec.get("Exec", "")
        if not cmd:
            wm.msgbox(self.desk, name, "Launcher has no Exec line.",
                      icon="error")
            return
        mode = spec.get("X-Kilix-Open", "tab")
        cwd = os.path.expanduser(spec.get("Path") or "~")
        if mode == "run":
            self._tab([os.path.join(KILIX_HOME, "kilix"), "run"]
                      + split_cmd(cmd), name, cwd)
        elif mode == "window":
            self._spawn_kitty_launch(["--type=os-window"], cmd, name, cwd)
        else:
            self._spawn_kitty_launch(["--type=tab"], cmd, name, cwd)

    # ── spawning into kilix ─────────────────────────────────────────────────
    def _kitten(self):
        k = os.environ.get("KILIX_KITTEN")
        if k and os.access(k, os.X_OK):
            return k
        for cand in (os.path.join(KILIX_HOME, "src/kitty/launcher/kitten"),
                     os.path.join(KILIX_HOME, "kitty.app/bin/kitten"),
                     shutil.which("kitten")):
            if cand and os.access(cand, os.X_OK):
                return cand
        return None

    def _spawn_kitty_launch(self, opts, cmd, title, cwd=None):
        """Run shell command `cmd` in a new kilix tab/window."""
        kitten = self._kitten()
        if not kitten or not os.environ.get("KITTY_LISTEN_ON"):
            wm.msgbox(self.desk, "kilix",
                      "Cannot reach kilix remote control\n"
                      "(KITTY_LISTEN_ON is not set).", icon="error")
            return
        argv = [kitten, "@", "launch", *opts, "--tab-title", title,
                "--cwd", cwd or os.path.expanduser("~"), "--",
                "bash", "-lc", f'{cmd}; rc=$?; [ $rc -ne 0 ] && '
                f'{{ echo; echo "[exit $rc — Enter to close]"; read -r; }}']
        self._popen(argv)

    def _tab(self, argv, title, cwd=None):
        kitten = self._kitten()
        if not kitten or not os.environ.get("KITTY_LISTEN_ON"):
            wm.msgbox(self.desk, "kilix", "Cannot reach kilix remote control\n"
                      "(KITTY_LISTEN_ON is not set).", icon="error")
            return
        self._popen([kitten, "@", "launch", "--type=tab", "--tab-title",
                     title, "--cwd", cwd or os.path.expanduser("~"), "--"]
                    + argv)

    def _popen(self, argv, cwd=None):
        try:
            subprocess.Popen(argv, cwd=cwd, start_new_session=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL)
        except OSError as e:
            wm.msgbox(self.desk, "kilix", f"Launch failed:\n{e}",
                      icon="error")

    def open_terminal(self, cwd=None):
        kitten = self._kitten()
        if not kitten or not os.environ.get("KITTY_LISTEN_ON"):
            wm.msgbox(self.desk, "kilix", "Cannot reach kilix remote control\n"
                      "(KITTY_LISTEN_ON is not set).", icon="error")
            return
        self._popen([kitten, "@", "launch", "--type=tab", "--tab-title",
                     "Terminal", "--cwd", cwd or os.path.expanduser("~")])

    def open_url(self, url):
        if url is None:
            wm.inputbox(self.desk, "Web Browser", "Address:", "https://",
                        cb=lambda u: u and self.open_url(u), icon="browser")
            return
        self._tab([os.path.join(KILIX_HOME, "kilix"), "browse", url],
                  "browse", None)

    def open_path(self, path, from_app=None):
        """The desktop's 'what do I do with this file' verb."""
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            self.open_app("filemgr", path)
            return
        low = path.lower()
        if low.endswith(".desktop"):
            self.launch(parse_launcher(path), path)
            return
        if low.endswith(IMG_EXT):
            self.open_app("viewer", path)
        elif low.endswith(TEXT_EXT) or self._looks_texty(path):
            self.open_app("notepad", path)
        elif os.access(path, os.X_OK):
            def do(ans):
                if ans == "Run":
                    self._spawn_kitty_launch(["--type=tab"],
                                             shell_quote(path),
                                             os.path.basename(path))
                elif ans == "Notepad":
                    self.open_app("notepad", path)
            wm.msgbox(self.desk, os.path.basename(path),
                      "This file is executable. Run it in a kilix tab?",
                      icon="question", buttons=("Run", "Notepad", "Cancel"),
                      cb=do)
        else:
            def do2(ans):
                if ans == "Notepad":
                    self.open_app("notepad", path)
            wm.msgbox(self.desk, os.path.basename(path),
                      "No association for this file type.",
                      icon="question", buttons=("Notepad", "Cancel"), cb=do2)
        self.add_recent(path)

    @staticmethod
    def _looks_texty(path):
        try:
            with open(path, "rb") as f:
                chunk = f.read(2048)
            return b"\0" not in chunk
        except OSError:
            return False

    def open_app(self, app, arg=None):
        import apps
        try:
            apps.open(self.desk, app, arg)
        except Exception as e:            # an app must never take the desk down
            wm.msgbox(self.desk, "kilix 95", f"{app}: {e}", icon="error")

    # ── recents ─────────────────────────────────────────────────────────────
    def add_recent(self, path):
        r = [p for p in self.state.get("recent", []) if p != path]
        r.insert(0, path)
        self.state["recent"] = r[:10]
        self._save_state()

    def recent_docs(self):
        return [(os.path.basename(p), p) for p in self.state.get("recent", [])
                if os.path.exists(p)]

    # ── dialogs ─────────────────────────────────────────────────────────────
    def run_dialog(self):
        def do(cmd):
            if cmd:
                self._spawn_kitty_launch(["--type=tab"], cmd,
                                         split_cmd(cmd)[0] if cmd else "run")
        wm.inputbox(self.desk, "Run",
                    "Type the name of a program to open it in a kilix tab:",
                    "", cb=do, icon="run", width=320)

    def shutdown_dialog(self):
        def do(ans):
            if ans == "Shut Down":
                self.desk.quit()
        wm.msgbox(self.desk, "Shut Down kilix 95",
                  "Are you sure you want to shut down the desktop?\n"
                  "(Your kilix terminal stays running.)",
                  icon="shutdown", buttons=("Shut Down", "Cancel"), cb=do)

    def about_dialog(self):
        wm.msgbox(self.desk, "About kilix 95",
                  "kilix 95\nA Windows 95-style desktop for kilix.\n\n"
                  "Rendered as pixels over the kitty graphics protocol.\n"
                  "All artwork drawn in-house — no Redmond bits inside.",
                  icon="flame")

    def display_properties(self):
        desk = self.desk
        win = wm.Window(desk, "Display Properties", 380, 250, icon="display",
                        resizable=False, modal=True)
        cw, ch = win.client_size()
        win.add(W.GroupBox(10, 6, cw - 20, 96, "Background color"))
        sw_size, gap = 34, 8
        cur = list(self.state["wall_color"])

        class _Swatch(W.Widget):
            def __init__(s, i, col, x, y):
                super().__init__(x, y, sw_size, sw_size)
                s.col = col

            def draw(s, d, img):
                sel = list(s.col) == cur
                T.sunken(d, s.x, s.y, s.x + s.w - 1, s.y + s.h - 1,
                         fill=s.col)
                if sel:
                    T.focus_rect(d, s.x - 2, s.y - 2, s.x + s.w + 1,
                                 s.y + s.h + 1)

            def on_mouse(s, ev):
                if ev.press:
                    cur[:] = list(s.col)
                    win.invalidate()
                return True

        x = 22
        for i, (name, col) in enumerate(WALL_COLORS):
            win.add(_Swatch(i, col, x, 26))
            x += sw_size + gap
        win.add(W.Label(22, 70, "Wallpaper (PNG/JPG path, optional):"))
        f_img = win.add(W.TextField(22, 108, cw - 130,
                                    self.state.get("wall_image") or ""))
        modes = ["stretch", "tile", "center"]
        d_mode = win.add(W.Dropdown(cw - 100, 108, 78, modes,
                                    modes.index(self.state.get("wall_mode",
                                                               "stretch"))))

        def apply(close=False):
            self.state["wall_color"] = cur
            self.state["wall_image"] = f_img.text.strip() or None
            self.state["wall_mode"] = d_mode.value
            self._save_state()
            self._wall = None
            self.invalidate()
            if close:
                win.close()

        win.add(W.Button(cw - 244, ch - 33, 72, 23, "OK", default=True,
                         cb=lambda: apply(True)))
        win.add(W.Button(cw - 164, ch - 33, 72, 23, "Cancel", cb=win.close))
        win.add(W.Button(cw - 84, ch - 33, 72, 23, "Apply", cb=apply))
        desk.wm.add(win)


# ── launcher file helpers ────────────────────────────────────────────────────

def parse_launcher(path):
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    out = {}
    try:
        cp.read(path)
        if cp.has_section("Desktop Entry"):
            out = dict(cp["Desktop Entry"])
    except (OSError, configparser.Error):
        pass
    return out


def write_launcher(path, spec):
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    cp["Desktop Entry"] = {"Version": "1.0", **spec}
    with open(path, "w") as f:
        cp.write(f)


def safe_name(name):
    return "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name)


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def split_cmd(cmd):
    import shlex
    try:
        return shlex.split(cmd) or [cmd]
    except ValueError:
        return [cmd]


def shell_quote(s):
    import shlex
    return shlex.quote(s)
