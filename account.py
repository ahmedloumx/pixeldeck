"""The setup screen: where your code lives (Bitbucket, GitHub or GitLab), its account and the
clones folder, saved without editing a file.

status() says what is set and what is missing. check() tries the typed account on each
endpoint PixelDeck reads, before anything is saved, and says what a refusal asks for. save()
writes the settings file and puts the token in the credential store when the system has one;
import_env() does the same from an existing .env.
"""

import os
import re
import urllib.parse
from pathlib import Path

import config
import credentials
import forge
from bitbucket import BitbucketError, Client

FEATURES = ["setup", "hosts"]
TOKEN = "BITBUCKET_TOKEN"
# A 403 names the scope it wanted. Older tokens use the short names on the left; the Atlassian
# token page lists the long ones.
SCOPES = {"account": "read:user:bitbucket", "team": "read:workspace:bitbucket",
          "repository": "read:repository:bitbucket", "pullrequest": "read:pullrequest:bitbucket",
          "project": "read:project:bitbucket"}
WORKSPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
# A GitHub user or organization: letters, digits and single hyphens, 39 characters at most.
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
# A GitLab group, with its subgroups: acme, or acme/platform.
GROUP = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}(?:/[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}){0,19}")
ENV_KEYS = ("CODE_HOST", "BITBUCKET_EMAIL", TOKEN, "BITBUCKET_WORKSPACE", "GITHUB_TOKEN", "GITHUB_OWNER",
            "GITHUB_API_URL", "GITLAB_TOKEN", "GITLAB_GROUP", "GITLAB_URL", "PIXELDECK_WORKSPACE_ROOT")


def _source(key):
    """Where a setting comes from: the environment, the file or the store, else None."""
    if os.environ.get(key):
        return "environment"
    if config.file_value(key):
        return "file"
    if key in config.SECRET_KEYS and credentials.store().get(key):
        return "store"
    return None


def _host(body):
    host = str(body.get("host") or config.host()).strip().lower()
    if host not in config.HOSTS:
        raise ValueError("Pick Bitbucket, GitHub or GitLab.")
    return host


def _server(value, what):
    """A server address typed in the setup: https only (plain http for this computer alone),
    with no user, query or fragment in it. Empty keeps the default."""
    value = str(value or "").strip().rstrip("/")
    if not value:
        return ""
    parts = urllib.parse.urlsplit(value)
    local = parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")
    if not (parts.scheme == "https" or local) or not parts.hostname or parts.username or parts.query or parts.fragment:
        raise ValueError(f"{what} is an address that starts with https://")
    return value


def status():
    host = config.host()
    token = _source(config.HOST_TOKEN[host])
    return {
        "needed": not (token and config.workspace()),
        "host": host,
        "hosts": [{"id": h, "label": forge.label(h), "token": _source(config.HOST_TOKEN[h]),
                   "workspace": config.get(config.HOST_WORKSPACE[h]) or ""} for h in config.HOSTS],
        "email": config.get("BITBUCKET_EMAIL"),
        "workspace": config.get("BITBUCKET_WORKSPACE"),
        "owner": config.get("GITHUB_OWNER"),
        "api_url": config.get("GITHUB_API_URL"),
        "group": config.get("GITLAB_GROUP"),
        "gitlab_url": config.get("GITLAB_URL"),
        "root": config.get("PIXELDECK_WORKSPACE_ROOT"),
        "root_default": str(config.workspace_root()),
        "token": token,
        "store": credentials.store().name,
        "env_file": str(config.env_path()),
        "from_environment": [k for k in ENV_KEYS if _source(k) == "environment"],
    }


def _scope_hint(error):
    wanted = (error.detail or {}).get("required") or []
    names = [SCOPES.get(name, name) for name in wanted if isinstance(name, str)]
    if names:
        return f"add {', '.join(names)} to the token"
    if error.status == 401:
        return "the email or the token is wrong"
    if error.status == 404:
        return "no such workspace, or the token cannot see it"
    return error.message


def _run(steps, stop_on=(0, 401)):
    """Each step is (label, call, scope, hint for a 404). The first answer is your user."""
    results, me = [], None
    for label, call, scope, missing in steps:
        try:
            answer = call(me)
        except BitbucketError as exc:
            hint = ("the token is wrong or expired" if exc.status == 401 else missing if exc.status == 404 and missing
                    else exc.message)
            results.append({"label": label, "ok": False, "scope": scope, "status": exc.status, "hint": hint})
            if exc.status in stop_on:
                break  # the rest would fail the same way
            continue
        if me is None:
            me = answer
        results.append({"label": label, "ok": True, "scope": scope})
    return results, me


def check(body, client_factory=Client):
    """Each endpoint PixelDeck reads, tried with the typed (or saved) account."""
    host = _host(body)
    if host == "github":
        return _check_github(body)
    if host == "gitlab":
        return _check_gitlab(body)
    email = str(body.get("email") or config.get("BITBUCKET_EMAIL")).strip()
    token = str(body.get("token") or config.get(TOKEN)).strip()
    workspace = str(body.get("workspace") or config.get("BITBUCKET_WORKSPACE")).strip()
    if not token:
        raise ValueError("Paste an API token first.")
    if not WORKSPACE.fullmatch(workspace):
        raise ValueError("The workspace is the name in bitbucket.org/<workspace>/<repo>.")
    client = client_factory(workspace, login=(email, token))
    results, me = [], None
    steps = [
        ("Your user", "/user", "read:user:bitbucket"),
        ("The workspace", f"/workspaces/{workspace}", "read:workspace:bitbucket"),
        ("Repositories", f"/repositories/{workspace}", "read:repository:bitbucket"),
        ("Pull requests", None, "read:pullrequest:bitbucket"),
    ]
    for label, path, scope in steps:
        if path is None:
            if not me:
                results.append({"label": label, "ok": None, "scope": scope, "hint": "needs your user first"})
                continue
            path = f"/workspaces/{workspace}/pullrequests/{me['uuid']}"
        try:
            answer = client.get(path, {"pagelen": 1} if "pullrequests" in path or path.startswith("/repositories") else None)
        except BitbucketError as exc:
            results.append({"label": label, "ok": False, "scope": scope, "status": exc.status, "hint": _scope_hint(exc)})
            if exc.status in (0, 401):
                break  # the rest would fail the same way
            continue
        if path == "/user":
            me = answer
        results.append({"label": label, "ok": True, "scope": scope})
    return {"ok": bool(results) and all(r["ok"] for r in results) and len(results) == len(steps), "results": results,
            "me": me.get("display_name") if me else None}


def _check_github(body, client_factory=None):
    import github
    token = str(body.get("token") or config.get("GITHUB_TOKEN")).strip()
    owner = str(body.get("owner") or config.get("GITHUB_OWNER")).strip()
    api = _server(body.get("api_url"), "The GitHub API address") or config.github_api()
    if not token:
        raise ValueError("Paste a GitHub token first.")
    if not OWNER.fullmatch(owner):
        raise ValueError("The owner is the name in github.com/<owner>/<repo>: you, or your organization.")
    client = (client_factory or github.Client)(owner, login=token, api=api)
    who = urllib.parse.quote(owner, safe="")

    def search(me):
        scope = "org" if client._owner() == "org" else "user"
        return client.get("/search/issues", {"q": f"is:pr author:{me['login']} {scope}:{owner}", "per_page": 1})

    steps = [
        ("Your user", lambda me: client.get("/user"), "a valid token", None),
        ("The owner", lambda me: client.get(f"/users/{who}"), "an existing user or organization", "no such user or organization"),
        ("Pull requests", search, "Pull requests: read (classic: repo)", None),
        ("Review threads", lambda me: client.graphql("query { viewer { login } }", {}), "GraphQL, for resolved threads", None),
    ]
    results, me = _run(steps)
    return {"ok": len(results) == len(steps) and all(r["ok"] for r in results), "results": results,
            "me": (me.get("name") or me.get("login")) if me else None}


def _check_gitlab(body, client_factory=None):
    import gitlab
    token = str(body.get("token") or config.get("GITLAB_TOKEN")).strip()
    group = str(body.get("group") or config.get("GITLAB_GROUP")).strip().strip("/")
    url = _server(body.get("gitlab_url"), "The GitLab address") or config.gitlab_url()
    if not token:
        raise ValueError("Paste a GitLab token first.")
    if not GROUP.fullmatch(group):
        raise ValueError("The group is the path in gitlab.com/<group>/<project>, such as acme or acme/platform.")
    client = (client_factory or gitlab.Client)(group, login=token, url=url)
    where = f"/groups/{urllib.parse.quote(group, safe='')}"
    steps = [
        ("Your user", lambda me: client.get("/user"), "read_api", None),
        ("The group", lambda me: client.get(where), "read_api, as a member of the group", "no such group, or you are not in it"),
        ("Merge requests", lambda me: client.get(f"{where}/merge_requests", {"per_page": 1, "scope": "all"}), "read_api", None),
    ]
    results, me = _run(steps)
    return {"ok": len(results) == len(steps) and all(r["ok"] for r in results), "results": results,
            "me": (me.get("name") or me.get("username")) if me else None}


def _put_secrets(values):
    """Move the tokens in values into the store when there is one; what stays goes to the file."""
    store = credentials.store()
    if not store.name:
        return values
    out = dict(values)
    for key in config.SECRET_KEYS:
        if out.get(key):
            store.set(key, out[key])
            out[key] = ""  # off the file, so the store's value is the one read
    return out


def _token(body, key, missing):
    token = str(body.get("token") or "").strip()
    if any(c.isspace() for c in token):
        raise ValueError("A token has no spaces; paste it again.")
    if not token:
        # Keep the saved one, but take it out of the file when a store can hold it.
        token = config.file_value(key) if credentials.store().name else ""
    if not token and not config.get(key):
        raise ValueError(missing)
    return token


def save(body):
    host = _host(body)
    root = str(body.get("root") or "").strip()
    if root and not Path(root).expanduser().is_dir():
        raise ValueError(f"No such folder: {root}")
    if host == "github":
        owner = str(body.get("owner") or "").strip()
        if not OWNER.fullmatch(owner):
            raise ValueError("The owner is the name in github.com/<owner>/<repo>: you, or your organization.")
        api = _server(body.get("api_url"), "The GitHub API address")
        token = _token(body, "GITHUB_TOKEN", "Paste a GitHub token.")
        values = {"CODE_HOST": "github", "GITHUB_OWNER": owner, "GITHUB_API_URL": api, "PIXELDECK_WORKSPACE_ROOT": root}
        if token:
            values["GITHUB_TOKEN"] = token
    elif host == "gitlab":
        group = str(body.get("group") or "").strip().strip("/")
        if not GROUP.fullmatch(group):
            raise ValueError("The group is the path in gitlab.com/<group>/<project>, such as acme or acme/platform.")
        url = _server(body.get("gitlab_url"), "The GitLab address")
        token = _token(body, "GITLAB_TOKEN", "Paste a GitLab token.")
        values = {"CODE_HOST": "gitlab", "GITLAB_GROUP": group, "GITLAB_URL": url, "PIXELDECK_WORKSPACE_ROOT": root}
        if token:
            values["GITLAB_TOKEN"] = token
    else:
        email = str(body.get("email") or "").strip()
        workspace = str(body.get("workspace") or "").strip()
        if email and "@" not in email:
            raise ValueError("The email is the one you sign in to Atlassian with.")
        if not WORKSPACE.fullmatch(workspace):
            raise ValueError("The workspace is the name in bitbucket.org/<workspace>/<repo>.")
        token = _token(body, TOKEN, "Paste an API token.")
        # Bitbucket is the default: no CODE_HOST line at all.
        values = {"CODE_HOST": "", "BITBUCKET_EMAIL": email, "BITBUCKET_WORKSPACE": workspace, "PIXELDECK_WORKSPACE_ROOT": root}
        if token:
            values[TOKEN] = token
    config.save(_put_secrets(values))
    return status()


def import_env(body):
    """Copy every setting of another PixelDeck's .env, its tokens into the store."""
    path = Path(str(body.get("path") or "").strip().strip('"')).expanduser()
    if path.is_dir():
        path = path / ".env"
    if not path.is_file():
        raise ValueError(f"No .env file at {path}")
    if path.resolve() == config.env_path().resolve():
        raise ValueError("That is the settings file PixelDeck already uses.")
    try:
        values = config.parse_env(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from None
    if not values:
        raise ValueError(f"{path} has no settings in it.")
    config.save(_put_secrets(values))
    return {**status(), "imported": sorted(values)}


def route(handler, engine, method, name, query, body):
    """GET setup, POST setup-check, setup, setup-import; None for any other route."""
    if method == "GET" and name == "setup":
        return status()
    if method != "POST" or name not in ("setup-check", "setup", "setup-import"):
        return None
    if name == "setup-check":
        return check(body)
    try:
        answer = save(body) if name == "setup" else import_env(body)
    except credentials.StoreError as exc:
        raise ValueError(str(exc)) from None
    engine.root = Path(config.workspace_root())  # the next scan of your clones looks there
    # Another host or another workspace: the engine starts over on it, and the page reloads.
    if forge.kind(engine.client) != config.host() or (engine.client.workspace or "") != config.workspace():
        engine.use_host()
        answer = {**answer, "reload": True}
    return answer
