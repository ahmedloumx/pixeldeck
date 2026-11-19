"""The tokens in the system's credential store, so they are not in a plain text file.

Windows keeps them in Credential Manager, under "PixelDeck:<KEY>" for the signed-in user.
macOS keeps them in the login Keychain (the `security` program), and Linux in the keyring
behind the Secret Service (GNOME Keyring, KWallet 6), through `secret-tool`. Where none of
these answers, the tokens stay in the settings file.

PIXELDECK_CREDENTIALS renames the entries (tests use their own name) or, set to "off",
turns the store off.
"""

import os
import shutil
import subprocess
import sys
import time


class StoreError(Exception):
    pass


class Store:
    """No store: nothing is kept, so the caller writes the token to the settings file."""

    name = None

    def get(self, key, fresh=False):
        """fresh: ask the store itself, past any cache (to read back what was just saved)."""
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

    def get(self, key, fresh=False):
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


class CommandStore(Store):
    """A store reached through a command-line program. Every Bitbucket call asks for the token,
    so answers are kept for a minute."""

    TTL = 60

    def __init__(self, service, run=subprocess.run):
        self._service, self._run, self._cache = service, run, {}

    def _exec(self, argv, stdin=None):
        try:
            return self._run(argv, input=stdin, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            raise StoreError(f"{self.name}: {exc}") from None

    def get(self, key, fresh=False):
        hit = self._cache.get(key)
        if hit and not fresh and time.time() - hit[0] < self.TTL:
            return hit[1]
        try:
            value = self._lookup(key)
        except StoreError:
            value = None
        self._cache[key] = (time.time(), value)
        return value

    def set(self, key, value):
        self._store(key, value)
        self._cache.pop(key, None)
        _touch()

    def delete(self, key):
        self._clear(key)
        self._cache.pop(key, None)
        _touch()


class MacStore(CommandStore):
    name = "the macOS Keychain"
    _NOT_FOUND = 44  # security's exit code when there is no such item

    def _lookup(self, key):
        out = self._exec(["security", "find-generic-password", "-s", self._service, "-a", key, "-w"])
        return (out.stdout.rstrip("\n") or None) if out.returncode == 0 else None

    def _store(self, key, value):
        if any(c in value for c in '"\\\n\r'):
            raise StoreError("the token has characters the Keychain command cannot take")
        # Through `security -i`, which reads the command from standard input, so the token is
        # never on a command line that other programs can list.
        line = f'add-generic-password -U -s "{self._service}" -a "{key}" -l "PixelDeck {key}" -w "{value}"\n'
        out = self._exec(["security", "-i"], stdin=line)
        if out.returncode != 0 or (out.stderr or "").strip():
            raise StoreError(f"the Keychain refused the token: {(out.stderr or '').strip()[:200]}")

    def _clear(self, key):
        out = self._exec(["security", "delete-generic-password", "-s", self._service, "-a", key])
        if out.returncode not in (0, self._NOT_FOUND):
            raise StoreError(f"the Keychain could not remove the token: {(out.stderr or '').strip()[:200]}")


class SecretServiceStore(CommandStore):
    name = "the system keyring"

    def _attrs(self, key):
        return ["service", self._service, "key", key]

    def _lookup(self, key):
        out = self._exec(["secret-tool", "lookup", *self._attrs(key)])
        return (out.stdout.rstrip("\n") or None) if out.returncode == 0 else None

    def _store(self, key, value):
        # secret-tool reads the secret from standard input when that is not a terminal.
        out = self._exec(["secret-tool", "store", f"--label=PixelDeck {key}", *self._attrs(key)], stdin=value)
        if out.returncode != 0:
            raise StoreError(f"the keyring refused the token: {(out.stderr or '').strip()[:200]}")

    def _clear(self, key):
        self._exec(["secret-tool", "clear", *self._attrs(key)])  # nothing to clear is no error


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
        elif service != "off" and sys.platform == "darwin" and shutil.which("security"):
            made = MacStore(service)
        elif service != "off" and sys.platform.startswith("linux") and shutil.which("secret-tool"):
            made = SecretServiceStore(service)
        _stores[service] = made
    return _stores[service]
