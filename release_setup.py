"""Initialize a release installation without changing Codex configuration."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

from configure import build_settings
from layout import INSTALL_DIR, DATA_DIR


def initialize_settings(config, python, upstream=None, provider=None, port=28615):
    destination = DATA_DIR / "settings.json"
    if destination.exists():
        raise ValueError("Existing settings.json is preserved. Do not run setup over an existing installation.")
    values = build_settings(config, upstream, provider, port)
    values.update(python_executable=str(Path(python).resolve()),
                  events_path=str(DATA_DIR / "events.jsonl"),
                  snapshot_path=str(DATA_DIR / "prefix-snapshots.dpapi"))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting settings from a concurrent setup.
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(values, ensure_ascii=False, indent=2) + "\n")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))/"config.toml")
    parser.add_argument("--upstream")
    parser.add_argument("--provider")
    parser.add_argument("--port", type=int, default=28615)
    args = parser.parse_args()
    if sys.version_info < (3, 11) or os.name != "nt":
        parser.exit(1, "Windows and Python 3.11+ are required.\n")
    if (DATA_DIR / "settings.json").exists():
        parser.exit(1, "Existing settings.json is preserved. Open CodexCompactionFix.exe instead.\n")
    try:
        # Validate before installing dependencies; never echo credential-bearing TOML errors.
        build_settings(args.config, args.upstream, args.provider, args.port)
    except (OSError, ValueError, TypeError):
        parser.exit(1, "Check your custom Codex provider and config path. If already using this proxy, restore direct routing first or pass --upstream ORIGINAL_URL.\n")
    try:
        import tkinter
        window = tkinter.Tk()
        window.withdraw()
        window.destroy()
    except Exception:
        parser.exit(1, "Python must include working Tcl/Tk. Install Python from python.org with Tcl/Tk enabled.\n")
    runtime = INSTALL_DIR / "runtime"
    python = runtime / "Scripts" / "python.exe"
    try:
        venv.EnvBuilder(with_pip=True).create(runtime)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(INSTALL_DIR/"requirements.txt")], check=True)
        subprocess.run([str(python), "-c", "import requests, tkinter, tomllib; w=tkinter.Tk(); w.withdraw(); w.destroy()"], check=True)
        initialize_settings(args.config, python, args.upstream, args.provider, args.port)
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(1, "Setup failed. Check Python, network and folder permissions, then retry. No Codex configuration was changed.\n")
    print("Setup complete. Open CodexCompactionFix.exe and click Start repair. Codex config has not been changed by setup.")


if __name__ == "__main__":
    main()
