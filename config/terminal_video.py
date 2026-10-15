#!/usr/bin/env python3
"""Play media in the caller's terminal with an explicitly selected mpv output."""
import argparse
import os
import shutil
import sys
import signal
import subprocess
import termios
from pathlib import Path


TERMINAL_RESET = (b"\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1005l"
                  b"\x1b[?1006l\x1b[?1015l\x1b[?1049l\x1b[?25h\x1b[0m")


def run_player(command):
    """Keep a parent alive to restore the calling tty even when mpv aborts."""
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    previous = {}
    player = None
    try:
        player = subprocess.Popen(command)
        def forward(signum, frame):
            if player.poll() is None:
                player.send_signal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, forward)
        code = player.wait()
        if code < 0:
            print(f"kilix video: player terminated by signal {-code}; terminal restored.",
                  file=sys.stderr)
        return code if code >= 0 else 128 - code
    finally:
        if player is not None and player.poll() is None:
            player.terminate()
            try:
                player.wait(timeout=3)
            except subprocess.TimeoutExpired:
                player.kill()
                player.wait()
        try:
            os.write(sys.stdout.fileno(), TERMINAL_RESET)
            termios.tcsetattr(fd, termios.TCSANOW, saved)
            termios.tcflush(fd, termios.TCIFLUSH)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kilix video",
        description="Play a local video or direct stream URL in this terminal.",
        epilog="Space: pause; arrows: seek; q: quit. Requires mpv. "
               "Use -- before a filename starting with -. Webpage URLs may require yt-dlp.")
    parser.add_argument("--vo", choices=("kitty", "tct"), default="kitty",
                        help="terminal renderer (default: kitty; tct uses text cells)")
    parser.add_argument("--mute", action="store_true", help="disable sound")
    parser.add_argument("--loop", action="store_true", help="repeat playback")
    parser.add_argument("source", help="local file or direct HTTP(S)/RTSP stream URL")
    args = parser.parse_args(argv)
    storage = Path(os.environ.get("KILIX_STORAGE_HOME",
                                  str(Path.home() / ".local/gpu_terminal/kilix")))
    private_player = storage / "media/mpv-0.40.0-kilix1/bin/mpv"
    mpv = str(private_player) if os.access(private_player, os.X_OK) else shutil.which("mpv")
    if not mpv:
        parser.exit(127, "kilix video: mpv is required; install the mpv package.\n")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(2, "kilix video: run in an interactive terminal (SSH requires -t).\n")
    if args.vo == "kitty" and mpv != str(private_player):
        version = subprocess.run([mpv, "--version"], capture_output=True, text=True,
                                 timeout=5, check=False).stdout.splitlines()
        if version and version[0].startswith("mpv v0.40.0 "):
            parser.exit(2, "kilix video: mpv 0.40.0 Kitty output can crash on resize. "
                        "Run 'kilix video --install-player' for the fixed private player "
                        "or use '--vo tct' for text-cell playback.\n")
    # Never load user output profiles/scripts that could create a GUI window.
    # -- terminates options so media names cannot become player instructions.
    command = [mpv, "--no-config", "--load-scripts=no", "--vo=" + args.vo,
               "--terminal=yes", "--input-terminal=yes", "--force-window=no",
               "--audio-display=no"]
    if args.mute:
        command.append("--mute=yes")
    if args.loop:
        command.append("--loop-file=inf")
    command.extend(["--", args.source])
    return run_player(command)


if __name__ == "__main__":
    raise SystemExit(main())
