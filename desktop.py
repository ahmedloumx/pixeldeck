"""Where people look for the packaged app: the app menu (Linux) and a `pixeldeck` command in a
terminal (macOS and Linux). Nothing installs an AppImage or a .app dragged to Applications, so
About offers both, and says when one points at an old place.

A git clone keeps install-desktop.sh for its menu entry. It writes the same file name (the
window's app id), so an entry or a command PixelDeck did not write is replaced only on an
explicit "Replace", and never removed.
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import config

MARK = "X-PixelDeck-Packaged=true"  # in the entries and the command this module writes


def program():
    """What a menu entry or the command starts: the AppImage, or the program inside the .app."""
    if not config.FROZEN or sys.platform == "win32":
        return None
    return config.appimage() or Path(sys.executable)


FEATURES = ["desktop"] if program() is not None else []


def _data_home():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def command_path():
    return Path.home() / ".local" / "bin" / "pixeldeck"


def menu_path():
    return _data_home() / "applications" / f"{config.APP_ID}.desktop"


def icon_path():
    return _data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{config.ICON_NAME}.svg"


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _desktop_quote(value):
    """Exec= quoting from the desktop entry spec: double quotes, with \\ before " ` $ and \\."""
    if not any(c in value for c in " \t\"'\\><~|&;$*?#()`"):
        return value
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in value) + '"'


def _starts(text):
    """The program an entry (Exec=) or a command script (exec) starts."""
    for line in (text or "").splitlines():
        for prefix in ("Exec=", "exec "):
            if line.startswith(prefix):
                try:
                    return shlex.split(line[len(prefix):])[0]
                except (ValueError, IndexError):
                    return None
    return None


def _state(path, target):
    """missing, ok, stale (ours, but for a program that moved) or other (not ours)."""
    text = _read(path)
    if text is None:
        return {"state": "missing"}
    starts = _starts(text)
    if MARK not in text:
        return {"state": "other", "starts": starts}
    return {"state": "ok" if starts == str(target) else "stale", "starts": starts}


def status():
    target = program()
    if target is None:
        return {"available": False}
    folder = str(command_path().parent)
    answer = {"available": True, "program": str(target),
              "command": {"path": str(command_path()), "on_path": folder in os.environ.get("PATH", "").split(os.pathsep),
                          **_state(command_path(), target)}}
    if sys.platform.startswith("linux"):
        answer["menu"] = {"path": str(menu_path()), **_state(menu_path(), target)}
    return answer


def _require():
    target = program()
    if target is None:
        raise ValueError("Only the packaged app on macOS or Linux installs these")
    return target


def _check_replace(path, target, replace):
    found = _state(path, target)
    if found["state"] == "other" and not replace:
        raise ValueError(f"{path} already starts {found.get('starts') or 'something else'}; replace it from About")


def install_command(replace=False):
    target = _require()
    path = command_path()
    _check_replace(path, target, replace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n# {MARK}: About, Install the pixeldeck command\nexec {shlex.quote(str(target))} \"$@\"\n",
                    encoding="utf-8")
    path.chmod(0o755)


def remove_command():
    path = command_path()
    if MARK not in (_read(path) or ""):
        raise ValueError(f"{path} was not written by PixelDeck; it stays")
    path.unlink()


def add_menu(replace=False):
    target = _require()
    if not sys.platform.startswith("linux"):
        raise ValueError("The app menu entry is for Linux; on macOS, Applications is the menu")
    path = menu_path()
    _check_replace(path, target, replace)
    icon = icon_path()
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config.APP_DIR / "static" / "app-icon.svg", icon)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The icon by its full path, as install-desktop.sh does: GNOME Shell scans icon names once at
    # login, so a name installed later would show a generic icon until the next one.
    path.write_text("\n".join([
        "[Desktop Entry]", "Type=Application", "Name=PixelDeck",
        "Comment=Bitbucket pull requests, review threads and Claude Code fixes",
        f"Exec={_desktop_quote(str(target))}", f"Icon={icon}", "Terminal=false", "Categories=Development;",
        "Keywords=PixelDeck;PR;pull request;Bitbucket;review;dashboard;",
        f"StartupWMClass={config.APP_ID}", "StartupNotify=true", MARK, ""]), encoding="utf-8")
    _refresh_menu(path.parent)


def remove_menu():
    path = menu_path()
    if MARK not in (_read(path) or ""):
        raise ValueError(f"{path} was not written by PixelDeck; it stays")
    path.unlink()
    _refresh_menu(path.parent)


def _refresh_menu(folder):
    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(["update-desktop-database", str(folder)], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


ACTIONS = {"install-command": install_command, "remove-command": remove_command, "add-menu": add_menu, "remove-menu": remove_menu}


def route(handler, engine, method, name, query, body):
    """GET desktop, POST desktop {action, replace}; None for any other route."""
    if name != "desktop":
        return None
    if method == "POST":
        action = ACTIONS.get(str(body.get("action")))
        if action is None:
            raise ValueError("unknown action")
        if action in (install_command, add_menu):
            action(replace=body.get("replace") is True)
        else:
            action()
    return status()
