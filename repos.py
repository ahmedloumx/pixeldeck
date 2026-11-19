"""The Repos panel's routes: repositories, branches, commits and files, read from Bitbucket,
and one write: a commit of one edited text file.

Every read is a GET. Every input is checked before it reaches a URL: the repository must be
one of the listed slugs, a commit a hex hash, a branch a plain ref name, a path relative with
no dot segments or control characters, and each path segment is url-encoded on its own.

The commit is refused before any call when it would land straight on main, master, develop
or the repository's main branch, and it always names both its branch and its parent, so a
branch that moved since the file was opened is refused rather than overwritten.

Branch names hold slashes, so a branch is resolved to its commit hash first and the code is
browsed by hash. What a hash lists never changes, so those answers are kept (bounded by
count and by bytes); the repository list and the branch tips move, so they expire.
"""

import re
import threading
import time
from collections import OrderedDict
from urllib.parse import quote

import forge
import gitscan
from bitbucket import BitbucketError

FEATURES = ["repos", "repo-commit"]
BODY_MAX = {"repo-commit": 3 * 1024 * 1024}  # a 512 KB file, JSON-escaped, with room to spare

LIST_TTL = 300  # the repository list
BRANCH_TTL = 60  # branch tips move with every push
LIST_PAGES = 10  # 1000 repositories at most
BRANCH_PAGES = 2  # the newest 200 branches; older ones are found by search
TREE_PAGES = 10  # 1000 entries in one folder
PAGELEN = 100
COMMITS = 30
FILE_MAX = 512 * 1024
TEXT_MAX = 300  # a description or a commit's first line
PATH_MAX = 1024
SEARCH_MAX = 100
KEEP_ITEMS = 400
KEEP_BYTES = 24 * 1024 * 1024

LIST_FIELDS = ("next,size,values.slug,values.name,values.description,values.language,"
               "values.updated_on,values.mainbranch.name")
BRANCH_FIELDS = ("next,size,values.name,values.target.hash,values.target.date,values.target.message,"
                 "values.target.author.raw,values.target.author.user.display_name")
COMMIT_FIELDS = "next,values.hash,values.date,values.message,values.author.raw,values.author.user.display_name"
ATTRIBUTES = ("binary", "lfs", "link", "executable", "subrepository")
UNREADABLE = ("binary", "lfs", "subrepository")  # shown as "open on Bitbucket", never downloaded
# Bitbucket keeps an executable bit or a link only when the upload says so again, in a header
# form that cannot be checked from here: those files are edited on Bitbucket instead.
KEEP_MODE = ("executable", "link")
PROTECTED = ("main", "master", "develop")
CHART_SUFFIX = "-helm-chart"
MESSAGE_MAX = 4000

_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_HASH = re.compile(r"[0-9a-fA-F]{7,40}")
_PROJECT = re.compile(r"[A-Za-z0-9_]{1,64}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# git's own ref rules, plus the double quote: names sit inside a quoted BBQL string.
_BAD_REF = re.compile(r'[\x00-\x20\x7f~^:?*\[\\"]')
_README = re.compile(r"readme(\.(md|markdown|txt|rst))?", re.IGNORECASE)
_MESSAGE_BAD = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # a message keeps its tabs and new lines only


class _Cache:
    """Answers by key, least recently used out first. An entry with a ttl expires; one without
    (pinned by a commit hash) stays until the count or byte bound pushes it out."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.items = OrderedDict()  # key -> (expires or None, cost, value)
        self.bytes = 0

    def get(self, key):
        with self.lock:
            hit = self.items.get(key)
            if hit is None:
                return None
            if hit[0] is not None and self.clock() >= hit[0]:
                self._drop(key)
                return None
            self.items.move_to_end(key)
            return hit[2]

    def put(self, key, value, ttl=None, cost=0):
        with self.lock:
            if key in self.items:
                self._drop(key)
            self.items[key] = (None if ttl is None else self.clock() + ttl, cost, value)
            self.bytes += cost
            while len(self.items) > KEEP_ITEMS or (self.bytes > KEEP_BYTES and len(self.items) > 1):
                self._drop(next(iter(self.items)))
        return value

    def _drop(self, key):
        self.bytes -= self.items.pop(key)[1]

    def forget(self, match):
        with self.lock:
            for key in [k for k in self.items if match(k)]:
                self._drop(key)


CACHE = _Cache()


def reset(clock=time.monotonic):
    """A fresh cache; tests pass their own clock."""
    global CACHE
    CACHE = _Cache(clock)


# ---- input checks ----------------------------------------------------------


def _slug_form(value):
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ValueError("bad repository name")
    return value


def check_hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("a commit is 7 to 40 hex characters")
    return value.lower()


def check_path(value):
    """A path inside the repository: relative, no empty, '.' or '..' segment, no control
    characters or backslashes. A name that merely holds two dots (a..b.txt) is fine: only a
    whole '..' segment could climb out. '' is the root."""
    if not isinstance(value, str) or len(value) > PATH_MAX:
        raise ValueError("bad path")
    if not value:
        return ""
    if value.startswith("/") or "\\" in value or _CONTROL.search(value):
        raise ValueError("bad path")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("bad path")
    return value


def check_branch(value):
    if not isinstance(value, str) or not 0 < len(value) <= 255 or _BAD_REF.search(value):
        raise ValueError("bad branch name")
    if value.startswith(("-", "/")) or value.endswith(("/", ".", ".lock")) or ".." in value or "//" in value or "@{" in value:
        raise ValueError("bad branch name")
    return value


def check_search(value):
    value = (value or "").strip() if isinstance(value, str) else None
    if value is None or len(value) > SEARCH_MAX or _CONTROL.search(value) or '"' in value or "\\" in value:
        raise ValueError("search text must be under 100 characters, without quotes or backslashes")
    return value


def _quoted(path):
    return "/".join(quote(part, safe="") for part in path.split("/"))


# ---- shaping ---------------------------------------------------------------


def _first_line(text):
    return (text or "").strip().split("\n", 1)[0].strip()[:TEXT_MAX]


def _who(author):
    author = author or {}
    name = (author.get("user") or {}).get("display_name")
    if name:
        return name
    raw = author.get("raw") or ""
    return raw.split("<", 1)[0].strip() or raw  # "Name <mail>": the name is enough


def _entry(raw):
    path = raw.get("path") or ""
    attrs = [a for a in raw.get("attributes") or [] if a in ATTRIBUTES]
    return {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "type": "dir" if raw.get("type") == "commit_directory" else "file",
        "size": raw.get("size"),
        "attributes": attrs,
    }


def _ws(engine):
    return quote(engine.client.workspace or "", safe="")


def _web(engine, slug):
    return f"https://bitbucket.org/{_ws(engine)}/{quote(slug, safe='')}"


# ---- the answers -----------------------------------------------------------


def repo_list(engine, force=False):
    """The chosen project's repositories (or the workspace's), newest first."""
    project = engine.project()
    key = (project or {}).get("key") or ""
    if key and not _PROJECT.fullmatch(key):
        raise ValueError("bad project key")
    hit = None if force else CACHE.get(("repos", key))
    if hit is not None:
        return hit
    params = {"sort": "-updated_on", "pagelen": PAGELEN, "fields": LIST_FIELDS}
    if key:
        params["q"] = f'project.key="{key}"'
    meta = {}
    raw = list(engine.client.paged(f"/repositories/{_ws(engine)}", params, max_pages=LIST_PAGES, meta=meta))
    repos = []
    for r in raw:
        slug = r.get("slug")
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            continue
        repos.append({
            "slug": slug,
            "name": r.get("name") or slug,
            "description": (r.get("description") or "").strip()[:TEXT_MAX],
            "language": r.get("language") or "",
            "mainbranch": (r.get("mainbranch") or {}).get("name") or "",
            "updated_on": r.get("updated_on") or "",
            "url": _web(engine, slug),
        })
    repos.sort(key=lambda r: r["updated_on"], reverse=True)
    pairs = pair_charts([r["slug"] for r in repos])
    charts = {}
    for chart, service in pairs.items():
        charts.setdefault(service, []).append(chart)
    for r in repos:
        slug = r["slug"]
        r["charts"] = charts.get(slug, [])
        r["service"] = pairs.get(slug, "")
        r["kind"] = "helm" if slug.endswith(CHART_SUFFIX) else "service" if r["charts"] else "other"
    out = {"project": project, "repos": repos, "total": meta.get("size") or len(repos), "at": time.time()}
    return CACHE.put(("repos", key), out, ttl=LIST_TTL)


def pair_charts(slugs):
    """Each helm chart repository and the service it deploys, by name: the chart is the
    service's slug plus "-helm-chart", or that with a trailing "-service" dropped first
    (x-service-helm-chart deploys x). A chart matching neither is an infrastructure chart.
    Two charts can claim one service: the exact match comes first, then by name."""
    names = set(slugs)
    services = {s for s in names if not s.endswith(CHART_SUFFIX)}
    found = []
    for chart in names - services:
        base = chart[: -len(CHART_SUFFIX)]
        if base in services:
            found.append((base, 0, chart))
        elif base.endswith("-service") and base[: -len("-service")] in services:
            found.append((base[: -len("-service")], 1, chart))
    return {chart: service for service, _, chart in sorted(found)}


def known_repo(engine, value):
    slug = _slug_form(value)
    for repo in repo_list(engine)["repos"]:
        if repo["slug"] == slug:
            return slug
    raise KeyError(f"{slug} is not one of this project's repositories")


def _branch_row(raw):
    target = raw.get("target") or {}
    return {
        "name": raw.get("name") or "",
        "hash": target.get("hash") or "",
        "date": target.get("date") or "",
        "author": _who(target.get("author")),
        "message": _first_line(target.get("message")),
    }


def branches(engine, slug, search=""):
    """The newest branches, or those whose name holds the search text."""
    key = ("branches", slug, search)
    hit = CACHE.get(key)
    if hit is not None:
        return hit
    params = {"sort": "-target.date", "pagelen": PAGELEN, "fields": BRANCH_FIELDS}
    if search:
        params["q"] = f'name~"{search}"'
    meta = {}
    path = f"/repositories/{_ws(engine)}/{slug}/refs/branches"
    rows = [_branch_row(v) for v in engine.client.paged(path, params, max_pages=1 if search else BRANCH_PAGES, meta=meta)]
    rows = [b for b in rows if b["name"] and b["hash"]]
    total = meta.get("size")
    out = {"repo": slug, "search": search, "branches": rows, "total": total if total is not None else len(rows),
           "complete": total is None or len(rows) >= total}
    return CACHE.put(key, out, ttl=BRANCH_TTL)


def resolve(engine, slug, branch):
    """A branch's tip commit: from the fresh branch list when it is there, else one lookup."""
    listed = CACHE.get(("branches", slug, ""))
    for row in (listed or {}).get("branches", []):
        if row["name"] == branch:
            return row
    key = ("ref", slug, branch)
    hit = CACHE.get(key)
    if hit is not None:
        return hit
    raw = engine.client.get(f"/repositories/{_ws(engine)}/{slug}/refs/branches",
                            {"q": f'name="{branch}"', "fields": BRANCH_FIELDS})
    for value in raw.get("values") or []:
        row = _branch_row(value)
        if row["name"] == branch and row["hash"]:
            return CACHE.put(key, row, ttl=BRANCH_TTL)
    raise KeyError(f"no branch {branch} in {slug}")


def commits(engine, slug, sha):
    """The newest commits reachable from a commit; fixed for a given hash."""
    key = ("commits", slug, sha)
    hit = CACHE.get(key)
    if hit is not None:
        return hit
    raw = engine.client.get(f"/repositories/{_ws(engine)}/{slug}/commits/{sha}",
                            {"pagelen": COMMITS, "fields": COMMIT_FIELDS})
    rows = [{"hash": c.get("hash") or "", "date": c.get("date") or "", "author": _who(c.get("author")),
             "message": _first_line(c.get("message"))} for c in raw.get("values") or []]
    out = {"repo": slug, "hash": sha, "commits": [c for c in rows if c["hash"]], "more": bool(raw.get("next"))}
    return CACHE.put(key, out, cost=1024)


def tree(engine, slug, sha, path):
    """One folder at a commit: folders first, then files, by name."""
    key = ("tree", slug, sha, path)
    hit = CACHE.get(key)
    if hit is not None:
        return hit
    url = f"/repositories/{_ws(engine)}/{slug}/src/{sha}/" + (_quoted(path) + "/" if path else "")
    raw = list(engine.client.paged(url, {"pagelen": PAGELEN}, max_pages=TREE_PAGES))
    entries = [_entry(v) for v in raw if v.get("path")]
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    readme = next((e["path"] for e in entries if e["type"] == "file" and _README.fullmatch(e["name"])
                   and not set(e["attributes"]) & set(UNREADABLE)), None)
    out = {"repo": slug, "hash": sha, "path": path, "entries": entries, "readme": readme,
           "truncated": len(raw) >= TREE_PAGES * PAGELEN}
    return CACHE.put(key, out, cost=256 * len(entries))


def read_file(engine, slug, sha, path):
    """A file's text at a commit, cut at FILE_MAX. Binary, LFS and submodule entries are named,
    not downloaded; a NUL byte in what comes back also marks the file binary."""
    if not path:
        raise ValueError("name a file")
    key = ("file", slug, sha, path)
    hit = CACHE.get(key)
    if hit is not None:
        return hit
    url = f"/repositories/{_ws(engine)}/{slug}/src/{sha}/{_quoted(path)}"
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    listing = CACHE.get(("tree", slug, sha, parent))
    entry = next((e for e in (listing or {}).get("entries", []) if e["path"] == path), None)
    if entry is None:  # opened straight from a remembered path: one metadata call, no download
        entry = _entry(engine.client.get(url, {"format": "meta"}))
    if entry["type"] != "file":
        raise ValueError("that path is a folder")
    out = {"repo": slug, "hash": sha, "path": path, "size": entry.get("size"), "attributes": entry["attributes"],
           "binary": bool(set(entry["attributes"]) & set(UNREADABLE)), "text": None, "cut": False}
    if not out["binary"]:
        text, cut = engine.client.text(url, FILE_MAX)
        if "\x00" in text:
            out["binary"] = True
        else:
            out["text"], out["cut"] = text, cut
    out["endings"] = endings(out["text"]) if out["text"] is not None else None
    out["locked"] = _locked(out)
    return CACHE.put(key, out, cost=len(out["text"] or "") + 256)


def endings(text):
    """The file's line endings: "lf", "crlf", "none" (one line), or "mixed" (both, or a lone CR)."""
    crlf = text.count("\r\n")
    lone_cr = text.count("\r") - crlf
    lone_lf = text.count("\n") - crlf
    if lone_cr or (crlf and lone_lf):
        return "mixed"
    return "crlf" if crlf else "lf" if lone_lf else "none"


def _locked(out):
    """Why this file cannot be edited here, or None. Whatever is shown must be the whole file,
    byte for byte once encoded again, or saving it would change more than the edit."""
    if out["binary"] or out["text"] is None:
        return "Binary files are not edited here."
    if out["cut"]:
        return "Files over 512 KB are edited on Bitbucket."
    if "\ufffd" in out["text"]:
        return "This file is not plain UTF-8: edit it on Bitbucket so its bytes stay as they are."
    if set(out["attributes"]) & set(KEEP_MODE):
        return "Executable files and links are edited on Bitbucket, so their mode stays."
    if out["endings"] == "mixed":
        return "This file mixes line endings: edit it on Bitbucket so they stay as they are."
    return None


# ---- the one write ---------------------------------------------------------


def _tip(engine, slug, branch):
    """A branch's tip right now, past every cache, or None when there is no such branch."""
    raw = engine.client.get(f"/repositories/{_ws(engine)}/{slug}/refs/branches",
                            {"q": f'name="{branch}"', "fields": BRANCH_FIELDS})
    return next((r for r in map(_branch_row, raw.get("values") or []) if r["name"] == branch and r["hash"]), None)


def commit(engine, body):
    """Commit one edited text file: to the branch it was opened on, or to a new branch made
    from the commit it was opened at. Checked in this order: the request itself, the
    protected names, the original file at that commit, then the branches as they are now."""
    if not isinstance(body, dict):
        raise ValueError("a JSON body is required")
    slug = known_repo(engine, body.get("repo"))
    branch = check_branch(body.get("branch"))
    parent = check_hash(body.get("parent"))
    if len(parent) != 40:
        raise ValueError("the parent must be a full 40-character commit hash")
    path = check_path(body.get("path"))
    if not path or '"' in path:
        raise ValueError("name the file to commit")
    message = body.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > MESSAGE_MAX or _MESSAGE_BAD.search(message):
        raise ValueError(f"the commit message must be 1 to {MESSAGE_MAX} characters, without control characters")
    content = body.get("content")
    if not isinstance(content, str):
        raise ValueError("the file's new text is required")
    data = content.encode("utf-8")
    if len(data) > FILE_MAX:
        raise ValueError("files over 512 KB are edited on Bitbucket")
    new = body.get("new_branch") or ""
    target = check_branch(new) if new else branch
    main = next((r["mainbranch"] for r in repo_list(engine)["repos"] if r["slug"] == slug), "")
    if target in PROTECTED or target == main:
        raise ValueError(f"PixelDeck does not commit straight to {target}: commit to a new branch, then open a pull request")

    original = read_file(engine, slug, parent, path)
    if original["locked"]:
        raise ValueError(original["locked"])
    if content == original["text"]:
        raise ValueError("nothing changed")
    now = endings(content)
    if now == "mixed" or (original["endings"] in ("lf", "crlf") and now not in (original["endings"], "none")):
        raise ValueError(f"the line endings changed: keep the file's own ({original['endings']})")

    if new:
        if _tip(engine, slug, new):
            raise ValueError(f"a branch named {new} exists already: pick another name")
    else:
        tip = _tip(engine, slug, branch)
        if not tip:
            raise KeyError(f"no branch {branch} in {slug}")
        if tip["hash"] != parent:
            raise ValueError(f"{branch} moved since you opened the file (now at {tip['hash'][:7]}): reload it and edit again")

    # branch and parents always go together: without branch Bitbucket commits on the main
    # branch, and with parents it refuses (409) a branch whose tip is no longer that commit.
    fields = [("message", message.rstrip()), ("branch", target), ("parents", parent)]
    files = [("/" + path, path.rsplit("/", 1)[-1], data)]  # the leading slash keeps "message" a file
    try:
        engine.client.post_form(f"/repositories/{_ws(engine)}/{slug}/src", fields, files)
    except BitbucketError as exc:
        if exc.status in (401, 403):
            raise PermissionError(f"Bitbucket refused the commit ({exc.status}): the token may lack repository write. "
                                  "Give it the Repositories: Write permission, then try again.") from None
        if exc.status == 409:
            raise ValueError(f"{branch} moved since you opened the file: reload it and edit again") from None
        raise
    CACHE.forget(lambda k: k[0] in ("branches", "ref") and k[1] == slug)
    out = {"repo": slug, "branch": target, "from": branch, "parent": parent, "path": path, "new_branch": bool(new), "hash": None}
    # The commit is made: what follows only helps the page along, so it never fails the answer.
    try:
        tip = _tip(engine, slug, target)
        out["hash"] = tip["hash"] if tip else None
    except BitbucketError:
        pass
    if new:
        out["pr"] = pr_flow(engine, slug, target, branch)
    return out


def pr_flow(engine, slug, branch, dest):
    """How to open a pull request from a branch made here. PixelDeck's own dialog reads the
    branch from your local clone, so the branch is fetched there first; without a clone, or
    when the branch's author is not your git identity, Bitbucket's own page does it."""
    url = f"{_web(engine, slug)}/pull-requests/new?source={quote(branch, safe='')}&dest={quote(dest, safe='')}"
    try:
        repo_dir = gitscan.repo_dirs(engine.root).get(slug)
        if repo_dir is not None:
            gitscan.git(repo_dir, "fetch", "--quiet", "origin", branch, timeout=30)
            if any(i["repo"] == slug and i["branch"] == branch for i in engine.pushed(force=True)):
                return {"flow": "preview"}
    except Exception:  # noqa: BLE001 - the commit is made; a failed fetch only means the web page
        pass
    return {"flow": "web", "url": url}


# ---- routing ---------------------------------------------------------------


def _routes():
    return {
        "repos": lambda e, q: repo_list(e, force=q("force") == "1"),
        "repo-branches": lambda e, q: branches(e, known_repo(e, q("repo")), check_search(q("q"))),
        "repo-ref": lambda e, q: resolve(e, known_repo(e, q("repo")), check_branch(q("branch"))),
        "repo-commits": lambda e, q: commits(e, known_repo(e, q("repo")), check_hash(q("hash"))),
        "repo-tree": lambda e, q: tree(e, known_repo(e, q("repo")), check_hash(q("hash")), check_path(q("path"))),
        "repo-file": lambda e, q: read_file(e, known_repo(e, q("repo")), check_hash(q("hash")), check_path(q("path"))),
    }


ROUTES = _routes()
WRITES = {"repo-commit": commit}


def route(handler, engine, method, name, query, body):
    """Answer a route this module owns, or None. A dict or list goes out as JSON; a
    (bytes, content type) pair goes out as is."""
    mine = (method == "POST" and name in WRITES) or (method == "GET" and name in ROUTES)
    if mine and forge.kind(engine.client) != "bitbucket":
        # The panel reads Bitbucket's source API; its pull requests work on every host.
        raise ValueError(f"The Repos panel reads Bitbucket repositories only, for now. "
                         f"Your {forge.label(forge.kind(engine.client))} pull requests are in the Pull Requests panel.")
    if method == "POST" and name in WRITES:
        return WRITES[name](engine, body)
    answer = ROUTES.get(name)
    if answer is None or method != "GET":
        return None
    return answer(engine, lambda k: (query.get(k) or [""])[0])
