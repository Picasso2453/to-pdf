"""Locate bundled assets both from source and inside the PyInstaller exe."""

import sys
from pathlib import Path


def asset_path(name: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return str(base / "image_to_pdf" / "assets" / name)
