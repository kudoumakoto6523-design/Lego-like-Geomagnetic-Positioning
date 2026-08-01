"""Bundled command-line entry point for the GeomagMac Python backend.

PyInstaller exposes bundled resources through ``sys._MEIPASS``.  The existing
algorithm resolves its own data relative to the packaged ``Geomag`` module, so
changing into that root keeps the original CLI and data registry unchanged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def bundled_root() -> Path:
    root = getattr(sys, "_MEIPASS", None)
    if root:
        return Path(root).resolve()
    override = os.environ.get("GEOMAG_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def run() -> int:
    root = bundled_root()
    os.chdir(root)
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    print(f"GEOMAG_BACKEND_ROOT={root}", flush=True)
    from main import main as algorithm_main

    result = algorithm_main()
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(run())
