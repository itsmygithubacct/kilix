"""Trust one launch directory the way each coding client records it.

A launch asked to trust its folder answers the client's first-start "trust
this folder?" question before the client starts, for exactly that directory:
- claude: `projects[<dir>].hasTrustDialogAccepted` in its global config
  (`$CLAUDE_CONFIG_DIR/.claude.json`, else `~/.claude.json`), written under
  Claude's own lock (`<file>.lock`, a proper-lockfile directory) and re-read
  inside it, so a running Claude's writes are not lost;
- codex: `[projects."<dir>"] trust_level = "trusted"` in
  `$CODEX_HOME/config.toml` (else `~/.codex/config.toml`); the new text is
  parsed again before it replaces the file;
- grok records its own trust: the launch passes `grok --trust` (see
  agent_control.agent_argv), so nothing here writes grok's store;
- omp and kimi have no trust prompt.

Nothing is typed into a dialog, no other setting changes, a decision the
user already made for the folder is left alone, and a symlinked or malformed
settings file is refused rather than replaced.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import tempfile
import time
import tomllib

UNSAFE = re.compile(r'["\\\x00-\x1f\x7f]')


class TrustError(RuntimeError):
    pass


def _replace(path: Path, text: str) -> None:
    """Write text to path atomically, keeping the file's mode; never through a link."""
    if path.is_symlink():
        raise TrustError(f"{path} is a symbolic link; it is left as it is")
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class _ClaudeLock:
    """Claude's own lock on its global config: proper-lockfile's `<file>.lock`
    directory. Waits a few seconds for a running Claude; never steals it."""

    def __init__(self, path: Path, wait: float = 5.0):
        self.lock, self.wait = path.with_name(path.name + ".lock"), wait

    def __enter__(self):
        deadline = time.monotonic() + self.wait
        while True:
            try:
                os.mkdir(self.lock)
                return self
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise TrustError(f"cannot take {self.lock}: {exc}") from exc
            if time.monotonic() >= deadline:
                raise TrustError(f"{self.lock} is held by a running Claude; try again")
            time.sleep(0.1)

    def __exit__(self, *exc):
        try:
            os.rmdir(self.lock)
        except OSError:
            pass
        return False


def _claude(directory: str, home: Path) -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    path = Path(base) / ".claude.json" if base else home / ".claude.json"
    if path.is_symlink():
        raise TrustError(f"{path} is a symbolic link; it is left as it is")
    with _ClaudeLock(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError) as exc:
            raise TrustError(f"{path} could not be read as JSON; left as it is") from exc
        projects = data.setdefault("projects", {}) if isinstance(data, dict) else None
        project = projects.setdefault(directory, {}) if isinstance(projects, dict) else None
        if not isinstance(project, dict):
            raise TrustError(f"{path} has an unexpected shape; left as it is")
        if project.get("hasTrustDialogAccepted") is True:
            return "already trusted"
        project["hasTrustDialogAccepted"] = True
        _replace(path, json.dumps(data, indent=2) + "\n")
    return "trusted"


def _codex(directory: str, home: Path) -> str:
    base = os.environ.get("CODEX_HOME")
    path = (Path(base) if base else home / ".codex") / "config.toml"
    if path.is_symlink():
        raise TrustError(f"{path} is a symbolic link; it is left as it is")
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        projects = tomllib.loads(text).get("projects") or {}
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise TrustError(f"{path} could not be read as TOML; left as it is") from exc
    if not isinstance(projects, dict):
        raise TrustError(f"{path} has an unexpected projects setting; left as it is")
    entry = projects.get(directory)
    if entry is not None:
        if isinstance(entry, dict) and entry.get("trust_level") == "trusted":
            return "already trusted"
        # The user decided otherwise for this folder: leave their decision.
        raise TrustError(f"{directory} has its own trust_level; left as it is")
    new = (text if text.endswith("\n") or not text else text + "\n") + \
        f'\n[projects."{directory}"]\ntrust_level = "trusted"\n'
    try:
        check = tomllib.loads(new)["projects"][directory]["trust_level"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError):
        check = None
    if check != "trusted":
        # e.g. an inline `projects = {...}` table cannot be extended this way.
        raise TrustError(f"{path} cannot take the entry without breaking it; left as it is")
    _replace(path, new)
    return "trusted"


CLIENTS = {"claude": _claude, "codex": _codex}


def trust(agent: str, directory: str, home: Path | None = None) -> str:
    """Record trust for exactly `directory`; return what was done."""
    home = home or Path.home()
    path = Path(directory)
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise TrustError("only an existing absolute directory, not a link, can be trusted")
    path = path.resolve()
    recorded = str(path)
    if UNSAFE.search(recorded):
        raise TrustError("the directory name has quotes, backslashes or control characters")
    if path == home.resolve() or path == Path("/"):
        raise TrustError("the home directory and / are never trusted wholesale")
    if path.stat().st_uid != os.getuid():
        raise TrustError("only a directory you own can be trusted")
    if agent == "grok":
        return "trusted by grok --trust"
    handler = CLIENTS.get(agent)
    if handler is None:
        return "no trust prompt"          # kimi, qwen-omp: nothing to record
    return handler(recorded, home)
