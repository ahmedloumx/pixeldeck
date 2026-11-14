"""Where you left off: the terminals and the page's own settings, kept across restarts.

Every few seconds, on any change and when the server stops, the open terminals are written
to the state folder: their name, their folder, whether Claude Code runs in them (with its
session id) and the last screen a page drew. At the next start the page offers to bring them
back: a shell in the same folder with its old screen above the new prompt, and Claude Code
resumed by its session id (claude --resume <id>), or the newest one in that folder.

The page's settings (layout, theme, terminal names...) are kept here as well. The window can
be killed before WebKit writes its local storage to disk; the server's copy survives that.
"""

import json
import os
import re
import threading
import time
from pathlib import Path

import config

SAVE_EVERY = 5
SCREEN_MAX = 200_000          # characters of a saved screen, per terminal
UI_KEYS = ("prdesk.layout.v2", "prdesk.prefs.v1", "prdesk.termnames.v1", "prdesk.termorder.v1", "prdesk.termtitles.v1",
           "prdesk.insights.range.v1", "prdesk.datefilter.v1", "prdesk.notify.v1", "prdesk.dock.width.v1")
UI_VALUE_MAX = 64 * 1024
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":")))
    os.replace(tmp, path)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---- what runs in a terminal (Linux reads /proc; elsewhere only the folder it started in) ----

def _descendants(pid):
    out, todo = [], [pid]
    while todo and len(out) < 200:
        p = todo.pop()
        out.append(p)
        for task in Path(f"/proc/{p}/task").glob("*/children"):
            try:
                todo.extend(int(c) for c in task.read_text().split())
            except (OSError, ValueError):
                pass
    return out


def _cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def _claude(pids, sessions_dir):
    """Claude Code keeps a small file per running process, named by its pid, that holds its
    session id. It is Claude Code's own record and may change; a miss falls back to --continue."""
    for pid in pids:
        info = _read(sessions_dir / f"{pid}.json")
        if isinstance(info, dict) and isinstance(info.get("sessionId"), str) and UUID.fullmatch(info["sessionId"]):
            return {"session": info["sessionId"], "cwd": info.get("cwd") if isinstance(info.get("cwd"), str) else None}
    return None


def describe(session, sessions_dir):
    pid = getattr(getattr(session, "proc", None), "pid", None)
    pids = _descendants(pid) if pid else []
    claude = _claude(pids, sessions_dir) if pids else None
    # Claude Code's own record knows its folder; for a shell, the shell's folder follows your cd
    # (a deeper process, such as a tool's helper, may sit anywhere).
    cwd = (claude or {}).get("cwd") or (_cwd(pid) if pid else None) or session.cwd
    snap = session.snapshot or {}
    return {
        "id": session.id, "title": session.title, "cwd": cwd, "started": session.started,
        "kind": "claude" if claude else "shell", "claude_session": (claude or {}).get("session"),
        "screen": (snap.get("text") or "")[-SCREEN_MAX:], "cols": snap.get("cols"), "rows": snap.get("rows"),
    }


class Keeper:
    def __init__(self, terms, folder=None, sessions_dir=None):
        self.terms = terms
        self.folder = Path(folder or config.folder("state"))
        self.file = self.folder / "terminals.json"
        self.offer_file = self.folder / "terminals.offer.json"
        self.ui_file = self.folder / "ui.json"
        self.sessions_dir = Path(sessions_dir or Path.home() / ".claude" / "sessions")
        self.lock = threading.Lock()
        self._last = None
        # What was open when the last run ended becomes the offer, until the page decides.
        previous = _read(self.file)
        if isinstance(previous, dict) and previous.get("terminals"):
            _write(self.offer_file, previous)
            try:
                self.file.unlink()
            except OSError:
                pass
        offer = _read(self.offer_file)
        self.offer = offer if isinstance(offer, dict) and offer.get("terminals") else None
        self.offer_saved_at = (self.offer or {}).get("saved_at")

    # ---- saving ------------------------------------------------------------

    def save(self):
        with self.lock:
            alive = [s for s in sorted(self.terms.sessions.values(), key=lambda s: s.started) if s.alive]
            terminals = []
            for s in alive:
                try:
                    terminals.append(describe(s, self.sessions_dir))
                except Exception:  # noqa: BLE001 - one odd terminal must not stop the others being kept
                    continue
            payload = {"saved_at": time.time(), "terminals": terminals}
            key = json.dumps(terminals, sort_keys=True)
            if key == self._last:
                return payload
            self._last = key
            _write(self.file, payload)
            return payload

    def run(self):
        def loop():
            while True:
                time.sleep(SAVE_EVERY)
                try:
                    self.save()
                except Exception:  # noqa: BLE001 - keep trying; a full disk is not a reason to stop the server
                    pass
        threading.Thread(target=loop, name="keep-terminals", daemon=True).start()

    # ---- the offer ---------------------------------------------------------

    def public_offer(self):
        """What the page shows: no screens, they can be large and are only needed to restore."""
        if not self.offer:
            return None
        return {"saved_at": self.offer.get("saved_at"), "terminals": [
            {k: t.get(k) for k in ("id", "title", "cwd", "kind", "started")} | {"resumes": bool(t.get("claude_session"))}
            for t in self.offer["terminals"]]}

    def decline(self):
        self.offer = None
        try:
            self.offer_file.unlink()
        except OSError:
            pass

    def take(self, ids, spawn):
        """Bring back the chosen terminals with spawn(title, kind, cwd, claude_session, screen);
        answers the old id with the new one, so the page can carry names and order over."""
        if not self.offer:
            raise ValueError("there is nothing to restore")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ValueError("ids must be a list of strings")
        picked = [t for t in self.offer["terminals"] if t.get("id") in set(ids)]
        moved = []
        for t in picked:
            try:
                session = spawn(t)
            except (RuntimeError, OSError) as exc:  # one that cannot start must not cost the others
                moved.append({"old": t.get("id"), "new": None, "error": str(exc)})
                continue
            moved.append({"old": t.get("id"), "new": session.id})
        self.decline()
        self.save()
        return moved

    # ---- the page's settings ----------------------------------------------

    def ui(self):
        data = _read(self.ui_file)
        return data if isinstance(data, dict) and isinstance(data.get("values"), dict) else {"stamp": 0, "values": {}}

    def set_ui(self, body):
        values, stamp = body.get("values"), body.get("stamp")
        if not isinstance(values, dict) or not isinstance(stamp, (int, float)):
            raise ValueError("values and stamp are required")
        clean = {}
        for key, value in values.items():
            if key not in UI_KEYS:
                raise ValueError(f"{key} is not kept here")
            if value is not None and (not isinstance(value, str) or len(value) > UI_VALUE_MAX):
                raise ValueError(f"{key} is too large")
            clean[key] = value
        with self.lock:
            current = self.ui()
            current["values"].update(clean)
            current["values"] = {k: v for k, v in current["values"].items() if v is not None}
            current["stamp"] = max(float(stamp), float(current.get("stamp") or 0))
            _write(self.ui_file, current)
        return {"stamp": current["stamp"]}


def restore_argv(entry, shell_argv, claude_binary):
    """The command that brings one terminal back: Claude Code resumed by its session id (only an
    id shaped like one), or the newest session in that folder, or a shell."""
    if entry.get("kind") == "claude":
        sid = entry.get("claude_session")
        if isinstance(sid, str) and UUID.fullmatch(sid):
            return [claude_binary(), "--resume", sid]
        return [claude_binary(), "--continue"]
    return shell_argv()


def page_state(ui):
    """The kept settings as a JSON island for index.html, safe inside a script element."""
    text = json.dumps(ui, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
