"""Cache, refresh cadence and team selection.

Everything the page shows is served from the on-disk cache, so the page is
instant and survives restarts. A background loop keeps the cache warm within
the hourly call budget: my own pull requests every 5 minutes, each teammate
every 15, comment threads only when a PR's update signature moves.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import events
import gitscan
import model
from bitbucket import HOURLY_LIMIT, BitbucketError, Client

ME_EVERY = 300
OPEN_EVERY = 60  # your open pull requests alone: one call, so an approval shows within a minute or two
TEAM_EVERY = 900
COMMENT_LOOKUPS = 5  # pull requests whose new comments are checked for authors, per refresh
TEAM_OPEN_EVERY = 300  # a teammate's open list alone, one call: team news within minutes, not a quarter hour
DETAIL_MAX_AGE = 900
MERGED_WINDOW_DAYS = 120
FULL_WALK_EVERY = 6 * 3600
HISTORY_WINDOW_DAYS = 30
PROJECT_LIST_MAX_AGE = 24 * 3600
DECLINED_WINDOW_DAYS = 90
BACKGROUND_CEILING = int(HOURLY_LIMIT * 0.8)
TICK = 30  # the refresh loop wakes this often


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":")))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Engine:
    def __init__(self, root, workspace=None):
        self.root = Path(root)
        self.client = Client(workspace)
        self.lock = threading.RLock()
        self.cache_file = config.folder("cache") / "cache.json"
        self.settings_file = config.settings_file()
        self.data = {"me": None, "members": [], "people": {}, "details": {}, "launches": [], "history": {}}
        self.config = {"team": None, "project": config.default_project()}
        self.auth = {"ok": None, "error": None}
        self._pushed = {"at": 0, "items": []}
        self._seed_tried = False
        self._load()
        self.events = events.Log(self.data, self._save)

    # ---- persistence ----------------------------------------------------

    def _load(self):
        try:
            self.data.update(json.loads(self.cache_file.read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError):
            pass
        try:
            self.config.update(json.loads(self.settings_file.read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError):
            pass

    def _save(self):
        with self.lock:
            _atomic_write(self.cache_file, self.data)

    def _save_config(self):
        _atomic_write(self.settings_file, self.config)

    # ---- auth and identity ----------------------------------------------

    def _note(self, exc):
        if exc.status in (401, 403) or exc.status == 0 and "No BITBUCKET_" in exc.message:
            self.auth = {"ok": False, "error": exc.message, "status": exc.status}

    def refresh_identity(self):
        try:
            raw = self.client.me()
        except BitbucketError as exc:
            self._note(exc)
            raise
        self.auth = {"ok": True, "error": None}
        with self.lock:
            self.data["me"] = {
                "uuid": raw.get("uuid"),
                "name": raw.get("display_name"),
                "nickname": raw.get("nickname"),
                "account_id": raw.get("account_id"),
            }
        try:
            members = self.client.members()
            with self.lock:
                self.data["members"] = [
                    {"uuid": m.get("uuid"), "name": m.get("display_name"), "nickname": m.get("nickname")}
                    for m in members if m.get("uuid")
                ]
        except BitbucketError:
            pass  # the team picker falls back to people seen on pull requests
        self._save()
        return self.data["me"]

    def me(self):
        return self.data.get("me")

    def _me_uuid(self):
        return (self.data.get("me") or {}).get("uuid")

    # ---- team -----------------------------------------------------------

    def candidates(self):
        """Workspace members, plus anyone seen on a cached pull request."""
        seen = {m["uuid"]: m for m in self.data.get("members", []) if m.get("uuid")}
        for person in self.data.get("people", {}).values():
            for pr in person.get("open", []) + person.get("merged", []):
                for user in [pr["author"], *pr.get("reviews", [])]:
                    if user.get("uuid") and user["uuid"] not in seen:
                        seen[user["uuid"]] = {"uuid": user["uuid"], "name": user["name"], "nickname": user.get("nickname")}
        me = (self.me() or {}).get("uuid")
        seen.pop(me, None)
        return sorted(seen.values(), key=lambda m: (m.get("name") or "").lower())

    def team(self):
        if self.config.get("team") is None and not self._seed_tried:
            self._seed_team()
        index = {c["uuid"]: c for c in self.candidates()}
        return [index[u] for u in self.config.get("team") or [] if u in index]

    def _seed_team(self):
        """First run: teammates are workspace members who committed to these
        repos in the last 90 days, matched by display name."""
        candidates = self.candidates()
        if not candidates:
            return
        self._seed_tried = True
        names = gitscan.recent_author_names(self.root)
        picked = [c["uuid"] for c in candidates if (c.get("name") or "").strip().lower() in names]
        if picked:
            self.config["team"] = picked
            self._save_config()

    def set_team(self, uuids):
        valid = {c["uuid"] for c in self.candidates()}
        self.config["team"] = [u for u in uuids if u in valid]
        self._save_config()
        return self.team()

    # ---- pull requests --------------------------------------------------

    def _person_lock(self, uuid):
        """One refresh per person at a time: two at once would both compare against the same
        old list and log every change twice. Reentrant, since the open lane calls the full one."""
        with self.lock:
            locks = self.__dict__.setdefault("_person_locks", {})
            return locks.setdefault(uuid, threading.RLock())

    def _record(self, uuid, found):
        self.events.record(uuid, self._who_commented(found))

    def _who_commented(self, found):
        """A comment count says how many, not who: read the newest comments of a few pull
        requests and drop yours, so your own replies never notify you. If the read fails the
        event stays as it was (fail-open: "new comments" beats silence)."""
        me, out, looked = self._me_uuid(), [], 0
        for ev in found:
            if ev.get("kind") != "comments" or looked >= COMMENT_LOOKUPS:
                out.append(ev)
                continue
            looked += 1
            try:
                raw = self.client.get(
                    f"/repositories/{self.client.workspace}/{ev['repo']}/pullrequests/{int(ev['pr_id'])}/comments",
                    {"sort": "-created_on", "pagelen": max(1, min(50, int(ev.get("count") or 1))),
                     "fields": "values.user.uuid,values.user.display_name,values.deleted"})
            except BitbucketError:
                out.append(ev)
                continue
            others = [c for c in raw.get("values") or [] if not c.get("deleted") and (c.get("user") or {}).get("uuid") != me]
            if not others:
                continue
            names = list(dict.fromkeys((c.get("user") or {}).get("display_name") or "Someone" for c in others))
            out.append({**ev, "count": len(others), "names": names[:5],
                        "actor": {"uuid": (others[0].get("user") or {}).get("uuid"), "name": names[0]}})
        return out

    def refresh_person(self, uuid, before_open=None):
        with self._person_lock(uuid):
            return self._refresh_person(uuid, before_open)

    def _refresh_person(self, uuid, before_open=None):
        """Open PRs in full; merged PRs incrementally.

        Merged lists are long (hundreds per person), so after the first full
        walk of the window only pages newer than the newest cached merge are
        read, with a full walk every few hours to catch anything missed.
        `before_open` is the open list to compare with when the caller already
        replaced it (the open lane), so a merge still reads as one.
        """
        window_start = (datetime.now(timezone.utc) - timedelta(days=MERGED_WINDOW_DAYS)).isoformat()
        previous = self.person(uuid) or {}
        cached = previous.get("merged") or []
        newest = cached[0]["updated_on"] if cached else None
        incremental = (
            newest and newest >= window_start and self.client.sort_supported
            and time.time() - (previous.get("full_at") or 0) < FULL_WALK_EVERY
        )
        meta = {}
        declined_start = (datetime.now(timezone.utc) - timedelta(days=DECLINED_WINDOW_DAYS)).isoformat()
        try:
            open_prs = [model.slim_pr(p) for p in self.client.user_prs(uuid, "OPEN", max_pages=4)]
            declined = [model.slim_pr(p) for p in self.client.user_prs(uuid, "DECLINED", newer_than=declined_start, max_pages=2)]
            if incremental:
                fresh = [model.slim_pr(p) for p in self.client.user_prs(uuid, "MERGED", newer_than=newest, max_pages=3, meta=meta)]
                by_key = {(p["repo"], p["id"]): p for p in cached}
                by_key.update({(p["repo"], p["id"]): p for p in fresh})
                merged = [p for p in by_key.values() if (p.get("updated_on") or "") >= window_start]
                full_at = previous.get("full_at")
            else:
                merged = [model.slim_pr(p) for p in self.client.user_prs(uuid, "MERGED", newer_than=window_start, max_pages=12, meta=meta)]
                full_at = time.time()
        except BitbucketError as exc:
            if exc.status == 401:  # a 403 here is one endpoint, not a dead token
                self._note(exc)
            with self.lock:
                entry = self.data["people"].setdefault(uuid, {"open": [], "merged": []})
                entry["error"] = exc.message
            raise
        self.auth = {"ok": True, "error": None}
        merged.sort(key=lambda p: p.get("updated_on") or "", reverse=True)
        if previous.get("synced_at"):  # a first sync has nothing to compare with
            self._record(uuid, events.diff(
                previous.get("open") if before_open is None else before_open, open_prs,
                self._me_uuid(), merged=merged, declined=declined))
        with self.lock:
            self.data["people"][uuid] = {
                "open": open_prs,
                "merged": merged,
                "declined": declined,
                "merged_total": meta.get("size") or previous.get("merged_total"),
                "synced_at": time.time(),
                "open_at": time.time(),
                "full_at": full_at,
                "error": None,
            }
        self._save()
        return self.data["people"][uuid]

    def refresh_open(self, uuid):
        """Only the open pull requests, in one call. One that left the list was merged or
        declined somewhere else, so the full refresh files it straight away."""
        if self.person(uuid) is None:
            return None  # never synced: the full refresh comes first, so spend no call here
        with self._person_lock(uuid):
            return self._refresh_open(uuid)

    def _refresh_open(self, uuid):
        open_prs = [model.slim_pr(p) for p in self.client.user_prs(uuid, "OPEN", max_pages=4)]
        with self.lock:
            entry = self.data["people"].get(uuid)
            if entry is None:
                return None
            before = entry.get("open", [])
            entry["open_at"] = time.time()
            changed = json.dumps(before, sort_keys=True) != json.dumps(open_prs, sort_keys=True)
            entry["open"] = open_prs
        if {(p["repo"], p["id"]) for p in before} - {(p["repo"], p["id"]) for p in open_prs}:
            try:
                return self.refresh_person(uuid, before_open=before)
            except BitbucketError:
                # Put the old list back, so the next read still sees what left and logs it.
                with self.lock:
                    entry["open"] = before
                raise
        if changed:
            self._record(uuid, events.diff(before, open_prs, self._me_uuid()))
            self._save()
        return entry

    def file_merged(self, uuid, raw):
        """Move a pull request just merged from here into the merged list at once, from
        Bitbucket's own answer, instead of waiting for its merged list to catch up."""
        pr = model.slim_pr(raw)
        key = (pr["repo"], pr["id"])
        with self.lock:
            entry = self.data["people"].get(uuid)
            if entry is None:
                return pr
            entry["open"] = [p for p in entry.get("open", []) if (p["repo"], p["id"]) != key]
            entry["merged"] = [pr] + [p for p in entry.get("merged", []) if (p["repo"], p["id"]) != key]
        self._save()
        return pr

    def person(self, uuid):
        with self.lock:
            entry = self.data["people"].get(uuid)
        return entry

    def update_pr(self, pr):
        """Put a freshly read pull request in place of every cached copy of it."""
        key = (pr["repo"], pr["id"])
        with self.lock:
            for person in self.data["people"].values():
                for name in ("open", "merged", "declined"):
                    items = person.get(name) or []
                    for i, old in enumerate(items):
                        if (old["repo"], old["id"]) == key:
                            items[i] = pr
        self._save()

    def find_pr(self, repo, pr_id):
        with self.lock:
            for person in self.data["people"].values():
                for pr in person.get("open", []) + person.get("merged", []):
                    if pr["repo"] == repo and pr["id"] == pr_id:
                        return pr
        return None

    def detail(self, repo, pr_id, force=False):
        key = f"{repo}#{pr_id}"
        pr = self.find_pr(repo, pr_id)
        if pr is None:
            pr = model.slim_pr(self.client.pull_request(repo, pr_id))
        with self.lock:
            cached = self.data["details"].get(key)
        fresh = (
            cached
            and cached.get("change_key") == model.change_key(pr)
            and time.time() - cached.get("fetched_at", 0) < DETAIL_MAX_AGE
        )
        if fresh and not force:
            return {**cached, "pr": pr}
        raw = self.client.pull_request(repo, pr_id)
        full = model.slim_pr(raw)
        threads = model.build_threads(self.client.comments(repo, pr_id), full["author"]["uuid"])
        tasks = model.slim_tasks(self.client.tasks(repo, pr_id))
        entry = {
            "change_key": model.change_key(pr),
            "fetched_at": time.time(),
            "description": raw.get("description") or "",
            "threads": threads,
            "tasks": tasks,
            "attention": model.attention_of(threads, tasks),
        }
        with self.lock:
            self.data["details"][key] = entry
        self._save()
        return {**entry, "pr": full}

    # ---- history: who requested changes, from the activity log -----------

    def history(self, pr):
        return self.data.setdefault("history", {}).get(f"{pr['repo']}#{pr['id']}")

    def warm_history(self, uuid, budget):
        """Merged PRs do not change, so each one's activity is read once and
        kept. Only the last HISTORY_WINDOW_DAYS are read, a few per tick."""
        entry = self.person(uuid) or {}
        since = (datetime.now(timezone.utc) - timedelta(days=HISTORY_WINDOW_DAYS)).isoformat()
        store = self.data.setdefault("history", {})
        done = 0
        for pr in entry.get("merged", []):
            if not self.in_scope(pr):
                continue
            if done >= budget or self.client.calls_last_hour() > BACKGROUND_CEILING:
                break
            if (pr.get("updated_on") or "") < since:
                break
            key = f"{pr['repo']}#{pr['id']}"
            if key in store:
                continue
            try:
                events = self.client.activity(pr["repo"], pr["id"])
            except BitbucketError:
                break
            store[key] = {
                "changes_requested": [
                    {"name": (e["changes_requested"].get("user") or {}).get("display_name"),
                     "uuid": (e["changes_requested"].get("user") or {}).get("uuid"),
                     "at": e["changes_requested"].get("date")}
                    for e in events if isinstance(e.get("changes_requested"), dict)
                ],
                "fetched_at": time.time(),
            }
            done += 1
        if done:
            self._save()
        return done

    def _pr_key(self, repo, pr_id):
        pr = self.find_pr(repo, pr_id) or {}
        return (repo, pr_id, pr.get("source_commit"), pr.get("updated_on"))

    def pr_diff(self, repo, pr_id):
        cache = self.__dict__.setdefault("_diff_cache", {})
        key = self._pr_key(repo, pr_id)
        if key in cache:
            return cache[key]
        text, cut = self.client.text(f"/repositories/{self.client.workspace}/{repo}/pullrequests/{int(pr_id)}/diff")
        result = {"files": model.parse_diff(text), "cut": cut, "fetched_at": time.time()}
        if len(cache) > 24:
            cache.pop(next(iter(cache)))
        cache[key] = result
        return result

    def pr_extra(self, repo, pr_id):
        cache = self.__dict__.setdefault("_extra_cache", {})
        key = self._pr_key(repo, pr_id)
        if key in cache:
            return cache[key]
        base = f"/repositories/{self.client.workspace}/{repo}/pullrequests/{int(pr_id)}"
        commits = []
        for c in self.client.paged(base + "/commits", {"pagelen": 50}, max_pages=4):
            author = c.get("author") or {}
            commits.append({
                "sha": (c.get("hash") or "")[:8],
                "subject": (c.get("message") or "").split("\n", 1)[0],
                "author": (author.get("user") or {}).get("display_name") or (author.get("raw") or "").split(" <")[0],
                "at": c.get("date"),
            })
        events = model.timeline(self.client.activity(repo, pr_id))
        result = {"commits": commits, "timeline": events[:60], "fetched_at": time.time()}
        if len(cache) > 48:
            cache.pop(next(iter(cache)))
        cache[key] = result
        return result

    def attention(self, pr):
        cached = self.data["details"].get(f"{pr['repo']}#{pr['id']}")
        if not cached:
            return None
        return {**cached["attention"], "stale": cached.get("change_key") != model.change_key(pr)}

    def warm_details(self, uuid, budget=12):
        """Keep my own open PRs' threads current so the counts are real."""
        entry = self.person(uuid) or {}
        for pr in entry.get("open", []):
            if not self.in_scope(pr):
                continue
            if budget <= 0 or self.client.calls_last_hour() > BACKGROUND_CEILING:
                return
            cached = self.data["details"].get(f"{pr['repo']}#{pr['id']}")
            if cached and cached.get("change_key") == model.change_key(pr) \
                    and time.time() - cached.get("fetched_at", 0) < DETAIL_MAX_AGE:
                continue
            try:
                self.detail(pr["repo"], pr["id"], force=True)
                budget -= 3
            except BitbucketError:
                return

    # ---- local git ------------------------------------------------------

    def pushed(self, force=False):
        if not force and time.time() - self._pushed["at"] < 120:
            return self._pushed["items"]
        me = self.me()
        if not me:
            return []
        entry = self.person(me["uuid"]) or {}
        covered = {(p["repo"], p["source"]) for p in entry.get("open", []) + entry.get("merged", [])}
        email = gitscan.my_email(self.root)
        items = gitscan.pushed_without_pr(self.root, email, covered) if email else []
        self._pushed = {"at": time.time(), "items": items}
        return items

    # ---- launches -------------------------------------------------------

    def record_launch(self, record):
        with self.lock:
            self.data["launches"] = ([record] + self.data.get("launches", []))[:30]
        self._save()

    # ---- background loop ------------------------------------------------

    def _due(self, uuid, every):
        entry = self.person(uuid)
        return not entry or time.time() - (entry.get("synced_at") or 0) > every

    def _open_due(self, uuid, every):
        entry = self.person(uuid)
        return bool(entry) and time.time() - (entry.get("open_at") or entry.get("synced_at") or 0) > every

    def schedule(self):
        """When your pull requests were read and when the next read is due, for the status bar.
        The loop wakes every TICK seconds, so a read is due at most that much after it is owed."""
        me = (self.person(self._me_uuid()) or {}) if self._me_uuid() else {}
        synced, open_at = me.get("synced_at"), me.get("open_at")
        owed = min((open_at or 0) + OPEN_EVERY, (synced or 0) + ME_EVERY)
        team = [self.person(m["uuid"]) or {} for m in self.team()]
        team_at = [t.get("open_at") or t.get("synced_at") for t in team if t.get("synced_at")]
        return {
            "synced_at": synced, "open_at": open_at, "next_at": max(time.time(), owed) if synced else None,
            "tick": TICK, "team_at": max(team_at) if team_at else None,
            "paused": self.client.calls_last_hour() > BACKGROUND_CEILING,
        }

    def _env_mtime(self):
        return config.mtime()

    def tick(self):
        if self.client.calls_last_hour() > BACKGROUND_CEILING:
            return
        if self.auth.get("ok") is False:
            # A rejected token is retried once a minute, or at once when the env file changes.
            mtime = self._env_mtime()
            if mtime == getattr(self, "_env_seen", None) and time.time() - getattr(self, "_auth_try_at", 0) < 60:
                return
            self._env_seen = mtime
        self._auth_try_at = time.time()
        try:
            if not self.me() or self.auth.get("ok") is not True:
                self.refresh_identity()
            me = self.me()["uuid"]
            if self._due(me, ME_EVERY):
                self.refresh_person(me)
                self.warm_details(me)
            elif time.time() - ((self.person(me) or {}).get("open_at") or 0) > OPEN_EVERY:
                self.refresh_open(me)
                self.warm_details(me)
            full = None
            for mate in self.team():
                if self._due(mate["uuid"], TEAM_EVERY):
                    self.refresh_person(mate["uuid"])
                    full = mate["uuid"]
                    break  # one teammate per tick spreads the calls out
            if full is None:
                stale = [m for m in self.team() if self._open_due(m["uuid"], TEAM_OPEN_EVERY)]
                if stale:
                    self.refresh_open(min(stale, key=lambda m: (self.person(m["uuid"]) or {}).get("open_at") or 0)["uuid"])
            if not self.warm_history(me, budget=8):
                for mate in self.team():
                    if self.warm_history(mate["uuid"], budget=4):
                        break
        except BitbucketError:
            pass

    def run_forever(self, interval=None):
        interval = interval or TICK
        def loop():
            while True:
                self.tick()
                time.sleep(interval)
        thread = threading.Thread(target=loop, name="pr-desk-refresh", daemon=True)
        thread.start()
        return thread

    def budget(self):
        return {"used": self.client.calls_last_hour(), "limit": HOURLY_LIMIT}

    # ---- project scope ----------------------------------------------------

    def projects(self):
        """Workspace projects, cached for a day."""
        cache = self.data.get("projects") or {}
        if cache.get("items") and time.time() - cache.get("at", 0) < PROJECT_LIST_MAX_AGE:
            return cache["items"]
        raw = self.client.paged(f"/workspaces/{self.client.workspace}/projects", {"pagelen": 100}, max_pages=5)
        items = sorted(({"key": p.get("key"), "name": p.get("name")} for p in raw if p.get("key")),
                       key=lambda p: (p["name"] or "").lower())
        with self.lock:
            self.data["projects"] = {"items": items, "at": time.time()}
        self._save()
        return items

    def project(self):
        key = self.config.get("project")
        if not key:
            return None
        name = next((p["name"] for p in (self.data.get("projects") or {}).get("items", []) if p["key"] == key), key)
        return {"key": key, "name": name}

    def project_repos(self, offline=False):
        """Repository slugs of the chosen project, cached for a day. None means every project.
        `offline` answers from the cache only, however old: the polled routes never call Bitbucket."""
        key = self.config.get("project")
        if not key:
            return None
        cache = (self.data.get("project_repos") or {}).get(key) or {}
        if cache.get("slugs") is not None and (offline or time.time() - cache.get("at", 0) < PROJECT_LIST_MAX_AGE):
            return set(cache["slugs"])
        if offline:
            return None
        try:
            raw = self.client.paged(f"/repositories/{self.client.workspace}",
                                    {"q": f'project.key="{key}"', "pagelen": 100, "fields": "values.slug,next"},
                                    max_pages=10)
            slugs = sorted(r["slug"] for r in raw if r.get("slug"))
        except BitbucketError:
            return set(cache["slugs"]) if cache.get("slugs") is not None else None
        with self.lock:
            self.data.setdefault("project_repos", {})[key] = {"slugs": slugs, "at": time.time()}
        self._save()
        return set(slugs)

    def in_scope(self, pr, repos=None):
        repos = self.project_repos() if repos is None else repos
        return repos is None or pr.get("repo") in repos

    def set_project(self, key):
        if key and key not in {p["key"] for p in self.projects()}:
            raise ValueError(f"unknown project {key!r}")
        self.config["project"] = key or ""
        self._save_config()
        return self.project()
