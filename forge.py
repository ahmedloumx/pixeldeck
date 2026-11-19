"""Where your code lives: Bitbucket Cloud, GitHub or GitLab (CODE_HOST in the settings).

PixelDeck began on Bitbucket, and model.py reads Bitbucket's shapes. The GitHub and GitLab
clients (github.py, gitlab.py) answer in those same shapes, so the queue, a pull request, the
notifications and the insights read every host alike.

What the engine and the actions need beyond those shapes is here, one function per need: on
Bitbucket the very calls PixelDeck has always made, on GitHub and GitLab a method of their
client. Every client raises BitbucketError for a refusal, so one except clause catches the
three hosts.
"""

import re
import urllib.parse

import config

LABELS = {"bitbucket": "Bitbucket", "github": "GitHub", "gitlab": "GitLab"}


def kind(client):
    """The host a client talks to. A client that does not say is Bitbucket's, as the tests' are."""
    return getattr(client, "kind", "bitbucket")


def label(host=None):
    return LABELS.get(host or config.host(), "Bitbucket")


def client(workspace=None, login=None, host=None):
    """A client for the chosen host. login is what to try before it is saved: (email, token)
    on Bitbucket, the token alone on GitHub and GitLab."""
    host = host or config.host()
    if host == "github":
        import github
        return github.Client(workspace, login=login)
    if host == "gitlab":
        import gitlab
        return gitlab.Client(workspace, login=login)
    import bitbucket
    return bitbucket.Client(workspace, login=login)


def missing_setting(exc):
    """A client that refused before any call, because a token or a workspace is not set."""
    return exc.status == 0 and bool(re.match(r"No [A-Z]+_[A-Z_]+ in ", exc.message or ""))


# ---- web addresses and clones ---------------------------------------------------------------

def web_host(host=None):
    """The server name in a clone's remote and in a pull request's web address."""
    host = host or config.host()
    if host == "github":
        name = urllib.parse.urlsplit(config.github_api()).hostname or "api.github.com"
        return "github.com" if name == "api.github.com" else name
    if host == "gitlab":
        return urllib.parse.urlsplit(config.gitlab_url()).hostname or "gitlab.com"
    return "bitbucket.org"


def web_base(host=None):
    """The website, as https://server (a self-hosted GitLab keeps its port and its path)."""
    host = host or config.host()
    if host == "gitlab":
        return config.gitlab_url()
    if host == "github":
        parts = urllib.parse.urlsplit(config.github_api())
        return "https://github.com" if parts.hostname == "api.github.com" else f"{parts.scheme}://{parts.netloc}"
    return "https://bitbucket.org"


def _remote(url):
    """(server, path) of a git remote: https://server/path, ssh://user@server:port/path or
    user@server:path. A Windows path (C:/x) is no remote."""
    url = (url or "").strip()
    if "://" in url:
        parts = urllib.parse.urlsplit(url)
        return (parts.hostname or "").lower(), parts.path.strip("/")
    if ":" in url and not re.match(r"^[A-Za-z]:[\\/]", url):
        left, _, path = url.partition(":")
        return left.rsplit("@", 1)[-1].lower(), path.strip("/")
    return "", ""


def remote_slug(url, workspace=None, host=None):
    """The repository a clone's origin names under the workspace, or None. On GitLab a project
    in a subgroup keeps its path below the group (platform/shop-api)."""
    host = host or config.host()
    workspace = config.workspace() if workspace is None else workspace
    if not workspace:
        return None
    if host == "bitbucket":
        for prefix in (f"bitbucket.org:{workspace}/", f"bitbucket.org/{workspace}/"):
            if prefix in url:
                return url.split(prefix, 1)[1].removesuffix(".git")
        return None
    server, path = _remote(url)
    servers = {web_host(host)}
    if host == "github" and "github.com" in servers:
        servers.add("ssh.github.com")  # SSH over port 443
    if server not in servers:
        return None
    if host == "gitlab":  # a server reached under a path: https://example.com/gitlab/group/project
        under = urllib.parse.urlsplit(config.gitlab_url()).path.strip("/")
        if under and path.lower().startswith(under.lower() + "/"):
            path = path[len(under) + 1:]
    head = workspace.lower() + "/"
    if not path.lower().startswith(head):
        return None
    slug = path[len(head):].removesuffix(".git").strip("/")
    if not slug or (host == "github" and "/" in slug):
        return None
    return slug


def new_pr_url(slug, branch, dest, host=None):
    """The host's own page for opening a pull request from branch into dest."""
    host = host or config.host()
    workspace = config.workspace()
    if host == "github":
        ref = lambda name: urllib.parse.quote(name, safe="/")  # noqa: E731
        return f"{web_base(host)}/{workspace}/{slug}/compare/{ref(dest)}...{ref(branch)}?expand=1"
    if host == "gitlab":
        query = urllib.parse.urlencode({"merge_request[source_branch]": branch, "merge_request[target_branch]": dest})
        return f"{web_base(host)}/{workspace}/{slug}/-/merge_requests/new?{query}"
    return f"https://bitbucket.org/{workspace}/{slug}/pull-requests/new?source={branch}&dest={dest}"


# ---- what the engine reads ------------------------------------------------------------------

def behind(client, repo, source, dest):
    """Commits on dest that source lacks: (how many, five at most; whether there are more)."""
    if kind(client) != "bitbucket":
        return client.behind(repo, source, dest)
    raw = client.get(f"/repositories/{client.workspace}/{repo}/commits",
                     {"include": dest, "exclude": source, "pagelen": 5, "fields": "values.hash,next"})
    return len(raw.get("values") or []), bool(raw.get("next"))


def newest_comments(client, repo, pr_id, count):
    """The newest comments, newest first, as [{"user": {"uuid", "display_name"}, "deleted"}]."""
    if kind(client) != "bitbucket":
        return client.newest_comments(repo, pr_id, count)
    raw = client.get(
        f"/repositories/{client.workspace}/{repo}/pullrequests/{int(pr_id)}/comments",
        {"sort": "-created_on", "pagelen": max(1, min(50, int(count or 1))),
         "fields": "values.user.uuid,values.user.display_name,values.deleted"})
    return raw.get("values") or []


def pr_diff(client, repo, pr_id):
    """The pull request's unified diff: (text, cut)."""
    if kind(client) != "bitbucket":
        return client.pr_diff(repo, pr_id)
    return client.text(f"/repositories/{client.workspace}/{repo}/pullrequests/{int(pr_id)}/diff")


def pr_commits(client, repo, pr_id):
    """The pull request's commits, as Bitbucket lists them (hash, message, author, date)."""
    if kind(client) != "bitbucket":
        return client.pr_commits(repo, pr_id)
    base = f"/repositories/{client.workspace}/{repo}/pullrequests/{int(pr_id)}"
    return list(client.paged(base + "/commits", {"pagelen": 50}, max_pages=4))


def projects(client):
    """The workspace's projects ({"key", "name"} rows); a Bitbucket scope, none elsewhere."""
    if kind(client) != "bitbucket":
        return client.projects()
    return client.paged(f"/workspaces/{client.workspace}/projects", {"pagelen": 100}, max_pages=5)


def project_repos(client, key):
    """The repositories ({"slug"} rows) of one project."""
    if kind(client) != "bitbucket":
        return client.project_repos(key)
    return client.paged(f"/repositories/{client.workspace}",
                        {"q": f'project.key="{key}"', "pagelen": 100, "fields": "values.slug,next"}, max_pages=10)


# ---- what the actions write -----------------------------------------------------------------

def default_reviewers(client, repo):
    """The reviewers a new pull request gets by default ({"user": {...}} rows)."""
    if kind(client) != "bitbucket":
        return client.default_reviewers(repo)
    return client.paged(f"/repositories/{client.workspace}/{repo}/effective-default-reviewers",
                        {"pagelen": 100}, max_pages=3)


def create_pr(client, repo, payload):
    """Open a pull request; payload is Bitbucket's (title, description, source, destination,
    close_source_branch, reviewers). Answers the new pull request in Bitbucket's shape."""
    if kind(client) != "bitbucket":
        return client.create_pr(repo, payload)
    return client.send("POST", f"/repositories/{client.workspace}/{repo}/pullrequests", payload)


def decline(client, repo, pr_id):
    """Decline (Bitbucket), close (GitHub, GitLab) a pull request."""
    if kind(client) != "bitbucket":
        return client.decline(repo, pr_id)
    return client.send("POST", f"/repositories/{client.workspace}/{repo}/pullrequests/{pr_id}/decline", {})


def merge_info(client, repo, pr_id):
    """The pull request read fresh, with its destination's merge strategies and default, as
    Bitbucket answers it with those fields asked for."""
    if kind(client) != "bitbucket":
        return client.merge_info(repo, pr_id)
    return client.get(f"/repositories/{client.workspace}/{repo}/pullrequests/{pr_id}",
                      {"fields": "+destination.branch.merge_strategies,+destination.branch.default_merge_strategy"})


def merge_message(client, repo, pr_id, source, dest, title, approvers):
    """The commit message the host itself would write for this merge."""
    host = kind(client)
    if host == "github":
        return f"Merge pull request #{pr_id} from {client.workspace}/{source}\n\n{title or ''}".rstrip()
    if host == "gitlab":
        return f"Merge branch '{source}' into '{dest}'\n\n{title or ''}\n\nSee merge request {client.workspace}/{repo}!{pr_id}".replace("\n\n\n", "\n\n")
    message = f"Merged in {source} (pull request #{pr_id})\n\n{title or ''}".rstrip()
    if approvers:
        message += "\n\n" + "\n".join(f"Approved-by: {name}" for name in approvers if name)
    return message
