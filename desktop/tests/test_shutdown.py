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

# Restart only exits the old desktop after the replacement tab launches
d3 = H.make_desk()
d3.shell._tab = lambda *_args, **_kw: False
d3.shell._restart_desktop()
assert d3.running
d3.shell._tab = lambda *_args, **_kw: True
d3.shell._restart_desktop()
assert not d3.running

# Update-and-Restart gates the quit on launch success and gates restart on update
d4 = H.make_desk()
seen = {}
d4.shell._best_update_command = lambda: "false"

def _fake_spawn(opts, cmd, title, **kw):
    seen.update({"opts": opts, "cmd": cmd, "title": title, "kw": kw})
    return False

d4.shell._spawn_kitty_launch = _fake_spawn
d4.shell._update_and_restart()
assert d4.running
assert "false && exec env KILIX_IN_OVERLAY=1" in seen["cmd"]
seen.clear()
d4.shell._spawn_kitty_launch = lambda opts, cmd, title, **kw: (
    seen.update({"cmd": cmd, "kw": kw}) or True)
d4.shell._update_and_restart()
assert not d4.running

# Maintenance tabs preserve the command exit status after the pause
d5 = H.make_desk()
seen = {}
d5.shell._spawn_kitty_launch = lambda opts, cmd, title, **kw: (
    seen.update({"cmd": cmd, "kw": kw}) or True)
d5.shell.run_maintenance("false", "Failing Task")
assert seen["kw"]["pause_on_error"] is False
assert "rc=$?" in seen["cmd"] and "exit $rc" in seen["cmd"]

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
