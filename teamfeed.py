"""The team activity feed: one timeline across your team's pull requests (Bitbucket only).

Entries come from each repository's activity log (hosts.repo_activity), read by the engine and
kept in a file of their own. The log has no entry for a pull request being opened, so those come
from the cached pull requests. The functions here are pure: the engine reads and keeps, they
merge, fill in and filter. The page reads the feed through route() below.
"""

import model
from codehost import HostError

FEATURES = ["team-activity"]
KINDS = ("commented", "approved", "changes_requested", "updated", "merged", "declined", "opened")
LIMIT = 200
LIMIT_MAX = 600
OPEN_SLACK = 60  # an update this close to the opening is the opening itself, already in the feed
EVERY_PROJECT = "*"  # the page's "every project"; no project key looks like it


def merge(kept, fresh, since, keep):
    """Kept and newly read entries as one list, newest first: each id once (the newer read
    wins), none older than since (an ISO time), at most keep of them."""
    by_id = {e["id"]: e for e in kept}
    by_id.update({e["id"]: e for e in fresh})
    floor = model.stamp(since)
    out = [e for e in by_id.values() if model.stamp(e["at"]) >= floor]
    out.sort(key=lambda e: model.stamp(e["at"]), reverse=True)
    return out[:keep]


def opened(prs):
    """An "opened" entry for each cached pull request, from its own creation time."""
    return [{"id": f"o:{p['repo']}:{p['id']}", "kind": "opened", "repo": p["repo"], "pr_id": p["id"],
             "pr_title": p.get("title") or "", "at": p["created_on"],
             "who": {"uuid": (p.get("author") or {}).get("uuid"), "name": (p.get("author") or {}).get("name") or "Unknown"}}
            for p in prs if p.get("created_on")]


def join(entries, prs):
    """Fill in from the cached pull requests what the log does not carry: the author (the page
    opens the pull request through them) and who closed a merged or declined one. An update
    right at the opening is dropped, since the opened entry says it."""
    by_key = {(p["repo"], p["id"]): p for p in prs}
    out = []
    for e in entries:
        pr = by_key.get((e["repo"], e["pr_id"]))
        if pr is not None:
            if e["kind"] == "updated" and abs(model.stamp(e["at"]) - model.stamp(pr.get("created_on"))) <= OPEN_SLACK:
                continue
            e = {**e, "owner": (pr.get("author") or {}).get("uuid"), "pr_state": pr.get("state")}
            closer = pr.get("closed_by")
            if e["kind"] in ("merged", "declined") and closer and closer.get("uuid"):
                e["who"] = {"uuid": closer["uuid"], "name": closer.get("name") or e["who"].get("name")}
        out.append(e)
    return out


def select(entries, people, who=None, repo=None, kind=None, since=None, until=None, limit=LIMIT):
    """The entries the filters keep, newest first. people is the uuids of you and your team:
    an entry counts when one of them did it (who narrows that to one), in repo, of kind, and
    between since and until (seconds since the epoch, either may be None)."""
    allowed = {who} if who in people else set(people)
    hits = []
    for e in entries:
        at = model.stamp(e.get("at"))
        if (e.get("who") or {}).get("uuid") not in allowed:
            continue
        if repo and e["repo"] != repo or kind and e["kind"] != kind:
            continue
        if since is not None and at < since or until is not None and at > until:
            continue
        hits.append((at, e))
    hits.sort(key=lambda h: h[0], reverse=True)
    return {"entries": [e for _, e in hits[:limit]], "total": len(hits)}


def filters(get):
    """The page's filters, checked: get(name) answers a value or None. ValueError on a wrong one."""
    out = {}
    for name in ("who", "repo", "project"):
        value = get(name)
        if value is not None and (not isinstance(value, str) or len(value) > 200):
            raise ValueError(f"{name} must be a short string")
        out[name] = value or None
    kind = get("kind")
    if kind and kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    out["kind"] = kind or None
    for name in ("since", "until"):
        value = get(name)
        if value in (None, ""):
            out[name] = None
            continue
        try:
            out[name] = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be seconds since the epoch") from None
    limit = get("limit")
    try:
        out["limit"] = max(1, min(LIMIT_MAX, int(limit))) if limit not in (None, "") else LIMIT
    except (TypeError, ValueError):
        raise ValueError("limit must be a number") from None
    return out


def route(handler, engine, method, name, query, body):
    """GET answers the feed from what is kept, never asking Bitbucket. POST first reads the
    repositories that are due, within the read's call budget, then answers the same way."""
    if name != "team-activity" or method not in ("GET", "POST"):
        return None
    if method == "GET":
        return engine.team_activity(**filters(lambda k: (query.get(k) or [None])[0]))
    body = body if isinstance(body, dict) else {}
    wanted = filters(body.get)
    error = None
    try:
        engine.read_activity(wanted["project"], force=body.get("force") is True)
    except HostError as exc:
        error = exc.message
    return {**engine.team_activity(**wanted), "error": error}

