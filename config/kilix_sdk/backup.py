"""Back up and restore one user's Kilix settings and desktop documents.

A backup is a private gzip tar whose first member, ``manifest.json``, names
every file it carries with its size and SHA-256.  It holds:

* ``settings.conf``      the shared settings file every component reads;
* ``kilix/kilix.env``    the user's Kilix overrides;
* ``kilix95/config``     the desktop provider's configuration;
* ``kilix95/state``      its durable records (desktop layout, offers, choices),
                         without crash logs or crash-recovery checkpoints;
* ``desktop``            the documents on the desktop.

Each file is copied once into a private spool while it is hashed, so the
archive always matches its manifest even if the file changes meanwhile.

Restoring is non-destructive: it only creates or replaces the files the backup
names, each atomically, after checking every destination and keeping a safety
copy of the files it replaces.  An archive is refused whole if any member is
not a plain file or directory, escapes its root, is excluded crash state, is
missing from the manifest, fails its digest, or collides with another member.
Symlinks are never archived, followed or written through.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile

FORMAT = "kilix.backup/v1"
MANIFEST = "manifest.json"
MAX_TOTAL = 1 << 30                  # 1 GiB of payload
MAX_MEMBERS = 100_000
MAX_MANIFEST = 16 << 20               # bytes; a compact entry is ~120 bytes
NAME_MAX = 255
PATH_MAX = 4096
_STATE_EXCLUDE = ("document-recovery", "crash.log")
# kilix.env keys that decide what the launcher runs or trusts; a restore flags them.
LAUNCH_KEYS = ("KILIX_HOME", "KILIX_PREBUILT_HOME", "KILIX_DESKTOP_FLAVOR",
               "KILIX_DESKTOP_NAME", "KILIX_DESKTOP_PROVIDER", "KILIX_PTY_BROKER",
               "KILIX_KITTEN", "KILIX_SHELL", "KILIX_STATE_LIBRARY")
_LAUNCH_MARKERS = ("_DIR", "_REPO", "_REF", "_BRANCH", "_TRUST", "_ALLOW",
                   "_AUTO_INSTALL", "_COMMAND", "_PREFIX", "_PYTHON")


def launch_key(key: str) -> bool:
    """Whether a kilix.env key decides what code the launcher fetches, trusts or runs."""
    return key in LAUNCH_KEYS or any(marker in key for marker in _LAUNCH_MARKERS)


class BackupError(Exception):
    pass


def _home(*parts: str) -> str:
    base = os.environ.get("GPU_TERMINAL_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "gpu_terminal")
    return os.path.join(os.path.abspath(os.path.expanduser(base)), *parts)


def _env_dir(name: str, default: str) -> str:
    return os.path.abspath(os.path.expanduser(os.environ.get(name) or default))


def _parse_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
            for line in fh:
                line = line.rstrip("\n").rstrip("\r")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key] = value
    except OSError:
        pass
    return values


def _kilix_env_files() -> list[str]:
    from kilix_sdk import paths
    default = os.path.join(paths.kilix_home(), "config", "kilix.env")
    user = os.environ.get("KILIX_ENV_CONFIG") or os.path.join(paths.config_dir(), "kilix.env")
    return [default] if user == default else [default, user]


def persisted_value(key: str) -> str | None:
    """A launcher setting as the launcher resolves it: the process environment
    wins, otherwise the last kilix.env that sets it."""
    if key in os.environ:
        return os.environ[key]
    value = None
    for path in _kilix_env_files():
        value = _parse_env(path).get(key, value)
    return value


def sources() -> list[tuple[str, str]]:
    """[(archive name, absolute path)] for everything a backup carries."""
    from kilix_sdk import paths, settings
    k95 = _env_dir("KILIX95_STORAGE_HOME", _home("kilix-95"))
    data = _env_dir("KILIX95_DATA_HOME", os.path.join(k95, "data"))
    desktop = persisted_value("KILIX_DESKTOP_DIR") or os.path.join(data, "desktop")
    return [
        ("settings.conf", settings.settings_path()),
        ("kilix/kilix.env", os.environ.get("KILIX_ENV_CONFIG")
         or os.path.join(paths.config_dir(), "kilix.env")),
        ("kilix95/config", _env_dir("KILIX95_CONFIG_HOME", os.path.join(k95, "config"))),
        ("kilix95/state", _env_dir("KILIX95_STATE_HOME", os.path.join(k95, "state"))),
        ("desktop", os.path.abspath(os.path.expanduser(desktop))),
    ]


def default_directory() -> str:
    return os.path.join(os.path.expanduser("~"), "kilix-backups")


def _excluded(name: str) -> bool:
    if not name.startswith("kilix95/state/"):
        return False
    first = name[len("kilix95/state/"):].split("/", 1)[0]
    return (first in _STATE_EXCLUDE or first.startswith("crash.log")
            or first.startswith(".kilixstate."))


def _walk(name: str, path: str):
    """Yield (archive name, absolute path) for regular files; skip links."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISREG(info.st_mode):
        if not _excluded(name):
            yield name, path
        return
    if not stat.S_ISDIR(info.st_mode):
        return                                   # links, devices, sockets: never
    for entry in sorted(os.listdir(path)):
        yield from _walk(f"{name}/{entry}", os.path.join(path, entry))


def _open_nofollow(path: str):
    return open(path, "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW))


def _digest(path: str) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with _open_nofollow(path) as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
            size += len(block)
    return size, h.hexdigest()


def create(output: str | None = None, *, label: str = "kilix-backup",
           only: set[str] | None = None) -> str:
    """Write a backup and return its path; ``only`` limits it to those names."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output = os.path.abspath(os.path.expanduser(
        output or os.path.join(default_directory(), f"{label}-{stamp}.tar.gz")))
    directory = os.path.dirname(output)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    spool = tempfile.mkdtemp(prefix=".kilix-backup-spool-", dir=directory)
    fd, temporary = tempfile.mkstemp(prefix=".kilix-backup-", dir=directory)
    os.fchmod(fd, 0o600)
    try:
        entries, spooled, total = {}, [], 0
        files = [item for name, path in sources() for item in _walk(name, path)
                 if only is None or item[0] in only]
        for index, (name, path) in enumerate(files):
            copy = os.path.join(spool, str(index))
            h, size = hashlib.sha256(), 0
            try:
                with _open_nofollow(path) as src, open(copy, "xb") as dst:
                    for block in iter(lambda: src.read(1 << 20), b""):
                        size += len(block)
                        total += len(block)
                        if total > MAX_TOTAL:
                            raise BackupError("backup would exceed 1 GiB; move large "
                                              "files off the desktop first")
                        h.update(block)
                        dst.write(block)
            except (FileNotFoundError, IsADirectoryError):
                continue                             # vanished or replaced meanwhile
            entries[name] = {"size": size, "sha256": h.hexdigest()}
            spooled.append((name, copy))
            if len(entries) > MAX_MEMBERS:
                raise BackupError(f"backup would hold more than {MAX_MEMBERS} files; "
                                  "move some off the desktop first")
        manifest = json.dumps({
            "format": FORMAT,
            "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "entries": entries,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(manifest) > MAX_MANIFEST:
            raise BackupError("backup manifest would be too large; move some files off "
                              "the desktop first")
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            fd = -1
            info = tarfile.TarInfo(MANIFEST)
            info.size, info.mode = len(manifest), 0o600
            tar.addfile(info, io.BytesIO(manifest))
            for name, copy in spooled:
                with open(copy, "rb") as fh:
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = entries[name]["size"], 0o600
                    tar.addfile(info, fh)
        os.replace(temporary, output)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    finally:
        shutil.rmtree(spool, ignore_errors=True)
    return output


def _safe_name(name: str) -> bool:
    parts = name.split("/")
    return (bool(name) and not name.startswith("/") and "\\" not in name
            and len(name.encode("utf-8", "surrogateescape")) <= PATH_MAX
            and all(p not in ("", ".", "..")
                    and len(p.encode("utf-8", "surrogateescape")) <= NAME_MAX
                    for p in parts))


def read(archive: str) -> tuple[dict, dict[str, bytes]]:
    """Validate an archive completely; return (manifest, {name: bytes})."""
    try:
        tar = tarfile.open(archive, mode="r:gz")
    except (OSError, tarfile.TarError) as error:
        raise BackupError(f"not a readable backup: {error}") from None
    payload: dict[str, bytes] = {}
    roots = [name for name, _path in sources()]
    try:
        with tar:
            first = tar.next()
            if first is None or first.name != MANIFEST or not first.isfile():
                raise BackupError("missing manifest")
            try:
                if first.size > MAX_MANIFEST:
                    raise BackupError("manifest too large")
                manifest = json.loads(tar.extractfile(first).read(MAX_MANIFEST))
            except ValueError:
                raise BackupError("unreadable manifest") from None
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT \
                    or not isinstance(manifest.get("entries"), dict) \
                    or len(manifest["entries"]) > MAX_MEMBERS:
                raise BackupError("unsupported backup format")
            entries = manifest["entries"]
            total = 0
            while True:
                # One header at a time: an archive cannot make us parse more
                # members than its own manifest promised.
                member = tar.next()
                if member is None:
                    break
                if len(payload) >= len(entries):
                    raise BackupError("archive has more members than its manifest lists")
                name = member.name
                if not member.isfile() or not _safe_name(name):
                    raise BackupError(f"refusing archive member {name!r}")
                if not any(name == r or name.startswith(r + "/") for r in roots):
                    raise BackupError(f"archive member outside the backup: {name!r}")
                if _excluded(name):
                    raise BackupError(f"archive member is crash state a backup never holds: {name!r}")
                expected = entries.get(name)
                if not isinstance(expected, dict) or name in payload:
                    raise BackupError(f"archive member not in the manifest: {name!r}")
                total += member.size
                if total > MAX_TOTAL:
                    raise BackupError("archive larger than 1 GiB")
                data = tar.extractfile(member).read()
                if len(data) != expected.get("size") or \
                        hashlib.sha256(data).hexdigest() != expected.get("sha256"):
                    raise BackupError(f"digest mismatch for {name!r}")
                payload[name] = data
    except (tarfile.TarError, EOFError, OSError) as error:
        raise BackupError(f"damaged backup: {error}") from None
    missing = set(entries) - set(payload)
    if missing:
        raise BackupError(f"manifest names files the archive lacks: {sorted(missing)[:3]}")
    names = set(payload)
    for name in names:                               # a file cannot also be a folder
        parts = name.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in names:
                raise BackupError(
                    f"archive member is both a file and a folder: {'/'.join(parts[:i])!r}")
    return manifest, payload


def _destination(name: str) -> str:
    for root, path in sources():
        if name == root:
            return path
        if name.startswith(root + "/"):
            return os.path.join(path, *name[len(root) + 1:].split("/"))
    raise BackupError(f"no destination for {name!r}")


def _action(dest: str, data: bytes) -> str:
    if not os.path.lexists(dest):
        return "create"
    try:
        same = _digest(dest)[1] == hashlib.sha256(data).hexdigest()
    except OSError:
        same = False
    return "same" if same else "replace"


def plan(archive: str) -> list[tuple[str, str, str]]:
    """[(name, destination, 'create'|'replace'|'same')] without writing anything."""
    _manifest, payload = read(archive)
    return [(name, _destination(name), _action(_destination(name), data))
            for name, data in sorted(payload.items())]


def changes(archive: str) -> list[tuple[str, str, str | None, str | None]]:
    """Setting-level changes a restore makes: (file, key, current, restored).

    A backup file can come from anywhere, and kilix.env decides what the
    launcher runs, so these are shown before anything is applied."""
    _manifest, payload = read(archive)
    out = []
    for name in ("settings.conf", "kilix/kilix.env"):
        if name not in payload:
            continue
        current = _parse_env(_destination(name))
        restored: dict[str, str] = {}
        for line in payload[name].decode("utf-8", "surrogateescape").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                restored[key] = value
        for key in sorted(set(current) | set(restored)):
            if current.get(key) != restored.get(key):
                out.append((name, key, current.get(key), restored.get(key)))
    return out


def _check_destination(dest: str) -> None:
    if os.path.islink(dest) or (os.path.lexists(dest) and not os.path.isfile(dest)):
        raise BackupError(f"refusing to replace a non-file at {dest}")
    probe = os.path.dirname(dest)
    while True:                              # never create or write through a symlink
        if os.path.islink(probe):
            raise BackupError(f"refusing to write through a symlink at {probe}")
        if os.path.exists(probe) or os.path.dirname(probe) == probe:
            if os.path.exists(probe) and not os.path.isdir(probe):
                raise BackupError(f"refusing to write under a non-directory at {probe}")
            return
        probe = os.path.dirname(probe)


def desktop_running() -> bool:
    """Whether this user's desktop provider is running (it saves its own state)."""
    from kilix_sdk import paths
    main = os.path.realpath(os.path.join(
        persisted_value("KILIX95_DIR") or paths.kilix95_home(), "main.py"))
    uid = os.getuid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            if os.stat(f"/proc/{entry}").st_uid != uid:
                continue
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                argv = fh.read().split(b"\0")
        except OSError:
            continue
        for arg in argv[1:3]:
            if arg.endswith(b"main.py") and os.path.realpath(os.fsdecode(arg)) == main:
                return True
    return False


def restore(archive: str) -> dict:
    """Apply a backup; returns {'safety': path|None, 'written': n, 'names': [...],
    'unchanged': n}.

    Every destination is checked before anything is written, so a refused
    archive leaves the user's files exactly as they were."""
    _manifest, payload = read(archive)
    targets = []
    for name, data in sorted(payload.items()):
        dest = _destination(name)
        _check_destination(dest)
        targets.append((name, dest, data, _action(dest, data)))
    replaced = {name for name, _dest, _data, action in targets if action == "replace"}
    safety = create(label="kilix-before-restore", only=replaced) if replaced else None
    written: list[str] = []
    unchanged = 0
    for name, dest, data, action in targets:
        if action == "same":
            unchanged += 1
            continue
        parent = os.path.dirname(dest)
        try:
            os.makedirs(parent, mode=0o700, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".kilix-restore-", dir=parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                os.replace(temporary, dest)
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
        except OSError as error:
            done = ", ".join(written) or "nothing"
            raise BackupError(f"could not write {name}: {error.strerror or error}; "
                              f"already restored: {done}; previous files: {safety}") from None
        written.append(name)
    return {"safety": safety, "written": len(written), "names": written,
            "unchanged": unchanged}


SESSION_FILES = ("kilix/kilix.env",)


def restart_advice(names) -> str:
    """What the user must do for the restored files to take effect."""
    if any(name in SESSION_FILES for name in names):
        return ("kilix.env was restored: log out and back in (or start a new Kilix "
                "session) for it to take effect; restarting the desktop is not enough.")
    return "Restart the desktop to load the restored settings."


def main(argv: list[str]) -> int:
    import argparse
    import sys
    parser = argparse.ArgumentParser(
        prog="kilix backup",
        description="Back up or restore your Kilix settings and desktop documents.")
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create", help="write a new backup")
    make.add_argument("--output", help="archive path (default ~/kilix-backups/...)")
    show = sub.add_parser("list", help="show what restoring an archive would change")
    show.add_argument("archive")
    back = sub.add_parser("restore", help="restore an archive (asks unless --yes)")
    back.add_argument("archive")
    back.add_argument("--yes", action="store_true", help="apply without asking")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            print(create(args.output))
            return 0
        rows = plan(args.archive)
        for name, _dest, action in rows:
            print(f"{action:8} {name}")
        setting_changes = changes(args.archive)
        if setting_changes:
            print("\nSetting changes:")
            for name, key, old, new in setting_changes:
                note = "   <- decides what the desktop runs" if (
                    name == "kilix/kilix.env" and launch_key(key)) else ""
                print(f"  {name}: {key}: {old!r} -> {new!r}{note}")
        if args.command == "list":
            return 0
        if not args.yes:
            print("\nNothing written. Re-run with --yes to restore; the files it "
                  "replaces are backed up first.")
            return 0
        if desktop_running():
            print("\nThe desktop is running and would save its own state over the "
                  "restore. Close it first, or restore from Control Panel > Backup.",
                  file=sys.stderr)
            return 3
        result = restore(args.archive)
        print(f"\nRestored {result['written']} file(s), {result['unchanged']} already "
              "current." + (f" Previous files: {result['safety']}" if result["safety"] else ""))
        if result["written"]:
            print(restart_advice(result["names"]))
        return 0
    except (BackupError, OSError) as error:
        print(f"kilix backup: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main(sys.argv[1:]))
