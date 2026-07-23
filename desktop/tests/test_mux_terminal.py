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
                "GPU_TERMINAL_SOURCE_HOME": directory}):
            assert d.shell.open_kilix_temps()
    assert seen["argv"] == [str(executable), "--graphics"]
    assert seen["title"] == "Kilix Temps"
    assert seen["cwd"] == str(project)


test_mux_icon_renders()
test_desktop_mux_terminal_launcher()
test_remote_launch_uses_private_credential()
test_kilix_temps_launcher_forces_graphical_tab()
print("test_mux_terminal OK")
