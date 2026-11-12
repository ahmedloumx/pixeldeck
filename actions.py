"""Write actions: preview and open a pull request from a pushed branch, delete a
pushed branch, decline one of your open pull requests.

The browser only names what to act on. Every action is checked against the
server's own data first (the pushed list, the cached pull request, the remote
tip), so a stale page cannot delete or decline the wrong thing.
"""

import json
import time

import config
import gitscan
import model

STATE_DIR = config.folder("state")
DELETED_LOG = STATE_DIR / "deleted-branches.jsonl"
TITLE_MAX = 255
DESCRIPTION_MAX = 20000
PROTECTED = {"develop", "main", "master", "HEAD"}
_reviewer_cache = {}


def _pushed(engine, repo, branch):
    for force in (False, True):
        for item in engine.pushed(force=force):
            if item["repo"] == repo and item["branch"] == branch:
                return item
    raise ValueError("that branch is not in your pushed-without-a-pull-request list; refresh and try again")


def _repo_dir(root, repo):
    repo_dir = gitscan.repo_dirs(root).get(repo)
    if repo_dir is None:
        raise ValueError(f"no local clone of {repo}")
    return repo_dir


def _ref_exists(repo_dir, ref):
    try:
        gitscan.git(repo_dir, "rev-parse", "--verify", "--quiet", ref)
        return True
    except RuntimeError:
        return False


def default_reviewers(engine, repo):
    """Project and repository default reviewers, minus you (an author cannot review)."""
    hit = _reviewer_cache.get(repo)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    me = (engine.me() or {}).get("uuid")
    people = []
    try:
        for row in engine.client.paged(f"/repositories/{engine.client.workspace}/{repo}/effective-default-reviewers",
                                       {"pagelen": 100}, max_pages=3):
            user = row.get("user") or {}
            if user.get("uuid") and user["uuid"] != me:
                people.append({"uuid": user["uuid"], "name": user.get("display_name")})
    except Exception:  # noqa: BLE001 - reviewers are a convenience, not a blocker
        people = []
    _reviewer_cache[repo] = (time.time(), people)
    return people


def preview(engine, root, repo, branch):
    item = _pushed(engine, repo, branch)
    repo_dir = _repo_dir(root, repo)
    dests = [d for d in ("develop", "main", "master") if _ref_exists(repo_dir, f"refs/remotes/origin/{d}")]
    dest = item["dest"] if item["dest"] in dests else (dests[0] if dests else item["dest"])
    gitscan.git(repo_dir, "fetch", "origin", branch, dest, timeout=60)
    rng = f"origin/{dest}..origin/{branch}"
    log = gitscan.git(repo_dir, "log", "--reverse", "-n", "80", "--format=%h%x1f%s%x1f%an%x1f%cI", rng)
    commits = []
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({"sha": parts[0], "subject": parts[1], "author": parts[2], "at": parts[3]})
    ahead = int(gitscan.git(repo_dir, "rev-list", "--count", rng).strip() or 0)
    behind = int(gitscan.git(repo_dir, "rev-list", "--count", f"origin/{branch}..origin/{dest}").strip() or 0)
    files, added, removed = [], 0, 0
    for line in gitscan.git(repo_dir, "diff", "--numstat", f"origin/{dest}...origin/{branch}").splitlines():
        a, d, path = (line.split("\t", 2) + ["", "", ""])[:3]
        a_n = int(a) if a.isdigit() else 0
        d_n = int(d) if d.isdigit() else 0
        added += a_n
        removed += d_n
        files.append({"path": path, "added": a_n, "removed": d_n, "binary": not a.isdigit()})
    files.sort(key=lambda f: -(f["added"] + f["removed"]))
    tip = gitscan.git(repo_dir, "rev-parse", f"origin/{branch}").strip()
    ticket = model.ticket_of(branch)
    title = commits[0]["subject"] if commits else branch
    if ticket and ticket["key"] not in title:
        title = f"{title} ({ticket['key']})"
    description = "\n".join(f"- {c['subject']}" for c in commits)
    return {
        "repo": repo, "branch": branch, "dest": dest, "dests": dests, "tip": tip,
        "ahead": ahead, "behind": behind, "commits": commits,
        "files": files[:60], "file_count": len(files), "added": added, "removed": removed,
        "title": title[:TITLE_MAX], "description": description, "ticket": ticket,
        "reviewers": default_reviewers(engine, repo),
        "close_source_branch": True,
        "bitbucket_url": item["create_url"],
    }


def create(engine, root, body):
    repo, branch = body.get("repo"), body.get("branch")
    if not isinstance(repo, str) or not isinstance(branch, str):
        raise ValueError("repo and branch are required")
    item = _pushed(engine, repo, branch)
    repo_dir = _repo_dir(root, repo)
    dest = body.get("dest") or item["dest"]
    if not isinstance(dest, str) or not _ref_exists(repo_dir, f"refs/remotes/origin/{dest}") or dest == branch:
        raise ValueError("pick a destination branch that exists")
    title = (body.get("title") or "").strip()
    description = body.get("description") or ""
    if not title or len(title) > TITLE_MAX:
        raise ValueError(f"the title must be 1 to {TITLE_MAX} characters")
    if not isinstance(description, str) or len(description) > DESCRIPTION_MAX:
        raise ValueError(f"the description must be at most {DESCRIPTION_MAX} characters")
    reviewers = default_reviewers(engine, repo)
    payload = {
        "title": title,
        "description": description,
        "source": {"branch": {"name": branch}},
        "destination": {"branch": {"name": dest}},
        "close_source_branch": bool(body.get("close_source_branch", True)),
        "reviewers": [{"uuid": r["uuid"]} for r in reviewers],
    }
    pr = engine.client.send("POST", f"/repositories/{engine.client.workspace}/{repo}/pullrequests", payload)
    _after_change(engine)
    return {"id": pr.get("id"), "url": ((pr.get("links") or {}).get("html") or {}).get("href"),
            "reviewers": len(reviewers)}


def delete_branch(engine, root, body):
    repo, branch, expected = body.get("repo"), body.get("branch"), body.get("sha")
    if not all(isinstance(v, str) and v for v in (repo, branch, expected)):
        raise ValueError("repo, branch and sha are required")
    if branch in PROTECTED:
        raise PermissionError("protected branches are never deleted from here")
    _pushed(engine, repo, branch)
    repo_dir = _repo_dir(root, repo)
    gitscan.git(repo_dir, "fetch", "origin", branch, timeout=60)
    tip = gitscan.git(repo_dir, "rev-parse", f"origin/{branch}").strip()
    if not tip.startswith(expected):
        raise ValueError(f"{branch} moved to {tip[:8]} since you looked; refresh before deleting")
    # The lease makes the push fail if anyone moved the branch in the meantime.
    gitscan.git(repo_dir, "push", f"--force-with-lease=refs/heads/{branch}:{tip}", "origin", f":refs/heads/{branch}", timeout=90)
    record = {"at": time.time(), "repo": repo, "branch": branch, "sha": tip,
              "restore": f"git push origin {tip}:refs/heads/{branch}"}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(DELETED_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    engine.pushed(force=True)
    return record


def decline(engine, body):
    repo, pr_id = body.get("repo"), body.get("id")
    if not isinstance(repo, str) or not isinstance(pr_id, int):
        raise ValueError("repo and id are required")
    pr = engine.find_pr(repo, pr_id)
    me = (engine.me() or {}).get("uuid")
    if pr is None:
        raise ValueError("unknown pull request; refresh first")
    if pr["author"]["uuid"] != me:
        raise PermissionError("only your own pull requests can be declined from here")
    if pr["state"] != "OPEN":
        raise ValueError(f"#{pr_id} is {pr['state'].lower()}, not open")
    engine.client.send("POST", f"/repositories/{engine.client.workspace}/{repo}/pullrequests/{pr_id}/decline", {})
    _after_change(engine)
    return {"repo": repo, "id": pr_id}


def _after_change(engine):
    me = (engine.me() or {}).get("uuid")
    if me:
        try:
            engine.refresh_person(me)
        except Exception:  # noqa: BLE001 - the change happened; the next tick catches up
            pass
    engine.pushed(force=True)
