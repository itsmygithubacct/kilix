"""Input injection for Kilix's private Weston seat."""

from __future__ import annotations

import socket
from pathlib import Path


KEYS = {
    "Escape": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7,
    "7": 8, "8": 9, "9": 10, "0": 11, "-": 12, "=": 13,
    "Backspace": 14, "Tab": 15, "q": 16, "w": 17, "e": 18, "r": 19,
    "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "[": 26, "]": 27, "Enter": 28, "a": 30, "s": 31, "d": 32,
    "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    ";": 39, "'": 40, "`": 41, "\\": 43, "z": 44, "x": 45,
    "c": 46, "v": 47, "b": 48, "n": 49, "m": 50, ",": 51,
    ".": 52, "/": 53, " ": 57, "F1": 59, "F2": 60, "F3": 61,
    "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66, "F9": 67,
    "F10": 68, "Home": 102, "ArrowUp": 103, "PageUp": 104,
    "ArrowLeft": 105, "ArrowRight": 106, "End": 107, "ArrowDown": 108,
    "PageDown": 109, "Insert": 110, "Delete": 111, "F11": 87, "F12": 88,
}
MODIFIERS = {57441: 42, 57442: 29, 57443: 56, 57444: 125,
             57447: 54, 57448: 97, 57449: 100, 57450: 126}
BUTTONS = {1: 0x110, 2: 0x112, 3: 0x111}


class Injector:
    """Send compact newline-delimited events to a private Weston module."""

    def __init__(self, path: str | Path, app_w: int, app_h: int,
                 offset_x: int = 0, offset_y: int = 0):
        self.app_w, self.app_h = app_w, app_h
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(str(path))
        self._send(f"o {max(0, int(offset_x))} {max(0, int(offset_y))}")

    def _send(self, event: str) -> None:
        self.socket.sendall(event.encode("ascii") + b"\n")

    def position(self, x: int = 0, y: int = 0) -> None:
        """Move the shared seat inside this injector's assigned output."""
        self._send(f"m {min(self.app_w - 1, max(0, int(x)))} "
                   f"{min(self.app_h - 1, max(0, int(y)))}")

    def frame_rate(self, fps: int) -> None:
        """Set this output's damage-driven compositor ceiling."""
        self._send(f"r {min(30, max(1, int(fps)))} 0")

    @staticmethod
    def code_for(key) -> int:
        if len(key) == 1:
            codepoint = ord(key)
            if codepoint in MODIFIERS:
                return MODIFIERS[codepoint]
            key = key.casefold()
        return KEYS.get(key, 0)

    def key(self, key, etype: int) -> bool:
        code = self.code_for(key)
        if not code or etype not in (1, 3):
            return False
        self._send(f"k {code} {1 if etype == 1 else 0}")
        return True

    def paste(self, text: str) -> None:
        for char in text:
            key = "Enter" if char == "\n" else char
            code = self.code_for(key)
            if code:
                self._send(f"k {code} 1")
                self._send(f"k {code} 0")

    def mouse(self, ev, box) -> None:
        bx, by, bw, bh = box
        x = min(self.app_w - 1, max(0, round((ev["x"] - bx) * self.app_w / bw)))
        y = min(self.app_h - 1, max(0, round((ev["y"] - by) * self.app_h / bh)))
        self._send(f"m {x} {y}")
        button = ev["b"]
        if button & 64:
            self._send(f"a 0 {-1 if (button & 3) == 0 else 1}")
        elif not button & 32:
            number = (button & 3) + 1
            if number in BUTTONS:
                self._send(f"b {BUTTONS[number]} {1 if ev['press'] else 0}")

    def close(self) -> None:
        self.socket.close()
