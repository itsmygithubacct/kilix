"""Regression tests for kilix Settings (desktop/apps/settings.py).

Covers F06 (raw kitty.conf tab edits lost on tab switch / stale Apply),
F27 (untouched Apply corrupts non-listed values and appends managed
defaults), and F52 (non-UTF-8 kitty.conf makes Settings unopenable).
"""
import contextlib
import os
import stat
import tempfile

import harness as H
from apps import settings

# _apply live-reloads the running kilix; make that a no-op under test so we
# never SIGUSR1 a real process or shell out to a kitten.
os.environ.pop("KITTY_LISTEN_ON", None)
os.environ.pop("KITTY_PID", None)


@contextlib.contextmanager
def conf(text, binary=False):
    """A temp KITTY_CONFIG_DIRECTORY holding a kitty.conf; yields its path."""
    prev = os.environ.get("KITTY_CONFIG_DIRECTORY")
    prev_shared = os.environ.get("GPU_TERMINAL_SETTINGS_FILE")
    d = tempfile.mkdtemp(prefix="kilix95-conf-")
    path = os.path.join(d, "kitty.conf")
    with open(path, "wb") as f:
        f.write(text if binary else text.encode())
    os.environ["KITTY_CONFIG_DIRECTORY"] = d
    os.environ["GPU_TERMINAL_SETTINGS_FILE"] = os.path.join(d, "settings.conf")
    try:
        yield path
    finally:
        if prev is None:
            os.environ.pop("KITTY_CONFIG_DIRECTORY", None)
        else:
            os.environ["KITTY_CONFIG_DIRECTORY"] = prev
        if prev_shared is None:
            os.environ.pop("GPU_TERMINAL_SETTINGS_FILE", None)
        else:
            os.environ["GPU_TERMINAL_SETTINGS_FILE"] = prev_shared


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def env_path_for(conf_path):
    return os.path.join(os.path.dirname(conf_path), "kilix.env")


# Private config replacement is atomic and replaces a stale symlink rather
# than following it into an unrelated file.
with tempfile.TemporaryDirectory(prefix="kilix-settings-atomic-") as d:
    unrelated = os.path.join(d, "unrelated")
    target = os.path.join(d, "kitty.conf")
    with open(unrelated, "w", encoding="utf-8") as f:
        f.write("keep\n")
    os.symlink(unrelated, target)
    settings._atomic_write_private(target, "font_size 14\n")
    assert not os.path.islink(target)
    assert read(target) == "font_size 14\n"
    assert read(unrelated) == "keep\n"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


# ── F06: raw editor edits survive a tab roundtrip and reach disk ────────────
with conf("font_size 12\n") as path:
    import apps
    d = H.make_desk()
    apps.open(d, "settings", None)
    win = H.find_window(d, "SettingsWin")
    assert win is not None

    win._switch_tab(win.raw_tab)             # go to the raw kitty.conf tab
    win.ta.set_text(win.ta.text() + "map ctrl+j scroll_line_down\n")
    win._switch_tab(0)                       # leave tab 2 …
    win._switch_tab(win.raw_tab)             # … and come back
    assert "map ctrl+j scroll_line_down" in win.ta.text(), \
        "F06: raw edit did not survive a tab roundtrip"

    # and an Apply issued from a form tab must persist the raw edit
    win._switch_tab(0)
    win._apply()
    saved = read(path)
    assert "map ctrl+j scroll_line_down" in saved, \
        "F06: Apply from a form tab wrote a stale buffer"
    assert "font_size" in saved


# ── F27: an untouched Apply is loss-free on odd-but-valid config ────────────
odd = (
    "# hand-tuned kilix config\n"
    "tab_bar_style custom\n"           # valid kitty value the dropdown omits
    "font_size        11.5\n"          # odd whitespace, must be preserved
    "map ctrl+shift+e launch --type=tab\n"
    "enable_audio_bell true\n"         # present as 'true', not 'yes'
    "symbol_map U+E0A0-U+E0A3 PowerlineSymbols\n"
)
with conf(odd) as path:
    d = H.make_desk()
    import apps
    apps.open(d, "settings", None)
    win = H.find_window(d, "SettingsWin")

    # the non-listed value must be shown, not silently reset to options[0]
    kind, wd = win.fields["tab_bar_style"]
    assert wd.value == "custom", \
        f"F27: non-listed tab_bar_style shown as {wd.value!r}, not 'custom'"

    win._apply()                             # user changed nothing
    after = read(path)
    assert after == odd, \
        "F27: untouched Apply mutated the config:\n--- before ---\n" \
        + odd + "\n--- after ---\n" + after
    # specifically: nothing rewritten, no managed defaults appended
    assert "tab_bar_style fade" not in after
    assert "cursor_shape" not in after
    assert "copy_on_select" not in after
    assert settings.MARKER not in after


# ── F27b: a real form change still writes (and only that key) ───────────────
with conf(odd) as path:
    d = H.make_desk()
    import apps
    apps.open(d, "settings", None)
    win = H.find_window(d, "SettingsWin")
    kind, wd = win.fields["cursor_shape"]
    wd.index = wd.options.index("beam")      # user picks a value
    win._apply()
    after = read(path)
    assert settings.get_key(after, "cursor_shape") == "beam", \
        "F27b: an actual choice change was not written"
    assert "tab_bar_style custom" in after   # untouched keys preserved
    assert settings.get_key(after, "font_size") == "11.5"
    assert "copy_on_select" not in after     # still no unrelated defaults


# ── Clickable chrome is shared; unrelated runtime settings stay in kilix.env
with conf("font_size 12\n") as path:
    d = H.make_desk()
    import apps
    apps.open(d, "settings", None)
    win = H.find_window(d, "SettingsWin")

    kind, volume = win.fields["KILIX_CHROME_VOLUME"]
    assert volume.checked, "shared default for KILIX_CHROME_VOLUME should be enabled"
    kind, thermal = win.fields["KILIX_CHROME_TEMPERATURE"]
    assert not thermal.checked, "thermal widget should be disabled by default"
    kind, synchronize = win.fields["KILIX_CHROME_BUTTON_SYNCHRONIZE_INPUT"]
    assert synchronize.checked, "synchronized-input button should default on"
    kind, memory_mode = win.fields["KILIX_CHROME_PANE_MEMORY_MODE"]
    assert memory_mode.value == "auto", "pane memory chip should default to auto"
    thermal.checked = True
    volume.checked = False
    synchronize.checked = False
    memory_mode.index = memory_mode.options.index("always")
    win._apply()
    assert "KILIX_CHROME_TEMPERATURE=1" in read(win.shared_path)
    assert "KILIX_CHROME_VOLUME=0" in read(win.shared_path)
    assert "KILIX_CHROME_BUTTON_SYNCHRONIZE_INPUT=0" in read(win.shared_path)
    assert "KILIX_CHROME_PANE_MEMORY_MODE=always" in read(win.shared_path)

    kind, wd = win.fields["KILIX_CHROME_CLOCK"]
    assert wd.checked, "shared default for KILIX_CHROME_CLOCK should be enabled"
    wd.checked = False
    win._apply()
    shared_text = read(win.shared_path)
    assert "KILIX_CHROME_CLOCK=0" in shared_text
    assert "KILIX_CHROME_CLOCK" not in read(path)
    assert not os.path.exists(env_path_for(path))

    wd.checked = True
    win._apply()
    shared_text = read(win.shared_path)
    assert "KILIX_CHROME_CLOCK=1" in shared_text

    kind, split_up = win.fields["KILIX_CHROME_BUTTON_SPLIT_UP"]
    split_up.checked = False
    win._apply()
    assert "KILIX_CHROME_BUTTON_SPLIT_UP=0" in read(win.shared_path)

    kind, doom = win.fields["KILIX_GAME_DOOM"]
    doom.checked = False
    win._apply()
    assert "KILIX_GAME_DOOM=0" in read(win.shared_path)

    for key in settings.shared_settings.MANAGED_KEYS:
        assert key in win.fields, f"Settings UI is missing shared control {key}"

    kind, flavor = win.fields["KILIX_DESKTOP_FLAVOR"]
    flavor.index = flavor.options.index("xp")
    win._apply()
    env_text = read(env_path_for(path))
    assert "KILIX_DESKTOP_FLAVOR=xp" in env_text


# ── F52: a non-UTF-8 kitty.conf must not make Settings unopenable ───────────
with conf(b"# note: caf\xe9 sync\nfont_size 13\n", binary=True) as path:
    d = H.make_desk()
    import apps
    apps.open(d, "settings", None)           # must not raise UnicodeDecodeError
    win = H.find_window(d, "SettingsWin")
    assert win is not None, "F52: Settings failed to open on a non-UTF-8 config"
    kind, wd = win.fields["font_size"]
    assert wd.text == "13", "F52: config was not parsed after tolerant decode"


print("ok")
