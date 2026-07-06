"""games.py: a broken games.conf must not crash a *_ready() check (F33),
and an installer error that isn't RuntimeError/OSError must be shown, not
leaked out of main() with the tab (F36). No network: conf files + stubs only."""
import builtins
import os
import sys
import tempfile
import zipfile

import harness as H       # noqa: F401  (sets up sys.path for `import games`)
import games

tmp = tempfile.mkdtemp(prefix="games-test-")
games.CONF = os.path.join(tmp, "games.conf")


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
