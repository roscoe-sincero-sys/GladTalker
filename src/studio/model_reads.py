"""Explain a failed read of a model file, and nothing else.

Model weights are read by ``torch.load`` (SadTalker's ``.pth`` files, and facexlib, GFPGAN and
spandrel underneath) and ``safetensors.torch.load_file`` (SadTalker's ``.safetensors``). Inside
``explain_checkpoint_reads()``, if one of those calls fails for a pinned model file, that one file
is checked again without the digest cache:

* busy (another program locked part of it, or it is being replaced): RetryLaterError, so Auto-Cast
  answers 503 with Retry-After and a render job says to try again in a moment;
* missing, incomplete or damaged: a UserFacingError saying to choose Download Models;
* healthy: the original error, unchanged.

Anything else that fails meanwhile (moving a model to the GPU and running out of memory, encoding,
disk space) is not a model read, so it keeps its own message and nothing is rehashed. The wrappers
are installed once and do nothing unless the calling thread is inside the context manager.
"""

from __future__ import annotations

import functools
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from src.studio import REPO_ROOT
from src.studio.jobs import RetryLaterError, UserFacingError

_active = threading.local()  # .scopes: what each explain_checkpoint_reads() block on this thread covers
_install_lock = threading.Lock()
_installed = False


def _same_path(a: Path, b: Path) -> bool:
    """Whether two spellings name the same file: compared the way the platform compares paths
    (on Windows, ignoring case and slash direction), then by file identity for anything else
    (symlinks, short names), without resolving either spelling."""
    if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _pin_under_root(root: Path):
    """A scope covering every pinned model file under ``root``, however its path is spelled."""
    def pin_for(target: Path) -> Optional[tuple[int, str]]:
        from scripts.download_models import MODEL_CHECKSUMS

        for rel, expected in MODEL_CHECKSUMS.items():
            if _same_path(target, root / rel):
                return expected
        return None

    return pin_for


def _pin_of_files(files: dict):
    """A scope covering exactly these files, each with the pin it was checked against."""
    def pin_for(target: Path) -> Optional[tuple[int, str]]:
        return next((expected for path, expected in files.items() if _same_path(target, Path(path))), None)

    return pin_for


def _explain(path, exc: BaseException) -> None:
    """Raise RetryLaterError or UserFacingError for a failed read of a model file covered by one of
    this thread's blocks; return quietly if the file is healthy or isn't covered."""
    from scripts.download_models import BUSY, MISSING, READY, verification_state

    target = Path(os.fspath(path))
    for pin_for in reversed(getattr(_active, "scopes", [])):
        expected = pin_for(target)
        if expected is None:
            continue
        state = verification_state(target, expected, use_cache=False)
        if state == BUSY:
            raise RetryLaterError(
                f"The model file {target.name} is being updated or is in use by another program, so it "
                "couldn't be read. Try again in a moment."
            ) from exc
        if state != READY:
            problem = "isn't downloaded" if state == MISSING else f"is {state}"
            raise UserFacingError(
                f"The model file {target.name} {problem}. Choose Download Models to repair it, then try again."
            ) from exc
        return


def _explained(read, path_param: str):
    """``read`` with every argument passed through unchanged; on failure, the file it was asked to
    read (the ``path_param`` argument, given by position or by name) is explained."""
    @functools.wraps(read)
    def read_model_file(*args, **kwargs):
        try:
            return read(*args, **kwargs)
        except Exception as exc:
            if getattr(_active, "scopes", None):
                source = args[0] if args else kwargs.get(path_param)
                if isinstance(source, (str, os.PathLike)):
                    _explain(source, exc)
            raise

    read_model_file.__explains_model_reads__ = True
    return read_model_file


def _install() -> None:
    global _installed
    with _install_lock:
        if _installed:
            return
        import safetensors.torch
        import torch

        # The file is torch.load's ``f`` and safetensors.torch.load_file's ``filename``.
        for module, name, path_param in ((torch, "load", "f"), (safetensors.torch, "load_file", "filename")):
            current = getattr(module, name)
            if not getattr(current, "__explains_model_reads__", False):
                setattr(module, name, _explained(current, path_param))
        _installed = True


@contextmanager
def explain_checkpoint_reads(root: Optional[Path] = None, files: Optional[dict] = None):
    """Within this block (on this thread), a failed read of a covered model file is explained as
    busy or damaged when it is; see the module docs. ``files`` ({path: (size, sha256)}) covers
    exactly those files with those pins, e.g. the ones a precheck just verified, wherever they are;
    otherwise every pinned model file under ``root`` (default: the repository) is covered."""
    _install()
    scopes = getattr(_active, "scopes", None)
    if scopes is None:
        scopes = _active.scopes = []
    scopes.append(_pin_of_files(files) if files is not None else _pin_under_root(Path(root or REPO_ROOT)))
    try:
        yield
    finally:
        scopes.pop()
