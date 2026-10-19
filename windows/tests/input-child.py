"""Capture native console input for the isolated Win32 message test."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time

root = Path(os.environ['KILIX_TEST_RESULT_DIR'])
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetStdHandle.argtypes = [wintypes.DWORD]
kernel.GetStdHandle.restype = wintypes.HANDLE
kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.ReadConsoleW.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
handle = kernel.GetStdHandle(-10 & 0xffffffff)
mode = wintypes.DWORD()
if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
    raise ctypes.WinError(ctypes.get_last_error())
if not kernel.SetConsoleMode(handle, 0x200):  # Raw virtual terminal input.
    raise ctypes.WinError(ctypes.get_last_error())
try:
    print('KILIX_INPUT_READY\nCursor position for input method test: ', end='', flush=True)
    (root / 'input-ready').write_text('ready')
    units = bytearray()
    while True:
        buf = (ctypes.c_uint16 * 1024)()
        count = wintypes.DWORD()
        if not kernel.ReadConsoleW(handle, buf, len(buf), ctypes.byref(count), None):
            raise ctypes.WinError(ctypes.get_last_error())
        chunk = ctypes.string_at(buf, count.value * 2)
        units.extend(chunk)
        if any(buf[i] in (10, 13) for i in range(count.value)):
            break
    text = units.decode('utf-16-le')
    (root / 'input-received.json').write_text(json.dumps({'text': text}), encoding='utf-8')
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not (root / 'input-pass.txt').exists() and not (root / 'input-error.json').exists():
        time.sleep(0.1)
finally:
    kernel.SetConsoleMode(handle, mode.value)
