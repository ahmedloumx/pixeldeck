"""The Repos panel's routes: repositories, branches, commits and files, read from Bitbucket.

Read only: every call here is a GET. Every input is checked before it reaches a URL: the
repository must be one of the listed slugs, a commit a hex hash, a branch a plain ref name,
a path relative with no dot segments or control characters, and each path segment is
url-encoded on its own.

Branch names hold slashes, so a branch is resolved to its commit hash first and the code is
browsed by hash. What a hash lists never changes, so those answers are kept (bounded by
count and by bytes); the repository list and the branch tips move, so they expire.
"""

import re
import threading
import time
from collections import OrderedDict
from urllib.parse import quote

FEATURES = ["repos"]

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

_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_HASH = re.compile(r"[0-9a-fA-F]{7,40}")
_PROJECT = re.compile(r"[A-Za-z0-9_]{1,64}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# git's own ref rules, plus the double quote: names sit inside a quoted BBQL string.
_BAD_REF = re.compile(r'[\x00-\x20\x7f~^:?*\[\\"]')
_README = re.compile(r"readme(\.(md|markdown|txt|rst))?", re.IGNORECASE)


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
    characters or backslashes. '' is the root."""
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
    out = {"project": project, "repos": repos, "total": meta.get("size") or len(repos), "at": time.time()}
    return CACHE.put(("repos", key), out, ttl=LIST_TTL)


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
    return CACHE.put(key, out, cost=len(out["text"] or "") + 256)


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


def route(handler, engine, method, name, query, body):
    """Answer a route this module owns, or None. A dict or list goes out as JSON; a
    (bytes, content type) pair goes out as is."""
    answer = ROUTES.get(name)
    if answer is None or method != "GET":
        return None
    return answer(engine, lambda k: (query.get(k) or [""])[0])
