"""Small EXE shell that opens the organized control panel with the configured Python."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main():
    source = Path(__file__).resolve().parent
    install = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
               else source.parent if source.name == "app" else source)
    settings_path = install / "data" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    python = Path(settings.get("python_executable") or sys.executable)
    pythonw = python.with_name("pythonw.exe")
    if not pythonw.is_file():
        pythonw = python
    subprocess.Popen([str(pythonw), str(install / "app" / "gui.py")], cwd=install / "app",
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # The EXE is a convenience shell; leave an actionable error if moved without its folders.
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(error), "Codex 压缩缓存修复", 0x10)
        raise SystemExit(1)
