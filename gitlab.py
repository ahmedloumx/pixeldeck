"""GitLab for PixelDeck: merge requests from gitlab.com or your own server, in Bitbucket's shapes.

Settings: GITLAB_TOKEN (a personal access token with the read_api scope; api to merge, close
and approve from here), GITLAB_GROUP (the group whose projects hold your merge requests, as in
gitlab.com/<group>/<project>; a subgroup path such as acme/platform works too) and, for your
own server, GITLAB_URL (https://gitlab.example.com).

model.py reads Bitbucket's shapes, so every answer here is translated into them: a merge
request carries source, destination, participants and reviewers as Bitbucket writes them, an
approval is an approval, a diff discussion is an inline thread with its resolved state. The
translations are plain functions, so the tests feed them GitLab's own payloads. A project in a
subgroup keeps its path below the group as its repository name (platform/shop-api).
"""

import json
import urllib.parse

import config
import rest
from bitbucket import BitbucketError

STATES = {"OPEN": "opened", "MERGED": "merged", "DECLINED": "closed"}
MR_STATE = {"opened": "OPEN", "locked": "OPEN", "merged": "MERGED", "closed": "DECLINED"}
CACHE_MAX = 3000


# ---- translations: GitLab's payloads to Bitbucket's shapes ------------------------------------

def user(raw):
    """A GitLab user as Bitbucket writes one; the numeric id is the key PixelDeck compares."""
    raw = raw if isinstance(raw, dict) else {}
    uid = str(raw["id"]) if raw.get("id") is not None else None
    return {"uuid": uid, "display_name": raw.get("name") or raw.get("username") or "Unknown",
            "nickname": raw.get("username"), "account_id": uid}


def project_path(raw):
    """The project's full path (acme/platform/shop-api) from a merge request."""
    full = ((raw.get("references") or {}).get("full") or "").split("!", 1)[0]
    if full:
        return full
    web = urllib.parse.urlsplit(raw.get("web_url") or "").path.strip("/")
    return web.split("/-/", 1)[0]


def slug_of(full, group):
    """A project's path below the group: shop-api, or platform/shop-api from a subgroup."""
    if group and full.lower().startswith(group.lower() + "/"):
        return full[len(group) + 1:]
    return full


def mr_shape(raw, approvals=None, reviewer_states=None, group=""):
    """A merge request, its approvals and its reviewers' states, as Bitbucket's pull request."""
    full = project_path(raw)
    slug = slug_of(full, group)
    author = user(raw.get("author"))
    named = {str(u.get("id")) for u in raw.get("reviewers") or [] if isinstance(u, dict)}
    people = {}
    for row in reviewer_states or []:
        who = row.get("user") or {}
        uid = str(who.get("id"))
        verdict = {"approved": "approved", "requested_changes": "changes_requested"}.get(row.get("state"))
        if row.get("state") in ("reviewed", "approved", "requested_changes"):
            people[uid] = {"user": user(who), "role": "REVIEWER", "state": verdict,
                           "approved": verdict == "approved", "participated_on": None}
    for row in (approvals or {}).get("approved_by") or []:
        who = row.get("user") or {}
        uid = str(who.get("id"))
        entry = people.setdefault(uid, {"user": user(who), "role": "REVIEWER" if uid in named else "PARTICIPANT",
                                        "participated_on": row.get("approved_at")})
        entry.update(state="approved", approved=True)
    participants = [p for uid, p in people.items() if uid != author["uuid"]]
    closer = raw.get("merged_by") or raw.get("merge_user") or raw.get("closed_by")
    same = raw.get("source_project_id") is None or raw.get("source_project_id") == raw.get("project_id")
    return {
        "id": raw.get("iid"),
        "title": raw.get("title") or "",
        "state": MR_STATE.get(raw.get("state"), "OPEN"),
        "draft": bool(raw.get("draft") or raw.get("work_in_progress")),
        "author": author,
        "description": raw.get("description") or "",
        "source": {"branch": {"name": raw.get("source_branch") or ""}, "commit": {"hash": raw.get("sha")},
                   "repository": {"full_name": full if same else None}},
        "destination": {"branch": {"name": raw.get("target_branch") or ""},
                        "repository": {"full_name": full, "slug": slug}},
        "created_on": raw.get("created_at"),
        "updated_on": raw.get("updated_at"),
        "comment_count": int(raw.get("user_notes_count") or 0),
        "task_count": 0,
        "closed_by": user(closer) if isinstance(closer, dict) else None,
        "links": {"html": {"href": raw.get("web_url")}},
        "participants": participants,
        "reviewers": [user(u) for u in raw.get("reviewers") or [] if isinstance(u, dict)],
        "close_source_branch": bool(raw.get("force_remove_source_branch") or raw.get("should_remove_source_branch")),
    }


def discussion_comments(discussions, web_url=""):
    """Discussions to flat comments. A diff discussion is an inline thread; it is resolved when
    every resolvable note in it is."""
    out = []
    for d in discussions or []:
        notes = [n for n in d.get("notes") or [] if isinstance(n, dict) and not n.get("system")]
        if not notes:
            continue
        root = notes[0]
        position = root.get("position") or {}
        inline = None
        if position.get("new_path") or position.get("old_path"):
            new_line = position.get("new_line")
            inline = {"path": position.get("new_path") or position.get("old_path"), "to": new_line,
                      "from": None if new_line else position.get("old_line"), "outdated": False}
        resolvable = [n for n in notes if n.get("resolvable")]
        resolution = None
        if resolvable and all(n.get("resolved") for n in resolvable):
            resolver = next((n.get("resolved_by") for n in reversed(resolvable) if n.get("resolved_by")), None)
            resolution = {"user": user(resolver)} if resolver else {"resolved": True}
        for i, note in enumerate(notes):
            out.append({
                "id": note.get("id"),
                "user": user(note.get("author")),
                "created_on": note.get("created_at"),
                "updated_on": note.get("updated_at") or note.get("created_at"),
                "content": {"raw": note.get("body") or ""},
                "deleted": False,
                "inline": inline if i == 0 else None,
                "parent": {"id": root.get("id")} if i else None,
                "resolution": resolution if i == 0 else None,
                "links": {"html": {"href": f"{web_url}#note_{note.get('id')}" if web_url else None}},
            })
    return out


def activity_shape(notes):
    """Notes, oldest first, as Bitbucket's activity. GitLab writes approvals, requests for
    changes, pushes, merges and closes as system notes with fixed words."""
    out = []
    for note in notes or []:
        if not isinstance(note, dict):
            continue
        who, at = user(note.get("author")), note.get("created_at")
        if note.get("system"):
            said = (note.get("body") or "").strip().lower()
            if said.startswith("approved this merge request"):
                out.append({"approval": {"user": who, "date": at}})
            elif said.startswith("requested changes"):
                out.append({"changes_requested": {"user": who, "date": at}})
            elif said.startswith("added ") and " commit" in said:
                out.append({"update": {"author": who, "date": at, "state": "OPEN"}})
            elif said.startswith("merged"):
                out.append({"update": {"author": who, "date": at, "state": "MERGED"}})
            elif said.startswith("closed"):
                out.append({"update": {"author": who, "date": at, "state": "DECLINED"}})
            elif said.startswith("reopened"):
                out.append({"update": {"author": who, "date": at, "state": "OPEN"}})
            continue
        position = note.get("position") or {}
        out.append({"comment": {"user": who, "created_on": at, "content": {"raw": note.get("body") or ""},
                                "inline": {"path": position.get("new_path") or position.get("old_path")} if position else {}}})
    return out


def diff_text(files):
    """GitLab's per-file diffs as one unified git diff, the text model.parse_diff reads."""
    out = []
    for f in files or []:
        old, new = f.get("old_path") or f.get("new_path"), f.get("new_path") or f.get("old_path")
        out.append(f"diff --git a/{old} b/{new}")
        if f.get("new_file"):
            out.append(f"new file mode {f.get('b_mode') or '100644'}")
        if f.get("deleted_file"):
            out.append(f"deleted file mode {f.get('a_mode') or '100644'}")
        if f.get("renamed_file"):
            out += [f"rename from {old}", f"rename to {new}"]
        body = (f.get("diff") or "").rstrip("\n")
        if not body:
            if not (f.get("renamed_file") or f.get("new_file") or f.get("deleted_file")) or f.get("too_large"):
                out.append(f"Binary files a/{old} and b/{new} differ")
            continue
        out.append("--- /dev/null" if f.get("new_file") else f"--- a/{old}")
        out.append("+++ /dev/null" if f.get("deleted_file") else f"+++ b/{new}")
        out.append(body)
    return "\n".join(out) + ("\n" if out else "")


def commit_shape(raw):
    return {"hash": raw.get("id"), "message": raw.get("message") or raw.get("title") or "",
            "date": raw.get("authored_date") or raw.get("created_at"),
            "author": {"user": None, "raw": f"{raw.get('author_name') or ''} <{raw.get('author_email') or ''}>"}}


def strategies(project):
    """The merge strategies a project allows, in Bitbucket's names, and its default. The merge
    method is the project's; whether to squash is the one choice a merge makes."""
    base = {"merge": "merge_commit", "rebase_merge": "rebase_merge", "ff": "fast_forward"}.get(project.get("merge_method"), "merge_commit")
    squashed = "squash_fast_forward" if base == "fast_forward" else "squash"
    option = project.get("squash_option") or "default_off"
    if option == "always":
        return [squashed], squashed
    if option == "never":
        return [base], base
    return [base, squashed], squashed if option == "default_on" else base


# ---- the client --------------------------------------------------------------------------------

class Client(rest.Client):
    kind = "gitlab"
    hourly_limit = 5000

    def __init__(self, workspace=None, login=None, url=None):
        super().__init__()
        self.workspace = workspace or config.workspace()
        self._login = login  # a token to try before it is saved; else the settings
        self._server = url  # an address typed in the setup, tried before it is saved
        self.sort_supported = True  # merge requests sort by update, newest first
        self._mrs = {}  # (project id, iid) -> (updated_at, shaped): read once per change

    @property
    def api(self):
        return (self._server or config.gitlab_url()).rstrip("/") + "/api/v4"

    def _auth(self):
        token = self._login or config.get("GITLAB_TOKEN")
        if not token:
            raise BitbucketError(0, f"No GITLAB_TOKEN in {config.env_path()}")
        if not self.workspace:
            self.workspace = config.workspace()
        if not self.workspace:
            raise BitbucketError(0, f"No GITLAB_GROUP in {config.env_path()}")
        return {"PRIVATE-TOKEN": token}

    def _message(self, text):
        try:
            answer = json.loads(text)
        except ValueError:
            return text[:240]
        if isinstance(answer, dict):
            said = answer.get("message") or answer.get("error_description") or answer.get("error") or ""
            if isinstance(said, (dict, list)):
                said = json.dumps(said)
            return str(said)[:300] or text[:240]
        return text[:240]

    def _group(self):
        return f"/groups/{urllib.parse.quote(self.workspace, safe='')}"

    def _project(self, slug):
        return f"/projects/{urllib.parse.quote(f'{self.workspace}/{slug}', safe='')}"

    def _mr(self, slug, iid):
        return f"{self._project(slug)}/merge_requests/{int(iid)}"

    # ---- who ----

    def me(self):
        return user(self.get("/user"))

    def members(self):
        return [user(m) for m in self.paged(f"{self._group()}/members/all", {"per_page": 100}, max_pages=10)]

    # ---- merge requests ----

    def user_prs(self, user_uuid, state, newer_than=None, max_pages=6, meta=None):
        """One person's merge requests in the group and its subgroups, newest update first."""
        params = {"author_id": user_uuid, "state": STATES[state], "scope": "all",
                  "order_by": "updated_at", "sort": "desc", "per_page": 50}
        if newer_than:
            params["updated_after"] = newer_than
        rows = list(self.paged(f"{self._group()}/merge_requests", params, max_pages=max_pages, meta=meta, total_header="x-total"))
        return [self._full(mr) for mr in rows if isinstance(mr, dict) and mr.get("iid")]

    def _say(self, path):
        """Approvals and reviewer states: None where the server or the token does not offer them."""
        try:
            return self.get(path)
        except BitbucketError as exc:
            if exc.status in (401, 0):
                raise
            return None

    def _full(self, mr):
        key = (mr.get("project_id"), mr.get("iid"))
        hit = self._mrs.get(key)
        if hit and hit[0] == mr.get("updated_at"):
            return hit[1]
        base = f"/projects/{mr.get('project_id')}/merge_requests/{int(mr['iid'])}"
        shaped = mr_shape(mr, self._say(base + "/approvals"), self._say(base + "/reviewers"), self.workspace)
        if len(self._mrs) >= CACHE_MAX:
            self._mrs.pop(next(iter(self._mrs)))
        self._mrs[key] = (mr.get("updated_at"), shaped)
        return shaped

    def pull_request(self, repo, pr_id):
        raw = self.get(self._mr(repo, pr_id))
        base = f"/projects/{raw.get('project_id')}/merge_requests/{int(pr_id)}"
        return mr_shape(raw, self._say(base + "/approvals"), self._say(base + "/reviewers"), self.workspace)

    def comments(self, repo, pr_id):
        web = (self.get(self._mr(repo, pr_id)) or {}).get("web_url") or ""
        rows = list(self.paged(self._mr(repo, pr_id) + "/discussions", {"per_page": 100}, max_pages=10))
        return discussion_comments(rows, web)

    def activity(self, repo, pr_id):
        notes = list(self.paged(self._mr(repo, pr_id) + "/notes",
                                {"sort": "asc", "order_by": "created_at", "per_page": 100}, max_pages=4))
        return activity_shape(notes)

    def tasks(self, repo, pr_id):
        return []  # GitLab's tasks live in the description's checklists, not in an API of their own

    def behind(self, repo, source, dest):
        raw = self.get(self._project(repo) + "/repository/compare", {"from": source, "to": dest})
        count = len(raw.get("commits") or [])
        return min(count, 5), count > 5

    def newest_comments(self, repo, pr_id, count):
        want = max(1, min(50, int(count or 1)))
        notes = self.get(self._mr(repo, pr_id) + "/notes", {"sort": "desc", "order_by": "created_at", "per_page": min(100, want * 3)})
        mine = [n for n in notes if isinstance(n, dict) and not n.get("system")][:want] if isinstance(notes, list) else []
        return [{"user": {"uuid": user(n.get("author"))["uuid"], "display_name": user(n.get("author"))["display_name"]},
                 "deleted": False} for n in mine]

    def pr_diff(self, repo, pr_id, limit=4 * 1024 * 1024):
        try:
            files = list(self.paged(self._mr(repo, pr_id) + "/diffs", {"per_page": 50}, max_pages=40))
        except BitbucketError as exc:
            if exc.status != 404:
                raise
            files = (self.get(self._mr(repo, pr_id) + "/changes") or {}).get("changes") or []  # before GitLab 15.7
        text = diff_text(files)
        data = text.encode("utf-8")
        return data[:limit].decode("utf-8", "ignore"), len(data) > limit

    def pr_commits(self, repo, pr_id):
        return [commit_shape(c) for c in self.paged(self._mr(repo, pr_id) + "/commits", {"per_page": 100}, max_pages=3)]

    def projects(self):
        return []  # projects are a Bitbucket scope

    def project_repos(self, key):
        return []

    def default_reviewers(self, repo):
        return []  # approval rules pick reviewers on GitLab's side

    # ---- writes ----

    def create_pr(self, repo, payload):
        body = {"source_branch": payload["source"]["branch"]["name"], "target_branch": payload["destination"]["branch"]["name"],
                "title": payload.get("title"), "description": payload.get("description") or "",
                "remove_source_branch": bool(payload.get("close_source_branch"))}
        return mr_shape(self.send("POST", self._project(repo) + "/merge_requests", body), group=self.workspace)

    def decline(self, repo, pr_id):
        return self.send("PUT", self._mr(repo, pr_id), {"state_event": "close"})

    def merge_info(self, repo, pr_id):
        """The merge request read fresh, with the project's merge method and squash choice."""
        shaped = self.pull_request(repo, pr_id)
        allowed, default = strategies(self.get(self._project(repo)))
        branch = shaped["destination"]["branch"]
        branch["merge_strategies"], branch["default_merge_strategy"] = allowed, default
        return shaped

    def merge(self, repo, pr_id, payload, expected=None):
        """Merge, squashed or not, only while the branch is still at expected."""
        squash = str(payload.get("merge_strategy") or "").startswith("squash")
        body = {"squash": squash, "should_remove_source_branch": bool(payload.get("close_source_branch"))}
        if expected:
            body["sha"] = expected
        message = (payload.get("message") or "").strip()
        if message:
            body["squash_commit_message" if squash else "merge_commit_message"] = message
        return mr_shape(self.send("PUT", self._mr(repo, pr_id) + "/merge", body), group=self.workspace)

    def review(self, repo, pr_id, action):
        if action == "approve":
            return self.send("POST", self._mr(repo, pr_id) + "/approve", {})
        if action == "unapprove":
            return self.send("POST", self._mr(repo, pr_id) + "/unapprove", {})
        raise BitbucketError(400, "PixelDeck cannot request changes on GitLab yet: do it on GitLab's website.")
