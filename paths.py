"""
paths.py — resolve file locations so chessIQ works both from source and when
frozen into a standalone app by PyInstaller.

Two distinct notions:
  - resource_path(rel): READ-ONLY bundled assets (templates, static, schema.sql,
    sample.pgn). When frozen these live in the PyInstaller bundle dir (sys._MEIPASS);
    from source they sit next to this file.
  - app_dir(): a WRITABLE directory for the database and any user data. When frozen
    this is the folder that contains chessIQ.exe (so the data lives next to the app,
    portable and easy to find/back up); from source it's the project directory.
"""

import os
import sys
from pathlib import Path


def resource_path(rel: str) -> str:
    """Absolute path to a bundled read-only resource."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def app_dir() -> Path:
    """Writable directory for app data (next to the exe when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent
