"""Read the local clones under the workspace root.

Only remote-tracking refs are read, so nothing here touches the network:
the "pushed, no PR" list is as fresh as the last fetch in each clone.
"""

import os
import subprocess
import time
from pathlib import Path

import config

SKIP_BRANCHES = {"HEAD", "develop", "main", "master"}


# Never wait on a prompt: the server has no terminal, so a missing key must fail, not hang.
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")}


def git(repo_dir, *args, timeout=20):
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True, text=True, timeout=timeout, env=GIT_ENV,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def repo_dirs(root):
    """Map repo slug -> main clone directory (a .git directory, not a worktree)."""
    found = {}
    for entry in sorted(Path(root).iterdir()):
        if not (entry / ".git").is_dir():
            continue
        try:
            url = git(entry, "remote", "get-url", "origin").strip()
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        workspace = config.workspace()
        if not workspace:
            return found
        marker = f"bitbucket.org:{workspace}/"
        alt = f"bitbucket.org/{workspace}/"
        for prefix in (marker, alt):
            if prefix in url:
                slug = url.split(prefix, 1)[1].removesuffix(".git")
                # A folder named exactly like the slug wins over a stray copy.
                if slug not in found or entry.name == slug:
                    found[slug] = entry
    return found


def default_dest(repo_dir, slug):
    if config.is_release_repo(slug):
        candidates = ("master", "main")
    else:
        candidates = ("develop", "main", "master")
    for name in candidates:
        try:
            git(repo_dir, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}")
            return name
        except RuntimeError:
            continue
    return candidates[0]


def my_email(root):
    try:
        return git(root, "config", "user.email").strip().lower()
    except RuntimeError:
        return ""


def pushed_without_pr(root, email, pr_branches, days=21):
    """Remote branches whose tip I authored recently and that no PR covers."""
    since = time.time() - days * 86400
    out = []
    for slug, repo_dir in repo_dirs(root).items():
        try:
            refs = git(repo_dir, "for-each-ref", "refs/remotes/origin",
                       "--format=%(refname:lstrip=3)%09%(authoremail)%09%(committerdate:unix)%09%(objectname:short)%09%(subject)")
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        dest = None
        for line in refs.splitlines():
            parts = line.split("\t", 4)
            if len(parts) < 5:
                continue
            branch, author, when, sha, subject = parts
            if branch in SKIP_BRANCHES or (slug, branch) in pr_branches:
                continue
            if author.strip("<>").lower() != email or int(when or 0) < since:
                continue
            dest = dest or default_dest(repo_dir, slug)
            try:
                git(repo_dir, "merge-base", "--is-ancestor", f"origin/{branch}", f"origin/{dest}")
                continue  # already contained in the destination
            except RuntimeError:
                pass
            except subprocess.TimeoutExpired:
                continue
            out.append({
                "repo": slug,
                "branch": branch,
                "dest": dest,
                "sha": sha,
                "subject": subject,
                "pushed_at": int(when),
                "create_url": f"https://bitbucket.org/{config.workspace()}/{slug}/pull-requests/new?source={branch}&dest={dest}",
            })
    out.sort(key=lambda b: -b["pushed_at"])
    return out


def recent_author_names(root, days=90):
    """Display names that committed to a destination branch lately, lowercased."""
    names = set()
    for slug, repo_dir in repo_dirs(root).items():
        dest = default_dest(repo_dir, slug)
        try:
            log = git(repo_dir, "log", f"origin/{dest}", f"--since={days}.days", "--format=%an")
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        names.update(n.strip().lower() for n in log.splitlines() if n.strip())
    return names
