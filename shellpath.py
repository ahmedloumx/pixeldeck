"""The PATH of the user's login shell, for a server started from the Dock, Finder or an app menu.

Such a start gets a short PATH (on macOS /usr/bin:/bin:/usr/sbin:/sbin), so `claude` and a
Homebrew git look missing to the server, though the terminals find them: each one starts a
login shell. extend() adds what the login shell has, and the folders the usual installers use,
after what the PATH already holds. It never raises, and gives up after a few seconds.
"""

import os
import shutil
import subprocess
import sys
import threading

MARK = "__PIXELDECK_PATH__"
# Where the Claude Code native installer, Homebrew (Apple Silicon, Intel) and pip --user put programs.
USUAL = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")
TIMEOUT = 5


def login_path(shell=None, run=subprocess.run):
    """The login shell's PATH entries, or [] when it does not answer in time."""
    shell = shell or os.environ.get("SHELL") or "/bin/sh"
    # Interactive too (-i): many people set PATH in .bashrc or .zshrc, which a login shell
    # alone may skip. The marks keep whatever the rc files print out of the answer.
    try:
        out = run([shell, "-l", "-i", "-c", f'printf "{MARK}%s{MARK}" "$PATH"'], capture_output=True, text=True,
                  timeout=TIMEOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    parts = (out.stdout or "").split(MARK)
    return [p for p in parts[1].split(":") if p] if len(parts) >= 3 else []


def extend(environ=None, found=None, isdir=os.path.isdir):
    """Add the missing folders to PATH; answers what was added."""
    environ = os.environ if environ is None else environ
    if sys.platform == "win32":
        return []
    current = [p for p in environ.get("PATH", "").split(":") if p]
    candidates = (login_path() if found is None else found) + [os.path.expanduser(p) for p in USUAL]
    added = [p for p in dict.fromkeys(candidates) if p not in current and isdir(p)]
    if added:
        environ["PATH"] = ":".join(current + added)
    return added


def extend_in_background(which=shutil.which):
    """At server start, only when the PATH lacks claude or git: an interactive shell runs the
    person's rc files, and a slow one must not hold up the first page."""
    if sys.platform == "win32" or (which("claude") and which("git")):
        return None
    thread = threading.Thread(target=lambda: _safe(extend), name="pixeldeck-shell-path", daemon=True)
    thread.start()
    return thread


def _safe(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001 - a broken rc file must not stop the server
        pass
