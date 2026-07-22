"""games.py: a broken games.conf must not crash a *_ready() check (F33),
and an installer error that isn't RuntimeError/OSError must be shown, not
leaked out of main() with the tab (F36). No network: conf files + stubs only."""
import builtins
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile

import harness as H       # noqa: F401  (sets up sys.path for `import games`)
import games
import icons

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

# Kilix Lights is a pinned native game whose executable lives under bin/.
assert "kilix-lights" in games.GAMES
assert games.GAMES["kilix-lights"]["label"] == "Kilix Lights"
assert games.GAMES["kilix-lights"]["icon"] == "lights"
assert games.CONTENT_CATALOG.require("kilix-lights").binary == "bin/kilix-lights"
write("")
assert games.game_ready("kilix-lights") is None
assert "lights" in icons.ICONS
icons.get("lights", 16)
icons.get("lights", 32)

# Super Kilix is a pinned native game whose executable lives at the repo root.
assert "super-kilix" in games.GAMES
assert games.GAMES["super-kilix"]["label"] == "Super Kilix"
assert games.GAMES["super-kilix"]["icon"] == "super-kilix"
assert games.CONTENT_CATALOG.require("super-kilix").binary == "super-kilix"
write("")
assert games.game_ready("super-kilix") is None
assert "super-kilix" in icons.ICONS
icons.get("super-kilix", 16)
icons.get("super-kilix", 32)

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

# ZIP extraction has the same traversal guarantee as tar extraction.
bad_zip = os.path.join(root, "bad.zip")
with zipfile.ZipFile(bad_zip, "w") as archive:
    archive.writestr("../zip-escape.txt", b"escape")
with zipfile.ZipFile(bad_zip) as archive:
    try:
        games._safe_extract_zip(archive, out_dir)
        assert False, "unsafe ZIP member was extracted"
    except RuntimeError as error:
        assert "unsafe path" in str(error)
assert not os.path.exists(os.path.join(root, "zip-escape.txt"))

for ref in (games.BASHED_REF, games.LANDER_REF, games.BROKEOUT_REF,
            games.AMP_REF):
    assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref)


# An interrupted source init (a .git directory with no HEAD) is repaired from
# a separately prepared checkout, without leaving another partial directory.
clone_root = tempfile.mkdtemp(prefix="games-clone-recovery-")
seed = os.path.join(clone_root, "seed")
dest = os.path.join(clone_root, "installed")
subprocess.run(["git", "init", "-q", "-b", "main", seed], check=True)
subprocess.run(["git", "-C", seed, "config", "user.name", "Test"], check=True)
subprocess.run(["git", "-C", seed, "config", "user.email", "test@example.invalid"], check=True)
game_bin = os.path.join(seed, "game")
with open(game_bin, "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
os.chmod(game_bin, 0o755)
subprocess.run(["git", "-C", seed, "add", "game"], check=True)
subprocess.run(["git", "-C", seed, "commit", "-qm", "game"], check=True)
ref = subprocess.check_output(
    ["git", "-C", seed, "rev-parse", "HEAD"], text=True).strip()
subprocess.run(["git", "init", "-q", dest], check=True)  # interrupted old run
recovered = games._clone_and_make(
    seed, ref, dest, "game", "unused", lambda _msg: None)
assert os.access(recovered, os.X_OK)
assert subprocess.check_output(
    ["git", "-C", dest, "rev-parse", "HEAD"], text=True).strip() == ref
assert not [name for name in os.listdir(clone_root) if ".partial-" in name]

failed_dest = os.path.join(clone_root, "failed-install")
try:
    games._clone_and_make(
        seed, "0" * 40, failed_dest, "game", "unused", lambda _msg: None)
    assert False, "missing ref unexpectedly installed"
except RuntimeError:
    pass
assert not os.path.exists(failed_dest)
assert not [name for name in os.listdir(clone_root) if ".partial-" in name]


# Managed ready checks cannot bypass the origin/ref/clean-source contract, but
# an explicitly different path remains a trusted user-managed executable.
import configparser
ready_cp = configparser.ConfigParser(interpolation=None)
ready_cp.add_section("fixture")
ready_cp.set("fixture", "dir", dest)
assert games._repo_ready(ready_cp, "fixture", "game", dest, seed, ref) == recovered
subprocess.run(["git", "-C", dest, "remote", "set-url", "origin", "bad-origin"],
               check=True)
assert games._repo_ready(ready_cp, "fixture", "game", dest, seed, ref) is None
subprocess.run(["git", "-C", dest, "remote", "set-url", "origin", seed], check=True)
with open(os.path.join(dest, "game"), "a") as f:
    f.write("# modified\n")
assert games._repo_ready(ready_cp, "fixture", "game", dest, seed, ref) is None
subprocess.run(["git", "-C", dest, "reset", "--hard", ref],
               check=True, capture_output=True)

external = os.path.join(clone_root, "external")
os.mkdir(external)
external_bin = os.path.join(external, "game")
with open(external_bin, "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
os.chmod(external_bin, 0o755)
ready_cp.set("fixture", "dir", external)
assert games._repo_ready(
    ready_cp, "fixture", "game", dest, seed, ref) == external_bin


# Downloads must fail closed when the pinned checksum does not match.
fetch_dir = tempfile.mkdtemp(prefix="games-fetch-test-")
src = os.path.join(fetch_dir, "src.bin")
dst = os.path.join(fetch_dir, "dst.bin")
with open(src, "wb") as f:
    f.write(b"not the expected artifact")
try:
    games._fetch("file://" + src, dst, lambda _msg: None,
                 sha256="0" * 64)
    assert False, "checksum mismatch was accepted"
except RuntimeError as e:
    assert "sha256 mismatch" in str(e)
assert not os.path.exists(dst), "bad artifact must be removed"


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
