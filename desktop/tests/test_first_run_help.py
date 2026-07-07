"""First launch opens the Help book once; the persisted marker stops repeats."""
import harness as H

# a fresh desktop has no marker → the first call opens Help and sets it
d = H.make_desk()
assert not d.shell.state.get("help_shown")
n0 = len(d.wm.windows)
d._first_run_help()
assert d.shell.state.get("help_shown") is True, "marker not persisted"
assert len(d.wm.windows) == n0 + 1, "Help did not open on first run"
assert H.find_window(d, "Help") is not None, "the opened window is not Help"

# a second call is a no-op — Help pops exactly once
d._first_run_help()
assert len(d.wm.windows) == n0 + 1, "Help opened a second time"

# a desktop whose state already carries the marker never opens Help
d2 = H.make_desk()
d2.shell.state["help_shown"] = True
m0 = len(d2.wm.windows)
d2._first_run_help()
assert len(d2.wm.windows) == m0, "Help opened despite the marker"

print("ok")
