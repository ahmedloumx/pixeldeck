"""What PixelDeck is connected to, for the settings panel: where your code lives (Bitbucket,
GitHub or GitLab), the Claude Code CLI, git.

The report says whether a token is set and where it lives, never the token. Program
versions come from running them once with a short timeout and are kept a few minutes,
so opening the panel never waits on a slow or missing program twice.
"""

import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import config
import forge
import gitscan
import launcher
import version

VERSION_TTL = 600
_versions = {}
_lock = threading.Lock()


def _version(argv):
    key = tuple(argv)
    with _lock:
        hit = _versions.get(key)
        if hit and time.time() - hit[0] < VERSION_TTL:
            return hit[1]
    try:
        run = subprocess.run(argv, capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL,
                             creationflags=gitscan.NO_WINDOW)
        text = (run.stdout or run.stderr or "").strip().splitlines()
        value = text[0][:120] if run.returncode == 0 and text else None
    except (OSError, subprocess.SubprocessError):
        value = None
    with _lock:
        _versions[key] = (time.time(), value)
    return value


def claude():
    try:
        path = launcher.claude_binary()
    except RuntimeError as exc:
        return {"ok": False, "path": None, "version": None, "error": str(exc), "setting": "PIXELDECK_CLAUDE"}
    version = _version([path, "--version"])
    return {"ok": version is not None, "path": path, "version": version,
            "error": None if version else "found, but it did not answer --version", "setting": "PIXELDECK_CLAUDE"}


def git():
    path = shutil.which("git")
    version = _version([path, "--version"]) if path else None
    return {"ok": version is not None, "path": path, "version": version, "error": None if path else "git is not on PATH"}


def bitbucket(engine):
    # The engine's account, its calls and its project are Bitbucket's only while it is the host.
    active = config.host() == "bitbucket"
    return {
        "active": active,
        "token_set": bool(config.get("BITBUCKET_TOKEN")),
        "email": config.get("BITBUCKET_EMAIL") or None,
        "workspace": config.get("BITBUCKET_WORKSPACE") or None,
        "env_file": str(config.env_path()),
        "auth": engine.auth if active else {"ok": None, "error": None},
        "me": engine.me() if active else None,
        "budget": engine.budget() if active else {"used": 0, "limit": 1000},
        "project": engine.project() if active else None,
    }


def host(engine):
    """The host PixelDeck reads now, and how each of the three is set up."""
    chosen = config.host()
    return {
        "id": chosen, "label": forge.label(chosen), "web": forge.web_base(chosen),
        "workspace": config.workspace() or None, "token_set": bool(config.get(config.HOST_TOKEN[chosen])),
        "auth": engine.auth, "me": engine.me(), "budget": engine.budget(), "env_file": str(config.env_path()),
        "hosts": {h: {"label": forge.label(h), "token_set": bool(config.get(config.HOST_TOKEN[h])),
                      "workspace": config.get(config.HOST_WORKSPACE[h]) or None, "active": h == chosen} for h in config.HOSTS},
    }


def report(engine, port, workspace_root, terminal_problem):
    return {
        "bitbucket": bitbucket(engine),
        "host": host(engine),
        "claude": claude(),
        "git": git(),
        "app": {
            "port": port, "version": version.VERSION, "workspace_root": str(workspace_root), "python": platform.python_version(),
            # the packaged app's own folder, not the bundle folder inside it
            "platform": sys.platform, "app_dir": str(Path(sys.executable).parent if config.FROZEN else config.APP_DIR),
            "briefs_dir": str(config.briefs_dir() or ""), "terminal": terminal_problem or None,
        },
    }
