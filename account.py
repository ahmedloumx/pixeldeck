"""The setup screen: the Bitbucket account and the clones folder, saved without editing a file.

status() says what is set and what is missing. check() tries an email and token on each
Bitbucket endpoint PixelDeck reads, before anything is saved, and names the scope a refusal
asks for. save() writes the settings file and puts the token in the credential store when the
system has one; import_env() does the same from an existing .env.
"""

import os
import re
from pathlib import Path

import config
import credentials
from bitbucket import BitbucketError, Client

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


def status():
    token = _source(TOKEN)
    fields = {"email": config.get("BITBUCKET_EMAIL"), "workspace": config.workspace(),
              "root": config.get("PIXELDECK_WORKSPACE_ROOT")}
    return {
        "needed": not (token and fields["workspace"]),
        **fields,
        "root_default": str(config.workspace_root()),
        "token": token,
        "store": credentials.store().name,
        "env_file": str(config.env_path()),
        "from_environment": [k for k in ("BITBUCKET_EMAIL", TOKEN, "BITBUCKET_WORKSPACE", "PIXELDECK_WORKSPACE_ROOT")
                             if _source(k) == "environment"],
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


def check(body, client_factory=Client):
    """Each endpoint PixelDeck reads, tried with the typed (or saved) email and token."""
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


def save(body):
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
    return answer
