"""ArcGIS Pro agent foundation. Importing this package never imports ArcPy."""

from pathlib import Path


def _read_version() -> str:
    """Read the repository version without importing ArcPy or other optional modules."""
    try:
        return (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return "0.1.0"


__version__ = _read_version()
