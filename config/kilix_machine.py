"""Direct Ask answers about this computer: what it is and what is on it.

These are closed reads of /proc, /sys, and a few listing commands. A request
that is not entirely one of them returns 3 so Ask can keep its normal route.
Nothing here starts, stops, installs, mounts, connects, or deletes anything.
Process answers name the program only, never its arguments. Folder answers
cover this folder, home, the named user folders such as Downloads, and
typed paths under home (~/...) without ".."; any other typed path is left to
the Needle files job and its own rules. A program
this computer does not have is left to Needle as well.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kilix_asktext import clean  # noqa: E402

NOT_MACHINE = 3
DU_SECONDS = 10.0
_WHAT = r"(?:what(?:'s| is| are)|which is|show(?: me)?|tell me|give me|check|list)"
_PC = r"(?:computer|machine|system|pc|laptop|box)"
_NAME = r"[a-z0-9][a-z0-9_.@+-]{0,39}"
_NOT_A_PROGRAM = {
    "it", "this", "that", "there", "anything", "something", "everything", "nothing",
    "he", "she", "anyone", "someone", "what", "who", "the", "a", "an", "my", "kilix",
    "time", "music", "audio", "sound", "wifi", "network", "internet", "battery",
}


class MachineError(Exception):
    """A recognized question that could not be read."""


def _run(argv: list[str], timeout: float = 6.0) -> str:
    """Output of a listing command, or a MachineError naming what is missing."""
    program = shutil.which(argv[0])
    if program is None:
        raise MachineError(f"{argv[0]} is not installed")
    try:
        result = subprocess.run(
            [program, *argv[1:]], check=False, capture_output=True, text=True,
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as error:
        raise MachineError(f"{argv[0]} took too long") from error
    except OSError as error:
        raise MachineError(str(error)) from error
    if result.returncode != 0 and not result.stdout.strip():
        detail = (result.stderr.strip().splitlines() or ["query failed"])[-1]
        raise MachineError(f"{argv[0]}: {detail[:200]}")
    return result.stdout


def _try(argv: list[str], timeout: float = 6.0) -> str:
    try:
        return _run(argv, timeout)
    except MachineError:
        return ""


def _size(nbytes: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(nbytes) < 1024 or unit == "TiB":
            return f"{nbytes:.0f} {unit}" if unit == "B" or nbytes >= 100 else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TiB"


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise MachineError(f"cannot read {path}") from error


# --- identity ---------------------------------------------------------------

def _hostname() -> str:
    return socket.gethostname()


def _user() -> str:
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def _kernel() -> str:
    name = os.uname()
    return f"{name.sysname} {name.release} ({name.machine})"


def _os() -> str:
    pretty = ""
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    pretty = line.split("=", 1)[1].strip().strip('"')
            break
        except OSError:
            continue
    return f"{pretty or 'Linux'}, kernel {os.uname().release}"


def _cpu_model() -> str:
    model = ""
    cores: set[tuple[str, str]] = set()
    physical = core = ""
    for line in _read("/proc/cpuinfo").splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "model name" and not model:
            model = " ".join(value.split())
        elif key == "physical id":
            physical = value
        elif key == "core id":
            core = value
        elif not key and (physical or core):
            cores.add((physical, core))
            physical = core = ""
    if physical or core:
        cores.add((physical, core))
    threads = os.cpu_count() or 0
    count = f"{len(cores)} cores, {threads} threads" if cores and len(cores) != threads \
        else f"{threads} cores"
    return f"{model or os.uname().machine}, {count}"


def _ram_total() -> str:
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            return f"{_size(int(line.split()[1]) * 1024)} of memory installed"
    raise MachineError("memory observation is incomplete")


def _gpus() -> list[dict[str, str]]:
    if shutil.which("nvidia-smi") is None:
        return []
    out = _try(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,temperature.gpu,utilization.gpu",
                "--format=csv,noheader,nounits"])
    found = []
    for line in out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 5:
            found.append(dict(zip(("name", "total", "used", "temp", "util"), parts)))
    return found


def _gpu() -> str:
    gpus = _gpus()
    if gpus:
        return "\n".join(
            f"{gpu['name']}, {_mib(gpu['total'])} of video memory" for gpu in gpus)
    names = []
    for line in _try(["lspci"]).splitlines():
        if re.search(r"VGA compatible controller|3D controller|Display controller", line):
            names.append(line.split(": ", 1)[-1].strip())
    if not names:
        raise MachineError("no graphics card was found")
    return "\n".join(names)


def _mib(value: str) -> str:
    try:
        return _size(float(value) * 1024 * 1024)
    except ValueError:
        return "unknown"


def _vram() -> str:
    gpus = _gpus()
    if not gpus:
        raise MachineError("video memory can be read on NVIDIA cards only")
    lines = []
    for gpu in gpus:
        try:
            free = float(gpu["total"]) - float(gpu["used"])
        except ValueError:
            continue
        lines.append(f"{_mib(str(free))} free of {_mib(gpu['total'])} on {gpu['name']}")
    if not lines:
        raise MachineError("video memory observation is incomplete")
    return "\n".join(lines)


def _gpu_load() -> str:
    gpus = _gpus()
    if not gpus:
        raise MachineError("graphics load can be read on NVIDIA cards only")
    return "\n".join(f"{gpu['util']}% busy, {gpu['temp']}°C on {gpu['name']}" for gpu in gpus)


# --- processes --------------------------------------------------------------

def _top(key: str) -> str:
    column = "%mem" if key == "memory" else "%cpu"
    out = _run(["ps", "-eo", f"comm:32,pid,{column},rss", f"--sort=-{'rss' if key == 'memory' else '%cpu'}",
                "--no-headers"])
    rows = []
    for line in out.splitlines()[:5]:
        parts = line.split()
        if len(parts) < 4 or parts[-3] == str(os.getpid()):
            continue
        name, pid, share, rss = " ".join(parts[:-3]), parts[-3], parts[-2], parts[-1]
        if name == "ps":
            continue
        if key == "memory":
            rows.append(f"{name} (pid {pid}): {_size(int(rss) * 1024)}, {share}%")
        else:
            rows.append(f"{name} (pid {pid}): {share}% of one core")
    if not rows:
        raise MachineError("no processes were listed")
    return "\n".join(rows)


def _process_count() -> str:
    count = sum(1 for entry in os.scandir("/proc") if entry.name.isdigit())
    return f"{count} processes"


def _running(name: str) -> str:
    name = name.lower()
    count = 0
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            comm = Path(entry.path, "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if comm.lower() == name[:15] or comm.lower() == name:
            count += 1
    unit = ""
    if shutil.which("systemctl"):
        for scope in ([], ["--user"]):
            state = _try(["systemctl", *scope, "is-active", f"{name}.service"], 3).strip()
            if state == "active":
                unit = f"{name}.service is active" + (" for this user" if scope else "")
                break
    if count and unit:
        return f"yes: {unit}, {count} process" + ("" if count == 1 else "es")
    if unit:
        return f"yes: {unit}"
    if count:
        return f"yes: {count} {name} process" + ("" if count == 1 else "es")
    known = shutil.which(name) is not None
    if not known and shutil.which("systemctl"):
        for scope in ([], ["--user"]):
            listed = _try(["systemctl", *scope, "list-unit-files", "--no-legend", f"{name}.service"], 3)
            known = known or bool(listed.strip())
    if not known:
        return ""
    return f"no: {name} is installed but not running"


def _listening(port: str | None) -> str:
    out = _run(["ss", "-H", "-ltnu"])
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto, local = parts[0], parts[4]
        address, _, number = local.rpartition(":")
        if port is not None and number != port:
            continue
        rows.append((int(number) if number.isdigit() else 0, proto, address.strip("[]") or "*"))
    if port is not None:
        if not rows:
            return f"nothing is listening on port {port}"
        names = _try(["ss", "-H", "-ltnup", f"sport = :{port}"], 4)
        owners = sorted(set(re.findall(r'users:\(\("([^"]+)"', names)))
        where = ", ".join(sorted({f"{proto} {address}" for _n, proto, address in rows}))
        return f"port {port}: {', '.join(owners) if owners else 'a process of another user'} ({where})"
    if not rows:
        return "nothing is listening"
    merged: dict[tuple[int, str], set[str]] = {}
    for number, proto, address in rows:
        merged.setdefault((number, proto), set()).add(address)
    lines = [f"{proto} {number} on {', '.join(sorted(addresses))}"
             for (number, proto), addresses in sorted(merged.items())]
    extra = len(lines) - 30
    return "\n".join(lines[:30] + ([f"and {extra} more"] if extra > 0 else []))


# --- disks, screen, devices ---------------------------------------------------

def _last_boot() -> str:
    seconds = float(_read("/proc/uptime").split()[0])
    booted = datetime.fromtimestamp(time.time() - seconds).astimezone()
    days, rest = divmod(int(seconds), 86400)
    hours, minutes = rest // 3600, rest % 3600 // 60
    span = (f"{days}d " if days else "") + f"{hours}h {minutes:02d}m"
    return f"{booted.strftime('%A, %Y-%m-%d %H:%M')}, {span} ago"


def _mounts() -> str:
    out = _run(["df", "-h", "--output=target,size,avail,pcent", "-x", "tmpfs", "-x", "devtmpfs",
                "-x", "efivarfs", "-x", "squashfs", "-x", "overlay"])
    lines = out.strip().splitlines()[1:]
    if not lines:
        raise MachineError("no disks were listed")
    rows = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 4:
            rows.append(f"{' '.join(parts[:-3])}: {parts[-2]} free of {parts[-3]} ({parts[-1]} used)")
    return "\n".join(rows)


def _fullest() -> str:
    out = _run(["df", "--output=target,pcent,avail", "--block-size=1", "-x", "tmpfs", "-x", "devtmpfs",
                "-x", "efivarfs", "-x", "squashfs", "-x", "overlay"])
    best = None
    for line in out.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3 or not parts[-2].rstrip("%").isdigit():
            continue
        used = int(parts[-2].rstrip("%"))
        if best is None or used > best[0]:
            best = (used, " ".join(parts[:-2]), int(parts[-1]))
    if best is None:
        raise MachineError("no disks were listed")
    return f"{best[1]} is {best[0]}% full, {_size(best[2])} free"


def _resolution() -> str:
    found = []
    if os.environ.get("DISPLAY") and shutil.which("xrandr"):
        for line in _try(["xrandr", "--current"]).splitlines():
            match = re.match(r"(\S+) connected(?: primary)? (\d+)x(\d+)\+", line)
            if match:
                found.append(f"{match.group(2)}x{match.group(3)} on {match.group(1)}")
    if not found:
        for status in sorted(Path("/sys/class/drm").glob("card*-*/status")):
            try:
                if status.read_text().strip() != "connected":
                    continue
                modes = (status.parent / "modes").read_text().split()
            except OSError:
                continue
            if modes:
                found.append(f"{modes[0]} on {status.parent.name.split('-', 1)[1]}")
    if not found:
        raise MachineError("no connected display was found")
    return "\n".join(found)


def _usb() -> str:
    names = []
    for line in _run(["lsusb"]).splitlines():
        match = re.match(r"Bus \d+ Device \d+: ID [0-9a-f:]+ ?(.*)", line)
        name = (match.group(1) if match else "").strip()
        if name and "root hub" not in name:
            names.append(name)
    return "\n".join(names) if names else "no USB devices besides the hubs"


def _plugged() -> str:
    seen = False
    for supply in sorted(Path("/sys/class/power_supply").glob("*")):
        try:
            if (supply / "type").read_text().strip() != "Mains":
                continue
            seen = True
            if (supply / "online").read_text().strip() == "1":
                return "yes, on wall power"
        except OSError:
            continue
    if seen:
        return "no, on battery"
    if not list(Path("/sys/class/power_supply").glob("BAT*")):
        return "yes, this computer has no battery"
    raise MachineError("no power adapter reading")


def _bluetooth() -> str:
    found = False
    for kind in sorted(Path("/sys/class/rfkill").glob("rfkill*/type")):
        try:
            if kind.read_text().strip() != "bluetooth":
                continue
            found = True
            soft = (kind.parent / "soft").read_text().strip()
            hard = (kind.parent / "hard").read_text().strip()
        except OSError:
            continue
        if soft == "0" and hard == "0":
            return "bluetooth is on"
    return "bluetooth is off" if found else "this computer has no bluetooth radio"


def _mic_muted() -> str:
    if shutil.which("wpctl"):
        out = _try(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"], 4)
        if out.strip():
            return "the microphone is muted" if "MUTED" in out else "the microphone is not muted"
    out = _run(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"], 4)
    return "the microphone is muted" if "yes" in out.lower() else "the microphone is not muted"


# --- network ----------------------------------------------------------------

def _interfaces() -> list[str]:
    return sorted(entry.name for entry in os.scandir("/sys/class/net") if entry.name != "lo")


def _mac() -> str:
    lines = []
    for name in _interfaces():
        base = Path("/sys/class/net", name)
        if not (base / "device").exists():
            continue
        try:
            address = (base / "address").read_text().strip()
        except OSError:
            continue
        if address and address != "00:00:00:00:00:00":
            lines.append(f"{name}: {address}")
    if not lines:
        raise MachineError("no network card was found")
    return "\n".join(lines)


def _dns() -> str:
    servers: list[str] = []
    if shutil.which("resolvectl"):
        for line in _try(["resolvectl", "dns"], 4).splitlines():
            servers += [part for part in line.split(":", 1)[-1].split() if part]
    if not servers:
        for line in _read("/etc/resolv.conf").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "nameserver":
                servers.append(parts[1])
    unique = list(dict.fromkeys(servers))
    if not unique:
        raise MachineError("no DNS server is configured")
    return ", ".join(unique)


def _vpn() -> str:
    tunnels = []
    for name in _interfaces():
        kind = ""
        try:
            for line in Path("/sys/class/net", name, "uevent").read_text().splitlines():
                if line.startswith("DEVTYPE="):
                    kind = line.split("=", 1)[1]
        except OSError:
            pass
        if kind in ("wireguard", "vpn", "ppp") or re.match(r"(?:tun|tap|wg|ppp|tailscale|nordlynx|proton)\d*", name):
            try:
                state = Path("/sys/class/net", name, "operstate").read_text().strip()
            except OSError:
                state = "unknown"
            if state != "down":
                tunnels.append(name)
    return f"yes, a tunnel is up: {', '.join(tunnels)}" if tunnels else "no VPN tunnel is up"


def _ssid() -> str:
    out = _run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi"], 8)
    for line in out.splitlines():
        parts = line.replace("\\:", "\0").split(":")
        if len(parts) >= 3 and parts[0] == "yes":
            return f"{parts[1].replace(chr(0), ':')} ({parts[2]}% signal)"
    return "not connected to wifi"


def _gateway() -> str:
    out = _run(["ip", "-4", "route", "show", "default"], 4)
    match = re.search(r"default via (\S+) dev (\S+)", out)
    if match is None:
        raise MachineError("no default route")
    return f"{match.group(1)} on {match.group(2)}"


# --- software ---------------------------------------------------------------

def _upgrades() -> str:
    out = _run(["apt", "list", "--upgradable"], 25)
    count = sum(1 for line in out.splitlines() if "/" in line and "[upgradable" in line)
    stamp = ""
    for path in ("/var/lib/apt/periodic/update-success-stamp", "/var/cache/apt/pkgcache.bin"):
        try:
            when = datetime.fromtimestamp(os.stat(path).st_mtime)
            stamp = f", as of the package list from {when.strftime('%Y-%m-%d %H:%M')}"
            break
        except OSError:
            continue
    if count == 0:
        return f"no package updates{stamp}"
    return f"{count} package" + ("" if count == 1 else "s") + f" can be upgraded{stamp}"


def _browser() -> str:
    out = _run(["xdg-settings", "get", "default-web-browser"], 5).strip()
    if not out:
        raise MachineError("no default browser is set")
    return out.removesuffix(".desktop")


def _shell() -> str:
    import pwd
    login = pwd.getpwuid(os.getuid()).pw_shell
    current = os.environ.get("SHELL", "")
    if current and current != login:
        return f"{current} (login shell {login})"
    return login or current or "unknown"


def _package_count() -> str:
    count = 0
    status = Path("/var/lib/dpkg/status")
    try:
        with status.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if line.startswith("Status: install ok installed"):
                    count += 1
    except OSError as error:
        raise MachineError("the package database cannot be read") from error
    return f"{count} packages installed"


# --- files ------------------------------------------------------------------

_XDG = {
    "downloads": "DOWNLOAD", "download": "DOWNLOAD", "documents": "DOCUMENTS",
    "docs": "DOCUMENTS", "pictures": "PICTURES", "photos": "PICTURES", "music": "MUSIC",
    "videos": "VIDEOS", "desktop": "DESKTOP", "templates": "TEMPLATES",
    "public": "PUBLICSHARE",
}
_FOLDER = (rf"(?P<here>this (?:folder|directory|dir)|the current (?:folder|directory|dir)|here)"
           rf"|(?:my |the )?(?P<xdg>{'|'.join(_XDG)})(?: folder| directory| dir)?"
           rf"|(?:my |the )?(?P<home>home)(?: folder| directory| dir)?"
           # A typed path only under home, with no "..": anything else is the files job's.
           rf"|(?P<tilde>~(?P<under>(?:/(?!\.\.?(?:/|\s|$))[\w.+-]+(?: [\w.+-]+)*?)*)/?)")


def _folder(match: re.Match) -> Path:
    if match["here"]:
        return Path.cwd()
    if match["home"]:
        return Path.home()
    if match["tilde"]:
        under = (match["under"] or "").strip("/")
        if not under:
            return Path.home()
        # The rule is on what was typed: a link the user made in home is theirs.
        return Path.home() / under
    if match["xdg"]:
        key = _XDG[match["xdg"].lower()]
        found = _try(["xdg-user-dir", key], 3).strip()
        if found and Path(found) != Path.home():
            return Path(found)
        guess = Path.home() / {"DOWNLOAD": "Downloads", "PUBLICSHARE": "Public"}.get(key, key.capitalize())
        return guess
    raise MachineError("name this folder, home, or one of your own folders")


def _must_be_dir(path: Path) -> Path:
    if not path.is_dir():
        raise MachineError(f"{path} is not a folder")
    return path


def _count(path: Path) -> str:
    files = folders = 0
    try:
        for entry in os.scandir(_must_be_dir(path)):
            if entry.is_dir(follow_symlinks=False):
                folders += 1
            else:
                files += 1
    except PermissionError as error:
        raise MachineError(f"{path} cannot be read") from error
    return (f"{files} file" + ("" if files == 1 else "s") + f" and {folders} folder"
            + ("" if folders == 1 else "s") + f" in {path}")


def _listing(path: Path) -> str:
    try:
        entries = sorted(os.scandir(_must_be_dir(path)), key=lambda entry: entry.name.lower())
    except PermissionError as error:
        raise MachineError(f"{path} cannot be read") from error
    names = [entry.name + ("/" if entry.is_dir(follow_symlinks=False) else "")
             for entry in entries if not entry.name.startswith(".")]
    if not names:
        return f"{path} is empty"
    extra = len(names) - 60
    return "\n".join(names[:60] + ([f"and {extra} more"] if extra > 0 else []))


def _du(path: Path) -> str:
    if not path.exists():
        raise MachineError(f"{path} does not exist")
    try:
        out = _run(["du", "-sx", "--block-size=1", "--", str(path)], DU_SECONDS)
    except MachineError as error:
        if "took too long" not in str(error):
            raise
        usage = shutil.disk_usage(path)
        return (f"{path} is too big to size within {DU_SECONDS:.0f} seconds. The disk it is on has "
                f"{_size(usage.used)} used of {_size(usage.total)}.\nFor the exact size run: du -sh {path}")
    try:
        return f"{_size(int(out.split()[0]))} in {path}"
    except (IndexError, ValueError) as error:
        raise MachineError("the size could not be read") from error


def _edge(path: Path, kind: str) -> str:
    best = None
    try:
        for entry in os.scandir(_must_be_dir(path)):
            if entry.name.startswith("."):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if kind in ("largest", "biggest") and not entry.is_file(follow_symlinks=False):
                continue
            value = info.st_size if kind in ("largest", "biggest") else info.st_mtime
            if best is None or (value < best[0] if kind == "oldest" else value > best[0]):
                best = (value, entry.name)
    except PermissionError as error:
        raise MachineError(f"{path} cannot be read") from error
    if best is None:
        return f"{path} is empty"
    if kind in ("largest", "biggest"):
        return f"{best[1]} ({_size(best[0])}) in {path}"
    stamp = datetime.fromtimestamp(best[0]).strftime("%Y-%m-%d %H:%M")
    return f"{best[1]} ({stamp}) in {path}"


# --- classification -----------------------------------------------------------

def answer(text: str) -> tuple[int, str]:
    """Return a status and the text to print. Status 3 is not a machine question."""
    text = clean(text)
    if not text:
        return NOT_MACHINE, ""
    low = text.lower()

    def hit(pattern: str) -> re.Match | None:
        return re.fullmatch(pattern, text, re.IGNORECASE)

    if hit(rf"(?:{_WHAT} )?(?:my|the|this {_PC}'s|this) (?:hostname|host name|{_PC} name|{_PC}'s name)"
           rf"|hostname|what is this {_PC} called"):
        return 0, _hostname()
    if hit(rf"who am i|whoami|(?:{_WHAT} )?my (?:user ?name|login|user)|what user am i(?: logged in as)?"
           r"|who am i logged in as"):
        return 0, _user()
    if hit(rf"(?:{_WHAT} )?(?:the |my )?kernel(?: version)?|what kernel am i (?:running|on|using)"
           r"|what kernel is this|what version of linux(?: is this| am i running)?"):
        return 0, _kernel()
    if hit(rf"(?:{_WHAT} )?(?:the |my )?(?:os|operating system|distro|distribution)(?: version)?"
           r"|what (?:os|operating system|distro|distribution|linux) (?:is this|am i (?:running|on|using))"
           r"|what version of debian(?: is this| am i running)?"):
        return 0, _os()
    if hit(rf"(?:{_WHAT} )?(?:the |my )?(?:cpu|processor)(?: model| type| name)?"
           r"|what (?:cpu|processor) (?:do i have|is this|am i (?:running|using)|is in this "
           rf"{_PC})|what kind of (?:cpu|processor) (?:do i have|is this)"):
        return 0, _cpu_model()
    if hit(r"how many (?:cpu )?(?:cores|cpus|threads|processors)(?: do i have| does this "
           rf"{_PC} have| are there)?|(?:{_WHAT} )?(?:the |my )?(?:core|cpu|thread) count"):
        return 0, _cpu_model().split(", ", 1)[1]
    if hit(rf"how much (?:ram|memory) (?:do i have|is installed|does this {_PC} have|is there)"
           rf"|(?:{_WHAT} )?(?:the |my )?total (?:ram|memory)|how much (?:ram|memory)"):
        return 0, _ram_total()
    if hit(rf"(?:{_WHAT} )?(?:the |my )?(?:gpu|graphics card|video card)(?: model| name)?"
           r"|what (?:gpu|graphics card|video card|graphics) (?:do i have|is this|am i using)"
           r"|do i have a (?:gpu|graphics card)"):
        return 0, _gpu()
    if hit(rf"how much (?:vram|video memory|gpu memory) (?:is (?:free|available|left|used)|do i have)"
           rf"|(?:{_WHAT} )?(?:the |my )?(?:vram|video memory|gpu memory)(?: usage| free)?"):
        return 0, _vram()
    if hit(rf"(?:{_WHAT} )?(?:the |my )?gpu (?:load|usage|utilization|temperature|temp)"
           r"|how (?:busy|hot) is (?:the |my )?gpu|is the gpu busy"):
        return 0, _gpu_load()

    top = hit(rf"(?:{_WHAT}|what(?:'s| is)|which (?:process|program|app) is) (?:using|eating|taking|hogging)"
              r"(?: up)?(?: all)? (?:the |my )?most (?P<key>memory|ram|cpu)"
              r"|(?:what|which) (?:process|program|app)(?:es|s)? (?:is|are) (?:using|eating|taking|hogging)"
              r"(?: up)?(?: all)?(?: the| my)?(?: most)? (?P<key2>memory|ram|cpu)"
              r"|what(?:'s| is) (?:using|eating|taking|hogging)(?: up)?(?: all)? (?:the |my )?(?P<key3>memory|ram|cpu)"
              rf"|(?:{_WHAT} )?(?:the )?top (?:(?P<key4>memory|ram|cpu) )?processes"
              r"|why is (?:my |the |this )?(?:computer|machine|system|pc|laptop) (?:so )?slow")
    if top:
        key = top["key"] or top["key2"] or top["key3"] or top["key4"] or "cpu"
        if "slow" in low:
            return 0, "busiest by CPU:\n" + _top("cpu") + "\nlargest by memory:\n" + _top("memory")
        return 0, _top("memory" if key.lower() in ("memory", "ram") else "cpu")
    if hit(rf"how many processes(?: are)?(?: running| there)?|(?:{_WHAT} )?(?:the )?process count"):
        return 0, _process_count()
    if hit(rf"what(?:'s| is| are)? ?(?:the )?processes(?: are)? running|(?:{_WHAT} )?(?:the )?running processes"
           r"|what is running"):
        return 0, _process_count() + "\nbusiest by CPU:\n" + _top("cpu")

    port = hit(r"(?:what|who)(?:'s| is) (?:listening|running) on port (?P<port>\d{1,5})"
               r"|(?:what|which program|which process)(?:'s| is)? (?:is )?using port (?P<port2>\d{1,5})"
               r"|is (?:anything|something) (?:listening|running) on port (?P<port3>\d{1,5})"
               r"|is port (?P<port4>\d{1,5}) (?:open|in use|used|free|taken|listening)")
    if port:
        number = port["port"] or port["port2"] or port["port3"] or port["port4"]
        if not 1 <= int(number) <= 65535:
            raise MachineError("a port is between 1 and 65535")
        return 0, _listening(number)
    if hit(rf"(?:what|which) ports are (?:open|listening|in use)|(?:{_WHAT} )?(?:the )?(?:open|listening) ports"):
        return 0, _listening(None)

    if hit(rf"when did (?:i|the {_PC}|this {_PC}|it) (?:last )?(?:re)?(?:boot|start)(?: up)?(?: last)?"
           rf"|when was (?:the )?last (?:re)?boot|(?:{_WHAT} )?(?:the )?(?:last )?boot time"):
        return 0, _last_boot()
    if hit(rf"(?:what|which) (?:disks|drives|filesystems|partitions) (?:are|do i have)(?: mounted| there| attached)?"
           rf"|(?:{_WHAT} )?(?:the |my |all )?(?:mounted )?(?:disks|drives|filesystems|mounts)"
           r"|how full are (?:my |the )?(?:disks|drives)"):
        return 0, _mounts()
    if hit(r"(?:what|which) (?:disk|drive|filesystem|partition) is (?:the )?(?:full|fullest|almost full|most full)"
           r"|is (?:my |the |any )?(?:disk|drive) (?:almost )?full"):
        return 0, _fullest()
    if hit(rf"(?:{_WHAT} )?(?:my |the )?(?:screen|display|monitor) (?:resolution|size)"
           r"|what resolution (?:is (?:my |the |this )?(?:screen|display|monitor)|am i (?:running|using|on))"
           rf"|(?:{_WHAT} )?(?:my |the )?resolution"):
        return 0, _resolution()
    if hit(rf"(?:what|which) usb devices (?:are )?(?:connected|plugged in|attached)"
           rf"|(?:{_WHAT} )?(?:the |my )?usb devices|what(?:'s| is) plugged in(?:to usb)?"):
        return 0, _usb()
    if hit(rf"(?:is|am) (?:the |my |this |i )?(?:{_PC} |charger |power |ac )?(?:plugged in|on (?:ac|wall) power|on battery)"
           r"|is the (?:charger|power adapter|power cord) (?:plugged in|connected)"):
        return 0, _plugged()
    if hit(r"is (?:the |my )?bluetooth (?:on|off|enabled|disabled|up)|(?:check|show) (?:the )?bluetooth"):
        return 0, _bluetooth()
    if hit(r"is (?:the |my )?(?:microphone|mic) muted|am i muted"):
        return 0, _mic_muted()

    if hit(rf"(?:{_WHAT} )?(?:my|the) mac(?: address(?:es)?)?"):
        return 0, _mac()
    if hit(rf"(?:what|which) dns(?: server)?s? (?:am i (?:using|on)|is (?:set|configured|this))"
           rf"|(?:{_WHAT} )?(?:my|the) dns(?: server)?s?"):
        return 0, _dns()
    if hit(r"am i (?:on|using|connected to|behind) (?:a |the )?vpn|is (?:the |my |a )?vpn (?:on|up|connected|active|running)"):
        return 0, _vpn()
    if hit(r"(?:what|which) (?:wifi|wi-fi)(?: network)? (?:am i (?:on|connected to|using)|is this)"
           rf"|(?:{_WHAT} )?(?:my|the) (?:wifi|wi-fi) (?:network|name|ssid)|what(?:'s| is) (?:my |the )?ssid"
           r"|how (?:strong|good) is (?:my |the )?(?:wifi|wi-fi)(?: signal)?"):
        return 0, _ssid()
    if hit(rf"(?:{_WHAT} )?(?:my|the) (?:default )?(?:gateway|router)(?: address| ip)?"):
        return 0, _gateway()

    if hit(r"(?:are there|do i have)(?: any)? (?:updates|upgrades)(?: available| pending| to install)?"
           r"|(?:are|is) (?:there )?(?:any )?(?:updates|upgrades) available|how many (?:updates|upgrades)"
           r"(?: are)?(?: available| pending)?|is (?:the |my |this )?(?:system|computer|machine) up to date"
           r"|check for updates"):
        return 0, _upgrades()
    if hit(rf"(?:{_WHAT} )?(?:my|the) default (?:web )?browser|what browser (?:do i use|is (?:the )?default)"):
        return 0, _browser()
    if hit(rf"(?:what|which) shell (?:am i (?:using|in|running)|is this)|(?:{_WHAT} )?(?:my|the) (?:login )?shell"):
        return 0, _shell()
    if hit(r"how many packages(?: are| do i have)?(?: installed)?"):
        return 0, _package_count()

    if hit(r"(?:what|which) (?:directory|folder|dir) am i in|where am i|pwd"
           rf"|(?:{_WHAT} )?(?:the )?(?:current|working) (?:directory|folder|dir)"):
        return 0, str(Path.cwd())
    where = hit(rf"where (?:is|are) (?:my |the )?(?P<xdg>{'|'.join(_XDG)})(?: folder| directory| dir)?"
                r"(?P<here>)(?P<home>)(?P<tilde>)"
                r"|where(?:'s| is) (?:my |the )?(?P<home2>home)(?: folder| directory| dir)?")
    if where:
        if where["home2"]:
            return 0, str(Path.home())
        return 0, str(_folder(where))
    listing = hit(rf"(?:what(?:'s| is)|what files are|{_WHAT}(?: the)?(?: files)?) in (?:{_FOLDER})"
                  rf"|(?:list|show)(?: me)?(?: the)? files(?: in (?:{_FOLDER.replace('P<', 'P<b_')}))?")
    if listing:
        groups = listing.groupdict()
        if any(groups.get(f"b_{name}") for name in ("here", "xdg", "home", "tilde")):
            return 0, _listing(_folder(_Alias(listing, "b_")))
        if any(groups.get(name) for name in ("here", "xdg", "home", "tilde")):
            return 0, _listing(_folder(listing))
        return 0, _listing(Path.cwd())
    counted = hit(rf"how many (?:files|things|items)(?: are)?(?: there)? in (?:{_FOLDER})")
    if counted:
        return 0, _count(_folder(counted))
    sized = hit(rf"how (?:big|large) is (?:{_FOLDER})|how much (?:space|disk)(?: space)? (?:does|is) "
                rf"(?:{_FOLDER.replace('P<', 'P<b_')}) (?:use|using|take|taking)(?: up)?"
                rf"|(?:{_WHAT} )?the size of (?:{_FOLDER.replace('P<', 'P<c_')})")
    if sized:
        groups = sized.groupdict()
        for prefix in ("", "b_", "c_"):
            if any(groups.get(prefix + name) for name in ("here", "xdg", "home", "tilde")):
                return 0, _du(_folder(_Alias(sized, prefix)))
    edge = hit(rf"(?:{_WHAT}|what(?:'s| is)) the (?P<kind>newest|latest|most recent|oldest|largest|biggest)"
               rf" (?:file|download|thing|item)(?: in (?:{_FOLDER}))?")
    if edge:
        groups = edge.groupdict()
        has = any(groups.get(name) for name in ("here", "xdg", "home", "tilde"))
        folder = _folder(edge) if has else (
            _folder(re.fullmatch(_FOLDER, "downloads")) if "download" in low else Path.cwd())
        kind = edge["kind"].lower()
        kind = {"latest": "newest", "most recent": "newest"}.get(kind, kind)
        return 0, _edge(folder, kind)

    running = hit(rf"is (?:the )?(?P<name>{_NAME})(?: service| daemon| server| process)? (?:running|active|up|alive|started)"
                  rf"|is there (?:a|an) (?P<name2>{_NAME}) (?:process|service|daemon|server) running")
    if running:
        name = (running["name"] or running["name2"]).lower().removesuffix(".service")
        if name not in _NOT_A_PROGRAM:
            found = _running(name)
            if found:
                return 0, found
    return NOT_MACHINE, ""


class _Alias:
    """Read a match's groups under a prefix, so one folder pattern can appear twice."""

    def __init__(self, match: re.Match, prefix: str):
        self._match = match
        self._prefix = prefix

    def __getitem__(self, name: str):
        return self._match[self._prefix + name]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["--ask"]:
        print("usage: kilix_machine.py --ask TEXT", file=sys.stderr)
        return 2
    try:
        status, text = answer(" ".join(argv[1:]))
    except MachineError as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    if text:
        print(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
