#!/usr/bin/env python3
"""Expose or attach to a live Kilix pane through kilix-multiplexer."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from typing import Any

import remote as live


KILIX_HOME = Path(__file__).resolve().parents[1]
KITTEN = os.environ.get("KILIX_KITTEN", "kitten")
RC_PASSWORD_FILE = os.environ.get("KILIX_RC_PASSWORD_FILE", "")
SESSION_HOME = Path(os.environ.get(
    "KILIX_SESSION_HOME",
    os.path.expanduser("~/.local/gpu_terminal/kilix/session"),
))
BROKER_SESSION = re.compile(r"[0-9a-f]{16,64}\Z")


def fail(message: str, code: int = 1) -> int:
    print(f"kilix remote: {message}", file=sys.stderr)
    return code


def build_binary(kind: str) -> str:
    proc = subprocess.run(
        [str(KILIX_HOME / "scripts" / "build-multiplexer.sh"),
         "--print-path", kind],
        check=False, stdout=subprocess.PIPE, stderr=None, text=True,
    )
    path = proc.stdout.strip()
    if proc.returncode != 0 or not path or not os.access(path, os.X_OK):
        raise RuntimeError(f"could not build the multiplexer {kind} executable")
    return path


def broker_binary() -> str:
    configured = os.environ.get("KITTY_PTY_BROKER_EXECUTABLE", "")
    if configured and os.access(configured, os.X_OK):
        return configured
    proc = subprocess.run(
        [str(KILIX_HOME / "scripts" / "build-pty-broker.sh"), "--print-path"],
        check=False, stdout=subprocess.PIPE, stderr=None, text=True,
    )
    path = proc.stdout.strip()
    if proc.returncode != 0 or not path or not os.access(path, os.X_OK):
        raise RuntimeError("could not build the protocol-v2 pty broker")
    return path


def environment(window: dict[str, Any]) -> dict[str, str]:
    value = window.get("env") or {}
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    answer: dict[str, str] = {}
    if isinstance(value, list):
        for item in value:
            key, separator, content = str(item).partition("=")
            if separator:
                answer[key] = content
    return answer


def target_window(raw: str) -> dict[str, Any]:
    state = live.load_state("remote")
    kind, pane_id = live.resolve_target("remote", raw, state)
    if kind != "pane":
        raise RuntimeError(
            f"{raw} is a tab; run 'kilix ls --panes' and choose a PANE_ID")
    if pane_id == os.environ.get("KITTY_WINDOW_ID"):
        raise RuntimeError("refusing to expose the pane running the server")
    for _, _, _, windows in live.iter_tabs(state):
        for window in windows:
            if str(window.get("id")) == pane_id:
                return window
    raise RuntimeError(f"no live pane with id {pane_id}")


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) \
            or info.st_uid != os.geteuid():
        raise RuntimeError(f"unsafe session directory: {path}")
    path.chmod(0o700)


def validated_session(value: str) -> str:
    if not BROKER_SESSION.fullmatch(value):
        raise RuntimeError("the selected pane has an invalid broker session id")
    return value


def frame_socket(session_id: str) -> Path:
    directory = SESSION_HOME / "remote"
    private_directory(directory)
    name = f"{session_id}.tap"
    candidate = directory / name
    # Linux sockaddr_un leaves 107 bytes for a pathname.
    if len(os.fsencode(candidate)) >= 107:
        digest = hashlib.sha256(session_id.encode("ascii")).hexdigest()[:24]
        candidate = directory / f"{digest}.tap"
    if candidate.exists() or candidate.is_symlink():
        raise RuntimeError(
            f"frame socket already exists: {candidate} "
            "(another server may own this pane)")
    return candidate


def input_helper(session_id: str) -> str:
    if not RC_PASSWORD_FILE or not os.path.isfile(RC_PASSWORD_FILE):
        raise RuntimeError("the private remote-control credential is unavailable")
    return shlex.join([
        KITTEN, "@", "--password-file", RC_PASSWORD_FILE,
        "send-text", "--match",
        f"env:KITTY_PTY_BROKER_SESSION={session_id}", "--stdin",
    ])


def cmd_serve(ns: argparse.Namespace) -> int:
    try:
        window = target_window(ns.pane)
        pane_env = environment(window)
        raw_session = pane_env.get("KITTY_PTY_BROKER_SESSION", "")
        if not raw_session:
            raise RuntimeError("the selected pane is not owned by the pty broker")
        session_id = validated_session(raw_session)
        runtime = os.environ.get(
            "KITTY_PTY_BROKER_RUNTIME",
            str(SESSION_HOME / "pty-broker"),
        )
        serve = build_binary("serve")
        broker = broker_binary()
        tap = None if ns.no_graphics else frame_socket(session_id)
        rows = int(window.get("lines") or 24)
        columns = int(window.get("columns") or 80)
        title = ns.title or str(window.get("title") or "live pane")
        argv = [
            serve, "--socket", ns.socket,
            "--broker-session", session_id,
            "--broker-runtime", runtime,
            "--broker-executable", broker,
            "--rows", str(rows), "--cols", str(columns),
            "--title", title,
        ]
        if tap is not None:
            argv.extend([
                "--tap-socket", str(tap),
                "--tap-session", session_id,
                "--pixel-budget", str(ns.video_budget),
            ])
        if not ns.no_input:
            argv.extend(["--input-command", input_helper(session_id)])
        if ns.audio_source:
            argv.extend([
                "--audio-source", ns.audio_source,
                "--audio-rate", str(ns.audio_rate),
                "--audio-channels", str(ns.audio_channels),
                "--audio-budget", str(ns.audio_budget),
            ])
        if ns.lan:
            argv.append("--lan")
        if ns.tls:
            argv.append("--tls")
        if ns.no_tls:
            argv.append("--no-tls")
        if ns.token:
            argv.extend(["--token", ns.token])
        os.execv(serve, argv)
    except (OSError, RuntimeError, ValueError) as exc:
        return fail(str(exc))
    return 1


def cmd_attach(ns: argparse.Namespace, *, view: bool) -> int:
    try:
        attach = build_binary("attach")
        argv = [attach, "--socket", ns.socket]
        if view:
            argv.append("--view")
        if ns.token:
            argv.extend(["--token", ns.token])
        if ns.tls_fingerprint:
            argv.extend(["--tls-fingerprint", ns.tls_fingerprint])
        if ns.no_predict:
            argv.append("--no-predict")
        if ns.dump:
            argv.append("--dump")
        if ns.send is not None:
            argv.extend(["--send", ns.send])
        if ns.seconds:
            argv.extend(["--seconds", str(ns.seconds)])
        argv.extend(["--reconnect", str(ns.reconnect)])
        if ns.no_audio:
            argv.append("--no-audio")
        elif ns.audio_output:
            argv.extend(["--audio-output", ns.audio_output])
        os.execv(attach, argv)
    except (OSError, RuntimeError, ValueError) as exc:
        return fail(str(exc))
    return 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="kilix remote",
        description="Attach to a live graphical Kilix pane",
    )
    commands = root.add_subparsers(dest="command", required=True)

    serve = commands.add_parser(
        "serve", help="expose one live pane through a broker observer")
    serve.add_argument("pane", help="PANE_ID from 'kilix ls --panes'")
    serve.add_argument("--socket", required=True, help="Unix path or HOST:PORT")
    serve.add_argument("--title", default="")
    serve.add_argument("--token", default="")
    serve.add_argument("--lan", action="store_true")
    transport = serve.add_mutually_exclusive_group()
    transport.add_argument("--tls", action="store_true")
    transport.add_argument("--no-tls", action="store_true")
    serve.add_argument("--no-input", action="store_true",
                       help="do not start the separate pane-scoped input path")
    serve.add_argument("--no-graphics", action="store_true")
    serve.add_argument("--video-budget", type=int, default=0,
                       help="motion bytes per second; zero is unlimited")
    serve.add_argument("--audio-source", default="",
                       help="command writing signed 16-bit PCM to stdout")
    serve.add_argument("--audio-rate", type=int, default=48000)
    serve.add_argument("--audio-channels", type=int, default=2)
    serve.add_argument("--audio-budget", type=int, default=0)

    for name in ("attach", "view"):
        attach = commands.add_parser(name, help=f"{name} a remote pane")
        attach.add_argument("--socket", required=True)
        attach.add_argument("--token", default="")
        attach.add_argument("--tls-fingerprint", default="")
        attach.add_argument("--reconnect", type=int, default=30)
        attach.add_argument("--no-predict", action="store_true")
        attach.add_argument("--dump", action="store_true")
        attach.add_argument("--send")
        attach.add_argument("--seconds", type=int, default=0)
        attach.add_argument("--audio-output", default="")
        attach.add_argument("--no-audio", action="store_true")
    return root


def main(argv: list[str]) -> int:
    ns = parser().parse_args(argv)
    if ns.command == "serve":
        return cmd_serve(ns)
    return cmd_attach(ns, view=ns.command == "view")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
