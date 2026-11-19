"""GitHub for PixelDeck: pull requests from github.com or GitHub Enterprise, in Bitbucket's shapes.

Settings: GITHUB_TOKEN (a personal access token, fine-grained or classic), GITHUB_OWNER (the
user or organization whose repositories hold your pull requests: github.com/<owner>/<repo>)
and, for GitHub Enterprise Server only, GITHUB_API_URL (https://<server>/api/v3).

model.py reads Bitbucket's shapes, so every answer here is translated into them: a pull
request carries source, destination, participants and reviewers as Bitbucket writes them, a
review is an approval or a request for changes, an inline comment has an inline anchor and a
resolution. The translations are plain functions, so the tests feed them GitHub's own payloads.

The REST API is used throughout, and GraphQL once: review threads (with their resolved state,
which REST does not give) come from one query per page of threads.
"""

import json
import urllib.parse

import config
import rest
from bitbucket import BitbucketError

API_VERSION = "2022-11-28"
SEARCH_STATE = {"OPEN": "is:open", "MERGED": "is:merged", "DECLINED": "is:closed is:unmerged"}
METHODS = {"merge_commit": "merge", "squash": "squash", "rebase_fast_forward": "rebase"}
PROTECTED = {"develop", "main", "master", "HEAD"}
CACHE_MAX = 3000  # pull requests kept read, each until it changes

THREADS_QUERY = """query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved isOutdated path line originalLine diffSide
          resolvedBy { login }
          comments(first: 50) {
            nodes { databaseId body createdAt updatedAt url author { login ... on User { name } } }
          }
        }
      }
    }
  }
}"""


# ---- translations: GitHub's payloads to Bitbucket's shapes ------------------------------------

def user(raw):
    """A GitHub user as Bitbucket writes one; the login is the stable key PixelDeck compares."""
    raw = raw if isinstance(raw, dict) else {}
    login = raw.get("login") or None
    return {"uuid": login, "display_name": raw.get("name") or login or "Unknown", "nickname": login,
            "account_id": str(raw["id"]) if raw.get("id") is not None else None}


def pr_shape(raw, reviews=(), repo=None):
    """A pull request (REST) and its reviews, as Bitbucket's pull request with participants."""
    base, head = raw.get("base") or {}, raw.get("head") or {}
    base_repo, head_repo = base.get("repo") or {}, head.get("repo") or {}
    repo = repo or base_repo.get("name") or ""
    owner = (base_repo.get("owner") or {}).get("login") or ""
    author = user(raw.get("user"))
    if raw.get("merged_at") or raw.get("merged"):
        state = "MERGED"
    elif raw.get("state") == "open":
        state = "OPEN"
    else:
        state = "DECLINED"
    # A reviewer's say is their latest approval or request for changes; a dismissal takes it back.
    verdict, people, when = {}, {}, {}
    for review in sorted((r for r in reviews if isinstance(r, dict)), key=lambda r: r.get("submitted_at") or ""):
        who = review.get("user") or {}
        login = who.get("login")
        if not login or login == author["uuid"] or review.get("state") == "PENDING":
            continue
        people[login], when[login] = who, review.get("submitted_at")
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED"):
            verdict[login] = review["state"]
        elif review.get("state") == "DISMISSED":
            verdict.pop(login, None)
    participants = []
    for login, who in people.items():
        state_of = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested"}.get(verdict.get(login))
        participants.append({"user": user(who), "role": "REVIEWER", "state": state_of,
                             "approved": state_of == "approved", "participated_on": when[login]})
    merged_by = raw.get("merged_by")
    return {
        "id": raw.get("number"),
        "title": raw.get("title") or "",
        "state": state,
        "draft": bool(raw.get("draft")),
        "author": author,
        "description": raw.get("body") or "",
        "source": {"branch": {"name": head.get("ref") or ""}, "commit": {"hash": head.get("sha")},
                   "repository": {"full_name": head_repo.get("full_name")}},
        "destination": {"branch": {"name": base.get("ref") or ""},
                        "repository": {"full_name": f"{owner}/{repo}" if owner else repo, "slug": repo}},
        "created_on": raw.get("created_at"),
        "updated_on": raw.get("updated_at"),
        "comment_count": int(raw.get("comments") or 0) + int(raw.get("review_comments") or 0),
        "task_count": 0,
        "closed_by": user(merged_by) if isinstance(merged_by, dict) else None,
        "links": {"html": {"href": raw.get("html_url")}},
        "participants": participants,
        "reviewers": [user(u) for u in raw.get("requested_reviewers") or [] if isinstance(u, dict)],
        "close_source_branch": False,
    }


def comment(raw, cid=None, inline=None, parent=None, resolution=None):
    """One comment, as Bitbucket's: issue comments, review summaries and review comments alike."""
    created = raw.get("created_at") or raw.get("createdAt") or raw.get("submitted_at")
    return {
        "id": raw.get("id") if cid is None else cid,
        "user": user(raw.get("user") or raw.get("author")),
        "created_on": created,
        "updated_on": raw.get("updated_at") or raw.get("updatedAt") or created,
        "content": {"raw": raw.get("body") or ""},
        "deleted": False,
        "inline": inline,
        "parent": {"id": parent} if parent is not None else None,
        "resolution": resolution,
        "links": {"html": {"href": raw.get("html_url") or raw.get("url")}},
    }


def _anchor(path, line, left, outdated):
    return {"path": path, "to": None if left else line, "from": line if left else None, "outdated": bool(outdated)}


def thread_comments(threads):
    """GraphQL review threads to flat comments: the first is the root, the rest reply to it."""
    out = []
    for thread in threads or []:
        nodes = [c for c in ((thread.get("comments") or {}).get("nodes") or []) if isinstance(c, dict)]
        if not nodes:
            continue
        root = nodes[0]
        inline = _anchor(thread.get("path"), thread.get("line") or thread.get("originalLine"),
                         thread.get("diffSide") == "LEFT", thread.get("isOutdated"))
        resolution = None
        if thread.get("isResolved"):
            resolver = thread.get("resolvedBy")
            resolution = {"user": user(resolver)} if resolver else {"resolved": True}
        for i, node in enumerate(nodes):
            out.append(comment(node, cid=node.get("databaseId"), inline=inline if i == 0 else None,
                               parent=root.get("databaseId") if i else None, resolution=resolution if i == 0 else None))
    return out


def review_comment(raw):
    """A REST review comment, for when the threads query is refused: no resolved state then."""
    left = raw.get("side") == "LEFT"
    inline = _anchor(raw.get("path"), raw.get("line") or raw.get("original_line"), left, raw.get("line") is None)
    return comment(raw, inline=inline, parent=raw.get("in_reply_to_id"))


def activity_shape(events):
    """The issue timeline, as Bitbucket's activity: approvals, requests for changes, comments
    and updates (pushes, merge, close)."""
    out = []
    merged = any(e.get("event") == "merged" for e in events if isinstance(e, dict))
    for e in events:
        if not isinstance(e, dict):
            continue
        kind, actor = e.get("event"), user(e.get("actor") or e.get("user"))
        if kind == "reviewed":
            verdict, at, reviewer = (e.get("state") or "").lower(), e.get("submitted_at"), user(e.get("user"))
            if verdict == "approved":
                out.append({"approval": {"user": reviewer, "date": at}})
            elif verdict == "changes_requested":
                out.append({"changes_requested": {"user": reviewer, "date": at}})
            elif (e.get("body") or "").strip():
                out.append({"comment": {"user": reviewer, "created_on": at, "content": {"raw": e["body"]}}})
        elif kind == "commented":
            out.append({"comment": {"user": actor, "created_on": e.get("created_at"), "content": {"raw": e.get("body") or ""}}})
        elif kind == "line-commented":
            for c in e.get("comments") or []:
                out.append({"comment": {"user": user(c.get("user")), "created_on": c.get("created_at"),
                                        "content": {"raw": c.get("body") or ""}, "inline": {"path": c.get("path")}}})
        elif kind == "committed":
            who = e.get("author") or {}
            out.append({"update": {"author": {"display_name": who.get("name") or "Someone"}, "date": who.get("date"), "state": "OPEN"}})
        elif kind in ("head_ref_force_pushed", "reopened"):
            out.append({"update": {"author": actor, "date": e.get("created_at"), "state": "OPEN"}})
        elif kind == "merged":
            out.append({"update": {"author": actor, "date": e.get("created_at"), "state": "MERGED"}})
        elif kind == "closed" and not merged:
            out.append({"update": {"author": actor, "date": e.get("created_at"), "state": "DECLINED"}})
    return out


def commit_shape(raw):
    commit = raw.get("commit") or {}
    who = commit.get("author") or {}
    login = (raw.get("author") or {}).get("login")
    return {"hash": raw.get("sha"), "message": commit.get("message") or "", "date": who.get("date"),
            "author": {"user": {"display_name": login} if login else None, "raw": f"{who.get('name') or ''} <{who.get('email') or ''}>"}}


def graphql_url(api):
    """api.github.com/graphql, or https://<server>/api/graphql on GitHub Enterprise."""
    return api[: -len("/v3")] + "/graphql" if api.endswith("/api/v3") else api + "/graphql"


def search_time(stamp):
    """An ISO time for a search qualifier: seconds, in UTC (PixelDeck's times are UTC)."""
    return str(stamp)[:19] + "+00:00"


# ---- the client --------------------------------------------------------------------------------

class Client(rest.Client):
    kind = "github"
    hourly_limit = 5000

    def __init__(self, workspace=None, login=None, api=None):
        super().__init__()
        self.workspace = workspace or config.workspace()
        self._login = login  # a token to try before it is saved; else the settings
        self._api = api  # an address typed in the setup, tried before it is saved
        self.sort_supported = True  # search sorts by update, newest first
        self._owner_kind = None
        self._prs = {}  # (repo, number) -> (updated_at, shaped): read once per change
        self._repos = {}
        self._me = None

    @property
    def api(self):
        return self._api or config.github_api()

    def _auth(self):
        token = self._login or config.get("GITHUB_TOKEN")
        if not token:
            raise BitbucketError(0, f"No GITHUB_TOKEN in {config.env_path()}")
        if not self.workspace:
            self.workspace = config.workspace()
        if not self.workspace:
            raise BitbucketError(0, f"No GITHUB_OWNER in {config.env_path()}")
        return {"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": API_VERSION}

    def _message(self, text):
        try:
            answer = json.loads(text)
        except ValueError:
            return text[:240]
        message = str(answer.get("message") or "") if isinstance(answer, dict) else ""
        errors = answer.get("errors") if isinstance(answer, dict) else None
        if isinstance(errors, list) and errors:
            more = "; ".join(str(e.get("message") or e.get("code") or "") if isinstance(e, dict) else str(e) for e in errors)
            message = f"{message}: {more}" if message else more
        return message[:300] or text[:240]

    def _repo(self, repo):
        return f"/repos/{urllib.parse.quote(self.workspace, safe='')}/{urllib.parse.quote(repo, safe='')}"

    def graphql(self, query, variables):
        answer = self.request("POST", graphql_url(self.api), {"query": query, "variables": variables})
        errors = answer.get("errors") if isinstance(answer, dict) else None
        if errors:
            raise BitbucketError(502, "; ".join(str(e.get("message")) for e in errors if isinstance(e, dict))[:300] or "GraphQL refused the query")
        return (answer or {}).get("data") or {}

    # ---- who ----

    def me(self):
        raw = self.get("/user")
        self._me = user(raw)
        return self._me

    def _owner(self):
        if self._owner_kind is None:
            raw = self.get(f"/users/{urllib.parse.quote(self.workspace, safe='')}")
            self._owner_kind = "org" if raw.get("type") == "Organization" else "user"
        return self._owner_kind

    def members(self):
        """An organization's members; a user's repositories have no team to pick from."""
        if self._owner() != "org":
            return []
        path = f"/orgs/{urllib.parse.quote(self.workspace, safe='')}/members"
        return [user(m) for m in self.paged(path, {"per_page": 100}, max_pages=10)]

    # ---- pull requests ----

    def user_prs(self, user_uuid, state, newer_than=None, max_pages=6, meta=None):
        """One person's pull requests in the owner's repositories, newest update first."""
        terms = ["is:pr", f"author:{user_uuid}", f"{'org' if self._owner() == 'org' else 'user'}:{self.workspace}", SEARCH_STATE[state]]
        if newer_than:
            terms.append(f"updated:>={search_time(newer_than)}")
        params = {"q": " ".join(terms), "sort": "updated", "order": "desc", "per_page": 50}
        items = list(self.paged("/search/issues", params, max_pages=max_pages, key="items", meta=meta))
        return [self._full(item) for item in items if isinstance(item, dict) and item.get("number")]

    def _full(self, item):
        """A search hit as a whole pull request with its reviews, read again only once it changed."""
        repo = str(item.get("repository_url") or "").rstrip("/").rsplit("/", 1)[-1]
        key = (repo, item["number"])
        hit = self._prs.get(key)
        if hit and hit[0] == item.get("updated_at"):
            return hit[1]
        shaped = self.pull_request(repo, item["number"])
        if len(self._prs) >= CACHE_MAX:
            self._prs.pop(next(iter(self._prs)))
        self._prs[key] = (item.get("updated_at"), shaped)
        return shaped

    def pull_request(self, repo, pr_id):
        base = f"{self._repo(repo)}/pulls/{int(pr_id)}"
        raw = self.get(base)
        reviews = list(self.paged(base + "/reviews", {"per_page": 100}, max_pages=3))
        return pr_shape(raw, reviews, repo)

    def comments(self, repo, pr_id):
        """Conversation comments, review summaries and inline threads, as Bitbucket's comments."""
        number = int(pr_id)
        out = [comment(c) for c in self.paged(f"{self._repo(repo)}/issues/{number}/comments", {"per_page": 100}, max_pages=10)]
        for review in self.paged(f"{self._repo(repo)}/pulls/{number}/reviews", {"per_page": 100}, max_pages=3):
            if (review.get("body") or "").strip():
                out.append(comment(review, cid=f"review-{review.get('id')}"))
        try:
            out += thread_comments(self._threads(repo, number))
        except BitbucketError:
            # GraphQL refused (an older Enterprise server, a token without it): the inline
            # comments still show, without their resolved state.
            out += [review_comment(c) for c in self.paged(f"{self._repo(repo)}/pulls/{number}/comments", {"per_page": 100}, max_pages=10)]
        return out

    def _threads(self, repo, number):
        threads, after = [], None
        for _ in range(10):
            data = self.graphql(THREADS_QUERY, {"owner": self.workspace, "repo": repo, "number": number, "after": after})
            page = ((((data.get("repository") or {}).get("pullRequest")) or {}).get("reviewThreads")) or {}
            threads += page.get("nodes") or []
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            after = info.get("endCursor")
        return threads

    def activity(self, repo, pr_id):
        events = list(self.paged(f"{self._repo(repo)}/issues/{int(pr_id)}/timeline", {"per_page": 100}, max_pages=4))
        return activity_shape(events)

    def tasks(self, repo, pr_id):
        return []  # GitHub has no pull request tasks

    def behind(self, repo, source, dest):
        ref = lambda name: urllib.parse.quote(name, safe="/")  # noqa: E731
        raw = self.get(f"{self._repo(repo)}/compare/{ref(source)}...{ref(dest)}", {"per_page": 1})
        count = int(raw.get("ahead_by") or 0)
        return min(count, 5), count > 5

    def newest_comments(self, repo, pr_id, count):
        number = int(pr_id)
        rows = list(self.paged(f"{self._repo(repo)}/issues/{number}/comments", {"per_page": 100}, max_pages=2))
        rows += list(self.paged(f"{self._repo(repo)}/pulls/{number}/comments",
                                {"per_page": 50, "sort": "created", "direction": "desc"}, max_pages=1))
        rows.sort(key=lambda c: c.get("created_at") or "", reverse=True)
        return [{"user": {"uuid": (c.get("user") or {}).get("login"), "display_name": (c.get("user") or {}).get("login")},
                 "deleted": False} for c in rows[: max(1, min(50, int(count or 1)))]]

    def pr_diff(self, repo, pr_id, limit=4 * 1024 * 1024):
        data = self.request("GET", self.url(f"{self._repo(repo)}/pulls/{int(pr_id)}"),
                            accept="application/vnd.github.diff", raw=True, limit=limit)
        return data[:limit].decode("utf-8", "replace"), len(data) > limit

    def pr_commits(self, repo, pr_id):
        return [commit_shape(c) for c in self.paged(f"{self._repo(repo)}/pulls/{int(pr_id)}/commits", {"per_page": 100}, max_pages=3)]

    def projects(self):
        return []  # projects are a Bitbucket scope

    def project_repos(self, key):
        return []

    def default_reviewers(self, repo):
        return []  # GitHub asks CODEOWNERS by itself when the pull request opens

    # ---- writes ----

    def create_pr(self, repo, payload):
        body = {"title": payload.get("title"), "body": payload.get("description") or "",
                "head": payload["source"]["branch"]["name"], "base": payload["destination"]["branch"]["name"]}
        raw = self.send("POST", f"{self._repo(repo)}/pulls", body)
        return pr_shape(raw, (), repo)

    def decline(self, repo, pr_id):
        return self.send("PATCH", f"{self._repo(repo)}/pulls/{int(pr_id)}", {"state": "closed"})

    def _settings(self, repo):
        if repo not in self._repos:
            self._repos[repo] = self.get(self._repo(repo))
        return self._repos[repo]

    def merge_info(self, repo, pr_id):
        """The pull request read fresh, with the merge methods the repository allows. A token
        that cannot read those settings gets GitHub's defaults; GitHub refuses a disallowed one."""
        shaped = self.pull_request(repo, pr_id)
        info = self._settings(repo)
        allowed = [s for s, key in (("merge_commit", "allow_merge_commit"), ("squash", "allow_squash_merge"),
                                    ("rebase_fast_forward", "allow_rebase_merge")) if info.get(key, True)]
        branch = shaped["destination"]["branch"]
        branch["merge_strategies"] = allowed
        branch["default_merge_strategy"] = allowed[0] if allowed else None
        shaped["close_source_branch"] = bool(info.get("delete_branch_on_merge"))
        return shaped

    def merge(self, repo, pr_id, payload, expected=None):
        """Merge with the chosen method, only if the branch is still at expected. Closing the
        branch deletes it afterwards, when it lives in the same repository and is not protected."""
        strategy = payload.get("merge_strategy") or "merge_commit"
        if strategy not in METHODS:
            raise ValueError(f"GitHub cannot merge with {strategy}")
        body = {"merge_method": METHODS[strategy]}
        if expected:
            body["sha"] = expected
        message = (payload.get("message") or "").strip()
        if message and strategy != "rebase_fast_forward":
            title, _, rest_ = message.partition("\n")
            body["commit_title"], body["commit_message"] = title.strip()[:250], rest_.strip()
        self.send("PUT", f"{self._repo(repo)}/pulls/{int(pr_id)}/merge", body)
        merged = self.pull_request(repo, pr_id)
        if payload.get("close_source_branch"):
            branch = merged["source"]["branch"]["name"]
            same = str(merged["source"]["repository"].get("full_name") or "").lower() == f"{self.workspace}/{repo}".lower()
            if same and branch and branch not in PROTECTED:
                try:
                    self.send("DELETE", f"{self._repo(repo)}/git/refs/heads/{urllib.parse.quote(branch, safe='/')}")
                except BitbucketError:
                    pass  # merged all the same; the branch stays
        return merged

    def review(self, repo, pr_id, action):
        path = f"{self._repo(repo)}/pulls/{int(pr_id)}/reviews"
        if action == "approve":
            return self.send("POST", path, {"event": "APPROVE"})
        if action == "request_changes":
            return self.send("POST", path, {"event": "REQUEST_CHANGES", "body": "Changes requested."})
        wanted = "APPROVED" if action == "unapprove" else "CHANGES_REQUESTED"
        me = (self._me or self.me())["uuid"]
        mine = [r for r in self.paged(path, {"per_page": 100}, max_pages=3)
                if (r.get("user") or {}).get("login") == me and r.get("state") == wanted]
        if not mine:
            raise BitbucketError(409, "There is no review of yours to take back.")
        return self.send("PUT", f"{path}/{mine[-1]['id']}/dismissals", {"message": "Taken back.", "event": "DISMISS"})
