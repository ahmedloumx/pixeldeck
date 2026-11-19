"""PixelDeck's own Spotify player: spotifyd, run in the background while PixelDeck runs.

Spotify's Web API only steers a Spotify app that is already open somewhere. spotifyd is a small
open-source Spotify Connect player (https://github.com/Spotifyd/spotifyd), with no window: it
shows in your Spotify devices as "PixelDeck" and plays through this computer's speakers, so music
plays without the Spotify app. It needs Spotify Premium, and it is not made by Spotify.

PixelDeck does not ship it. When spotifyd is installed (on the PATH, in ~/.local/bin, or named
by SPOTIFYD_PATH) and signed in, the server starts it, restarts it if it stops, and stops it
when the server stops. It never announces itself on the network: a stranger on the same Wi-Fi
could otherwise take it over. Signing in is spotifyd's own: Spotify's login page in your browser,
the result kept in PixelDeck's state folder (readable by you only). SPOTIFYD=off turns it off.
"""

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import config
import opener

FEATURES = ["spotifyd"]
DEVICE_NAME = "PixelDeck"
OAUTH_PORT = 8000         # spotifyd's own default for the sign-in redirect
BACKOFF = (3, 10, 30, 120)  # seconds before each restart of a player that stopped
SIGN_IN_WAIT = 300        # seconds a sign-in may take
LOG_MAX = 256 * 1024
WINDOWS = sys.platform == "win32"


def binary():
    named = config.get("SPOTIFYD_PATH")
    for candidate in (named, shutil.which("spotifyd"), Path.home() / ".local" / "bin" / "spotifyd"):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def folder():
    path = Path(config.folder("state")) / "spotifyd"
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def signed_in(cache=None):
    return (Path(cache or folder()) / "oauth" / "credentials.json").is_file()


def device_name():
    name = (config.get("SPOTIFYD_DEVICE_NAME") or DEVICE_NAME).strip()
    return name[:60] if name else DEVICE_NAME


def enabled():
    return config.get("SPOTIFYD").strip().lower() not in ("off", "0", "false", "no")


def player_argv(exe, cache, name):
    """The command for the player: in the foreground (the server watches it), sound through the
    PulseAudio interface (PipeWire on Fedora answers it), no announcing on the network."""
    return [exe, "--no-daemon", "--cache-path", str(cache), "--device-name", name, "--device-type", "computer",
            "--backend", "pulseaudio", "--bitrate", "320", "--volume-controller", "soft-volume", "--initial-volume", "60",
            "--disable-discovery=true", "--use-mpris=false", "--autoplay=false"]


def signin_argv(exe, cache):
    return [exe, "authenticate", "--cache-path", str(cache), "--oauth-port", str(OAUTH_PORT)]


def find_login_url(text):
    """The Spotify login link in what spotifyd printed, or None. Only accounts.spotify.com."""
    for word in str(text or "").split():
        word = word.strip("<>()[]\"'.,")
        if word.startswith("https://accounts.spotify.com/"):
            return word
    return None


def _alive(pid):
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _ours(pid, cache):
    """Is pid a spotifyd started with this cache folder? Only such a leftover is stopped."""
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    return any(part.endswith(b"spotifyd") for part in cmd[:1]) and str(cache).encode() in cmd


class Player:
    """The spotifyd process, started and watched by the server."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.state = "off"          # off, missing, signed-out, starting, running, stopped, error
        self.error = None
        self.started_at = None
        self.restarts = 0
        self.stopping = False
        self.signin = {"state": "idle", "url": None, "error": None}
        self._signin_proc = None

    # ---- the player ---------------------------------------------------------

    def _pid_file(self):
        return folder() / "spotifyd.pid"

    def _clear_leftover(self, cache):
        """A player a server killed hard left behind: stop it, so two never fight for the name."""
        try:
            pid = int(self._pid_file().read_text().strip())
        except (OSError, ValueError):
            return
        if pid != os.getpid() and _alive(pid) and _ours(pid, cache):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            for _ in range(30):
                if not _alive(pid):
                    break
                time.sleep(0.1)

    def start(self):
        """Start the player when it can run; say why not otherwise. Returns the status."""
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return self.status()
            exe = binary()
            if not enabled():
                self.state, self.error = "off", None
                return self.status()
            if not exe:
                self.state, self.error = "missing", None
                return self.status()
            cache = folder()
            if not signed_in(cache):
                self.state, self.error = "signed-out", None
                return self.status()
            self._clear_leftover(cache)
            log = open(cache / "spotifyd.log", "ab")
            try:
                if log.tell() > LOG_MAX:
                    log.truncate(0)
                self.proc = subprocess.Popen(player_argv(exe, cache, device_name()), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                             start_new_session=not WINDOWS)
            except OSError as exc:
                self.state, self.error = "error", f"spotifyd did not start: {exc}"
                return self.status()
            finally:
                log.close()
            self._pid_file().write_text(str(self.proc.pid))
            self.state, self.error, self.started_at, self.stopping = "running", None, time.time(), False
            threading.Thread(target=self._watch, args=(self.proc,), name="spotifyd-watch", daemon=True).start()
            return self.status()

    def _watch(self, proc):
        code = proc.wait()
        with self.lock:
            if self.stopping or proc is not self.proc:
                return
            self.state = "stopped"
            self.error = f"spotifyd stopped (exit {code}); see {folder() / 'spotifyd.log'}"
            wait = BACKOFF[min(self.restarts, len(BACKOFF) - 1)]
            self.restarts += 1
        time.sleep(wait)
        if not self.stopping:
            self.start()

    def stop(self):
        with self.lock:
            self.stopping = True
            proc, self.proc = self.proc, None
            self.state = "off"
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            self._pid_file().unlink()
        except OSError:
            pass

    def restart(self):
        self.stop()
        self.restarts = 0
        return self.start()

    # ---- signing in ---------------------------------------------------------

    def sign_in(self):
        """Run spotifyd's own sign-in: it prints a Spotify login link, the browser opens it, and
        Spotify sends the browser back to spotifyd, which keeps the login. Then the player starts."""
        exe = binary()
        if not exe:
            raise ValueError("spotifyd is not installed: see Settings, Connected apps, Spotify")
        with self.lock:
            if self.signin["state"] == "waiting":
                return self.status()
            self.signin = {"state": "waiting", "url": None, "error": None}
        try:
            proc = subprocess.Popen(signin_argv(exe, folder()), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, start_new_session=not WINDOWS)
        except OSError as exc:
            self.signin = {"state": "failed", "url": None, "error": f"spotifyd did not start: {exc}"}
            return self.status()
        self._signin_proc = proc
        threading.Thread(target=self._follow_signin, args=(proc,), name="spotifyd-signin", daemon=True).start()
        # The link comes within a moment; give the page something to show at once.
        for _ in range(40):
            if self.signin["url"] or proc.poll() is not None:
                break
            time.sleep(0.1)
        return self.status()

    def _follow_signin(self, proc):
        seen = []
        timer = threading.Timer(SIGN_IN_WAIT, lambda: proc.poll() is None and proc.kill())
        timer.start()
        try:
            for line in proc.stdout:
                seen.append(line.strip())
                url = find_login_url(line)
                if url and not self.signin["url"]:
                    self.signin["url"] = url
                    try:
                        opener.open_link(url)
                    except Exception:  # noqa: BLE001 - the page shows the link to click instead
                        pass
            code = proc.wait()
        finally:
            timer.cancel()
        if code == 0 and signed_in():
            self.signin = {"state": "done", "url": None, "error": None}
            self.restart()
        else:
            tail = " ".join(seen[-3:])[:300]
            self.signin = {"state": "failed", "url": self.signin.get("url"), "error": tail or f"spotifyd authenticate ended with {code}"}

    # ---- what the page shows ---------------------------------------------------

    def status(self):
        exe = binary()
        running = bool(self.proc and self.proc.poll() is None)
        cache = folder()
        return {
            "enabled": enabled(), "installed": bool(exe), "path": exe, "signed_in": signed_in(cache),
            "state": "running" if running else self.state, "error": self.error, "device_name": device_name(),
            "since": self.started_at if running else None, "restarts": self.restarts, "signin": dict(self.signin),
            "signin_command": f"spotifyd authenticate --cache-path {cache}",
            "install_url": "https://github.com/Spotifyd/spotifyd/releases/latest",
        }


PLAYER = Player()


def route(handler, engine, method, name, query, body):
    """GET spotifyd, POST spotifyd-signin, spotifyd-restart, spotifyd-stop; None for others."""
    if not name.startswith("spotifyd"):
        return None
    if method == "GET" and name == "spotifyd":
        return PLAYER.status()
    if method == "POST" and name == "spotifyd-signin":
        return PLAYER.sign_in()
    if method == "POST" and name == "spotifyd-restart":
        return PLAYER.restart()
    if method == "POST" and name == "spotifyd-stop":
        PLAYER.stop()
        return PLAYER.status()
    return None
