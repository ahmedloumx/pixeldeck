"""PixelDeck settings: one .env file, with the process environment on top.

Lookup order, first hit wins:
  1. the real environment (handy for one-off runs and tests),
  2. the settings file (see env_path),
  3. for the tokens only, the system's credential store (see credentials.py).

The file is re-read whenever it changes on disk, so a replaced token is picked up
without a restart. See .env.example for every key.
"""

import os
import re
import sys
import threading
from fnmatch import fnmatch
from pathlib import Path

import credentials

# A packaged app (PyInstaller) unpacks static/ and briefs/ into its bundle folder, and has no
# source files next to them.
FROZEN = bool(getattr(sys, "frozen", False))
# Resolved: reached by a short name (C:\Users\JOHNDO~1), it would not match the long paths
# that resolve() gives the files under it.
APP_DIR = Path(sys._MEIPASS).resolve() if FROZEN else Path(__file__).resolve().parent
SECRET_KEYS = ("BITBUCKET_TOKEN", "JIRA_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN")
APP_NAME = "PixelDeck"
SLUG = "pixeldeck"
# The Linux window's application id. GNOME ties a window to the launcher named <APP_ID>.desktop,
# so install-desktop.sh writes that file under the same id.
APP_ID = "io.github.ahmedloumx.PixelDeck"
ICON_NAME = "pixeldeck"
DEFAULT_PORT = 8642


def appimage():
    """The AppImage file this packaged app runs from, or None.

    Its runtime sets APPIMAGE and APPDIR, and every program started from inside one inherits
    them: a shell in another AppImage's terminal, say. So believe them only when this very
    program sits under APPDIR."""
    image, mount = os.environ.get("APPIMAGE"), os.environ.get("APPDIR")
    if not (FROZEN and image and mount and sys.platform.startswith("linux")):
        return None
    try:
        Path(sys.executable).resolve().relative_to(Path(mount).resolve())
    except ValueError:
        return None
    return Path(image)

_lock = threading.Lock()
_cache = {"path": None, "mtime": None, "values": {}}


def env_path():
    """PIXELDECK_ENV, else a .env next to the code (a git clone), else the one in the config
    folder, which an update of the packaged app never touches."""
    named = os.environ.get("PIXELDECK_ENV")
    if named:
        return Path(named).expanduser()
    beside = APP_DIR / ".env"
    if not FROZEN and beside.is_file():
        return beside
    override = os.environ.get("PIXELDECK_CONFIG_DIR")  # not get(): that reads this very file
    return (Path(override).expanduser() if override else _platform_dir("config")) / ".env"


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
    value = _file_values().get(key)
    if value:
        return value
    if key in SECRET_KEYS:
        return credentials.store().get(key) or default
    return default


def file_value(key):
    """The settings file's own value, whatever the environment or the store says."""
    return _file_values().get(key, "")


def _quote(value):
    if value != value.strip() or any(c in value for c in " #\"'"):
        return f"'{value}'" if '"' in value else f'"{value}"'
    return value


def save(values):
    """Write KEY=value lines into the settings file, keeping its other lines and comments.
    An empty value removes the key."""
    for key, value in values.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\n" in value or "\r" in value:
            raise ValueError(f"cannot save {key!r}")
    path = env_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    pending, done, out = dict(values), set(), []
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key = line.split("=", 1)[0].strip() if "=" in line and not line.startswith("#") else None
        if key in done:  # a second line for a key we just wrote would win over it
            continue
        if key in pending:
            done.add(key)
            value = pending.pop(key)
            if value:
                out.append(f"{key}={_quote(value)}")
            continue
        out.append(raw)
    out += [f"{key}={_quote(value)}" for key, value in pending.items() if value]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    with _lock:
        _cache.update(path=None, mtime=None, values={})


def get_list(key, default=""):
    return [part.strip() for part in get(key, default).split(",") if part.strip()]


def get_int(key, default):
    try:
        return int(get(key, str(default)))
    except ValueError:
        return default


def mtime():
    """When the settings last changed, so the engine can retry after a new token."""
    try:
        stamp = env_path().stat().st_mtime
    except OSError:
        stamp = 0
    return max(stamp, credentials.changed())


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


CODE_HOSTS = ("bitbucket", "github", "gitlab")


def code_host():
    """Where your pull requests live: bitbucket (the default, so older settings keep working),
    github or gitlab. One at a time; each keeps its own settings when you switch."""
    value = get("PIXELDECK_CODE_HOST").strip().lower()
    return value if value in CODE_HOSTS else "bitbucket"


def workspace_root():
    """The folder that holds your local clones. A git clone of PixelDeck defaults to the folder
    it sits in; the packaged app to your home folder, until the setup screen sets it."""
    root = get("PIXELDECK_WORKSPACE_ROOT")
    if root:
        return Path(root).expanduser()
    return Path.home() if FROZEN else APP_DIR.parent


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
