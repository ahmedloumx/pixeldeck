"""GitHub as the code host, against a scripted transport: no network, no real token.

Every request goes to FakeGitHub, which fails the test on anything it was not told to expect,
so a test also proves which calls were made and with which headers."""

import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import account  # noqa: E402
import codehost  # noqa: E402
import engine as engine_mod  # noqa: E402
import github  # noqa: E402

API = "https://api.github.com"
TOKEN = "ghp_test-secret-token-000"


def answer(status=200, payload=None, headers=None, raw=None):
    body = raw if raw is not None else (b"" if payload is None else json.dumps(payload).encode())
    return status, {"content-type": "application/json", **(headers or {})}, body


class FakeGitHub:
    """Answers queued per (method, URL prefix), longest prefix first. The last answer repeats;
    a callable answer is asked with the URL."""

    def __init__(self):
        self.calls, self.routes = [], []

    def on(self, method, prefix, *answers):
        self.routes.insert(0, (method, prefix, list(answers)))  # a later route wins over an equal one
        self.routes.sort(key=lambda r: -len(r[1]))

    def __call__(self, method, url, headers, body):
        self.calls.append(types.SimpleNamespace(method=method, url=url, headers=dict(headers), body=body))
        for m, prefix, answers in self.routes:
            if m == method and url.startswith(prefix):
                found = answers.pop(0) if len(answers) > 1 else answers[0]
                return found(url) if callable(found) else found
        raise AssertionError(f"unexpected request {method} {url}")

    def to(self, part):
        return [c for c in self.calls if part in c.url]


def gh_user(login, name=None):
    return {"login": login, "id": hash(login) % 10000, **({"name": name} if name else {})}


def search_item(number, repo="acme/shop", author="me", state="open", updated="2026-11-10T10:00:00Z", merged_at=None,
                closed_at=None, title="Cheaper bundle"):
    return {"number": number, "title": title, "state": state, "user": gh_user(author), "draft": False,
            "repository_url": f"{API}/repos/{repo}", "html_url": f"https://github.com/{repo}/pull/{number}",
            "created_at": "2026-11-01T09:00:00Z", "updated_at": updated, "closed_at": closed_at, "comments": 1,
            "pull_request": {"merged_at": merged_at}}


def pull(number, repo="acme/shop", author="me", state="open", updated="2026-11-10T10:00:00Z", requested=(), merged_at=None,
         merged_by=None, head="feat/ABC-12-bundle", base="develop", title="Cheaper bundle"):
    return {"number": number, "title": title, "state": state, "draft": False, "user": gh_user(author), "body": "Why: cheaper.",
            "head": {"ref": head, "sha": "a" * 40}, "base": {"ref": base, "repo": {"full_name": repo}},
            "created_at": "2026-11-01T09:00:00Z", "updated_at": updated, "merged_at": merged_at, "closed_at": merged_at,
            "merged_by": gh_user(merged_by) if merged_by else None, "comments": 2, "review_comments": 3,
            "html_url": f"https://github.com/{repo}/pull/{number}", "requested_reviewers": [gh_user(u) for u in requested]}


def review(login, state, at, body=""):
    return {"id": abs(hash((login, at))) % 100000, "user": gh_user(login), "state": state, "submitted_at": at, "body": body,
            "html_url": "https://github.com/acme/shop/pull/7#review"}


def limits(remaining, limit=5000, resource="core", reset=2_000_000_000):
    return {"x-ratelimit-limit": str(limit), "x-ratelimit-remaining": str(remaining), "x-ratelimit-reset": str(reset),
            "x-ratelimit-resource": resource}


class GitHubBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pixeldeck-github-"))
        env_file = self.tmp / ".env"
        env_file.write_text(f"PIXELDECK_CODE_HOST=github\nGITHUB_TOKEN={TOKEN}\nTICKET_PREFIXES=ABC\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "GITLAB_", "BITBUCKET_", "PIXELDECK_"))}
        env.update(PIXELDECK_ENV=str(env_file), PIXELDECK_CREDENTIALS="off", PIXELDECK_CACHE_DIR=str(self.tmp / "cache"),
                   PIXELDECK_SETTINGS_FILE=str(self.tmp / "settings.json"), PIXELDECK_STATE_DIR=str(self.tmp / "state"))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env_file = env_file
        self.now = [1_900_000_000.0]
        self.fake = FakeGitHub()

    def host(self, engine=None):
        return github.GitHubHost(engine or types.SimpleNamespace(person=lambda uuid: None),
                                 transport=self.fake, clock=lambda: self.now[0])

    def engine(self):
        eng = engine_mod.Engine(self.tmp)
        self.assertEqual(eng.host.id, "github")
        eng._host = self.host(eng)
        return eng

    def everything_sent(self):
        return json.dumps([[c.url, c.headers, (c.body or b"").decode()] for c in self.fake.calls])


class MappingTest(GitHubBase):
    def test_an_open_pull_request_reads_as_the_page_expects(self):
        self.fake.on("GET", f"{API}/search/issues", answer(200, {"total_count": 1, "items": [search_item(7)]}))
        self.fake.on("GET", f"{API}/repos/acme/shop/pulls/7", answer(200, pull(7, requested=["carol", "dave"])))
        self.fake.on("GET", f"{API}/repos/acme/shop/pulls/7/reviews", answer(200, [
            review("alice", "CHANGES_REQUESTED", "2026-11-02T10:00:00Z"),
            review("alice", "COMMENTED", "2026-11-03T10:00:00Z", "one more thing"),  # a comment keeps the verdict
            review("bob", "APPROVED", "2026-11-02T11:00:00Z"),
            review("eve", "APPROVED", "2026-11-02T11:00:00Z"), review("eve", "DISMISSED", "2026-11-04T11:00:00Z"),
            review("dave", "COMMENTED", "2026-11-02T12:00:00Z"),
            review("me", "COMMENTED", "2026-11-02T12:00:00Z"),  # the author's own review is no review
            review("frank", "PENDING", "2026-11-02T12:00:00Z"),  # a draft only frank sees
        ]))
        [pr] = self.host().prs("me", "OPEN", max_pages=4)
        self.assertEqual({k: pr[k] for k in ("repo", "id", "state", "source", "dest", "comment_count", "task_count", "url")},
                         {"repo": "acme/shop", "id": 7, "state": "OPEN", "source": "feat/ABC-12-bundle", "dest": "develop",
                          "comment_count": 5, "task_count": 0, "url": "https://github.com/acme/shop/pull/7"})
        self.assertEqual(pr["author"]["uuid"], "me")
        self.assertEqual(pr["ticket"]["key"], "ABC-12")
        self.assertEqual(sorted(pr), sorted(["repo", "id", "title", "state", "draft", "author", "source", "dest", "source_commit",
                                             "created_on", "updated_on", "comment_count", "task_count", "closed_by", "url",
                                             "reviews", "ticket"]), "the same keys as a Bitbucket pull request")
        states = {r["uuid"]: (r["state"], r["reviewer"]) for r in pr["reviews"]}
        self.assertEqual(states, {"alice": ("changes_requested", True), "bob": ("approved", True), "carol": ("pending", True),
                                  "dave": ("pending", True), "eve": ("commented", False)})
        self.assertTrue(next(r for r in pr["reviews"] if r["uuid"] == "dave").get("commented"))
        query = self.fake.to("/search/issues")[0].url
        self.assertIn("is%3Aopen", query)
        self.assertIn("author%3Ame", query)

    def test_a_bot_is_searched_by_its_app_and_a_bad_login_is_refused(self):
        self.fake.on("GET", f"{API}/search/issues", answer(200, {"items": []}))
        self.host().prs("dependabot[bot]", "OPEN")
        self.assertIn("author%3Aapp%2Fdependabot", self.fake.calls[0].url)
        with self.assertRaises(ValueError):
            self.host().prs("me is:merged", "OPEN")

    def test_the_organization_scopes_every_search(self):
        self.env_file.write_text(self.env_file.read_text() + "GITHUB_ORG=acme\n")
        self.fake.on("GET", f"{API}/search/issues", answer(200, {"items": []}))
        self.host().prs("me", "OPEN")
        self.assertIn("org%3Aacme", self.fake.calls[0].url)

    def test_merged_ones_date_by_the_merge_and_only_a_few_are_read_in_full(self):
        items = [search_item(n, state="closed", merged_at=f"2026-11-{10 + n:02d}T08:00:00Z", updated="2026-11-30T00:00:00Z")
                 for n in range(1, 13)]
        self.fake.on("GET", f"{API}/search/issues", answer(200, {"total_count": 12, "items": items}))

        def read(url):
            tail = url.split("/pulls/", 1)[1].split("?")[0]
            if tail.endswith("/reviews"):
                return answer(200, [])
            n = int(tail)
            return answer(200, pull(n, state="closed", merged_at=f"2026-11-{10 + n:02d}T08:00:00Z", merged_by="bob"))
        self.fake.on("GET", f"{API}/repos/acme/shop/pulls/", read)
        meta = {}
        out = self.host().prs("me", "MERGED", newer_than="2026-11-12T00:00:00+00:00", max_pages=3, meta=meta)
        self.assertEqual(meta["size"], 12)
        self.assertTrue(all(p["updated_on"] >= "2026-11-12" for p in out), "filtered to the second, after a search by day")
        self.assertEqual(len(out), 11)
        self.assertIn("merged%3A%3E%3D2026-11-12", self.fake.to("/search/issues")[0].url)
        full_reads = [c for c in self.fake.calls if "/pulls/" in c.url and "/reviews" not in c.url]
        self.assertEqual(len(full_reads), github.CLOSED_READS)
        self.assertEqual(sum(1 for p in out if p["closed_by"]), github.CLOSED_READS - 1, "who merged it, from the full reads")

    def test_a_merged_one_the_cache_already_read_costs_no_call(self):
        known = dict(github.slim_pull(pull(5, state="closed", merged_at="2026-11-15T08:00:00Z", merged_by="bob"), []))
        person = {"merged": [known], "declined": []}
        host = self.host(types.SimpleNamespace(person=lambda uuid: person))
        self.fake.on("GET", f"{API}/search/issues", answer(200, {"items": [
            search_item(5, state="closed", merged_at="2026-11-15T08:00:00Z")]}))
        [pr] = host.prs("me", "MERGED")
        self.assertIs(pr, known)
        self.assertEqual(len(self.fake.calls), 1)

    def test_an_unreadable_repository_keeps_the_search_answer(self):
        self.fake.on("GET", f"{API}/search/issues", answer(200, {"items": [search_item(3, repo="other/locked")]}))
        self.fake.on("GET", f"{API}/repos/other/locked/pulls/3", answer(403, {"message": "Resource not accessible by personal access token"},
                                                                        limits(4000)))
        [pr] = self.host().prs("me", "OPEN")
        self.assertEqual((pr["repo"], pr["id"], pr["source"], pr["reviews"]), ("other/locked", 3, "", []))


class DetailTest(GitHubBase):
    def _detail_routes(self, graphql):
        base = f"{API}/repos/acme/shop"
        self.fake.on("GET", f"{base}/pulls/7", answer(200, pull(7)))
        self.fake.on("GET", f"{base}/pulls/7/reviews", answer(200, [review("alice", "COMMENTED", "2026-11-02T10:00:00Z", "Overall fine")]))
        self.fake.on("GET", f"{base}/pulls/7/comments", answer(200, [
            {"id": 11, "path": "app.py", "line": 4, "side": "RIGHT", "position": 3, "user": gh_user("alice"), "body": "Why?",
             "created_at": "2026-11-02T10:00:00Z", "updated_at": "2026-11-02T10:00:00Z", "html_url": "u11"},
            {"id": 12, "in_reply_to_id": 11, "path": "app.py", "line": 4, "user": gh_user("me"), "body": "Because.",
             "created_at": "2026-11-02T11:00:00Z", "updated_at": "2026-11-02T11:00:00Z"},
            {"id": 21, "path": "db.py", "line": 9, "side": "RIGHT", "position": 5, "user": gh_user("bob"), "body": "Index?",
             "created_at": "2026-11-03T10:00:00Z", "updated_at": "2026-11-03T10:00:00Z"},
            {"id": 31, "path": "old.py", "line": None, "position": None, "original_line": 2, "user": gh_user("bob"),
             "body": "Gone now", "created_at": "2026-11-01T10:00:00Z", "updated_at": "2026-11-01T10:00:00Z"},
        ]))
        self.fake.on("GET", f"{API}/repos/acme/shop/issues/7/comments", answer(200, [
            {"id": 41, "user": gh_user("carol"), "body": "Nice", "created_at": "2026-11-04T10:00:00Z", "updated_at": "2026-11-04T10:00:00Z"}]))
        self.fake.on("POST", f"{API}/graphql", graphql)

    def test_threads_resolved_by_graphql_and_counted_like_bitbucket(self):
        self._detail_routes(answer(200, {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
            {"isResolved": True, "resolvedBy": {"login": "me"}, "comments": {"nodes": [{"databaseId": 11}]}},
            {"isResolved": False, "resolvedBy": None, "comments": {"nodes": [{"databaseId": 21}]}},
            {"isResolved": False, "resolvedBy": None, "comments": {"nodes": [{"databaseId": 31}]}}]}}}}}))
        eng = self.engine()
        out = eng.detail("acme/shop", 7)
        threads = {str(t["id"]): t for t in out["threads"]}
        self.assertEqual(out["description"], "Why: cheaper.")
        self.assertEqual(out["tasks"], [])
        self.assertTrue(threads["11"]["resolved"])
        self.assertEqual(threads["11"]["resolved_by"]["uuid"], "me")
        self.assertEqual([m["raw"] for m in threads["11"]["messages"]], ["Why?", "Because."])
        self.assertTrue(threads["21"]["awaiting_author"])
        self.assertTrue(threads["31"]["outdated"])
        self.assertEqual(threads["issue-41"]["inline"], False)
        self.assertIn("review-", " ".join(threads))
        self.assertEqual(out["attention"], {"unresolved": 2, "awaiting": 2, "tasks_open": 0, "threads": 5})
        sent = self.fake.to("/graphql")[0]
        self.assertEqual(json.loads(sent.body)["variables"], {"owner": "acme", "name": "shop", "number": 7})

    def test_without_graphql_an_outdated_thread_counts_as_settled(self):
        self._detail_routes(answer(200, {"errors": [{"message": "Resource not accessible"}]}))
        out = self.host().detail("acme/shop", 7)
        unresolved = sorted(str(t["id"]) for t in out["threads"] if t["unresolved"])
        self.assertEqual(unresolved, ["11", "21"])

    def test_diff_commits_and_behind(self):
        base = f"{API}/repos/acme/shop"
        self.fake.on("GET", f"{base}/pulls/7", answer(200, raw=b"diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"))
        text, cut = self.host().diff("acme/shop", 7)
        self.assertFalse(cut)
        self.assertEqual(self.fake.calls[-1].headers["Accept"], "application/vnd.github.diff")
        self.assertNotIn("If-None-Match", self.fake.calls[-1].headers)
        self.fake.on("GET", f"{base}/pulls/7/commits", answer(200, [
            {"sha": "1" * 40, "commit": {"message": "first\n\nbody", "author": {"name": "Me", "date": "2026-11-01T00:00:00Z"}}, "author": gh_user("me"),
             "html_url": "https://github.com/acme/shop/commit/111"},
            {"sha": "2" * 40, "commit": {"message": "second", "author": {"name": "Me", "date": "2026-11-02T00:00:00Z"}}, "author": None}]))
        commits = self.host().commits("acme/shop", 7)
        self.assertEqual([(c["sha"], c["subject"], c["author"]) for c in commits], [("22222222", "second", "Me"), ("11111111", "first", "me")])
        self.assertEqual(commits[1]["url"], "https://github.com/acme/shop/commit/111", "each commit links to its own page")
        self.fake.on("GET", f"{base}/compare/develop...feat/x", answer(200, {"behind_by": 3, "ahead_by": 1}))
        self.assertEqual(self.host().behind({"repo": "acme/shop", "id": 7, "source": "feat/x", "dest": "develop"}), (3, False))
        with self.assertRaises(ValueError):
            self.host().diff("../../etc", 7)


class TransportTest(GitHubBase):
    def test_pages_follow_the_link_header_on_this_host_only(self):
        first = answer(200, [{"login": "a"}], {"link": f'<{API}/orgs/acme/members?page=2>; rel="next", <{API}/x>; rel="last"'})
        second = answer(200, [{"login": "b"}], {"link": '<https://evil.example/orgs/acme/members?page=3>; rel="next"'})
        self.fake.on("GET", f"{API}/orgs/acme/members", first, second)
        api = github.GitHubApi(API, TOKEN, transport=self.fake)
        self.assertEqual([m["login"] for m in api.paged("/orgs/acme/members")], ["a", "b"])
        self.assertEqual(len(self.fake.calls), 2, "the page on another server is not asked for")

    def test_an_etag_makes_the_next_read_conditional_and_a_304_answers_from_memory(self):
        self.fake.on("GET", f"{API}/repos/acme/shop/pulls/7",
                     answer(200, pull(7), {"etag": 'W/"abc"', **limits(4990)}),
                     answer(304, None, limits(4990)))
        api = github.GitHubApi(API, TOKEN, transport=self.fake)
        first = api.get("/repos/acme/shop/pulls/7")
        second = api.get("/repos/acme/shop/pulls/7")
        self.assertEqual(first, second)
        self.assertNotIn("If-None-Match", self.fake.calls[0].headers)
        self.assertEqual(self.fake.calls[1].headers["If-None-Match"], 'W/"abc"')
        self.assertEqual(api.budget(), {"used": 10, "limit": 5000})

    def test_the_budget_meter_is_githubs_own_count_and_search_has_its_own(self):
        self.fake.on("GET", f"{API}/user", answer(200, gh_user("me", "Me Myself"), limits(4321)))
        self.fake.on("GET", f"{API}/orgs", answer(200, []))
        eng = self.engine()
        self.assertEqual(eng.budget(), {"used": 0, "limit": 5000}, "nothing asked yet")
        eng.refresh_identity()
        self.assertEqual(eng.me()["name"], "Me Myself")
        self.assertEqual(eng.budget(), {"used": 679, "limit": 5000})
        self.fake.on("GET", f"{API}/search/issues", answer(200, {"items": []}, limits(25, limit=30, resource="search")))
        eng.host.prs("me", "OPEN")
        self.assertEqual(eng.budget(), {"used": 679, "limit": 5000}, "the search limit is not the hourly one")
        self.assertFalse(eng.host.busy())
        self.fake.on("GET", f"{API}/user", answer(200, gh_user("me"), limits(900)))
        eng.refresh_identity()
        self.assertTrue(eng.host.busy(), "past 80 percent the background refresh pauses, as for Bitbucket")
        self.assertTrue(eng.schedule()["paused"])

    def test_a_spent_limit_is_not_a_refused_token_and_is_waited_out(self):
        self.fake.on("GET", f"{API}/user", answer(403, {"message": "API rate limit exceeded for user."},
                                                  limits(0, reset=int(self.now[0]) + 120)))
        eng = self.engine()
        with self.assertRaises(codehost.HostError) as caught:
            eng.refresh_identity()
        self.assertEqual(caught.exception.status, 429)
        self.assertIsNot(eng.auth.get("ok"), False, "a spent limit must not ask you for a new token")
        calls = len(self.fake.calls)
        with self.assertRaises(codehost.HostError) as again:
            eng.host.me()
        self.assertEqual(len(self.fake.calls), calls, "no call until the reset")
        self.assertIn("resets in 120 s", again.exception.message)
        self.now[0] += 121
        self.fake.on("GET", f"{API}/user", answer(200, gh_user("me"), limits(4999)))
        self.assertEqual(eng.host.me()["uuid"], "me")

    def test_a_secondary_limit_waits_for_retry_after(self):
        self.fake.on("GET", f"{API}/search/issues", answer(403, {"message": "You have exceeded a secondary rate limit"},
                                                           {"retry-after": "30"}))
        host = self.host()
        with self.assertRaises(codehost.HostError) as caught:
            host.prs("me", "OPEN")
        self.assertEqual(caught.exception.status, 429)
        with self.assertRaises(codehost.HostError):
            host.prs("me", "OPEN")
        self.assertEqual(len(self.fake.calls), 1)

    def test_a_refused_token_is_an_auth_failure(self):
        self.fake.on("GET", f"{API}/user", answer(401, {"message": "Bad credentials"}))
        eng = self.engine()
        with self.assertRaises(codehost.HostError):
            eng.refresh_identity()
        self.assertEqual((eng.auth["ok"], eng.auth["status"], eng.auth["error"]), (False, 401, "Bad credentials"))

    def test_no_token_asks_for_setup_without_a_call(self):
        self.env_file.write_text("PIXELDECK_CODE_HOST=github\n")
        eng = self.engine()
        with self.assertRaises(codehost.HostError):
            eng.refresh_identity()
        self.assertFalse(eng.auth["ok"])
        self.assertEqual(self.fake.calls, [])

    def test_the_token_goes_to_github_only(self):
        self.fake.on("GET", f"{API}/repos/acme/shop/pulls/7", answer(301, None, {"location": "https://evil.example/steal"}))
        api = github.GitHubApi(API, TOKEN, transport=self.fake)
        with self.assertRaises(codehost.HostError) as caught:
            api.get("/repos/acme/shop/pulls/7")
        self.assertEqual(len(self.fake.calls), 1)
        self.assertNotIn(TOKEN, caught.exception.message)
        with self.assertRaises(codehost.HostError):
            api.get("https://evil.example/repos")
        self.assertEqual(len(self.fake.calls), 1, "an address on another server is refused before a request")
        self.assertEqual(self.fake.calls[0].headers["Authorization"], f"Bearer {TOKEN}")

    def test_a_renamed_repository_is_followed_on_the_same_host(self):
        self.fake.on("GET", f"{API}/repos/acme/shop/pulls/7", answer(301, None, {"location": f"{API}/repositories/42/pulls/7"}))
        self.fake.on("GET", f"{API}/repositories/42/pulls/7", answer(200, pull(7)))
        api = github.GitHubApi(API, TOKEN, transport=self.fake)
        self.assertEqual(api.get("/repos/acme/shop/pulls/7")["number"], 7)

    def test_only_https_or_this_machine_gets_a_token(self):
        self.assertEqual(codehost.check_base("https://ghe.example.com/api/v3/", "GitHub"), "https://ghe.example.com/api/v3")
        self.assertEqual(codehost.check_base("http://127.0.0.1:9000", "GitHub"), "http://127.0.0.1:9000")
        for bad in ("http://ghe.example.com/api/v3", "ftp://x", "https://u:p@ghe.example.com", "https://ghe.example.com/?a=b", ""):
            with self.assertRaises(ValueError, msg=bad):
                codehost.check_base(bad, "GitHub")
        self.assertEqual(github.GitHubApi("https://ghe.example.com/api/v3", TOKEN).graphql_url(), "https://ghe.example.com/api/graphql")
        self.assertEqual(github.GitHubApi(API, TOKEN).graphql_url(), f"{API}/graphql")


class EngineTest(GitHubBase):
    def test_your_lists_and_review_requests_from_outside_the_team(self):
        requested = []

        def search(url):
            if "review-requested" in url:
                return answer(200, {"items": list(requested)})
            return answer(200, {"items": [search_item(7)] if "is%3Aopen" in url else [], "total_count": 0})
        self.fake.on("GET", f"{API}/search/issues", search)
        self.fake.on("GET", f"{API}/user", answer(200, gh_user("me", "Me Myself"), limits(4990)))
        self.fake.on("GET", f"{API}/repos/acme/shop/pulls/7", answer(200, pull(7)))
        self.fake.on("GET", f"{API}/repos/acme/shop/pulls/7/reviews", answer(200, []))
        self.fake.on("GET", f"{API}/repos/team/app/pulls/9", answer(200, pull(9, repo="team/app", author="zed", requested=["me"])))
        self.fake.on("GET", f"{API}/repos/team/app/pulls/9/reviews", answer(200, []))
        eng = self.engine()
        eng.refresh_identity()
        entry = eng.refresh_person("me")
        self.assertEqual([(p["repo"], p["id"], p["source"]) for p in entry["open"]], [("acme/shop", 7, "feat/ABC-12-bundle")])
        self.assertEqual(eng.asked(), [])
        requested.append(search_item(9, repo="team/app", author="zed"))
        self.now[0] += 10
        eng.refresh_open("me")
        self.assertEqual([(p["repo"], p["id"]) for p in eng.asked()], [("team/app", 9)])
        self.assertEqual([(e["kind"], e["repo"]) for e in eng.events.read()["events"]], [("review_requested", "team/app")])
        self.assertEqual(eng.find_pr("team/app", 9)["author"]["uuid"], "zed", "a pull request asking you is found to open it")
        reads = len(self.fake.to("/pulls/7"))
        eng.refresh_open("me")
        self.assertEqual(len(self.fake.to("/pulls/7")), reads, "an unmoved pull request is not read again")
        self.assertEqual(len(eng.events.read()["events"]), 1, "the same request is news once")
        self.assertTrue(eng.cache_file.name.endswith("cache-github.json"))
        self.assertTrue(eng.settings_file.name.endswith("settings-github.json"))
        self.assertNotIn(TOKEN, eng.cache_file.read_text())
        self.assertNotIn(TOKEN, json.dumps(eng.data))


class BitbucketStaysTest(GitHubBase):
    def test_bitbucket_is_the_default_with_its_own_files_and_every_route(self):
        self.env_file.write_text("BITBUCKET_WORKSPACE=acme\n")
        eng = engine_mod.Engine(self.tmp)
        self.assertEqual(eng.host.id, "bitbucket")
        self.assertEqual(eng.cache_file.name, "cache.json")
        self.assertEqual(eng.settings_file.name, "settings.json")
        self.assertTrue(all(eng.host.serves(r) for r in ("merge", "review", "sync", "repos", "repo-file", "pr", "events")))
        self.assertEqual(eng.host.features(["merge", "reviews", "events"]), ["merge", "reviews", "events"])
        self.assertIsNone(eng.host.review_requests("{me}"))

    def test_github_refuses_the_bitbucket_writes_and_hides_their_buttons(self):
        host = self.host()
        for route in ("merge", "merge-options", "decline", "review", "sync", "pr-create", "propose", "repos", "repo-tree", "repo-commit"):
            self.assertFalse(host.serves(route), route)
        for route in ("pr", "pr-diff", "pr-extra", "person", "team", "events", "launch", "setup", "settings"):
            self.assertTrue(host.serves(route), route)
        self.assertEqual(host.features(["merge", "sync", "reviews", "events", "jira", "repos"]), ["events", "jira"])

    def test_switching_hosts_keeps_each_ones_cache(self):
        self.env_file.write_text("BITBUCKET_WORKSPACE=acme\n")
        eng = engine_mod.Engine(self.tmp)
        eng.data["me"] = {"uuid": "{bb}"}
        eng._save()
        self.env_file.write_text("PIXELDECK_CODE_HOST=github\n")
        self.assertTrue(eng.use_host())
        self.assertEqual((eng.host.id, eng.me()), ("github", None))
        self.assertFalse(eng.use_host(), "no switch when nothing changed")
        self.env_file.write_text("PIXELDECK_CODE_HOST=bitbucket\n")
        self.assertTrue(eng.use_host())
        self.assertEqual(eng.me(), {"uuid": "{bb}"})


class ClonesTest(GitHubBase):
    def test_a_clone_is_found_by_its_github_origin_in_any_form(self):
        import gitscan
        for url in ("git@github.com:acme/shop.git", "https://github.com/acme/shop", "ssh://git@github.com/acme/shop.git"):
            self.assertEqual(gitscan.remote_repo(url), "acme/shop", url)
        for url in ("https://github.com/acme", "https://evil.example/acme/shop", "git@gitlab.com:acme/shop.git", "git@github.com:../x"):
            self.assertIsNone(gitscan.remote_repo(url), url)
        self.env_file.write_text(self.env_file.read_text() + "GITHUB_URL=https://ghe.example.com/api/v3\n")
        self.assertEqual(gitscan.remote_repo("git@ghe.example.com:acme/shop.git"), "acme/shop", "Enterprise clones by its host")
        self.assertIsNone(gitscan.remote_repo("git@github.com:acme/shop.git"))


class SetupTest(GitHubBase):
    def test_saving_github_keeps_bitbucket_and_never_shows_the_token(self):
        self.env_file.write_text("BITBUCKET_WORKSPACE=acme\nBITBUCKET_TOKEN=bb-secret\n# kept\n")
        out = account.save({"host": "github", "token": "ghp_new-secret", "org": "acme", "url": "https://api.github.com/", "root": ""})
        text = self.env_file.read_text()
        self.assertIn("PIXELDECK_CODE_HOST=github", text)
        self.assertIn("GITHUB_ORG=acme", text)
        self.assertNotIn("GITHUB_URL", text, "the default address is not written down")
        self.assertIn("BITBUCKET_TOKEN=bb-secret", text)
        self.assertIn("# kept", text)
        self.assertEqual((out["host"], out["needed"], out["github"]["token"], out["github"]["org"]), ("github", False, "file", "acme"))
        self.assertNotIn("secret", json.dumps(out))
        back = account.save({"host": "bitbucket", "email": "", "token": "", "workspace": "acme", "root": ""})
        self.assertEqual(back["host"], "bitbucket")
        self.assertNotIn("PIXELDECK_CODE_HOST", self.env_file.read_text())
        self.assertIn("GITHUB_TOKEN=ghp_new-secret", self.env_file.read_text(), "kept for a switch back")

    def test_bad_values_are_refused_before_anything_is_written(self):
        before = self.env_file.read_text()
        for body in ({"host": "github", "token": "a b"}, {"host": "github", "token": "t", "url": "http://ghe.example.com"},
                     {"host": "github", "token": "t", "org": "no spaces"}, {"host": "svn", "token": "t"}):
            with self.assertRaises(ValueError, msg=body):
                account.save(body)
        self.assertEqual(self.env_file.read_text(), before)

    def test_check_tries_each_read_and_names_the_permission(self):
        self.fake.on("GET", f"{API}/user", answer(200, gh_user("me")))
        self.fake.on("GET", f"{API}/orgs/acme", answer(200, {"login": "acme"}))
        self.fake.on("GET", f"{API}/search/issues", answer(200, {"items": [search_item(7)]}))
        self.fake.on("GET", f"{API}/repos/acme/shop/pulls", answer(200, []))
        self.fake.on("GET", f"{API}/repos/acme/shop/commits", answer(403, {"message": "Resource not accessible by personal access token"}))
        out = github.check({"token": "ghp_typed-secret", "org": "acme"}, transport=self.fake)
        self.assertFalse(out["ok"])
        self.assertEqual([(r["label"], r["ok"]) for r in out["results"]],
                         [("Your user", True), ("The organization", True), ("Pull requests", True), ("Code, for diffs", False)])
        self.assertIn("Contents: read", out["results"][-1]["hint"])
        self.assertEqual(out["me"], "me")
        self.assertNotIn("secret", json.dumps(out))
        self.assertTrue(all(c.url.startswith(API) for c in self.fake.calls))

    def test_a_wrong_token_stops_the_check_at_once(self):
        self.fake.on("GET", f"{API}/user", answer(401, {"message": "Bad credentials"}))
        out = github.check({"token": "nope"}, transport=self.fake)
        self.assertEqual([(r["label"], r["ok"], r["hint"]) for r in out["results"]], [("Your user", False, "the token is wrong or expired")])
        self.env_file.write_text("PIXELDECK_CODE_HOST=github\n")
        with self.assertRaises(ValueError):
            github.check({"token": ""}, transport=self.fake)


if __name__ == "__main__":
    unittest.main()
