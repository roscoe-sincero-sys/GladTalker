#!/usr/bin/env python3
"""Cross-platform model downloader for GladTalker (Windows, macOS, Linux).

Downloads the pretrained SadTalker checkpoints and GFPGAN/facexlib weights
using only the Python standard library (no wget required).
"""

import argparse
import os
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Optional

MODELS = [
    (
        "checkpoints/mapping_00109-model.pth.tar",
        "https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/mapping_00109-model.pth.tar",
    ),
    (
        "checkpoints/mapping_00229-model.pth.tar",
        "https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/mapping_00229-model.pth.tar",
    ),
    (
        "checkpoints/SadTalker_V0.0.2_256.safetensors",
        "https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/SadTalker_V0.0.2_256.safetensors",
    ),
    (
        "checkpoints/SadTalker_V0.0.2_512.safetensors",
        "https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/SadTalker_V0.0.2_512.safetensors",
    ),
    (
        "gfpgan/weights/alignment_WFLW_4HG.pth",
        "https://github.com/xinntao/facexlib/releases/download/v0.1.0/alignment_WFLW_4HG.pth",
    ),
    (
        "gfpgan/weights/detection_Resnet50_Final.pth",
        "https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth",
    ),
    (
        "gfpgan/weights/GFPGANv1.4.pth",
        "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
    ),
    (
        "gfpgan/weights/parsing_parsenet.pth",
        "https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth",
    ),
]


def _format_bytes(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def download_file(
    url: str,
    dest: Path,
    force: bool = False,
    progress: Optional[Callable[[int, Optional[int]], None]] = None,
) -> None:
    """Download ``url`` to ``dest`` atomically via a ``.part`` file.

    Args:
        progress: Optional callback ``progress(downloaded_bytes, total_bytes_or_None)`` invoked
            after every chunk. When given, console progress output is suppressed. The callback
            may raise to abort the download; the partial ``.part`` file is removed.
    """
    dest = Path(dest)
    if dest.exists() and not force:
        if progress is None:
            print(f"[skip] {dest} ({_format_bytes(dest.stat().st_size)})")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(dest.suffix + ".part")
    if progress is None:
        print(f"[download] {dest.name} <- {url}")

    req = urllib.request.Request(url, headers={"User-Agent": "GladTalker-Downloader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_path, "wb") as out:
            total = resp.headers.get("Content-Length")
            total_bytes = int(total) if total and total.isdigit() else None
            downloaded = 0
            chunk_size = 1024 * 1024  # 1 MB

            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if progress is not None:
                    progress(downloaded, total_bytes)
                elif total_bytes:
                    pct = downloaded * 100.0 / total_bytes
                    sys.stdout.write(
                        f"\r  {_format_bytes(downloaded)} / {_format_bytes(total_bytes)} ({pct:5.1f}%)"
                    )
                else:
                    sys.stdout.write(f"\r  {_format_bytes(downloaded)}")
                if progress is None:
                    sys.stdout.flush()
            if progress is None:
                sys.stdout.write("\n")

        os.replace(tmp_path, dest)
        if progress is None:
            print(f"[ok] {dest}")
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Download GladTalker pretrained models.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root directory (defaults to parent of scripts/).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download files even if they already exist.",
    )
    args = parser.parse_args()

    for rel_path, url in MODELS:
        download_file(url, args.root / rel_path, force=args.force)

    print("All models are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
