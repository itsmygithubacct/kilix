"""Bundled provider keeps the authoritative password-safety behavior."""
import types

import harness as H
import security
import widgets as W


# Never probe or invoke the host's real privileged helper. The suite also runs
# on Plebian-OS itself, where that helper is expected to exist.
security.available = lambda: False
assert security.is_default_password() is False
ok, message = security.change_password("unused")
assert ok is False and "not available" in message

d = H.make_desk()
taskbar = d.taskbar
assert "password" not in [entry[0] for entry in taskbar._tray_icons()]
d.password_nag = True
assert "password" in [entry[0] for entry in taskbar._tray_icons()]

entry = next(item for item in taskbar._tray_icons() if item[0] == "password")
H.click(d, (entry[2] + entry[3]) // 2,
        (taskbar.rect()[1] + taskbar.rect()[3]) // 2)
dialog = next(win for win in d.wm.windows if win.title == "Change Password")
fields = [widget for widget in dialog.widgets if isinstance(widget, W.TextField)]
assert len(fields) == 2 and all(field.mask for field in fields)
new, confirm = fields
new.text = "secret-value"
assert new._disp() == "•" * len(new.text)

new.text, confirm.text = "new-secret", "new-secret"
security.change_password = lambda _password: (True, "changed")
security.is_default_password = lambda: False
confirm.on_enter()
assert dialog not in d.wm.windows
assert d.password_nag is False

# Masked selections cannot leak to the shared/host clipboard.
field = W.TextField(0, 0, 100, mask=True)
field.window = types.SimpleNamespace(
    desk=d, focus=field, invalidate=lambda: None, caret_on=True)
field.text, field.anchor, field.cur = "hunter2", 0, 7
d.set_clipboard("")
field.on_key(W.Ev(kind="key", key="c", ctrl=True, text=""))
assert d.clipboard == ""

print("ok")
