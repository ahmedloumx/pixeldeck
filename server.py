"""PixelDeck: a local dashboard for Bitbucket pull requests.

Security model, because this server can open a terminal that pushes code:
- binds 127.0.0.1 only;
- rejects any Host header other than 127.0.0.1:<port> / localhost:<port>
  (stops DNS rebinding);
- every /api call needs the per-start secret that is injected into the page,
  sent back as the X-Desk-Token header (a cross-site page cannot read it);
- the launch endpoint takes only {repo, id, action}; the brief is built from
  the server's own cache.
"""

import argparse
import re
import base64
import os
import json
import mimetypes
import secrets
import shutil
import sys
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import actions  # noqa: E402
import launcher  # noqa: E402
import terminals  # noqa: E402
import opener  # noqa: E402
import proposals  # noqa: E402
from bitbucket import BitbucketError  # noqa: E402
from engine import Engine  # noqa: E402

import config  # noqa: E402

STATIC = HERE / "static"
WORKSPACE_ROOT = config.workspace_root()
SECRET = secrets.token_urlsafe(32)
# A second key, for an agent proposing a pull request from a terminal: it can ask and read the
# answer, nothing else. Written to the state folder (mode 600) at start; see proposals.py.
AGENT_KEY = secrets.token_urlsafe(32)
AGENT_ROUTES = {("POST", "propose"), ("GET", "proposal")}
PROPOSALS = proposals.Proposals()
FEATURES = ["merge", "sync", "drop", "proposals", "snapshots", "reviews"]
SNAPSHOT_MAX = 4 * 1024 * 1024
MIN_COLS, MIN_ROWS = 20, 4  # a collapsed panel must not squeeze a program into a sliver
TERMS = terminals.Terminals()
MAX_BODY = 64 * 1024
DROP_MAX = 50 * 1024 * 1024
DROP_KEEP_DAYS = 7


def save_drop(folder, name, stream, length):
    """Write one file dropped on a terminal into folder and return its path.

    Only a bare file name survives (never a path), an existing file is never replaced, and
    no more than length bytes are read. Drops older than a week are cleared on the way in.
    """
    base = os.path.basename(str(name).replace("\\", "/")).strip()
    if not base or base in (".", "..") or len(base) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in base):
        raise ValueError("unusable file name")
    base = re.sub(r'[<>:"|?*]', "_", base)  # characters Windows will not take in a name
    if not 0 < length <= DROP_MAX:
        raise ValueError(f"a dropped file must be between 1 byte and {DROP_MAX // 1048576} MB")
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, 0o700)
    cutoff = time.time() - DROP_KEEP_DAYS * 86400
    for old in folder.iterdir():
        if old.is_file() and old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}-{base}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    left = length
    try:
        with os.fdopen(fd, "wb") as out:
            while left:
                chunk = stream.read(min(65536, left))
                if not chunk:
                    raise ValueError("the upload ended early")
                out.write(chunk)
                left -= len(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


class Desk(BaseHTTPRequestHandler):
    server_version = "PixelDeck/1.0"
    engine: Engine = None
    port = 0

    # ---- helpers --------------------------------------------------------

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or "") and not args[1:2] == ("200",):
            sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _host_ok(self):
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if self.headers.get("Host", "") not in allowed:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in allowed}

    def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message, **more):
        self._send(status, {"error": message, **more})

    def _authorized(self, method="", route=""):
        if secrets.compare_digest(self.headers.get("X-Desk-Token", ""), SECRET):
            return True
        return (method, route) in AGENT_ROUTES and secrets.compare_digest(self.headers.get("X-Agent-Key", ""), AGENT_KEY)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("body too large")
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("JSON body required")
        return json.loads(self.rfile.read(length) or b"{}")

    # ---- routing --------------------------------------------------------

    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        url = urlparse(self.path)
        if url.path == "/healthz":
            return self._send(200, b"ok", "text/plain")
        if url.path in ("/", "/index.html"):
            return self._index()
        if url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        if url.path.startswith("/api/"):
            if not self._authorized("GET", url.path[5:]):
                return self._error(HTTPStatus.UNAUTHORIZED, "stale page token")
            return self._api("GET", url.path[5:], parse_qs(url.query), None)
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        if not self._authorized("POST", url.path[5:]):
            return self._error(HTTPStatus.UNAUTHORIZED, "stale page token")
        if url.path == "/api/term-snapshot":  # the serialized screen is text far above the JSON cap
            query = parse_qs(url.query)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= SNAPSHOT_MAX:
                    raise ValueError("snapshot size out of range")
                num = lambda k: int((query.get(k) or ["0"])[0])  # noqa: E731
                session = TERMS.get((query.get("id") or [""])[0])
                kept = session.remember(self.rfile.read(length).decode("utf-8", "replace"), num("pos"), num("cols"), num("rows"))
            except KeyError as exc:
                return self._error(HTTPStatus.NOT_FOUND, str(exc).strip("'"))
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return self._send(200, {"kept": kept})
        if url.path == "/api/term-drop":  # raw file bytes, not JSON: read before _body() would refuse them
            try:
                length = int(self.headers.get("Content-Length") or 0)
                path = save_drop(config.folder("state") / "drops", unquote(self.headers.get("X-File-Name") or ""), self.rfile, length)
            except (ValueError, OSError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return self._send(200, {"path": str(path)})
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        return self._api("POST", url.path[5:], {}, body)

    def _index(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__DESK_TOKEN__", SECRET)
        self._send(200, html.encode(), "text/html; charset=utf-8", {
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; "
                                       "style-src 'self' 'unsafe-inline'; "
                                       "script-src 'self'; font-src 'self'; connect-src 'self'; "
                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        })

    def _static(self, rel):
        if any(part.startswith(".") for part in Path(rel).parts):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        target = (STATIC / rel).resolve()
        if STATIC not in target.parents or not target.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".woff2":
            ctype = "font/woff2"
        self._send(200, target.read_bytes(), ctype)

    def _api(self, method, route, query, body):
        engine = self.engine
        try:
            if method == "GET" and route == "bootstrap":
                return self._send(200, self._bootstrap())
            if method == "GET" and route == "person":
                uuid = (query.get("uuid") or [""])[0]
                return self._send(200, self._person(uuid))
            if method == "GET" and route == "team":
                return self._send(200, self._team())
            if method == "GET" and route == "pr":
                repo = (query.get("repo") or [""])[0]
                pr_id = int((query.get("id") or ["0"])[0])
                force = (query.get("force") or ["0"])[0] == "1"
                return self._send(200, engine.detail(repo, pr_id, force=force))
            if method == "GET" and route == "pr-diff":
                return self._send(200, engine.pr_diff((query.get("repo") or [""])[0], int((query.get("id") or ["0"])[0])))
            if method == "GET" and route == "pr-extra":
                return self._send(200, engine.pr_extra((query.get("repo") or [""])[0], int((query.get("id") or ["0"])[0])))
            if method == "GET" and route == "terms":
                return self._send(200, {"terms": TERMS.list()})
            if method == "GET" and route == "term-stream":
                return self._stream_term((query.get("cursors") or [""])[0])
            if method == "POST" and route == "terms":
                if body.get("kind") != "shell":
                    raise ValueError("only a shell can be opened directly")
                session = TERMS.spawn("Shell", shell_argv(), WORKSPACE_ROOT)
                return self._send(200, session.info())
            if method == "POST" and route == "term-input":
                data = body.get("data")
                if not isinstance(data, str) or len(data) > 65536:
                    raise ValueError("data must be a string under 64 KiB")
                TERMS.get(str(body.get("id"))).write(data.encode("utf-8"))
                return self._send(200, {"ok": True})
            if method == "POST" and route == "term-resize":
                cols, rows = body.get("cols"), body.get("rows")
                if not (isinstance(cols, int) and isinstance(rows, int) and 2 <= cols <= 1000 and 2 <= rows <= 500):
                    raise ValueError("cols and rows must be sane integers")
                if cols < MIN_COLS or rows < MIN_ROWS:
                    return self._send(200, {"ok": False, "ignored": "too small"})
                TERMS.get(str(body.get("id"))).resize(cols, rows)
                return self._send(200, {"ok": True})
            if method == "POST" and route == "term-close":
                TERMS.forget(str(body.get("id")))
                return self._send(200, {"terms": TERMS.list()})
            if method == "GET" and route == "branch":
                repo = (query.get("repo") or [""])[0]
                branch = (query.get("branch") or [""])[0]
                return self._send(200, actions.preview(engine, WORKSPACE_ROOT, repo, branch))
            if method == "POST" and route == "pr-create":
                result = actions.create(engine, WORKSPACE_ROOT, body)
                PROPOSALS.opened_elsewhere(body.get("repo"), body.get("branch"), result)
                return self._send(200, result)
            if method == "POST" and route == "propose":
                return self._send(200, PROPOSALS.propose(engine, WORKSPACE_ROOT, body))
            if method == "GET" and route == "proposal":
                return self._send(200, PROPOSALS.get((query.get("id") or [""])[0]))
            if method == "GET" and route == "proposals":
                return self._send(200, {"proposals": PROPOSALS.list()})
            if method == "POST" and route == "proposal-decide":
                fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
                return self._send(200, PROPOSALS.decide(engine, WORKSPACE_ROOT, str(body.get("id") or ""), body.get("action"), fields))
            if method == "GET" and route == "branch-diff":
                q = lambda k: (query.get(k) or [""])[0]  # noqa: E731
                return self._send(200, actions.branch_diff(WORKSPACE_ROOT, q("repo"), q("branch"), q("dest")))
            if method == "POST" and route == "branch-delete":
                return self._send(200, actions.delete_branch(engine, WORKSPACE_ROOT, body))
            if method == "POST" and route == "decline":
                return self._send(200, actions.decline(engine, body))
            if method == "POST" and route == "merge":
                return self._send(200, actions.merge(engine, body))
            if method == "POST" and route == "review":
                return self._send(200, actions.review(engine, body))
            if method == "POST" and route == "sync":
                return self._send(200, actions.sync(engine, WORKSPACE_ROOT, body))
            if method == "GET" and route == "pr-sync":
                repo = (query.get("repo") or [""])[0]
                pr_id = int((query.get("id") or ["0"])[0])
                return self._send(200, actions.sync_status(engine, WORKSPACE_ROOT, repo, pr_id))
            if method == "GET" and route == "projects":
                return self._send(200, {"projects": engine.projects(), "project": engine.project()})
            if method == "POST" and route == "project":
                key = body.get("key")
                if key is not None and not isinstance(key, str):
                    return self._error(HTTPStatus.BAD_REQUEST, "key must be a string")
                return self._send(200, {"project": engine.set_project(key or "")})
            if method == "GET" and route == "candidates":
                return self._send(200, {"candidates": engine.candidates(), "team": [m["uuid"] for m in engine.team()]})
            if method == "POST" and route == "refresh":
                return self._send(200, self._refresh(body.get("uuid")))
            if method == "POST" and route == "team":
                uuids = body.get("uuids")
                if not isinstance(uuids, list) or not all(isinstance(u, str) for u in uuids):
                    return self._error(HTTPStatus.BAD_REQUEST, "uuids must be a list of strings")
                return self._send(200, {"team": engine.set_team(uuids)})
            if method == "POST" and route == "launch":
                return self._send(200, self._launch(body))
            if method == "POST" and route == "open":
                url = body.get("url")
                if not isinstance(url, str):
                    raise ValueError("url required")
                return self._send(200, {"browser": opener.open_url(url)})
        except BitbucketError as exc:
            return self._error(HTTPStatus.BAD_GATEWAY, exc.message, upstream=exc.status, auth=engine.auth)
        except actions.SyncConflict as exc:
            return self._error(HTTPStatus.CONFLICT, str(exc), conflicts=exc.files)
        except KeyError as exc:
            return self._error(HTTPStatus.NOT_FOUND, str(exc).strip("'"))
        except PermissionError as exc:
            return self._error(HTTPStatus.FORBIDDEN, str(exc))
        except (ValueError, KeyError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001 - one error shape for the page
            traceback.print_exc()
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
        return self._error(HTTPStatus.NOT_FOUND, f"no route {method} {route}")

    # ---- handlers -------------------------------------------------------

    def _bootstrap(self):
        engine = self.engine
        if engine.auth.get("ok") is None and not engine.me():
            try:
                engine.refresh_identity()
            except BitbucketError:
                pass
        return {
            "me": engine.me(),
            "auth": engine.auth,
            "team": engine.team(),
            "budget": engine.budget(),
            "launches": engine.data.get("launches", [])[:8],
            "project": engine.project(),
            "root": str(WORKSPACE_ROOT),
            "platform": sys.platform,
            "terminal": terminals.UNAVAILABLE or "",
            "features": FEATURES,
            "now": time.time(),
        }

    def _with_history(self, prs):
        out = []
        for pr in prs:
            hist = self.engine.history(pr)
            out.append({**pr, "changes_requested_by": None if hist is None else
                        sorted({c["name"] for c in hist["changes_requested"] if c.get("name")})})
        return out

    def _scoped(self, prs):
        repos = self.engine.project_repos()
        return prs if repos is None else [p for p in prs if p.get("repo") in repos]

    def _stream_term(self, cursors):
        """One stream for every terminal, as newline-delimited JSON:
        {"id", "p", "d"} output, {"id", "exit"} when a process ends, {"id", "gone"} when closed.
        cursors is "id:offset,id:offset"; sessions it does not name start from their scrollback."""
        pos = {}
        for part in cursors.split(","):
            if ":" in part:
                sid, _, off = part.partition(":")
                if off.isdigit():
                    pos[sid] = int(off)
        reported_exit, known = set(), set(pos)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        version = -1
        try:
            while True:
                lines = []
                current = dict(TERMS.sessions)
                for sid in known - set(current):
                    lines.append({"id": sid, "gone": True})
                known = set(current)
                for sid, session in current.items():
                    first = sid not in pos
                    with session.cond:
                        snap = session.snapshot if first else None
                        if snap and snap["pos"] < session.base:
                            snap = None  # its tail scrolled out of the buffer
                        start = snap["pos"] if snap else max(pos.get(sid, 0), session.base)
                        data = bytes(session.buf[start - session.base:])
                        end = session.end
                        size = {"cols": session.cols, "rows": session.rows}
                    if snap:
                        lines.append({"id": sid, "t": session.title, "snap": snap["text"], "sp": snap["pos"], "cols": snap["cols"], "rows": snap["rows"]})
                    if data or first:
                        line = {"id": sid, "t": session.title, "p": end, "d": base64.b64encode(data).decode()}
                        if first and not snap:
                            line.update(size)  # replay at the size the output was written for
                        lines.append(line)
                        pos[sid] = end
                    if not session.alive and sid not in reported_exit:
                        lines.append({"id": sid, "t": session.title, "exit": session.exit_code})
                        reported_exit.add(sid)
                out = "".join(json.dumps(x) + "\n" for x in lines) or "{}\n"
                self.wfile.write(out.encode())
                self.wfile.flush()
                version = TERMS.wait(version, timeout=15.0)
        except (BrokenPipeError, ConnectionResetError):
            return None

    def _decorate(self, prs):
        return [{**pr, "attention": self.engine.attention(pr)} for pr in prs]

    def _person(self, uuid):
        engine = self.engine
        me = engine.me() or {}
        if not uuid:
            raise ValueError("uuid required")
        entry = engine.person(uuid)
        if entry is None and engine.auth.get("ok") is not False:
            try:
                entry = engine.refresh_person(uuid)
            except BitbucketError as exc:
                entry = {"open": [], "merged": [], "error": exc.message}
        entry = entry or {"open": [], "merged": []}
        out = {
            "uuid": uuid,
            "open": self._decorate(self._scoped(entry.get("open", []))),
            "merged": self._with_history(self._scoped(entry.get("merged", []))),
            "declined": self._scoped(entry.get("declined", [])),
            "project": engine.project(),
            "merged_total": entry.get("merged_total"),
            "synced_at": entry.get("synced_at"),
            "error": entry.get("error"),
            "auth": engine.auth,
            "budget": engine.budget(),
        }
        if uuid == me.get("uuid"):
            out["review_requests"] = self._review_requests(uuid)
            try:
                out["pushed"] = self._scoped(engine.pushed())
            except Exception as exc:  # noqa: BLE001 - a git hiccup must not blank the page
                out["pushed"] = []
                out["pushed_error"] = str(exc)
        return out

    def _review_requests(self, my_uuid):
        out = []
        for mate in self.engine.team():
            entry = self.engine.person(mate["uuid"]) or {}
            for pr in self._scoped(entry.get("open", [])):
                mine = next((r for r in pr.get("reviews", []) if r["uuid"] == my_uuid), None)
                if mine and mine.get("reviewer") and mine["state"] in ("pending", "commented"):
                    out.append(pr)
        out.sort(key=lambda p: p.get("updated_on") or "")
        return out

    def _team(self):
        engine = self.engine
        members = []
        me = engine.me()
        people = ([{**me, "is_me": True}] if me else []) + engine.team()
        for mate in people:
            entry = engine.person(mate["uuid"]) or {}
            members.append({
                "user": mate,
                "open": self._scoped(entry.get("open", [])),
                "merged": self._with_history(self._scoped(entry.get("merged", []))),
                "declined": self._scoped(entry.get("declined", [])),
                "merged_total": entry.get("merged_total"),
                "synced_at": entry.get("synced_at"),
                "error": entry.get("error"),
            })
        return {"members": members, "me": engine.me(), "budget": engine.budget()}

    def _refresh(self, uuid):
        engine = self.engine
        if not engine.me() or engine.auth.get("ok") is not True:
            engine.refresh_identity()
        me = engine.me()["uuid"]
        if uuid == "team":
            for mate in engine.team():
                engine.refresh_person(mate["uuid"])
            return self._team()
        uuid = uuid or me
        engine.refresh_person(uuid)
        if uuid == me:
            engine.warm_details(me, budget=30)
            engine.pushed(force=True)
        return self._person(uuid)

    def _launch(self, body):
        engine = self.engine
        repo, pr_id, action = body.get("repo"), body.get("id"), body.get("action")
        if not isinstance(repo, str) or not isinstance(pr_id, int) or action not in launcher.ACTIONS:
            raise ValueError("repo, id and a known action are required")
        pr = engine.find_pr(repo, pr_id)
        if pr is None:
            raise ValueError("unknown pull request; refresh first")
        detail = engine.detail(repo, pr_id)
        me = (engine.me() or {}).get("uuid")
        where = body.get("where", "app")
        record = launcher.launch(WORKSPACE_ROOT, action, detail["pr"], detail, pr["author"]["uuid"] == me,
                                 terms=TERMS if where == "app" else None)
        record.update({"repo": repo, "id": pr_id, "action": action, "pr_title": pr["title"]})
        engine.record_launch(record)
        return record


def shell_argv():
    """A login shell on Linux and macOS; PowerShell on Windows, or cmd when it is missing."""
    if sys.platform == "win32":
        for name in ("pwsh", "powershell"):
            found = shutil.which(name)
            if found:
                return [found, "-NoLogo"]
        return [os.environ.get("COMSPEC", "cmd.exe")]
    return [os.environ.get("SHELL") or "/bin/sh", "-l"]


def write_agent_key(port):
    """Where pixeldeck.py --propose finds the propose-only key (and the port) of this run."""
    path = config.folder("state") / "agent.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"port": port, "key": AGENT_KEY}))
    if os.name != "nt":
        os.chmod(path, 0o600)


def main():
    parser = argparse.ArgumentParser(description="PixelDeck local server")
    parser.add_argument("--port", type=int, default=config.port())
    parser.add_argument("--no-refresh", action="store_true", help="serve the cache only")
    args = parser.parse_args()

    Desk.engine = Engine(WORKSPACE_ROOT)
    Desk.port = args.port
    write_agent_key(args.port)
    if not args.no_refresh:
        Desk.engine.run_forever()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Desk)
    httpd.daemon_threads = True
    print(f"PixelDeck on http://127.0.0.1:{args.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
