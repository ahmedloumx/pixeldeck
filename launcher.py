"""Open an interactive Claude Code session on a pull request.

The brief is built here from cached Bitbucket data and the templates in briefs/,
never from text sent by the browser. On Linux and macOS it reaches Claude as one
argv element, so no shell ever sees reviewer text and a backtick in a comment
stays a backtick. Windows caps a whole command line at 32,767 characters, so
there the brief goes to a file and Claude is pointed at it.
"""

import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import config
import gitscan

BRIEFS = config.APP_DIR / "briefs"
WINDOWS = sys.platform == "win32"

ACTIONS = {
    "summarize": "Read and summarize",
    "fix": "Fix",
    "fix_push": "Fix and push",
    "review": "Review with the rubric",
}
OWN_ONLY = {"fix", "fix_push"}
MAX_BRIEF_BYTES = 120_000  # Linux caps one argv element at 128 KiB


def _fence(text):
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _quote(text):
    fence = _fence(text)
    return f"{fence}text\n{text}\n{fence}"


def _review_line(review):
    label = {
        "approved": "approved",
        "changes_requested": "requested changes",
        "pending": "has not reviewed yet",
        "commented": "commented",
    }.get(review["state"], review["state"])
    role = "reviewer" if review.get("reviewer") else "participant"
    return f"- {review['name']} ({role}): {label}"


def _thread_block(index, thread):
    where = f"{thread['path']}:{thread['line']}" if thread.get("path") else "general comment"
    flags = []
    if thread["resolved"]:
        flags.append("resolved")
    elif thread["inline"]:
        flags.append("unresolved")
    if thread.get("outdated"):
        flags.append("outdated (the line moved since)")
    if thread.get("awaiting_author"):
        flags.append("waiting on the PR author")
    lines = [f"### Thread {index}: {where} [{', '.join(flags) or 'open'}]"]
    if thread.get("side") == "new":
        lines.append("Line number is on the new side of the diff.")
    for message in thread["messages"]:
        lines.append(f"{message['author']['name']} at {message['at']}:")
        lines.append(_quote(message["raw"]))
    return "\n".join(lines)


def template(name):
    """Your own copy from PIXELDECK_BRIEFS_DIR when it has one, else the one in briefs/."""
    own = config.briefs_dir()
    for folder in ([own] if own else []) + [BRIEFS]:
        path = folder / name
        if path.is_file():
            return path.read_text(encoding="utf-8").rstrip("\n")
    raise RuntimeError(f"brief template {name} is missing")


def render(text, values):
    # One pass over {name} only: a value that holds braces (a PR title) is never read again.
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), text)


def build_brief(action, pr, detail, cwd, is_mine):
    threads = detail.get("threads", [])
    tasks = detail.get("tasks", [])
    open_threads = [t for t in threads if not t["resolved"]]
    resolved = [t for t in threads if t["resolved"]]
    ticket = (pr.get("ticket") or {}).get("key")
    values = {
        "id": str(pr["id"]), "workspace": config.workspace(), "repo": pr["repo"], "title": pr["title"],
        "url": pr["url"] or "", "source": pr["source"], "dest": pr["dest"], "author": pr["author"]["name"],
        "me": " (me)" if is_mine else "", "ticket": ticket or "none found in the branch or title",
        "cwd": str(cwd), "reviews": "\n".join([_review_line(r) for r in pr.get("reviews", [])] or ["- nobody has reviewed yet"]),
        "ticket_subjects": f" with {ticket} in every subject." if ticket else ".",
    }
    head = render(template("head.md"), values)
    task = render(template(f"{action}.md"), values)

    body = []
    for index, thread in enumerate(open_threads, 1):
        body.append(_thread_block(index, thread))
    open_tasks = [t for t in tasks if t["state"] != "RESOLVED"]
    if open_tasks:
        body.append("## Open Bitbucket tasks")
        for task_item in open_tasks:
            body.append(f"- by {task_item['creator']['name']}:")
            body.append(_quote(task_item["raw"]))
    if resolved:
        body.append(f"({len(resolved)} resolved thread(s) left out.)")
    if not open_threads:
        body.insert(0, "There are no open review threads right now.")

    brief = head + "\n\n" + task + "\n\n## Open threads\n\n" + "\n\n".join(body)
    encoded = brief.encode()
    if len(encoded) > MAX_BRIEF_BYTES:
        note = f"\n\n[Brief cut to fit the terminal. Open {pr['url']} for the rest.]"
        keep = MAX_BRIEF_BYTES - len(note.encode())
        brief = encoded[:keep].decode("utf-8", "ignore") + note
    return brief


def _worktree_for(repo_dir, branch):
    listing = gitscan.git(repo_dir, "worktree", "list", "--porcelain")
    path = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}" and path:
            return Path(path)
    return None


def ensure_worktree(root, repo_dir, slug, pr):
    """Reuse the checkout that already has the branch, or add one under worktrees/pr/."""
    branch = pr["source"]
    gitscan.git(repo_dir, "fetch", "origin", branch, timeout=90)
    existing = _worktree_for(repo_dir, branch)
    if existing:
        return existing, "reused"
    target = Path(root) / "worktrees" / "pr" / f"{slug}-{pr['id']}"
    if target.exists():
        raise RuntimeError(f"{target} exists but is not on {branch}; clean it up first")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        gitscan.git(repo_dir, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        gitscan.git(repo_dir, "worktree", "add", str(target), branch, timeout=120)
    except RuntimeError:
        gitscan.git(repo_dir, "worktree", "add", "--track", "-b", branch, str(target), f"origin/{branch}", timeout=120)
    return target, "created"


def claude_binary():
    named = config.get("PIXELDECK_CLAUDE")
    home = Path.home() / ".local" / "bin"
    for candidate in (named, shutil.which("claude"), home / ("claude.exe" if WINDOWS else "claude")):
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise RuntimeError("claude CLI not found: install Claude Code, or set PIXELDECK_CLAUDE to its path")


def brief_argv(brief, name, by_file=WINDOWS):
    """The argv that starts Claude on this brief, by argument or by file."""
    claude = claude_binary()
    if not by_file:
        return [claude, brief]
    folder = config.folder("state") / "briefs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{re.sub(r'[^A-Za-z0-9_.-]+', '-', name)}.md"
    path.write_text(brief, encoding="utf-8")
    return [claude, f"Your brief is in {path}. Read all of it first, then follow it."]


def open_terminal(cwd, title, argv):
    """A terminal window of the system's own, for Shift-click."""
    cwd = str(cwd)
    if sys.platform == "darwin":
        line = f"cd {shlex.quote(cwd)} && {shlex.join(argv)}"
        script = 'tell application "Terminal" to do script "{}"'.format(line.replace("\\", "\\\\").replace('"', '\\"'))
        command = ["osascript", "-e", script, "-e", 'tell application "Terminal" to activate']
    elif WINDOWS:
        if shutil.which("wt"):
            command = ["wt", "-w", "new", "--title", title, "-d", cwd, *argv]
        else:
            command = ["cmd", "/c", "start", title, "/D", cwd, *argv]
    else:
        linux = [
            ("ptyxis", lambda: ["ptyxis", "--new-window", "-d", cwd, "-T", title, "--", *argv]),
            ("gnome-terminal", lambda: ["gnome-terminal", f"--working-directory={cwd}", f"--title={title}", "--", *argv]),
            ("konsole", lambda: ["konsole", "--workdir", cwd, "-e", *argv]),
            ("xfce4-terminal", lambda: ["xfce4-terminal", f"--working-directory={cwd}", "-T", title, "-x", *argv]),
            ("x-terminal-emulator", lambda: ["x-terminal-emulator", "-e", *argv]),
        ]
        found = next((make for name, make in linux if shutil.which(name)), None)
        if not found:
            raise RuntimeError("No terminal found (ptyxis, gnome-terminal, konsole, xfce4-terminal)")
        command = found()
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}
    subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **flags)


def launch(root, action, pr, detail, is_mine, terms=None):
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    if action in OWN_ONLY and not is_mine:
        raise PermissionError("Fix actions are only offered on your own pull requests")
    repos = gitscan.repo_dirs(root)
    repo_dir = repos.get(pr["repo"])
    if repo_dir is None:
        raise RuntimeError(f"No local clone of {pr['repo']} under {root}")
    if action in OWN_ONLY:
        cwd, how = ensure_worktree(root, repo_dir, pr["repo"], pr)
    else:
        cwd, how = repo_dir, "repo"
    brief = build_brief(action, pr, detail, cwd, is_mine)
    title = f"{ACTIONS[action]} · {pr['repo']} #{pr['id']}"
    record = {"cwd": str(cwd), "checkout": how, "title": title, "at": time.time(), "chars": len(brief)}
    # macOS hands an outside terminal its command through AppleScript, so a long brief goes by file there too.
    by_file = WINDOWS or (terms is None and sys.platform == "darwin")
    argv = brief_argv(brief, f"{pr['repo']}-{pr['id']}-{action}", by_file)
    if terms is not None:
        record["term"] = terms.spawn(title, argv, cwd).id
    else:
        open_terminal(cwd, title, argv)
    return record
