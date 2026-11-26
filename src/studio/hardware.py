"""Compute device, accelerator, FFmpeg, and model readiness reporting."""

from __future__ import annotations

import platform
import threading
import time
from pathlib import Path
from typing import Optional

from src.studio import REPO_ROOT
from src.studio.ffmpeg import ffmpeg_version, find_ffmpeg, find_ffprobe
from src.studio.jobs import JobContext, UserFacingError


class DeviceManager:
    """Resolves the torch device once (``auto`` -> CUDA/ROCm, DirectML, MPS, CPU) and caches it."""

    def __init__(self, spec: Optional[str] = None):
        self.spec = spec or "auto"
        self._device = None
        self._error: Optional[str] = None
        self._lock = threading.Lock()

    def resolve(self):
        with self._lock:
            if self._device is None and self._error is None:
                from src.utils.device import get_device

                try:
                    self._device = get_device(device_arg=self.spec)
                except Exception as exc:  # noqa: BLE001 - surface misconfiguration in /hardware
                    self._error = str(exc)
            return self._device

    @property
    def error(self) -> Optional[str]:
        self.resolve()
        return self._error

    def info(self) -> dict:
        device = self.resolve()
        if device is None:
            return {"spec": self.spec, "type": None, "label": None, "is_gpu": False, "error": self._error}
        from src.utils.device import describe_device

        return {
            "spec": self.spec,
            "type": device.type,
            "name": str(device),
            "label": describe_device(device),
            "is_gpu": device.type != "cpu",
            "error": None,
        }


def _gpu_inventory() -> dict:
    import torch

    from src.utils.device import _import_directml

    backends = {
        "cuda": bool(torch.cuda.is_available() and not getattr(torch.version, "hip", None)),
        "rocm": bool(torch.cuda.is_available() and getattr(torch.version, "hip", None)),
        "directml": False,
        "mps": bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()),
        "cpu": True,
    }
    gpus = []
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            gpus.append({"backend": "rocm" if backends["rocm"] else "cuda", "index": i,
                         "name": torch.cuda.get_device_name(i), "spec": f"cuda:{i}"})
    dml = _import_directml()
    if dml is not None:
        try:
            if dml.is_available():
                backends["directml"] = True
                for i in range(dml.device_count()):
                    gpus.append({"backend": "directml", "index": i,
                                 "name": (dml.device_name(i) or "").replace("\x00", "").strip(), "spec": f"dml:{i}"})
        except Exception:  # noqa: BLE001 - a broken DirectML install shouldn't break /hardware
            pass
    if backends["mps"]:
        gpus.append({"backend": "mps", "index": 0, "name": "Apple GPU (Metal)", "spec": "mps"})
    return {"torch": torch.__version__, "backends": backends, "gpus": gpus}


def hardware_report(devices: DeviceManager, root: Path = REPO_ROOT) -> dict:
    ffmpeg = find_ffmpeg()
    return {
        "device": devices.info(),
        **_gpu_inventory(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ffmpeg": {
            "available": bool(ffmpeg),
            "path": ffmpeg,
            "version": ffmpeg_version(ffmpeg),
            "ffprobe": find_ffprobe(),
        },
        "models": models_status(root),
    }


# --------------------------------------------------------------------------------------------
# Pretrained models (shares the manifest with scripts/download_models.py)
# --------------------------------------------------------------------------------------------

def model_manifest() -> list[tuple[str, str]]:
    from scripts.download_models import MODELS

    return list(MODELS)


def models_status(root: Path = REPO_ROOT) -> dict:
    """Readiness of each model file (``state``: see ``verification_state``). A file is ``ready``
    only if it has its pinned size *and* SHA-256. Truncated files are ``incomplete`` and files
    whose completed check found a checksum mismatch are ``damaged``; both are repaired by the next
    download job. A file that kept changing or was locked while being checked is ``busy``: that is
    temporary, so clients should poll again (top-level ``busy`` > 0) rather than offer a repair.
    Digests are cached by size+mtime, so only the first check after a file changes hashes it."""
    from scripts.download_models import BUSY, DAMAGED, INCOMPLETE, MISSING, READY, expected_for, verification_state

    files = []
    for rel, url in model_manifest():
        path = Path(root) / rel
        expected = expected_for(rel)
        state = verification_state(path, expected)
        try:
            size = path.stat().st_size if state != MISSING else None
        except OSError:
            size = None
        files.append({
            "path": rel,
            "name": path.name,
            "url": url,
            "state": state,
            "exists": state != MISSING,
            "ready": state == READY,
            "incomplete": state == INCOMPLETE,
            "damaged": state == DAMAGED,
            "busy": state == BUSY,
            "size_bytes": size,
            "expected_bytes": expected[0] if expected else None,
            "partial": any(path.parent.glob(path.name + ".*part")) if path.parent.is_dir() else False,
        })
    ready = sum(f["ready"] for f in files)
    return {"ready": ready, "busy": sum(f["busy"] for f in files), "total": len(files),
            "complete": ready == len(files), "files": files}


def download_models(ctx: JobContext, root: Path = REPO_ROOT, force: bool = False) -> dict:
    """Job body: fetch every missing, incomplete or damaged model file (``force``: every file),
    reporting per-file byte progress. A file that is busy (being replaced, or locked by another
    program) is not something to repair: it is skipped, rechecked just before its turn and again
    if its download fails, and reported in ``deferred`` while the rest of the queue proceeds."""
    from scripts.download_models import (BUSY, READY, DownloadIntegrityError, download_file, expected_for,
                                         verification_state)

    def state_of(rel: str) -> str:
        return verification_state(Path(root) / rel, expected_for(rel))

    manifest = model_manifest()
    states = {} if force else {rel: state_of(rel) for rel, _ in manifest}
    todo = [(rel, url) for rel, url in manifest if states.get(rel) not in (READY, BUSY)]
    deferred = [rel for rel, _ in manifest if states.get(rel) == BUSY]
    ctx.set_stages([Path(rel).name for rel, _ in todo] or ["check"])

    downloaded = []
    for i, (rel, url) in enumerate(todo):
        name = Path(rel).name
        last = [0.0]
        state = state_of(rel)
        if state == BUSY:
            deferred.append(rel)
            ctx.log(f"Skipped {rel}: it is being updated or in use by another program")
            continue
        if state == READY and not force:
            continue  # another writer finished it meanwhile

        def on_chunk(done: int, total: Optional[int], i=i, name=name, last=last) -> None:
            now = time.monotonic()
            if now - last[0] < 0.25 and not (total and done >= total):
                ctx.check_cancelled()
                return
            last[0] = now
            frac = (done / total) if total else 0.0
            size = f"{done / 1e6:.1f} MB" + (f" of {total / 1e6:.1f} MB" if total else "")
            ctx.progress((i + frac) / len(todo), f"Downloading {name} ({i + 1} of {len(todo)}): {size}",
                         stage=name, bytes_done=done, bytes_total=total)

        ctx.progress(i / len(todo), f"Downloading {name} ({i + 1} of {len(todo)})…", stage=name)
        try:
            download_file(url, Path(root) / rel, force=force, progress=on_chunk, expected=expected_for(rel))
        except DownloadIntegrityError as exc:
            raise UserFacingError(str(exc)) from exc
        except OSError as exc:  # URLError, timeouts, disk full... or the file became busy
            if state_of(rel) == BUSY:
                deferred.append(rel)
                ctx.log(f"Skipped {rel}: it became busy while it was being replaced")
                continue
            reason = getattr(exc, "reason", None) or exc.strerror or exc
            raise UserFacingError(
                f"Couldn't download {name}: {reason}. Check your internet connection and free disk "
                "space, then choose Download Models again."
            ) from exc
        downloaded.append(rel)
        ctx.log(f"Downloaded {rel}")

    status = models_status(root)
    if status["complete"]:
        message = "All models are downloaded and verified."
    elif deferred:
        names = ", ".join(Path(rel).name for rel in deferred)
        message = (f"Some models are being updated or are in use by another program, so they were "
                   f"left alone: {names}. GladTalker will check them again.")
    else:
        message = "Some models still need downloading. Choose Download Models again."
    ctx.progress(1.0, message)
    return {"downloaded": downloaded, "deferred": deferred, "status": status, "message": message}
