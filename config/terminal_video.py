#!/usr/bin/env python3
"""Play media in the caller's terminal with an explicitly selected mpv output."""
import argparse
import os
import shutil
import sys


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
    mpv = shutil.which("mpv")
    if not mpv:
        parser.exit(127, "kilix video: mpv is required; install the mpv package.\n")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(2, "kilix video: run in an interactive terminal (SSH requires -t).\n")
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
    os.execv(mpv, command)


if __name__ == "__main__":
    main()
