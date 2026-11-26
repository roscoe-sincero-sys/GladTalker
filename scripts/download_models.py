#!/usr/bin/env python3
"""Cross-platform model downloader for GladTalker (Windows, macOS, Linux).

Downloads the pretrained SadTalker checkpoints and GFPGAN/facexlib weights
using only the Python standard library (no wget required).
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
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
    """Size-only pre-check: the file exists and (when pinned) has the expected size.
    Use is_verified() to decide whether a file is actually usable."""
    dest = Path(dest)
    if not dest.is_file():
        return False
    return expected is None or dest.stat().st_size == expected[0]


# SHA-256 results are cached per directory, keyed by file size + modification time, so readiness
# checks hash each model once rather than on every status poll. Like git's "racy clean" rule, an
# entry is only trusted if the file was last modified comfortably *before* it was hashed: two
# writes inside one filesystem timestamp tick share an mtime, so a fresher file is re-hashed.
VERIFY_CACHE_NAME = ".gladtalker_verified.json"
RACY_WINDOW_NS = 2_000_000_000
_cache_lock = threading.Lock()


def _cache_path(directory: Path) -> Path:
    return Path(directory) / VERIFY_CACHE_NAME


def _load_cache(directory: Path) -> dict:
    try:
        with open(_cache_path(directory), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class FileChangedError(OSError):
    """The file at a path kept changing while it was being hashed."""


def _identity(st: os.stat_result) -> tuple[int, int, int]:
    return (st.st_size, st.st_mtime_ns, st.st_ino)


def _record_digest(dest: Path, digest: str, snapshot: os.stat_result) -> None:
    """Remember ``digest`` for the exact file described by ``snapshot`` (best effort). The entry
    stores that file's size, mtime and file ID, so it can never describe a different file."""
    dest = Path(dest)
    try:
        with _cache_lock:
            cache = _load_cache(dest.parent)
            cache[dest.name] = {"size": snapshot.st_size, "mtime_ns": snapshot.st_mtime_ns,
                                "ino": snapshot.st_ino, "sha256": digest,
                                "verified_at_ns": time.time_ns()}
            fd, tmp = tempfile.mkstemp(prefix=VERIFY_CACHE_NAME + ".", dir=dest.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=1, sort_keys=True)
            os.replace(tmp, _cache_path(dest.parent))
    except OSError:
        pass  # a read-only model folder just means we re-hash next time


def _hash_open_file(f) -> str:
    h = hashlib.sha256()
    for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
        h.update(chunk)
    return h.hexdigest()


def hash_snapshot(dest: Path, attempts: int = 3) -> tuple[str, os.stat_result]:
    """Hash ``dest`` and return ``(digest, snapshot)`` where ``snapshot`` is the metadata of the
    very file that was hashed: taken from the open handle before and after reading, and still
    what the path points to afterwards. If the file is replaced or modified meanwhile, retry."""
    dest = Path(dest)
    for _ in range(attempts):
        with open(dest, "rb") as f:
            before = os.fstat(f.fileno())
            digest = _hash_open_file(f)
            after = os.fstat(f.fileno())
        try:
            current = os.stat(dest)
        except FileNotFoundError:
            current = None
        if current is not None and _identity(before) == _identity(after) == _identity(current):
            return digest, before
    raise FileChangedError(f"{dest.name} kept changing while it was being verified; try again.")


def _cached_digest(dest: Path, st: os.stat_result) -> Optional[str]:
    """The cached SHA-256 for the exact file ``st`` describes (size, mtime and file ID), if it
    was hashed comfortably after its last modification; otherwise None."""
    entry = _load_cache(Path(dest).parent).get(Path(dest).name)
    if (entry and (entry.get("size"), entry.get("mtime_ns"), entry.get("ino")) == _identity(st)
            and st.st_mtime_ns < entry.get("verified_at_ns", 0) - RACY_WINDOW_NS):
        return entry["sha256"]
    return None


def file_digest(dest: Path, use_cache: bool = True) -> str:
    dest = Path(dest)
    if use_cache:
        cached = _cached_digest(dest, dest.stat())
        if cached is not None:
            return cached
    digest, snapshot = hash_snapshot(dest)
    _record_digest(dest, digest, snapshot)
    return digest


def is_verified(dest: Path, expected: Optional[tuple[int, str]], use_cache: bool = True) -> bool:
    """True if ``dest`` exists with the pinned size *and* SHA-256 (no pin: it just exists).
    A same-size file with damaged contents is not verified."""
    if not is_complete(dest, expected):
        return False
    if expected is None:
        return True
    return file_digest(dest, use_cache=use_cache) == expected[1]


class DestinationBusyError(PermissionError):
    """The destination file is in use (being replaced, or locked by another program), so it can't
    be checked or replaced right now. Unlike a network or disk failure, this is worth retrying."""


def _whole_file_readable(f, size: int) -> bool:
    """Whether no other program holds a lock that would stop reading any part of the open file.

    On Windows, a byte-range lock anywhere in the file makes reads of that range fail, so reading
    the first byte is not enough. Take (and at once release) a *shared* lock on the whole file
    without waiting: it is refused exactly when someone holds an exclusive lock on part of it, and
    other readers' shared locks don't get in its way. Elsewhere, locks are advisory and never stop
    a read, so the first byte is all there is to check."""
    if f.read(1) == b"" and size:
        return False  # shorter than its metadata says: being rewritten
    if sys.platform != "win32":
        return True
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    kernel32.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.POINTER(_Overlapped)]
    handle = msvcrt.get_osfhandle(f.fileno())
    length = max(size, 1)
    low, high = length & 0xFFFFFFFF, length >> 32
    overlapped = _Overlapped()
    lockfile_fail_immediately = 0x1  # and not LOCKFILE_EXCLUSIVE_LOCK: a shared lock
    if not kernel32.LockFileEx(handle, lockfile_fail_immediately, 0, low, high, ctypes.byref(overlapped)):
        return False
    kernel32.UnlockFileEx(handle, 0, low, high, ctypes.byref(overlapped))
    return True


MISSING, INCOMPLETE, READY, DAMAGED, BUSY = "missing", "incomplete", "ready", "damaged", "busy"


def verification_state(dest: Path, expected: Optional[tuple[int, str]], use_cache: bool = True,
                       attempts: int = 3) -> str:
    """Classify ``dest`` without raising, from one stable snapshot of one open file:

    * ``missing``: nothing is there;
    * ``incomplete``: the file has the wrong size;
    * ``ready``: the file can be read and has the pinned size and SHA-256 (no pin: it can be read);
    * ``damaged``: a completed check of an unchanging file found a checksum mismatch;
    * ``busy``: the check couldn't finish because the file kept changing or being replaced, was
      locked by another program, or disappeared after it was seen. This is temporary: check
      again shortly rather than repairing.

    The size, the cache lookup and the digest all come from the same open handle, and the result
    only counts if the path still names that file afterwards; otherwise it is retried. Even a
    cached digest needs the whole file to be readable (no other program's lock on any part of it),
    so a locked file is never reported ready."""
    dest = Path(dest)
    for attempt in range(attempts):
        cached = None
        try:
            f = open(dest, "rb")
        except FileNotFoundError:
            return MISSING if attempt == 0 else BUSY  # it was there a moment ago: being replaced
        except OSError:
            if dest.is_dir():  # Windows reports opening a folder as "access denied"
                return MISSING
            return BUSY  # sharing violation or other lock
        try:
            with f:
                before = os.fstat(f.fileno())
                if expected is not None and before.st_size != expected[0]:
                    state, digest, after = INCOMPLETE, None, before
                else:
                    if use_cache and expected is not None:
                        cached = _cached_digest(dest, before)
                    if cached is not None or expected is None:
                        # Nothing to hash, but the file must still be readable end to end now.
                        if not _whole_file_readable(f, before.st_size):
                            continue
                        digest, after = cached, before
                    else:
                        digest = _hash_open_file(f)
                        after = os.fstat(f.fileno())
                    state = READY if expected is None or digest == expected[1] else DAMAGED
            current = os.stat(dest)
        except OSError:  # a lock on the contents, or the path vanished after reading
            continue
        if _identity(before) == _identity(after) == _identity(current):
            if digest is not None and cached is None:
                _record_digest(dest, digest, before)
            return state
    return BUSY


def _verified_quietly(dest: Path, expected: tuple[int, str]) -> bool:
    """Fresh (uncached) verification that treats "file busy" as not verified yet: on Windows the
    destination can't be opened while another writer is mid-replace."""
    try:
        return is_verified(dest, expected, use_cache=False)
    except OSError:
        return False


def _is_read_only(path: Path) -> bool:
    """Windows' read-only attribute: replacing the file is refused until it is cleared."""
    import stat

    try:
        return bool(getattr(os.stat(path), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY)
    except OSError:
        return False


def _held_by_another_program(path: Path) -> bool:
    """Whether another handle stops ``path`` from being replaced right now (Windows), as opposed to
    a permission problem: both make ``os.replace`` fail with "access denied". Opening the file for
    DELETE access while sharing everything changes nothing, and is refused with a sharing violation
    only when someone else holds it open without allowing deletion."""
    if sys.platform != "win32":
        return False  # elsewhere an open file never blocks a rename: a refusal is about permissions
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    delete, share_all, open_existing, normal = 0x00010000, 0x7, 3, 0x80
    handle = kernel32.CreateFileW(str(path), delete, share_all, None, open_existing, normal, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        sharing_violation, lock_violation = 32, 33
        return ctypes.get_last_error() in (sharing_violation, lock_violation)
    kernel32.CloseHandle(handle)
    return False


def _publish(tmp_path: Path, dest: Path, expected: Optional[tuple[int, str]], attempts: int = 10) -> None:
    """Atomically move a verified download into place.

    On Windows, replacing a file that another process (e.g. a second downloader that finished at
    the same moment) is replacing or reading fails with PermissionError. If the destination is
    already fully verified (size and checksum, freshly hashed), the other writer won and our copy
    is redundant; otherwise retry briefly and finally raise, so a damaged file that cannot be
    replaced is never reported as repaired. The final error is DestinationBusyError only when
    another program is confirmed to be holding the file; a read-only file or any other permission
    problem raises the original PermissionError at once, since waiting won't fix it.
    """
    for attempt in range(attempts):
        try:
            os.replace(tmp_path, dest)
            return
        except PermissionError as exc:
            if expected is not None and _verified_quietly(dest, expected):
                tmp_path.unlink(missing_ok=True)
                return
            if _is_read_only(dest):
                raise
            if attempt == attempts - 1:
                if _held_by_another_program(dest):
                    raise DestinationBusyError(
                        13, f"{dest.name} is in use by another program, so it couldn't be replaced",
                        str(dest)) from exc
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
    is skipped only if it verifies (so truncated or damaged files from older runs are repaired).

    Raises DestinationBusyError only when the destination itself is in use: it can't be checked
    before downloading, or can't be replaced afterwards. Network, integrity and
    temporary-file failures raise their own errors.

    Args:
        progress: Optional callback ``progress(downloaded_bytes, total_bytes_or_None)`` invoked
            after every chunk. When given, console progress output is suppressed. The callback
            may raise to abort the download; the partial file is removed.
    """
    dest = Path(dest)
    state = None if force else verification_state(dest, expected)
    if state == READY:
        if progress is None:
            print(f"[skip] {dest} (verified)")
        return
    if state == BUSY:
        raise DestinationBusyError(
            13, f"{dest.name} is being updated or is in use by another program; try again shortly", str(dest))
    if dest.exists() and not force and progress is None:
        print(f"[repair] {dest} is incomplete or damaged; downloading again")

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
