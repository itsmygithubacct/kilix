#!/usr/bin/env python3
"""kilix 95 — the Games section: registry + on-demand installers.

`games.py doom` is what Start ▸ Programs ▸ Games ▸ Doom runs (in a new kilix
tab). If ~/.config/kilix/games.conf points at a working DOSBox and Doom, it
boots straight in; otherwise it downloads the official shareware episode
(id's doom19s.zip from the idgames mirrors — the shareware episode is freely
redistributable) and, when no dosbox is installed, a dosbox-staging release
build, stores everything under ~/.local/share/kilix/games/, writes the
config, and boots. Nothing is written inside the kilix tree.

No DOS needed for the install: doom19s.zip's DEICE parts (DOOMS_19.1/.2)
concatenate into a self-extracting ZIP that python's zipfile reads directly.

Inside kilix the game runs through `kilix run` (DOSBox on a private X server,
streamed into the pane); on a plain X session it just runs DOSBox.
"""
import configparser
import hashlib
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile

HOME = os.path.expanduser("~")
CONF = os.path.join(os.environ.get("XDG_CONFIG_HOME")
                    or os.path.join(HOME, ".config"), "kilix", "games.conf")
GAMES_DIR = os.path.join(os.environ.get("XDG_DATA_HOME")
                         or os.path.join(HOME, ".local", "share"),
                         "kilix", "games")
KILIX_HOME = (os.environ.get("KILIX_HOME")
              or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DOOM_URLS = [  # idgames mirrors of id Software's shareware installer
    "https://www.gamers.org/pub/idgames/idstuff/doom/doom19s.zip",
    "https://ftp.fu-berlin.de/pc/games/idgames/idstuff/doom/doom19s.zip",
    "https://youfailit.net/pub/idgames/idstuff/doom/doom19s.zip",
]
DOOM1_WAD_MD5 = "f0cefca49926d00903cf57551d901abe"      # shareware 1.9

DOSBOX_VER = "v0.82.2"
DOSBOX_URL = ("https://github.com/dosbox-staging/dosbox-staging/releases/"
              f"download/{DOSBOX_VER}/dosbox-staging-linux-x86_64-"
              f"{DOSBOX_VER}.tar.xz")


def load():
    cp = configparser.ConfigParser()
    cp.read(CONF)
    return cp


def save(cp):
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    with open(CONF, "w") as f:
        cp.write(f)


def _find(d, name):
    """Case-insensitive file search under d; returns a path or None."""
    for root, _dirs, files in os.walk(d):
        for f in files:
            if f.lower() == name.lower():
                return os.path.join(root, f)
    return None


def doom_ready(cp=None):
    """(dosbox, doom_exe) if the config points at a working install."""
    cp = cp or load()
    if not cp.has_section("doom"):
        return None
    dosbox = os.path.expanduser(cp.get("doom", "dosbox", fallback=""))
    ddir = os.path.expanduser(cp.get("doom", "dir", fallback=""))
    if not (dosbox and os.access(dosbox, os.X_OK) and os.path.isdir(ddir)):
        return None
    exe = _find(ddir, "DOOM.EXE")
    wad = _find(ddir, "DOOM1.WAD") or _find(ddir, "DOOM.WAD")
    return (dosbox, exe) if exe and wad else None


def _fetch(urls, dest, report):
    """Download the first URL that works, with a coarse progress line."""
    last = None
    for url in urls if isinstance(urls, list) else [urls]:
        try:
            report(f"downloading {url.rsplit('/', 1)[-1]} …")
            req = urllib.request.Request(url, headers={"User-Agent": "kilix"})
            with urllib.request.urlopen(req, timeout=60) as r, \
                    open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = pct = 0
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total and got * 10 // total > pct:
                        pct = got * 10 // total
                        report(f"  {pct * 10}% of {total // 1024} KB")
            return
        except OSError as e:
            last = e
            report(f"  failed: {e}")
    raise RuntimeError(f"all mirrors failed ({last})")


def ensure_dosbox(cp, report):
    """A runnable dosbox: config > $PATH > previously vendored > download."""
    cur = os.path.expanduser(cp.get("doom", "dosbox", fallback=""))
    if cur and os.access(cur, os.X_OK):
        return cur
    for name in ("dosbox", "dosbox-staging", "dosbox-x"):
        p = shutil.which(name)
        if p:
            report(f"using system {name}: {p}")
            return p
    vend = os.path.join(GAMES_DIR, "dosbox-staging")
    exe = _find(vend, "dosbox") if os.path.isdir(vend) else None
    if exe and os.access(exe, os.X_OK):
        return exe
    if os.uname().machine != "x86_64":
        raise RuntimeError(
            "no dosbox on PATH and the dosbox-staging release build is "
            f"x86_64-only (this is {os.uname().machine}) — install dosbox "
            "with your package manager, or set [doom] dosbox= in "
            f"{CONF}")
    os.makedirs(vend, exist_ok=True)
    tar = os.path.join(vend, "dosbox.tar.xz")
    _fetch(DOSBOX_URL, tar, report)
    report("unpacking dosbox-staging …")
    with tarfile.open(tar, "r:xz") as t:
        t.extractall(vend)
    os.unlink(tar)
    exe = _find(vend, "dosbox")
    if not (exe and os.access(exe, os.X_OK)):
        raise RuntimeError("dosbox-staging unpack yielded no dosbox binary")
    return exe


def ensure_doom(cp, report):
    """A directory with DOOM.EXE + DOOM1.WAD: config > vendored > download."""
    cur = os.path.expanduser(cp.get("doom", "dir", fallback=""))
    if cur and _find(cur, "DOOM.EXE") and (_find(cur, "DOOM1.WAD")
                                           or _find(cur, "DOOM.WAD")):
        return cur
    ddir = os.path.join(GAMES_DIR, "doom")
    if _find(ddir, "DOOM.EXE") and _find(ddir, "DOOM1.WAD"):
        return ddir
    os.makedirs(ddir, exist_ok=True)
    outer = os.path.join(ddir, "doom19s.zip")
    _fetch(DOOM_URLS, outer, report)
    report("extracting the shareware episode …")
    with zipfile.ZipFile(outer) as z:
        z.extractall(ddir)
    # DEICE's split archive: DOOMS_19.1 + DOOMS_19.2 = a self-extracting ZIP
    joined = os.path.join(ddir, "dooms_19.sfx")
    with open(joined, "wb") as out:
        for part in ("DOOMS_19.1", "DOOMS_19.2"):
            p = _find(ddir, part)
            if not p:
                raise RuntimeError(f"{part} missing from doom19s.zip")
            with open(p, "rb") as f:
                out.write(f.read())
    with zipfile.ZipFile(joined) as z:      # zipfile handles the MZ stub
        z.extractall(ddir)
    for junk in ("doom19s.zip", "dooms_19.sfx", "DOOMS_19.1", "DOOMS_19.2",
                 "DOOMS_19.DAT", "DEICE.EXE", "INSTALL.BAT"):
        p = _find(ddir, junk)
        if p:
            os.unlink(p)
    wad = _find(ddir, "DOOM1.WAD")
    if not (wad and _find(ddir, "DOOM.EXE")):
        raise RuntimeError("extraction yielded no DOOM.EXE/DOOM1.WAD")
    md5 = hashlib.md5(open(wad, "rb").read()).hexdigest()
    if md5 != DOOM1_WAD_MD5:
        report(f"note: DOOM1.WAD md5 {md5} differs from the known 1.9 build")
    return ddir


def ensure(game, report=print):
    if game != "doom":
        raise SystemExit(f"kilix games: unknown game {game!r}")
    cp = load()
    if not cp.has_section("doom"):
        cp.add_section("doom")
    dosbox = ensure_dosbox(cp, report)
    ddir = ensure_doom(cp, report)
    cp.set("doom", "dosbox", dosbox)
    cp.set("doom", "dir", ddir)
    save(cp)
    report(f"ready — config saved to {CONF}")
    return dosbox, _find(ddir, "DOOM.EXE")


def main():
    args = [a for a in sys.argv[1:]]
    setup_only = "--setup-only" in args
    args = [a for a in args if a != "--setup-only"]
    game = args[0] if args else "doom"
    dosbox, exe = ensure(game)
    if setup_only:
        return
    kilix = os.path.join(KILIX_HOME, "kilix")
    argv = [dosbox, exe, "-exit"]
    if os.environ.get("KITTY_WINDOW_ID") and os.access(kilix, os.X_OK):
        # already in our own tab: run DOSBox in-place through `kilix run`
        # (private X server, pixels streamed into this pane)
        os.environ["KILIX_IN_OVERLAY"] = "1"
        os.execv(kilix, [kilix, "run"] + argv)
    elif os.environ.get("DISPLAY"):
        os.execv(dosbox, argv)                # plain X session
    else:
        raise SystemExit("kilix games: no display (run inside kilix or X)")


if __name__ == "__main__":
    main()
