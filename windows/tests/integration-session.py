"""Qualify one owned test window without synthesizing global mouse/key input."""
import ctypes
from ctypes import wintypes
import importlib.util
import json
import os
from pathlib import Path
import time

from kitty.fast_data_types import add_timer

spec = importlib.util.spec_from_file_location('win32_window', Path(__file__).with_name('win32_window.py'))
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
root = Path(os.environ['KILIX_QUAL_RESULT'])
# Mutating window APIs can synchronously re-enter the engine's WndProc. Retain the GIL.
ui = ctypes.PyDLL('user32', use_last_error=True)
unicode_text = 'Unicode input: \u03bb \u4e2d \U0001f642'
drop_text = 'C:\\drop path with spaces\\\u03bb\u4e2d.txt'
started = False


def record(name, value):
    with (root / 'integration.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'check': name, 'value': value}, ensure_ascii=False) + '\n')


def monitors():
    result = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    @callback_type
    def collect(handle, dc, rect, data):
        result.append(native.coordinates(rect.contents))
        return True

    native.user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), callback_type, wintypes.LPARAM]
    if not native.user32.EnumDisplayMonitors(None, None, collect, 0):
        raise ctypes.WinError()
    return result


def clipboard(hwnd):
    if os.environ.get('KILIX_QUAL_CLIPBOARD') != '1':
        return {'status': 'skipped', 'reason': 'Clipboard test requires -Clipboard and an empty clipboard.'}
    user32 = ui
    if user32.CountClipboardFormats() != 0:
        return {'status': 'skipped', 'reason': 'Existing clipboard content retained; clear it explicitly before testing.'}
    from kitty.clipboard import get_clipboard_string, set_clipboard_string
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    sequence = None
    try:
        set_clipboard_string(unicode_text)
        sequence = user32.GetClipboardSequenceNumber()
        if get_clipboard_string() != unicode_text or not user32.IsClipboardFormatAvailable(13):
            raise AssertionError('Unicode clipboard round trip failed')
        return {'status': 'passed', 'format': 'CF_UNICODETEXT'}
    finally:
        # Do not erase a newer clipboard value copied by another application.
        if sequence is not None and user32.GetClipboardSequenceNumber() == sequence and user32.OpenClipboard(hwnd):
            try:
                user32.EmptyClipboard()
            finally:
                user32.CloseClipboard()


def on_resize(boss, window, data):
    global started
    if started:
        return
    started = True
    (root / 'engine-pid.txt').write_text(str(os.getpid()))
    begun = time.monotonic()
    phases = set()
    displays = monitors()
    placements = displays if os.environ.get('KILIX_QUAL_MONITORS') == '1' else []
    original = native.snapshot()
    record('window', original)
    record('monitors', displays)
    (root / 'window-handle.txt').write_text(str(original['handle']))

    def tick(timer):
        try:
            elapsed = time.monotonic() - begun
            if elapsed > 1 and 'clipboard' not in phases:
                phases.add('clipboard')
                record('clipboard', clipboard(original['handle']))
                try:
                    get_dpi = native.user32.GetDpiForWindow
                    get_dpi.argtypes = [wintypes.HWND]
                    dpi = get_dpi(original['handle'])
                    if not dpi:
                        raise ctypes.WinError()
                    record('dpi', {'status': 'observed', 'dpi': dpi})
                except AttributeError:
                    record('dpi', {'status': 'unavailable'})
            if placements and elapsed > 2:
                rect = placements.pop(0)
                ui.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                                       ctypes.c_int, ctypes.c_int, wintypes.UINT]
                if not ui.SetWindowPos(original['handle'], None, rect[0] + 20, rect[1] + 20,
                                                  min(800, rect[2] - rect[0] - 40), min(500, rect[3] - rect[1] - 40), 0x14):
                    raise ctypes.WinError()
                record('monitorMove', native.snapshot())
                return
            if elapsed > 4 and not placements and 'input' not in phases and 'KILIX_QUAL_READY' in window.as_text():
                phases.add('input')
                window.write_to_child((unicode_text + '\r').encode())
            if elapsed > 5 and 'input' in phases and 'drop' not in phases:
                phases.add('drop')
                window.on_drop({'text/plain;charset=utf-8': drop_text.encode()})
                window.write_to_child(b'\r')
                record('dropScope', 'Text drop handler to ConPTY only; Explorer/OLE file drops require manual testing.')
            if elapsed > 20 and 'timeout' not in phases:
                phases.add('timeout')
                record('error', 'Input receiver did not exit within 20 seconds')
                boss.close_windows_no_confirm(list(boss.all_windows))
        except Exception as error:
            record('error', repr(error))
            boss.close_windows_no_confirm(list(boss.all_windows))

    add_timer(tick, 0.5, True)
