"""Stop the engine when the program that started it goes away.

The desktop app starts the engine as a child process and stops it when it closes. If the app
crashes, or is killed from Task Manager, nothing would stop the engine, and it would keep its
port, its GPU memory, and possibly a render. ``studio_server.py --exit-with-parent PID`` watches
that process and shuts the engine down when it ends.

This matters on Windows in particular: a venv's ``python.exe`` is a small launcher that starts
the real interpreter as *its* child, so stopping the launcher alone can leave the real engine
running. Watching the app's own PID covers that case too.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

log = logging.getLogger("gladtalker.studio")

_SYNCHRONIZE = 0x00100000
_INFINITE = 0xFFFFFFFF
_ERROR_INVALID_PARAMETER = 87  # OpenProcess: no process with that id


def wait_for_exit(pid: int, poll_s: float = 1.0) -> bool:
    """Block until process ``pid`` has ended. Returns False if it can't be watched (no access),
    in which case the caller should keep running rather than assume the parent is gone."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return ctypes.get_last_error() == _ERROR_INVALID_PARAMETER  # gone already, or no access
        try:
            kernel32.WaitForSingleObject(handle, _INFINITE)
        finally:
            kernel32.CloseHandle(handle)
        return True
    while True:
        try:
            os.kill(pid, 0)  # signal 0: only checks that the process exists (POSIX)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # exists, owned by someone else
        time.sleep(poll_s)


def watch_parent(pid: int, on_exit: Callable[[], None]) -> threading.Thread:
    """Call ``on_exit`` (from a background thread) once process ``pid`` has ended."""

    def run():
        if wait_for_exit(pid):
            log.warning("The program that started GladTalker Studio (PID %s) has closed; shutting down.", pid)
            on_exit()
        else:
            log.warning("Can't watch PID %s; the engine will keep running until it is stopped.", pid)

    thread = threading.Thread(target=run, name="studio-parent-watch", daemon=True)
    thread.start()
    return thread
