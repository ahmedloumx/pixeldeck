"""The setup screen: the code host (Bitbucket, GitHub or GitLab), its account and the clones
folder, saved without editing a file.

status() says what is set and what is missing. check() tries a token on each endpoint
PixelDeck reads, before anything is saved, and names the permission a refusal asks for.
save() writes the settings file and puts the token in the credential store when the system has
one; import_env() does the same from an existing .env. Each host keeps its own settings, so
switching to another and back loses nothing.
"""

import os
import re
from pathlib import Path

import config
import credentials
import github
import gitlab
from bitbucket import BitbucketError, Client
from codehost import check_base

FEATURES = ["setup"]
TOKEN = "BITBUCKET_TOKEN"
# A 403 names the scope it wanted. Older tokens use the short names on the left; the Atlassian
# token page lists the long ones.
SCOPES = {"account": "read:user:bitbucket", "team": "read:workspace:bitbucket",
          "repository": "read:repository:bitbucket", "pullrequest": "read:pullrequest:bitbucket",
          "project": "read:project:bitbucket"}
WORKSPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")


def _source(key):
    """Where a setting comes from: the environment, the file or the store, else None."""
    if os.environ.get(key):
        return "environment"
    if config.file_value(key):
        return "file"
    if key in config.SECRET_KEYS and credentials.store().get(key):
        return "store"
    return None


HOST_KEYS = {
    "bitbucket": ("BITBUCKET_EMAIL", TOKEN, "BITBUCKET_WORKSPACE"),
    "github": (github.TOKEN, "GITHUB_URL", "GITHUB_ORG"),
    "gitlab": (gitlab.TOKEN, "GITLAB_URL", "GITLAB_GROUP"),
}


def status():
    token = _source(TOKEN)
    host = config.code_host()
    fields = {"email": config.get("BITBUCKET_EMAIL"), "workspace": config.workspace(),
              "root": config.get("PIXELDECK_WORKSPACE_ROOT")}
    hosts = {
        "github": {"token": _source(github.TOKEN), "url": config.get("GITHUB_URL"), "url_default": github.API,
                   "org": github.configured_org()},
        "gitlab": {"token": _source(gitlab.TOKEN), "url": config.get("GITLAB_URL"), "url_default": gitlab.WEB,
                   "group": gitlab.configured_group()},
    }
    needed = not (token and fields["workspace"]) if host == "bitbucket" else not hosts[host]["token"]
    return {
        "needed": needed,
        "host": host,
        **fields,
        **hosts,
        "root_default": str(config.workspace_root()),
        "token": token,
        "store": credentials.store().name,
        "env_file": str(config.env_path()),
        "from_environment": [k for k in (*HOST_KEYS[host], "PIXELDECK_CODE_HOST", "PIXELDECK_WORKSPACE_ROOT")
                             if _source(k) == "environment"],
    }


def _host_of(body):
    host = str(body.get("host") or "").strip().lower()
    if host and host not in config.CODE_HOSTS:
        raise ValueError("The code host is bitbucket, github or gitlab.")
    return host


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


def check(body, client_factory=Client):
    """Each endpoint PixelDeck reads, tried with the typed (or saved) email and token."""
    host = _host_of(body) or config.code_host()
    if host == "github":
        return github.check(body)
    if host == "gitlab":
        return gitlab.check(body)
    email = str(body.get("email") or config.get("BITBUCKET_EMAIL")).strip()
    token = str(body.get("token") or config.get(TOKEN)).strip()
    workspace = str(body.get("workspace") or config.workspace()).strip()
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


def _put_secrets(values):
    """Move the tokens in values into the store when there is one; what stays goes to the file."""
    store = credentials.store()
    if not store.name:
        return values
    out = dict(values)
    for key in config.SECRET_KEYS:
        if not out.get(key):
            continue
        # Off the file only once the store gives the same token back: a keyring that is locked
        # or missing must not lose it.
        try:
            store.set(key, out[key])
            kept = store.get(key, fresh=True) == out[key]
        except credentials.StoreError:
            kept = False
        if kept:
            out[key] = ""  # so the store's value is the one read
    return out


def _save_other(host, body):
    """GitHub or GitLab: the token, the address and the organization or group, and the clones
    folder. The Bitbucket settings stay as they are, for a switch back."""
    module = github if host == "github" else gitlab
    token = str(body.get("token") or "").strip()
    url = str(body.get("url") or "").strip()
    scope_key, scope = ("GITHUB_ORG", "org") if host == "github" else ("GITLAB_GROUP", "group")
    owner = str(body.get(scope) or "").strip().strip("/")
    root = str(body.get("root") or "").strip()
    pattern = github.ORG if host == "github" else gitlab.GROUP
    if url:
        url = check_base(url, "GitHub" if host == "github" else "GitLab")
        if url == (github.API if host == "github" else gitlab.WEB):
            url = ""  # the default is not written down, so it can move with PixelDeck
    if owner and not pattern.fullmatch(owner):
        raise ValueError("The organization is the name in github.com/<organization>." if host == "github"
                         else "The group is the path in gitlab.com/<group>.")
    if any(c.isspace() for c in token):
        raise ValueError("A token has no spaces; paste it again.")
    if root and not Path(root).expanduser().is_dir():
        raise ValueError(f"No such folder: {root}")
    if not token:
        token = config.file_value(module.TOKEN) if credentials.store().name else ""
    if not token and not config.get(module.TOKEN):
        raise ValueError("Paste a token.")
    values = {"PIXELDECK_CODE_HOST": host, f"{host.upper()}_URL": url, scope_key: owner, "PIXELDECK_WORKSPACE_ROOT": root}
    if token:
        values[module.TOKEN] = token
    config.save(_put_secrets(values))
    return status()


def save(body):
    host = _host_of(body)
    if host in ("github", "gitlab"):
        return _save_other(host, body)
    email = str(body.get("email") or "").strip()
    token = str(body.get("token") or "").strip()
    workspace = str(body.get("workspace") or "").strip()
    root = str(body.get("root") or "").strip()
    if email and "@" not in email:
        raise ValueError("The email is the one you sign in to Atlassian with.")
    if not WORKSPACE.fullmatch(workspace):
        raise ValueError("The workspace is the name in bitbucket.org/<workspace>/<repo>.")
    if any(c.isspace() for c in token):
        raise ValueError("A token has no spaces; paste it again.")
    if root and not Path(root).expanduser().is_dir():
        raise ValueError(f"No such folder: {root}")
    if not token:
        # Keep the saved one, but take it out of the file when a store can hold it.
        token = config.file_value(TOKEN) if credentials.store().name else ""
    if not token and not config.get(TOKEN):
        raise ValueError("Paste an API token.")
    values = {"BITBUCKET_EMAIL": email, "BITBUCKET_WORKSPACE": workspace, "PIXELDECK_WORKSPACE_ROOT": root}
    if host:  # an older page names no host: the choice stays as it was
        values["PIXELDECK_CODE_HOST"] = ""  # Bitbucket is the default, so the key goes
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
        values = config.parse_env(path.read_text(encoding="utf-8"))
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
    if hasattr(engine, "use_host"):
        engine.use_host()  # a new code host: its own cache and team, read from now on
    return answer
