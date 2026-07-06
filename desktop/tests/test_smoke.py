"""Harness smoke test: desk + typing + menus, byte parser, all scenes."""
import harness as H
import apps
import main as desk_main

# offscreen desk, notepad, typing through dispatch_key
d = H.make_desk()
assert d.size() == (1024, 768)
apps.open(d, "notepad", None)
np = H.find_window(d, "Notepad")
assert np is not None and np is d.wm.active
assert np.focus is np.ta
H.type_text(d, "hi")
assert np.ta.text() == "hi", repr(np.ta.text())

# click on the menubar opens the File menu; Escape closes it
gx, gy = np.client_origin()
H.click(d, gx + 10, gy + 5)
assert d.menus.active
H.key(d, "Escape")
assert not d.menus.active
d.render()
assert not d.dirty

# raw bytes: SGR-pixel mouse + kitty 'u' key parse through the pipe fd
evs = H.term_feed(b"\x1b[<64;512;300M\x1b[97u")
assert [e["kind"] for e in evs] == ["mouse", "key"], evs
assert evs[0]["b"] == 64 and evs[0]["x"] == 512 and evs[0]["y"] == 300
assert evs[1]["key"] == "a" and evs[1]["text"] == "a"

# adversarial chunk splitting: every split point, then byte-by-byte
data = b"\x1b[<0;10;10M\x1b[<0;10;10m"
for i in range(len(data) + 1):
    evs = H.term_feed(data, chunks=[i])
    assert [e["kind"] for e in evs] == ["mouse", "mouse"], (i, evs)
assert len(H.term_feed(data, chunks=range(len(data)))) == 2

# every --scene choice renders offscreen without raising
for scene in ["desktop", "start", "filemgr", "notepad", "settings",
              "dialog", "launcher", "menu", "all"]:
    with H.desktop_dir():
        sd = H.make_desk()
        desk_main._scene(sd, scene)
        sd.render()
        assert not sd.dirty, scene

print("ok")
