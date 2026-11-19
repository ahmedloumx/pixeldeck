"""GitHub as the code host: pull requests from github.com or GitHub Enterprise, read with the
REST API (v3) and a personal access token.

Settings: GITHUB_TOKEN (fine-grained or classic), GITHUB_URL (https://api.github.com by default,
https://<your server>/api/v3 for Enterprise), GITHUB_ORG (optional: keep to one organization,
and offer its members for the team).

A person is a login, a repository owner/name. Each person's lists come from the search API,
one query per state. An open pull request is then read in full (its branches, its reviewers
and their verdicts) only when its update time moved, and every read goes out with the ETag of
the last answer, so an unchanged one comes back as a 304 that GitHub does not count. Whether a
review thread is resolved is only in GitHub's GraphQL API: one query when a pull request is
opened; without it, a thread counts as settled once it is outdated.

Read only for now: no merge, approve or decline from here, so the token needs read access only.
"""

import re
import time
from urllib.parse import quote

import config
import model
from codehost import Api, HostError, check_base
from hosts import Host

API = "https://api.github.com"
TOKEN = "GITHUB_TOKEN"
API_VERSION = "2022-11-28"
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
ORG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
REPO = re.compile(r"([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})")
PER_PAGE = 50
CLOSED_READS = 8  # merged or declined pull requests read in full per list, newest first
OPEN_FRESH = 600  # seconds an unmoved open pull request is trusted before it is read again
VERDICTS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested"}
THREADS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
    "pullRequest(number:$number){reviewThreads(first:100){nodes{isResolved resolvedBy{login} "
    "comments(first:1){nodes{databaseId}}}}}}}"
)


def configured_org():
    return config.get("GITHUB_ORG").strip()


class GitHubApi(Api):
    name = "GitHub"
    default_limit = 5000

    def auth_headers(self, token):
        return {"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": API_VERSION}

    def accept(self):
        return "application/vnd.github+json"

    def read_limits(self, headers, resource):
        if "x-ratelimit-remaining" not in headers:
            return
        try:
            entry = {"limit": int(headers.get("x-ratelimit-limit") or 0),
                     "remaining": int(headers["x-ratelimit-remaining"]),
                     "reset": int(headers.get("x-ratelimit-reset") or 0)}
        except ValueError:
            return
        with self.lock:
            self.limits[headers.get("x-ratelimit-resource") or resource] = entry

    def limited(self, status, headers, message):
        # A spent limit answers 403, like a missing permission: the headers tell them apart.
        return status == 429 or status == 403 and (
            headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers or "rate limit" in message.lower())

    def graphql_url(self):
        return self.base[: -len("/v3")] + "/graphql" if self.base.endswith("/api/v3") else self.base + "/graphql"


# ---------------------------------------------------------------- shapes

def user(raw):
    raw = raw or {}
    login = raw.get("login")
    return {"uuid": login, "name": raw.get("name") or login or "Unknown", "nickname": login, "account_id": None}


def split_repo(repo):
    match = REPO.fullmatch(str(repo or ""))
    if not match:
        raise ValueError(f"not a GitHub repository: {repo!r}")
    return match.group(1), match.group(2)


def repo_of(api_url):
    """owner/name from a repository_url such as https://api.github.com/repos/owner/name."""
    tail = str(api_url or "").split("/repos/", 1)[-1]
    return tail if REPO.fullmatch(tail) else ""


def _state(open_, merged_at):
    return "OPEN" if open_ else "MERGED" if merged_at else "DECLINED"


def _updated(state, raw, merged_at):
    """When it last moved, as Bitbucket's updated_on means it: for a merged or declined pull
    request that is when it closed, so the insights count a merge on the day it happened."""
    if state == "MERGED":
        return merged_at
    if state == "DECLINED":
        return raw.get("closed_at") or raw.get("updated_at")
    return raw.get("updated_at")


def reviews_of(pull, reviews):
    """One entry per person with a say, as model.reviews_of gives Bitbucket's. A verdict
    (approve, request changes) stands until a later one or a dismissal replaces it; a
    comment does not take it back. Requested reviewers who have not answered are pending."""
    author = (pull.get("user") or {}).get("login")
    by_login = {}
    for review in sorted(reviews or [], key=lambda r: r.get("submitted_at") or ""):
        who = review.get("user") or {}
        login, state = who.get("login"), review.get("state")
        if not login or login == author or state == "PENDING":
            continue  # a pending review is a draft only its writer sees
        entry = by_login.setdefault(login, {**user(who), "state": "commented", "reviewer": False, "at": None})
        if state in VERDICTS:
            entry.update(state=VERDICTS[state], reviewer=True)
        elif state == "DISMISSED":
            entry.update(state="commented", reviewer=False)  # asked again, they are pending below
        entry["at"] = review.get("submitted_at")
    for who in pull.get("requested_reviewers") or []:
        login = who.get("login")
        if not login or login == author:
            continue
        entry = by_login.get(login)
        if entry is None:
            by_login[login] = {**user(who), "state": "pending", "reviewer": True, "at": None}
        else:
            entry["reviewer"] = True
            if entry["state"] == "commented":
                entry["state"] = "pending"
                entry["commented"] = True
    return sorted(by_login.values(), key=lambda r: (model.REVIEW_RANK.get(r["state"], 9), not r["reviewer"], r["name"]))


def slim_pull(pull, reviews=()):
    """A pull request read in full, in the shape model.slim_pr gives the page."""
    head, base = pull.get("head") or {}, pull.get("base") or {}
    merged_at = pull.get("merged_at")
    state = _state(pull.get("state") == "open", merged_at)
    source = head.get("ref") or ""
    return {
        "repo": (base.get("repo") or {}).get("full_name") or "",
        "id": pull.get("number"),
        "title": pull.get("title") or "",
        "state": state,
        "draft": bool(pull.get("draft")),
        "author": user(pull.get("user")),
        "source": source,
        "dest": base.get("ref") or "",
        "source_commit": head.get("sha"),
        "created_on": pull.get("created_at"),
        "updated_on": _updated(state, pull, merged_at),
        "comment_count": (pull.get("comments") or 0) + (pull.get("review_comments") or 0),
        "task_count": 0,
        "closed_by": user(pull.get("merged_by")) if pull.get("merged_by") else None,
        "url": pull.get("html_url"),
        "reviews": reviews_of(pull, reviews),
        "ticket": model.ticket_of(source, pull.get("title")),
    }


def slim_item(item):
    """A search result: no branches and no reviewers, until it is read in full."""
    merged_at = (item.get("pull_request") or {}).get("merged_at")
    state = _state(item.get("state") == "open", merged_at)
    return {
        "repo": repo_of(item.get("repository_url")),
        "id": item.get("number"),
        "title": item.get("title") or "",
        "state": state,
        "draft": bool(item.get("draft")),
        "author": user(item.get("user")),
        "source": "",
        "dest": "",
        "source_commit": None,
        "created_on": item.get("created_at"),
        "updated_on": _updated(state, item, merged_at),
        "comment_count": item.get("comments") or 0,
        "task_count": 0,
        "closed_by": None,
        "url": item.get("html_url"),
        "reviews": [],
        "ticket": model.ticket_of(item.get("title")),
    }


def _message(raw, at=None):
    created = at or raw.get("created_at")
    updated = raw.get("updated_at")
    return {"id": raw.get("id"), "author": user(raw.get("user")), "at": created,
            "edited": updated if updated and updated != created else None, "raw": raw.get("body") or "", "deleted": False}


def build_threads(inline, general, reviews, author, states):
    """Review comments grouped under the comment they answer, then the conversation's own
    comments and review summaries, each a thread of one. states maps a thread's first comment
    to its resolution, or is None when GitHub could not say."""
    by_id = {c.get("id"): c for c in inline}
    children, roots = {}, []
    for comment in inline:
        parent = comment.get("in_reply_to_id")
        if parent in by_id:
            children.setdefault(parent, []).append(comment)
        else:
            roots.append(comment)
    threads = []
    for root in roots:
        chain = [root, *sorted(children.get(root.get("id"), []), key=lambda c: c.get("created_at") or "")]
        messages = [_message(c) for c in chain]
        outdated = root.get("line") is None and root.get("position") is None
        state = (states or {}).get(root.get("id"))
        resolved = outdated if states is None else bool(state and state["resolved"])
        threads.append({
            "id": root.get("id"),
            "path": root.get("path"),
            "line": root.get("line") or root.get("original_line"),
            "side": "old" if root.get("side") == "LEFT" else "new",
            "outdated": outdated,
            "inline": True,
            "resolved": resolved,
            "resolved_by": user({"login": state["by"]}) if state and state.get("by") else None,
            "unresolved": not resolved,
            "awaiting_author": not resolved and messages[-1]["author"]["uuid"] != author,
            "url": root.get("html_url"),
            "messages": messages,
        })
    singles = [(f"issue-{c.get('id')}", _message(c), c.get("html_url")) for c in general]
    singles += [(f"review-{r.get('id')}", _message(r, r.get("submitted_at")), r.get("html_url"))
                for r in reviews if (r.get("body") or "").strip() and r.get("state") != "PENDING"]
    for thread_id, message, url in singles:
        threads.append({"id": thread_id, "path": None, "line": None, "side": None, "outdated": False, "inline": False,
                        "resolved": False, "resolved_by": None, "unresolved": False, "awaiting_author": False,
                        "url": url, "messages": [message]})
    threads.sort(key=lambda t: t["messages"][0]["at"] or "")
    return threads


# ---------------------------------------------------------------- the host

class GitHubHost(Host):
    id = "github"
    label = "GitHub"
    writes = False
    incremental = True  # the search sorts and filters by date, so a merged list can stop early

    def __init__(self, engine, transport=None, clock=time.time):
        self.engine = engine
        self.transport = transport
        self.clock = clock
        self._api, self._api_for = None, None
        self._full = {}  # (repo, number) -> (updated_at, read_at, slim): reads not worth repeating

    @property
    def api(self):
        """Built for the address in the settings, and again when it changes. The token is read
        at each call, so a replaced one is used without a restart."""
        wanted = config.get("GITHUB_URL") or API
        if self._api is None or self._api_for != wanted:
            try:
                base = check_base(wanted, "GitHub")
            except ValueError as exc:
                raise HostError(0, f"GITHUB_URL: {exc}", unset=True) from None
            self._api = GitHubApi(base, lambda: config.get(TOKEN), transport=self.transport, clock=self.clock)
            self._api_for = wanted
        return self._api

    def budget(self):
        try:
            return self.api.budget()
        except HostError:
            return {"used": 0, "limit": GitHubApi.default_limit}

    def busy(self):
        try:
            return self.api.busy()
        except HostError:
            return False

    # ---- people -----------------------------------------------------------

    def me(self):
        raw = self.api.get("/user")
        if not raw.get("login"):
            raise HostError(502, "GitHub did not say who you are")
        return {**user(raw), "name": raw.get("name") or raw["login"]}

    def members(self):
        org = configured_org()
        if not org:
            return []  # the team picker offers the people seen on pull requests instead
        if not ORG.fullmatch(org):
            raise HostError(0, f"GITHUB_ORG is not an organization name: {org!r}")
        return [{"uuid": m["login"], "name": m["login"], "nickname": m["login"]}
                for m in self.api.paged(f"/orgs/{org}/members", {"per_page": 100}, max_pages=10) if m.get("login")]

    # ---- lists --------------------------------------------------------------

    def _query(self, *terms):
        org = configured_org()
        return " ".join([*terms, f"org:{org}"] if org and ORG.fullmatch(org) else terms)

    @staticmethod
    def _author(login):
        if not LOGIN.fullmatch(str(login or "")):
            raise ValueError(f"not a GitHub login: {login!r}")
        # An app's pull requests (dependabot[bot]) are searched by the app's name.
        return f"author:app/{login[:-5]}" if login.endswith("[bot]") else f"author:{login}"

    def _search(self, query, max_pages, meta=None):
        params = {"q": query, "sort": "updated", "order": "desc", "per_page": PER_PAGE}
        return list(self.api.paged("/search/issues", params, max_pages=max_pages, meta=meta, resource="search"))

    def prs(self, uuid, state, newer_than=None, max_pages=6, meta=None):
        author = self._author(uuid)
        since = f":>={newer_than[:10]}" if newer_than else ""
        if state == "OPEN":
            return [self._open(item) for item in self._search(self._query("is:pr", "is:open", "archived:false", author), max_pages)]
        if state == "MERGED":
            terms = ["is:pr", "is:merged", author] + ([f"merged{since}"] if since else [])
        elif state == "DECLINED":
            terms = ["is:pr", "is:closed", "is:unmerged", author] + ([f"closed{since}"] if since else [])
        else:
            raise ValueError(f"unknown state {state!r}")
        items = self._search(self._query(*terms), max_pages, meta)
        out = self._closed(uuid, items)
        # The search takes a day; the engine asks to the second.
        return [p for p in out if not newer_than or (p.get("updated_on") or "") >= newer_than]

    def review_requests(self, uuid):
        items = self._search(self._query("is:pr", "is:open", "archived:false", "review-requested:@me"), 2)
        return [self._open(item) for item in items]

    def _open(self, item):
        repo, number = repo_of(item.get("repository_url")), item.get("number")
        memo = self._full.get((repo, number))
        if memo and memo[0] == item.get("updated_at") and self.clock() - memo[1] < OPEN_FRESH:
            return memo[2]
        try:
            return self._read(repo, number)
        except (HostError, ValueError) as exc:
            if isinstance(exc, HostError) and exc.status not in (403, 404, 410):
                raise
            return slim_item(item)  # one repository the token cannot read must not blank the list

    def _closed(self, uuid, items):
        """Merged and declined pull requests do not change: the newest few are read in full
        once (their branches, reviewers and who merged them), the rest keep what the cache
        already has, or what the search says."""
        person = self.engine.person(uuid) if hasattr(self.engine, "person") else None
        known = {(p["repo"], p["id"]): p for p in (person or {}).get("merged", []) + (person or {}).get("declined", [])
                 if p.get("source")}
        out, reads = [], 0
        for item in items:
            slim = slim_item(item)
            key = (slim["repo"], slim["id"])
            memo = self._full.get(key)
            if key in known and known[key].get("updated_on") == slim["updated_on"]:
                out.append(known[key])
            elif memo and memo[2].get("updated_on") == slim["updated_on"]:
                out.append(memo[2])
            elif reads < CLOSED_READS and slim["repo"]:
                reads += 1
                try:
                    out.append(self._read(*key))
                except HostError as exc:
                    if exc.status not in (403, 404, 410):
                        raise
                    out.append(slim)
            else:
                out.append(slim)
        return out

    # ---- one pull request ---------------------------------------------------

    def _path(self, repo, number):
        owner, name = split_repo(repo)
        return f"/repos/{owner}/{name}", int(number)

    def _reviews(self, repo, number):
        base, n = self._path(repo, number)
        return list(self.api.paged(f"{base}/pulls/{n}/reviews", {"per_page": 100}, max_pages=3))

    def _read(self, repo, number, pull=None):
        base, n = self._path(repo, number)
        pull = pull or self.api.get(f"{base}/pulls/{n}")
        slim = slim_pull(pull, self._reviews(repo, n))
        self._full[(slim["repo"] or repo, n)] = (pull.get("updated_at"), self.clock(), slim)
        if len(self._full) > 2000:
            self._full.pop(next(iter(self._full)))
        return slim

    def pull_request(self, repo, pr_id):
        return self._read(repo, pr_id)

    def _comments(self, repo, number):
        base, n = self._path(repo, number)
        inline = list(self.api.paged(f"{base}/pulls/{n}/comments", {"per_page": 100}, max_pages=10))
        general = list(self.api.paged(f"{base}/issues/{n}/comments", {"per_page": 100}, max_pages=10))
        return inline, general

    def _thread_states(self, repo, number):
        owner, name = split_repo(repo)
        try:
            answer = self.api.request("POST", self.api.graphql_url(), resource="graphql", body={
                "query": THREADS_QUERY, "variables": {"owner": owner, "name": name, "number": int(number)}})
        except HostError:
            return None
        if not isinstance(answer, dict) or answer.get("errors") or not answer.get("data"):
            return None
        pull = ((answer["data"].get("repository") or {}).get("pullRequest") or {})
        states = {}
        for node in (pull.get("reviewThreads") or {}).get("nodes") or []:
            first = ((node.get("comments") or {}).get("nodes") or [{}])[0].get("databaseId")
            if first is not None:
                states[first] = {"resolved": bool(node.get("isResolved")), "by": (node.get("resolvedBy") or {}).get("login")}
        return states

    def detail(self, repo, pr_id):
        base, n = self._path(repo, pr_id)
        pull = self.api.get(f"{base}/pulls/{n}")
        reviews = self._reviews(repo, n)
        slim = slim_pull(pull, reviews)
        self._full[(slim["repo"] or repo, n)] = (pull.get("updated_at"), self.clock(), slim)
        inline, general = self._comments(repo, n)
        threads = build_threads(inline, general, reviews, slim["author"]["uuid"], self._thread_states(repo, n))
        return {"pr": slim, "description": pull.get("body") or "", "threads": threads, "tasks": []}

    def diff(self, repo, pr_id):
        base, n = self._path(repo, pr_id)
        return self.api.request("GET", f"{base}/pulls/{n}", accept="application/vnd.github.diff", raw=True)

    def commits(self, repo, pr_id):
        base, n = self._path(repo, pr_id)
        out = []
        for c in self.api.paged(f"{base}/pulls/{n}/commits", {"per_page": 100}, max_pages=3):
            commit = c.get("commit") or {}
            author = commit.get("author") or {}
            out.append({"sha": (c.get("sha") or "")[:8], "subject": (commit.get("message") or "").split("\n", 1)[0],
                        "author": (c.get("author") or {}).get("login") or author.get("name") or "", "at": author.get("date"),
                        "url": c.get("html_url")})
        out.reverse()  # newest first, as Bitbucket lists them
        return out

    def timeline(self, repo, pr_id):
        reviews = self._reviews(repo, pr_id)
        inline, general = self._comments(repo, pr_id)
        out = []
        for r in reviews:
            who = user(r.get("user"))["name"]
            if r.get("state") in VERDICTS:
                out.append({"kind": VERDICTS[r["state"]], "who": who, "at": r.get("submitted_at")})
            elif r.get("state") == "COMMENTED" and (r.get("body") or "").strip():
                out.append({"kind": "commented", "who": who, "at": r.get("submitted_at"), "where": None, "text": r["body"][:160]})
        for c in inline + general:
            out.append({"kind": "commented", "who": user(c.get("user"))["name"], "at": c.get("created_at"),
                        "where": c.get("path"), "text": (c.get("body") or "")[:160]})
        out.sort(key=lambda x: x.get("at") or "", reverse=True)
        return out

    def changes_requested(self, repo, pr_id):
        return [{"name": (r.get("user") or {}).get("login"), "uuid": (r.get("user") or {}).get("login"), "at": r.get("submitted_at")}
                for r in self._reviews(repo, pr_id) if r.get("state") == "CHANGES_REQUESTED"]

    def behind(self, pr):
        """Commits on the target the branch lacks, from one compare. A branch from a fork
        is not compared (its name alone does not find it): it raises, and is skipped."""
        if not pr.get("source") or not pr.get("dest"):
            raise HostError(0, "the branches are not known yet")
        base, _ = self._path(pr["repo"], pr["id"])
        answer = self.api.get(f"{base}/compare/{quote(pr['dest'], safe='/')}...{quote(pr['source'], safe='/')}", {"per_page": 1})
        return int(answer.get("behind_by") or 0), False

    def commenters(self, repo, pr_id, count):
        inline, general = self._comments(repo, pr_id)
        newest = sorted(inline + general, key=lambda c: c.get("created_at") or "", reverse=True)[:count]
        return [{"uuid": user(c.get("user"))["uuid"], "name": user(c.get("user"))["name"]} for c in newest]


# ---------------------------------------------------------------- setup

def _hint(exc, scope):
    if exc.status == 401:
        return "the token is wrong or expired"
    if exc.status == 403:
        return exc.message if "SAML" in exc.message else f"give the token {scope}"
    if exc.status == 404:
        return "not found, or the token cannot see it"
    return exc.message


def check(body, transport=None):
    """Each kind of read PixelDeck makes, tried with the typed (or saved) token before it is
    saved. Nothing here is kept, and the token never comes back in the answer."""
    token = str(body.get("token") or config.get(TOKEN)).strip()
    url = check_base(str(body.get("url") or config.get("GITHUB_URL") or API), "GitHub")
    org = str(body["org"] if "org" in body else configured_org()).strip()
    if not token:
        raise ValueError("Paste a token first.")
    if org and not ORG.fullmatch(org):
        raise ValueError("The organization is the name in github.com/<organization>.")
    api = GitHubApi(url, token, transport=transport)
    results, me, sample = [], None, None
    steps = [("Your user", "any token")]
    if org:
        steps.append(("The organization", "Metadata: read (classic: read:org)"))
    steps += [("Pull requests", "Pull requests: read (classic: repo)"), ("Code, for diffs", "Contents: read (classic: repo)")]
    for label, scope in steps:
        try:
            if label == "Your user":
                me = api.get("/user")
            elif label == "The organization":
                api.get(f"/orgs/{org}")
            elif not me:
                results.append({"label": label, "ok": None, "scope": scope, "hint": "needs your user first"})
                continue
            elif label == "Pull requests":
                query = f"is:pr author:{me.get('login')}" + (f" org:{org}" if org else "")
                found = api.get("/search/issues", {"q": query, "per_page": 1}, resource="search")
                sample = repo_of(((found.get("items") or [{}])[0]).get("repository_url"))
                if sample:
                    api.get(f"/repos/{sample}/pulls", {"per_page": 1})
            elif not sample:
                results.append({"label": label, "ok": None, "scope": scope, "hint": "needs one of your pull requests to try"})
                continue
            else:
                api.get(f"/repos/{sample}/commits", {"per_page": 1})
        except HostError as exc:
            results.append({"label": label, "ok": False, "scope": scope, "status": exc.status, "hint": _hint(exc, scope)})
            if exc.status in (0, 401):
                break  # the rest would fail the same way
            continue
        results.append({"label": label, "ok": True, "scope": scope})
    return {"ok": len(results) == len(steps) and all(r["ok"] for r in results), "results": results,
            "me": (me or {}).get("login")}


def report(engine):
    """For the settings: what is set, never the token."""
    active = engine.host.id == "github"
    return {"token_set": bool(config.get(TOKEN)), "url": config.get("GITHUB_URL") or API, "org": configured_org() or None,
            "active": active, "auth": engine.auth if active else None, "me": engine.me() if active else None,
            "budget": engine.budget() if active else None}
