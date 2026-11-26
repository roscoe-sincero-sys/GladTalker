"""Audio-preview files: one folder per engine, bounded retention, and cleanup that never touches
another running engine's work.

Several engines can share one workspace (two ports, or the end-to-end tests). A preview's WAV is
only reachable through the in-memory job of the engine that made it, so each engine writes into
its own folder, ``previews/engine-<id>/``, and holds an OS lock on ``.owner.lock`` in it for as
long as it runs. Before each new preview:

* **Its own folder:** previews whose job this engine no longer knows (dropped from its job
  history) are deleted, and only the newest ``keep`` finished previews stay. Queued and running
  previews are never touched.
* **Other engines' folders:** deleted only with proof that Studio made them and their engine has
  exited: the name must be exactly ``engine-<12 hex>``, the ``.owner.lock`` marker must exist and
  be over ``STALE_S`` old, and its lock must be free (the OS releases it when a process ends, even
  after a crash). Anything else in ``previews/``, marked or not, is never touched.

Inside its own folder an engine likewise only deletes preview folders with the names it
generates.

When an engine shuts down cleanly it deletes its own folder: nothing in it can be fetched once
its jobs are gone.

Deleting an engine folder (at shutdown, or when reclaiming a dead engine's) removes the previews
first and the ``.owner.lock`` marker last, and only once nothing else is left. On Windows a file
another program holds open (a media player, an antivirus scan) can't be deleted; its folder then
keeps its marker, which is the proof a later engine needs to retry once the file is released.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

PREFIX = "engine-"
LOCK_NAME = ".owner.lock"
ENGINE_DIR_RE = re.compile(r"^engine-[0-9a-f]{12}$")
PREVIEW_DIR_RE = re.compile(r"^\d{8}_\d{6}_\d{6}_[0-9A-Za-z]+$")  # what new_folder() names
STALE_S = 60.0


def _is_plain_dir(path: Path) -> bool:
    """A real directory, not a link to one: symlinks and Windows junctions (and other reparse
    points) are refused without following them, so cleanup never reaches outside ``previews/``."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False  # a symlink (or not a directory at all)
    return not getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


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

    def holds(self, path: Path) -> bool:
        """Whether ``path`` (not followed if it is a link) is still the very file this lock holds."""
        if self._file is None:
            return False
        try:
            mine, there = os.fstat(self._file.fileno()), os.lstat(path)
        except OSError:
            return False
        return stat.S_ISREG(there.st_mode) and (mine.st_dev, mine.st_ino) == (there.st_dev, there.st_ino)

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
                lock, self._lock = self._lock, None
                _remove_engine_folder(self.dir, lock)

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
            if not folder.is_dir() or not PREVIEW_DIR_RE.match(folder.name):
                continue  # not a preview folder this engine made: leave it
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
            if folder == self.dir or not ENGINE_DIR_RE.match(folder.name) or not _is_plain_dir(folder):
                continue  # not an engine folder Studio made (a link to one is never followed)
            marker = folder / LOCK_NAME
            try:
                if not marker.is_file() or now - marker.stat().st_mtime < self.stale_s:
                    continue  # no proof of ownership, or an engine that may still be starting up
            except OSError:
                continue  # vanished meanwhile: another engine reclaimed it
            lock = _FileLock(marker)
            try:
                if not lock.acquire(create=False):
                    continue  # its engine is running: hands off
            except FileNotFoundError:
                continue  # another engine reclaimed this folder first: nothing left to do
            except OSError:
                continue  # can't tell (permissions, a share hiccup): leave it for next time
            _remove_engine_folder(folder, lock)


def _remove_engine_folder(folder: Path, lock: _FileLock) -> bool:
    """Delete an engine folder whose ``.owner.lock`` the caller holds: the previews first, while
    still holding the lock (so no other engine starts on it too), then, only if nothing else is
    left, the marker and the folder. If a file can't be deleted (held open on Windows), the
    marker stays so a later engine can retry once it is released. Returns whether it is gone.

    Nothing is deleted unless, with the lock held, the folder is still a real directory (not a
    link) and its marker is still the very file locked. Links inside it are removed as links;
    their targets are never entered."""
    if not _is_plain_dir(folder) or not lock.holds(folder / LOCK_NAME):
        lock.release()
        return False
    try:
        for entry in folder.iterdir():
            if entry.name == LOCK_NAME:
                continue
            if _is_plain_dir(entry):
                shutil.rmtree(entry, ignore_errors=True)
            else:
                _remove_link_or_file(entry)
        payload_left = any(entry.name != LOCK_NAME for entry in folder.iterdir())
    except FileNotFoundError:
        payload_left = False  # another engine removed it meanwhile
    except OSError:
        payload_left = True  # can't tell: keep the marker
    finally:
        lock.release()
    if payload_left:
        return False
    try:
        (folder / LOCK_NAME).unlink(missing_ok=True)
        folder.rmdir()
    except OSError:
        return False  # another engine took the marker in the meantime, or the folder isn't empty
    return True


def _remove_link_or_file(entry: Path) -> None:
    """Remove a file, a symlink, or a Windows junction itself, never what a link points to."""
    try:
        entry.unlink()
    except OSError:
        try:
            os.rmdir(entry)  # a junction (or a directory symlink on Windows) is removed as a link
        except OSError:
            pass
