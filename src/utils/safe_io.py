"""File helpers that behave the same on every platform.

``cv2.imread`` cannot open paths containing non-ASCII characters on Windows (it returns None),
which breaks renders whose workspace sits under e.g. ``C:\\Users\\Zoë``. These helpers read and
write through NumPy so any path the OS accepts works.
"""

import os
import shutil

import cv2
import numpy as np


def imread(path, flags=cv2.IMREAD_COLOR):
    """Drop-in for ``cv2.imread`` that handles non-ASCII paths; returns None on failure."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def imwrite(path, image) -> bool:
    """Drop-in for ``cv2.imwrite`` that handles non-ASCII paths."""
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, image)
    if not ok:
        return False
    buf.tofile(path)
    return True


def resolve_ffmpeg() -> str:
    """FFmpeg executable: ``GLADTALKER_FFMPEG`` if set, else ``ffmpeg`` from PATH."""
    return os.environ.get("GLADTALKER_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
