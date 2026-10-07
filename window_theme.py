"""Apply Windows title bar colors to the control panel's own top-level window."""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


def colorref(color):
    red, green, blue = (int(color[index:index+2], 16) for index in (1, 3, 5))
    return red | (green << 8) | (blue << 16)


def apply_titlebar(window, dark, background, foreground):
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        dwm.DwmSetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD,
                                             ctypes.c_void_p, wintypes.DWORD)
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        hwnd = user32.GetAncestor(window.winfo_id(), 2)  # Tk's outer window.
        if not hwnd:
            return False
        enabled = wintypes.BOOL(dark)
        status = dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
        if status != 0:
            status = dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))
        # Windows 11 supports explicit caption/text colors, including inactive windows.
        for attribute, color in ((35, background), (36, foreground)):
            value = wintypes.DWORD(colorref(color))
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
        return status == 0
    except (OSError, AttributeError):
        return False
