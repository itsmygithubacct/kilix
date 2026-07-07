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
APPS_DIR = os.path.join(os.environ.get("XDG_DATA_HOME")
                        or os.path.join(HOME, ".local", "share"),
                        "kilix", "apps")
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

BASHED_REPO = "https://github.com/itsmygithubacct/Bashed-Earth"
LANDER_REPO = "https://github.com/itsmygithubacct/terminal_lander"
BROKEOUT_REPO = "https://github.com/itsmygithubacct/kitty-brokeout"
AMP_REPO = "https://github.com/itsmygithubacct/kilix-amp"

# the Start-menu registry (taskbar/shell build the Games submenu from this)
GAMES = {
    "doom": {
        "label": "Doom", "icon": "doom",
        "blurb": "Download the official shareware episode (~2.4 MB) —\n"
                 "plus DOSBox if none is installed — into\n"
                 "~/.local/share/kilix/games, and play?",
    },
    "dosbox": {
        "label": "DOSBox", "icon": "dosbox",
        "blurb": "Open an MS-DOS prompt (DOSBox) with C: mounted to\n"
                 "~/.local/share/kilix/games. Fetches a dosbox-staging\n"
                 "build there first if none is already installed.",
    },
    "bashed-earth": {
        "label": "Bashed Earth", "icon": "tank",
        "blurb": "Clone and build Bashed Earth (terminal artillery\n"
                 "combat, github.com/itsmygithubacct/Bashed-Earth)\n"
                 "into ~/.local/share/kilix/games, and play?",
    },
    "terminal-lander": {
        "label": "Terminal Lander", "icon": "lander",
        "blurb": "Clone and build Terminal Lander (a kitty-graphics\n"
                 "lunar lander, github.com/itsmygithubacct/terminal_lander)\n"
                 "into ~/.local/share/kilix/games, and play?",
    },
    "kitty-brokeout": {
        "label": "Kitty Brokeout", "icon": "brokeout",
        "blurb": "Clone and build Kitty Brokeout (a kitty-graphics\n"
                 "brick breaker, github.com/itsmygithubacct/kitty-brokeout)\n"
                 "into ~/.local/share/kilix/games, and play?",
    },
}


def load():
    # interpolation=None so a '%' in a stored path is literal, not a token;
    # a malformed conf reads as empty (== "no game installed") instead of
    # taking down whatever called a *_ready() check.
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(CONF)
    except configparser.Error:
        cp = configparser.ConfigParser(interpolation=None)
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


def dosbox_ready(cp=None):
    """Path to a runnable dosbox if one is already available (no install):
    config > $PATH > previously vendored."""
    cp = cp or load()
    cur = os.path.expanduser(cp.get("dosbox", "exe", fallback=""))
    if cur and os.access(cur, os.X_OK):
        return cur
    for name in ("dosbox", "dosbox-staging", "dosbox-x"):
        p = shutil.which(name)
        if p:
            return p
    vend = os.path.join(GAMES_DIR, "dosbox-staging")
    exe = _find(vend, "dosbox") if os.path.isdir(vend) else None
    return exe if exe and os.access(exe, os.X_OK) else None


def game_ready(game, cp=None):
    """Installed-and-runnable check dispatched by game name (None if not)."""
    cp = cp or load()
    return {"doom": doom_ready, "dosbox": dosbox_ready,
            "bashed-earth": bashed_ready, "terminal-lander": lander_ready,
            "kitty-brokeout": brokeout_ready
            }.get(game, lambda c=None: None)(cp)


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


def _inside(root, path):
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    return os.path.commonpath([root, path]) == root


def _safe_extract_tar(tar, dest):
    """Extract a tarball only if every member stays below dest."""
    root = os.path.abspath(dest)
    for m in tar.getmembers():
        target = os.path.abspath(os.path.join(root, m.name))
        if not _inside(root, target):
            raise RuntimeError(f"unsafe path in archive: {m.name}")
        if m.issym() or m.islnk():
            raise RuntimeError(f"archive links are not supported: {m.name}")
        if m.isdev() or m.isfifo():
            raise RuntimeError(f"unsupported archive member: {m.name}")
    tar.extractall(root)


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
        _safe_extract_tar(t, vend)
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


# DOSBox: fullscreen on its private X server, which `kilix run` sizes to the
# pane's exact pixel area — so the game fills the WHOLE pane, no letterbox
# (aspect=false: pane aspect wins over VGA aspect). Sound on (Doom autodetects
# the emulated Sound Blaster). Understood by classic DOSBox and staging.
DOSBOX_CONF = """\
[sdl]
fullscreen=true
fullresolution=desktop
output=opengl

[render]
aspect=false

[mixer]
nosound=false
rate=44100
"""

# Doom merges this with its built-in defaults: fire on Space (57), use/open
# on Ctrl (157 = right-ctrl) — swapped from the DOS defaults per taste — and
# the Sound Blaster settings SETUP.EXE would normally write (without them the
# engine defaults to NO sound devices). Device 3 = Sound Blaster: digital sfx
# + OPL FM music, exactly what DOSBox emulates at 220/7/1.
DOOM_CFG = """\
key_fire\t\t57
key_use\t\t\t157
sfx_volume\t\t8
music_volume\t\t8
snd_channels\t\t3
snd_musicdevice\t\t3
snd_sfxdevice\t\t3
snd_sbport\t\t544
snd_sbirq\t\t7
snd_sbdma\t\t1
"""


def _write_settings(ddir, report):
    """Drop the dosbox conf + key bindings next to the game (created once;
    a user-edited file is never overwritten)."""
    conf = os.path.join(ddir, "dosbox-kilix.conf")
    if not os.path.exists(conf):
        with open(conf, "w") as f:
            f.write(DOSBOX_CONF)
        report("wrote dosbox-kilix.conf (fullscreen, aspect, sound on)")
    exe = _find(ddir, "DOOM.EXE")
    cfg = os.path.join(os.path.dirname(exe), "DEFAULT.CFG") if exe else None
    if cfg and not os.path.exists(cfg):
        with open(cfg, "w") as f:
            f.write(DOOM_CFG)
        report("wrote DEFAULT.CFG (fire = Space, use = Ctrl)")
    return conf


def _write_dosbox_conf(report):
    """The shared fullscreen/sound dosbox conf in the games dir (once)."""
    os.makedirs(GAMES_DIR, exist_ok=True)
    conf = os.path.join(GAMES_DIR, "dosbox-kilix.conf")
    if not os.path.exists(conf):
        with open(conf, "w") as f:
            f.write(DOSBOX_CONF)
        report("wrote dosbox-kilix.conf (fullscreen, sound on)")
    return conf


def _audio_check(report):
    import subprocess
    try:
        r = subprocess.run(["pactl", "info"], capture_output=True, text=True,
                           timeout=5)
        if r.returncode == 0:
            srv = next((ln.split(":", 1)[1].strip()
                        for ln in r.stdout.splitlines()
                        if ln.startswith("Server Name")), "PulseAudio")
            report(f"audio: {srv} reachable — DOSBox sound will work")
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    report("audio: no PulseAudio/PipeWire found — Doom will run silent")


def _clone_and_make(repo, dest, binary, dep_hint, report):
    """Shared clone+build installer: git clone --depth 1, make, return the
    built binary's path."""
    import subprocess
    if not os.path.isdir(os.path.join(dest, ".git")):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        report(f"cloning {repo} …")
        r = subprocess.run(["git", "clone", "--depth", "1", repo, dest],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"clone failed:\n{r.stderr.strip()[-500:]}")
    exe = os.path.join(dest, binary)
    if not os.access(exe, os.X_OK):
        report("building (make) …")
        r = subprocess.run(["make", "-C", dest], capture_output=True,
                           text=True)
        if r.returncode != 0:
            raise RuntimeError(f"build failed ({dep_hint}):\n"
                               + (r.stderr or r.stdout).strip()[-600:])
    if not os.access(exe, os.X_OK):
        raise RuntimeError(f"make succeeded but no {binary} binary appeared")
    return exe


def _repo_ready(cp, section, binary):
    if not cp.has_section(section):
        return None
    d = os.path.expanduser(cp.get(section, "dir", fallback=""))
    exe = os.path.join(d, binary) if d else ""
    return exe if exe and os.access(exe, os.X_OK) else None


def bashed_ready(cp=None):
    return _repo_ready(cp or load(), "bashed-earth", "bashed-earth")


def ensure_bashed(cp, report):
    return bashed_ready(cp) or _clone_and_make(
        BASHED_REPO, os.path.join(GAMES_DIR, "bashed-earth"), "bashed-earth",
        "needs gcc/clang, zlib, make", report)


def lander_ready(cp=None):
    return _repo_ready(cp or load(), "terminal-lander", "terminal-lander")


def ensure_lander(cp, report):
    return lander_ready(cp) or _clone_and_make(
        LANDER_REPO, os.path.join(GAMES_DIR, "terminal-lander"),
        "terminal-lander", "needs a C compiler + zlib, make", report)


def brokeout_ready(cp=None):
    return _repo_ready(cp or load(), "kitty-brokeout", "kitty-brokeout")


def ensure_brokeout(cp, report):
    return brokeout_ready(cp) or _clone_and_make(
        BROKEOUT_REPO, os.path.join(GAMES_DIR, "kitty-brokeout"),
        "kitty-brokeout", "needs a C compiler + zlib, make", report)


def amp_ready(cp=None):
    return _repo_ready(cp or load(), "kilix-amp", "kilix-amp")


def ensure_amp(cp, report):
    return amp_ready(cp) or _clone_and_make(
        AMP_REPO, os.path.join(APPS_DIR, "kilix-amp"), "kilix-amp",
        "needs libsdl2-dev, libsdl2-image-dev, libsndfile1-dev, zlib1g-dev",
        report)


def ensure(game, report=print):
    cp = load()
    if not cp.has_section(game):
        cp.add_section(game)
    if game == "doom":
        dosbox = ensure_dosbox(cp, report)
        ddir = ensure_doom(cp, report)
        conf = _write_settings(ddir, report)
        _audio_check(report)
        cp.set("doom", "dosbox", dosbox)
        cp.set("doom", "dir", ddir)
        payload = (dosbox, conf, _find(ddir, "DOOM.EXE"))
    elif game == "dosbox":
        dosbox = ensure_dosbox(cp, report)
        conf = _write_dosbox_conf(report)
        _audio_check(report)
        cp.set("dosbox", "exe", dosbox)
        payload = (dosbox, conf)
    elif game == "bashed-earth":
        exe = ensure_bashed(cp, report)
        cp.set("bashed-earth", "dir", os.path.dirname(exe))
        payload = exe
    elif game == "terminal-lander":
        exe = ensure_lander(cp, report)
        cp.set("terminal-lander", "dir", os.path.dirname(exe))
        payload = exe
    elif game == "kitty-brokeout":
        exe = ensure_brokeout(cp, report)
        cp.set("kitty-brokeout", "dir", os.path.dirname(exe))
        payload = exe
    elif game == "kilix-amp":
        exe = ensure_amp(cp, report)
        cp.set("kilix-amp", "dir", os.path.dirname(exe))
        payload = exe
    else:
        raise SystemExit(f"kilix games: unknown game {game!r}")
    save(cp)
    report(f"ready — config saved to {CONF}")
    return payload


def _launch_doom(payload):
    dosbox, conf, exe = payload
    kilix = os.path.join(KILIX_HOME, "kilix")
    argv = [dosbox, "-conf", conf, exe, "-exit"]
    if os.environ.get("KITTY_WINDOW_ID") and os.access(kilix, os.X_OK):
        # already in our own tab: run DOSBox in-place through `kilix run`.
        # 640x400 = Doom's own 16:10, so DOSBox fullscreen has zero bars;
        # --fill then stretches the placement over the WHOLE pane.
        os.environ["KILIX_IN_OVERLAY"] = "1"
        os.execv(kilix, [kilix, "run", "--fill", "--size", "640x400"] + argv)
    elif os.environ.get("DISPLAY"):
        os.execv(dosbox, argv)                # plain X session
    raise SystemExit("kilix games: no display (run inside kilix or X)")


def _launch_dosbox(payload):
    dosbox, conf = payload
    kilix = os.path.join(KILIX_HOME, "kilix")
    # boot to a DOS prompt with C: mounted at the games folder, so any DOS
    # programs dropped there are one `C:` away
    argv = [dosbox, "-conf", conf,
            "-c", f'mount c "{GAMES_DIR}"', "-c", "c:"]
    if os.environ.get("KITTY_WINDOW_ID") and os.access(kilix, os.X_OK):
        # 640x400 = 80x25 VGA text, so the prompt is crisp; --fill stretches
        # the placement over the whole pane (aspect=false in the conf)
        os.environ["KILIX_IN_OVERLAY"] = "1"
        os.execv(kilix, [kilix, "run", "--fill", "--size", "640x400"] + argv)
    elif os.environ.get("DISPLAY"):
        os.execv(dosbox, argv)                # plain X session
    raise SystemExit("kilix games: no display (run inside kilix or X)")


def _launch_native(exe):
    # a native terminal game (Bashed Earth, Terminal Lander) that speaks the
    # kitty graphics protocol itself: runs right here in the tab
    os.chdir(os.path.dirname(exe))
    os.execv(exe, [exe])


def main():
    args = [a for a in sys.argv[1:]]
    setup_only = "--setup-only" in args
    args = [a for a in args if a != "--setup-only"]
    game = args[0] if args else "doom"
    try:
        payload = ensure(game)
    except Exception as e:      # BadZipFile/TarError/configparser.Error too
        # keep the message readable in the tab instead of vanishing with it
        print(f"\x1b[1;31mkilix games: {e}\x1b[0m", file=sys.stderr)
        try:
            input("\n[Enter to close]")
        except EOFError:
            pass
        sys.exit(1)
    if setup_only:
        return
    if game == "doom":
        _launch_doom(payload)
    elif game == "dosbox":
        _launch_dosbox(payload)
    else:
        _launch_native(payload)


if __name__ == "__main__":
    main()
