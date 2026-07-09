import icons
import harness as H


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


test_mux_icon_renders()
test_desktop_mux_terminal_launcher()
print("test_mux_terminal OK")
