"""The code host the engine reads pull requests from: Bitbucket, GitHub or GitLab.

The engine keeps the cache, the cadence, the team and the notifications; a host only answers
questions about pull requests, in the shapes model.py gives the page:

  me(), members()                        who you are, and who could be on your team
  prs(uuid, state, newer_than, ...)      one person's OPEN, MERGED or DECLINED pull requests
  review_requests(uuid)                  open ones that ask for your review, or None: the
                                         engine then finds them on your team's lists
  pull_request(repo, id), detail(...)    one pull request, and its description and threads
  diff, commits, timeline                what the pull request view shows
  changes_requested(repo, id)            who asked for changes, for the insights
  behind(pr), commenters(repo, id, n)    for the notifications
  projects(), project_repos(key)         Bitbucket's project scope; other hosts have none
  budget(), busy()                       the status bar's meter, and when to stop polling

A person is a uuid the host chooses (Bitbucket's {uuid}, a GitHub or GitLab login), a
repository a string it chooses (a Bitbucket slug, owner/name on GitHub, the project path on
GitLab). Bitbucket can also write (merge, decline, review, sync, open pull requests, the Repos
panel); the others only read for now, and the routes that write are refused for them, so a
stale page can never send a Bitbucket call to them.
"""

import config
import model
from bitbucket import HOURLY_LIMIT

BACKGROUND_SHARE = 0.8

# Routes only Bitbucket answers: the writes, and the panels built on its API.
BITBUCKET_ROUTES = {"merge", "merge-options", "decline", "review", "sync", "pr-sync", "pr-create", "branch",
                    "branch-diff", "branch-delete", "propose", "proposal-decide"}
# Page features that need those routes: a host without them does not offer them.
BITBUCKET_FEATURES = {"merge", "sync", "merge-options", "reviews", "proposals", "repos", "repo-commit"}


def file_for(path, host_id):
    """Each host keeps its own cache and team, so switching hosts and back loses nothing:
    Bitbucket the files it always had, the others a sibling named after them."""
    return path if host_id == "bitbucket" else path.with_name(f"{path.stem}-{host_id}{path.suffix}")


def current():
    return config.code_host()


def make(host_id, engine):
    if host_id == "github":
        import github
        return github.GitHubHost(engine)
    if host_id == "gitlab":
        import gitlab
        return gitlab.GitLabHost(engine)
    return BitbucketHost(engine)


class Host:
    """What every host shares. writes says whether the Bitbucket-only routes apply."""

    id = "bitbucket"
    label = "Bitbucket"
    writes = False

    def public(self):
        return {"id": self.id, "label": self.label, "writes": self.writes}

    def serves(self, route):
        return self.writes or not (route in BITBUCKET_ROUTES or route == "repos" or route.startswith("repo-"))

    def refusal(self, route):
        """What the page says when it asks for a route this host does not serve."""
        if route == "repos" or route.startswith("repo-"):
            return f"The Repos panel reads Bitbucket only for now; browse the code on {self.label}."
        return f"PixelDeck does not do that on {self.label} yet: do it on {self.label}."

    def features(self, features):
        return list(features) if self.writes else [f for f in features if f not in BITBUCKET_FEATURES]

    def review_requests(self, uuid):
        return None

    def projects(self):
        return []

    def project_repos(self, key):
        return None


class BitbucketHost(Host):
    """Bitbucket Cloud through the engine's own client, read at each call: the actions and the
    Repos panel use that client directly, and a test may swap it on a live engine."""

    id = "bitbucket"
    label = "Bitbucket"
    writes = True

    def __init__(self, engine):
        self.engine = engine

    @property
    def client(self):
        return self.engine.client

    @property
    def incremental(self):
        """Whether a merged list can stop at the newest pull request already cached."""
        return self.client.sort_supported

    def budget(self):
        return {"used": self.client.calls_last_hour(), "limit": HOURLY_LIMIT}

    def busy(self):
        return self.client.calls_last_hour() > int(HOURLY_LIMIT * BACKGROUND_SHARE)

    def me(self):
        raw = self.client.me()
        return {"uuid": raw.get("uuid"), "name": raw.get("display_name"), "nickname": raw.get("nickname"),
                "account_id": raw.get("account_id")}

    def members(self):
        return [{"uuid": m.get("uuid"), "name": m.get("display_name"), "nickname": m.get("nickname")}
                for m in self.client.members() if m.get("uuid")]

    def prs(self, uuid, state, newer_than=None, max_pages=6, meta=None):
        kw = {"max_pages": max_pages}
        if newer_than is not None:
            kw["newer_than"] = newer_than
        if meta is not None:
            kw["meta"] = meta
        return [model.slim_pr(p) for p in self.client.user_prs(uuid, state, **kw)]

    def pull_request(self, repo, pr_id):
        return model.slim_pr(self.client.pull_request(repo, pr_id))

    def detail(self, repo, pr_id):
        raw = self.client.pull_request(repo, pr_id)
        full = model.slim_pr(raw)
        threads = model.build_threads(self.client.comments(repo, pr_id), full["author"]["uuid"])
        tasks = model.slim_tasks(self.client.tasks(repo, pr_id))
        return {"pr": full, "description": raw.get("description") or "", "threads": threads, "tasks": tasks}

    def _base(self, repo, pr_id):
        return f"/repositories/{self.client.workspace}/{repo}/pullrequests/{int(pr_id)}"

    def diff(self, repo, pr_id):
        return self.client.text(self._base(repo, pr_id) + "/diff")

    def commits(self, repo, pr_id):
        out = []
        for c in self.client.paged(self._base(repo, pr_id) + "/commits", {"pagelen": 50}, max_pages=4):
            author = c.get("author") or {}
            out.append({
                "sha": (c.get("hash") or "")[:8],
                "subject": (c.get("message") or "").split("\n", 1)[0],
                "author": (author.get("user") or {}).get("display_name") or (author.get("raw") or "").split(" <")[0],
                "at": c.get("date"),
                "url": ((c.get("links") or {}).get("html") or {}).get("href"),  # in the same answer: no call
            })
        return out

    def timeline(self, repo, pr_id):
        return model.timeline(self.client.activity(repo, pr_id))

    def changes_requested(self, repo, pr_id):
        return [
            {"name": (e["changes_requested"].get("user") or {}).get("display_name"),
             "uuid": (e["changes_requested"].get("user") or {}).get("uuid"),
             "at": e["changes_requested"].get("date")}
            for e in self.client.activity(repo, pr_id) if isinstance(e.get("changes_requested"), dict)
        ]

    def behind(self, pr):
        """Commits on the target the branch lacks: five at most, enough to say so."""
        raw = self.client.get(f"/repositories/{self.client.workspace}/{pr['repo']}/commits",
                              {"include": pr["dest"], "exclude": pr["source"], "pagelen": 5, "fields": "values.hash,next"})
        return len(raw.get("values") or []), bool(raw.get("next"))

    def commenters(self, repo, pr_id, count):
        """The newest comments' authors, newest first, deleted ones left out."""
        raw = self.client.get(self._base(repo, pr_id) + "/comments",
                              {"sort": "-created_on", "pagelen": count,
                               "fields": "values.user.uuid,values.user.display_name,values.deleted"})
        return [{"uuid": (c.get("user") or {}).get("uuid"), "name": (c.get("user") or {}).get("display_name")}
                for c in raw.get("values") or [] if not c.get("deleted")]

    def projects(self):
        raw = self.client.paged(f"/workspaces/{self.client.workspace}/projects", {"pagelen": 100}, max_pages=5)
        return sorted(({"key": p.get("key"), "name": p.get("name")} for p in raw if p.get("key")),
                      key=lambda p: (p["name"] or "").lower())

    def project_repos(self, key):
        raw = self.client.paged(f"/repositories/{self.client.workspace}",
                                {"q": f'project.key="{key}"', "pagelen": 100, "fields": "values.slug,next"},
                                max_pages=10)
        return sorted(r["slug"] for r in raw if r.get("slug"))
