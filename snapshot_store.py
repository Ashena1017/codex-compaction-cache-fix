"""Per-Windows-user encrypted persistence; contains no authorization or chat text."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from ctypes import wintypes


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    if os.name != "nt":
        raise OSError("Windows DPAPI is required")
    source_buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    dll = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN, without machine scope: only this Windows user.
    if decrypt:
        fn = dll.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        fn = dll.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(source), "Codex compaction tool prefix", None, None,
                None, 1, ctypes.byref(target))
    if not ok:
        raise OSError("DPAPI operation failed")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class SnapshotStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return None
        if self.path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Snapshot file too large")
        return json.loads(crypt(self.path.read_bytes(), decrypt=True))

    def save(self, value):
        encrypted = crypt(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name+f".{os.getpid()}.tmp")
        try:
            temporary.write_bytes(encrypted)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
