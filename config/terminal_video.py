#!/usr/bin/env python3
"""Play media in the caller's terminal with an explicitly selected mpv output."""
import argparse
import fcntl
import os
import re
import select
import shutil
import struct
import sys
import signal
import subprocess
import termios
import threading
import time
import tty
from pathlib import Path


# A player killed while sending an image leaves its graphics command (an APC
# string) open, and the terminal then swallows everything up to the next ST.
# Close it first so the reset and later output are not consumed as image data.
TERMINAL_RESET = (b"\x1b\\\x1b[?2026l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1005l"
                  b"\x1b[?1006l\x1b[?1016l\x1b[?1015l\x1b[?1049l\x1b[?25h\x1b[0m")
# mpv warns about this deliberate WSL setting on every run; it is not a fault.
STREAM_SILENCE_NOTICE = "The --audio-stream-silence option is set."
CELL_SIZE_REPLY = re.compile(rb'\x1b\[6;([0-9]{1,4});([0-9]{1,4})t')


def run_player(command, environment=None):
    """Keep a parent alive to restore the calling tty even when mpv aborts."""
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    previous = {}
    player = None
    diagnostics = bytearray()
    reader = None
    code = None

    def collect():
        # Drain continuously so a noisy decoder cannot fill the pipe and stall.
        while chunk := player.stderr.read(4096):
            diagnostics.extend(chunk)
            del diagnostics[:-65536]

    try:
        player = subprocess.Popen(command, stderr=subprocess.PIPE, env=environment)
        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        def forward(signum, frame):
            if player.poll() is None:
                player.send_signal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, forward)
        code = player.wait()
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
        # Print after leaving the alternate screen, including native crash text.
        if reader is not None:
            reader.join(timeout=1)
            if not reader.is_alive():
                player.stderr.close()
        if diagnostics:
            text = diagnostics.decode('utf-8', errors='replace')
            text = ''.join(c for c in text if c in '\n\t' or c.isprintable())
            text = '\n'.join(line for line in text.splitlines()
                              if STREAM_SILENCE_NOTICE not in line)
            if text.strip():
                print(text.rstrip(), file=sys.stderr)
        if code is not None and code < 0:
            print(f"kilix video: player terminated by signal {-code}; terminal restored.",
                  file=sys.stderr)


def cell_size(device='/dev/tty', timeout=1):
    """Ask the terminal for its cell size (CSI 16 t) when the pty has no pixels.

    The Windows console gives WSL's pty only cell counts, and kilix-mpv would
    otherwise assume 9x18-pixel cells. Returns WIDTHxHEIGHT or None."""
    try:
        fd = os.open(device, os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    match = None
    try:
        rows, columns, width, height = struct.unpack('HHHH', fcntl.ioctl(fd, termios.TIOCGWINSZ, bytes(8)))
        if (width and height) or not (rows and columns) or background(fd):
            return None
        saved = termios.tcgetattr(fd)
        try:
            tty.setraw(fd, termios.TCSANOW)
            os.write(fd, b'\x1b[16t')
            deadline = time.monotonic() + timeout
            reply = b''
            # Typed-ahead bytes can arrive first, so wait for the reply itself.
            while not (match := CELL_SIZE_REPLY.search(reply)) and len(reply) < 1024:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                    break
                chunk = os.read(fd, 64)
                if not chunk:
                    break
                reply += chunk
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, saved)
    except (OSError, termios.error):
        return None
    finally:
        os.close(fd)
    if not match or not (0 < int(match[1]) <= 1024 and 0 < int(match[2]) <= 1024):
        return None
    return f'{int(match[2])}x{int(match[1])}'


def background(fd):
    """A background job that touches its terminal is stopped by SIGTTOU."""
    try:
        return os.tcgetpgrp(fd) != os.getpgrp()
    except OSError:
        return False  # not this process's controlling terminal: no job control


def local_shared_memory_terminal():
    """Prove that a local Kitty process sees the same /dev/shm directory."""
    if any(os.environ.get(key) for key in ('SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY', 'TMUX')):
        return False
    pid = os.environ.get('KITTY_PID', '')
    if not pid.isascii() or not pid.isdigit() or len(pid) > 10 or int(pid) <= 1:
        return False
    process = Path('/proc') / pid
    try:
        if process.stat().st_uid != os.getuid():
            return False
        if (process / 'comm').read_text().strip() != 'kitty':
            return False
        return os.path.samefile('/dev/shm', process / 'root/dev/shm')
    except (OSError, ValueError):
        return False


def wsl_kernel():
    try:
        return 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()
    except OSError:
        return False


def supports_terminal_fullscreen(player):
    # An older separately installed fork must remain usable with a newer host.
    try:
        result = subprocess.run([player, '--no-config', '--list-options'],
                                capture_output=True, text=True, timeout=5, check=False)
        return result.returncode == 0 and '--vo-kitty-terminal-fullscreen ' in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


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
    parser.add_argument("--transport", choices=("auto", "inline", "shm"), default="auto",
                        help="Kitty transport (default: auto; use shm for a verified local "
                             "Kitty/Kilix terminal, otherwise inline)")
    parser.add_argument("source", help="local file or direct HTTP(S)/RTSP stream URL")
    args = parser.parse_args(argv)
    storage = Path(os.environ.get("KILIX_STORAGE_HOME",
                                  str(Path.home() / ".local/gpu_terminal/kilix")))
    private_player = storage / "media/mpv-0.40.0-kilix1/bin/mpv"
    # The WSL companion names its catalog build of the same fork.
    configured = os.environ.get("KILIX_VIDEO_PLAYER", "")
    named_player = (configured if os.path.isabs(configured) and os.access(configured, os.X_OK)
                    else shutil.which("kilix-mpv"))
    mpv = named_player or (str(private_player) if os.access(private_player, os.X_OK)
                           else shutil.which("mpv"))
    if not mpv:
        parser.exit(127, "kilix video: mpv is required; install the mpv package.\n")
    if args.transport == "shm" and (args.vo != "kitty" or not named_player):
        parser.exit(2, "kilix video: --transport shm requires the Kitty renderer and kilix-mpv.\n")
    if args.transport == "shm" and any(os.environ.get(key) for key in
                                       ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "TMUX")):
        parser.exit(2, "kilix video: use inline transport in SSH or tmux sessions.\n")
    if args.transport == "shm" and wsl_kernel():
        parser.exit(2, "kilix video: use inline transport in WSL; Windows Kilix cannot "
                    "read Linux shared memory.\n")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(2, "kilix video: run in an interactive terminal (SSH requires -t).\n")
    if args.vo == "kitty" and mpv not in (str(private_player), named_player):
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
               "--audio-display=no", "--term-osd=no", "--term-status-msg=",
               "--msg-level=all=warn", "--msg-color=no",
               "--script=" + str(Path(__file__).with_name('terminal_video_errors.lua'))]
    if (args.vo == "kitty" and named_player and os.environ.get("KILIX_STORAGE_HOME")
            and supports_terminal_fullscreen(mpv)):
        command.append("--vo-kitty-terminal-fullscreen=yes")
    if (args.transport == "shm" or
            (args.transport == "auto" and args.vo == "kitty" and named_player
             and local_shared_memory_terminal())):
        command.append("--vo-kitty-use-shm=yes")
    if args.mute:
        command.append("--mute=yes")
    if wsl_kernel():
        # WSLg's PulseAudio suspends a corked sink after 5 s and can then hang
        # mpv on resume or quit. Streaming silence while paused keeps it awake.
        command.append("--audio-stream-silence=yes")
    if args.loop:
        command.append("--loop-file=inf")
    command.extend(["--", args.source])
    environment = None
    if args.vo == "kitty" and (size := cell_size()):
        # kilix-mpv sizes frames from it when the pty reports no pixels.
        environment = dict(os.environ, KILIX_MPV_CELL_SIZE=size)
    return run_player(command, environment)


if __name__ == "__main__":
    raise SystemExit(main())
