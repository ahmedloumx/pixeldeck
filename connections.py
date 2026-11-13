"""What PixelDeck is connected to, for the settings panel: Bitbucket, the Claude Code CLI, git.

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

import config
import launcher

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
        run = subprocess.run(argv, capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL)
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
    return {
        "token_set": bool(config.get("BITBUCKET_TOKEN")),
        "email": config.get("BITBUCKET_EMAIL") or None,
        "workspace": config.workspace() or None,
        "env_file": str(config.env_path()),
        "auth": engine.auth,
        "me": engine.me(),
        "budget": engine.budget(),
        "project": engine.project(),
    }


def report(engine, port, workspace_root, terminal_problem):
    return {
        "bitbucket": bitbucket(engine),
        "claude": claude(),
        "git": git(),
        "app": {
            "port": port, "workspace_root": str(workspace_root), "python": platform.python_version(),
            "platform": sys.platform, "app_dir": str(config.APP_DIR),
            "briefs_dir": str(config.briefs_dir() or ""), "terminal": terminal_problem or None,
        },
    }
