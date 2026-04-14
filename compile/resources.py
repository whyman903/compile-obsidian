from __future__ import annotations

from pathlib import Path
import sys


def package_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    return package_root().joinpath(*parts)
