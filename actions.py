"""Write actions: preview and open a pull request from a pushed branch, delete a
pushed branch, sync, merge or decline one of your open pull requests.

The browser only names what to act on. Every action is checked against the
server's own data first (the pushed list, the cached pull request, the remote
tip), so a stale page cannot delete or decline the wrong thing.
"""

import json
import secrets
import shutil
import subprocess
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


def remote_tip(root, repo, branch):
    """The branch's commit on the remote right now, after a fetch."""
    repo_dir = _repo_dir(root, repo)
    gitscan.git(repo_dir, "fetch", "--quiet", "origin", branch, timeout=90)
    return gitscan.git(repo_dir, "rev-parse", f"origin/{branch}").strip()


DIFF_MAX = 400_000  # bytes of diff text read for the popup; a larger diff is cut and says so


def branch_diff(root, repo, branch, dest):
    """The diff a pull request from branch into dest would show, parsed, for the popup."""
    repo_dir = _repo_dir(root, repo)
    for ref in (branch, dest):
        if not isinstance(ref, str) or not ref or ref.startswith("-") or not _ref_exists(repo_dir, f"refs/remotes/origin/{ref}"):
            raise ValueError(f"unknown branch {ref!r}")
    text = gitscan.git(repo_dir, "diff", "--no-color", "--no-ext-diff", f"origin/{dest}...origin/{branch}", timeout=60)
    cut = len(text) > DIFF_MAX
    if cut:
        text = text[:DIFF_MAX].rsplit("\ndiff --git ", 1)[0]
    return {"files": model.parse_diff(text), "cut": cut}


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


def merge(engine, body):
    """Merge one of your own open pull requests, with its destination's default strategy.

    The pull request is read fresh first: the cache can be minutes old, and the destination
    branch says which strategies it allows. Bitbucket still runs its own merge checks
    (approvals, builds, tasks) and its refusal comes back as the error.
    """
    repo, pr_id = body.get("repo"), body.get("id")
    if not isinstance(repo, str) or not isinstance(pr_id, int):
        raise ValueError("repo and id are required")
    pr = engine.find_pr(repo, pr_id)
    me = (engine.me() or {}).get("uuid")
    if pr is None:
        raise ValueError("unknown pull request; refresh first")
    if pr["author"]["uuid"] != me:
        raise PermissionError("only your own pull requests can be merged from here")
    base = f"/repositories/{engine.client.workspace}/{repo}/pullrequests/{pr_id}"
    live = engine.client.get(base)
    if live.get("state") != "OPEN":
        raise ValueError(f"#{pr_id} is {str(live.get('state', 'unknown')).lower()} on Bitbucket, not open")
    branch = (live.get("destination") or {}).get("branch") or {}
    strategy = branch.get("default_merge_strategy")
    payload = {"type": "pullrequest"}
    if strategy:
        payload["merge_strategy"] = strategy
    result = engine.client.send("POST", f"{base}/merge", payload)
    merged = None
    if result.get("state") == "MERGED":
        merged = engine.file_merged(me, result)
    _after_change(engine)
    # A slow merge answers 202 with no body: Bitbucket finishes it, and the next refresh files it.
    return {"repo": repo, "id": pr_id, "state": result.get("state") or "PENDING", "strategy": strategy, "pr": merged}


class SyncConflict(Exception):
    """The destination does not merge cleanly into the branch; nothing was pushed."""

    def __init__(self, dest, files):
        super().__init__(f"{dest} does not merge cleanly: {len(files)} file(s) conflict")
        self.files = files


_clones = {"at": 0, "root": None, "map": {}}


def _clone(root, repo):
    """The local clone of a repository; the scan of the workspace is kept ten minutes."""
    if _clones["root"] != str(root) or time.time() - _clones["at"] > 600:
        _clones.update(at=time.time(), root=str(root), map=gitscan.repo_dirs(root))
    return _clones["map"].get(repo)


def _own_open(engine, repo, pr_id):
    if not isinstance(repo, str) or not isinstance(pr_id, int):
        raise ValueError("repo and id are required")
    pr = engine.find_pr(repo, pr_id)
    if pr is None:
        raise ValueError("unknown pull request; refresh first")
    if pr["author"]["uuid"] != (engine.me() or {}).get("uuid"):
        raise PermissionError("only your own pull requests can be synced from here")
    if pr["state"] != "OPEN":
        raise ValueError(f"#{pr_id} is {pr['state'].lower()}, not open")
    return pr


def _counts(repo_dir, pr):
    src, dest = pr["source"], pr["dest"]
    gitscan.git(repo_dir, "fetch", "--quiet", "origin", src, dest, timeout=120)
    behind = int(gitscan.git(repo_dir, "rev-list", "--count", f"origin/{src}..origin/{dest}").strip())
    ahead = int(gitscan.git(repo_dir, "rev-list", "--count", f"origin/{dest}..origin/{src}").strip())
    return behind, ahead


def sync_status(engine, root, repo, pr_id):
    """How far the branch is behind its destination, read from a fresh fetch of the clone."""
    pr = _own_open(engine, repo, pr_id)
    repo_dir = _clone(root, repo)
    if repo_dir is None:
        return {"available": False, "why": f"no local clone of {repo} under {root}"}
    behind, ahead = _counts(repo_dir, pr)
    return {"available": True, "behind": behind, "ahead": ahead, "source": pr["source"], "dest": pr["dest"]}


def sync(engine, root, body):
    """Merge the destination into the pull request's branch and push it, as Bitbucket's
    "Sync now" does.

    The merge happens in a throwaway worktree on the remote tip, so no checkout of yours
    changes. The push is a plain one: if someone pushed to the branch meanwhile, git
    refuses it and nothing is lost. A conflict aborts the merge and pushes nothing. Hooks
    are skipped (--no-verify) because this commit only joins two pushed histories, the
    way Bitbucket's own button does on its servers, and a temporary worktree has no
    installed tooling for them to run.
    """
    pr = _own_open(engine, body.get("repo"), body.get("id"))
    repo_dir = _clone(root, pr["repo"])
    if repo_dir is None:
        raise ValueError(f"no local clone of {pr['repo']} under {root}")
    src, dest = pr["source"], pr["dest"]
    behind, _ = _counts(repo_dir, pr)
    if behind == 0:
        return {"state": "up to date", "behind": 0, "merged": 0}
    tip = gitscan.git(repo_dir, "rev-parse", f"origin/{src}").strip()
    folder = config.folder("state") / "sync"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{pr['repo']}-{pr['id']}-{secrets.token_hex(3)}"
    gitscan.git(repo_dir, "worktree", "add", "--detach", str(path), tip, timeout=120)
    try:
        try:
            gitscan.git(path, "merge", "--no-ff", "--no-verify", "-m", f"Merged {dest} into {src}", f"origin/{dest}", timeout=180)
        except (RuntimeError, subprocess.TimeoutExpired):
            conflicts = gitscan.git(path, "diff", "--name-only", "--diff-filter=U").split()
            try:
                gitscan.git(path, "merge", "--abort")
            except RuntimeError:
                pass
            raise SyncConflict(dest, conflicts) from None
        head = gitscan.git(path, "rev-parse", "HEAD").strip()
        gitscan.git(path, "push", "--no-verify", "origin", f"{head}:refs/heads/{src}", timeout=180)
    finally:
        try:
            gitscan.git(repo_dir, "worktree", "remove", "--force", str(path), timeout=60)
        except (RuntimeError, subprocess.TimeoutExpired):
            shutil.rmtree(path, ignore_errors=True)
            try:
                gitscan.git(repo_dir, "worktree", "prune")
            except RuntimeError:
                pass
    _after_change(engine)
    return {"state": "synced", "behind": 0, "merged": behind, "head": head[:12], "source": src, "dest": dest}


def _after_change(engine):
    me = (engine.me() or {}).get("uuid")
    if me:
        try:
            engine.refresh_person(me)
        except Exception:  # noqa: BLE001 - the change happened; the next tick catches up
            pass
    engine.pushed(force=True)
