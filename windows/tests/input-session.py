"""Exercise Win32 messages against only the renderer hosting this watcher."""
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
# Synchronous messages re-enter the engine, which can call Python. Keep the GIL
# while inside SendMessage, just as the native event loop does.
user = ctypes.PyDLL('user32', use_last_error=True)
user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user.SendMessageW.restype = ctypes.c_ssize_t
user.GetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
user.SetKeyboardState.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]

root = Path(os.environ['KILIX_TEST_RESULT_DIR'])
started = False


class CompositionForm(ctypes.Structure):
    _fields_ = [('style', wintypes.DWORD), ('point', wintypes.POINT), ('area', wintypes.RECT)]


def exercise(window):
    hwnd = native.snapshot()['handle']
    saved = (ctypes.c_ubyte * 256)()
    if not user.GetKeyboardState(saved):
        raise ctypes.WinError(ctypes.get_last_error())

    def modifiers(*keys):
        state = (ctypes.c_ubyte * 256)()
        for key in keys:
            state[key] = 0x80
        if not user.SetKeyboardState(state):
            raise ctypes.WinError(ctypes.get_last_error())

    def send(message, value):
        with (root / 'input-events.log').open('a') as stream:
            stream.write(f'{message:x} {value:x}\n')
        return user.SendMessageW(hwnd, message, value, 1)
    try:
        modifiers()
        if send(0x109, 0xffff) != 1:
            raise AssertionError('WM_UNICHAR capability response')
        for char in 'café 日本語 ':
            send(0x102, ord(char))
        send(0x102, 0xd83d)
        send(0x102, 0xde80)  # Supplementary plane from UTF-16.
        send(0x109, 0x1f642)  # Supplementary plane from UTF-32.
        send(0x109, 0x110000)  # Invalid scalar must not reach the child.
        send(0x102, 0xd83d)
        send(0x102, ord('Z'))
        send(0x102, 0xde80)  # Stale high surrogate must not combine.
        modifiers(0x11, 0xa2, 0x12, 0xa5)  # Ctrl + right Alt (AltGr).
        send(0x100, ord('Q'))
        send(0x102, ord('@'))
        send(0x101, ord('Q'))
        modifiers(0x12, 0xa4)  # Ordinary Alt shortcut produces one escape.
        send(0x100, ord('X'))
        send(0x106, ord('x'))
        send(0x101, ord('X'))
        modifiers()
        imm = ctypes.PyDLL('imm32', use_last_error=True)
        imm.ImmGetContext.argtypes = [wintypes.HWND]
        imm.ImmGetContext.restype = wintypes.HANDLE
        imm.ImmGetCompositionWindow.argtypes = [wintypes.HANDLE, ctypes.POINTER(CompositionForm)]
        imm.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
        send(0x10d, 0)  # Begin system composition; update cursor coordinates.
        context = imm.ImmGetContext(hwnd)
        ime = {'status': 'unavailable', 'reason': 'No input method context on this system'}
        if context:
            try:
                form = CompositionForm()
                if not imm.ImmGetCompositionWindow(context, ctypes.byref(form)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if form.style != 2:
                    raise AssertionError(('composition style', form.style))
                if form.point.x < 0 or form.point.y <= 0:
                    raise AssertionError(('cursor position', form.point.x, form.point.y))
                ime = {'status': 'passed', 'position': [form.point.x, form.point.y]}
            finally:
                imm.ImmReleaseContext(hwnd, context)
        send(0x10e, 0)  # End only this synthetic composition.
        (root / 'input-message-results.json').write_text(json.dumps({'ime': ime}))
        send(0x100, 0x0d)
        send(0x101, 0x0d)
    finally:
        user.SetKeyboardState(saved)


def on_resize(boss, window, data):
    global started
    if started or os.environ.get('KILIX_TEST_ISOLATED') != '1':
        return
    started = True
    (root / 'engine-pid.txt').write_text(str(os.getpid()))
    beginning = time.monotonic()
    sent = False

    def tick(timer):
        nonlocal sent
        try:
            if not sent and (root / 'input-ready').exists() and time.monotonic() - beginning > 2:
                sent = True
                exercise(window)
            if (root / 'input-received.json').exists():
                actual = json.loads((root / 'input-received.json').read_text())['text']
                expected = 'café 日本語 🚀🙂Z@\x1bx\r'
                if actual != expected:
                    raise AssertionError({'expected': expected, 'actual': actual})
                (root / 'input-pass.txt').write_text('Unicode, surrogate recovery, AltGr, Alt shortcut: PASS\n')
                boss.close_windows_no_confirm(list(boss.all_windows))
            elif time.monotonic() - beginning > 20:
                raise TimeoutError('Native input test did not complete')
        except Exception as exc:
            (root / 'input-error.json').write_text(json.dumps({'error': repr(exc)}))
            boss.close_windows_no_confirm(list(boss.all_windows))

    add_timer(tick, 0.2, True)


def on_close(boss, window, data):
    (root / 'input-close.json').write_text(json.dumps({'text': window.as_text()}), encoding='utf-8')
