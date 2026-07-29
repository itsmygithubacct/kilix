#!/usr/bin/env python3
"""Disposable graphical pane used for live ``kilix remote`` validation."""

from __future__ import annotations

import os
from pathlib import Path
import select
import signal
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from gfx import FramePresenter  # noqa: E402


WIDTH = 96
HEIGHT = 64
FRAME_SECONDS = 0.1


class Terminal:
    def write(self, value: str) -> None:
        sys.stdout.write(value)
        sys.stdout.flush()


def frame(number: int) -> bytes:
    """Return a visibly changing RGB test pattern."""
    pixels = bytearray(WIDTH * HEIGHT * 3)
    offset = number * 11
    cursor = 0
    for row in range(HEIGHT):
        for column in range(WIDTH):
            pixels[cursor] = (column * 3 + offset) & 0xff
            pixels[cursor + 1] = (row * 4 + offset * 2) & 0xff
            pixels[cursor + 2] = ((column ^ row) * 5 + offset * 3) & 0xff
            cursor += 3
    return bytes(pixels)


def main() -> int:
    session = os.environ.get("KITTY_PTY_BROKER_SESSION", "")
    if not session:
        print("LIVE_APP_ERROR=no broker session", flush=True)
        return 2

    running = True

    def stop(*_args) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    presenter = FramePresenter(Terminal(), image_id=2000000001, max_fps=10)
    number = 0
    pending = ""
    next_frame = time.monotonic()
    print(f"LIVE_APP_READY={session}", flush=True)
    try:
        while running:
            now = time.monotonic()
            if now >= next_frame:
                presenter.present(
                    frame(number), WIDTH, HEIGHT, columns=20, rows=8,
                    content_key="live-remote-app", now=now,
                )
                number += 1
                next_frame = now + FRAME_SECONDS
            else:
                presenter.flush(now)

            timeout = max(0.0, min(0.05, next_frame - time.monotonic()))
            readable, _, _ = select.select([sys.stdin], [], [], timeout)
            if not readable:
                continue
            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk:
                break
            pending += chunk.decode("utf-8", "replace")
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                line = line.rstrip("\r")
                print(f"LIVE_INPUT={line}", flush=True)
                if line == "stop":
                    running = False
    finally:
        presenter.close(discard=True)
        print("LIVE_APP_STOPPED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
