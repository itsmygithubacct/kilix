"""xdgapps — freedesktop application scanner.

All fixtures are throwaway temp dirs exported via XDG_DATA_HOME/XDG_DATA_DIRS
with generic sample .desktop files written here — no real system paths, no
personal data.
"""
import os
import tempfile

import harness  # noqa: F401  (sets sys.path for the desktop package)
import xdgapps


def _fresh():
    """(home_base, sys_base) two temp XDG data dirs, each with applications/."""
    home = tempfile.mkdtemp(prefix="kilix95-xdg-home-")
    sysd = tempfile.mkdtemp(prefix="kilix95-xdg-sys-")
    for b in (home, sysd):
        os.makedirs(os.path.join(b, "applications"), exist_ok=True)
    return home, sysd


def _apps(base):
    return os.path.join(base, "applications")


def _write(base, name, text):
    p = os.path.join(_apps(base), name)
    with open(p, "w") as f:
        f.write(text)
    return p


def _use(home, sysd):
    os.environ["XDG_DATA_HOME"] = home
    os.environ["XDG_DATA_DIRS"] = sysd
    xdgapps.scan(force=True)          # drop any cache from a previous fixture


def _entry(scan_result, name):
    for e in scan_result:
        if e["name"] == name:
            return e
    return None


def _desktop(name, exec_="/usr/bin/true", cats="Utility", extra=""):
    return ("[Desktop Entry]\nType=Application\nName=%s\nExec=%s\n"
            "Categories=%s;\n%s" % (name, exec_, cats, extra))


# ── a normal app is found and Exec field codes are stripped ──────────────────
def normal_app_and_field_codes():
    home, sysd = _fresh()
    _write(home, "editor.desktop",
           _desktop("Editor", exec_="/usr/bin/editor --new %F"))
    _use(home, sysd)
    e = _entry(xdgapps.scan(), "Editor")
    assert e is not None, "app not discovered"
    assert e["exec"] == "/usr/bin/editor --new", e["exec"]
    assert e["id"] == "editor.desktop"
    assert e["categories"] == ["Utility"]
    assert e["terminal"] is False


def percent_literal_kept():
    home, sysd = _fresh()
    _write(home, "pct.desktop", _desktop("Pct", exec_="run 100%% %U"))
    _use(home, sysd)
    assert _entry(xdgapps.scan(), "Pct")["exec"] == "run 100%"


def quoted_arg_spaces_kept():
    # stripping %F must not collapse runs of spaces inside a quoted argument
    home, sysd = _fresh()
    _write(home, "sp.desktop",
           _desktop("Sp", exec_='app "two  spaces" %F --flag'))
    _use(home, sysd)
    assert _entry(xdgapps.scan(), "Sp")["exec"] == 'app "two  spaces" --flag'


# ── NoDisplay / Hidden / TryExec-missing / Type!=Application are skipped ──────
def filtered_entries_skipped():
    home, sysd = _fresh()
    _write(home, "ok.desktop", _desktop("Keep"))
    _write(home, "nd.desktop", _desktop("Nope", extra="NoDisplay=true\n"))
    _write(home, "hid.desktop", _desktop("Gone", extra="Hidden=true\n"))
    _write(home, "try.desktop",
           _desktop("Missing", extra="TryExec=kilix_no_such_binary_zzz\n"))
    _write(home, "dir.desktop",
           "[Desktop Entry]\nType=Directory\nName=Folder\n")
    _use(home, sysd)
    names = {e["name"] for e in xdgapps.scan()}
    assert names == {"Keep"}, names


def tryexec_present_kept():
    home, sysd = _fresh()
    _write(home, "sh.desktop", _desktop("Shellish", extra="TryExec=sh\n"))
    _use(home, sysd)
    assert _entry(xdgapps.scan(), "Shellish") is not None


# ── dedup precedence: user dir overrides system dir ──────────────────────────
def dedup_user_wins():
    home, sysd = _fresh()
    _write(home, "shared.desktop", _desktop("UserVersion"))
    _write(sysd, "shared.desktop", _desktop("SysVersion"))
    _use(home, sysd)
    res = xdgapps.scan()
    assert _entry(res, "UserVersion") is not None
    assert _entry(res, "SysVersion") is None
    assert sum(e["id"] == "shared.desktop" for e in res) == 1


def hidden_user_masks_system():
    # a Hidden entry in the user dir deletes the id — the system one must not
    # resurface
    home, sysd = _fresh()
    _write(home, "shared.desktop", _desktop("U", extra="Hidden=true\n"))
    _write(sysd, "shared.desktop", _desktop("S"))
    _use(home, sysd)
    assert _entry(xdgapps.scan(), "S") is None


# ── categories bucket correctly ──────────────────────────────────────────────
def buckets():
    def one(cats):
        return xdgapps.bucket({"categories": cats.split(";")})
    assert one("Network") == "Internet"
    assert one("AudioVideo") == "Multimedia"
    assert one("Game") == "Games"
    assert one("Settings") == "System"
    assert one("System") == "System"
    assert one("Graphics;Utility") == "Graphics"   # specific beats Utility
    assert one("Utility") == "Accessories"
    assert one("Weird") == "Other"
    assert xdgapps.icon_for({"categories": ["Network"]}) == "browser"
    assert xdgapps.icon_for({"categories": []}) == "app"


def grouped_shape():
    home, sysd = _fresh()
    _write(home, "web.desktop", _desktop("Web", cats="Network"))
    _write(home, "pix.desktop", _desktop("Pix", cats="Graphics"))
    _use(home, sysd)
    g = xdgapps.grouped()
    assert g["Internet"][0]["name"] == "Web"
    assert g["Graphics"][0]["name"] == "Pix"
    assert list(g.keys()) == ["Graphics", "Internet"]   # display order
    assert "Other" not in g


# ── a garbage / binary file is ignored, never raises ─────────────────────────
def garbage_ignored():
    home, sysd = _fresh()
    _write(home, "good.desktop", _desktop("Good"))
    with open(os.path.join(_apps(home), "junk.desktop"), "wb") as f:
        f.write(b"\xff\xfe\x00\x01 not a desktop file \x80\x90")
    with open(os.path.join(_apps(home), "empty.desktop"), "w") as f:
        f.write("random text, no group here\n")
    _use(home, sysd)
    names = {e["name"] for e in xdgapps.scan()}
    assert names == {"Good"}, names


def missing_dirs_fine():
    # point at dirs with no applications/ subdir at all
    empty = tempfile.mkdtemp(prefix="kilix95-xdg-empty-")
    os.environ["XDG_DATA_HOME"] = empty
    os.environ["XDG_DATA_DIRS"] = "/kilix/does/not/exist"
    assert xdgapps.app_dirs() == []
    assert xdgapps.scan(force=True) == []


def spec_defaults_when_unset():
    os.environ.pop("XDG_DATA_HOME", None)
    os.environ.pop("XDG_DATA_DIRS", None)
    # must not raise; returns whatever exists under the spec defaults
    xdgapps.scan(force=True)
    dirs = xdgapps.app_dirs()
    assert all(d.endswith("applications") for d in dirs)


# ── caching: fast on the second call, refreshes on mtime change / force ──────
def caching():
    home, sysd = _fresh()
    _write(home, "a.desktop", _desktop("Aardvark"))
    _use(home, sysd)
    r1 = xdgapps.scan()
    r2 = xdgapps.scan()
    assert r1 is r2, "second call did not hit the cache"
    # add a file and bump the dir mtime deterministically → auto rescan
    _write(home, "b.desktop", _desktop("Beetle"))
    t = os.stat(_apps(home)).st_mtime + 100
    os.utime(_apps(home), (t, t))
    r3 = xdgapps.scan()
    assert r3 is not r2
    assert _entry(r3, "Beetle") is not None
    # force always rescans (fresh object)
    r4 = xdgapps.scan(force=True)
    assert r4 is not r3


def cache_sees_inplace_and_subdir_edits():
    # editing a file in place (dir mtime unchanged) and adding one inside a
    # subdir must both invalidate the cache
    home, sysd = _fresh()
    p = _write(home, "c.desktop", _desktop("Cat"))
    _use(home, sysd)
    r1 = xdgapps.scan()
    assert _entry(r1, "Cat") is not None
    # rewrite in place, pin the applications/ dir mtime so only the file changed
    dirt = os.stat(_apps(home)).st_mtime
    with open(p, "w") as f:
        f.write(_desktop("Cougar"))
    ft = dirt + 100
    os.utime(p, (ft, ft))
    os.utime(_apps(home), (dirt, dirt))
    r2 = xdgapps.scan()
    assert r2 is not r1
    assert _entry(r2, "Cougar") is not None
    # add a file inside a subdirectory (top-level dir mtime pinned again)
    sub = os.path.join(_apps(home), "sub")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "d.desktop"), "w") as f:
        f.write(_desktop("Deer"))
    os.utime(_apps(home), (dirt, dirt))
    r3 = xdgapps.scan()
    assert r3 is not r2
    assert _entry(r3, "Deer") is not None
    assert _entry(r3, "Deer")["id"] == "sub-d.desktop"


def sorted_case_insensitive():
    home, sysd = _fresh()
    _write(home, "b.desktop", _desktop("banana"))
    _write(home, "a.desktop", _desktop("Apple"))
    _use(home, sysd)
    assert [e["name"] for e in xdgapps.scan()] == ["Apple", "banana"]


# ── localized Name honored for the current locale ────────────────────────────
def localized_name():
    keep = {k: os.environ.get(k) for k in ("LC_ALL", "LC_MESSAGES", "LANG")}
    home, sysd = _fresh()
    _write(home, "loc.desktop",
           "[Desktop Entry]\nType=Application\nExec=/usr/bin/true\n"
           "Name=English\nName[de]=Deutsch\nCategories=Utility;\n")
    try:
        os.environ["LC_ALL"] = "de_DE.UTF-8"
        os.environ.pop("LC_MESSAGES", None)
        os.environ["LANG"] = "de_DE.UTF-8"
        _use(home, sysd)
        assert _entry(xdgapps.scan(), "Deutsch") is not None
        os.environ["LC_ALL"] = "C"
        assert _entry(xdgapps.scan(force=True), "English") is not None
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── launch synthesizes a Shell spec ──────────────────────────────────────────
def launch_spec():
    class _StubShell:
        def __init__(self):
            self.spec = None

        def launch(self, spec, path=None):
            self.spec = spec

    gui = {"name": "Gui", "exec": "/usr/bin/gui", "terminal": False,
           "workdir": ""}
    term = {"name": "Term", "exec": "htop", "terminal": True,
            "workdir": "/data"}
    sh = _StubShell()
    xdgapps.launch(sh, gui)
    assert sh.spec == {"Name": "Gui", "Exec": "/usr/bin/gui", "Path": "~",
                       "X-Kilix-Open": "run"}
    xdgapps.launch(sh, term)
    assert sh.spec["X-Kilix-Open"] == "tab"
    assert sh.spec["Path"] == "/data"


normal_app_and_field_codes()
percent_literal_kept()
quoted_arg_spaces_kept()
filtered_entries_skipped()
tryexec_present_kept()
dedup_user_wins()
hidden_user_masks_system()
buckets()
grouped_shape()
garbage_ignored()
missing_dirs_fine()
spec_defaults_when_unset()
caching()
cache_sees_inplace_and_subdir_edits()
sorted_case_insensitive()
localized_name()
launch_spec()
print("ok")
