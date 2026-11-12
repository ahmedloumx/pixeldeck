"""Turn raw Bitbucket payloads into the small shapes the page renders."""

import re

import config

REVIEW_RANK = {"changes_requested": 0, "approved": 1, "commented": 2, "pending": 3}


def _ticket_pattern():
    # Listed prefixes only when some are set; otherwise any Jira-shaped KEY-123.
    prefixes = config.ticket_prefixes()
    if prefixes:
        return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(map(re.escape, prefixes)) + r")-(\d+)(?![0-9])")
    # No list: a key needs two capitals and two digits and must stand alone, so UTF-8 or
    # the BUG-99 inside ABC-BUG-99 is not taken for a ticket. Set TICKET_PREFIXES to be exact.
    return re.compile(r"(?<![A-Za-z0-9-])([A-Z][A-Z0-9]{1,9})-(\d{2,})(?![0-9])")


def ticket_of(*texts):
    pattern = _ticket_pattern()
    site = config.jira_site()
    for text in texts:
        match = pattern.search(text or "")
        if match:
            key = f"{match.group(1)}-{match.group(2)}"
            return {"key": key, "url": f"{site}/browse/{key}" if site else None}
    return None


def _user(raw):
    raw = raw or {}
    return {
        "uuid": raw.get("uuid"),
        "name": raw.get("display_name") or raw.get("nickname") or "Unknown",
        "nickname": raw.get("nickname"),
        "account_id": raw.get("account_id"),
    }


def reviews_of(raw):
    """One entry per person with a say on the PR: reviewers first, then
    participants who approved, requested changes or commented."""
    author = ((raw.get("author") or {}).get("uuid"))
    by_uuid = {}
    for participant in raw.get("participants") or []:
        user = _user(participant.get("user"))
        if not user["uuid"] or user["uuid"] == author:
            continue
        state = participant.get("state")
        if not state:
            state = "approved" if participant.get("approved") else "commented"
        by_uuid[user["uuid"]] = {
            **user,
            "state": state,
            "reviewer": participant.get("role") == "REVIEWER",
            "at": participant.get("participated_on"),
        }
    for reviewer in raw.get("reviewers") or []:
        user = _user(reviewer)
        if not user["uuid"] or user["uuid"] == author:
            continue
        entry = by_uuid.get(user["uuid"])
        if entry is None:
            by_uuid[user["uuid"]] = {**user, "state": "pending", "reviewer": True, "at": None}
        else:
            entry["reviewer"] = True
            if entry["state"] == "commented":
                entry["state"] = "pending"
                entry["commented"] = True
    return sorted(by_uuid.values(), key=lambda r: (REVIEW_RANK.get(r["state"], 9), not r["reviewer"], r["name"]))


def slim_pr(raw):
    destination = raw.get("destination") or {}
    source = raw.get("source") or {}
    full_name = ((destination.get("repository") or {}).get("full_name")) or ""
    repo = full_name.split("/", 1)[-1]
    source_branch = ((source.get("branch") or {}).get("name")) or ""
    return {
        "repo": repo,
        "id": raw.get("id"),
        "title": raw.get("title") or "",
        "state": raw.get("state"),
        "draft": bool(raw.get("draft")),
        "author": _user(raw.get("author")),
        "source": source_branch,
        "dest": ((destination.get("branch") or {}).get("name")) or "",
        "source_commit": ((source.get("commit") or {}).get("hash")),
        "created_on": raw.get("created_on"),
        "updated_on": raw.get("updated_on"),
        "comment_count": raw.get("comment_count") or 0,
        "task_count": raw.get("task_count") or 0,
        "closed_by": _user(raw.get("closed_by")) if raw.get("closed_by") else None,
        "url": (((raw.get("links") or {}).get("html") or {}).get("href")),
        "reviews": reviews_of(raw),
        "ticket": ticket_of(source_branch, raw.get("title")),
    }


def change_key(pr):
    """Detail is refetched when any of these move."""
    return f"{pr.get('updated_on')}|{pr.get('comment_count')}|{pr.get('task_count')}"


def _message(comment):
    content = comment.get("content") or {}
    return {
        "id": comment.get("id"),
        "author": _user(comment.get("user")),
        "at": comment.get("created_on"),
        "edited": comment.get("updated_on") if comment.get("updated_on") != comment.get("created_on") else None,
        "raw": content.get("raw") or "",
        "deleted": bool(comment.get("deleted")),
    }


def build_threads(comments, pr_author_uuid):
    """Group flat comments into threads under their root comment.

    A thread is unresolved when its root carries no resolution. It awaits the
    author when it is unresolved and the last word belongs to someone else.
    General (non-inline) comments are listed but never counted as unresolved,
    because a passing "looks good" would otherwise sit in the count forever.
    """
    comments = [c for c in comments if not c.get("pending")]
    by_id = {c.get("id"): c for c in comments}
    children = {}
    roots = []
    for comment in comments:
        parent = (comment.get("parent") or {}).get("id")
        if parent in by_id:
            children.setdefault(parent, []).append(comment)
        else:
            roots.append(comment)

    def walk(comment):
        out = [comment]
        for child in sorted(children.get(comment.get("id"), []), key=lambda c: c.get("created_on") or ""):
            out.extend(walk(child))
        return out

    threads = []
    for root in sorted(roots, key=lambda c: c.get("created_on") or ""):
        messages = [_message(c) for c in walk(root)]
        visible = [m for m in messages if not m["deleted"]]
        if not visible:
            continue
        messages.sort(key=lambda m: m["at"] or "")
        inline = root.get("inline") or {}
        resolution = root.get("resolution")
        resolved = bool(resolution)
        last = visible[-1]
        is_inline = bool(inline.get("path"))
        open_inline = is_inline and not resolved
        threads.append({
            "id": root.get("id"),
            "path": inline.get("path"),
            "line": inline.get("to") or inline.get("from"),
            "side": "new" if inline.get("to") else ("old" if inline.get("from") else None),
            "outdated": bool(inline.get("outdated")),
            "inline": is_inline,
            "resolved": resolved,
            "resolved_by": _user((resolution or {}).get("user")) if resolution and resolution.get("user") else None,
            "unresolved": open_inline,
            "awaiting_author": open_inline and last["author"]["uuid"] != pr_author_uuid,
            "url": (((root.get("links") or {}).get("html") or {}).get("href")),
            "messages": [m for m in messages if not m["deleted"]],
        })
    return threads


def slim_tasks(tasks):
    out = []
    for task in tasks:
        content = task.get("content") or {}
        out.append({
            "id": task.get("id"),
            "state": task.get("state"),
            "raw": content.get("raw") or "",
            "creator": _user(task.get("creator")),
            "comment_id": (task.get("comment") or {}).get("id"),
        })
    return out


def attention_of(threads, tasks):
    return {
        "unresolved": sum(1 for t in threads if t["unresolved"]),
        "awaiting": sum(1 for t in threads if t["awaiting_author"]),
        "tasks_open": sum(1 for t in tasks if t["state"] != "RESOLVED"),
        "threads": len(threads),
    }


DIFF_LINES_MAX = 40000
FILE_LINES_MAX = 4000


def parse_diff(text):
    """Unified git diff to files, hunks and numbered lines.

    Line numbers follow the hunk headers: old numbers on removed and context
    lines, new numbers on added and context lines, as Bitbucket shows them.
    """
    files, current, hunk = [], None, None
    old_no = new_no = 0
    total = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line[len("diff --git "):].split(" b/", 1)
            old = parts[0][2:] if parts[0].startswith("a/") else parts[0]
            new = parts[1] if len(parts) > 1 else old
            current = {"old_path": old, "path": new, "status": "modified", "added": 0, "removed": 0,
                       "binary": False, "hunks": [], "truncated": False, "lines": 0}
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if hunk is None:
            if line.startswith("new file mode"):
                current["status"] = "added"
            elif line.startswith("deleted file mode"):
                current["status"] = "removed"
            elif line.startswith("rename from "):
                current["status"] = "renamed"
                current["old_path"] = line[len("rename from "):]
            elif line.startswith("rename to "):
                current["path"] = line[len("rename to "):]
            elif line.startswith("Binary files "):
                current["binary"] = True
            elif line.startswith("--- ") or line.startswith("+++ ") or line.startswith("index ") or line.startswith("similarity "):
                pass
        if line.startswith("@@"):
            try:
                ranges = line.split("@@")[1].strip().split(" ")
                old_no = int(ranges[0][1:].split(",")[0])
                new_no = int(ranges[1][1:].split(",")[0])
            except (IndexError, ValueError):
                old_no = new_no = 0
            hunk = {"header": line, "lines": []}
            current["hunks"].append(hunk)
            continue
        if hunk is None:
            continue
        if current["lines"] >= FILE_LINES_MAX or total >= DIFF_LINES_MAX:
            current["truncated"] = True
            if line[:1] == "+":
                current["added"] += 1
            elif line[:1] == "-":
                current["removed"] += 1
            continue
        kind = line[:1]
        if kind == "+":
            hunk["lines"].append(["+", None, new_no, line[1:]])
            new_no += 1
            current["added"] += 1
        elif kind == "-":
            hunk["lines"].append(["-", old_no, None, line[1:]])
            old_no += 1
            current["removed"] += 1
        elif kind == " " or line == "":
            hunk["lines"].append([" ", old_no, new_no, line[1:]])
            old_no += 1
            new_no += 1
        elif kind == "\\":
            continue  # "\ No newline at end of file"
        else:
            continue
        current["lines"] += 1
        total += 1
    return files


def timeline(events):
    """Activity entries to a newest-first list of who did what, when."""
    out = []
    for e in events:
        if isinstance(e.get("approval"), dict):
            a = e["approval"]
            out.append({"kind": "approved", "who": _user(a.get("user"))["name"], "at": a.get("date")})
        elif isinstance(e.get("changes_requested"), dict):
            c = e["changes_requested"]
            out.append({"kind": "changes_requested", "who": _user(c.get("user"))["name"], "at": c.get("date")})
        elif isinstance(e.get("comment"), dict):
            c = e["comment"]
            if c.get("deleted"):
                continue
            inline = c.get("inline") or {}
            out.append({"kind": "commented", "who": _user(c.get("user"))["name"], "at": c.get("created_on"),
                        "where": inline.get("path"), "text": ((c.get("content") or {}).get("raw") or "")[:160]})
        elif isinstance(e.get("update"), dict):
            u = e["update"]
            out.append({"kind": "updated", "who": _user(u.get("author"))["name"], "at": u.get("date"),
                        "state": u.get("state")})
    out.sort(key=lambda x: x.get("at") or "", reverse=True)
    return out
