"""Read the local clones under the workspace root.

Only remote-tracking refs are read, so nothing here touches the network:
the "pushed, no PR" list is as fresh as the last fetch in each clone.
"""

import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import config

SKIP_BRANCHES = {"HEAD", "develop", "main", "master"}
_SCP = re.compile(r"(?:[\w.+-]+@)?([A-Za-z0-9.-]+):(?!//)(.+)")
_REPO_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*){1,20}")


# Never wait on a prompt: the server has no terminal, so a missing key must fail, not hang.
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")}
# On Windows the server runs without a console, so each console program it runs (git, a
# --version check) would open a console window of its own. 0 elsewhere.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(repo_dir, *args, timeout=20):
    """git with its exit code kept, for a command whose code is part of the answer (merge-tree)."""
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True, text=True, timeout=timeout, env=GIT_ENV, creationflags=NO_WINDOW,
    )


def git(repo_dir, *args, timeout=20):
    result = run(repo_dir, *args, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _web_host():
    """The host name a clone of the code host in use points at (GitHub or GitLab)."""
    if config.code_host() == "github":
        api = urlsplit(config.get("GITHUB_URL") or "https://api.github.com").hostname or ""
        return "github.com" if api == "api.github.com" else api.lower()
    return (urlsplit(config.get("GITLAB_URL") or "https://gitlab.com").hostname or "").lower()


def remote_repo(url):
    """The repository an origin URL names on GitHub (owner/name) or GitLab (group/project),
    in https, ssh:// or scp-like (git@host:path) form, or None for another server."""
    parts = urlsplit(url)
    if parts.scheme and parts.hostname:
        host, path = parts.hostname.lower(), parts.path
    else:
        match = _SCP.fullmatch(url)
        if not match:
            return None
        host, path = match.group(1).lower(), match.group(2)
    path = path.strip("/").removesuffix(".git")
    if host != _web_host() or not _REPO_PATH.fullmatch(path):
        return None
    return path if config.code_host() == "gitlab" or path.count("/") == 1 else None


def repo_dirs(root):
    """Map repo slug -> main clone directory (a .git directory, not a worktree). The slug is
    the code host's own name for it: a Bitbucket slug, owner/name on GitHub, a GitLab path."""
    found = {}
    for entry in sorted(Path(root).iterdir()):
        if not (entry / ".git").is_dir():
            continue
        try:
            url = git(entry, "remote", "get-url", "origin").strip()
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        if config.code_host() != "bitbucket":
            slug = remote_repo(url)
            # A folder named like the repository wins over a stray copy.
            if slug and (slug not in found or entry.name == slug.rsplit("/", 1)[-1]):
                found[slug] = entry
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
