"""Closed Ask answers for this machine.

A request that is not entirely one of them returns 3 so Ask can keep its
normal route. This does not open a panel, connect or disconnect a network,
start the player, or start the voice daemon. Session launch names one
provider and returns a receipt. It does not attach, detach, or stop a session.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

NOT_LOCAL = 3
_PREFIX = re.compile(r"^(?:please\s+)?", re.IGNORECASE)
_TRAIL = re.compile(r"[?!.]+$")
_BRIGHT_SET = re.compile(
    r"(?:set |make |turn )?(?:the )?(?:screen )?brightness"
    r" (?:to |at )?(?P<level>\d{1,3})(?:%| percent)?",
    re.IGNORECASE,
)
_BRIGHT_CHECK = re.compile(
    r"(?:show |check )?(?:the )?(?:screen )?brightness"
    r"|how bright is (?:the |my )?(?:screen|display)"
    r"|what(?:'s| is) the (?:screen )?brightness",
    re.IGNORECASE,
)
_NETWORK = re.compile(
    r"(?:what|which) network am i on"
    r"|what(?:'s| is) my network"
    r"|what network is this"
    r"|(?:show |check )(?:the )?network",
    re.IGNORECASE,
)
_ONLINE = re.compile(
    r"am i (?:online|connected)"
    r"|is the (?:network|wifi|wi-fi) (?:up|connected|online)",
    re.IGNORECASE,
)
_PLAYING = re.compile(
    r"what(?:'s| is) playing"
    r"|what(?:'s| is) (?:the )?(?:current )?(?:song|track)"
    r"|now playing"
    r"|(?:show |check )(?:what's |what is )?playing",
    re.IGNORECASE,
)
_VOICE = re.compile(
    r"is the (?:microphone|mic) (?:on|off|listening)"
    r"|(?:show |check )(?:the )?(?:microphone|mic)"
    r"|am i listening",
    re.IGNORECASE,
)
_BATTERY = re.compile(
    r"(?:show |check )?(?:the |my )?battery(?: status| level| percent)?"
    r"|how much battery(?: is left| do i have)?"
    r"|is the battery (?:charging|charged|full)"
    r"|what(?:'s| is) the battery(?: status| level| percent)?",
    re.IGNORECASE,
)
_UPTIME = re.compile(
    r"(?:show |check )?(?:the |my )?uptime"
    r"|what(?:'s| is) the uptime"
    r"|how long has (?:the )?(?:computer|machine|system) been (?:up|on|running)",
    re.IGNORECASE,
)
_PACKAGE = re.compile(
    r"is (?:the )?(?:package )?(?P<name>[a-z0-9][a-z0-9+.-]{0,63}) (?:package )?installed",
    re.IGNORECASE,
)
_ADDRESS = re.compile(
    r"what(?:'s| is) my (?:local )?ip(?: address)?"
    r"|what(?:'s| is) (?:the |my )local ip(?: address)?"
    r"|what ip am i on"
    r"|show my ip",
    re.IGNORECASE,
)
_VERSION = re.compile(
    r"(?:what(?:'s| is) )?(?:the )?kilix version"
    r"|what version of kilix(?: is this)?"
    r"|what version is (?:this|kilix)",
    re.IGNORECASE,
)
_RUN_ID = r"[0-9]{8}t[0-9]{6}\.[0-9]{6}z-[0-9a-f]{32}"
_SESSION_LIST = re.compile(
    r"(?:list|show)(?: me)?(?: the)? (?:kilix )?sessions"
    r"|what sessions (?:are open|do i have|are running)"
    r"|which sessions are (?:open|running)",
    re.IGNORECASE,
)
_SESSION_READ = re.compile(
    rf"(?:read|show) session (?P<run>{_RUN_ID})",
    re.IGNORECASE,
)
_SESSION_LOG = re.compile(
    rf"(?:where is the log for|what(?:'s| is) the log path for|session log path)"
    rf" (?:session )?(?P<run>{_RUN_ID})",
    re.IGNORECASE,
)
_SESSION_LAUNCH = re.compile(
    r"(?:launch|start)(?: a| an)? (?P<provider>codex|claude|grok)(?: session)?",
    re.IGNORECASE,
)
_RUN_EXACT = re.compile(_RUN_ID, re.IGNORECASE)
_PACKAGE_NAME = re.compile(r"[a-z0-9][a-z0-9+.-]{0,63}\Z")
_VERSION_TEXT = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,39}\Z")


class LocalError(Exception):
    """A recognized question that could not be read or changed."""


def clean(text: str) -> str:
    text = " ".join(text.strip().replace("’", "'").split())
    text = _TRAIL.sub("", text).strip()
    return _PREFIX.sub("", text).strip()


def classify(text: str) -> tuple[str, str] | None:
    """Return the local action and its argument, or None when Ask should continue."""
    text = clean(text)
    if not text:
        return None
    level = _BRIGHT_SET.fullmatch(text)
    if level:
        return "brightness-set", level.group("level")
    if _BRIGHT_CHECK.fullmatch(text):
        return "brightness", ""
    if _BATTERY.fullmatch(text):
        return "battery", ""
    if _UPTIME.fullmatch(text):
        return "uptime", ""
    package = _PACKAGE.fullmatch(text)
    if package:
        return "package", package.group("name").casefold()
    if _ADDRESS.fullmatch(text):
        return "address", ""
    if _VERSION.fullmatch(text):
        return "version", ""
    if _SESSION_LIST.fullmatch(text):
        return "sessions", ""
    read = _SESSION_READ.fullmatch(text)
    if read:
        return "session-read", read.group("run")
    log = _SESSION_LOG.fullmatch(text)
    if log:
        return "session-log", log.group("run")
    launch = _SESSION_LAUNCH.fullmatch(text)
    if launch:
        return "session-launch", launch.group("provider").casefold()
    if _NETWORK.fullmatch(text):
        return "network", ""
    if _ONLINE.fullmatch(text):
        return "online", ""
    if _PLAYING.fullmatch(text):
        return "playing", ""
    if _VOICE.fullmatch(text):
        return "voice", ""
    return None


def _backlight_root() -> Path:
    return Path(os.environ.get("KILIX_BACKLIGHT_ROOT", "/sys/class/backlight"))


def _backlight() -> Path:
    root = _backlight_root()
    if not root.is_dir():
        raise LocalError("no backlight")
    found = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if (child / "brightness").is_file() and (child / "max_brightness").is_file():
            found.append(child)
    if not found:
        raise LocalError("no backlight")
    for child in found:
        if "intel" in child.name.casefold():
            return child
    return found[0]


def _levels(device: Path) -> tuple[int, int]:
    try:
        current = int((device / "brightness").read_text(encoding="utf-8").strip())
        maximum = int((device / "max_brightness").read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        raise LocalError("backlight reading is incomplete") from error
    if maximum <= 0 or current < 0:
        raise LocalError("backlight reading is incomplete")
    return current, maximum


def _percent(current: int, maximum: int) -> int:
    return int(round(current * 100 / maximum))


def _brightness(level: int | None) -> str:
    device = _backlight()
    _current, maximum = _levels(device)
    if level is not None:
        level = max(0, min(100, level))
        raw = max(0, min(maximum, int(round(level * maximum / 100))))
        (device / "brightness").write_text(f"{raw}\n", encoding="utf-8")
    current, maximum = _levels(device)
    return f"{_percent(current, maximum)}%"


def _split_terse(line: str) -> list[str]:
    """Split one nmcli -t line, honouring backslash escapes of : and \\."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def _nmcli_bin() -> Path | None:
    override = os.environ.get("KILIX_NMCLI")
    if override is not None:
        path = Path(override).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path
        return None
    found = shutil.which("nmcli")
    return Path(found) if found else None


def _nmcli_names(binary: Path) -> list[str] | None:
    try:
        result = subprocess.run(
            [str(binary), "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 and not result.stdout.strip():
        return None
    names = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = _split_terse(line)
        if len(fields) < 4 or fields[1] == "loopback":
            continue
        if not fields[2].startswith("connected"):
            continue
        label = fields[3].strip() or fields[0].strip()
        if label:
            names.append(label)
    return names


def _sysfs_names() -> list[str]:
    root = Path(os.environ.get("KILIX_NET_SYS", "/sys/class/net"))
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    names = []
    for child in children:
        if child.name == "lo":
            continue
        try:
            state = (child / "operstate").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if state == "up":
            names.append(child.name)
    return names


def _network_names() -> list[str]:
    binary = _nmcli_bin()
    if binary is not None:
        names = _nmcli_names(binary)
        if names is not None:
            return names
    return _sysfs_names()


def _amp_socket() -> str:
    override = os.environ.get("KILIX_AMP_SOCKET")
    if override:
        return override
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        runtime = os.path.expanduser("~/.local/gpu_terminal/kilix/session")
    return os.path.join(runtime, "kilix-amp.sock")


def _music():
    candidates = []
    override = os.environ.get("KILIX_TUI_UTILS_DIR")
    if override:
        candidates.append(Path(override))
    candidates.append(
        Path.home() / ".local/gpu_terminal/sources/kilix-desktops/kilix-tui-utils"
    )
    path = None
    for candidate in candidates:
        trial = candidate / "src" / "kilix_tui" / "music_protocol.py"
        if trial.is_file():
            path = trial
            break
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("kilix_ask_music", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def format_track(reply: dict) -> str:
    """Title while playing, paused title, or nothing."""
    title = reply.get("title")
    if not isinstance(title, str) or not title.strip():
        file = reply.get("file")
        title = os.path.basename(file.rstrip("/")) if isinstance(file, str) else ""
    title = " ".join(title.split())
    state = reply.get("state")
    if state == "playing" and title:
        return title
    if state == "paused" and title:
        return f"paused: {title}"
    return "nothing is playing"


def _playing() -> str:
    path = _amp_socket()
    if not os.path.exists(path):
        return "nothing is playing"
    module = _music()
    if module is None:
        raise LocalError("the player status is unreadable")
    control = module.MusicControl(path)
    try:
        reply = control.command("state")
    finally:
        control.close()
    if not isinstance(reply, dict) or not reply:
        return "nothing is playing"
    return format_track(reply)


def _voice_socket() -> str:
    session = os.environ.get("KILIX_VOICE_SESSION")
    if session:
        return os.path.join(session, "control.sock")
    home = os.environ.get("KILIX_SESSION_HOME")
    if home:
        return os.path.join(home, "voice", "control.sock")
    storage = os.environ.get("KILIX_STORAGE_HOME")
    if storage:
        return os.path.join(storage, "session", "voice", "control.sock")
    return os.path.expanduser(
        "~/.local/gpu_terminal/kilix/session/voice/control.sock"
    )


def _format_voice(status: dict) -> str:
    parts = ["listening" if status.get("listening") is True else "not listening"]
    if status.get("speaking") is True:
        parts.append("speaking")
    model = status.get("model")
    if isinstance(model, str):
        model = " ".join(model.split())
        if model:
            parts.append(model[:80])
    return ", ".join(parts)


def _voice() -> str:
    """Read an already running voice daemon. Never start one."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    client.settimeout(2)
    try:
        try:
            client.connect(_voice_socket())
        except OSError:
            return "not listening"
        client.sendall(b'{"op":"status"}\n')
        raw = client.recv(65536)
    finally:
        client.close()
    try:
        reply = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise LocalError("the microphone status is unreadable") from error
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise LocalError("the microphone status is unreadable")
    status = reply.get("status")
    if not isinstance(status, dict):
        raise LocalError("the microphone status is unreadable")
    return _format_voice(status)


def _battery() -> str:
    root = Path(os.environ.get("KILIX_BATTERY_SUPPLY_DIR", "/sys/class/power_supply"))
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return "no battery"
    lines = []
    for child in children:
        try:
            kind = (child / "type").read_text(encoding="utf-8").strip().casefold()
        except OSError:
            continue
        if kind != "battery":
            continue
        try:
            status = (child / "status").read_text(encoding="utf-8").strip().casefold() or "unknown"
        except OSError:
            status = "unknown"
        try:
            value = int((child / "capacity").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            value = -1
        label = f"{value}% {status}" if 0 <= value <= 100 else status
        lines.append((child.name, label))
    if not lines:
        return "no battery"
    if len(lines) == 1:
        return lines[0][1]
    return "\n".join(f"{name}: {label}" for name, label in lines)


def _uptime() -> str:
    path = Path(os.environ.get("KILIX_UPTIME_FILE", "/proc/uptime"))
    try:
        seconds = int(float(path.read_text(encoding="utf-8").split()[0]))
    except (OSError, ValueError, IndexError) as error:
        raise LocalError("uptime is unreadable") from error
    if seconds < 0:
        raise LocalError("uptime is unreadable")
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _tool(name: str, env_name: str, fallbacks: tuple[str, ...] = ()) -> Path | None:
    override = os.environ.get(env_name)
    if override is not None:
        path = Path(override).expanduser()
        return path if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return Path(found)
    for candidate in fallbacks:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def _package(name: str) -> str:
    if _PACKAGE_NAME.fullmatch(name) is None:
        raise LocalError("name one package")
    binary = _tool("dpkg-query", "KILIX_DPKG_QUERY")
    if binary is None:
        raise LocalError("package lookup is not installed")
    result = subprocess.run(
        [str(binary), "-W", "-f=${db:Status-Status}\t${Version}", name],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return f"{name} is not installed"
    status, _, version = result.stdout.strip().partition("\t")
    if status != "installed":
        return f"{name} is not installed"
    version = " ".join(version.split())
    if version:
        return f"{name} {version}"
    return f"{name} is installed"


def _ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or (len(part) > 1 and part.startswith("0")):
            return False
        number = int(part)
        if number > 255:
            return False
    return True


def _address() -> str:
    binary = _tool("ip", "KILIX_IP_BIN", ("/usr/bin/ip", "/usr/sbin/ip", "/bin/ip"))
    if binary is None:
        raise LocalError("address lookup is not installed")
    result = subprocess.run(
        [str(binary), "-4", "route", "get", "192.0.2.1"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    match = re.search(r"\bsrc (\d{1,3}(?:\.\d{1,3}){3})\b", result.stdout)
    if result.returncode != 0 or match is None or not _ipv4(match.group(1)):
        raise LocalError("no address")
    return match.group(1)


def _version() -> str:
    override = os.environ.get("KILIX_VERSION_FILE")
    if override:
        path = Path(override).expanduser()
    else:
        home = os.environ.get("KILIX_HOME")
        path = Path(home) / "VERSION" if home else Path(__file__).resolve().parents[1] / "VERSION"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise LocalError("version is unknown") from error
    if _VERSION_TEXT.fullmatch(text) is None:
        raise LocalError("version is unknown")
    return text


def _runs_root() -> Path:
    override = os.environ.get("KILIX_AGENT_RUNS_HOME")
    if override:
        return Path(override).expanduser()
    storage = os.environ.get("KILIX_STORAGE_HOME")
    if storage:
        return Path(storage).expanduser() / "agent-runs"
    return Path.home() / ".local/gpu_terminal/kilix/agent-runs"


def _session_bin() -> Path | None:
    override = os.environ.get("KILIX_SESSION_BIN")
    if override is not None:
        path = Path(override).expanduser()
        return path if path.is_file() and os.access(path, os.X_OK) else None
    home = os.environ.get("KILIX_HOME")
    path = Path(home) / "kilix-session" if home else Path(__file__).resolve().parents[1] / "kilix-session"
    return path if path.is_file() else None


def _run_session(args: list[str]) -> subprocess.CompletedProcess[str]:
    binary = _session_bin()
    if binary is None:
        raise LocalError("session control is not installed")
    return subprocess.run(
        [str(binary), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _session_text(args: list[str]) -> str:
    result = _run_session(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise LocalError((detail[-1] if detail else "session control failed")[:300])
    return result.stdout


def _run_id(value: str) -> str:
    if _RUN_EXACT.fullmatch(value) is None:
        raise LocalError("use the exact session id")
    return value


def _session_log(run_id: str) -> Path:
    run_id = _run_id(run_id)
    root = _runs_root()
    directory = root / run_id
    if not directory.is_dir():
        raise LocalError("no session")
    return directory / "events.jsonl"


def _format_run(row: dict) -> str:
    run_id = row.get("run_id")
    if not isinstance(run_id, str) or _RUN_EXACT.fullmatch(run_id) is None:
        return ""
    provider = row.get("provider") if isinstance(row.get("provider"), str) else ""
    title = " ".join(str(row.get("title") or "").split())[:80]
    status = row.get("effective_status") if isinstance(row.get("effective_status"), str) else ""
    return " ".join(part for part in (run_id, provider, title, status) if part)


def _sessions() -> str:
    try:
        payload = json.loads(_session_text(["list", "--limit", "20"]))
    except json.JSONDecodeError as error:
        raise LocalError("session list is unreadable") from error
    runs = payload.get("runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise LocalError("session list is unreadable")
    lines = [_format_run(row) for row in runs[:20] if isinstance(row, dict)]
    lines = [line for line in lines if line]
    return "\n".join(lines) if lines else "no sessions"


def _session_read(run_id: str) -> str:
    path = _session_log(run_id)
    if not path.is_file():
        raise LocalError("this output is not available")
    try:
        data = path.read_bytes()[-65536:]
    except OSError as error:
        raise LocalError("this output is not available") from error
    lines = data.decode("utf-8", "replace").splitlines()[-40:]
    text = "\n".join(lines).strip()
    return text or "no session output"


def _session_launch(provider: str) -> str:
    if provider not in {"codex", "claude", "grok"}:
        raise LocalError("name a session: codex, claude, or grok")
    try:
        payload = json.loads(_session_text(["launch", provider, "--new-tab"]))
    except json.JSONDecodeError as error:
        raise LocalError("session launch did not return a receipt") from error
    if not isinstance(payload, dict):
        raise LocalError("session launch did not return a receipt")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str):
        raise LocalError("session launch did not return a receipt")
    run_id = _run_id(run_id)
    launch = payload.get("launch") if isinstance(payload.get("launch"), dict) else {}
    pane = launch.get("pane") if isinstance(launch.get("pane"), dict) else {}
    pane_id = pane.get("pane_id")
    pane_text = str(pane_id) if type(pane_id) is int and pane_id > 0 else "none"
    run_dir = payload.get("run_dir")
    if isinstance(run_dir, str) and run_dir.startswith("/") and ".." not in Path(run_dir).parts:
        log = str(Path(run_dir) / "events.jsonl")
    else:
        log = str(_runs_root() / run_id / "events.jsonl")
    return f"run {run_id}\npane {pane_text}\nlog {log}"


def answer(text: str) -> tuple[int, str]:
    action = classify(text)
    if action is None:
        return NOT_LOCAL, ""
    kind, arg = action
    if kind == "brightness":
        return 0, _brightness(None)
    if kind == "brightness-set":
        return 0, _brightness(int(arg))
    if kind == "battery":
        return 0, _battery()
    if kind == "uptime":
        return 0, _uptime()
    if kind == "package":
        return 0, _package(arg)
    if kind == "address":
        return 0, _address()
    if kind == "version":
        return 0, _version()
    if kind == "sessions":
        return 0, _sessions()
    if kind == "session-read":
        return 0, _session_read(arg)
    if kind == "session-log":
        return 0, str(_session_log(arg))
    if kind == "session-launch":
        return 0, _session_launch(arg)
    names = None
    if kind in {"network", "online"}:
        names = _network_names()
    if kind == "network":
        return 0, "\n".join(names) if names else "not connected"
    if kind == "online":
        return 0, "online" if names else "offline"
    if kind == "playing":
        return 0, _playing()
    if kind == "voice":
        return 0, _voice()
    return NOT_LOCAL, ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["--ask"]:
        print("usage: kilix_local.py --ask TEXT", file=sys.stderr)
        return 2
    try:
        status, text = answer(" ".join(argv[1:]))
    except LocalError as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    if status == 1:
        print(text or "kilix ask: local status failed", file=sys.stderr)
    elif text:
        print(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
