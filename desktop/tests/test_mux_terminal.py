import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import harness as H
import icons


def test_mux_icon_renders():
    icons.get("mux", 16)
    icons.get("mux", 32)


def test_desktop_mux_terminal_launcher():
    d = H.make_desk()
    items = {i["label"]: i for i in d.shell.grid.items}
    item = items["Mux Terminal"]
    assert item["icon"] == "mux"
    assert item["data"] == ("builtin", ("mux", None))

    seen = {}

    def fake_tab(argv, title, cwd=None):
        seen.update(argv=argv, title=title, cwd=cwd)
        return True

    d.shell._tab = fake_tab
    assert d.shell.open_mux_terminal()
    assert seen["argv"][-2:] == ["serve", "main"]
    assert seen["title"] == "Mux: main"


def test_remote_launch_uses_private_credential():
    d = H.make_desk()
    with patch.dict(os.environ, {"KILIX_RC_PASSWORD_FILE": "/tmp/rc-pass"}):
        assert d.shell._kitten_remote("kitten", "launch") == [
            "kitten", "@", "--password-file", "/tmp/rc-pass", "launch",
        ]


def test_kilix_temps_launcher_forces_graphical_tab():
    d = H.make_desk()
    seen = {}

    def fake_tab(argv, title, cwd=None):
        seen.update(argv=argv, title=title, cwd=cwd)
        return True

    d.shell._tab = fake_tab
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory) / "kilix-temps"
        executable = project / "build" / "kilix-temps"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
        with patch.dict(os.environ, {
                "GPU_TERMINAL_SOURCE_HOME": directory}), \
                patch("shell.shutil.which", return_value=None):
            assert d.shell.open_kilix_temps()
    assert seen["argv"] == [str(executable), "--graphics"]
    assert seen["title"] == "Kilix Temps"
    assert seen["cwd"] == str(project)


def test_kilix_temps_installed_command_precedes_source_launcher():
    d = H.make_desk()
    seen = {}
    d.shell._tab = lambda argv, title, cwd=None: seen.update(
        argv=argv, title=title, cwd=cwd) or True
    with tempfile.TemporaryDirectory() as directory:
        raw = Path(directory) / "kilix-temps" / "kilix-temps"
        raw.parent.mkdir(parents=True)
        raw.write_text("#!/bin/sh\n")
        raw.chmod(0o755)
        with patch.dict(os.environ, {
                "GPU_TERMINAL_SOURCE_HOME": directory}), \
                patch("shell.shutil.which",
                      return_value="/usr/local/bin/kilix-temps"):
            assert d.shell.open_kilix_temps()
    assert seen["argv"] == ["/usr/local/bin/kilix-temps", "--graphics"]
    assert seen["cwd"] is None


def test_tmux_manager_opens_in_a_new_tab():
    d = H.make_desk()
    seen = {}
    d.shell._tab = lambda argv, title, cwd=None: seen.update(
        argv=argv, title=title, cwd=cwd) or True
    with patch("shell.shutil.which",
               side_effect=lambda name: "/usr/local/bin/tmux-tui"
               if name == "tmux-tui" else None):
        assert d.shell.open_tmux_manager()
    assert seen["argv"] == ["/usr/local/bin/tmux-tui"]
    assert seen["title"] == "Tmux Manager"
    assert seen["cwd"] == os.path.expanduser("~")


def test_start_menu_names_tmux_manager():
    d = H.make_desk()
    d.taskbar.open_start_menu()
    programs = next(
        item for item in d.menus.stack[0].items if item.label == "Programs")
    assert any(item.label == "Tmux Manager" for item in programs.submenu)


test_mux_icon_renders()
test_desktop_mux_terminal_launcher()
test_remote_launch_uses_private_credential()
test_kilix_temps_launcher_forces_graphical_tab()
test_kilix_temps_installed_command_precedes_source_launcher()
test_tmux_manager_opens_in_a_new_tab()
test_start_menu_names_tmux_manager()
print("test_mux_terminal OK")
