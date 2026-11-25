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
    """Readiness of each model file. A file is ``ready`` only if it has its pinned size *and*
    SHA-256. Truncated files are ``incomplete``, same-size files with bad contents are
    ``damaged``; both are repaired by the next download job. Digests are cached by size+mtime,
    so only the first check after a file changes hashes it."""
    from scripts.download_models import expected_for, is_complete, is_verified

    files = []
    for rel, url in model_manifest():
        path = Path(root) / rel
        expected = expected_for(rel)
        exists = path.is_file()
        right_size = is_complete(path, expected)
        ready = right_size and is_verified(path, expected)
        files.append({
            "path": rel,
            "name": path.name,
            "url": url,
            "exists": exists,
            "ready": ready,
            "incomplete": exists and not right_size,
            "damaged": right_size and not ready,
            "size_bytes": path.stat().st_size if exists else None,
            "expected_bytes": expected[0] if expected else None,
            "partial": any(path.parent.glob(path.name + ".*part")) if path.parent.is_dir() else False,
        })
    ready = sum(f["ready"] for f in files)
    return {"ready": ready, "total": len(files), "complete": ready == len(files), "files": files}


def download_models(ctx: JobContext, root: Path = REPO_ROOT, force: bool = False) -> dict:
    """Job body: fetch every missing model file, reporting per-file byte progress."""
    from scripts.download_models import DownloadIntegrityError, download_file, expected_for, is_verified

    manifest = model_manifest()
    todo = [(rel, url) for rel, url in manifest
            if force or not is_verified(Path(root) / rel, expected_for(rel))]
    ctx.set_stages([Path(rel).name for rel, _ in todo] or ["check"])
    if not todo:
        ctx.progress(1.0, "All models are already downloaded.", stage="check")
        return {"downloaded": [], "status": models_status(root)}

    downloaded = []
    for i, (rel, url) in enumerate(todo):
        name = Path(rel).name
        last = [0.0]

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
        except OSError as exc:  # URLError, timeouts, disk full...
            reason = getattr(exc, "reason", None) or exc.strerror or exc
            raise UserFacingError(
                f"Couldn't download {name}: {reason}. Check your internet connection and free disk "
                "space, then choose Download Models again."
            ) from exc
        downloaded.append(rel)
        ctx.log(f"Downloaded {rel}")

    return {"downloaded": downloaded, "status": models_status(root)}
