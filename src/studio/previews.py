"""Audio-preview files: one folder per engine, bounded retention, and cleanup that never touches
another running engine's work.

Several engines can share one workspace (two ports, or the end-to-end tests). A preview's WAV is
only reachable through the in-memory job of the engine that made it, so each engine writes into
its own folder, ``previews/engine-<id>/``, and holds an OS lock on ``.owner.lock`` in it for as
long as it runs. Before each new preview:

* **Its own folder:** previews whose job this engine no longer knows (dropped from its job
  history) are deleted, and only the newest ``keep`` finished previews stay. Queued and running
  previews are never touched.
* **Other engines' folders:** deleted only if their lock can be taken, which means the owner has
  exited (the OS releases the lock when a process ends, even if it crashed). Folders whose lock
  file is younger than ``STALE_S`` are left alone, so an engine that is still setting up its
  folder is never mistaken for a dead one.

When an engine shuts down cleanly it deletes its own folder: nothing in it can be fetched once
its jobs are gone.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

PREFIX = "engine-"
LOCK_NAME = ".owner.lock"
STALE_S = 60.0


class _FileLock:
    """A non-blocking, exclusive, process-lifetime lock on one file (Windows and POSIX)."""

    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def acquire(self, create: bool = True) -> bool:
        """Take the lock without waiting. With ``create=False`` the file must already exist
        (``FileNotFoundError`` otherwise): used to test another engine's lock without leaving a
        new file behind in a folder that may be in the middle of being deleted."""
        f = open(self.path, "a+b" if create else "r+b")
        try:
            if os.name == "nt":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # byte 0; beyond EOF is allowed
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._file = f
        return True

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._file.close()
            self._file = None


class PreviewStore:
    def __init__(self, root: Path, keep: int = 5, stale_s: float = STALE_S):
        self.root = Path(root)
        self.keep = keep
        self.stale_s = stale_s
        self.dir = self.root / f"{PREFIX}{uuid.uuid4().hex[:12]}"
        self._lock: Optional[_FileLock] = None
        self._mutex = threading.Lock()
        self._seq = 0

    def new_folder(self, job_id: str, jobs) -> Path:
        """Folder for a new preview's files (not created yet), after cleaning up."""
        with self._mutex:
            self._claim()
            self._prune_own(jobs, current=job_id)
            self._reclaim_others()
            self._seq += 1
            # Timestamp, then a counter for previews started within the same second; sorts by age.
            return self.dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{self._seq:06d}_{job_id}"

    def close(self) -> None:
        with self._mutex:
            if self._lock is not None:
                self._lock.release()
                self._lock = None
                shutil.rmtree(self.dir, ignore_errors=True)

    # -- internals -------------------------------------------------------------------------------
    def _claim(self) -> None:
        if self._lock is not None:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        lock = _FileLock(self.dir / LOCK_NAME)
        if not lock.acquire():  # can't happen: the folder name is unique to this engine
            raise RuntimeError(f"Preview folder {self.dir} is locked by another process.")
        self._lock = lock

    def _prune_own(self, jobs, current: str) -> None:
        known = {j.id: j for j in jobs.list()}
        finished = []
        for folder in self.dir.iterdir():
            if not folder.is_dir():
                continue
            job_id = folder.name.rsplit("_", 1)[-1]
            job = known.get(job_id)
            if job_id == current or (job is not None and not job.done):
                continue
            if job is None:
                shutil.rmtree(folder, ignore_errors=True)  # unreachable: its job is gone
            else:
                finished.append(folder)
        finished.sort(key=lambda f: f.name)  # names start with a timestamp and a counter
        for folder in finished[:max(0, len(finished) - self.keep)]:
            shutil.rmtree(folder, ignore_errors=True)

    def _reclaim_others(self) -> None:
        now = time.time()
        for folder in self.root.iterdir():
            if not folder.is_dir() or not folder.name.startswith(PREFIX) or folder == self.dir:
                continue
            marker = folder / LOCK_NAME
            try:
                age = now - (marker if marker.exists() else folder).stat().st_mtime
            except OSError:
                continue
            if age < self.stale_s:
                continue  # possibly an engine that is just starting up
            if marker.exists():
                lock = _FileLock(marker)
                try:
                    if not lock.acquire(create=False):
                        continue  # its engine is running: hands off
                except FileNotFoundError:
                    continue  # another engine reclaimed this folder first: nothing left to do
                except OSError:
                    continue  # can't tell (permissions, a share hiccup): leave it for next time
                lock.release()
            shutil.rmtree(folder, ignore_errors=True)
