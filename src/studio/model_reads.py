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

_active = threading.local()  # .roots: model roots of the explain_checkpoint_reads() blocks on this thread
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


def _pin_for(target: Path, root: Path) -> Optional[tuple[int, str]]:
    """The pin of the model file under ``root`` that ``target`` names (however it is spelled), or
    None if it isn't one of the pinned model files."""
    from scripts.download_models import MODEL_CHECKSUMS

    for rel, expected in MODEL_CHECKSUMS.items():
        if _same_path(target, root / rel):
            return expected
    return None


def _explain(path, exc: BaseException) -> None:
    """Raise RetryLaterError or UserFacingError for a failed read of a pinned model file under one
    of this thread's roots; return quietly if the file is healthy or isn't a model file."""
    from scripts.download_models import BUSY, MISSING, READY, verification_state

    target = Path(os.fspath(path))
    for root in reversed(getattr(_active, "roots", [])):
        expected = _pin_for(target, Path(root))
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
            if getattr(_active, "roots", None):
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
def explain_checkpoint_reads(root: Optional[Path] = None):
    """Within this block (on this thread), a failed read of a pinned model file under ``root``
    (default: the repository) is explained as busy or damaged when it is; see the module docs."""
    _install()
    roots = getattr(_active, "roots", None)
    if roots is None:
        roots = _active.roots = []
    roots.append(Path(root or REPO_ROOT))
    try:
        yield
    finally:
        roots.pop()
