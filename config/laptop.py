"""kilix laptop — laptop session profiles as a host verb.

Every Kilix desktop that ships a laptop object reads the same profile
convention: plain KEY=value files named <id>.profile in
~/.local/gpu_terminal/laptop/ (override: KILIX_LAPTOP_PROFILES, an absolute
directory). A profile either names another desktop provider to open, or
describes one kilix terminal session — its pane layout, each pane's working
directory or ssh destination, and each pane's command. This module is the
host-side owner of that convention: `kilix laptop list|open|status|close`
is the one implementation the desktops delegate to (probed, never assumed,
the way games.py probes `kilix games play`), so a laptop opened from the
mansion, the house, the launcher TUI, or a typed command is the same code
spawning the same argv.

The parser mirrors the desktops' rejection rules exactly — a profile one
surface refuses, every surface refuses. Values never pass through a shell:
panes become lines of a kitty --session file, launches are fixed argv
vectors, and the two characters that could change how kitty splits a line
(double quotes, control bytes) are refused at parse time.

RUN REGISTRY:

  directory  <profiles>/run — <profiles> is $KILIX_LAPTOP_PROFILES
             (absolute) or ~/.local/gpu_terminal/laptop; every level is
             created 0700 on first write.
  file       run/<profile-id>.pid — ASCII decimal PID on the first line,
             followed by boot_id= and start_time= lines binding it to a
             process instance. Written atomically, mode 0600. The first
             line remains readable by older desktop implementations.
  liveness   a live, non-zombie process must match both identity fields.
             Dead or mismatched records are stale and removed. A live
             legacy PID-only record is unverified: preserve it and refuse
             open/close until its window is closed manually.
  close      verify identity after opening a Linux process descriptor,
             then send SIGTERM through that descriptor so PID reuse
             cannot redirect the signal to another process.
  scope      only pane profiles are tracked. A desktop profile opens a
             provider tab through `kilix <provider>` whose wrapper exits
             once the tab exists, so there is no long-lived pid to
             record; status reports such profiles as `desktop`.
"""
from __future__ import annotations

import os
import re
import select
import signal
import sys
import tempfile
import time

PROFILE_SUFFIX = ".profile"
ID_MAX = 40
NAME_MAX = 48
VALUE_MAX = 200
DESKTOP_MAX = 12
PANES_MAX = 8
FILE_MAX = 16 * 1024
PROVIDERS = ("desktop", "95", "xp", "cap", "tui", "land")

USAGE = """usage: kilix laptop [list|open PROFILE|status|close PROFILE]
  list           the profile ids, one per line
  open PROFILE   open the profile: a pane profile becomes its own kilix
                 session window and is recorded in the run registry; a
                 desktop profile opens that provider (not tracked)
  status         one line per profile: running (pid N) | stopped | desktop
                 | unverified (legacy or unreadable process identity)
  close PROFILE  SIGTERM the profile's recorded session, then remove its
                 registry entry
Profiles live in ~/.local/gpu_terminal/laptop (KILIX_LAPTOP_PROFILES
overrides); the run registry is the run/ directory beside them."""


class ProfileError(Exception):
    """One short user-facing sentence, matching the desktops' wording."""


# ── the shared profile convention ────────────────────────────────────────────


def profiles_directory() -> str:
    override = os.environ.get("KILIX_LAPTOP_PROFILES", "")
    if override:
        if not override.startswith("/"):
            raise ProfileError("No laptop profile directory.")
        return override
    home = os.environ.get("HOME", "")
    if not home.startswith("/"):
        raise ProfileError("No laptop profile directory.")
    return os.path.join(home, ".local", "gpu_terminal", "laptop")


def valid_id(profile_id: str) -> bool:
    if not profile_id or profile_id.startswith(".") or len(profile_id) >= ID_MAX:
        return False
    return all(
        c.isascii() and (c.isalnum() or c in "._-") for c in profile_id
    )


def _valid_value(value: str) -> bool:
    return all(ord(c) >= 0x20 and ord(c) != 0x7F and c != '"' for c in value)


def _valid_ssh_destination(value: str) -> bool:
    if not value or value.startswith("-"):
        return False
    return all(
        c.isascii() and (c.isalnum() or c in "._-@") for c in value
    )


def _blank_pane() -> dict:
    return {"title": "", "cwd": "", "ssh": "", "cmd": ""}


def _assign_pane_key(profile: dict, seen: list, key: str, value: str) -> None:
    number, _, field = key[5:].partition(".")
    # The same numbers strtol accepts in the C parsers: optional leading
    # whitespace and sign, then decimal digits, ending exactly at the dot.
    if not re.fullmatch(r"[ \t]*[+-]?[0-9]+", number) or not field:
        raise ProfileError("Unknown profile key.")
    pane = int(number.replace(" ", "").replace("\t", ""))
    if pane < 1 or pane > PANES_MAX:
        raise ProfileError("Pane numbers run 1..8.")
    seen[pane - 1] = True
    slot = profile["panes"][pane - 1]
    profile["pane_count"] = max(profile["pane_count"], pane)
    profile["saw_pane_key"] = True
    if field == "title":
        if len(value) >= NAME_MAX:
            raise ProfileError("A pane title is too long.")
        slot["title"] = value
    elif field == "cwd":
        if len(value) >= VALUE_MAX:
            raise ProfileError("A pane directory is too long.")
        slot["cwd"] = value
    elif field == "ssh":
        if len(value) >= VALUE_MAX:
            raise ProfileError("A pane destination is too long.")
        if not _valid_ssh_destination(value):
            raise ProfileError("ssh destinations are [user@]host only.")
        slot["ssh"] = value
    elif field == "cmd":
        if len(value) >= VALUE_MAX:
            raise ProfileError("A pane command is too long.")
        slot["cmd"] = value
    else:
        raise ProfileError("Unknown profile key.")


def _assign_key(profile: dict, seen: list, key: str, value: str) -> None:
    if not _valid_value(value):
        raise ProfileError(
            "Profile values cannot hold quotes or control characters."
        )
    if key == "name":
        if not value or len(value) >= NAME_MAX:
            raise ProfileError("The profile name will not fit.")
        profile["name"] = value
    elif key == "desktop":
        if value not in PROVIDERS:
            raise ProfileError("desktop= must name a kilix provider.")
        profile["desktop"] = value
    elif key == "layout":
        if value == "splits":
            profile["tabs"] = False
        elif value == "tabs":
            profile["tabs"] = True
        else:
            raise ProfileError("layout= must be splits or tabs.")
    elif key.startswith("pane."):
        _assign_pane_key(profile, seen, key, value)
    else:
        raise ProfileError("Unknown profile key.")


def load_profile(profile_id: str) -> dict:
    """Strict parse of <directory>/<id>.profile — the desktops' exact
    rejection catalogue. Raises ProfileError with one short sentence."""
    if not valid_id(profile_id):
        raise ProfileError("That profile name is not valid.")
    path = os.path.join(profiles_directory(), profile_id + PROFILE_SUFFIX)
    try:
        with open(path, "rb") as handle:
            raw = handle.read(FILE_MAX + 1)
    except OSError as error:
        raise ProfileError("That profile cannot be read.") from error
    if len(raw) >= FILE_MAX:
        raise ProfileError("That profile cannot be read.")
    # latin-1 maps every byte to one code point, so the length and
    # character checks below see exactly the bytes the C parsers see.
    contents = raw.decode("latin-1")
    profile = {
        "id": profile_id,
        "name": profile_id,
        "desktop": "",
        "tabs": False,
        "pane_count": 0,
        "saw_pane_key": False,
        "panes": [_blank_pane() for _ in range(PANES_MAX)],
    }
    seen = [False] * PANES_MAX
    for raw in contents.split("\n"):
        line = raw[:-1] if raw.endswith("\r") else raw
        line = line.lstrip(" \t")
        if not line or line.startswith("#"):
            continue
        key, equals, value = line.partition("=")
        if not equals:
            raise ProfileError("Profile lines are KEY=value.")
        _assign_key(profile, seen, key, value)
    if profile["desktop"]:
        if profile["saw_pane_key"]:
            raise ProfileError("A profile is a desktop or panes, not both.")
        return profile
    if profile["pane_count"] == 0:
        raise ProfileError("A profile needs pane.1 or desktop=.")
    if not all(seen[: profile["pane_count"]]):
        raise ProfileError("Pane numbers must be contiguous.")
    return profile


def desktop_arguments(profile: dict) -> list:
    """argv words after `kilix` for a desktop profile; [] for panes."""
    provider = profile["desktop"]
    if not provider:
        return []
    if provider == "desktop":
        return ["desktop"]
    if provider == "95":
        return ["desktop", "95"]
    return [provider]


def _expanded_cwd(cwd: str) -> str:
    home = os.environ.get("HOME", "")
    if cwd.startswith("~") and (len(cwd) == 1 or cwd[1] == "/") and \
            home.startswith("/"):
        return home + cwd[1:]
    return cwd


def session_text(profile: dict) -> str:
    """The kitty --session file for a pane profile: line-for-line the text
    the desktops generate, so a delegated open behaves identically."""
    if profile["desktop"] or profile["pane_count"] == 0:
        raise ProfileError("Not a pane profile.")
    lines = [
        "# Generated by kilix laptop from %s.profile; do not edit.\n"
        % profile["id"],
        "os_window_title %s\n" % profile["name"],
    ]
    if not profile["tabs"]:
        lines.append("new_tab %s\n" % profile["name"])
        lines.append("layout splits\n")
    for index in range(profile["pane_count"]):
        pane = profile["panes"][index]
        location = "--location=vsplit " if index % 2 == 1 \
            else "--location=hsplit "
        if profile["tabs"]:
            lines.append("new_tab %s\n"
                         % (pane["title"] or profile["name"]))
            location = ""
        elif index == 0:
            location = ""
        if pane["title"]:
            lines.append("title %s\n" % pane["title"])
        if pane["ssh"]:
            launch = "launch %sssh -t %s" % (location, pane["ssh"])
            if pane["cmd"] and pane["cwd"]:
                launch += " \"cd %s && exec %s\"" % (pane["cwd"],
                                                     pane["cmd"])
            elif pane["cmd"]:
                launch += " \"exec %s\"" % pane["cmd"]
            elif pane["cwd"]:
                launch += " \"cd %s && exec \\$SHELL -l\"" % pane["cwd"]
            lines.append(launch + "\n")
            continue
        if pane["cwd"]:
            lines.append("cd %s\n" % _expanded_cwd(pane["cwd"]))
        if pane["cmd"]:
            lines.append("launch %ssh -lc \"%s\"\n" % (location,
                                                       pane["cmd"]))
        elif location:
            lines.append("launch %s\n" % location)
        else:
            lines.append("launch\n")
    return "".join(lines)


# ── the run registry ─────────────────────────────────────────────────────────


def run_directory() -> str:
    return os.path.join(profiles_directory(), "run")


def _ensure_directory(path: str) -> None:
    """Each missing level is created 0700; existing levels are left as
    found — the same walk every desktop performs."""
    parts = path.split("/")
    current = ""
    for part in parts:
        if not part:
            continue
        current += "/" + part
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            pass


def _write_private(path: str, text: str) -> None:
    descriptor, temp = tempfile.mkstemp(dir=os.path.dirname(path),
                                       prefix=".%s." % os.path.basename(path))
    try:
        # latin-1 undoes the byte-preserving decode load_profile performed,
        # so session files carry the profile's original bytes.
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(text.encode("latin-1"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def pid_path(profile_id: str) -> str:
    return os.path.join(run_directory(), profile_id + ".pid")


def record_session(profile_id: str, pid: int) -> None:
    identity = _process_identity(pid)
    if identity is None:
        raise ProfileError("the session exited immediately")
    _ensure_directory(run_directory())
    _write_private(pid_path(profile_id), "%d\nboot_id=%s\nstart_time=%s\n"
                   % (pid, *identity))


def _process_identity(pid: int):
    """Boot and start ticks, None if exited; unreadable identity is an error."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as handle:
            if os.fstat(handle.fileno()).st_uid != os.geteuid():
                raise ProfileError("the session process belongs to another user")
            fields = handle.read().rsplit(b") ", 1)[1].split()
        if fields[0] in (b"Z", b"X"):
            return None
        start = str(int(fields[19]))
    except (FileNotFoundError, ProcessLookupError):
        return None
    except (OSError, ValueError, IndexError) as error:
        raise ProfileError("cannot verify the session process identity") from error
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as handle:
            boot = handle.read().strip()
        if not re.fullmatch(r"[0-9a-f-]{36}", boot):
            raise ValueError("invalid boot id")
    except (OSError, ValueError) as error:
        raise ProfileError("cannot verify the session boot identity") from error
    return boot, start


def _alive(pid: int) -> bool:
    """The actual process check the contract requires. EPERM means the
    process exists but is not ours to signal — still alive. A zombie is
    already gone: its window closed, and only an unreaped parent keeps
    the pid answering kill(0)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    try:
        with open("/proc/%d/stat" % pid, "rb") as handle:
            stat = handle.read(512)
        closing = stat.rfind(b")")
        if closing >= 0 and stat[closing + 2:closing + 3] == b"Z":
            return False
    except OSError:
        pass
    return True


def _session_record(profile_id: str):
    path = pid_path(profile_id)
    try:
        with open(path, "r", encoding="ascii", errors="strict") as handle:
            lines = handle.read(1024).splitlines()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise ProfileError("cannot read the session registry") from error
    try:
        pid = int(lines[0])
    except (ValueError, IndexError):
        pid = 0
    if pid <= 1:
        clear_session(profile_id)
        return None
    fields = dict(line.split("=", 1) for line in lines[1:] if "=" in line)
    return pid, (fields.get("boot_id"), fields.get("start_time"))


def _verified_record(profile_id: str, record) -> bool:
    pid, recorded = record
    current = _process_identity(pid)
    if current is None:
        clear_session(profile_id)
        return False
    if not all(recorded):
        raise ProfileError("unverified legacy session; close its window manually")
    if current != recorded:
        clear_session(profile_id)
        return False
    return True


def session_pid(profile_id: str):
    """Return only a verified live process; never adopt a reused PID."""
    record = _session_record(profile_id)
    return record[0] if record and _verified_record(profile_id, record) else None


def clear_session(profile_id: str) -> None:
    try:
        os.unlink(pid_path(profile_id))
    except OSError:
        pass


def scan_profiles() -> list:
    """Sorted profile ids. The directory is created on first use; the host
    ships no bundled examples, so it never seeds (the desktops do)."""
    directory = profiles_directory()
    if not os.path.isdir(directory):
        _ensure_directory(directory)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    ids = []
    for name in names:
        if not name.endswith(PROFILE_SUFFIX):
            continue
        stem = name[: -len(PROFILE_SUFFIX)]
        if valid_id(stem):
            ids.append(stem)
    return sorted(ids)


# ── the verbs ────────────────────────────────────────────────────────────────


def _kilix_command() -> str:
    home = os.environ.get("KILIX_HOME", "")
    path = os.path.join(home, "kilix") if home else ""
    if not path or not os.access(path, os.X_OK):
        raise ProfileError("no kilix launcher (KILIX_HOME is not set)")
    return path


def _spawn_detached(argv: list) -> int:
    """Fixed argv, stdio on /dev/null, its own session so a closing
    terminal never HUPs it. No shell is ever involved. posix_spawn returns
    only the pid, so a deliberately long-lived detached child does not leave
    a discarded subprocess wrapper that emits ResourceWarning."""
    devnull = os.open(os.devnull, os.O_RDWR)
    actions = [
        (os.POSIX_SPAWN_DUP2, devnull, descriptor)
        for descriptor in (0, 1, 2)
    ]
    if devnull > 2:
        actions.append((os.POSIX_SPAWN_CLOSE, devnull))
    try:
        return os.posix_spawn(
            argv[0], argv, os.environ, file_actions=actions, setsid=True)
    finally:
        os.close(devnull)


def _child_status(pid: int):
    """Return an exited direct child's status without blocking, else None."""
    try:
        waited, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return None if _alive(pid) else 0
    if waited == 0:
        return None
    return os.waitstatus_to_exitcode(status)


def _session_file_directory() -> str:
    configured = os.environ.get("KILIX_SESSION_HOME", "")
    if configured.startswith("/"):
        return configured
    return run_directory()


def cmd_list() -> int:
    for profile_id in scan_profiles():
        print(profile_id)
    return 0


def cmd_status() -> int:
    for profile_id in scan_profiles():
        try:
            profile = load_profile(profile_id)
        except ProfileError:
            print("%s invalid" % profile_id)
            continue
        if profile["desktop"]:
            print("%s desktop" % profile_id)
            continue
        try:
            pid = session_pid(profile_id)
        except ProfileError:
            print("%s unverified" % profile_id)
            continue
        if pid is not None:
            print("%s running (pid %d)" % (profile_id, pid))
        else:
            print("%s stopped" % profile_id)
    return 0


def cmd_open(profile_id: str) -> int:
    profile = load_profile(profile_id)
    kilix = _kilix_command()
    if profile["desktop"]:
        child_pid = _spawn_detached([kilix] + desktop_arguments(profile))
        time.sleep(0.3)
        status = _child_status(child_pid)
        if status is not None and status != 0:
            raise ProfileError("the %s provider did not start"
                               % profile["desktop"])
        print("laptop %s: opened (desktop profile, not tracked)"
              % profile_id)
        return 0
    existing = session_pid(profile_id)
    if existing is not None:
        print("laptop %s: already running (pid %d)" % (profile_id,
                                                       existing))
        return 0
    directory = _session_file_directory()
    _ensure_directory(directory)
    session_path = os.path.join(directory,
                                "laptop-%s.session" % profile_id)
    _write_private(session_path, session_text(profile))
    child_pid = _spawn_detached([kilix, "--session", session_path])
    try:
        record_session(profile_id, child_pid)
    except (ProfileError, OSError):
        # This is still our unreaped direct child, so its PID cannot be reused.
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(child_pid, 0)
        raise
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        if _child_status(child_pid) is not None:
            clear_session(profile_id)
            raise ProfileError("the session exited immediately")
        time.sleep(0.05)
    print("laptop %s: opened (pid %d)" % (profile_id, child_pid))
    return 0


def cmd_close(profile_id: str) -> int:
    if not valid_id(profile_id):
        raise ProfileError("That profile name is not valid.")
    record = _session_record(profile_id)
    if record is None or not _verified_record(profile_id, record):
        profile_path = os.path.join(profiles_directory(),
                                    profile_id + PROFILE_SUFFIX)
        if not os.path.isfile(profile_path):
            raise ProfileError("no such profile")
        print("laptop %s: not running" % profile_id)
        return 0
    pid = record[0]
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise ProfileError("safe session close requires Linux process descriptors")
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        clear_session(profile_id)
        print("laptop %s: closed" % profile_id)
        return 0
    except OSError as error:
        raise ProfileError("cannot safely open session process %d" % pid) from error
    try:
        # A PID could have been reused between the first check and pidfd_open.
        if not _verified_record(profile_id, record):
            print("laptop %s: not running" % profile_id)
            return 0
        try:
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as error:
            raise ProfileError("cannot signal session process %d" % pid) from error
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        if poller.poll(5000):
            try:
                os.waitpid(pid, os.WNOHANG)  # reap if it was our child
            except (ChildProcessError, OSError):
                pass
            clear_session(profile_id)
            print("laptop %s: closed" % profile_id)
            return 0
    finally:
        os.close(descriptor)
    print("laptop %s: still shutting down (pid %d)" % (profile_id, pid),
          file=sys.stderr)
    return 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0] if argv else "status"
    rest = argv[1:]
    try:
        if action in ("-h", "--help", "help"):
            print(USAGE)
            return 0
        if action == "list":
            if rest:
                print(USAGE, file=sys.stderr)
                return 2
            return cmd_list()
        if action == "status":
            if rest:
                print(USAGE, file=sys.stderr)
                return 2
            return cmd_status()
        if action in ("open", "close"):
            if len(rest) != 1:
                print(USAGE, file=sys.stderr)
                return 2
            return cmd_open(rest[0]) if action == "open" \
                else cmd_close(rest[0])
    except ProfileError as error:
        print("kilix laptop: %s" % error, file=sys.stderr)
        return 1
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
