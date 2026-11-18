"""The tokens in the system's credential store, so they are not in a plain text file.

Windows keeps them in Credential Manager, under "PixelDeck:<KEY>" for the signed-in user.
macOS and Linux have no store here yet: their tokens stay in the settings file.

PIXELDECK_CREDENTIALS renames the entries (tests use their own name) or, set to "off",
turns the store off.
"""

import os
import sys
import time


class StoreError(Exception):
    pass


class Store:
    """No store: nothing is kept, so the caller writes the token to the settings file."""

    name = None

    def get(self, key):
        return None

    def set(self, key, value):
        raise StoreError("this system has no credential store PixelDeck can use")

    def delete(self, key):
        pass


class WindowsStore(Store):
    name = "Windows Credential Manager"
    _TYPE_GENERIC = 1
    _PERSIST_LOCAL_MACHINE = 2  # this user, this computer: not copied to other machines
    _ERROR_NOT_FOUND = 1168

    def __init__(self, service):
        import ctypes
        from ctypes import wintypes

        class Credential(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

        self._ctypes, self._Credential, self._service = ctypes, Credential, service
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        self._read, self._write, self._delete, self._free = api.CredReadW, api.CredWriteW, api.CredDeleteW, api.CredFree
        self._read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
        self._write.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._free.argtypes = [ctypes.c_void_p]
        for fn in (self._read, self._write, self._delete):
            fn.restype = wintypes.BOOL

    def _target(self, key):
        return f"{self._service}:{key}"

    def get(self, key):
        ctypes = self._ctypes
        found = ctypes.POINTER(self._Credential)()
        if not self._read(self._target(key), self._TYPE_GENERIC, 0, ctypes.byref(found)):
            return None
        try:
            cred = found.contents
            return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode("utf-8") or None
        finally:
            self._free(found)

    def set(self, key, value):
        ctypes = self._ctypes
        blob = value.encode("utf-8")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = self._Credential(Type=self._TYPE_GENERIC, TargetName=self._target(key), CredentialBlobSize=len(blob),
                                CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
                                Persist=self._PERSIST_LOCAL_MACHINE, UserName=key)
        if not self._write(ctypes.byref(cred), 0):
            raise StoreError(f"Credential Manager refused the token (error {ctypes.get_last_error()})")
        _touch()

    def delete(self, key):
        if not self._delete(self._target(key), self._TYPE_GENERIC, 0):
            error = self._ctypes.get_last_error()
            if error != self._ERROR_NOT_FOUND:
                raise StoreError(f"Credential Manager could not remove the token (error {error})")
        _touch()


_changed = 0.0
_stores = {}


def _touch():
    global _changed
    _changed = time.time()


def changed():
    """When a token in the store last changed, from this process."""
    return _changed


def store():
    service = os.environ.get("PIXELDECK_CREDENTIALS") or "PixelDeck"
    if service not in _stores:
        made = Store()
        if service != "off" and sys.platform == "win32":
            try:
                made = WindowsStore(service)
            except (OSError, AttributeError):
                pass
        _stores[service] = made
    return _stores[service]
