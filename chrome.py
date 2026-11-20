"""The window's own title bar.

The page's top bar stands in for the system title bar: dragging its empty parts moves the
window and its buttons minimize, maximize and close it. The page asks through a WebKit
script message; only the requests below are understood, everything else is ignored.
"""

import json
import re
from pathlib import Path

ACTIONS = ("move", "maximize", "minimize", "close", "titlebar", "hello", "youtube", "ytplace", "ytctl")
YT_COMMANDS = ("toggle", "pause", "stop", "popout", "dock", "page")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
PREFS_KEY = "prdesk.prefs.v1"


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse(raw):
    """A request from the page as a dict, or None when it is not one of ours."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict) or raw.get("action") not in ACTIONS:
        return None
    action = raw["action"]
    if action == "move":
        x, y, button = raw.get("x"), raw.get("y"), raw.get("button")
        if not (_number(x) and _number(y) and isinstance(button, int) and not isinstance(button, bool) and 1 <= button <= 3):
            return None
        return {"action": "move", "x": float(x), "y": float(y), "button": button}
    if action == "titlebar":
        if not isinstance(raw.get("system"), bool):
            return None
        return {"action": "titlebar", "system": raw["system"]}
    if action == "youtube":
        # A video whose owner keeps it on YouTube: the window opens youtube.com itself for it.
        # Only an id goes through, so the page cannot point that window anywhere else.
        vid = raw.get("id")
        if not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid):
            return None
        title = raw.get("title") if isinstance(raw.get("title"), str) else ""
        title = "".join(c for c in title if c.isprintable())[:200].strip()
        mode = raw.get("mode") if raw.get("mode") in ("window", "dock") else "window"
        return {"action": "youtube", "id": vid, "title": title, "mode": mode}
    if action == "ytplace":
        # where the TV's screen is in the page, so YouTube's own page can sit exactly on it
        box = [raw.get(k) for k in ("x", "y", "w", "h")]
        if not all(_number(v) and -20000 <= v <= 20000 for v in box) or not isinstance(raw.get("show"), bool):
            return None
        x, y, w, h = (round(float(v)) for v in box)
        return {"action": "ytplace", "x": x, "y": y, "w": max(0, w), "h": max(0, h), "show": raw["show"]}
    if action == "ytctl":
        return {"action": "ytctl", "cmd": raw["cmd"]} if raw.get("cmd") in YT_COMMANDS else None
    return {"action": action}


def titlebar_mode(state_folder):
    """"system" when you chose the system title bar in Personalise, else "custom". Read from
    the settings the server keeps (ui.json), since the window starts before the page."""
    try:
        values = json.loads((Path(state_folder) / "ui.json").read_text(encoding="utf-8")).get("values") or {}
        prefs = json.loads(values.get(PREFS_KEY) or "{}")
    except (OSError, ValueError, AttributeError, TypeError):
        return "custom"
    return "system" if isinstance(prefs, dict) and prefs.get("titlebar") == "system" else "custom"
