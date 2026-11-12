"""PixelDeck settings: one .env file, with the process environment on top.

Lookup order, first hit wins:
  1. the real environment (handy for one-off runs and tests),
  2. the file named by PIXELDECK_ENV, or else .env next to this file.

The file is re-read whenever it changes on disk, so a replaced token is picked up
without a restart. See .env.example for every key.
"""

import os
import re
import sys
import threading
from fnmatch import fnmatch
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
APP_NAME = "PixelDeck"
SLUG = "pixeldeck"
# The Linux window's application id. GNOME ties a window to the launcher named <APP_ID>.desktop,
# so install-desktop.sh writes that file under the same id.
APP_ID = "io.github.ahmedloumx.PixelDeck"
ICON_NAME = "pixeldeck"
DEFAULT_PORT = 8642

_lock = threading.Lock()
_cache = {"path": None, "mtime": None, "values": {}}


def env_path():
    named = os.environ.get("PIXELDECK_ENV")
    return Path(named).expanduser() if named else APP_DIR / ".env"


def parse_env(text):
    """KEY=value lines; # comments, optional `export`, optional matching quotes."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = value
    return values


def _file_values():
    path = env_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if _cache["path"] != path or _cache["mtime"] != mtime:
            try:
                values = parse_env(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                values = {}
            _cache.update(path=path, mtime=mtime, values=values)
        return _cache["values"]


def get(key, default=""):
    value = os.environ.get(key)
    if value:
        return value
    return _file_values().get(key) or default


def get_list(key, default=""):
    return [part.strip() for part in get(key, default).split(",") if part.strip()]


def get_int(key, default):
    try:
        return int(get(key, str(default)))
    except ValueError:
        return default


def mtime():
    """When the .env last changed, so the engine can retry after a new token."""
    try:
        return env_path().stat().st_mtime
    except OSError:
        return 0


# ---------------------------------------------------------------- folders

def _platform_dir(kind):
    home = Path.home()
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return {"config": roaming / APP_NAME, "cache": local / APP_NAME / "Cache",
                "state": local / APP_NAME / "State", "data": local / APP_NAME / "Data"}[kind]
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support" / APP_NAME
        return {"config": support, "cache": home / "Library" / "Caches" / APP_NAME,
                "state": support / "State", "data": support / "Data"}[kind]
    var, fallback = {"config": ("XDG_CONFIG_HOME", ".config"), "cache": ("XDG_CACHE_HOME", ".cache"),
                     "state": ("XDG_STATE_HOME", ".local/state"), "data": ("XDG_DATA_HOME", ".local/share")}[kind]
    return Path(os.environ.get(var) or home / fallback) / SLUG


def folder(kind):
    """config, cache, state or data; PIXELDECK_<KIND>_DIR moves any of them."""
    override = get(f"PIXELDECK_{kind.upper()}_DIR")
    return Path(override).expanduser() if override else _platform_dir(kind)


def settings_file():
    """What the app itself saves (team, project): kept apart from the .env you edit."""
    override = get("PIXELDECK_SETTINGS_FILE")
    return Path(override).expanduser() if override else folder("config") / "settings.json"


# ---------------------------------------------------------------- settings

def port():
    return get_int("PIXELDECK_PORT", DEFAULT_PORT)


def workspace():
    """The Bitbucket workspace slug, as in bitbucket.org/<workspace>/<repo>."""
    return get("BITBUCKET_WORKSPACE")


def workspace_root():
    """The folder that holds your local clones. Defaults to the folder PixelDeck sits in."""
    root = get("PIXELDECK_WORKSPACE_ROOT")
    return Path(root).expanduser() if root else APP_DIR.parent


def default_project():
    return get("BITBUCKET_PROJECT") or None


def jira_site():
    """https://<you>.atlassian.net, or empty: ticket keys then show without a link."""
    return get("JIRA_SITE").rstrip("/")


def ticket_prefixes():
    """Project keys that count as tickets, such as ABC,XYZ. Empty: any KEY-123."""
    return [p.upper() for p in get_list("TICKET_PREFIXES")]


def briefs_dir():
    """A folder of your own Claude brief templates; any file missing falls back to briefs/."""
    folder_ = get("PIXELDECK_BRIEFS_DIR")
    return Path(folder_).expanduser() if folder_ else None


def is_release_repo(slug):
    """Repos whose pull requests go to master or main before develop (charts, manifests)."""
    return any(fnmatch(slug, pattern) for pattern in get_list("PIXELDECK_RELEASE_REPOS"))
