import os
import signal

import harness as H
import shell as shell_mod
import widgets as W


def _btn(win, label):
    for wd in win.widgets:
        if isinstance(wd, W.Button) and wd.text == label:
            return wd
    raise AssertionError(f"no {label!r} button in {win.title!r}")


def _menu_item(desk, label):
    for it in desk.menus.stack[-1].items:
        if it.label == label:
            return it
    raise AssertionError(f"no {label!r} item; got "
                         f"{[i.label for i in desk.menus.stack[-1].items]}")


# ── F07: a non-UTF-8 .desktop file must not crash the desktop at startup ──────
def f07_bad_utf8_launcher():
    with H.desktop_dir() as dd:
        with open(os.path.join(dd, "Evil.desktop"), "wb") as f:
            f.write(b"[Desktop Entry]\nName=bad\n\xff\xfe\x00binary")
        d = H.make_desk()                       # pre-fix: UnicodeDecodeError here
    labels = {it["label"] for it in d.shell.grid.items}
    assert "My Computer" in labels              # shell built despite the bad file


# ── F53: a .state.json whose top level is not a dict must not crash startup ───
def f53_array_state_json():
    with H.desktop_dir() as dd:
        with open(os.path.join(dd, ".state.json"), "w") as f:
            f.write("[1, 2]")
        d = H.make_desk()                       # pre-fix: TypeError from dict.update
    assert d.shell.state["wall_mode"] == "stretch"   # defaults regenerated


# ── F34: opening a FIFO must refuse politely, never block the loop ────────────
def f34_fifo_refused():
    d = H.make_desk()
    fifo = os.path.join(d.shell.dir, "pipe")
    os.mkfifo(fifo)

    class _Timeout(BaseException):
        pass

    def _boom(sig, frm):
        raise _Timeout()

    signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, 2.0)    # pre-fix open(2) blocks forever
    try:
        d.shell.open_path(fifo)
    except _Timeout:
        raise AssertionError("open_path blocked on the FIFO")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    box = H.find_window(d, "Window")
    assert box is not None and box.modal, "no refusal dialog"
    assert fifo not in d.shell.state["recent"], "special file leaked into recents"


# ── F35: rename must not silently clobber an existing file ────────────────────
def f35_rename_no_clobber():
    d = H.make_desk()
    with open(os.path.join(d.shell.dir, "a.txt"), "w") as f:
        f.write("AAA")
    with open(os.path.join(d.shell.dir, "precious.txt"), "w") as f:
        f.write("PRECIOUS")
    d.shell.refresh()
    item = next(i for i in d.shell.grid.items if i["label"] == "a.txt")
    d.shell._rename_item(item)
    box = H.find_window(d, "Window")             # the Rename inputbox
    box.focus.set("precious.txt")
    H.key(d, "Enter")
    with open(os.path.join(d.shell.dir, "precious.txt")) as f:
        assert f.read() == "PRECIOUS"            # pre-fix: clobbered to 'AAA'
    assert os.path.exists(os.path.join(d.shell.dir, "a.txt"))


def f35_rename_normal_still_works():
    d = H.make_desk()
    with open(os.path.join(d.shell.dir, "a.txt"), "w") as f:
        f.write("AAA")
    d.shell.refresh()
    item = next(i for i in d.shell.grid.items if i["label"] == "a.txt")
    d.shell._rename_item(item)
    box = H.find_window(d, "Window")
    box.focus.set("b.txt")
    H.key(d, "Enter")
    assert os.path.exists(os.path.join(d.shell.dir, "b.txt"))
    assert not os.path.exists(os.path.join(d.shell.dir, "a.txt"))


def f35_rename_rejects_path_name():
    d = H.make_desk()
    with open(os.path.join(d.shell.dir, "a.txt"), "w") as f:
        f.write("AAA")
    outside_name = os.path.basename(d.shell.dir) + "-renamed.txt"
    outside = os.path.join(os.path.dirname(d.shell.dir), outside_name)
    d.shell.refresh()
    item = next(i for i in d.shell.grid.items if i["label"] == "a.txt")
    d.shell._rename_item(item)
    box = H.find_window(d, "Window")
    box.focus.set("../" + outside_name)
    H.key(d, "Enter")
    assert os.path.exists(os.path.join(d.shell.dir, "a.txt"))
    assert not os.path.exists(outside)


def f58_launcher_write_error_dialog():
    d = H.make_desk()
    old = shell_mod.write_launcher

    def boom(path, spec):
        raise OSError("disk full")

    shell_mod.write_launcher = boom
    try:
        d.shell.create_launcher_dialog(prefill_cmd="true")
        dlg = H.find_window(d, "Window")
        _btn(dlg, "OK").cb()
    finally:
        shell_mod.write_launcher = old

    err = H.find_window(d, "Window")
    assert err is not None and err.modal
    assert err.title == "Create Launcher"


# ── F55: context Delete on a selected icon deletes the whole selection ────────
def f55_context_delete_multi():
    d = H.make_desk()
    for n in ("a.txt", "b.txt", "c.txt"):
        open(os.path.join(d.shell.dir, n), "w").close()
    d.shell.refresh()
    idx = {i["label"]: n for n, i in enumerate(d.shell.grid.items)}
    d.shell.grid.sel = {idx["a.txt"], idx["b.txt"], idx["c.txt"]}
    item_a = d.shell.grid.items[idx["a.txt"]]
    d.shell._context(item_a, H.ev("mouse", x=40, y=40))
    _menu_item(d, "Delete…").action()       # pre-fix: deletes only a.txt
    _btn(H.find_window(d, "Window"), "Yes").cb()
    for n in ("a.txt", "b.txt", "c.txt"):
        assert not os.path.exists(os.path.join(d.shell.dir, n)), f"{n} survived"


def f55_context_delete_unselected_one():
    # right-clicking an icon that is NOT in the selection deletes only it
    d = H.make_desk()
    for n in ("a.txt", "b.txt"):
        open(os.path.join(d.shell.dir, n), "w").close()
    d.shell.refresh()
    idx = {i["label"]: n for n, i in enumerate(d.shell.grid.items)}
    d.shell.grid.sel = {idx["a.txt"]}
    item_b = d.shell.grid.items[idx["b.txt"]]
    assert d.shell._sel_or_one(item_b) == [item_b]


# ── F56: a launcher/document named "-" must stay clickable (not a separator) ──
def f56_dash_launcher_not_separator():
    import shell as S
    assert S._menu_label("-") != "-"
    assert S._menu_label("") == "(unnamed)"
    assert S._menu_label("Firefox") == "Firefox"
    d = H.make_desk()
    with open(os.path.join(d.shell.dir, "-.desktop"), "w") as f:
        f.write("[Desktop Entry]\nName=-\nExec=true\n")
    d.shell.refresh()
    items = d.shell.launcher_menu_items()
    assert items and items[0].label != "-"       # pre-fix: label '-' -> separator


def f56_dash_recent_doc_not_separator():
    d = H.make_desk()
    p = os.path.join(d.shell.dir, "-")
    open(p, "w").close()
    d.shell.state["recent"] = [p]
    assert d.shell.recent_docs()[0][0] != "-"


f07_bad_utf8_launcher()
f53_array_state_json()
f34_fifo_refused()
f35_rename_no_clobber()
f35_rename_normal_still_works()
f35_rename_rejects_path_name()
f58_launcher_write_error_dialog()
f55_context_delete_multi()
f55_context_delete_unselected_one()
f56_dash_launcher_not_separator()
f56_dash_recent_doc_not_separator()
print("ok")
