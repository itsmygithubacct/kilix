"""Explicit installation of the three bundled skills; never edit agent config.

Three discovery links share one atomic version pointer outside the scan root.
Receipts bind every byte and registration inode. Old snapshots are retained:
removing a registration must never recursively delete a user's later edits.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid

SOURCE = Path(__file__).resolve().parent.parent
NAMES = ("kilix", "kilix-model-switch", "kilix-session-launch")
AGENTS = ("codex", "claude", "kimi")
STORE = ".kilix-skill-bundles"
LIMIT = 1024 * 1024
MAX_FILES = 256
PROFILE = "documented-user-roots-2026-09"
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC


class Conflict(Exception):
    """A refusal that preserves existing entries."""


def identity(info):
    return [info.st_dev, info.st_ino]


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise Conflict(f"agent root must be absolute without '..': {path}")
    return path


def locations(agent, home=None, env=None):
    """Current documented conventions, not a guess based on directory presence.

    HOME is the caller's real user home for normal CLI use. Custom agent data
    roots only redirect the agent-specific location, never the shared root.
    No older-version fallback is silently created.
    """
    env = os.environ if env is None else env
    home = _absolute(home if home is not None else Path.home())
    shared = home / ".agents/skills"
    codex = _absolute(env.get("CODEX_HOME") or home / ".codex") / "skills"
    claude = _absolute(env.get("CLAUDE_CONFIG_DIR") or home / ".claude") / "skills"
    kimi = _absolute(env.get("KIMI_CODE_HOME") or home / ".kimi-code") / "skills"
    if agent == "codex":
        return shared, [codex]
    if agent == "claude":
        return claude, []
    if agent == "kimi":
        chosen = kimi if env.get("KIMI_CODE_HOME") else shared
        # Older Kimi selected the first existing generic/brand root. Treat its
        # registrations as conflicts, not authority to create a second copy.
        others = [shared, kimi, home / ".config/agents/skills", home / ".kimi/skills"]
        return chosen, list(dict.fromkeys(p for p in others if p != chosen))
    raise Conflict(f"unknown agent: {agent}")


@contextmanager
def directory(path, *, create=False, require_user=True):
    """Pin each ancestor; do not follow or chmod user entries."""
    path = _absolute(path)
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=fd)
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            sticky_tmp = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
            # Existing user-owned agent roots commonly use 0775. Preserve
            # their mode; only our separate snapshot store must be 0700.
            unsafe_write = (info.st_mode & 0o002 or
                            (info.st_uid != os.getuid() and info.st_mode & 0o020))
            if info.st_uid not in (0, os.getuid()) or (unsafe_write and not sticky_tmp):
                raise Conflict(f"unsafe writable or foreign directory: {path}")
        if require_user and os.fstat(fd).st_uid != os.getuid():
            raise Conflict(f"agent directory is not owned by this user: {path}")
        yield fd
    finally:
        os.close(fd)


def info_at(fd, name):
    try:
        return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def read_file(fd, name, limit=LIMIT):
    handle = os.open(name, FILE_FLAGS, dir_fd=fd)
    try:
        before = os.fstat(handle)
        if (not stat.S_ISREG(before.st_mode) or before.st_size > limit
                or before.st_nlink != 1 or before.st_mode & 0o022):
            raise Conflict(f"not an unchanged bounded private regular file: {name}")
        data = bytearray()
        while len(data) <= before.st_size:
            chunk = os.read(handle, min(65536, before.st_size + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(handle)
        if len(data) != before.st_size or (
            before.st_size, before.st_mtime_ns, before.st_ctime_ns
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Conflict(f"file changed during verification: {name}")
        return bytes(data), stat.S_IMODE(before.st_mode)
    finally:
        os.close(handle)


def population(fd, *, receipt=False):
    files = {}
    directories = []
    total = 0

    def walk(parent, prefix="", depth=0):
        nonlocal total
        if depth > 12:
            raise Conflict("skill tree is too deep")
        entries = sorted(os.listdir(parent))
        if len(entries) > MAX_FILES:
            raise Conflict("too many skill entries")
        for name in entries:
            if receipt and not prefix and name == "receipt.json":
                continue
            relative = prefix + name
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(entry.st_mode):
                if entry.st_mode & 0o022 or (receipt and (
                    stat.S_IMODE(entry.st_mode) != 0o700 or entry.st_uid != os.getuid()
                )):
                    raise Conflict(f"writable package directory: {relative}")
                directories.append(relative)
                child = os.open(name, DIR_FLAGS, dir_fd=parent)
                try:
                    walk(child, relative + "/", depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(entry.st_mode):
                if receipt and entry.st_uid != os.getuid():
                    raise Conflict(f"foreign package file: {relative}")
                files[relative] = read_file(parent, name)
                total += len(files[relative][0])
                if total > 8 * LIMIT:
                    raise Conflict("skill bundle exceeds 8 MiB")
            else:
                raise Conflict(f"package contains a link or special file: {relative}")
            if len(files) + len(directories) > MAX_FILES:
                raise Conflict("too many skill entries")
    walk(fd)
    return files, directories


def manifest(files, directories):
    return {"directories": directories, "files": {
        name: {"sha256": hashlib.sha256(data).hexdigest(), "mode": mode,
               "size": len(data)} for name, (data, mode) in files.items()}}


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def bundle(source=SOURCE):
    with directory(source / "skills", require_user=False) as fd:
        files, dirs = population(fd)
    files = {name: (data, 0o700 if mode & 0o111 else 0o600)
             for name, (data, mode) in files.items()}
    if sorted(name for name in dirs if "/" not in name) != sorted(NAMES):
        raise Conflict("the bundled set must contain exactly the three Kilix skills")
    if any(name.split("/")[0] not in NAMES for name in files):
        raise Conflict("unexpected file outside a bundled skill")
    descriptions = {}
    for name in NAMES:
        data = files.get(name + "/SKILL.md", (b"", 0))[0].decode("utf-8")
        header = data.split("---", 2)
        if len(header) != 3 or header[0].strip():
            raise Conflict(f"missing frontmatter: {name}")
        if not re.search(r"^name:\s*[\"']?" + re.escape(name) + r"[\"']?\s*$",
                         header[1], re.M):
            raise Conflict(f"wrong discovery name: {name}")
        description = re.search(r"^description:\s*(\S[^\n]*)", header[1], re.M)
        if not description:
            raise Conflict(f"missing description: {name}")
        descriptions[name] = description[1].strip("\"'")
    with directory(source, require_user=False) as fd:
        version = read_file(fd, "VERSION", 128)[0].decode().strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+.][a-zA-Z0-9.-]+)?", version):
        raise Conflict("invalid bundled VERSION")
    record = {"version": version, "population": manifest(files, dirs)}
    record["digest"] = hashlib.sha256(encoded(record)).hexdigest()
    return record, files, dirs, descriptions


@contextmanager
def inspect_directory(path):
    """Read names only, including ordinary shared or symlinked user roots.

    This must never be used for writes or receipt authority. A user-selected
    alternate root need not satisfy the managed destination's permissions.
    """
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield fd
    finally:
        os.close(fd)


def no_duplicates(root, others):
    for path in [root, *others]:
        try:
            with inspect_directory(path) as fd:
                for name in NAMES:
                    candidates = [name + ".md"] if path == root else [name, name + ".md"]
                    for candidate in candidates:
                        if info_at(fd, candidate) is not None:
                            raise Conflict(f"existing discovery entry must be reconciled: {path / candidate}")
        except FileNotFoundError:
            pass


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise Conflict("duplicate receipt key")
        out[key] = value
    return out


def current(store, root, root_path):
    entry = info_at(store, "current")
    if entry is None:
        if any(info_at(root, name) is not None for name in NAMES):
            raise Conflict("existing skills have no Kilix ownership receipt")
        return None
    if not stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid():
        raise Conflict("foreign current-version entry")
    target = os.readlink("current", dir_fd=store)
    if not re.fullmatch(r"version-[0-9a-f]{32}", target):
        raise Conflict("invalid current-version target")
    try:
        gen = os.open(target, DIR_FLAGS, dir_fd=store)
    except FileNotFoundError:
        raise Conflict("current package snapshot is missing") from None
    try:
        try:
            raw = read_file(gen, "receipt.json")[0]
        except FileNotFoundError:
            raise Conflict("current package receipt is missing") from None
        record = json.loads(raw, object_pairs_hook=_pairs)
        if (not isinstance(record, dict) or set(record) != {
            "schema", "root", "owners", "links", "bundle"
        } or record["schema"] != 1 or record["root"] != str(root_path)
            or not isinstance(record["owners"], list)
            or any(not isinstance(a, str) or a not in AGENTS for a in record["owners"])
            or record["owners"] != sorted(set(record["owners"]))
            or not isinstance(record["links"], dict)
            or set(record["links"]) != (set(NAMES) if record["owners"] else set())
            or not isinstance(record["bundle"], dict)):
            raise Conflict("invalid Kilix ownership receipt")
        files, dirs = population(gen, receipt=True)
        expected = record["bundle"]
        if (set(expected) != {"version", "population", "digest"}
            or manifest(files, dirs) != expected["population"]
            or hashlib.sha256(encoded({k: expected[k] for k in ("version", "population")})).hexdigest()
                != expected["digest"]):
            raise Conflict("installed package was edited; preserved unchanged")
        for name in NAMES:
            link = info_at(root, name)
            if not record["owners"]:
                if link is not None:
                    raise Conflict(f"unowned discovery entry: {name}")
            elif (link is None or not stat.S_ISLNK(link.st_mode)
                  or link.st_uid != os.getuid() or identity(link) != record["links"][name]
                  or os.readlink(name, dir_fd=root) != link_target(name)):
                raise Conflict(f"registration was edited or replaced: {name}")
        return record, files, dirs, identity(entry)
    finally:
        os.close(gen)


def link_target(name):
    return f"../{STORE}/current/{name}"


@contextmanager
def locked(store):
    lock = os.open("lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
                   | os.O_CLOEXEC, 0o600, dir_fd=store)
    try:
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise Conflict("unsafe skill installation lock")
        deadline = time.monotonic() + 2
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Conflict("another skill installation holds the lock") from None
                time.sleep(0.02)
        yield
    finally:
        os.close(lock)


def write_new(fd, name, data, mode=0o600):
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
                  | os.O_NOFOLLOW, mode, dir_fd=fd)
    try:
        with os.fdopen(out, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(out)
    finally:
        os.close(out)


def _still_here(path, fd):
    with directory(path) as live:
        if identity(os.fstat(live)) != identity(os.fstat(fd)):
            raise Conflict(f"agent directory was relocated or replaced: {path}")


def move_new(source_fd, source, target_fd, target):
    """Linux rename with no replacement, including dangling symlinks."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_fd, os.fsencode(source), target_fd, os.fsencode(target), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def publish(store, root, root_path, old, owners, packaged):
    record, files, dirs = packaged
    generation = "version-" + uuid.uuid4().hex
    os.mkdir(generation, 0o700, dir_fd=store)
    gen = os.open(generation, DIR_FLAGS, dir_fd=store)
    created = {}
    pending = "pending-" + uuid.uuid4().hex
    committed = False
    removed = []
    backup = None
    try:
        for name in dirs:
            os.mkdir(name, 0o700, dir_fd=gen)
        for name, (data, mode) in files.items():
            write_new(gen, name, data, mode)
        # Check again after preparing the complete snapshot, before touching
        # registrations. Failed/interrupted preparations stay outside discovery.
        verified = current(store, root, root_path)
        if (None if verified is None else (verified[0], verified[3])) != (
            None if old is None else (old[0], old[3])
        ):
            raise Conflict("installation changed during preparation")
        _still_here(root_path, root)
        _still_here(root_path.parent / STORE, store)
        links = {} if old is None else dict(old[0]["links"])
        if owners and not links:
            for name in NAMES:
                os.symlink(link_target(name), name, dir_fd=root)
                created[name] = identity(os.stat(name, dir_fd=root, follow_symlinks=False))
            links = created.copy()
        receipt = {"schema": 1, "root": str(root_path), "owners": sorted(owners),
                   "links": links if owners else {}, "bundle": record}
        write_new(gen, "receipt.json", encoded(receipt))
        os.fsync(gen)
        if not owners:
            # Only the exact links verified above are removed. Copied versions
            # are deliberately retained, including any later user additions.
            backup_name = "removed-" + uuid.uuid4().hex
            os.mkdir(backup_name, 0o700, dir_fd=store)
            backup = os.open(backup_name, DIR_FLAGS, dir_fd=store)
            for name in NAMES:
                if name in links:
                    found = info_at(root, name)
                    if found is None or identity(found) != links[name]:
                        raise Conflict(f"registration changed during removal: {name}")
                    move_new(root, name, backup, name)
                    removed.append(name)
                    if identity(os.stat(name, dir_fd=backup, follow_symlinks=False)) != links[name]:
                        raise Conflict(f"registration changed during removal: {name}")
        os.symlink(generation, pending, dir_fd=store)
        _still_here(root_path, root)
        _still_here(root_path.parent / STORE, store)
        pointer = info_at(store, "current")
        if old is None:
            if pointer is not None:
                raise Conflict("current-version entry appeared during preparation")
        elif (pointer is None or not stat.S_ISLNK(pointer.st_mode)
              or identity(pointer) != old[3]):
            raise Conflict("current-version entry changed during preparation")
        # Atomic set update: all three discovery names select this one pointer.
        if old is None:
            move_new(store, pending, store, "current")
        else:
            os.replace(pending, "current", src_dir_fd=store, dst_dir_fd=store)
        committed = True
        os.fsync(store)
        os.fsync(root)
    finally:
        os.close(gen)
        if not committed:
            for name in removed:
                # Never overwrite an entry another process created meanwhile.
                # A failed restore leaves the exact entry in the private backup.
                try:
                    move_new(backup, name, root, name)
                except FileExistsError:
                    pass
            for name, inode in created.items():
                found = info_at(root, name)
                if found is not None and identity(found) == inode:
                    os.unlink(name, dir_fd=root)
            if info_at(store, pending) is not None:
                os.unlink(pending, dir_fd=store)
        if backup is not None:
            os.close(backup)


def operate(action, agent, *, source=SOURCE, home=None, env=None):
    root_path, others = locations(agent, home, env)
    result = {"agent": agent, "root": str(root_path), "profile": PROFILE,
              "skills": list(NAMES), "state": "not-installed", "owners": []}
    if root_path == (Path(home) if home is not None else Path.home()) / ".agents/skills":
        result["discovery_note"] = (
            "Codex and Kimi both auto-discover this shared root; --agent tracks "
            "registration/removal ownership, not isolation from the other client."
        )
    if action != "remove":
        no_duplicates(root_path, others)
    packaged = bundle(source) if action == "install" else None
    create = action == "install"
    store_path = root_path.parent / STORE
    if not create and not os.path.lexists(store_path):
        try:
            with inspect_directory(root_path) as inspected:
                if any(info_at(inspected, name) is not None for name in NAMES):
                    raise Conflict("existing skills have no Kilix ownership receipt")
        except FileNotFoundError:
            pass
        return result
    try:
        root_context = directory(root_path, create=create)
        root = root_context.__enter__()
    except FileNotFoundError:
        if create:
            raise
        if os.path.lexists(store_path / "current"):
            raise Conflict("managed discovery directory is missing") from None
        return result
    try:
        with directory(store_path, create=create) as store:
            if os.fstat(store).st_mode & 0o077:
                raise Conflict("bundle store must be private (0700); existing mode preserved")
            if action == "status":
                old = current(store, root, root_path)
            else:
                with locked(store):
                    old = current(store, root, root_path)
                    owners = set(old[0]["owners"]) if old else set()
                    if action == "install":
                        owners.add(agent)
                        assert packaged is not None
                        same = old and old[0]["bundle"] == packaged[0]
                        if not same or owners != set(old[0]["owners"]):
                            publish(store, root, root_path, old, owners, packaged[:3])
                    elif action == "remove" and agent in owners:
                        owners.remove(agent)
                        publish(store, root, root_path, old, owners,
                                (old[0]["bundle"], old[1], old[2]))
                    old = current(store, root, root_path)
            if old:
                result.update(owners=old[0]["owners"], version=old[0]["bundle"]["version"],
                              digest=old[0]["bundle"]["digest"], snapshots=str(store_path))
                if old[0]["owners"]:
                    result["state"] = "installed" if agent in old[0]["owners"] else "shared"
    finally:
        root_context.__exit__(None, None, None)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "status", "install", "remove"))
    parser.add_argument("--agent", choices=AGENTS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.action in ("install", "remove") and not args.agent:
        parser.error("install and remove require an explicit --agent")
    try:
        if args.action == "list":
            record, _, _, descriptions = bundle()
            rows = [{"name": name, "description": descriptions[name],
                     "version": record["version"], "digest": record["digest"]} for name in NAMES]
        else:
            rows = []
            for agent in ([args.agent] if args.agent else AGENTS):
                try:
                    rows.append(operate(args.action, agent))
                except (Conflict, OSError, ValueError, TypeError, RecursionError) as error:
                    rows.append({"agent": agent, "state": "conflict", "error": str(error)})
    except (Conflict, OSError, ValueError, TypeError, RecursionError) as error:
        rows = [{"state": "conflict", "error": str(error)}]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            if "name" in row:
                print(f"{row['name']}  {row['version']}  {row['description']}")
            else:
                print(f"{row.get('agent', 'bundle')}: {row['state']}"
                      f"  {row.get('root', '')}  {row.get('error', '')}")
                if row.get("discovery_note"):
                    print(f"  {row['discovery_note']}")
                if row.get("snapshots") and args.action == "remove":
                    print(f"  Private version snapshots retained: {row['snapshots']}")
    return 1 if any(row.get("state") == "conflict" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
