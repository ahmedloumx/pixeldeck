"""The lock screen's two server halves: hearing the system lock, and the optional passcode.

When you lock the computer (Super+L, or the menu), the desktop says so on the session bus:
GNOME as org.gnome.ScreenSaver ActiveChanged, KDE and others as org.freedesktop.ScreenSaver
ActiveChanged. A `dbus-monitor` child listens for that signal only and counts the locks; the
page reads the count in /api/status and shows its own "Let's code" screen when it went up.
Without dbus-monitor (Windows, macOS, a desktop without a session bus) the count stays put and
the screen only comes from the menu.

The passcode is a privacy screen for the window, not a second lock on the computer: it keeps
a glance at an unlocked screen off your pull requests, nothing more. Only a salted PBKDF2 hash
is kept, in the state folder, readable by you only. Five wrong codes make it wait 30 seconds.
`pixeldeck.py --forget-passcode` removes it when it is forgotten.
"""

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time

import config

FEATURES = ["lock"]
CODE_RE = re.compile(r"[0-9]{4,8}")
ITERATIONS = 200_000
TRIES, WAIT_S = 5, 30
MATCHES = [
    "type='signal',interface='org.gnome.ScreenSaver',member='ActiveChanged'",
    "type='signal',interface='org.freedesktop.ScreenSaver',member='ActiveChanged'",
]


# ---------------------------------------------------------------- the system lock

class Watcher:
    """Counts the system's locks from dbus-monitor's output, and restarts it if it stops."""

    def __init__(self, which=shutil.which, popen=subprocess.Popen, clock=time.time):
        self.which, self.popen, self.clock = which, popen, clock
        self.lock = threading.Lock()
        self.locks, self.locked, self.locked_at = 0, False, None
        self.proc = None
        self.stopped = False
        self._signal = False  # the last header line was our signal: its value comes next

    def available(self):
        return sys.platform.startswith("linux") and bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")) and bool(self.which("dbus-monitor"))

    def feed(self, line):
        """One line of dbus-monitor's output. A header names the signal; the next line
        carries its boolean."""
        line = line.strip()
        if line.startswith("signal "):
            self._signal = "member=ActiveChanged" in line and "ScreenSaver" in line
            return
        if not self._signal or not line.startswith("boolean "):
            return
        self._signal = False
        on = line.split()[-1] == "true"
        with self.lock:
            if on and not self.locked:
                self.locks += 1
                self.locked_at = self.clock()
            self.locked = on

    def status(self):
        with self.lock:
            return {"os": self.available(), "locks": self.locks, "locked": self.locked, "locked_at": self.locked_at}

    def run(self):
        """Listen until stop(); a dbus-monitor that dies is started again, more slowly each time."""
        if not self.available():
            return
        delay = 2
        while not self.stopped:
            started = self.clock()
            try:
                self.proc = self.popen(["dbus-monitor", "--session", *MATCHES], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       stdin=subprocess.DEVNULL, text=True, bufsize=1)
                for line in self.proc.stdout:
                    self.feed(line)
            except OSError:
                pass
            if self.stopped:
                return
            delay = 2 if self.clock() - started > 60 else min(delay * 2, 120)
            time.sleep(delay)

    def start(self):
        threading.Thread(target=self.run, name="screenlock", daemon=True).start()

    def stop(self):
        self.stopped = True
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()


WATCHER = Watcher()


# ---------------------------------------------------------------- the passcode

class Passcode:
    def __init__(self, path=None, clock=time.time):
        self._path = path
        self.clock = clock
        self.lock = threading.Lock()
        self.wrong, self.until = 0, 0.0

    def path(self):
        return self._path or config.folder("state") / "lock.json"

    def _read(self):
        try:
            data = json.loads(self.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        ok = isinstance(data, dict) and all(isinstance(data.get(k), str) for k in ("salt", "hash")) and isinstance(data.get("iterations"), int)
        return data if ok else None

    def is_set(self):
        return self._read() is not None

    @staticmethod
    def _hash(code, salt, iterations):
        return hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(salt), iterations).hex()

    def _matches(self, code):
        data = self._read()
        if data is None:
            return True
        if not isinstance(code, str) or not CODE_RE.fullmatch(code):
            return False
        return hmac.compare_digest(self._hash(code, data["salt"], data["iterations"]), data["hash"])

    def check(self, code):
        """{"ok": bool, "wait": seconds}. Five wrong codes in a row wait 30 seconds."""
        with self.lock:
            left = self.until - self.clock()
            if left > 0:
                return {"ok": False, "wait": math.ceil(left)}
            if self._matches(code):
                self.wrong = 0
                return {"ok": True, "wait": 0}
            self.wrong += 1
            if self.wrong >= TRIES:
                self.wrong, self.until = 0, self.clock() + WAIT_S
                return {"ok": False, "wait": WAIT_S}
            return {"ok": False, "wait": 0}

    def save(self, body):
        """Set, change or clear the code. Changing or clearing one asks for the current code."""
        current = body.get("current")
        if self.is_set():
            answer = self.check(current if isinstance(current, str) else "")
            if not answer["ok"]:
                raise ValueError(f"Wait {answer['wait']} seconds, then try again." if answer["wait"] else "The current passcode is not right.")
        if body.get("clear"):
            try:
                self.path().unlink()
            except FileNotFoundError:
                pass
            return {"has_code": False}
        code = body.get("code")
        if not isinstance(code, str) or not CODE_RE.fullmatch(code):
            raise ValueError("A passcode is 4 to 8 digits.")
        salt = secrets.token_hex(16)
        data = {"salt": salt, "iterations": ITERATIONS, "hash": self._hash(code, salt, ITERATIONS)}
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
        return {"has_code": True}

    def forget(self):
        try:
            self.path().unlink()
            return True
        except FileNotFoundError:
            return False


PASSCODE = Passcode()


def status():
    return {**WATCHER.status(), "has_code": PASSCODE.is_set()}


def route(handler, engine, method, name, query, body):
    if not name.startswith("lock-"):
        return None
    body = body if isinstance(body, dict) else {}
    if method == "GET" and name == "lock-state":
        return status()
    if method == "POST" and name == "lock-check":
        return PASSCODE.check(body.get("code"))
    if method == "POST" and name == "lock-code":
        return PASSCODE.save(body)
    return None
