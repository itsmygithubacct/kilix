"""games.py: a broken games.conf must not crash a *_ready() check (F33),
and an installer error that isn't RuntimeError/OSError must be shown, not
leaked out of main() with the tab (F36). No network: conf files + stubs only."""
import builtins
import io
import os
import sys
import tarfile
import tempfile
import zipfile

import harness as H       # noqa: F401  (sets up sys.path for `import games`)
import games

tmp = tempfile.mkdtemp(prefix="games-test-")
games.CONF = os.path.join(tmp, "games.conf")
games.GAMES_DIR = os.path.join(tmp, "games")   # isolate the vendored-binary scan


def write(text):
    with open(games.CONF, "w") as f:
        f.write(text)


# F33a: a syntactically bad conf (missing '=') reads as empty, not a crash.
# Pre-fix load() let configparser.ParsingError escape doom_ready().
write("[doom]\ndosbox /usr/bin/dosbox\n")
assert games.doom_ready() is None
assert games.bashed_ready() is None
cp = games.load()
assert not cp.has_section("doom"), "malformed conf should load as empty"

# F33b: a '%' in a stored path is literal, not an interpolation token.
# Pre-fix cp.get() raised InterpolationSyntaxError from BasicInterpolation.
write("[doom]\ndosbox = /opt/games/%stuff/dosbox\ndir = /nonexistent\n")
assert games.doom_ready() is None
cp = games.load()
assert cp.get("doom", "dosbox") == "/opt/games/%stuff/dosbox"

# a well-formed conf still round-trips
write("[doom]\ndosbox = /nonexistent/dosbox\ndir = /nonexistent\n")
cp = games.load()
assert cp.get("doom", "dir") == "/nonexistent"
assert games.doom_ready() is None            # path doesn't exist -> not ready


# DOSBox is a first-class Games entry, launchable on its own; game_ready
# dispatches to dosbox_ready, which finds a dosbox on $PATH without installing.
assert "dosbox" in games.GAMES and games.GAMES["dosbox"]["icon"] == "dosbox"
import shutil as _sh
_which = _sh.which
_sh.which = lambda n: "/usr/bin/dosbox" if n == "dosbox" else None
try:
    write("")                                    # empty conf, no [dosbox]
    assert games.dosbox_ready(games.load()) == "/usr/bin/dosbox"
    assert games.game_ready("dosbox") == "/usr/bin/dosbox"
    _sh.which = lambda n: None                    # nothing on PATH, none vendored
    assert games.dosbox_ready(games.load()) is None
    assert games.game_ready("nonesuch") is None
finally:
    _sh.which = _which

# Terminal Lander is a first-class Games entry, built from source like Bashed
# Earth; game_ready dispatches to lander_ready (None until it's cloned+built).
assert "terminal-lander" in games.GAMES
assert games.GAMES["terminal-lander"]["icon"] == "lander"
write("")                                        # empty conf, no [terminal-lander]
assert games.lander_ready(games.load()) is None
assert games.game_ready("terminal-lander") is None

# Kitty Brokeout is a first-class Games entry, built from source the same way.
assert "kitty-brokeout" in games.GAMES
assert games.GAMES["kitty-brokeout"]["icon"] == "brokeout"
write("")                                        # empty conf, no [kitty-brokeout]
assert games.brokeout_ready(games.load()) is None
assert games.game_ready("kitty-brokeout") is None


# Tarball extraction must reject members that escape the destination. Python
# 3.11's tarfile.extractall() does not filter these by default.
root = tempfile.mkdtemp(prefix="games-tar-test-")
bad_tar = os.path.join(root, "bad.tar")
out_dir = os.path.join(root, "out")
os.mkdir(out_dir)
with tarfile.open(bad_tar, "w") as t:
    data = b"escape"
    ti = tarfile.TarInfo("../escape.txt")
    ti.size = len(data)
    t.addfile(ti, io.BytesIO(data))
with tarfile.open(bad_tar, "r") as t:
    try:
        games._safe_extract_tar(t, out_dir)
        assert False, "unsafe tar member was extracted"
    except RuntimeError as e:
        assert "unsafe path" in str(e)
assert not os.path.exists(os.path.join(root, "escape.txt"))


# F36: main() catches installer errors that don't subclass RuntimeError/OSError
# (BadZipFile from a mirror serving HTML, TarError, configparser.Error) and
# exits cleanly with the [Enter to close] path instead of dumping a traceback
# into a tab that then vanishes.
def boom(game, report=print):
    raise zipfile.BadZipFile("mirror returned an HTML error page")


games.ensure = boom
builtins.input = lambda *a: (_ for _ in ()).throw(EOFError())
sys.argv = ["games.py", "doom"]
try:
    games.main()
    assert False, "main() should have exited"
except SystemExit as e:
    assert e.code == 1, e.code
except zipfile.BadZipFile:
    assert False, "BadZipFile leaked past main()'s handler"

print("ok")
