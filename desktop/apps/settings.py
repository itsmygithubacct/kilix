"""kilix desktop — kilix Settings (the control panel).

Edits the kilix configuration (config/kitty.conf, i.e. whatever
$KITTY_CONFIG_DIRECTORY points at). Two form tabs cover the common knobs;
the third tab is the raw file in a text editor. Apply writes the file and
live-reloads the running kilix via `kitten @ action load_config_file`,
falling back to SIGUSR1 at $KITTY_PID. Only the managed lines are rewritten
(last occurrence wins, matching kitty's own semantics); everything else in
the file — comments included — is preserved byte for byte.
"""
import os
import re
import signal
import subprocess

import shell as _shell
import theme as T
import widgets as W
import wm

MARKER = "# ── kilix desktop settings ──"

# (key, label, kind, extra) — kind: text | choice | bool
APPEARANCE = [
    ("font_family", "Font family", "text", None),
    ("font_size", "Font size", "text", None),
    ("foreground", "Text color", "color", None),
    ("background", "Background color", "color", None),
    ("background_opacity", "Background opacity", "text", None),
    ("cursor_shape", "Cursor shape", "choice",
     ["block", "beam", "underline"]),
    ("tab_bar_style", "Tab bar style", "choice",
     ["fade", "separator", "powerline", "slant", "hidden"]),
]
BEHAVIOR = [
    ("scrollback_lines", "Scrollback lines", "text", None),
    ("enable_audio_bell", "Audio bell", "bool", "no"),
    ("copy_on_select", "Copy on select", "bool", "no"),
    ("confirm_os_window_close", "Confirm window close (panes)", "text", None),
    ("mouse_hide_wait", "Hide mouse after (s)", "text", None),
    ("cursor_blink_interval", "Cursor blink interval", "text", None),
]


def config_path():
    d = os.environ.get("KITTY_CONFIG_DIRECTORY") or os.path.join(
        _shell.KILIX_HOME, "config")
    return os.path.join(d, "kitty.conf")


def _is_true(s):
    return s.lower() in ("yes", "y", "true", "1")


def get_key(text, key):
    pat = re.compile(rf"^\s*{re.escape(key)}\s+(.*?)\s*$", re.M)
    hits = pat.findall(text)
    return hits[-1] if hits else None


def set_key(text, key, value):
    line = f"{key:<30} {value}".rstrip()
    pat = re.compile(rf"^\s*{re.escape(key)}\s+.*$", re.M)
    hits = list(pat.finditer(text))
    if hits:
        last = hits[-1]
        return text[:last.start()] + line + text[last.end():]
    if MARKER not in text:
        text = text.rstrip("\n") + f"\n\n{MARKER}\n"
    return text.rstrip("\n") + "\n" + line + "\n"


class _Swatch(W.Widget):
    """Live color preview next to a #rrggbb text field."""

    def __init__(self, x, y, field):
        super().__init__(x, y, 21, 21)
        self.field = field

    def draw(self, d, img):
        col = T.FACE
        m = re.fullmatch(r"#?([0-9a-fA-F]{6})", self.field.text.strip())
        if m:
            v = int(m.group(1), 16)
            col = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        T.sunken(d, self.x, self.y, self.x + self.w - 1,
                 self.y + self.h - 1, fill=col)


class SettingsWin(wm.Window):
    def __init__(self, desk):
        super().__init__(desk, "kilix Settings", 500, 420, icon="settings")
        self.min_w, self.min_h = 420, 320
        self.path = config_path()
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                self.buffer = f.read()
        except OSError:
            self.buffer = ""
        cw, ch = self.client_size()
        self.tabs = self.add(W.TabBar(6, 6, cw - 12,
                                      ["Appearance", "Behavior", "kitty.conf"],
                                      cb=self._switch_tab))
        self.fields = {}              # key -> (kind, widget)
        self.panels = [[], [], []]
        for tab_i, spec in ((0, APPEARANCE), (1, BEHAVIOR)):
            y = 44
            for key, label, kind, extra in spec:
                lw = self.add(W.Label(18, y + 4, label + ":"))
                self.panels[tab_i].append(lw)
                if kind == "choice":
                    wd = self.add(W.Dropdown(200, y, 180, extra))
                elif kind == "bool":
                    wd = self.add(W.Checkbox(200, y + 3, "enabled"))
                    wd.default_val = extra
                else:
                    wd = self.add(W.TextField(200, y, 180))
                    if kind == "color":
                        sw = self.add(_Swatch(388, y, wd))
                        self.panels[tab_i].append(sw)
                        wd.on_change = lambda *_: self.invalidate()
                self.fields[key] = (kind, wd)
                self.panels[tab_i].append(wd)
                y += 30
        note_y = 44 + 30 * max(len(APPEARANCE), len(BEHAVIOR)) + 6
        note = self.add(W.Label(
            18, note_y, "Applied live to this kilix — no restart needed.",
            font=T.SMALL, color=T.SHADOW))
        self.panels[0].append(note)
        self.ta = self.add(W.TextArea(6, self.tabs.y + W.TabBar.H + 2,
                                      cw - 12, ch - 84, self.buffer))
        self.panels[2].append(self.ta)
        self.b_ok = self.add(W.Button(cw - 244, ch - 33, 72, 23, "OK",
                                      default=True,
                                      cb=lambda: self._apply(close=True)))
        self.b_cancel = self.add(W.Button(cw - 164, ch - 33, 72, 23,
                                          "Cancel", cb=self.close))
        self.b_apply = self.add(W.Button(cw - 84, ch - 33, 72, 23, "Apply",
                                         cb=self._apply))
        self.b_sounds = self.add(W.Button(
            10, ch - 33, 84, 23, "Sounds…", icon="soundcp",
            cb=lambda: self.desk.shell.open_app("soundcp")))
        self.status = self.add(W.Label(102, ch - 28, "", font=T.SMALL,
                                       color=T.SHADOW))
        self._cur_tab = 0
        self._populate()
        self._switch_tab(0)

    def on_resize(self):
        cw, ch = self.client_size()
        self.tabs.w = cw - 12
        self.ta.w, self.ta.h = cw - 12, ch - 84
        for b, dx in ((self.b_ok, 244), (self.b_cancel, 164),
                      (self.b_apply, 84)):
            b.x, b.y = cw - dx, ch - 33
        self.b_sounds.y = ch - 33
        self.status.y = ch - 28

    def draw_client(self, d, img):
        if self.tabs.active != 2:
            cw, ch = self.client_size()
            T.raised(d, 6, self.tabs.y + W.TabBar.H - 2, cw - 7, ch - 44)
            # redraw widgets over the panel face happens in the widget pass;
            # the panel is drawn first because draw_client precedes widgets

    def _switch_tab(self, i):
        if self._cur_tab == 2:
            self.buffer = self.ta.text()   # keep raw edits made on the conf tab
        if i != 2:
            self._populate()
        else:
            self._form_to_buffer()
            self.ta.set_text(self.buffer)
        self._cur_tab = i
        for tab_i, panel in enumerate(self.panels):
            for wdg in panel:
                wdg.visible = tab_i == i
        vis = [w for w in self.panels[i] if w.focusable and w.visible]
        self.set_focus(vis[0] if vis else None)
        self.invalidate()

    # form <-> buffer -----------------------------------------------------
    def _populate(self):
        for key, (kind, wd) in self.fields.items():
            val = get_key(self.buffer, key)
            if kind == "bool":
                wd.checked = _is_true(val if val is not None
                                      else wd.default_val)
            elif kind == "choice":
                if val is not None and val not in wd.options:
                    wd.options.append(val)   # keep a valid non-listed value
                if val in wd.options:
                    wd.index = wd.options.index(val)
            else:
                wd.set(val if val is not None else "")

    def _form_to_buffer(self):
        # only rewrite a key when its value actually changed, so keys absent
        # from the file stay absent and untouched values keep their formatting
        for key, (kind, wd) in self.fields.items():
            cur = get_key(self.buffer, key)
            if kind == "bool":
                v = "yes" if wd.checked else "no"
                if _is_true(v) == _is_true(cur if cur is not None
                                           else wd.default_val):
                    continue
            elif kind == "choice":
                v = wd.value
                if v == (cur if cur is not None else wd.options[0]):
                    continue
            else:
                v = wd.text.strip()
                if not v or v == (cur or ""):
                    continue
            self.buffer = set_key(self.buffer, key, v)

    # apply ----------------------------------------------------------------
    def _apply(self, close=False):
        if self.tabs.active == 2:
            self.buffer = self.ta.text()
        else:
            self._form_to_buffer()
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(self.buffer)
        except OSError as e:
            wm.msgbox(self.desk, "kilix Settings", f"Cannot write config:\n{e}",
                      icon="error")
            return
        msg = self._reload_live()
        self.status.set(msg)
        self.invalidate()
        if close:
            self.close()

    def _reload_live(self):
        kitten = self.desk.shell._kitten()
        if kitten and os.environ.get("KITTY_LISTEN_ON"):
            try:
                r = subprocess.run([kitten, "@", "action", "load_config_file"],
                                   capture_output=True, timeout=5)
                if r.returncode == 0:
                    return "Saved — kilix config reloaded live."
            except (OSError, subprocess.TimeoutExpired):
                pass
        pid = os.environ.get("KITTY_PID", "")
        if pid.isdigit():
            try:
                os.kill(int(pid), signal.SIGUSR1)
                return "Saved — reload signaled (SIGUSR1)."
            except (OSError, ProcessLookupError):
                pass
        return "Saved. Reload kilix config with Ctrl+Shift+F5."
