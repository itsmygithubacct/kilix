"""kilix desktop — Recycle Bin backing store (no UI).

Deleted files/dirs are moved under the bin (default ~/.local/share/kilix/
recycled) into a per-item opaque token, beside a JSON sidecar recording the
original path, deletion time, name, size and is_dir. The sidecars ARE the
index: items() rescans them each call, so a missing or corrupt sidecar
degrades to a best-effort entry instead of desyncing the bin. No network.

Location: $KILIX_RECYCLE_DIR wins; else the bin sits beside the desktop
folder (a sibling of $KILIX_DESKTOP_DIR when set — so redirecting the desktop
to a temp dir isolates the bin too); else ~/.local/share/kilix/recycled.
"""
import json
import os
import shutil
import time
import uuid


def _base():
    over = os.environ.get("KILIX_RECYCLE_DIR")
    if over:
        return os.path.expanduser(over)
    desk = os.environ.get("KILIX_DESKTOP_DIR")
    if desk:
        return os.path.join(os.path.dirname(
            os.path.abspath(os.path.expanduser(desk))), "recycled")
    return os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
        "kilix", "recycled")


def _store():
    fdir = os.path.join(_base(), "files")
    os.makedirs(fdir, exist_ok=True)
    return fdir


def _tree_size(path):
    if os.path.islink(path) or not os.path.isdir(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for n in files:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


def _unique(path):
    if not os.path.lexists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.lexists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def send(path):
    """Move path into the bin; return its token."""
    path = os.path.abspath(os.path.expanduser(path))
    fdir = _store()
    token = uuid.uuid4().hex
    is_dir = os.path.isdir(path) and not os.path.islink(path)
    info = {"orig": path,
            "name": os.path.basename(path.rstrip("/")) or path,
            "when": time.time(),
            "size": _tree_size(path),
            "is_dir": is_dir}
    shutil.move(path, os.path.join(fdir, token))     # may raise OSError
    with open(os.path.join(fdir, token + ".info"), "w") as f:
        json.dump(info, f)
    return token


def items():
    """List of {token, orig, name, when, size, is_dir}, newest first."""
    fdir = _store()
    out = []
    for n in sorted(os.listdir(fdir)):
        if n.endswith(".info"):
            continue
        p = os.path.join(fdir, n)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        info = {}
        try:
            with open(p + ".info") as f:
                info = json.load(f)
        except (OSError, ValueError):
            pass
        out.append({"token": n,
                    "orig": info.get("orig", ""),
                    "name": info.get("name") or n,
                    "when": info.get("when", st.st_mtime),
                    "size": info.get("size", st.st_size),
                    "is_dir": info.get("is_dir",
                                       os.path.isdir(p)
                                       and not os.path.islink(p))})
    out.sort(key=lambda i: i["when"], reverse=True)
    return out


def has_items():
    """True if the bin holds anything. Cheap: no sidecar reads (for tick hooks)."""
    return any(not n.endswith(".info") for n in os.listdir(_store()))


def restore(token):
    """Move a token back to its original path (disambiguated if occupied);
    return the path it landed at."""
    fdir = _store()
    src = os.path.join(fdir, token)
    if not os.path.lexists(src):
        raise KeyError(token)
    info = {}
    try:
        with open(src + ".info") as f:
            info = json.load(f)
    except (OSError, ValueError):
        pass
    dest = info.get("orig") or os.path.join(
        os.path.expanduser("~"), info.get("name") or token)
    try:
        os.makedirs(os.path.dirname(dest) or "/", exist_ok=True)
    except OSError:
        pass
    dest = _unique(dest)
    shutil.move(src, dest)
    try:
        os.unlink(src + ".info")
    except OSError:
        pass
    return dest


def purge(token):
    """Permanently remove one token from the bin."""
    fdir = _store()
    p = os.path.join(fdir, token)
    if os.path.isdir(p) and not os.path.islink(p):
        shutil.rmtree(p, ignore_errors=True)
    elif os.path.lexists(p):
        try:
            os.unlink(p)
        except OSError:
            pass
    try:
        os.unlink(p + ".info")
    except OSError:
        pass


def empty():
    """Purge every item."""
    for it in items():
        purge(it["token"])
