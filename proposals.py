"""Pull requests an agent asks to open, waiting for you to decide in PixelDeck.

An agent (Claude Code in a terminal) runs `pixeldeck.py --propose ...` instead of opening
the pull request itself. The proposal shows up in PixelDeck as a notification: Proceed opens
it as proposed, Review opens the usual popup (target branch, close the branch after the
merge, the diff), Decline tells the agent no.

This is a cooperative step, not a security boundary: the propose key only lets a caller ask
and read the answer, never approve, but any program running as you can reach PixelDeck and
your token anyway. What it does guarantee is that the pull request opened is the one you
saw: approval is refused if the branch moved since it was proposed.
"""

import secrets
import threading
import time

import actions

PENDING_MAX = 10
KEEP_DECIDED = 3600  # a late answer still reaches the agent's next call for an hour


class Proposals:
    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()

    # ---- reading -----------------------------------------------------------

    @staticmethod
    def public(item):
        return {k: v for k, v in item.items() if k != "lock"}

    def _prune(self):
        now = time.time()
        for pid in [p for p, it in self.items.items() if it["status"] not in ("pending", "opening")
                    and now - (it["decided_at"] or now) > KEEP_DECIDED]:
            del self.items[pid]

    def get(self, pid):
        with self.lock:
            item = self.items.get(pid)
            if item is None:
                raise KeyError("no such proposal; PixelDeck may have restarted, so propose it again")
            return self.public(item)

    def list(self):
        with self.lock:
            self._prune()
            return [self.public(it) for it in sorted(self.items.values(), key=lambda it: it["created_at"])]

    # ---- asking ------------------------------------------------------------

    def propose(self, engine, root, body):
        repo, branch = body.get("repo"), body.get("branch")
        if not isinstance(repo, str) or not isinstance(branch, str) or not repo or not branch:
            raise ValueError("repo and branch are required")
        with self.lock:
            self._prune()
            # Asking again for the same branch returns the same proposal, pending or decided.
            for it in self.items.values():
                if it["repo"] == repo and it["branch"] == branch:
                    return self.public(it)
            if sum(it["status"] == "pending" for it in self.items.values()) >= PENDING_MAX:
                raise ValueError(f"{PENDING_MAX} proposals are already waiting; decide those first")
        preview = actions.preview(engine, root, repo, branch)  # also checks it is your pushed branch, with no pull request
        dest = body.get("dest") or preview["dest"]
        if dest not in preview["dests"]:
            raise ValueError(f"{dest!r} is not a branch {repo} can merge into ({', '.join(preview['dests'])})")
        title = body.get("title") if isinstance(body.get("title"), str) and body.get("title").strip() else preview["title"]
        description = body.get("description") if isinstance(body.get("description"), str) else preview["description"]
        if len(title.strip()) > actions.TITLE_MAX:
            raise ValueError(f"the title must be at most {actions.TITLE_MAX} characters")
        if len(description) > actions.DESCRIPTION_MAX:
            raise ValueError(f"the description must be at most {actions.DESCRIPTION_MAX} characters")
        item = {
            "id": secrets.token_hex(6), "status": "pending", "created_at": time.time(), "decided_at": None,
            "repo": repo, "branch": branch, "dest": dest, "title": title.strip(), "description": description,
            "close_source_branch": bool(body.get("close_source_branch", True)),
            "tip": preview["tip"], "ahead": preview["ahead"], "behind": preview["behind"],
            "file_count": preview["file_count"], "added": preview["added"], "removed": preview["removed"],
            "result": None, "reason": None,
        }
        with self.lock:
            for it in self.items.values():  # another call may have won the race
                if it["repo"] == repo and it["branch"] == branch:
                    return self.public(it)
            self.items[item["id"]] = item
        return self.public(item)

    # ---- deciding (the page only) -------------------------------------------

    def decide(self, engine, root, pid, action, fields=None):
        fields = fields or {}
        with self.lock:
            item = self.items.get(pid)
            if item is None:
                raise KeyError("no such proposal")
            if item["status"] != "pending":
                return self.public(item)  # already decided: a second click changes nothing
            if action == "decline":
                item.update(status="declined", decided_at=time.time(), reason=str(fields.get("reason") or "declined in PixelDeck")[:200])
                return self.public(item)
            if action != "approve":
                raise ValueError("action must be approve or decline")
            item["status"] = "opening"  # a double click cannot open it twice
        try:
            if actions.remote_tip(root, item["repo"], item["branch"]) != item["tip"]:
                raise ValueError("the branch moved since it was proposed; ask for it again so you see what is opened")
            body = {
                "repo": item["repo"], "branch": item["branch"],
                "dest": fields.get("dest") or item["dest"],
                "title": fields["title"] if isinstance(fields.get("title"), str) else item["title"],
                "description": fields["description"] if isinstance(fields.get("description"), str) else item["description"],
                "close_source_branch": bool(fields.get("close_source_branch", item["close_source_branch"])),
            }
            result = actions.create(engine, root, body)
        except Exception:
            with self.lock:
                item["status"] = "pending"
            raise
        with self.lock:
            item.update(status="opened", decided_at=time.time(), result=result, dest=body["dest"], title=body["title"])
            return self.public(item)

    def opened_elsewhere(self, repo, branch, result):
        """A pull request opened for the branch some other way answers its proposal too."""
        with self.lock:
            for item in self.items.values():
                if item["repo"] == repo and item["branch"] == branch and item["status"] == "pending":
                    item.update(status="opened", decided_at=time.time(), result=result)
