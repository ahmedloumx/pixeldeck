"""The window's own title bar.

The page's top bar stands in for the system title bar: dragging its empty parts moves the
window and its buttons minimize, maximize and close it. The page asks through a WebKit
script message; only the requests below are understood, everything else is ignored.
"""

import json
from pathlib import Path

ACTIONS = ("move", "maximize", "minimize", "close", "titlebar", "hello")
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
