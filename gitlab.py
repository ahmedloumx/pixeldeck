"""GitLab as the code host: merge requests from gitlab.com or your own GitLab, read with the
REST API (v4) and a personal access token with the read_api scope.

Settings: GITLAB_TOKEN, GITLAB_URL (https://gitlab.com by default, or your server's address),
GITLAB_GROUP (optional: keep to one group and its subgroups, and offer its members for the team).

A person is a username, a repository the project's path (group/project). A merge request list
carries its branches, its reviewers and who merged it already, so only the approvals are read
on top, and only when the merge request moved. Discussions carry their own resolution, so the
threads count like Bitbucket's.

GitLab's rate limits are counted per minute; the status bar keeps PixelDeck's own hourly
count, as for Bitbucket, and a limit GitLab says is spent is waited out.

Read only for now: no merge, approve or close from here.
"""

import re
import time
from urllib.parse import quote, urlsplit

import config
import model
from codehost import Api, HostError, check_base
from hosts import Host

WEB = "https://gitlab.com"
TOKEN = "GITLAB_TOKEN"
USERNAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}")
# A path segment starts with a letter, digit or underscore, so "." and ".." are never one.
PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*){1,20}")
GROUP = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*){0,20}")
PER_PAGE = 50
OPEN_FRESH = 600
# GitLab writes these notes itself when someone approves or asks for changes.
APPROVED_NOTE = "approved this merge request"
CHANGES_NOTE = "requested changes"


def configured_group():
    return config.get("GITLAB_GROUP").strip().strip("/")


def web_base():
    return check_base(config.get("GITLAB_URL") or WEB, "GitLab")


class GitLabApi(Api):
    name = "GitLab"
    default_limit = 2000  # PixelDeck's own hourly meter: GitLab counts per minute

    def auth_headers(self, token):
        return {"PRIVATE-TOKEN": token}

    def read_limits(self, headers, resource):
        # Only "spent until" is kept: a per-minute count on an hourly meter would mislead.
        if headers.get("ratelimit-remaining") == "0":
            try:
                reset = int(headers.get("ratelimit-reset") or 0)
            except ValueError:
                reset = 0
            with self.lock:
                self.limits[resource] = {"remaining": 0, "reset": reset or self.clock() + 60}

    def message_of(self, status, payload, raw):
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("error")
            if isinstance(message, dict):
                message = "; ".join(f"{k}: {v}" for k, v in message.items())
            if isinstance(message, (str, list)) and message:
                return str(message)[:240]
        return super().message_of(status, payload, raw)


# ---------------------------------------------------------------- shapes

def user(raw):
    raw = raw or {}
    name = raw.get("username")
    return {"uuid": name, "name": raw.get("name") or name or "Unknown", "nickname": name, "account_id": None}


def path_of(mr):
    """The project path, from references.full (group/project!12), else from the web address."""
    full = ((mr.get("references") or {}).get("full") or "").rsplit("!", 1)[0]
    if PATH.fullmatch(full):
        return full
    web = urlsplit(mr.get("web_url") or "").path.strip("/").split("/-/", 1)[0]
    return web if PATH.fullmatch(web) else ""


def project(repo):
    if not PATH.fullmatch(str(repo or "")):
        raise ValueError(f"not a GitLab project path: {repo!r}")
    return "/projects/" + quote(repo, safe="")


def _state(mr):
    return {"opened": "OPEN", "locked": "OPEN", "merged": "MERGED"}.get(mr.get("state"), "DECLINED")


def reviews_of(mr, approvals):
    """Reviewers who have not approved are pending; approvers approved. GitLab keeps a
    request for changes in a note, read for the insights rather than for every list."""
    author = (mr.get("author") or {}).get("username")
    by_name = {}
    for row in (approvals or {}).get("approved_by") or []:
        who = row.get("user") or {}
        if who.get("username") and who["username"] != author:
            by_name[who["username"]] = {**user(who), "state": "approved", "reviewer": True, "at": None}
    for who in mr.get("reviewers") or []:
        name = who.get("username")
        if name and name != author and name not in by_name:
            by_name[name] = {**user(who), "state": "pending", "reviewer": True, "at": None}
    return sorted(by_name.values(), key=lambda r: (model.REVIEW_RANK.get(r["state"], 9), not r["reviewer"], r["name"]))


def slim_mr(mr, approvals=None):
    state = _state(mr)
    source = mr.get("source_branch") or ""
    closer = (mr.get("merge_user") or mr.get("merged_by")) if state == "MERGED" else mr.get("closed_by")
    updated = {"MERGED": mr.get("merged_at"), "DECLINED": mr.get("closed_at")}.get(state) or mr.get("updated_at")
    return {
        "repo": path_of(mr),
        "id": mr.get("iid"),
        "title": mr.get("title") or "",
        "state": state,
        "draft": bool(mr.get("draft") or mr.get("work_in_progress")),
        "author": user(mr.get("author")),
        "source": source,
        "dest": mr.get("target_branch") or "",
        "source_commit": mr.get("sha"),
        "created_on": mr.get("created_at"),
        "updated_on": updated,
        "comment_count": mr.get("user_notes_count") or 0,
        "task_count": 0,
        "closed_by": user(closer) if closer else None,
        "url": mr.get("web_url"),
        # A closed one's reviewers who never approved are not pending any more: left out.
        "reviews": reviews_of(mr, approvals) if approvals is not None else reviews_of(mr, {}) if state == "OPEN" else [],
        "ticket": model.ticket_of(source, mr.get("title")),
    }


def build_threads(discussions, author):
    """GitLab's discussions as the page's threads: one on a line of the diff is inline, and
    unresolved until GitLab says it is resolved, like a Bitbucket inline comment."""
    threads = []
    for d in discussions:
        notes = [n for n in d.get("notes") or [] if not n.get("system")]
        if not notes:
            continue
        first = notes[0]
        position = first.get("position") or {}
        inline = first.get("type") == "DiffNote" and bool(position.get("new_path") or position.get("old_path"))
        resolvable = [n for n in notes if n.get("resolvable")]
        resolved = bool(resolvable) and all(n.get("resolved") for n in resolvable)
        messages = [{"id": n.get("id"), "author": user(n.get("author")), "at": n.get("created_at"),
                     "edited": n.get("updated_at") if n.get("updated_at") not in (None, n.get("created_at")) else None,
                     "raw": n.get("body") or "", "deleted": False} for n in notes]
        resolver = next((n.get("resolved_by") for n in resolvable if n.get("resolved_by")), None)
        threads.append({
            "id": d.get("id"),
            "path": (position.get("new_path") or position.get("old_path")) if inline else None,
            "line": (position.get("new_line") or position.get("old_line")) if inline else None,
            "side": ("new" if position.get("new_line") else "old") if inline else None,
            "outdated": False,
            "inline": inline,
            "resolved": resolved,
            "resolved_by": user(resolver) if resolver else None,
            "unresolved": inline and not resolved,
            "awaiting_author": inline and not resolved and messages[-1]["author"]["uuid"] != author,
            "url": None,
            "messages": messages,
        })
    threads.sort(key=lambda t: t["messages"][0]["at"] or "")
    return threads


def unified_diff(changes):
    """GitLab's per-file diffs, put back together as one git diff for model.parse_diff."""
    out = []
    for c in changes:
        old, new = c.get("old_path") or c.get("new_path"), c.get("new_path") or c.get("old_path")
        out.append(f"diff --git a/{old} b/{new}")
        if c.get("new_file"):
            out.append("new file mode 100644")
        if c.get("deleted_file"):
            out.append("deleted file mode 100644")
        if c.get("renamed_file"):
            out += [f"rename from {old}", f"rename to {new}"]
        out += ["--- /dev/null" if c.get("new_file") else f"--- a/{old}",
                "+++ /dev/null" if c.get("deleted_file") else f"+++ b/{new}"]
        out.append((c.get("diff") or "").rstrip("\n"))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- the host

class GitLabHost(Host):
    id = "gitlab"
    label = "GitLab"
    writes = False
    incremental = True  # lists are sorted by update time and filtered from a date

    def __init__(self, engine, transport=None, clock=time.time):
        self.engine = engine
        self.transport = transport
        self.clock = clock
        self._api, self._api_for = None, None
        self._full = {}  # (repo, iid) -> (updated_at, read_at, slim)

    @property
    def api(self):
        wanted = config.get("GITLAB_URL") or WEB
        if self._api is None or self._api_for != wanted:
            try:
                base = check_base(wanted, "GitLab")
            except ValueError as exc:
                raise HostError(0, f"GITLAB_URL: {exc}", unset=True) from None
            self._api = GitLabApi(base + "/api/v4", lambda: config.get(TOKEN), transport=self.transport, clock=self.clock)
            self._api_for = wanted
        return self._api

    def budget(self):
        try:
            return self.api.budget()
        except HostError:
            return {"used": 0, "limit": GitLabApi.default_limit}

    def busy(self):
        try:
            return self.api.busy()
        except HostError:
            return False

    def me(self):
        raw = self.api.get("/user")
        if not raw.get("username"):
            raise HostError(502, "GitLab did not say who you are")
        return user(raw)

    def members(self):
        group = configured_group()
        if not group:
            return []
        if not GROUP.fullmatch(group):
            raise HostError(0, f"GITLAB_GROUP is not a group path: {group!r}")
        rows = self.api.paged(f"/groups/{quote(group, safe='')}/members/all", {"per_page": 100}, max_pages=10)
        return [{"uuid": m["username"], "name": m.get("name") or m["username"], "nickname": m["username"]}
                for m in rows if m.get("username") and m.get("state", "active") == "active"]

    def _list(self, params, max_pages, meta=None):
        group = configured_group()
        if group and GROUP.fullmatch(group):
            path = f"/groups/{quote(group, safe='')}/merge_requests"
        else:
            path, params = "/merge_requests", {**params, "scope": "all"}
        params = {"order_by": "updated_at", "sort": "desc", "per_page": PER_PAGE, **params}
        return list(self.api.paged(path, params, max_pages=max_pages, meta=meta))

    def prs(self, uuid, state, newer_than=None, max_pages=6, meta=None):
        if not USERNAME.fullmatch(str(uuid or "")):
            raise ValueError(f"not a GitLab username: {uuid!r}")
        wanted = {"OPEN": "opened", "MERGED": "merged", "DECLINED": "closed"}.get(state)
        if not wanted:
            raise ValueError(f"unknown state {state!r}")
        params = {"author_username": uuid, "state": wanted}
        if newer_than and state != "OPEN":
            params["updated_after"] = newer_than  # a merge or close moves the update time too
        rows = self._list(params, max_pages, meta)
        if state == "OPEN":
            return [self._open(mr) for mr in rows]
        out = self._closed(uuid, rows)
        return [p for p in out if not newer_than or (p.get("updated_on") or "") >= newer_than]

    def review_requests(self, uuid):
        if not USERNAME.fullmatch(str(uuid or "")):
            return None
        return [self._open(mr) for mr in self._list({"reviewer_username": uuid, "state": "opened"}, 2)]

    def _approvals(self, repo, iid):
        return self.api.get(f"{project(repo)}/merge_requests/{int(iid)}/approvals")

    def _remember(self, repo, iid, updated_at, slim):
        self._full[(repo, iid)] = (updated_at, self.clock(), slim)
        if len(self._full) > 2000:
            self._full.pop(next(iter(self._full)))
        return slim

    def _open(self, mr):
        repo, iid = path_of(mr), mr.get("iid")
        memo = self._full.get((repo, iid))
        if memo and memo[0] == mr.get("updated_at") and self.clock() - memo[1] < OPEN_FRESH:
            return memo[2]
        try:
            approvals = self._approvals(repo, iid)
        except (HostError, ValueError) as exc:
            if isinstance(exc, HostError) and exc.status not in (403, 404):
                raise
            return slim_mr(mr)
        return self._remember(repo, iid, mr.get("updated_at"), slim_mr(mr, approvals))

    def _closed(self, uuid, rows):
        """Merged and closed merge requests as the list gives them, branches and who merged them
        included: their approvals are not read, so a long history costs no call per item."""
        return [slim_mr(mr) for mr in rows]

    def pull_request(self, repo, pr_id):
        mr = self.api.get(f"{project(repo)}/merge_requests/{int(pr_id)}")
        return self._remember(repo, int(pr_id), mr.get("updated_at"), slim_mr(mr, self._approvals(repo, pr_id)))

    def _discussions(self, repo, iid):
        return list(self.api.paged(f"{project(repo)}/merge_requests/{int(iid)}/discussions", {"per_page": 100}, max_pages=10))

    def _notes(self, repo, iid, per_page=100):
        return self.api.get(f"{project(repo)}/merge_requests/{int(iid)}/notes",
                            {"sort": "desc", "order_by": "created_at", "per_page": per_page}) or []

    def detail(self, repo, pr_id):
        mr = self.api.get(f"{project(repo)}/merge_requests/{int(pr_id)}")
        slim = self._remember(repo, int(pr_id), mr.get("updated_at"), slim_mr(mr, self._approvals(repo, pr_id)))
        threads = build_threads(self._discussions(repo, pr_id), slim["author"]["uuid"])
        return {"pr": slim, "description": mr.get("description") or "", "threads": threads, "tasks": []}

    def diff(self, repo, pr_id):
        base = f"{project(repo)}/merge_requests/{int(pr_id)}"
        try:
            changes = list(self.api.paged(base + "/diffs", {"per_page": 100}, max_pages=30))
        except HostError as exc:
            if exc.status != 404:
                raise
            changes = (self.api.get(base + "/changes") or {}).get("changes") or []  # a GitLab before /diffs
        text = unified_diff(changes)
        return text, False

    def commits(self, repo, pr_id):
        rows = self.api.paged(f"{project(repo)}/merge_requests/{int(pr_id)}/commits", {"per_page": 100}, max_pages=3)
        return [{"sha": (c.get("short_id") or c.get("id") or "")[:8], "subject": c.get("title") or "",
                 "author": c.get("author_name") or "", "at": c.get("authored_date") or c.get("created_at"),
                 "url": c.get("web_url")} for c in rows]

    def timeline(self, repo, pr_id):
        out = []
        for n in self._notes(repo, pr_id):
            who = user(n.get("author"))["name"]
            body = n.get("body") or ""
            if n.get("system"):
                if body.startswith(APPROVED_NOTE):
                    out.append({"kind": "approved", "who": who, "at": n.get("created_at")})
                elif body.startswith(CHANGES_NOTE):
                    out.append({"kind": "changes_requested", "who": who, "at": n.get("created_at")})
                continue
            out.append({"kind": "commented", "who": who, "at": n.get("created_at"),
                        "where": (n.get("position") or {}).get("new_path"), "text": body[:160]})
        out.sort(key=lambda x: x.get("at") or "", reverse=True)
        return out

    def changes_requested(self, repo, pr_id):
        return [{"name": user(n.get("author"))["name"], "uuid": user(n.get("author"))["uuid"], "at": n.get("created_at")}
                for n in self._notes(repo, pr_id) if n.get("system") and (n.get("body") or "").startswith(CHANGES_NOTE)]

    def behind(self, pr):
        if not pr.get("source") or not pr.get("dest"):
            raise HostError(0, "the branches are not known yet")
        answer = self.api.get(f"{project(pr['repo'])}/repository/compare", {"from": pr["source"], "to": pr["dest"]})
        return len(answer.get("commits") or []), False

    def commenters(self, repo, pr_id, count):
        notes = [n for n in self._notes(repo, pr_id) if not n.get("system")][:count]
        return [{"uuid": user(n.get("author"))["uuid"], "name": user(n.get("author"))["name"]} for n in notes]


# ---------------------------------------------------------------- setup

def _hint(exc, scope):
    if exc.status == 401:
        return "the token is wrong, expired or revoked"
    if exc.status == 403:
        return f"give the token {scope}"
    if exc.status == 404:
        return "not found, or the token cannot see it"
    return exc.message


def check(body, transport=None):
    """The reads PixelDeck makes, tried with the typed (or saved) token before it is saved."""
    token = str(body.get("token") or config.get(TOKEN)).strip()
    base = check_base(str(body.get("url") or config.get("GITLAB_URL") or WEB), "GitLab")
    group = str(body["group"] if "group" in body else configured_group()).strip().strip("/")
    if not token:
        raise ValueError("Paste a token first.")
    if group and not GROUP.fullmatch(group):
        raise ValueError("The group is the path in gitlab.com/<group>.")
    api = GitLabApi(base + "/api/v4", token, transport=transport)
    scope = "read_api"
    steps = ["Your user"] + (["The group"] if group else []) + ["Merge requests", "Code, for diffs"]
    results, me, sample = [], None, None
    for label in steps:
        try:
            if label == "Your user":
                me = api.get("/user")
            elif label == "The group":
                api.get(f"/groups/{quote(group, safe='')}")
            elif not me:
                results.append({"label": label, "ok": None, "scope": scope, "hint": "needs your user first"})
                continue
            elif label == "Merge requests":
                found = api.get("/merge_requests", {"scope": "all", "author_username": me.get("username"), "per_page": 1})
                sample = path_of(found[0]) if isinstance(found, list) and found else None
            elif not sample:
                results.append({"label": label, "ok": None, "scope": scope, "hint": "needs one of your merge requests to try"})
                continue
            else:
                api.get(f"{project(sample)}/repository/commits", {"per_page": 1})
        except HostError as exc:
            results.append({"label": label, "ok": False, "scope": scope, "status": exc.status, "hint": _hint(exc, scope)})
            if exc.status in (0, 401):
                break
            continue
        results.append({"label": label, "ok": True, "scope": scope})
    return {"ok": len(results) == len(steps) and all(r["ok"] for r in results), "results": results,
            "me": (me or {}).get("username")}


def report(engine):
    active = engine.host.id == "gitlab"
    return {"token_set": bool(config.get(TOKEN)), "url": config.get("GITLAB_URL") or WEB, "group": configured_group() or None,
            "active": active, "auth": engine.auth if active else None, "me": engine.me() if active else None,
            "budget": engine.budget() if active else None}
