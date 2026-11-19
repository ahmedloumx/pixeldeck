"""The team activity feed against a scripted Bitbucket: no network, no real token.

FakeLogs serves each repository's activity log a few entries per page and records every call,
so a test also proves how many calls a read spent and where its paging stopped."""

import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine as engine_mod  # noqa: E402
import hosts  # noqa: E402
import model  # noqa: E402
import teamfeed  # noqa: E402
from bitbucket import BitbucketError  # noqa: E402

NOW = 1_900_000_000.0
ME = {"uuid": "{me}", "display_name": "Me Myself"}
ALICE = {"uuid": "{alice}", "display_name": "Alice Mate"}
BOB = {"uuid": "{bob}", "display_name": "Bob Outsider"}


def ago(minutes, now=NOW):
    return model.iso(now - minutes * 60)


def pr_ref(pr_id):
    return {"type": "pullrequest", "id": pr_id, "title": f"Pull request {pr_id}",
            "links": {"html": {"href": f"https://bitbucket.org/acme/shop/pull-requests/{pr_id}"}}}


def approval(pr_id, user, at):
    return {"approval": {"date": at, "user": user, "pullrequest": pr_ref(pr_id)}, "pull_request": pr_ref(pr_id)}


def changes(pr_id, user, at):
    return {"changes_requested": {"date": at, "user": user}, "pull_request": pr_ref(pr_id)}


def comment(cid, pr_id, user, at, raw="Looks good", path=None, deleted=False):
    c = {"id": cid, "user": user, "created_on": at, "updated_on": at, "deleted": deleted,
         "content": {"raw": raw, "markup": "markdown"}, "type": "pullrequest_comment"}
    if path:
        c["inline"] = {"path": path, "to": 3, "from": None}
    return {"comment": c, "pull_request": pr_ref(pr_id)}


def update(pr_id, user, at, state="OPEN"):
    return {"update": {"state": state, "author": user, "date": at, "title": f"Pull request {pr_id}", "reason": ""},
            "pull_request": pr_ref(pr_id)}


def slim(repo, pr_id, author, created, updated, state="OPEN", closed_by=None):
    return {"repo": repo, "id": pr_id, "title": f"Pull request {pr_id}", "state": state,
            "author": {"uuid": author["uuid"], "name": author["display_name"]}, "created_on": created,
            "updated_on": updated, "reviews": [],
            "closed_by": {"uuid": closed_by["uuid"], "name": closed_by["display_name"]} if closed_by else None}


class FakeLogs:
    """Bitbucket's repository activity logs, newest first, served `per_page` entries a page."""

    workspace = "acme"

    def __init__(self, logs=None, per_page=2, errors=None):
        self.logs = logs or {}
        self.per_page = per_page
        self.errors = errors or {}
        self.calls = []
        self.used = 0

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path.startswith("https://api.bitbucket.org/next/"):
            repo, start = path.rsplit("/", 2)[-2:]
            start = int(start)
            assert params is None, "a next link carries its own query"
        else:
            parts = path.split("/")
            assert parts[1:3] == ["repositories", "acme"] and path.endswith("/pullrequests/activity"), path
            repo, start = parts[3], 0
            assert params == {"pagelen": 50}, params
        if repo in self.errors:
            raise self.errors[repo]
        values = self.logs.get(repo, [])
        page = {"values": values[start:start + self.per_page]}
        if start + self.per_page < len(values):
            page["next"] = f"https://api.bitbucket.org/next/{repo}/{start + self.per_page}"
        return page

    def to(self, repo):
        return [c for c in self.calls if f"/{repo}/" in c[0]]

    def calls_last_hour(self):
        return self.used + len(self.calls)


# ---------------------------------------------------------------- parsing


class FeedEntryTest(unittest.TestCase):
    def test_each_kind_becomes_one_entry_with_a_stable_id(self):
        at = "2026-11-18T10:00:00.123456+00:00"
        got = [model.feed_entry(raw, "shop") for raw in (
            approval(7, ALICE, at), changes(7, ALICE, at), comment(99, 7, ALICE, at, raw="x" * 300, path="src/app.py"),
            update(7, ME, at), update(7, ME, at, "MERGED"), update(7, ME, at, "DECLINED"))]
        self.assertEqual([e["kind"] for e in got], ["approved", "changes_requested", "commented", "updated", "merged", "declined"])
        self.assertTrue(all(e["repo"] == "shop" and e["pr_id"] == 7 and e["pr_title"] == "Pull request 7" for e in got))
        self.assertEqual(got[0]["who"], {"uuid": "{alice}", "name": "Alice Mate"})
        self.assertEqual((got[2]["where"], len(got[2]["text"])), ("src/app.py", 160))
        self.assertEqual(len({e["id"] for e in got}), 6)
        self.assertEqual(model.feed_entry(approval(7, ALICE, at), "shop")["id"], got[0]["id"], "the same entry read again")

    def test_what_the_feed_leaves_out(self):
        at = "2026-11-18T10:00:00+00:00"
        self.assertIsNone(model.feed_entry(comment(1, 7, ALICE, at, deleted=True), "shop"))
        self.assertIsNone(model.feed_entry({"task": {"id": 1}, "pull_request": pr_ref(7)}, "shop"))
        self.assertIsNone(model.feed_entry({"approval": {"date": at, "user": ALICE}}, "shop"), "no pull request")
        self.assertIsNone(model.feed_entry({"approval": {"user": ALICE}, "pull_request": pr_ref(7)}, "shop"), "no date")

    def test_times_compare_as_instants(self):
        self.assertEqual(model.stamp("2026-11-18T10:00:00Z"), model.stamp("2026-11-18T10:00:00+00:00"))
        self.assertEqual(model.stamp("2026-11-18T11:00:00+01:00"), model.stamp("2026-11-18T10:00:00+00:00"))
        self.assertEqual((model.stamp(None), model.stamp("soon")), (0.0, 0.0))


# ---------------------------------------------------------------- the host


class HostTest(unittest.TestCase):
    def host(self, fake):
        return hosts.BitbucketHost(types.SimpleNamespace(client=fake))

    def test_the_walk_stops_at_the_entry_it_already_has(self):
        log = [approval(3, ALICE, ago(1)), comment(5, 3, ME, ago(2)), update(3, ME, ago(30)), update(2, ME, ago(40))]
        fake = FakeLogs({"shop": log})
        got = self.host(fake).repo_activity("shop", newer_than=ago(10), max_pages=4)
        self.assertEqual([e["kind"] for e in got["entries"]], ["approved", "commented"])
        self.assertEqual((got["reached"], got["pages"], len(fake.calls)), (True, 2, 2),
                         "the second page holds the older entry: the third is never asked for")

    def test_the_page_cap_says_it_did_not_get_back(self):
        log = [comment(i, 3, ME, ago(i)) for i in range(1, 9)]
        fake = FakeLogs({"shop": log})
        got = self.host(fake).repo_activity("shop", newer_than=ago(60), max_pages=2)
        self.assertEqual((len(got["entries"]), got["reached"], got["pages"]), (4, False, 2))

    def test_the_end_of_the_log_is_reached(self):
        fake = FakeLogs({"shop": [comment(1, 3, ME, ago(1))]})
        got = self.host(fake).repo_activity("shop", newer_than=None, max_pages=4)
        self.assertEqual((len(got["entries"]), got["reached"], got["pages"]), (1, True, 1))

    def test_a_log_out_of_order_is_not_trusted_to_stop_early(self):
        log = [comment(1, 3, ME, ago(90)), comment(2, 3, ME, ago(5)), comment(3, 3, ME, ago(6)), comment(4, 3, ME, ago(1))]
        got = self.host(FakeLogs({"shop": log})).repo_activity("shop", newer_than=ago(60), max_pages=4)
        self.assertEqual(len(got["entries"]), 4, "an old entry before newer ones does not end the walk")
        self.assertEqual((got["reached"], got["pages"]), (True, 2), "it read to the log's end")
        got = self.host(FakeLogs({"shop": log})).repo_activity("shop", newer_than=ago(60), max_pages=1)
        self.assertEqual((len(got["entries"]), got["reached"]), (2, False))

    def test_other_hosts_have_no_such_log(self):
        self.assertIsNone(hosts.Host().repo_activity("shop"))
        github_like = type("GitHubLike", (hosts.Host,), {"id": "github", "label": "GitHub"})()
        self.assertFalse(github_like.serves("team-activity"))
        self.assertNotIn("team-activity", github_like.features(["events", "team-activity"]))
        self.assertIn("team-activity", hosts.BitbucketHost(None).features(["events", "team-activity"]))


# ---------------------------------------------------------------- the engine


class FeedBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pixeldeck-feed-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = NOW
        patcher = mock.patch.object(engine_mod, "time", types.SimpleNamespace(time=lambda: self.now))
        patcher.start()
        self.addCleanup(patcher.stop)

    def engine(self, logs=None, per_page=2, errors=None, people=None):
        eng = engine_mod.Engine.__new__(engine_mod.Engine)
        eng.lock = threading.RLock()
        eng.client = FakeLogs(logs, per_page, errors)
        eng.auth = {"ok": True, "error": None}
        eng.host_id = "bitbucket"
        eng._seed_tried = True
        eng._feed_file = self.tmp / "activity.json"
        eng.config = {"team": ["{alice}"], "project": None}
        eng.data = {"me": {"uuid": "{me}", "name": "Me Myself"},
                    "members": [{"uuid": "{alice}", "name": "Alice Mate"}, {"uuid": "{bob}", "name": "Bob Outsider"}],
                    "people": people if people is not None else {
                        "{me}": {"open": [slim("shop", 3, ME, ago(600), ago(1))], "merged": []},
                        "{alice}": {"open": [slim("web", 8, ALICE, ago(900), ago(300))], "merged": []}},
                    "details": {}, "history": {}}
        eng._save = lambda: None
        return eng

    def later(self, seconds):
        self.now += seconds


class EngineReadTest(FeedBase):
    def test_a_first_read_then_only_what_is_new(self):
        logs = {"shop": [approval(3, ALICE, ago(2)), comment(5, 3, ME, ago(30)), update(3, ME, ago(50)), update(3, ME, ago(70))],
                "web": [comment(9, 8, ALICE, ago(20))]}
        eng = self.engine(logs, per_page=3)
        self.assertEqual(eng.read_activity(), 3, "shop takes two pages of three, web one")
        kept = json.loads((self.tmp / "activity.json").read_text())
        self.assertEqual(sorted(kept["repos"]), ["shop", "web"])
        self.assertEqual(eng.read_activity(), 0, "nothing is due right after a read")

        self.later(engine_mod.ACTIVITY_EVERY)
        logs["shop"].insert(0, comment(6, 3, ALICE, ago(1, self.now)))
        before = len(eng.client.calls)
        self.assertEqual(eng.read_activity(), 2, "one call per repository: the first page reaches what is kept")
        self.assertEqual(len(eng.client.calls) - before, 2)
        shop = eng._feed()["repos"]["shop"]["entries"]
        self.assertEqual([e["id"] for e in shop][:2], ["c:shop:6", f"a:shop:3:{{alice}}:{ago(2)}"])
        self.assertEqual(len(shop), 5, "entries read twice are kept once")

    def test_a_moved_pull_request_makes_its_repository_due_sooner(self):
        eng = self.engine({"shop": [comment(1, 3, ME, ago(5))], "web": []})
        eng.read_activity()
        self.later(engine_mod.ACTIVITY_FLOOR - 1)
        eng.data["people"]["{me}"]["open"][0]["updated_on"] = ago(0, self.now)
        self.assertEqual(eng.read_activity(), 0, "never within the floor")
        self.later(2)
        before = len(eng.client.calls)
        self.assertEqual(eng.read_activity(), 1)
        self.assertEqual([c[0] for c in eng.client.calls[before:]], ["/repositories/acme/shop/pullrequests/activity"],
                         "web did not move: it waits for its turn")

    def test_force_reads_whatever_its_age_but_not_within_the_floor(self):
        eng = self.engine({"shop": [], "web": []})
        eng.read_activity()
        self.assertEqual(eng.read_activity(force=True), 0)
        self.later(engine_mod.ACTIVITY_FLOOR)
        self.assertEqual(eng.read_activity(force=True), 2)

    def test_the_budget_and_a_busy_host_stop_the_read(self):
        eng = self.engine({"shop": [comment(i, 3, ME, ago(i)) for i in range(1, 9)], "web": [comment(20, 8, ALICE, ago(1))]})
        self.assertEqual(eng.read_activity(budget=3), 3)
        self.assertNotIn("web", eng._feed()["repos"], "the budget ran out first")
        self.later(engine_mod.ACTIVITY_EVERY)
        eng.client.used = 10_000
        self.assertEqual(eng.read_activity(), 0)

    def test_too_much_at_once_leaves_a_gap_the_answer_names(self):
        eng = self.engine({"shop": [comment(1, 3, ME, ago(5))], "web": []}, per_page=1)
        eng.read_activity()
        self.assertEqual(eng.team_activity()["partial"], [])
        self.later(engine_mod.ACTIVITY_EVERY)
        eng.client.logs["shop"] = [comment(i, 3, ME, ago(i, self.now)) for i in range(10, 2, -1)][::-1] + eng.client.logs["shop"]
        eng.read_activity()
        answer = eng.team_activity()
        self.assertEqual(answer["partial"], ["shop"], "two pages did not get back to the entry kept")
        self.assertEqual(eng.team_activity(since=self.now - 200)["partial"], [], "a range inside what was read is whole")

    def test_one_repository_failing_does_not_stop_the_others(self):
        eng = self.engine({"web": [comment(1, 8, ALICE, ago(1))]}, errors={"shop": BitbucketError(404, "Repository not found")})
        self.assertEqual(eng.read_activity(), 2)
        answer = eng.team_activity()
        self.assertEqual(answer["errors"], [{"repo": "shop", "error": "Repository not found"}])
        self.assertEqual([e["repo"] for e in answer["entries"] if e["kind"] == "commented"], ["web"])

    def test_a_refused_token_stops_the_read_and_is_noted(self):
        eng = self.engine({}, errors={"shop": BitbucketError(401, "Unauthorized")})
        with self.assertRaises(BitbucketError):
            eng.read_activity()
        self.assertFalse(eng.auth["ok"])

    def test_only_one_read_at_a_time(self):
        eng = self.engine({"shop": [], "web": []})
        eng._feed_lock().acquire()
        try:
            self.assertIsNone(eng.read_activity())
            self.assertTrue(eng.team_activity()["reading"])
        finally:
            eng._feed_lock().release()
        self.assertEqual(eng.client.calls, [])

    def test_a_file_that_cannot_be_written_does_not_keep_the_read_locked(self):
        eng = self.engine({"shop": [comment(1, 3, ME, ago(1))], "web": []})
        with mock.patch.object(engine_mod, "_atomic_write", side_effect=PermissionError("held by a scanner")):
            self.assertEqual(eng.read_activity(), 2)
        self.assertFalse(eng._feed_lock().locked())
        self.assertEqual(len(eng._feed()["repos"]["shop"]["entries"]), 1, "what was read stays in memory")

    def test_a_feed_failure_never_stops_the_tick(self):
        eng = self.engine({"shop": [], "web": []})
        eng.team_activity()
        with mock.patch.object(eng, "read_activity", side_effect=KeyError("broken")), \
                mock.patch.object(engine_mod.traceback, "print_exc"):
            self.assertEqual(eng.warm_activity(), 0)
        with mock.patch.object(eng, "read_activity", side_effect=BitbucketError(0, "offline")):
            with self.assertRaises(BitbucketError, msg="the tick's own handler takes a host error"):
                eng.warm_activity()

    def test_the_tick_warms_the_feed_only_while_the_page_shows_it(self):
        eng = self.engine({"shop": [], "web": []})
        self.assertEqual(eng.warm_activity(), 0)
        eng.team_activity()
        self.assertEqual(eng.warm_activity(), engine_mod.ACTIVITY_TICK_CALLS)
        self.later(engine_mod.ACTIVITY_WATCHED + 1)
        self.later(engine_mod.ACTIVITY_EVERY)
        self.assertEqual(eng.warm_activity(), 0)

    def test_a_host_without_the_log_spends_nothing(self):
        eng = self.engine({"shop": []})
        eng._host = type("GitHubLike", (hosts.Host,), {"id": "github", "label": "GitHub"})()
        self.assertIsNone(eng.read_activity())
        self.assertEqual(eng.client.calls, [])

    def test_the_repositories_follow_the_team_and_the_project(self):
        people = {"{me}": {"open": [slim("shop", 3, ME, ago(9000), ago(1))],
                           "merged": [slim("old", 1, ME, ago(90000), ago(60 * 24 * 60), state="MERGED")]},
                  "{alice}": {"open": [slim("web", 8, ALICE, ago(900), ago(300))]},
                  "{bob}": {"open": [slim("elsewhere", 2, BOB, ago(900), ago(1))]}}
        eng = self.engine(people=people)
        self.assertEqual(eng.activity_repos(None)["repos"], ["shop", "web"], "Bob is not on the team; old merged too long ago")
        eng.data["projects"] = {"items": [{"key": "SHOP", "name": "Shop"}], "at": NOW}
        eng.data["project_repos"] = {"SHOP": {"slugs": ["shop"], "at": NOW}}
        self.assertEqual(eng.team_activity(project="SHOP")["repos"], ["shop"])
        self.assertEqual(eng.team_activity(project="*")["repos"], ["shop", "web"])
        eng.config["project"] = "SHOP"
        self.assertEqual(eng.team_activity()["project"], {"key": "SHOP", "name": "Shop"}, "none asked: the bar's own")
        with self.assertRaises(ValueError):
            eng.team_activity(project='X" OR 1=1')
        del eng.data["project_repos"]
        self.assertFalse(eng.team_activity(project="SHOP")["scope_known"], "offline: the project's list is not read here")
        self.assertEqual(eng.client.calls, [])


class AnswerTest(FeedBase):
    def test_the_answer_joins_filters_and_never_calls(self):
        logs = {"shop": [update(3, ME, ago(2), "MERGED"), approval(3, ALICE, ago(4)), comment(5, 3, BOB, ago(6)),
                         update(3, ME, ago(600))],
                "web": [changes(8, ME, ago(10))]}
        people = {"{me}": {"open": [], "merged": [slim("shop", 3, ME, ago(600), ago(2), "MERGED", closed_by=ALICE)]},
                  "{alice}": {"open": [slim("web", 8, ALICE, ago(900), ago(10))]}}
        eng = self.engine(logs, people=people)
        eng.read_activity()
        calls = len(eng.client.calls)
        answer = eng.team_activity()
        self.assertEqual(len(eng.client.calls), calls)
        self.assertEqual([(e["kind"], e["who"]["name"]) for e in answer["entries"]],
                         [("merged", "Alice Mate"), ("approved", "Alice Mate"), ("changes_requested", "Me Myself"),
                          ("opened", "Me Myself"), ("opened", "Alice Mate")],
                         "Bob is not on the team; the merge names who closed it; the update at the opening is the opening")
        self.assertEqual(answer["entries"][0]["owner"], "{me}", "the page opens the pull request through its author")
        self.assertEqual([p["uuid"] for p in answer["people"]], ["{me}", "{alice}"])
        self.assertEqual(answer["total"], 5)
        self.assertEqual([e["kind"] for e in eng.team_activity(who="{alice}")["entries"]], ["merged", "approved", "opened"])
        self.assertEqual([e["kind"] for e in eng.team_activity(who="{bob}")["entries"]][:1], ["merged"],
                         "someone off the team does not narrow it to them")
        self.assertEqual([e["repo"] for e in eng.team_activity(repo="web")["entries"]], ["web", "web"])
        self.assertEqual([e["kind"] for e in eng.team_activity(kind="approved")["entries"]], ["approved"])
        between = eng.team_activity(since=self.now - 5 * 60, until=self.now - 3 * 60)
        self.assertEqual([e["kind"] for e in between["entries"]], ["approved"])
        self.assertEqual(len(eng.team_activity(limit=2)["entries"]), 2)


class FiltersTest(unittest.TestCase):
    def test_the_page_filters_are_checked(self):
        got = teamfeed.filters({"who": "{me}", "kind": "approved", "since": "1700000000", "limit": "9999"}.get)
        self.assertEqual((got["who"], got["kind"], got["since"], got["until"], got["limit"]),
                         ("{me}", "approved", 1700000000.0, None, teamfeed.LIMIT_MAX))
        for bad in ({"kind": "deleted"}, {"since": "yesterday"}, {"who": ["{me}"]}, {"limit": "many"}):
            with self.assertRaises(ValueError, msg=bad):
                teamfeed.filters(bad.get)

    def test_merge_keeps_each_entry_once_newest_first_within_the_window(self):
        old = [{"id": "a", "at": ago(5)}, {"id": "b", "at": ago(50)}, {"id": "gone", "at": ago(60 * 24 * 40)}]
        new = [{"id": "c", "at": ago(1)}, {"id": "a", "at": ago(5), "fresh": True}]
        got = teamfeed.merge(old, new, ago(60 * 24 * 30), keep=3)
        self.assertEqual([e["id"] for e in got], ["c", "a", "b"])
        self.assertTrue(got[1].get("fresh"))
        self.assertEqual([e["id"] for e in teamfeed.merge(old, new, ago(60 * 24 * 30), keep=2)], ["c", "a"])


# ---------------------------------------------------------------- the route


class RouteTest(FeedBase):
    """Through the real server: the page's GET answers from what is kept, its POST reads."""

    def setUp(self):
        super().setUp()
        env = {"PIXELDECK_CACHE_DIR": str(self.tmp / "cache"), "PIXELDECK_STATE_DIR": str(self.tmp / "state"),
               "PIXELDECK_CREDENTIALS": "off"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        from http.server import ThreadingHTTPServer
        import server
        self.server_mod = server
        saved = (getattr(server.Desk, "engine", None), getattr(server.Desk, "port", None))
        self.eng = self.engine({"shop": [approval(3, ALICE, ago(2))], "web": []})
        server.Desk.engine = self.eng
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Desk)
        self.httpd.daemon_threads = True
        server.Desk.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

        def stop():
            self.httpd.shutdown()
            self.httpd.server_close()
            server.Desk.engine, server.Desk.port = saved
        self.addCleanup(stop)

    def call(self, method, route, body=None):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.server_mod.Desk.port}/api/{route}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Desk-Token": self.server_mod.SECRET, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_get_answers_from_the_cache_and_post_reads(self):
        code, answer = self.call("GET", "team-activity")
        self.assertEqual((code, self.eng.client.calls), (200, []))
        self.assertEqual(answer["unread"], ["shop", "web"])
        self.assertEqual([e["kind"] for e in answer["entries"]], ["opened", "opened"])
        code, answer = self.call("POST", "team-activity", {"kind": "approved"})
        self.assertEqual(code, 200)
        self.assertEqual(len(self.eng.client.calls), 2)
        self.assertEqual([(e["kind"], e["who"]["name"]) for e in answer["entries"]], [("approved", "Alice Mate")])
        self.assertIsNone(answer["error"])
        self.assertEqual(self.call("GET", "team-activity?kind=deleted")[0], 400)
        self.assertEqual(self.call("GET", "team-activity?project=NOPE")[0], 400)

    def test_post_answers_the_kept_feed_when_bitbucket_is_unreachable(self):
        self.eng.client.errors = {"shop": BitbucketError(0, "offline")}
        code, answer = self.call("POST", "team-activity", {})
        self.assertEqual((code, answer["error"]), (200, "offline"))

    def test_another_host_refuses_the_route_and_hides_the_feature(self):
        self.eng._host = type("GitHubLike", (hosts.Host,), {"id": "github", "label": "GitHub"})()
        code, answer = self.call("GET", "team-activity")
        self.assertEqual(code, 501)
        self.assertIn("GitHub", answer["error"])
        self.assertNotIn("team-activity", self.eng.host.features(self.server_mod.FEATURES))
        self.assertIn("team-activity", hosts.BitbucketHost(self.eng).features(self.server_mod.FEATURES))

    def test_the_filters_are_kept_with_the_other_page_settings(self):
        import sessions
        self.assertIn("prdesk.activity.v1", sessions.UI_KEYS)
        keep = (Path(__file__).resolve().parent.parent / "static" / "keep.js").read_text(encoding="utf-8")
        self.assertIn('"prdesk.activity.v1"', keep)


if __name__ == "__main__":
    unittest.main()
