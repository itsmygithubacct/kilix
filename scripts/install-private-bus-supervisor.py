#!/usr/bin/python3
"""Normal root installation source; --plan is filesystem-only and inert.

Installs immutable source/template/own-account authorization; enables nothing.
No invocation of this installer is part of source/inert qualification.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

FILES = {"config/kilix_sdk/" + name: "/usr/libexec/kilix-private-bus-supervisor/" + name
         for name in ("bus_supervisor.py", "bus_supervision.py", "bus_ownership.py", "bus_platform.py")}
FILES.update({"share/private-bus/kilix-private-bus-supervisor@.service": "/etc/systemd/system/kilix-private-bus-supervisor@.service",
              "share/private-bus/90-kilix-private-bus.rules": "/usr/share/polkit-1/rules.d/90-kilix-private-bus.rules"})


def plan(root):
    records = []
    sources = [root / name for name in FILES if name.startswith("config/")]
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    version = hashlib.sha256("\n".join(hashes).encode()).hexdigest()
    directory = "/usr/libexec/kilix-private-bus-supervisor/" + version
    for source, target in FILES.items():
        path = root / source
        if not stat.S_ISREG(path.lstat().st_mode):
            raise RuntimeError("missing/unsafe supervision delivery source: " + source)
        data = path.read_bytes()
        if source.startswith("config/"):
            target = directory + "/" + path.name
        if source.endswith(".service"):
            data = data.replace(b"@PROGRAM@", (directory + "/bus_supervisor.py").encode())
        records.append(dict(source=source, target=target, mode=0o644, uid=0, gid=0,
                            sha256=hashlib.sha256(data).hexdigest(), data=data))
    return records


class InstallFailure(RuntimeError):
    def __init__(self, problems, pending):
        self.pending = tuple(pending)
        super().__init__("supervisor installation incomplete: " + "; ".join(map(str, problems)))


def deliver(records, filesystem, manager):
    """Exact-file transaction, including manager reload failure/rollback.

    No service starts/enables, no package downloads or environment/login change.
    Prior payloads are retained for rollback; installed paths may not be symlinks
    or nonroot/writable objects. No deletion of other modules or runtime state.
    """
    old, changed = {}, []
    try:
        for record in records:
            target = record["target"]
            old[target] = filesystem.read_safe(target)
        for record in records:
            changed.append(record["target"])  # Includes ambiguous post-publication failures.
            if old[record["target"]] != record["data"]:
                if "/usr/libexec/" in record["target"] and old[record["target"]] is not None:
                    raise RuntimeError("immutable supervisor version conflicts")
                filesystem.replace(record["target"], record["data"], record["mode"])
        for record in records:
            if filesystem.read_safe(record["target"]) != record["data"]:
                raise RuntimeError("installed source readback mismatch: " + record["target"])
        manager.reload()
    except BaseException as error:
        problems, pending = [error], []
        for target in reversed(changed):
            try:
                if old[target] is None: filesystem.remove(target)
                else: filesystem.replace(target, old[target], 0o644)
            except BaseException as rollback_error:
                problems.append(rollback_error)
                pending.append(target)
        try: manager.reload()
        except BaseException as reload_error: problems.append(reload_error)
        raise InstallFailure(problems, pending) from error


class InstalledFilesystem:
    def parent(self, path):
        parent = Path(path).parent
        parts = parent.parts
        current = Path(parts[0])
        for part in parts[1:]:
            current /= part
            try: st = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o755)
                st = current.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                raise RuntimeError("unsafe installed directory: " + str(current))
        return parent

    def read_safe(self, path):
        self.parent(path)
        target = Path(path)
        try: st = target.lstat()
        except FileNotFoundError: return None
        if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise RuntimeError("unsafe installed payload: " + path)
        return target.read_bytes()

    def replace(self, path, data, mode):
        self.parent(path)
        # Exclusive temporary creation: never follow/overwrite an existing
        # caller-selected temporary, then publish via the root-owned parent.
        temp = Path(path + ".kilix-install-new")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chown(temp, 0, 0, follow_symlinks=False)
            os.chmod(temp, mode, follow_symlinks=False)
            os.replace(temp, path)
        finally:
            if temp.exists(): temp.unlink()

    def remove(self, path):
        if self.read_safe(path) is not None:
            Path(path).unlink()


class SystemManager:
    def reload(self):
        subprocess.run(["/usr/bin/systemctl", "--system", "daemon-reload"],
                       check=True, timeout=10, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})


def main():
    records = plan(Path(__file__).resolve().parents[1])
    if sys.argv[1:] == ["--plan"]:
        print(json.dumps([{k: v for k, v in item.items() if k != "data"} for item in records], indent=2))
        return
    if sys.argv[1:] != ["--install"] or os.getuid() != 0 or os.geteuid() != 0:
        raise SystemExit("use --plan or the normal root installer with --install")
    deliver(records, InstalledFilesystem(), SystemManager())


if __name__ == "__main__":
    main()
