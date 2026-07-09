"""Start > System install/update launchers.

Drives shell.system_menu_items() with a faked filesystem (os.path.exists/isdir/
listdir/access monkeypatched) so each detection branch is exercised without
touching the real machine.
"""
import os

import harness as H
import shell as shell_mod


def _labels(items):
    return [it.label for it in items if it.label != "-"]


def _item(items, label):
    for it in items:
        if it.label == label:
            return it
    raise AssertionError(f"missing {label!r}; got {_labels(items)}")


DESK = H.make_desk()          # build BEFORE patching os (Shell.__init__ mkdir)


def run_case(present_files, present_dirs, dir_listing, expect,
             which_tools=()):
    """present_files: set of paths os.path.exists() should return True for.
       present_dirs:  set of paths os.path.isdir() should return True for.
       dir_listing:   {dir: [names]} for os.listdir(); those names are X_OK."""
    real_exists, real_isdir = os.path.exists, os.path.isdir
    real_listdir, real_access = os.listdir, os.access
    real_which = shell_mod.shutil.which
    execable = {os.path.join(d, n) for d, ns in dir_listing.items() for n in ns}

    os.path.exists = lambda p: p in present_files
    os.path.isdir = lambda p: p in present_dirs
    os.listdir = lambda p: dir_listing.get(p, [])
    os.access = lambda p, m: p in execable or p in present_files
    shell_mod.shutil.which = (
        lambda n: f"/usr/local/bin/{n}" if n in which_tools else None)
    try:
        items = DESK.shell.system_menu_items()   # only the call is patched
    finally:
        os.path.exists, os.path.isdir = real_exists, real_isdir
        os.listdir, os.access = real_listdir, real_access
        shell_mod.shutil.which = real_which
    assert _labels(items) == expect, (_labels(items), expect)
    return items


KH = shell_mod.KILIX_HOME
HOME = os.path.expanduser("~")

BASE_SYSTEM = ["Install", "Update"]
BASE_INSTALL = ["Install Claude Code", "Install Codex",
                "Install Google Chrome"]

# Even on a fresh checkout, the System menu offers installers and system update.
items = run_case(set(), set(), {}, BASE_SYSTEM)
assert _labels(_item(items, "Install").submenu) == BASE_INSTALL
assert _labels(_item(items, "Update").submenu) == ["Update System"]

# a bare kilix git checkout (no pleb) adds the kilix updater.
items = run_case(
    present_files={os.path.join(KH, "kilix")},
    present_dirs={os.path.join(KH, ".git")},
    dir_listing={},
    expect=BASE_SYSTEM)
assert _labels(_item(items, "Update").submenu) == ["Update System",
                                                   "Update kilix"]

# pleb present but not a kilix checkout adds the pleb updater.
items = run_case(
    present_files={os.path.join(HOME, "pleb", "bin", "pleb")},
    present_dirs=set(),
    dir_listing={},
    expect=BASE_SYSTEM)
assert _labels(_item(items, "Update").submenu) == [
    "Update System", "Update Pleb (kilix + session)"]

# Claude/Codex update items appear only when the commands are installed.
items = run_case(set(), set(), {}, BASE_SYSTEM,
                 which_tools={"claude", "codex"})
assert _labels(_item(items, "Update").submenu) == [
    "Update System", "Update Claude Code", "Update Codex"]

# a full Plebian-OS box: kilix + pleb + the installed stack scripts + an extra
# maintenance script under ~/pleb/scripts
items = run_case(
    present_files={
        os.path.join(KH, "kilix"),
        os.path.join(HOME, "pleb", "bin", "pleb"),
        "/usr/local/bin/plebian-os-update",
        "/usr/local/sbin/plebian-os-install-deps",
    },
    present_dirs={
        os.path.join(KH, ".git"),
        os.path.join(HOME, "pleb", "scripts"),
    },
    dir_listing={os.path.join(HOME, "pleb", "scripts"):
                 ["install-go.sh", "notes.txt"]},
    expect=BASE_SYSTEM + ["Scripts"],
    which_tools={"claude", "codex"})
assert _labels(_item(items, "Install").submenu) == (
    BASE_INSTALL + ["Reinstall dependencies"])
assert _labels(_item(items, "Update").submenu) == [
    "Update System", "Update Claude Code", "Update Codex", "Update kilix",
    "Update Pleb (kilix + session)", "Update Plebian-OS (kilix + pleb)"]
# the Scripts submenu carries only the executable *.sh (not notes.txt)
scripts = _item(items, "Scripts")
assert _labels(scripts.submenu) == ["install-go.sh"], _labels(scripts.submenu)

# Script-backed entries launch through run_maintenance, which opens a new tab.
seen = []
real_run_maintenance = DESK.shell.run_maintenance
DESK.shell.run_maintenance = lambda cmd, title: seen.append((cmd, title))
try:
    _item(_item(items, "Install").submenu, "Install Claude Code").action()
    _item(_item(items, "Update").submenu, "Update System").action()
finally:
    DESK.shell.run_maintenance = real_run_maintenance
script_dir = os.path.join(shell_mod._here, "scripts")
assert seen == [
    (os.path.join(script_dir, "install-claude-code.sh"),
     "Install Claude Code"),
    (os.path.join(script_dir, "update-system.sh"), "Update System"),
], seen

print("test_system_menu OK")
