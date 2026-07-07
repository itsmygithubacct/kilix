"""Shut Down dialog: offers the four actions and wires the safe one correctly."""
import os

import harness as H
import widgets as W


def _buttons(win):
    return [w.text for w in win.widgets if isinstance(w, W.Button) and w.text]


def _click(win, text):
    for w in win.widgets:
        if isinstance(w, W.Button) and w.text == text:
            w.cb()
            return
    raise AssertionError(f"no {text!r} button; got {_buttons(win)}")


# all four actions plus Cancel are present
d = H.make_desk()
d.shell.shutdown_dialog()
win = d.wm.windows[-1]
for want in ("Shut Down", "Restart", "Exit to Terminal",
             "Update and Restart", "Cancel"):
    assert want in _buttons(win), (want, _buttons(win))

# Exit to Terminal quits the desktop (the side-effect-free action to exercise)
assert d.running
_click(win, "Exit to Terminal")
assert not d.running                      # desk.quit() fired
assert win not in d.wm.windows            # dialog closed after choosing

# Cancel just closes; the desktop keeps running
d2 = H.make_desk()
d2.shell.shutdown_dialog()
w2 = d2.wm.windows[-1]
_click(w2, "Cancel")
assert w2 not in d2.wm.windows and d2.running

# Update-and-Restart uses the most complete updater available
real_exists = os.path.exists
try:
    os.path.exists = lambda p: p == "/usr/local/bin/plebian-os-update"
    assert d.shell._best_update_command() == "/usr/local/bin/plebian-os-update"
    os.path.exists = lambda p: p.endswith("/pleb/bin/pleb")
    assert d.shell._best_update_command().endswith('/bin/pleb" update')
    os.path.exists = lambda p: False
    assert d.shell._best_update_command().endswith('kilix" update')
finally:
    os.path.exists = real_exists

print("test_shutdown OK")
