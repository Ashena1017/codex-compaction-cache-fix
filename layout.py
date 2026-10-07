"""Resolve install paths for both a source checkout and the organized install."""
from pathlib import Path
import sys


if getattr(sys, "frozen", False):
    INSTALL_DIR = Path(sys.executable).resolve().parent
    APP_DIR = INSTALL_DIR / "app"
    DATA_DIR = INSTALL_DIR / "data"
elif Path(__file__).resolve().parent.name == "app":
    APP_DIR = Path(__file__).resolve().parent
    INSTALL_DIR = APP_DIR.parent
    DATA_DIR = INSTALL_DIR / "data"
else:
    APP_DIR = Path(__file__).resolve().parent
    INSTALL_DIR = APP_DIR
    DATA_DIR = INSTALL_DIR

SETTINGS_FILE = DATA_DIR / "settings.json"
