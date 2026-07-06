"""kilix desktop — WordPad. An editor over widgets.TextArea with a toolbar,
ruler, word-wrap toggle, Find/Replace, Go To and a live word/char count."""
import os

import theme as T
import widgets as W
import wm

TB_Y = T.MENU_H + 2               # toolbar row (below the menu bar)
TB_H = 26
RULER_H = 14
STATUS_H = 20
TA_Y = TB_Y + TB_H + RULER_H + 1
SIZES = ["8", "9", "10", "11", "12", "14", "16", "18"]


class WordPad(wm.Window):
    def __init__(self, desk, path=None):
        super().__init__(desk, "Document - WordPad", 600, 430, icon="wordpad")
        self.min_w, self.min_h = 420, 240
        self.path = None
        self.modified = False
        self.wrap = False
        self._soft = set()             # visual-line indices with a soft break
        self._find_term = ""
        self.n_words = self.n_chars = 0
        cw, ch = self.client_size()
        self.menubar = self.add(W.MenuBar(cw, [
            ("File", self._file_menu), ("Edit", self._edit_menu),
            ("View", self._view_menu), ("Help", self._help_menu)]))
        y = TB_Y + 2
        self.add(W.Button(4, y, 40, 22, "New", cb=self._new))
        self.add(W.Button(49, y, 46, 22, "Open", cb=self._open))
        self.add(W.Button(100, y, 44, 22, "Save", cb=self._save))
        self.add(W.Button(149, y, 46, 22, "Print", cb=self._print))
        self.add(W.Button(200, y, 44, 22, "Find", cb=self._find))
        self.size_dd = self.add(W.Dropdown(290, TB_Y + 3, 56, SIZES,
                                           index=SIZES.index("11"),
                                           cb=self._set_size))
        self.ta = self.add(W.TextArea(2, TA_Y, cw - 4,
                                      ch - TA_Y - STATUS_H - 2))
        self.ta.on_change = self._changed
        self.set_focus(self.ta)
        if path:
            self._load(path)

    def on_resize(self):
        cw, ch = self.client_size()
        self.menubar.w = cw
        self.ta.w, self.ta.h = cw - 4, ch - TA_Y - STATUS_H - 2
        if self.wrap:
            self._set(self._text())

    def draw_client(self, d, img):
        cw, ch = self.client_size()
        T.raised_thin(d, 0, TB_Y, cw - 1, TB_Y + TB_H - 1)
        d.text((256, TB_Y + 7), "Size", font=T.FONT, fill=T.TEXT)
        ry = TB_Y + TB_H
        T.sunken(d, 4, ry + 1, cw - 5, ry + RULER_H - 2, fill=T.WINDOW_BG)
        for x in range(8, cw - 8, 48):
            d.line([(x, ry + RULER_H - 6), (x, ry + RULER_H - 3)],
                   fill=T.SHADOW)
        for x in range(32, cw - 8, 48):
            d.point((x, ry + RULER_H - 4), fill=T.SHADOW)
        T.sunken(d, 2, ch - STATUS_H, cw - 3, ch - 3, fill=T.FACE)
        msg = (f"Words: {self.n_words}   Chars: {self.n_chars}   "
               f"Ln {self.ta.cr + 1}, Col {self.ta.cc + 1}"
               + ("   Wrap" if self.wrap else ""))
        d.text((8, ch - STATUS_H + 3), msg, font=T.FONT, fill=T.TEXT)

    # ── word-wrap: TextArea holds a reflowed copy; _text() is canonical ──────
    def _wrap(self, text):
        maxpx = self.ta.w - T.SCROLL_W - 10
        out, soft = [], set()
        for para in text.split("\n"):
            base = len(out)
            for k, ln in enumerate(self._wrap_para(para, maxpx)):
                if k:
                    soft.add(base + k)
                out.append(ln)
        self._soft = soft
        return "\n".join(out)

    def _wrap_para(self, para, maxpx):
        lines, cur = [], None
        for word in para.split(" "):
            trial = word if cur is None else cur + " " + word
            if cur is None or T.text_w(self.ta.font, trial) <= maxpx:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur is not None:
            lines.append(cur)
        return lines or [""]

    def _dewrap(self, text):
        lines = text.split("\n")
        res = lines[0] if lines else ""
        for i in range(1, len(lines)):
            res += (" " if i in self._soft else "\n") + lines[i]
        return res

    def _text(self):
        return self._dewrap(self.ta.text()) if self.wrap else self.ta.text()

    def _set(self, text):
        if self.wrap:
            self.ta.set_text(self._wrap(text))
        else:
            self._soft = set()
            self.ta.set_text(text)
        self._recount()

    def _toggle_wrap(self):
        t = self._text()
        self.wrap = not self.wrap
        self._set(t)
        self.invalidate()

    def _recount(self):
        t = self._text()
        self.n_words = len(t.split())
        self.n_chars = len(t)

    def _set_size(self, val):
        self.ta.font = T._find_font(T._candidates(False), int(val))
        if self.wrap:
            self._set(self._text())
        self.ta.invalidate()
        self.invalidate()

    # ── find / replace / go to ──────────────────────────────────────────────
    def find_next(self, term):
        if not term:
            return False
        ta, lines = self.ta, self.ta.lines
        n = len(lines)
        for di in range(n + 1):
            r = (ta.cr + di) % n
            idx = lines[r].find(term, ta.cc if di == 0 else 0)
            if idx != -1:
                ta.anchor = (r, idx)
                ta.cr, ta.cc = r, idx + len(term)
                ta._reveal()
                ta.invalidate()
                self._find_term = term
                return True
        return False

    def replace_one(self, term, repl):
        ta = self.ta
        sel = ta._sel()
        if sel:
            (ar, ac), (br, bc) = sel
            if ar == br and ta.lines[ar][ac:bc] == term:
                ta.insert(repl)
        return self.find_next(term)

    def replace_all(self, term, repl):
        if not term:
            return 0
        text = self.ta.text()
        count = text.count(term)
        if count:
            self.ta.set_text(text.replace(term, repl))
            self._changed()
        return count

    def goto_line(self, n):
        ta = self.ta
        ta.cr = max(0, min(n - 1, len(ta.lines) - 1))
        ta.cc, ta.anchor = 0, None
        ta._reveal()
        ta.invalidate()
        self.invalidate()

    def _find(self):
        def do(t):
            if t and not self.find_next(t):
                wm.msgbox(self.desk, "WordPad", f'Cannot find "{t}".',
                          icon="info")
        wm.inputbox(self.desk, "Find", "Find what:", self._find_term,
                    cb=do, icon="wordpad")

    def _replace(self):
        self.desk.wm.add(ReplaceDialog(self.desk, self))

    def _goto(self):
        def do(t):
            if t and t.strip().isdigit():
                self.goto_line(int(t.strip()))
        wm.inputbox(self.desk, "Go To Line", "Line number:",
                    str(self.ta.cr + 1), cb=do, icon="wordpad")

    def _print(self):
        wm.msgbox(self.desk, "WordPad",
                  "Printing is not available in kilix 95.", icon="info")

    # ── file plumbing (mirrors Notepad) ─────────────────────────────────────
    def _retitle(self):
        name = os.path.basename(self.path) if self.path else "Document"
        self.title = f"{'*' if self.modified else ''}{name} - WordPad"
        self.invalidate()

    def _changed(self):
        self._recount()
        if not self.modified:
            self.modified = True
        self._retitle()

    def _load(self, path):
        path = os.path.expanduser(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                self._set(f.read())
        except OSError as e:
            wm.msgbox(self.desk, "WordPad", str(e), icon="error")
            return
        self.path = path
        self.modified = False
        self.desk.shell.add_recent(path)
        self._retitle()

    def _save(self, then=None, path=None):
        target = os.path.expanduser(path) if path else self.path
        if not target:
            return self._save_as(then)
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(self._text())
        except OSError as e:
            wm.msgbox(self.desk, "WordPad", str(e), icon="error")
            return
        self.path = target
        self.modified = False
        self._retitle()
        if then:
            then()

    def _save_as(self, then=None):
        def do(path):
            if path:
                self._save(then, path=path)
        wm.inputbox(self.desk, "Save As", "Save to path:",
                    self.path or os.path.expanduser("~/"), cb=do,
                    icon="wordpad", width=340)

    def _open(self):
        def go():
            wm.inputbox(self.desk, "Open", "Path to open:",
                        os.path.dirname(self.path) + "/" if self.path
                        else os.path.expanduser("~/"),
                        cb=lambda p: p and self._load(p), icon="wordpad",
                        width=340)
        self._if_saved(go)

    def _new(self):
        def go():
            self.path = None
            self._set("")
            self.modified = False
            self._retitle()
        self._if_saved(go)

    def _if_saved(self, then):
        if not self.modified:
            then()
            return

        def do(ans):
            if ans == "Yes":
                self._save(then)
            elif ans == "No":
                then()
        wm.msgbox(self.desk, "WordPad",
                  "The text has changed.\nSave the changes?",
                  icon="warn", buttons=("Yes", "No", "Cancel"), cb=do)

    def request_close(self):
        self._if_saved(self.close)

    # ── menus ────────────────────────────────────────────────────────────────
    def _file_menu(self):
        MI, sep = W.MenuItem, W.sep
        return [
            MI("New", action=self._new),
            MI("Open…", action=self._open),
            MI("Save", action=self._save),
            MI("Save As…", action=self._save_as),
            sep(),
            MI("Print…", action=self._print),
            sep(),
            MI("Close", action=self.request_close),
        ]

    def _edit_menu(self):
        MI, sep = W.MenuItem, W.sep
        ta = self.ta

        def key(k, ctrl=True):
            return lambda: ta.on_key(W.Ev(kind="key", key=k, ctrl=ctrl))
        return [
            MI("Cut", action=key("x")),
            MI("Copy", action=key("c")),
            MI("Paste", action=key("v")),
            MI("Select All", action=key("a")),
            sep(),
            MI("Find…", action=self._find),
            MI("Replace…", action=self._replace),
            MI("Go To…", action=self._goto),
        ]

    def _view_menu(self):
        return [W.MenuItem("Word Wrap", checked=self.wrap,
                           action=self._toggle_wrap)]

    def _help_menu(self):
        return [W.MenuItem(
            "About WordPad…", icon="wordpad",
            action=lambda: wm.msgbox(
                self.desk, "About WordPad",
                "kilix 95 WordPad\nWord wrap · Find/Replace · Go To\n"
                "Ctrl+F find · Ctrl+H replace · Ctrl+G go to.",
                icon="wordpad"))]

    def on_key(self, ev):
        if ev.ctrl and ev.key == "s":
            self._save(); return True
        if ev.ctrl and ev.key == "o":
            self._open(); return True
        if ev.ctrl and ev.key == "n":
            self._new(); return True
        if ev.ctrl and ev.key == "f":
            self._find(); return True
        if ev.ctrl and ev.key == "h":
            self._replace(); return True
        if ev.ctrl and ev.key == "g":
            self._goto(); return True
        if ev.key == "F3" and self._find_term:
            self.find_next(self._find_term); return True
        return super().on_key(ev)


class ReplaceDialog(wm.Window):
    def __init__(self, desk, editor):
        super().__init__(desk, "Replace", 350, 170, icon="wordpad",
                         resizable=False, modal=True)
        self.editor = editor
        cw, ch = self.client_size()
        self.add(W.Label(10, 14, "Find what:"))
        self.f_find = self.add(W.TextField(96, 10, cw - 106,
                                           editor._find_term))
        self.add(W.Label(10, 42, "Replace with:"))
        self.f_repl = self.add(W.TextField(96, 38, cw - 106))
        by = 74
        self.add(W.Button(10, by, 92, 23, "Find Next", cb=self._find,
                          default=True))
        self.add(W.Button(108, by, 92, 23, "Replace", cb=self._replace))
        self.add(W.Button(206, by, 92, 23, "Replace All", cb=self._all))
        self.add(W.Button(cw - 82, ch - 30, 72, 23, "Close", cb=self.close))
        self.set_focus(self.f_find)

    def _find(self):
        t = self.f_find.text
        if t and not self.editor.find_next(t):
            wm.msgbox(self.desk, "WordPad", f'Cannot find "{t}".', icon="info")

    def _replace(self):
        self.editor.replace_one(self.f_find.text, self.f_repl.text)

    def _all(self):
        n = self.editor.replace_all(self.f_find.text, self.f_repl.text)
        wm.msgbox(self.desk, "WordPad", f"{n} replacement(s).", icon="info")
