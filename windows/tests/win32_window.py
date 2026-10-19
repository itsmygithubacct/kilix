"""Observe the test renderer's actual Win32 frame, independently of GLFW flags."""
import ctypes
from ctypes import wintypes
import os

# ShowWindow can synchronously re-enter the engine's Python window callbacks.
user32 = ctypes.PyDLL('user32', use_last_error=True)
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class MonitorInfo(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                ('work', wintypes.RECT), ('flags', wintypes.DWORD)]


user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]


def coordinates(rect):
    return [rect.left, rect.top, rect.right, rect.bottom]


def snapshot():
    windows, captions = [], set()

    @callback_type
    def collect(hwnd, unused):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, name, len(name))
            if name.value == 'kitty-glfw':
                windows.append(hwnd)
            elif name.value == 'kittyCaptionButtons' and user32.IsWindowVisible(hwnd):
                captions.add(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    if len(windows) != 1:
        raise RuntimeError(f'Fullscreen test needs one native window, found {len(windows)}')
    hwnd = windows[0]
    user32.EnumChildWindows(hwnd, collect, 0)
    rect, client = wintypes.RECT(), wintypes.RECT()
    info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    if not (user32.GetWindowRect(hwnd, ctypes.byref(rect)) and
            user32.GetClientRect(hwnd, ctypes.byref(client)) and
            user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))):
        raise ctypes.WinError(ctypes.get_last_error())
    return {'handle': hwnd, 'rect': coordinates(rect), 'client': coordinates(client),
            'monitor': coordinates(info.monitor), 'maximized': bool(user32.IsZoomed(hwnd)),
            'visible_captions': len(captions)}


def maximize():
    user32.ShowWindow(snapshot()['handle'], 3)
