#!/usr/bin/env python3
"""Cross-platform model downloader for GladTalker (Windows, macOS, Linux).

Downloads the pretrained SadTalker checkpoints and GFPGAN/facexlib weights
using only the Python standard library (no wget required).
"""

import argparse
import hashlib
import os
import sys
import tempfile
import time
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


# Pinned (size in bytes, SHA-256) of each release asset; sizes match the upstream Content-Length.
MODEL_CHECKSUMS = {
    "checkpoints/mapping_00109-model.pth.tar": (
        155779231, "84a8642468a3fcfdd9ab6be955267043116c2bec2284686a5262f1eaf017f64c"),
    "checkpoints/mapping_00229-model.pth.tar": (
        155521183, "62a1e06006cc963220f6477438518ed86e9788226c62ae382ddc42fbcefb83f1"),
    "checkpoints/SadTalker_V0.0.2_256.safetensors": (
        725066984, "c211f5d6de003516bf1bbda9f47049a4c9c99133b1ab565c6961e5af16477bff"),
    "checkpoints/SadTalker_V0.0.2_512.safetensors": (
        725066984, "0e063f7ff5258240bdb0f7690783a7b1374e6a4a81ce8fa33456f4cd49694340"),
    "gfpgan/weights/alignment_WFLW_4HG.pth": (
        193670248, "bbfd137307a4c7debd5c283b9b0ce539466cee417ac0a155e184d857f9f2899c"),
    "gfpgan/weights/detection_Resnet50_Final.pth": (
        109497761, "6d1de9c2944f2ccddca5f5e010ea5ae64a39845a86311af6fdf30841b0a5a16d"),
    "gfpgan/weights/GFPGANv1.4.pth": (
        348632874, "e2cd4703ab14f4d01fd1383a8a8b266f9a5833dacee8e6a79d3bf21a1b6be5ad"),
    "gfpgan/weights/parsing_parsenet.pth": (
        85331193, "3d558d8d0e42c20224f13cf5a29c79eba2d59913419f945545d8cf7b72920de2"),
}


def _format_bytes(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


class DownloadIntegrityError(RuntimeError):
    """A downloaded file is truncated or does not match its pinned checksum."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_for(rel_path: str) -> Optional[tuple[int, str]]:
    return MODEL_CHECKSUMS.get(Path(rel_path).as_posix())


def is_complete(dest: Path, expected: Optional[tuple[int, str]]) -> bool:
    """Cheap readiness check: the file exists and (when pinned) has the expected size."""
    dest = Path(dest)
    if not dest.is_file():
        return False
    return expected is None or dest.stat().st_size == expected[0]


def _publish(tmp_path: Path, dest: Path, expected: Optional[tuple[int, str]], attempts: int = 10) -> None:
    """Atomically move a verified download into place.

    On Windows, replacing a file that another process (e.g. a second downloader that finished at
    the same moment) is replacing or reading fails with PermissionError. If the destination is
    already complete, the other writer won and our copy is redundant; otherwise retry briefly.
    """
    for attempt in range(attempts):
        try:
            os.replace(tmp_path, dest)
            return
        except PermissionError:
            if expected is not None and is_complete(dest, expected):
                tmp_path.unlink(missing_ok=True)
                return
            if attempt == attempts - 1:
                raise
            time.sleep(0.2)


def download_file(
    url: str,
    dest: Path,
    force: bool = False,
    progress: Optional[Callable[[int, Optional[int]], None]] = None,
    expected: Optional[tuple[int, str]] = None,
) -> None:
    """Download ``url`` to ``dest`` atomically via a uniquely named ``.part`` file.

    The file is only moved into place after its length matches ``Content-Length`` and, when
    ``expected=(size, sha256)`` is given, its size and SHA-256 match too. An existing ``dest``
    is skipped only if it is complete (so truncated files from older runs are repaired).

    Args:
        progress: Optional callback ``progress(downloaded_bytes, total_bytes_or_None)`` invoked
            after every chunk. When given, console progress output is suppressed. The callback
            may raise to abort the download; the partial file is removed.
    """
    dest = Path(dest)
    if not force and is_complete(dest, expected):
        if progress is None:
            print(f"[skip] {dest} ({_format_bytes(dest.stat().st_size)})")
        return
    if dest.exists() and not force and progress is None:
        print(f"[repair] {dest} is incomplete ({_format_bytes(dest.stat().st_size)}); downloading again")

    dest.parent.mkdir(parents=True, exist_ok=True)
    # A unique temp name means two concurrent downloaders can never write the same partial file.
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".part", dir=dest.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
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

        if total_bytes is not None and downloaded != total_bytes:
            raise DownloadIntegrityError(
                f"{dest.name}: connection ended after {downloaded} of {total_bytes} bytes. Try again."
            )
        if expected is not None:
            size, digest = expected
            if downloaded != size:
                raise DownloadIntegrityError(
                    f"{dest.name}: got {downloaded} bytes but expected {size}. Try again."
                )
            if sha256_file(tmp_path) != digest:
                raise DownloadIntegrityError(f"{dest.name}: checksum mismatch (corrupted download). Try again.")

        _publish(tmp_path, dest, expected)
        if progress is None:
            print(f"[ok] {dest}")
    except BaseException:
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
        download_file(url, args.root / rel_path, force=args.force, expected=expected_for(rel_path))

    print("All models are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
