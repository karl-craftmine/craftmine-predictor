"""Filesystem roots that work both from source and from a PyInstaller exe.

When frozen one-file, PyInstaller extracts bundled files to a temp dir
(``sys._MEIPASS``) that is wiped on exit, while the ``.exe`` itself sits in a
normal, persistent folder. So we *read* bundled resources (index.html, the
API-Football key) from ``_MEIPASS`` but *write* the cache next to the ``.exe``
so it survives between launches. Running from source, both are the repo root.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEV_ROOT = Path(__file__).resolve().parent.parent   # …/predictor


def data_dir() -> Path:
    """Writable base dir: next to the .exe when frozen, else the project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _DEV_ROOT


def resource_dir() -> Path:
    """Read-only base dir for files bundled into the build.

    PyInstaller extracts those under ``sys._MEIPASS``; in dev it's the repo root.
    """
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else _DEV_ROOT
