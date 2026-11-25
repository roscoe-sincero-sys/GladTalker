"""GladTalker version metadata (Semantic Versioning 2.0.0)."""

from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
__version__ = _VERSION_FILE.read_text(encoding="utf-8").strip() if _VERSION_FILE.is_file() else "0.1.0"
