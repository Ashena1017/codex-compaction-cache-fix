"""Wait for a target configuration file to change using Windows notifications."""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path


class WindowsConfigWatcher:
    FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
    FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 0x102
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self, path, callback):
        self.path = Path(path).resolve()
        self.callback = callback
        self.stop_event = threading.Event()
        self.handle = None
        self.thread = threading.Thread(target=self._run, name="codex-config-watch", daemon=True)

    def start(self):
        if self.path.parent.exists():
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def _run(self):
        if os.name != "nt":
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
        kernel.FindFirstChangeNotificationW.restype = wintypes.HANDLE
        kernel.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
        kernel.FindNextChangeNotification.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
        kernel.FindCloseChangeNotification.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.FindFirstChangeNotificationW(str(self.path.parent), False,
                                                     self.FILE_NOTIFY_CHANGE_FILE_NAME |
                                                     self.FILE_NOTIFY_CHANGE_LAST_WRITE)
        if handle == self.INVALID_HANDLE_VALUE:
            return
        self.handle = handle
        signature = self._signature()
        try:
            while not self.stop_event.is_set():
                result = kernel.WaitForSingleObject(handle, 500)
                if result == self.WAIT_TIMEOUT:
                    continue
                if result != self.WAIT_OBJECT_0:
                    break
                current = self._signature()
                changed = current != signature
                signature = current
                kernel.FindNextChangeNotification(handle)
                if changed and not self.stop_event.wait(0.12):
                    self.callback()
        finally:
            self.handle = None
            kernel.FindCloseChangeNotification(handle)

    def _signature(self):
        try:
            stat = self.path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None
