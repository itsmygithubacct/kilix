import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import harness as H
import icons


def test_mux_icon_renders():
    icons.get("mux", 16)
    icons.get("mux", 32)
    icons.get("memory", 16)
    icons.get("memory", 32)


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


def test_pdf_viewer_chooser_opens_the_catalog_app_in_a_desktop_window():
    d = H.make_desk()
    seen = {}
    d.shell.open_in_xpane = lambda argv, title, **kwargs: seen.update(
        argv=list(argv), title=title, kwargs=kwargs) or True
    with tempfile.TemporaryDirectory() as directory:
        document = str(Path(directory) / "report.pdf")
        with patch("filedialog.open_file",
                   side_effect=lambda _desk, _title, cb, **_kwargs:
                   cb(document) or True):
            assert d.shell.open_kilix_pdf()
    assert seen["argv"] == [
        str(Path(H.KILIX_HOME) / "kilix"), "app", "window",
        "kilix-pdf", "--action", "open", "--", document,
    ]
    assert seen["title"] == "PDF Viewer"
    assert seen["kwargs"]["icon"] == "doc_text"
    assert seen["kwargs"]["app_size"] == (960, 700)
    assert seen["kwargs"]["cwd"] == os.path.expanduser("~")


def test_pdf_file_association_uses_the_viewer():
    d = H.make_desk()
    seen = {}
    d.shell.open_catalog_application = lambda content_id, **kwargs: seen.update(
        content_id=content_id, kwargs=kwargs) or True
    with tempfile.TemporaryDirectory() as directory:
        document = Path(directory) / "manual.PDF"
        document.write_bytes(b"%PDF-1.4\n")
        d.shell.open_path(str(document))
    assert seen == {
        "content_id": "kilix-pdf",
        "kwargs": {"action": "open", "arguments": (str(document),)},
    }


def test_every_catalog_application_reaches_a_managed_desktop_window():
    d = H.make_desk()
    d.taskbar.open_start_menu()
    programs = next(
        item for item in d.menus.stack[0].items if item.label == "Programs")
    catalog = next(
        item for item in programs.submenu if item.label == "Kilix Applications")
    labels = {item.label for item in catalog.submenu}
    for expected in ("File Manager", "System Center", "Software Center",
                     "Voice Studio", "Character Map", "Notepad"):
        assert expected in labels, (expected, sorted(labels))

    seen = {}
    d.shell.open_in_xpane = lambda argv, title, **kwargs: seen.update(
        argv=list(argv), title=title, kwargs=kwargs) or True
    system = next(
        item for item in catalog.submenu if item.label == "System Center")
    system.action()
    assert seen["argv"] == [
        str(Path(H.KILIX_HOME) / "kilix"), "app", "window",
        "kilix-system-center",
    ]
    assert seen["kwargs"]["application_id"] == "kilix-system-center"

    existing = type("ExistingWindow", (), {
        "kilix_application_id": "kilix-system-center",
    })()
    d.wm.windows.append(existing)
    activated = []
    d.wm.activate = activated.append
    d.shell.open_in_xpane = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("a single-instance app opened a duplicate window"))
    assert d.shell.open_catalog_application("kilix-system-center")
    assert activated == [existing]


def test_kilix_temps_launcher_forces_graphical_tab():
    d = H.make_desk()
    seen = {}

    def fake_tab(argv, title, cwd=None):
        seen.update(argv=argv, title=title, cwd=cwd)
        return True

    d.shell._tab = fake_tab
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory) / "kilix-desktops" / "kilix-tui-utils"
        entry = project / "tools" / "temps" / "main.py"
        entry.parent.mkdir(parents=True)
        entry.write_text("print('fixture')\n")
        with patch.dict(os.environ, {
                "GPU_TERMINAL_SOURCE_HOME": directory}), \
                patch("shell.shutil.which", return_value=None):
            assert d.shell.open_kilix_temps()
    assert seen["argv"] == ["python3", str(entry), "--graphics"]
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


def test_kilix_memory_launcher_forces_graphical_tab():
    d = H.make_desk()
    seen = {}
    d.shell._tab = lambda argv, title, cwd=None: seen.update(
        argv=argv, title=title, cwd=cwd) or True
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory) / "kilix-desktops" / "kilix-tui-utils"
        entry = project / "tools" / "memory" / "main.py"
        entry.parent.mkdir(parents=True)
        entry.write_text("print('fixture')\n")
        with patch.dict(os.environ, {
                "GPU_TERMINAL_SOURCE_HOME": directory}), \
                patch("shell.shutil.which", return_value=None):
            assert d.shell.open_kilix_memory()
    assert seen == {
        "argv": ["python3", str(entry), "--graphics"],
        "title": "Kilix Memory",
        "cwd": str(project),
    }


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


def test_pty_manager_uses_kilix_without_brokering_itself():
    d = H.make_desk()
    seen = {}
    d.shell._tab = lambda argv, title, cwd=None, env=None: seen.update(
        argv=argv, title=title, cwd=cwd, env=env) or True
    assert d.shell.open_pty_manager()
    assert os.path.basename(seen["argv"][0]) == "kilix"
    assert seen["argv"][1:] == ["pty"]
    assert seen["title"] == "PTY Sessions"
    assert seen["cwd"] == os.path.expanduser("~")
    assert seen["env"] == {"KITTY_PTY_BROKER_BYPASS": "1"}


def test_start_menu_names_tmux_manager():
    d = H.make_desk()
    seen = []
    d.shell.open_pty_manager = lambda: seen.append("pty")
    d.taskbar.open_start_menu()
    programs = next(
        item for item in d.menus.stack[0].items if item.label == "Programs")
    assert any(item.label == "Tmux Manager" for item in programs.submenu)
    pty = next(item for item in programs.submenu
               if item.label == "PTY Sessions")
    pty.action()
    assert seen == ["pty"]
    assert any(item.label == "Kilix Memory" for item in programs.submenu)


test_mux_icon_renders()
test_desktop_mux_terminal_launcher()
test_remote_launch_uses_private_credential()
test_pdf_viewer_chooser_opens_the_catalog_app_in_a_desktop_window()
test_pdf_file_association_uses_the_viewer()
test_every_catalog_application_reaches_a_managed_desktop_window()
test_kilix_temps_launcher_forces_graphical_tab()
test_kilix_temps_installed_command_precedes_source_launcher()
test_kilix_memory_launcher_forces_graphical_tab()
test_tmux_manager_opens_in_a_new_tab()
test_pty_manager_uses_kilix_without_brokering_itself()
test_start_menu_names_tmux_manager()
print("test_mux_terminal OK")
