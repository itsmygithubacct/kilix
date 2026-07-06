"""kilix desktop — the start bar.

Start button + menu, one button per open window (stable launch order), and
a sunken clock well. The Start menu is a MenuHost popup with the classic
vertical "kilix 95" sidebar; its content is assembled from the shell's
built-in apps and the user's launchers.
"""
import time

import icons
import theme as T
import widgets as W

START_W = 58
CLOCK_W = 76


class Taskbar:
    def __init__(self, desk):
        self.desk = desk
        self.menu_open = -1           # duck-types as a MenuBar for MenuHost
        self._minute = ""
        self._pressed_btn = None

    # geometry -----------------------------------------------------------
    def rect(self):
        sw, sh = self.desk.size()
        return 0, sh - T.TASKBAR_H, sw, sh - 1

    def _buttons(self):
        """[(win, x0, x1)] task buttons in stable launch order."""
        x0, y0, x1, y1 = self.rect()
        wins = sorted((w for w in self.desk.wm.windows if not w.modal),
                      key=lambda w: w.seq)
        if not wins:
            return []
        bx = x0 + START_W + 10
        lim = x1 - CLOCK_W - 8
        bw = min(150, max(22, (lim - bx) // len(wins) - 3))
        out = []
        for w in wins:
            if bx + bw - 1 > lim:     # never run under the clock
                break
            out.append((w, bx, bx + bw - 1))
            bx += bw + 3
        return out

    def invalidate(self):
        self.desk.dirty = True

    def hover_switch(self, gev):      # MenuBar duck-type hook (unused here)
        pass

    def tick(self, now):
        m = time.strftime("%H:%M", time.localtime(now))
        if m != self._minute:
            self._minute = m
            self.invalidate()

    # drawing --------------------------------------------------------------
    def draw(self, fb, d):
        x0, y0, x1, y1 = self.rect()
        d.rectangle([x0, y0, x1, y1], fill=T.FACE)
        d.line([(x0, y0), (x1, y0)], fill=T.LIGHT)   # raised top edge
        # Start
        sb = (x0 + 2, y0 + 4, x0 + 2 + START_W - 1, y1 - 3)
        if self.menu_open >= 0:
            T.pressed(d, *sb)
            off = 1
        else:
            T.raised(d, *sb)
            off = 0
        icons.paint(fb, "flame", sb[0] + 4 + off, sb[1] + 2 + off, 16)
        d.text((sb[0] + 24 + off, sb[1] + 3 + off), "Start", font=T.BOLD,
               fill=T.TEXT)
        # task buttons
        active = self.desk.wm.active
        for win, bx0, bx1 in self._buttons():
            r = (bx0, y0 + 4, bx1, y1 - 3)
            if win is active and not win.minimized:
                T.pressed(d, *r, fill=T.LTGRAY)
                o = 1
            else:
                T.raised(d, *r)
                o = 0
            icons.paint(fb, win.icon, r[0] + 3 + o, r[1] + 2 + o, 16)
            tw = bx1 - bx0 - 28
            if tw > 6:                # icon-only when squeezed
                d.text((r[0] + 23 + o, r[1] + 3 + o),
                       T.ellipsize(T.FONT, win.title, tw),
                       font=T.FONT, fill=T.TEXT)
        # clock well
        cw = (x1 - CLOCK_W - 2, y0 + 4, x1 - 2, y1 - 3)
        T.sunken(d, *cw, fill=T.FACE)
        icons.paint(fb, "display", cw[0] + 4, cw[1] + 2, 16)
        clock = self._minute or time.strftime("%H:%M")
        d.text((cw[2] - 8 - T.text_w(T.FONT, clock), cw[1] + 3), clock,
               font=T.FONT, fill=T.TEXT)

    # input ------------------------------------------------------------------
    def hit(self, gx, gy):
        x0, y0, x1, y1 = self.rect()
        return y0 <= gy <= y1

    def on_mouse(self, gev):
        x0, y0, x1, y1 = self.rect()
        if not gev.press:
            return True
        modal = self.desk.wm.modal_top()
        if gev.btn == 1 and x0 + 2 <= gev.x < x0 + 2 + START_W:
            if modal:
                self.desk.wm.activate(modal)
            else:
                self.open_start_menu()
            return True
        for win, bx0, bx1 in self._buttons():
            if bx0 <= gev.x <= bx1:
                if modal:
                    self.desk.wm.activate(modal)
                elif gev.btn == 3:
                    win._system_menu(bx0, y0)
                elif win is self.desk.wm.active and not win.minimized:
                    self.desk.wm.minimize(win)
                else:
                    self.desk.wm.activate(win)
                return True
        return True

    # the Start menu -----------------------------------------------------------
    def open_start_menu(self):
        modal = self.desk.wm.modal_top()
        if modal:                     # a modal dialog owns all input
            self.desk.wm.activate(modal)
            return
        if self.menu_open >= 0:       # pressing Start again closes it
            self.desk.menus.close_all()
            return
        shell = self.desk.shell
        MI, sub = W.MenuItem, W.sep

        def games():
            builtin = [
                MI("Minesweeper", icon="mines",
                   action=lambda: shell.open_app("mines")),
                MI("Solitaire", icon="cards",
                   action=lambda: shell.open_app("sol")),
            ]
            return builtin + shell.game_menu_items()

        def accessories():
            return [
                MI("Calculator", icon="calc",
                   action=lambda: shell.open_app("calc")),
                MI("Character Map", icon="charmap",
                   action=lambda: shell.open_app("charmap")),
                MI("Help", icon="help",
                   action=lambda: shell.open_app("winhelp")),
                MI("Notepad", icon="notepad",
                   action=lambda: shell.open_app("notepad")),
                MI("Paint", icon="paint",
                   action=lambda: shell.open_app("paint")),
                MI("WordPad", icon="wordpad",
                   action=lambda: shell.open_app("wordpad")),
            ]

        def programs():
            items = [
                MI("Accessories", icon="folder", submenu=accessories()),
                MI("Games", icon="games", submenu=games()),
                sub(),
                MI("File Manager", icon="folder_open",
                   action=lambda: shell.open_app("filemgr")),
                MI("Terminal", icon="terminal", action=shell.open_terminal),
                MI("Web Browser", icon="browser",
                   action=lambda: shell.open_url(None)),
                MI("Media Player", icon="amp",
                   action=lambda: shell.open_app("amp")),
            ]
            user = shell.launcher_menu_items()
            if user:
                items.append(sub())
                items.extend(user)
            return items

        def documents():
            docs = [MI(label, icon=icons.for_path(p),
                       action=lambda p=p: shell.open_path(p))
                    for label, p in shell.recent_docs()]
            return docs or [MI("(Empty)", enabled=False)]

        settings_sub = [
            MI("kilix Settings", icon="settings",
               action=lambda: shell.open_app("settings")),
            MI("Display…", icon="display", action=shell.display_properties),
        ]
        find_sub = [
            MI("Files or Folders…", icon="find",
               action=lambda: shell.open_app("findfiles")),
        ]
        items = [
            MI("Programs", icon="folder", submenu=programs()),
            MI("Documents", icon="doc_text", submenu=documents()),
            MI("Settings", icon="settings", submenu=settings_sub),
            MI("Find", icon="find", submenu=find_sub),
            sub(),
            MI("Create Launcher…", icon="exe",
               action=lambda: shell.create_launcher_dialog()),
            MI("Run…", icon="run", action=shell.run_dialog),
            sub(),
            MI("Shut Down…", icon="shutdown", action=shell.shutdown_dialog),
        ]
        x0, y0, x1, y1 = self.rect()
        self.desk.menus.open(items, x0 + 2, y0, item_h=24,
                             sidebar="kilix 95", bar=self, min_w=150)
        self.menu_open = 1
        self.invalidate()
