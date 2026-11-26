"""Hold a file the way another program can on Windows, for tests of "busy" model files."""

import contextlib
import ctypes
import sys
import unittest

windows_only = unittest.skipUnless(sys.platform == "win32", "Windows file-sharing locks")


@contextlib.contextmanager
def exclusive_open(path):
    """Keep ``path`` open with no sharing allowed (as an antivirus scan or another writer can), so
    any other attempt to open it fails with a sharing violation until the block ends."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    generic_read, no_sharing, open_existing, normal = 0x80000000, 0, 3, 0x80
    handle = kernel32.CreateFileW(str(path), generic_read, no_sharing, None, open_existing, normal, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel32.CloseHandle(handle)


@contextlib.contextmanager
def range_lock(path, offset, length, exclusive=True):
    """Hold a byte-range lock on part of ``path`` from another handle, as a program can: an
    exclusive lock stops everyone else reading that range; a shared one only stops writers."""
    import msvcrt
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel32.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.POINTER(Overlapped)]
    with open(path, "rb") as holder:
        handle = msvcrt.get_osfhandle(holder.fileno())
        overlapped = Overlapped(Offset=offset & 0xFFFFFFFF, OffsetHigh=offset >> 32)
        flags = 0x1 | (0x2 if exclusive else 0)  # LOCKFILE_FAIL_IMMEDIATELY | LOCKFILE_EXCLUSIVE_LOCK
        if not kernel32.LockFileEx(handle, flags, 0, length, 0, ctypes.byref(overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            kernel32.UnlockFileEx(handle, 0, length, 0, ctypes.byref(overlapped))
