"""Graphical password prompt for Kilix's sudo launch.

sudo runs this program with --askpass. The typed password is written to
stdout only after a second check that the parent is still setuid sudo
running the trusted GParted command. Cancel and every refusal write nothing.
"""

import os
import sys

from kilix_sdk.elevation import trusted_sudo_target


def read_parent_process(pid=None):
    if pid is None:
        pid = os.getppid()
    proc_exe = f"/proc/{pid}/exe"
    try:
        link = os.readlink(proc_exe)
    except OSError:
        return None
    if link.endswith(" (deleted)"):
        return None
    try:
        info = os.stat(proc_exe)
        with open(f"/proc/{pid}/cmdline", "rb") as stream:
            raw = stream.read(1 << 20)
    except OSError:
        return None
    if not raw or len(raw) >= (1 << 20):
        return None
    parts = raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    try:
        argv = [part.decode("utf-8", "strict") for part in parts]
    except UnicodeError:
        return None
    try:
        exe = os.path.realpath(link)
    except OSError:
        return None
    return {"exe": exe, "mode": info.st_mode, "uid": info.st_uid, "argv": argv}


def gtk_password_dialog(message):
    """Modal hidden-entry dialog. Return the password, or None on cancel."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    initialized = Gtk.init_check([])
    if isinstance(initialized, tuple):
        initialized = initialized[0]
    if not initialized:
        raise RuntimeError("the graphical session is not available")
    dialog = Gtk.Dialog(title="Authentication Required", modal=True)
    dialog.set_resizable(False)
    dialog.set_position(Gtk.WindowPosition.CENTER)
    dialog.set_keep_above(True)
    dialog.set_icon_name("dialog-password")
    dialog.set_default_size(440, -1)
    dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    dialog.add_button("Authenticate", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    area = dialog.get_content_area()
    area.set_margin_top(12)
    area.set_margin_bottom(8)
    area.set_margin_start(12)
    area.set_margin_end(12)
    area.set_spacing(8)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.pack_start(Gtk.Image.new_from_icon_name("dialog-password", Gtk.IconSize.DIALOG),
                   False, False, 0)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    heading = Gtk.Label(xalign=0)
    heading.set_markup("<b>Authentication Required</b>")
    body = Gtk.Label(label=message, wrap=True, xalign=0, max_width_chars=52)
    text.pack_start(heading, False, False, 0)
    text.pack_start(body, False, False, 0)
    row.pack_start(text, True, True, 0)
    entry = Gtk.Entry()
    entry.set_visibility(False)
    entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    entry.set_activates_default(True)
    entry.set_placeholder_text("Password")
    area.pack_start(row, False, False, 0)
    area.pack_start(entry, False, False, 0)
    dialog.show_all()
    entry.grab_focus()
    response = dialog.run()
    revealed = entry.get_text() if response == Gtk.ResponseType.OK else None
    entry.set_text("")
    dialog.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)
    return revealed


def main(argv=None, *, dialog=None, parent_reader=None, stdout=None, stderr=None,
         stat_fn=os.stat, lstat_fn=os.lstat, realpath_fn=os.path.realpath):
    if argv is None:
        argv = sys.argv
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    if parent_reader is None:
        parent_reader = read_parent_process
    if dialog is None:
        dialog = gtk_password_dialog
    checks = dict(stat_fn=stat_fn, lstat_fn=lstat_fn, realpath_fn=realpath_fn)
    target = trusted_sudo_target(parent_reader(), **checks)
    if target is None:
        stderr.write("kilix askpass: refused\n")
        return 1
    try:
        password = dialog(f"Authentication is required to run {target} as root.")
    except Exception:
        stderr.write("kilix askpass: the password dialog failed\n")
        return 1
    if not isinstance(password, str) or "\n" in password or "\x00" in password or len(password) > 1024:
        return 1
    if trusted_sudo_target(parent_reader(), **checks) != target:
        stderr.write("kilix askpass: refused\n")
        return 1
    stdout.write(password + "\n")
    stdout.flush()
    return 0
