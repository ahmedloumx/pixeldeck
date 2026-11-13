"""What changed on the pull requests PixelDeck watches, as a short log the page polls.

Each refresh compares a person's open pull requests with the ones seen before and records
what moved: a pull request opened, merged or declined, an approval, a request for changes,
new comments, new commits, a review asked of you. The page turns the log into its
notifications. Reading the log never calls Bitbucket.

Nothing is recorded on a person's first sync (there is nothing to compare with), and
nothing you did yourself: your own approval or push is not news to you.
"""

import secrets
import threading
import time

KEEP = 200        # the log is short: the page polls it every few seconds
PAGE = 50


def _who(user):
    return {"uuid": (user or {}).get("uuid"), "name": (user or {}).get("name") or "Someone"}


def _key(pr):
    return (pr.get("repo"), pr.get("id"))


def _base(pr, kind, **more):
    return {
        "kind": kind, "repo": pr.get("repo"), "pr_id": pr.get("id"), "title": pr.get("title") or "",
        "author": _who(pr.get("author")), "url": pr.get("url"), "dest": pr.get("dest"), **more,
    }


def diff(before_open, after_open, me_uuid, merged=None, declined=None):
    """Events between two lists of open pull requests of one person.

    `merged` and `declined` are that person's lists after the refresh, used to say what
    happened to a pull request that left the open list. Without them it is not recorded:
    a pull request that left for an unknown reason is not news worth a guess.
    """
    out = []
    before = {_key(p): p for p in before_open or []}
    after = {_key(p): p for p in after_open or []}
    mine = lambda pr: (pr.get("author") or {}).get("uuid") == me_uuid  # noqa: E731
    for key, pr in after.items():
        old = before.get(key)
        if old is None:
            # A pull request opened with you already on it asks for your review from the start.
            if any(r.get("uuid") == me_uuid and r.get("reviewer") for r in pr.get("reviews") or []):
                out.append(_base(pr, "review_requested", actor=_who(pr.get("author"))))
            if not mine(pr):
                out.append(_base(pr, "opened", actor=_who(pr.get("author"))))
            continue
        out.extend(_review_changes(old, pr, me_uuid))
        added = (pr.get("comment_count") or 0) - (old.get("comment_count") or 0)
        if added > 0:
            out.append(_base(pr, "comments", count=added))
        if pr.get("source_commit") and old.get("source_commit") and pr["source_commit"] != old["source_commit"] and not mine(pr):
            out.append(_base(pr, "updated", actor=_who(pr.get("author"))))
    closed = {_key(p): ("declined", p) for p in declined or []}
    closed.update({_key(p): ("merged", p) for p in merged or []})
    for key, pr in before.items():
        if key in after or key not in closed:
            continue
        kind, now = closed[key]
        by = now.get("closed_by")
        if by and by.get("uuid") == me_uuid:
            continue  # you merged or declined it: PixelDeck already showed you
        out.append(_base({**pr, **now}, kind, actor=_who(by) if by else None))
    return out


def _review_changes(old, new, me_uuid):
    out = []
    was = {r.get("uuid"): r for r in old.get("reviews") or []}
    for r in new.get("reviews") or []:
        uuid = r.get("uuid")
        prev = was.get(uuid) or {}
        if uuid == me_uuid:
            if r.get("reviewer") and not prev.get("reviewer"):
                out.append(_base(new, "review_requested", actor=_who(new.get("author"))))
            continue  # your own approval is not news to you
        state = r.get("state")
        if state != prev.get("state") and state in ("approved", "changes_requested"):
            out.append(_base(new, state, actor=_who(r)))
    return out


class Log:
    """The event log, kept in the engine's cache so ids keep counting across restarts:
    a page holding id 40 must never be handed a new event numbered 12. When the cache is
    lost the log starts over with a new epoch, and a page that sees the epoch change
    drops its cursor and history instead of mistaking new ids for old ones."""

    def __init__(self, data, save):
        self.lock = threading.Lock()
        self.data = data.setdefault("events", {"seq": 0, "items": []})
        self.data.setdefault("epoch", secrets.token_hex(6))
        self.save = save

    def record(self, owner_uuid, events):
        if not events:
            return []
        now = time.time()
        with self.lock:
            for ev in events:
                self.data["seq"] += 1
                ev.update(id=self.data["seq"], at=now, owner=owner_uuid)
                self.data["items"].append(ev)
            del self.data["items"][:-KEEP]
        self.save()
        return events

    def last(self):
        with self.lock:
            return self.data["seq"]

    def read(self, since=None, keep=lambda ev: True):
        """Events after `since`, oldest first; without `since`, the latest ones for the bell."""
        with self.lock:
            items = [ev for ev in self.data["items"] if keep(ev)]
            last, epoch = self.data["seq"], self.data["epoch"]
        if since is None:
            return {"events": items[-PAGE:], "last": last, "epoch": epoch}
        return {"events": [ev for ev in items if ev["id"] > since][:PAGE], "last": last, "epoch": epoch}
