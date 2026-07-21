#!/usr/bin/env python3
"""Deterministic benchmark for Kilix frame presentation.

Workloads cover scrolling, cursor movement, full-motion pixels, and wake-up
after an idle run. The report is JSON so VM/image acceptance can retain and
compare median/p95 latency, CPU time, copied/wire bytes, drops, and black
frames.
"""

import argparse
import base64
import json
import random
import re
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import gfx


class ModelTerm:
    """Minimal inline Kitty model used to verify every emitted framebuffer."""

    APC = re.compile(r"\x1b_G([^;]*);([^\x1b]*)\x1b\\")

    def __init__(self, width, height):
        self.bytes = 0
        self.commands = 0
        self.width, self.height = width, height
        self.frame = None

    @staticmethod
    def _control(value):
        result = {}
        for item in value.split(","):
            key, found, raw = item.partition("=")
            if found:
                result[key] = raw
        return result

    def _finish_transfer(self, transfer):
        control, payload = transfer
        pixels = zlib.decompress(base64.b64decode(payload))
        width, height = int(control["s"]), int(control["v"])
        if control["a"] == "T":
            if (width, height) != (self.width, self.height):
                raise AssertionError("full-frame geometry changed")
            self.frame = bytearray(pixels)
            return
        if self.frame is None:
            raise AssertionError("frame edit arrived before a full frame")
        x, y = int(control["x"]), int(control["y"])
        row_bytes = width * 3
        if len(pixels) != row_bytes * height:
            raise AssertionError("frame-edit payload size mismatch")
        for row in range(height):
            source = row * row_bytes
            target = ((y + row) * self.width + x) * 3
            self.frame[target:target + row_bytes] = \
                pixels[source:source + row_bytes]

    def _compose(self, control):
        if self.frame is None:
            raise AssertionError("compose arrived before a full frame")
        sx, sy = int(control["X"]), int(control["Y"])
        dx, dy = int(control["x"]), int(control["y"])
        width, height = int(control["w"]), int(control["h"])
        row_bytes = width * 3
        source = bytearray(row_bytes * height)
        for row in range(height):
            at = ((sy + row) * self.width + sx) * 3
            source[row * row_bytes:(row + 1) * row_bytes] = \
                self.frame[at:at + row_bytes]
        for row in range(height):
            at = ((dy + row) * self.width + dx) * 3
            self.frame[at:at + row_bytes] = \
                source[row * row_bytes:(row + 1) * row_bytes]

    def write(self, value):
        self.bytes += len(value.encode("utf-8"))
        self.commands += value.count("\x1b_G")
        transfer = None
        for match in self.APC.finditer(value):
            control = self._control(match.group(1))
            payload = match.group(2)
            if transfer is not None and "t" not in control:
                transfer[1] += payload
                if control.get("m", "0") == "0":
                    self._finish_transfer(transfer)
                    transfer = None
                continue
            action = control.get("a")
            if action in ("T", "f") and control.get("t") == "d":
                transfer = [control, payload]
                if control.get("m", "0") == "0":
                    self._finish_transfer(transfer)
                    transfer = None
            elif action == "c":
                self._compose(control)
        if transfer is not None:
            raise AssertionError("unterminated graphics transfer")


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1,
                       max(0, round((len(ordered) - 1) * fraction)))]


def row_frame(width, rows):
    return b"".join(bytes((value & 255, (value * 3) & 255,
                            (value * 7) & 255)) * width for value in rows)


def cursor_frame(base, width, height, x, y):
    value = bytearray(base)
    for cy in range(y, min(height, y + 16)):
        for cx in range(x, min(width, x + 10)):
            if cx - x <= (cy - y) // 2 + 1:
                at = (cy * width + cx) * 3
                value[at:at + 3] = b"\xff\xff\xff"
    return bytes(value)


def run_workload(name, frames, width, height, scroll_hints=None):
    terminal = ModelTerm(width, height)
    presenter = gfx.FramePresenter(
        terminal, image_id=17, stream=True, enable_scroll=True,
        stream_warmup_seconds=0, stream_keyframe_seconds=0)
    durations = []
    black = 0
    mismatched = 0
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    try:
        for index, frame in enumerate(frames):
            started = time.perf_counter()
            result = presenter.present(
                frame, width, height, max(1, width // 8),
                max(1, height // 16),
                scroll=(scroll_hints[index] if scroll_hints else None))
            durations.append((time.perf_counter() - started) * 1000)
            if result.emitted:
                black += terminal.frame is None or not any(terminal.frame)
                mismatched += terminal.frame != frame
    finally:
        presenter.close()
    cpu_ms = (time.process_time() - cpu_start) * 1000
    wall_ms = (time.perf_counter() - wall_start) * 1000
    stats = presenter.stats
    return {
        "name": name,
        "frames": len(frames),
        "p50_ms": round(percentile(durations, 0.50), 3),
        "p95_ms": round(percentile(durations, 0.95), 3),
        "cpu_ms": round(cpu_ms, 3),
        "wall_ms": round(wall_ms, 3),
        "copied_bytes": stats.pixel_bytes,
        "wire_bytes": stats.wire_bytes,
        "full_frame_baseline_bytes": len(frames) * width * height * 3,
        "drops": stats.frames_dropped,
        "black_frames": black,
        "mismatched_frames": mismatched,
        "full_frames": stats.full_frames,
        "rect_updates": stats.rect_updates,
        "scroll_updates": stats.scroll_updates,
        "commands": terminal.commands,
    }


def pacing_workload(width, height, count):
    now = [0.0]
    terminal = ModelTerm(width, height)
    presenter = gfx.FramePresenter(
        terminal, image_id=19, stream=True, max_fps=30,
        stream_warmup_seconds=0, stream_keyframe_seconds=0,
        clock=lambda: now[0])
    base = bytes((20, 30, 40)) * (width * height)
    for index in range(count):
        changed = bytearray(base)
        changed[(index % (width * height)) * 3] = index & 255
        presenter.present(bytes(changed), width, height,
                          max(1, width // 8), max(1, height // 16))
        now[0] += 1 / 60
        presenter.flush()
    now[0] += 1
    presenter.flush()
    result = {
        "name": "60fps_offer_30fps_present",
        "frames": count,
        "emitted": presenter.stats.frames_emitted,
        "drops": presenter.stats.frames_dropped,
        "pending_latency_p95_ms": round(
            percentile(presenter.stats.latencies_ms, 0.95), 3),
        "wire_bytes": presenter.stats.wire_bytes,
    }
    presenter.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=180)
    parser.add_argument("--frames", type=int, default=60)
    args = parser.parse_args()
    width, height = args.width, args.height
    count = max(4, args.frames)

    initial_rows = list(range(height))
    scrolling = []
    hints = []
    rows = initial_rows
    for index in range(count):
        scrolling.append(row_frame(width, rows))
        hints.append(None if index == 0 else (0, -3))
        rows = rows[3:] + [height + index * 3 + n for n in range(3)]

    background = bytes((12, 24, 36)) * (width * height)
    cursor = [cursor_frame(background, width, height,
                           (index * 7) % max(1, width - 10),
                           (index * 5) % max(1, height - 16))
              for index in range(count)]

    rng = random.Random(20260721)
    video = [rng.randbytes(width * height * 3) for _ in range(count)]

    idle = [background] * (count - 1)
    wake = bytearray(background)
    wake[(height // 2 * width + width // 2) * 3:
         (height // 2 * width + width // 2) * 3 + 3] = b"\xff\x80\x20"
    idle.append(bytes(wake))

    report = {
        "schema": 1,
        "dimensions": [width, height],
        "workloads": [
            run_workload("scroll", scrolling, width, height, hints),
            run_workload("cursor", cursor, width, height),
            run_workload("video", video, width, height),
            run_workload("idle_to_input", idle, width, height),
            pacing_workload(width, height, count),
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
