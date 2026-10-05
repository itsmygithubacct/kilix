"""Back up and restore one user's Kilix settings and desktop documents.

A backup is a private gzip tar whose first member, ``manifest.json``, names
every file it carries with its size and SHA-256.  It holds:

* ``settings.conf``      the shared settings file every component reads;
* ``kilix/kilix.env``    the user's Kilix overrides;
* ``kilix95/config``     the desktop provider's configuration;
* ``kilix95/state``      its durable records (desktop layout, offers, choices),
                         without crash logs or crash-recovery checkpoints;
* ``desktop``            the documents on the desktop.

Restoring is non-destructive: it only creates or replaces the files the backup
names, each atomically, and first takes a safety backup of what it is about to
replace.  An archive is refused whole if any member is not a plain file or
directory, escapes its root, is missing from the manifest, or fails its digest.
Symlinks are never archived and never followed.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile

FORMAT = "kilix.backup/v1"
MANIFEST = "manifest.json"
MAX_TOTAL = 1 << 30                  # 1 GiB of payload
_STATE_EXCLUDE = ("document-recovery", "crash.log")


class BackupError(Exception):
    pass


def _home(*parts: str) -> str:
    base = os.environ.get("GPU_TERMINAL_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "gpu_terminal")
    return os.path.join(os.path.abspath(os.path.expanduser(base)), *parts)


def _env_dir(name: str, default: str) -> str:
    return os.path.abspath(os.path.expanduser(os.environ.get(name) or default))


def sources() -> list[tuple[str, str]]:
    """[(archive name, absolute path)] for everything a backup carries."""
    from kilix_sdk import paths, settings
    k95 = _env_dir("KILIX95_STORAGE_HOME", _home("kilix-95"))
    data = _env_dir("KILIX95_DATA_HOME", os.path.join(k95, "data"))
    return [
        ("settings.conf", settings.settings_path()),
        ("kilix/kilix.env", os.path.join(paths.config_dir(), "kilix.env")),
        ("kilix95/config", _env_dir("KILIX95_CONFIG_HOME", os.path.join(k95, "config"))),
        ("kilix95/state", _env_dir("KILIX95_STATE_HOME", os.path.join(k95, "state"))),
        ("desktop", _env_dir("KILIX_DESKTOP_DIR", os.path.join(data, "desktop"))),
    ]


def default_directory() -> str:
    return os.path.join(os.path.expanduser("~"), "kilix-backups")


def _excluded(name: str) -> bool:
    if not name.startswith("kilix95/state/"):
        return False
    rest = name[len("kilix95/state/"):]
    first = rest.split("/", 1)[0]
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


def _digest(path: str) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW)) as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
            size += len(block)
    return size, h.hexdigest()


def _private_output(path: str):
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".kilix-backup-", dir=directory)
    os.fchmod(fd, 0o600)
    return fd, temporary


def create(output: str | None = None, *, label: str = "kilix-backup") -> str:
    """Write a backup and return its path."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output = os.path.abspath(os.path.expanduser(
        output or os.path.join(default_directory(), f"{label}-{stamp}.tar.gz")))
    files = [item for name, path in sources() for item in _walk(name, path)]
    entries, total = {}, 0
    for name, path in files:
        size, digest = _digest(path)
        total += size
        if total > MAX_TOTAL:
            raise BackupError("backup would exceed 1 GiB; move large files off the desktop first")
        entries[name] = {"size": size, "sha256": digest}
    manifest = json.dumps({
        "format": FORMAT,
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "entries": entries,
    }, indent=1, sort_keys=True).encode("utf-8")
    fd, temporary = _private_output(output)
    try:
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            info = tarfile.TarInfo(MANIFEST)
            info.size, info.mode = len(manifest), 0o600
            tar.addfile(info, io.BytesIO(manifest))
            for name, path in files:
                with open(path, "rb", opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW)) as fh:
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = entries[name]["size"], 0o600
                    tar.addfile(info, fh)
        os.replace(temporary, output)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return output


def _safe_name(name: str) -> bool:
    parts = name.split("/")
    return (bool(name) and not name.startswith("/") and "\\" not in name
            and all(p not in ("", ".", "..") for p in parts))


def read(archive: str) -> tuple[dict, dict[str, bytes]]:
    """Validate an archive completely; return (manifest, {name: bytes})."""
    try:
        tar = tarfile.open(archive, mode="r:gz")
    except (OSError, tarfile.TarError) as error:
        raise BackupError(f"not a readable backup: {error}") from None
    payload: dict[str, bytes] = {}
    with tar:
        members = tar.getmembers()
        if not members or members[0].name != MANIFEST or not members[0].isfile():
            raise BackupError("missing manifest")
        try:
            manifest = json.loads(tar.extractfile(members[0]).read(1 << 20))
        except ValueError:
            raise BackupError("unreadable manifest") from None
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT \
                or not isinstance(manifest.get("entries"), dict):
            raise BackupError("unsupported backup format")
        entries = manifest["entries"]
        roots = {name for name, _path in sources()}
        total = 0
        for member in members[1:]:
            name = member.name
            if not member.isfile() or not _safe_name(name):
                raise BackupError(f"refusing archive member {name!r}")
            if not any(name == r or name.startswith(r + "/") for r in roots):
                raise BackupError(f"archive member outside the backup: {name!r}")
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
        missing = set(entries) - set(payload)
        if missing:
            raise BackupError(f"manifest names files the archive lacks: {sorted(missing)[:3]}")
    return manifest, payload


def _destination(name: str) -> str:
    for root, path in sources():
        if name == root:
            return path
        if name.startswith(root + "/"):
            return os.path.join(path, *name[len(root) + 1:].split("/"))
    raise BackupError(f"no destination for {name!r}")


def plan(archive: str) -> list[tuple[str, str, str]]:
    """[(name, destination, 'create'|'replace'|'same')] without writing anything."""
    _manifest, payload = read(archive)
    out = []
    for name, data in sorted(payload.items()):
        dest = _destination(name)
        if not os.path.lexists(dest):
            action = "create"
        else:
            try:
                same = _digest(dest)[1] == hashlib.sha256(data).hexdigest()
            except OSError:
                same = False
            action = "same" if same else "replace"
        out.append((name, dest, action))
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


def restore(archive: str) -> dict:
    """Apply a backup; returns {'safety': path, 'written': n, 'unchanged': n}.

    Every destination is checked before anything is written, so a refused
    archive leaves the user's files exactly as they were."""
    _manifest, payload = read(archive)
    targets = [(name, _destination(name), data) for name, data in sorted(payload.items())]
    for _name, dest, _data in targets:
        _check_destination(dest)
    safety = create(label="kilix-before-restore")
    written = unchanged = 0
    for _name, dest, data in targets:
        parent = os.path.dirname(dest)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        if os.path.isfile(dest) and _digest(dest)[1] == hashlib.sha256(data).hexdigest():
            unchanged += 1
            continue
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
        written += 1
    return {"safety": safety, "written": written, "unchanged": unchanged}


def main(argv: list[str]) -> int:
    import argparse
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
        for name, dest, action in rows:
            print(f"{action:8} {name}")
        if args.command == "list":
            return 0
        if not args.yes:
            print("\nNothing written. Re-run with --yes to restore; your current files "
                  "are backed up first.")
            return 0
        result = restore(args.archive)
        print(f"\nRestored {result['written']} file(s), {result['unchanged']} already "
              f"current. Previous files: {result['safety']}")
        print("Restart the desktop (or log out and in) to load restored settings.")
        return 0
    except BackupError as error:
        print(f"kilix backup: {error}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main(sys.argv[1:]))
