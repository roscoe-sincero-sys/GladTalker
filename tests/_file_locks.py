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
