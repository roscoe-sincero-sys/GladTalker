"""Small FFmpeg helpers (discovery, version, and error-reporting subprocess runs)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from typing import Optional, Sequence

# Don't flash a console window for each FFmpeg call when running as a windowed (Tauri) sidecar.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class FFmpegError(RuntimeError):
    pass


def find_ffmpeg() -> Optional[str]:
    return os.environ.get("GLADTALKER_FFMPEG") or shutil.which("ffmpeg")


def find_ffprobe() -> Optional[str]:
    return os.environ.get("GLADTALKER_FFPROBE") or shutil.which("ffprobe")


def require_ffmpeg() -> str:
    path = find_ffmpeg()
    if not path:
        raise FFmpegError(
            "FFmpeg was not found. Install it (Windows: `winget install Gyan.FFmpeg`, macOS: "
            "`brew install ffmpeg`, Linux: your package manager) and restart GladTalker Studio, "
            "or set GLADTALKER_FFMPEG to the ffmpeg executable."
        )
    return path


@lru_cache(maxsize=4)
def ffmpeg_version(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        out = subprocess.run(
            [path, "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.splitlines()[0] if out else ""
    # "ffmpeg version 7.1-full_build-www.gyan.dev Copyright ..." -> "7.1-full_build-www.gyan.dev"
    parts = first.split()
    return parts[2] if len(parts) >= 3 and parts[1] == "version" else (first or None)


def run_ffmpeg(args: Sequence[str], ffmpeg: Optional[str] = None) -> None:
    """Run ffmpeg quietly, raising FFmpegError with the tail of stderr on failure."""
    exe = ffmpeg or require_ffmpeg()
    proc = subprocess.run(
        [exe, "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        creationflags=NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-8:])
        raise FFmpegError(f"FFmpeg failed (exit {proc.returncode}): {tail}")
