"""kilix desktop — the file manager (Explorer, at heart).

Toolbar (back/forward/up + address bar), large-icon grid, menu bar, status
bar. Opening a file defers to the shell's open_path verb, so text lands in
Notepad, images in the viewer, launchers launch, and executables prompt.
"""
import os
import shutil
import stat
import time

import icons
import shell as _shell
import theme as T
import widgets as W
import wm

TB_Y = T.MENU_H + 2                  # toolbar row (below the menu bar)
TB_H = 26
STATUS_H = 20


class FileWindow(wm.Window):
    def __init__(self, desk, path):
        super().__init__(desk, "File Manager", 560, 400, icon="folder_open")
        self.min_w, self.min_h = 340, 220
        self.hist, self.hist_i = [], -1
        self.show_hidden = False
        cw, ch = self.client_size()
        self.menubar = self.add(W.MenuBar(cw, [
            ("File", self._file_menu), ("View", self._view_menu),
            ("Help", self._help_menu)]))
        bx = 4
        self.b_back = self.add(W.Button(bx, TB_Y + 2, 24, 22, icon="back",
                                        cb=lambda: self._go(-1)))
        self.b_fwd = self.add(W.Button(bx + 26, TB_Y + 2, 24, 22,
                                       icon="forward",
                                       cb=lambda: self._go(+1)))
        self.b_up = self.add(W.Button(bx + 52, TB_Y + 2, 24, 22, icon="up",
                                      cb=self._up))
        self.addr = self.add(W.TextField(bx + 84, TB_Y + 3, cw - bx - 92,
                                         on_enter=self._addr_enter))
        self.grid = self.add(W.IconGrid(2, TB_Y + TB_H + 2, cw - 4,
                                        ch - TB_Y - TB_H - STATUS_H - 4,
                                        on_activate=self._activate,
                                        on_context=self._context))
        self.set_focus(self.grid)
        self.navigate(os.path.expanduser(path))

    # ── layout / chrome ─────────────────────────────────────────────────────
    def on_resize(self):
        cw, ch = self.client_size()
        self.menubar.w = cw
        self.addr.w = cw - self.addr.x - 8
        self.grid.w, self.grid.h = cw - 4, ch - TB_Y - TB_H - STATUS_H - 4

    def draw_client(self, d, img):
        cw, ch = self.client_size()
        T.raised_thin(d, 0, TB_Y, cw - 1, TB_Y + TB_H - 1)
        n = len(self.grid.items)
        sel = len(self.grid.sel)
        msg = f"{n} object(s)" + (f"   ({sel} selected)" if sel else "")
        T.sunken(d, 2, ch - STATUS_H, cw - 3, ch - 3, fill=T.FACE)
        d.text((8, ch - STATUS_H + 3), msg, font=T.FONT, fill=T.TEXT)

    # ── navigation ──────────────────────────────────────────────────────────
    def navigate(self, path, from_hist=False):
        path = os.path.abspath(os.path.expanduser(path or "/"))
        try:
            names = os.listdir(path)
        except OSError as e:
            wm.msgbox(self.desk, "File Manager", str(e), icon="error")
            return
        if not from_hist:
            self.hist = self.hist[:self.hist_i + 1] + [path]
            self.hist_i = len(self.hist) - 1
        self.path = path
        self.addr.set(path)
        self.title = os.path.basename(path) or path
        if not self.show_hidden:
            names = [n for n in names if not n.startswith(".")]
        key = lambda n: (not os.path.isdir(os.path.join(path, n)), n.lower())
        items = []
        for n in sorted(names, key=key):
            p = os.path.join(path, n)
            isdir = os.path.isdir(p)
            label, icon, shortcut = n, icons.for_path(p, isdir), False
            if n.endswith(".desktop") and not isdir:
                spec = _shell.parse_launcher(p)
                if spec.get("Name"):
                    label = spec["Name"]
                icon, shortcut = spec.get("Icon") or "exe", True
            items.append({"label": label, "icon": icon, "shortcut": shortcut,
                          "data": p, "isdir": isdir})
        self.grid.set_items(items)
        self.grid.sb.pos = 0
        self.b_back.enabled = self.hist_i > 0
        self.b_fwd.enabled = self.hist_i < len(self.hist) - 1
        self.b_up.enabled = path != "/"
        self.invalidate()

    def refresh(self):
        self.navigate(self.path, from_hist=True)

    def _go(self, step):
        i = self.hist_i + step
        if 0 <= i < len(self.hist):
            self.hist_i = i
            self.navigate(self.hist[i], from_hist=True)

    def _up(self):
        self.navigate(os.path.dirname(self.path))

    def _addr_enter(self, text):
        self.navigate(text)
        self.set_focus(self.grid)

    def _activate(self, item):
        if item["isdir"]:
            self.navigate(item["data"])
        else:
            self.desk.shell.open_path(item["data"])

    # ── menus ───────────────────────────────────────────────────────────────
    def _file_menu(self):
        MI, sep = W.MenuItem, W.sep
        sel = self.grid.selected_items()
        one = sel[0] if len(sel) == 1 else None
        return [
            MI("Open", enabled=bool(sel),
               action=lambda: sel and self._activate(sel[0])),
            MI("Open Terminal Here", icon="terminal",
               action=lambda: self.desk.shell.open_terminal(self.path)),
            sep(),
            MI("New Folder…", icon="folder", action=self._new_folder),
            MI("New Text File…", icon="doc_text", action=self._new_file),
            MI("Create Launcher…", icon="exe",
               action=lambda: self.desk.shell.create_launcher_dialog(
                   prefill_cmd=_shell.shell_quote(one["data"]) if one
                   else None)),
            sep(),
            MI("Rename…", enabled=one is not None,
               action=lambda: self._rename(one)),
            MI("Delete…", enabled=bool(sel),
               action=lambda: self._delete(sel)),
            MI("Properties…", enabled=one is not None,
               action=lambda: self._properties(one)),
            sep(),
            MI("Close", action=self.request_close),
        ]

    def _view_menu(self):
        MI, sep = W.MenuItem, W.sep
        return [
            MI("Refresh", action=self.refresh),
            MI("Show Hidden Files", checked=self.show_hidden,
               action=self._toggle_hidden),
            sep(),
            MI("Open Desktop Folder", icon="folder_open",
               action=lambda: self.navigate(self.desk.shell.dir)),
        ]

    def _help_menu(self):
        return [W.MenuItem("About File Manager…", icon="folder_open",
                           action=lambda: wm.msgbox(
                               self.desk, "About File Manager",
                               "kilix 95 File Manager\n"
                               "Browse, open, rename, delete —\n"
                               "and make desktop launchers from files.",
                               icon="folder_open"))]

    def _toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.refresh()

    def _context(self, item, ev):
        gx, gy = self.client_origin()
        gx, gy = gx + ev.x, gy + ev.y
        MI, sep = W.MenuItem, W.sep
        if item is None:
            items = [
                MI("New Folder…", icon="folder", action=self._new_folder),
                MI("New Text File…", icon="doc_text", action=self._new_file),
                sep(),
                MI("Open Terminal Here", icon="terminal",
                   action=lambda: self.desk.shell.open_terminal(self.path)),
                MI("Refresh", action=self.refresh),
                MI("Show Hidden Files", checked=self.show_hidden,
                   action=self._toggle_hidden),
            ]
        else:
            items = [
                MI("Open", action=lambda: self._activate(item)),
            ]
            if item["isdir"]:
                items.append(MI("Open Terminal Here", icon="terminal",
                                action=lambda: self.desk.shell.open_terminal(
                                    item["data"])))
            else:
                items.append(MI("Open with Notepad", icon="notepad",
                                action=lambda: self.desk.shell.open_app(
                                    "notepad", item["data"])))
            items += [
                MI("Create Launcher…", icon="exe",
                   action=lambda: self.desk.shell.create_launcher_dialog(
                       prefill_cmd=_shell.shell_quote(item["data"]))),
                sep(),
                MI("Rename…", action=lambda: self._rename(item)),
                MI("Delete…", action=lambda: self._delete([item])),
                MI("Properties…", action=lambda: self._properties(item)),
            ]
        self.desk.menus.open(items, gx, gy)

    # ── file ops ────────────────────────────────────────────────────────────
    def _new_folder(self):
        def do(name):
            if name:
                try:
                    os.makedirs(os.path.join(self.path, name),
                                exist_ok=False)
                except OSError as e:
                    wm.msgbox(self.desk, "New Folder", str(e), icon="error")
                self.refresh()
        wm.inputbox(self.desk, "New Folder", "Folder name:", "New Folder",
                    cb=do, icon="folder")

    def _new_file(self):
        def do(name):
            if name:
                try:
                    open(os.path.join(self.path, name), "x").close()
                except OSError as e:
                    wm.msgbox(self.desk, "New File", str(e), icon="error")
                self.refresh()
        wm.inputbox(self.desk, "New Text File", "File name:",
                    "New File.txt", cb=do, icon="doc_text")

    def _rename(self, item):
        if not item:
            return

        def do(name):
            if name and name != os.path.basename(item["data"]):
                try:
                    os.rename(item["data"],
                              os.path.join(self.path, name))
                except OSError as e:
                    wm.msgbox(self.desk, "Rename", str(e), icon="error")
                self.refresh()
        wm.inputbox(self.desk, "Rename", "New name:",
                    os.path.basename(item["data"]), cb=do)

    def _delete(self, sel):
        if not sel:
            return
        names = ", ".join(i["label"] for i in sel[:4]) + (
            "…" if len(sel) > 4 else "")

        def do(ans):
            if ans != "Yes":
                return
            for it in sel:
                try:
                    p = it["data"]
                    if os.path.isdir(p) and not os.path.islink(p):
                        shutil.rmtree(p)
                    else:
                        os.unlink(p)
                except OSError as e:
                    wm.msgbox(self.desk, "Delete", str(e), icon="error")
            self.refresh()
        wm.msgbox(self.desk, "Confirm Delete",
                  f"Delete {names}?\nThis cannot be undone.", icon="warn",
                  buttons=("Yes", "No"), default=1, cb=do)

    def _properties(self, item):
        p = item["data"]
        try:
            st = os.lstat(p)
        except OSError as e:
            wm.msgbox(self.desk, "Properties", str(e), icon="error")
            return
        kind = ("Folder" if item["isdir"] else
                "Launcher" if p.endswith(".desktop") else "File")
        size = st.st_size
        if item["isdir"]:
            try:
                size = len(os.listdir(p))
                size_s = f"{size} item(s)"
            except OSError:
                size_s = "?"
        else:
            size_s = _human(size)
        wm.msgbox(self.desk, f"{item['label']} Properties",
                  f"Type:      {kind}\n"
                  f"Location:  {os.path.dirname(p)}\n"
                  f"Size:      {size_s}\n"
                  f"Modified:  {time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}\n"
                  f"Mode:      {stat.filemode(st.st_mode)}",
                  icon=item.get("icon", "doc"),
                  win_icon=item.get("icon", "doc"))

    # ── keys ────────────────────────────────────────────────────────────────
    def on_key(self, ev):
        if self.focus is self.grid:
            if ev.key == "Backspace":
                self._up()
                return True
            if ev.key == "F5":
                self.refresh()
                return True
            if ev.key == "Delete":
                self._delete(self.grid.selected_items())
                return True
            if ev.key == "F2":
                sel = self.grid.selected_items()
                if sel:
                    self._rename(sel[0])
                return True
        if ev.ctrl and ev.key == "l":
            self.set_focus(self.addr)
            return True
        return super().on_key(ev)


def _human(n):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
