"""Route SadTalker's internal ``tqdm`` loops to Studio progress callbacks.

SadTalker reports per-frame progress only through ``tqdm`` bars (``Face Renderer:``,
``Face Enhancer:``, ``seamlessClone:``...). We swap the ``tqdm`` name in those modules for a
dispatcher: on a thread inside ``report_progress(hook)`` it calls ``hook(desc, done, total)``
after every item (the hook may raise to cancel); on any other thread it behaves like real tqdm.
"""

from __future__ import annotations

import importlib
import threading
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from tqdm import tqdm as _real_tqdm

ProgressHook = Callable[[str, int, Optional[int]], None]

HOOKED_MODULES = (
    "src.facerender.modules.make_animation",
    "src.utils.face_enhancer",
    "src.utils.preprocess",
    "src.utils.paste_pic",
    "src.face3d.extract_kp_videos_safe",
)

_local = threading.local()
_install_lock = threading.Lock()
_installed = False


class _HookedTqdm:
    def __init__(self, iterable=None, desc=None, total=None, *args, **kwargs):
        self.iterable = iterable
        self.desc = str(desc if desc is not None else kwargs.get("desc") or "").strip().rstrip(":").strip()
        if total is None and iterable is not None and hasattr(iterable, "__len__"):
            total = len(iterable)
        self.total = total
        self.n = 0

    def __iter__(self) -> Iterator:
        hook: ProgressHook = _local.hook
        hook(self.desc, 0, self.total)
        for item in self.iterable:
            yield item
            self.n += 1
            hook(self.desc, self.n, self.total)

    def __len__(self):
        return self.total or 0

    def update(self, n=1):
        self.n += n
        _local.hook(self.desc, self.n, self.total)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def set_description(self, desc=None, refresh=True):
        self.desc = desc or self.desc

    def set_postfix(self, *args, **kwargs):
        pass

    def refresh(self):
        pass


def _dispatch_tqdm(*args, **kwargs):
    if getattr(_local, "hook", None) is not None:
        return _HookedTqdm(*args, **kwargs)
    return _real_tqdm(*args, **kwargs)


def install() -> None:
    """Patch ``tqdm`` in SadTalker's modules (idempotent). Importing them loads torch."""
    global _installed
    with _install_lock:
        if _installed:
            return
        for name in HOOKED_MODULES:
            module = importlib.import_module(name)
            if getattr(module, "tqdm", None) is not None:
                module.tqdm = _dispatch_tqdm
        _installed = True


@contextmanager
def report_progress(hook: ProgressHook):
    """Send tqdm progress from SadTalker code running on *this thread* to ``hook``."""
    install()
    previous = getattr(_local, "hook", None)
    _local.hook = hook
    try:
        yield
    finally:
        _local.hook = previous
