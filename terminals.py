"""Terminal sessions for the built-in dock: a pseudo-terminal per session, streamed to the page.

Each session runs one server-chosen command (a login shell in the workspace, or
Claude Code briefed on a pull request). Output is kept in a bounded buffer so a
page that reconnects, or opens later, sees the scrollback.

Linux and macOS use the standard library's pty. Windows uses ConPTY through the
optional pywinpty package; without it the terminal panel says what to install.
"""

import atexit
import os
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid

WINDOWS = sys.platform == "win32"
if WINDOWS:
    try:
        from winpty import PtyProcess
    except ImportError:
        PtyProcess = None
    UNAVAILABLE = None if PtyProcess else "On Windows the terminal needs pywinpty: run  py -m pip install pywinpty  and restart PixelDeck."
else:
    import fcntl
    import pty
    import termios
    UNAVAILABLE = None

BUFFER_MAX = 2 * 1024 * 1024
SESSIONS_MAX = 10


def _clean_env():
    env = dict(os.environ)
    # A server started from inside Claude Code inherits its markers; a nested
    # claude would refuse to start, so the terminal gets a clean slate.
    for key in list(env):
        if key == "CLAUDECODE" or key.startswith("CLAUDE_CODE"):
            env.pop(key)
    env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor", "FORCE_COLOR": "1"})
    return env


class Session:
    """The buffer and stream every platform shares; subclasses own the process."""

    def __init__(self, title, argv, cwd, cols=120, rows=32, notify=None):
        self._notify = notify or (lambda: None)
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.cwd = str(cwd)
        self.started = time.time()
        self.exit_code = None
        self.buf = bytearray()
        self.base = 0  # stream offset of buf[0]
        self.cond = threading.Condition()
        self.cols, self.rows = cols, rows
        # The screen as a page last drew it, with the stream offset it had drawn up to. A page
        # that opens later starts from it and only replays what came after, which was all
        # written at the same size. Replaying the whole history at one width garbles programs
        # that redraw in place (Claude Code) whenever the size changed along the way.
        self.snapshot = None
        self._start(argv, cols, rows)
        threading.Thread(target=self._pump, name=f"term-{self.id}", daemon=True).start()

    @property
    def end(self):
        return self.base + len(self.buf)

    @property
    def alive(self):
        return self.exit_code is None

    def _pump(self):
        while True:
            chunk = self._read()
            if not chunk:
                break
            with self.cond:
                self.buf.extend(chunk)
                if len(self.buf) > BUFFER_MAX:
                    drop = len(self.buf) - BUFFER_MAX
                    del self.buf[:drop]
                    self.base += drop
                self.cond.notify_all()
            self._notify()
        code = self._wait()
        with self.cond:
            self.exit_code = code
            self.cond.notify_all()
        self._notify()
        self._cleanup()

    def read_from(self, pos, timeout=15.0):
        """Bytes after stream offset pos, waiting up to timeout for new output."""
        with self.cond:
            if pos >= self.end and self.alive:
                self.cond.wait(timeout)
            start = max(pos, self.base)
            data = bytes(self.buf[start - self.base:])
            return data, self.end

    def write(self, data):
        if self.alive:
            self._write(data)

    def resize(self, cols, rows):
        if self.alive:
            self._resize(cols, rows)
            self.cols, self.rows = cols, rows

    def remember(self, text, pos, cols, rows):
        """Keep a page's picture of the screen, if its offset still has its tail in the buffer."""
        with self.cond:
            if not self.base <= pos <= self.end:
                return False
            if self.snapshot and self.snapshot["pos"] > pos:
                return False  # an older picture never replaces a newer one
            self.snapshot = {"text": text, "pos": pos, "cols": cols, "rows": rows}
            return True

    def close(self):
        if self.alive:
            self._kill()

    def info(self):
        return {"id": self.id, "title": self.title, "cwd": self.cwd, "started": self.started,
                "alive": self.alive, "exit_code": self.exit_code}


class UnixSession(Session):
    def _start(self, argv, cols, rows):
        self.master, slave = pty.openpty()
        self._resize(cols, rows)
        self.proc = subprocess.Popen(
            argv, cwd=self.cwd, env=_clean_env(), stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True, close_fds=True,
        )
        os.close(slave)

    def _read(self):
        try:
            return os.read(self.master, 65536)
        except OSError:
            return b""

    def _write(self, data):
        os.write(self.master, data)

    def _resize(self, cols, rows):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", max(2, rows), max(2, cols), 0, 0))

    def _wait(self):
        return self.proc.wait()

    def _kill(self):
        try:
            os.killpg(self.proc.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass

    def _cleanup(self):
        try:
            os.close(self.master)
        except OSError:
            pass


class WindowsSession(Session):
    def _start(self, argv, cols, rows):
        self.proc = PtyProcess.spawn(argv, cwd=self.cwd, env=_clean_env(), dimensions=(max(2, rows), max(2, cols)))

    def _read(self):
        try:
            return self.proc.read(65536).encode("utf-8", "surrogateescape")
        except (EOFError, OSError):
            return b""

    def _write(self, data):
        self.proc.write(data.decode("utf-8", "replace"))

    def _resize(self, cols, rows):
        self.proc.setwinsize(max(2, rows), max(2, cols))

    def _wait(self):
        while self.proc.isalive():
            time.sleep(0.1)
        return self.proc.exitstatus or 0

    def _kill(self):
        try:
            self.proc.terminate(force=True)
        except Exception:  # pywinpty raises its own types once the process is gone
            pass

    def _cleanup(self):
        pass


SessionType = WindowsSession if WINDOWS else UnixSession


class Terminals:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()
        self.changed = threading.Condition()
        self.version = 0
        atexit.register(self.close_all)

    def _bump(self):
        with self.changed:
            self.version += 1
            self.changed.notify_all()

    def wait(self, version, timeout=15.0):
        """Block until any session produced output or changed, or the timeout passes."""
        with self.changed:
            if self.version == version:
                self.changed.wait(timeout)
            return self.version

    def spawn(self, title, argv, cwd):
        if UNAVAILABLE:
            raise RuntimeError(UNAVAILABLE)
        with self.lock:
            finished = [s for s in self.sessions.values() if not s.alive]
            while len(self.sessions) >= SESSIONS_MAX and finished:
                self.sessions.pop(finished.pop(0).id, None)
            if len(self.sessions) >= SESSIONS_MAX:
                raise RuntimeError(f"{SESSIONS_MAX} terminals are open; close one first")
            session = SessionType(title, argv, cwd, notify=self._bump)
            self.sessions[session.id] = session
        self._bump()
        return session

    def get(self, sid):
        session = self.sessions.get(sid)
        if session is None:
            raise KeyError("no such terminal")
        return session

    def forget(self, sid):
        session = self.sessions.pop(sid, None)
        if session:
            session.close()
        self._bump()

    def list(self):
        return [s.info() for s in sorted(self.sessions.values(), key=lambda s: s.started)]

    def close_all(self):
        for s in list(self.sessions.values()):
            s.close()
