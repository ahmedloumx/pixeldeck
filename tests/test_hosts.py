"""Where your code lives: GitHub and GitLab answers in Bitbucket's shapes, clones found by their
remote on each host, the actions sent to the host's client, and the setup that picks a host."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env: they get settings of their own, and no token.
_HOME = Path(tempfile.mkdtemp(prefix="pixeldeck-test-"))
_ENV = _HOME / ".env"
_ENV.write_text("BITBUCKET_WORKSPACE=acme\n")
os.environ["PIXELDECK_ENV"] = str(_ENV)
os.environ["PIXELDECK_CREDENTIALS"] = "off"
for _key in ("CODE_HOST", "BITBUCKET_TOKEN", "BITBUCKET_EMAIL", "BITBUCKET_WORKSPACE", "GITHUB_TOKEN", "GITHUB_OWNER",
             "GITHUB_API_URL", "GITLAB_TOKEN", "GITLAB_GROUP", "GITLAB_URL", "PIXELDECK_WORKSPACE_ROOT"):
    os.environ.pop(_key, None)

import account  # noqa: E402
import actions  # noqa: E402
import config  # noqa: E402
import credentials  # noqa: E402
import engine as engine_mod  # noqa: E402
import forge  # noqa: E402
import github  # noqa: E402
import gitlab  # noqa: E402
import model  # noqa: E402
import rest  # noqa: E402
from bitbucket import BitbucketError  # noqa: E402


def settings(**values):
    """Point PixelDeck at a settings file holding these values, for one test."""
    path = _HOME / f"{len(list(_HOME.iterdir()))}.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    config._cache.update(path=None, mtime=None, values={})
    return mock.patch.dict(os.environ, {"PIXELDECK_ENV": str(path)})


class FakeStore(credentials.Store):
    name = "Test Store"

    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


# ---- the host and its addresses -------------------------------------------------------------

class HostTest(unittest.TestCase):
    def test_bitbucket_is_the_default_and_an_unknown_host_falls_back_to_it(self):
        with settings(BITBUCKET_WORKSPACE="acme"):
            self.assertEqual((config.host(), config.workspace(), forge.label()), ("bitbucket", "acme", "Bitbucket"))
        with settings(CODE_HOST="sourceforge", BITBUCKET_WORKSPACE="acme"):
            self.assertEqual(config.host(), "bitbucket")

    def test_each_host_names_its_own_workspace(self):
        with settings(CODE_HOST="GitHub", GITHUB_OWNER="octo-org", BITBUCKET_WORKSPACE="acme"):
            self.assertEqual((config.host(), config.workspace()), ("github", "octo-org"))
            self.assertIsInstance(forge.client(), github.Client)
            self.assertIsNone(config.default_project(), "projects are Bitbucket's")
        with settings(CODE_HOST="gitlab", GITLAB_GROUP="/acme/platform/"):
            self.assertEqual(config.workspace(), "acme/platform")
            self.assertIsInstance(forge.client(), gitlab.Client)

    def test_a_settings_file_with_a_byte_order_mark_keeps_its_first_key(self):
        # PowerShell's Set-Content -Encoding utf8 and some editors write one; it hid CODE_HOST.
        path = _HOME / "bom.env"
        path.write_bytes("﻿CODE_HOST=github\nGITHUB_OWNER=octo\n".encode("utf-8"))
        config._cache.update(path=None, mtime=None, values={})
        with mock.patch.dict(os.environ, {"PIXELDECK_ENV": str(path)}):
            self.assertEqual((config.host(), config.workspace()), ("github", "octo"))
            config.save({"GITHUB_OWNER": "other"})
            self.assertEqual(config.parse_env(path.read_text(encoding="utf-8")), {"CODE_HOST": "github", "GITHUB_OWNER": "other"},
                             "a save keeps the first key once, without the mark")

    def test_a_missing_setting_reads_as_one(self):
        self.assertTrue(forge.missing_setting(BitbucketError(0, "No GITHUB_TOKEN in /x/.env")))
        self.assertTrue(forge.missing_setting(BitbucketError(0, "No BITBUCKET_WORKSPACE in /x/.env")))
        self.assertFalse(forge.missing_setting(BitbucketError(0, "connection refused")))
        self.assertFalse(forge.missing_setting(BitbucketError(401, "No GITHUB_TOKEN in /x/.env")))


class RemoteTest(unittest.TestCase):
    def test_bitbucket_remotes_read_as_they_always_did(self):
        for url in ("git@bitbucket.org:acme/shop-api.git", "https://me@bitbucket.org/acme/shop-api.git"):
            self.assertEqual(forge.remote_slug(url, "acme", "bitbucket"), "shop-api")
        self.assertIsNone(forge.remote_slug("git@bitbucket.org:other/shop-api.git", "acme", "bitbucket"))

    def test_github_remotes_by_https_ssh_and_scp_and_any_case_of_the_owner(self):
        with settings(CODE_HOST="github", GITHUB_OWNER="Octo-Org"):
            for url in ("https://github.com/octo-org/shop.git", "git@github.com:Octo-Org/shop.git",
                        "ssh://git@ssh.github.com:443/octo-org/shop.git", "https://github.com/octo-org/shop"):
                self.assertEqual(forge.remote_slug(url), "shop", url)
            for url in ("https://github.com/someone/shop.git", "https://gitlab.com/octo-org/shop.git",
                        "https://github.com/octo-org/shop/extra.git", "C:/clones/octo-org/shop"):
                self.assertIsNone(forge.remote_slug(url), url)

    def test_github_enterprise_is_found_on_its_own_server(self):
        with settings(CODE_HOST="github", GITHUB_OWNER="team", GITHUB_API_URL="https://ghe.example.com/api/v3"):
            self.assertEqual(forge.web_host(), "ghe.example.com")
            self.assertEqual(forge.remote_slug("git@ghe.example.com:team/app.git"), "app")
            self.assertIsNone(forge.remote_slug("git@github.com:team/app.git"))

    def test_gitlab_keeps_a_subgroup_path_and_a_server_under_a_path(self):
        with settings(CODE_HOST="gitlab", GITLAB_GROUP="acme"):
            self.assertEqual(forge.remote_slug("git@gitlab.com:acme/platform/shop-api.git"), "platform/shop-api")
        with settings(CODE_HOST="gitlab", GITLAB_GROUP="acme", GITLAB_URL="https://example.com:8443/gitlab"):
            self.assertEqual(forge.remote_slug("https://example.com:8443/gitlab/acme/shop.git"), "shop")
            self.assertEqual(forge.remote_slug("ssh://git@example.com:2222/acme/shop.git"), "shop")

    def test_each_host_opens_a_pull_request_on_its_own_page(self):
        with settings(BITBUCKET_WORKSPACE="acme"):
            self.assertEqual(forge.new_pr_url("api", "feat/x", "develop"),
                             "https://bitbucket.org/acme/api/pull-requests/new?source=feat/x&dest=develop")
        with settings(CODE_HOST="github", GITHUB_OWNER="octo"):
            self.assertEqual(forge.new_pr_url("api", "feat/x", "main"), "https://github.com/octo/api/compare/main...feat/x?expand=1")
        with settings(CODE_HOST="gitlab", GITLAB_GROUP="acme"):
            self.assertEqual(forge.new_pr_url("api", "feat/x", "main"), "https://gitlab.com/acme/api/-/merge_requests/new?"
                             "merge_request%5Bsource_branch%5D=feat%2Fx&merge_request%5Btarget_branch%5D=main")

    def test_pages_are_followed_only_under_the_same_api(self):
        base = "https://api.github.com"
        self.assertEqual(rest.next_link('<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=9>; rel="last"', base),
                         "https://api.github.com/x?page=2")
        self.assertIsNone(rest.next_link('<https://evil.example/x?page=2>; rel="next"', base))
        self.assertTrue(rest.same_api("https://gitlab.example.com/api/v4/groups", "https://gitlab.example.com/api/v4"))
        self.assertFalse(rest.same_api("https://gitlab.example.com/api/v4evil/x", "https://gitlab.example.com/api/v4"))
        self.assertFalse(rest.same_api("http://api.github.com/x", base))


# ---- GitHub in Bitbucket's shapes ---------------------------------------------------------------

def gh_pr(**over):
    raw = {"number": 7, "title": "Cart totals (SHOP-12)", "state": "open", "draft": False, "body": "Totals.",
           "user": {"login": "me", "id": 1}, "html_url": "https://github.com/octo/shop/pull/7",
           "created_at": "2026-11-01T10:00:00Z", "updated_at": "2026-11-02T10:00:00Z", "comments": 2, "review_comments": 3,
           "head": {"ref": "feat/SHOP-12-cart", "sha": "abc123", "repo": {"full_name": "octo/shop"}},
           "base": {"ref": "main", "repo": {"name": "shop", "full_name": "octo/shop", "owner": {"login": "octo"}}},
           "requested_reviewers": [{"login": "bob", "id": 3}], "merged_at": None, "merged_by": None}
    raw.update(over)
    return raw


def review(login, state, at):
    return {"user": {"login": login, "id": hash(login) % 1000}, "state": state, "submitted_at": at, "id": hash((login, at)) % 10000}


class GitHubShapeTest(unittest.TestCase):
    def test_a_pull_request_reads_like_bitbucket_s(self):
        reviews = [review("ann", "APPROVED", "2026-11-02T09:00:00Z"), review("ann", "COMMENTED", "2026-11-02T09:30:00Z"),
                   review("cid", "CHANGES_REQUESTED", "2026-11-02T08:00:00Z"), review("cid", "DISMISSED", "2026-11-02T08:30:00Z"),
                   review("dee", "CHANGES_REQUESTED", "2026-11-02T07:00:00Z"), review("me", "COMMENTED", "2026-11-02T07:30:00Z")]
        pr = model.slim_pr(github.pr_shape(gh_pr(), reviews, "shop"))
        self.assertEqual((pr["repo"], pr["id"], pr["state"], pr["source"], pr["dest"], pr["source_commit"]),
                         ("shop", 7, "OPEN", "feat/SHOP-12-cart", "main", "abc123"))
        self.assertEqual((pr["comment_count"], pr["url"]), (5, "https://github.com/octo/shop/pull/7"))
        states = {r["uuid"]: r["state"] for r in pr["reviews"]}
        self.assertEqual(states, {"ann": "approved", "cid": "commented", "dee": "changes_requested", "bob": "pending"},
                         "a later comment keeps an approval; a dismissal takes a verdict back; the author is no reviewer")

    def test_merged_and_closed(self):
        merged = model.slim_pr(github.pr_shape(gh_pr(state="closed", merged_at="2026-11-03T10:00:00Z", merged_by={"login": "ann"}), [], "shop"))
        self.assertEqual((merged["state"], merged["closed_by"]["uuid"]), ("MERGED", "ann"))
        self.assertEqual(model.slim_pr(github.pr_shape(gh_pr(state="closed"), [], "shop"))["state"], "DECLINED")

    def test_review_threads_keep_their_resolved_state_and_side(self):
        threads = [
            {"isResolved": True, "isOutdated": False, "path": "cart.py", "line": 12, "diffSide": "RIGHT", "resolvedBy": {"login": "ann"},
             "comments": {"nodes": [{"databaseId": 1, "body": "Round it", "createdAt": "2026-11-02T10:00:00Z", "author": {"login": "ann"}, "url": "u1"},
                                    {"databaseId": 2, "body": "Done", "createdAt": "2026-11-02T11:00:00Z", "author": {"login": "me"}}]}},
            {"isResolved": False, "isOutdated": True, "path": "old.py", "line": None, "originalLine": 4, "diffSide": "LEFT",
             "comments": {"nodes": [{"databaseId": 3, "body": "Why gone?", "createdAt": "2026-11-02T12:00:00Z", "author": {"login": "bob"}}]}},
        ]
        out = model.build_threads(github.thread_comments(threads), "me")
        first, second = out
        self.assertEqual((first["path"], first["line"], first["side"], first["resolved"], first["resolved_by"]["name"]), ("cart.py", 12, "new", True, "ann"))
        self.assertEqual([m["raw"] for m in first["messages"]], ["Round it", "Done"])
        self.assertEqual((second["line"], second["side"], second["outdated"], second["unresolved"], second["awaiting_author"]), (4, "old", True, True, True))

    def test_the_timeline_reads_as_bitbucket_activity(self):
        events = [{"event": "reviewed", "state": "approved", "user": {"login": "ann"}, "submitted_at": "2026-11-02T09:00:00Z"},
                  {"event": "reviewed", "state": "changes_requested", "user": {"login": "cid"}, "submitted_at": "2026-11-02T08:00:00Z"},
                  {"event": "commented", "actor": {"login": "bob"}, "created_at": "2026-11-02T07:00:00Z", "body": "LGTM"},
                  {"event": "merged", "actor": {"login": "ann"}, "created_at": "2026-11-03T10:00:00Z"},
                  {"event": "closed", "actor": {"login": "ann"}, "created_at": "2026-11-03T10:00:00Z"}]
        kinds = [(e["kind"], e["who"]) for e in model.timeline(github.activity_shape(events))]
        self.assertEqual(kinds, [("updated", "ann"), ("approved", "ann"), ("changes_requested", "cid"), ("commented", "bob")],
                         "a merge also closes: only the merge is told")

    def test_graphql_lives_next_to_the_api(self):
        self.assertEqual(github.graphql_url("https://api.github.com"), "https://api.github.com/graphql")
        self.assertEqual(github.graphql_url("https://ghe.example.com/api/v3"), "https://ghe.example.com/api/graphql")


class FakeGitHub(github.Client):
    """GitHub's client with canned answers by path; the calls are kept."""

    def __init__(self, answers):
        super().__init__("octo", login="t", api="https://api.github.com")
        self.answers, self.calls = answers, []

    def request(self, method, url, body=None, accept="application/json", raw=False, limit=None, headers_out=None):
        path = url.split("api.github.com", 1)[1].split("?", 1)[0]
        self.calls.append((method, path, url, body))
        answer = self.answers.get((method, path), self.answers.get(path))
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is not None else ([] if path.endswith(("/reviews", "/comments")) else {})


class GitHubClientTest(unittest.TestCase):
    def test_a_person_s_pull_requests_come_from_a_search_and_each_is_read_once_per_change(self):
        hit = {"number": 7, "repository_url": "https://api.github.com/repos/octo/shop", "updated_at": "2026-11-02T10:00:00Z"}
        client = FakeGitHub({"/users/octo": {"type": "Organization"}, "/search/issues": {"items": [hit], "total_count": 1},
                             "/repos/octo/shop/pulls/7": gh_pr(), "/repos/octo/shop/pulls/7/reviews": []})
        meta = {}
        first = client.user_prs("me", "MERGED", newer_than="2026-10-01T00:00:00.123456+00:00", meta=meta)
        query = [c[2] for c in client.calls if c[1] == "/search/issues"][0]
        self.assertIn("org%3Aocto", query)
        self.assertIn("is%3Amerged", query)
        self.assertIn("updated%3A%3E%3D2026-10-01T00%3A00%3A00%2B00%3A00", query)
        self.assertEqual((first[0]["id"], meta["size"]), (7, 1))
        reads = len([c for c in client.calls if c[1] == "/repos/octo/shop/pulls/7"])
        client.user_prs("me", "MERGED")
        self.assertEqual(len([c for c in client.calls if c[1] == "/repos/octo/shop/pulls/7"]), reads, "unchanged: read from what was kept")

    def test_merge_sends_the_method_the_message_and_the_tip_it_expects(self):
        merged = gh_pr(state="closed", merged_at="2026-11-03T10:00:00Z")
        client = FakeGitHub({("PUT", "/repos/octo/shop/pulls/7/merge"): {"merged": True}, "/repos/octo/shop/pulls/7": merged,
                             ("DELETE", "/repos/octo/shop/git/refs/heads/feat/SHOP-12-cart"): {}})
        out = client.merge("shop", 7, {"merge_strategy": "squash", "message": "Cart totals (#7)\n\nMore.", "close_source_branch": True}, expected="abc123")
        body = [c[3] for c in client.calls if c[0] == "PUT"][0]
        self.assertEqual(body, {"merge_method": "squash", "sha": "abc123", "commit_title": "Cart totals (#7)", "commit_message": "More."})
        self.assertEqual(out["state"], "MERGED")
        self.assertIn(("DELETE", "/repos/octo/shop/git/refs/heads/feat/SHOP-12-cart"), [(c[0], c[1]) for c in client.calls])
        with self.assertRaises(ValueError):
            client.merge("shop", 7, {"merge_strategy": "fast_forward"})

    def test_taking_back_an_approval_dismisses_your_latest_one(self):
        client = FakeGitHub({"/user": {"login": "me", "id": 1},
                             "/repos/octo/shop/pulls/7/reviews": [review("me", "APPROVED", "2026-11-01T00:00:00Z") | {"id": 41},
                                                                  review("bob", "APPROVED", "2026-11-02T00:00:00Z") | {"id": 42}]})
        client.review("shop", 7, "unapprove")
        self.assertIn(("PUT", "/repos/octo/shop/pulls/7/reviews/41/dismissals"), [(c[0], c[1]) for c in client.calls])
        with self.assertRaises(BitbucketError):
            client.review("shop", 7, "unrequest_changes")


# ---- GitLab in Bitbucket's shapes ---------------------------------------------------------------

def gl_mr(**over):
    raw = {"iid": 12, "project_id": 99, "title": "Draft: cart totals", "state": "opened", "draft": True, "description": "Totals.",
           "author": {"id": 1, "username": "me", "name": "Me Myself"}, "web_url": "https://gitlab.com/acme/platform/shop/-/merge_requests/12",
           "references": {"full": "acme/platform/shop!12"}, "source_branch": "feat/cart", "target_branch": "main", "sha": "def456",
           "created_at": "2026-11-01T10:00:00Z", "updated_at": "2026-11-02T10:00:00Z", "user_notes_count": 4,
           "reviewers": [{"id": 2, "username": "ann", "name": "Ann"}, {"id": 3, "username": "bob", "name": "Bob"}],
           "source_project_id": 99}
    raw.update(over)
    return raw


class GitLabShapeTest(unittest.TestCase):
    def test_a_merge_request_reads_like_bitbucket_s(self):
        approvals = {"approved_by": [{"user": {"id": 2, "username": "ann", "name": "Ann"}}, {"user": {"id": 4, "username": "cy", "name": "Cy"}}]}
        states = [{"user": {"id": 3, "username": "bob", "name": "Bob"}, "state": "requested_changes"},
                  {"user": {"id": 2, "username": "ann", "name": "Ann"}, "state": "unreviewed"}]
        pr = model.slim_pr(gitlab.mr_shape(gl_mr(), approvals, states, "acme"))
        self.assertEqual((pr["repo"], pr["id"], pr["state"], pr["draft"], pr["source"], pr["dest"]),
                         ("platform/shop", 12, "OPEN", True, "feat/cart", "main"))
        by = {r["name"]: (r["state"], r["reviewer"]) for r in pr["reviews"]}
        self.assertEqual(by, {"Ann": ("approved", True), "Bob": ("changes_requested", True), "Cy": ("approved", False)})
        self.assertEqual(gitlab.mr_shape(gl_mr(state="merged"), group="acme")["state"], "MERGED")
        self.assertEqual(gitlab.mr_shape(gl_mr(state="closed"), group="acme")["state"], "DECLINED")

    def test_discussions_become_threads_resolved_when_every_note_is(self):
        discussions = [
            {"notes": [{"id": 1, "body": "Round it", "author": {"id": 2, "name": "Ann"}, "created_at": "2026-11-02T10:00:00Z",
                        "resolvable": True, "resolved": True, "resolved_by": {"id": 1, "name": "Me Myself"},
                        "position": {"new_path": "cart.py", "new_line": 12}},
                       {"id": 2, "body": "Done", "author": {"id": 1, "name": "Me Myself"}, "created_at": "2026-11-02T11:00:00Z", "resolvable": True, "resolved": True}]},
            {"notes": [{"id": 3, "body": "Typo", "author": {"id": 3, "name": "Bob"}, "created_at": "2026-11-02T12:00:00Z",
                        "resolvable": True, "resolved": False, "position": {"old_path": "old.py", "new_line": None, "old_line": 4}}]},
            {"notes": [{"id": 4, "body": "approved this merge request", "system": True, "author": {"id": 2}}]},
            {"notes": [{"id": 5, "body": "Nice work", "author": {"id": 2, "name": "Ann"}, "created_at": "2026-11-02T13:00:00Z"}]},
        ]
        threads = model.build_threads(gitlab.discussion_comments(discussions, "https://gl/mr/12"), "1")
        self.assertEqual(len(threads), 3, "system notes are no thread")
        done, typo, general = threads
        self.assertEqual((done["resolved"], done["resolved_by"]["name"], done["line"], done["side"]), (True, "Me Myself", 12, "new"))
        self.assertEqual((typo["unresolved"], typo["awaiting_author"], typo["line"], typo["side"]), (True, True, 4, "old"))
        self.assertEqual((general["inline"], general["unresolved"]), (False, False))
        self.assertEqual(done["url"], "https://gl/mr/12#note_1")

    def test_system_notes_tell_the_activity(self):
        notes = [{"system": True, "body": "approved this merge request", "author": {"id": 2, "name": "Ann"}, "created_at": "2026-11-02T09:00:00Z"},
                 {"system": True, "body": "added 2 commits\n\n<ul>…</ul>", "author": {"id": 1, "name": "Me"}, "created_at": "2026-11-02T10:00:00Z"},
                 {"system": False, "body": "Why?", "author": {"id": 3, "name": "Bob"}, "created_at": "2026-11-02T11:00:00Z"}]
        self.assertEqual([(e["kind"], e["who"]) for e in model.timeline(gitlab.activity_shape(notes))],
                         [("commented", "Bob"), ("updated", "Me"), ("approved", "Ann")])

    def test_per_file_diffs_become_one_git_diff(self):
        files = [{"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1,2 +1,2 @@\n-x = 1\n+x = 2\n y = 3\n"},
                 {"old_path": "new.py", "new_path": "new.py", "new_file": True, "diff": "@@ -0,0 +1 @@\n+print(1)\n"},
                 {"old_path": "gone.py", "new_path": "gone.py", "deleted_file": True, "diff": "@@ -1 +0,0 @@\n-x\n"},
                 {"old_path": "old/name.py", "new_path": "new/name.py", "renamed_file": True, "diff": ""},
                 {"old_path": "logo.png", "new_path": "logo.png", "diff": ""}]
        parsed = {f["path"]: f for f in model.parse_diff(gitlab.diff_text(files))}
        self.assertEqual((parsed["a.py"]["added"], parsed["a.py"]["removed"], parsed["a.py"]["status"]), (1, 1, "modified"))
        self.assertEqual(parsed["new.py"]["status"], "added")
        self.assertEqual(parsed["gone.py"]["status"], "removed")
        self.assertEqual((parsed["new/name.py"]["status"], parsed["new/name.py"]["old_path"]), ("renamed", "old/name.py"))
        self.assertTrue(parsed["logo.png"]["binary"])

    def test_the_project_decides_the_merge_method_and_the_squash(self):
        self.assertEqual(gitlab.strategies({"merge_method": "merge", "squash_option": "default_off"}), (["merge_commit", "squash"], "merge_commit"))
        self.assertEqual(gitlab.strategies({"merge_method": "ff", "squash_option": "default_on"}), (["fast_forward", "squash_fast_forward"], "squash_fast_forward"))
        self.assertEqual(gitlab.strategies({"merge_method": "rebase_merge", "squash_option": "never"}), (["rebase_merge"], "rebase_merge"))
        self.assertEqual(gitlab.strategies({"squash_option": "always"}), (["squash"], "squash"))

    def test_requesting_changes_says_it_cannot_yet(self):
        client = gitlab.Client("acme", login="t", url="https://gitlab.example.com")
        with self.assertRaises(BitbucketError) as caught:
            client.review("shop", 12, "request_changes")
        self.assertIn("GitLab", caught.exception.message)


# ---- the actions on another host ---------------------------------------------------------------

class HostClient:
    """A GitHub-like client for the actions: what they ask of it is kept."""

    kind = "github"
    workspace = "octo"

    def __init__(self):
        self.calls = []

    def merge_info(self, repo, pr_id):
        self.calls.append(("merge_info", repo, pr_id))
        return {"state": "OPEN", "title": "Cart", "source": {"branch": {"name": "feat/x"}, "commit": {"hash": "abc"}},
                "destination": {"branch": {"name": "main", "merge_strategies": ["merge_commit", "squash"], "default_merge_strategy": "merge_commit"}},
                "participants": [{"user": {"display_name": "Ann"}, "approved": True}]}

    def merge(self, repo, pr_id, payload, expected=None):
        self.calls.append(("merge", repo, pr_id, payload, expected))
        return {"id": pr_id, "state": "MERGED", "destination": {"repository": {"full_name": "octo/shop", "slug": "shop"}}}

    def decline(self, repo, pr_id):
        self.calls.append(("decline", repo, pr_id))

    def review(self, repo, pr_id, action):
        self.calls.append(("review", repo, pr_id, action))

    def pull_request(self, repo, pr_id):
        return github.pr_shape(gh_pr(user={"login": "ann"}), [], repo)


def action_engine(author="me"):
    pr = model.slim_pr(github.pr_shape(gh_pr(user={"login": author}), [], "shop"))
    eng = type("E", (), {})()
    eng.client = HostClient()
    eng.find_pr = lambda repo, pr_id: pr if (repo, pr_id) == ("shop", 7) else None
    eng.me = lambda: {"uuid": "me"}
    eng.refresh_person = lambda uuid: None
    eng.pushed = lambda force=False: []
    eng.update_pr = lambda fresh: None
    eng.filed = []
    eng.file_merged = lambda uuid, raw: eng.filed.append(raw) or model.slim_pr(raw)
    return eng


class HostActionsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_merge_goes_to_the_host_with_the_tip_it_read(self):
        eng = action_engine()
        out = actions.merge(eng, {"repo": "shop", "id": 7, "strategy": "squash", "message": "Merge it"})
        name, repo, pr_id, payload, expected = eng.client.calls[-1]
        self.assertEqual((name, repo, pr_id, payload["merge_strategy"], payload["message"], expected), ("merge", "shop", 7, "squash", "Merge it", "abc"))
        self.assertEqual((out["state"], len(eng.filed)), ("MERGED", 1))

    def test_the_merge_popup_writes_the_host_s_own_message(self):
        options = actions.merge_options(action_engine(), "shop", 7)
        self.assertTrue(options["message"].startswith("Merge pull request #7 from octo/feat/x"))
        self.assertEqual((options["strategies"], options["default"]), (["merge_commit", "squash"], "merge_commit"))

    def test_close_and_review(self):
        eng = action_engine()
        actions.decline(eng, {"repo": "shop", "id": 7})
        self.assertEqual(eng.client.calls[-1], ("decline", "shop", 7))
        theirs = action_engine(author="ann")
        actions.review(theirs, {"repo": "shop", "id": 7, "action": "approve"})
        self.assertEqual(theirs.client.calls[-1], ("review", "shop", 7, "approve"))


# ---- the setup screen on each host -----------------------------------------------------------

class SetupTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        patcher = mock.patch.object(credentials, "store", lambda: self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.path = _HOME / f"setup-{self.id()}.env"
        self.path.write_text("BITBUCKET_WORKSPACE=acme\n")
        config._cache.update(path=None, mtime=None, values={})
        env = mock.patch.dict(os.environ, {"PIXELDECK_ENV": str(self.path)})
        env.start()
        self.addCleanup(env.stop)

    def test_saving_github_switches_the_host_and_keeps_bitbucket_s_settings(self):
        state = account.save({"host": "github", "token": "ghp_secret", "owner": "octo-org"})
        text = self.path.read_text()
        self.assertNotIn("ghp_secret", text)
        self.assertEqual(self.store.values, {"GITHUB_TOKEN": "ghp_secret"})
        self.assertEqual(config.parse_env(text), {"BITBUCKET_WORKSPACE": "acme", "CODE_HOST": "github", "GITHUB_OWNER": "octo-org"})
        self.assertEqual((state["host"], state["needed"], state["token"]), ("github", False, "store"))
        back = account.save({"host": "bitbucket", "workspace": "acme", "token": "bb"})
        self.assertNotIn("CODE_HOST", config.parse_env(self.path.read_text()), "Bitbucket is the default: no line for it")
        self.assertEqual(back["host"], "bitbucket")

    def test_saving_gitlab_with_its_own_server(self):
        account.save({"host": "gitlab", "token": "glpat", "group": "acme/platform", "gitlab_url": "https://gitlab.example.com/"})
        values = config.parse_env(self.path.read_text())
        self.assertEqual((values["CODE_HOST"], values["GITLAB_GROUP"], values["GITLAB_URL"]), ("gitlab", "acme/platform", "https://gitlab.example.com"))

    def test_bad_values_are_refused_before_anything_is_written(self):
        for body, words in (({"host": "github", "token": "t", "owner": "-octo"}, "owner"),
                            ({"host": "github", "token": "t", "owner": "octo", "api_url": "http://ghe.example.com/api/v3"}, "https://"),
                            ({"host": "gitlab", "token": "t", "group": "acme/../x"}, "group"),
                            ({"host": "github", "owner": "octo"}, "Paste a GitHub token"),
                            ({"host": "jira"}, "Pick Bitbucket, GitHub or GitLab")):
            with self.assertRaises(ValueError, msg=body) as caught:
                account.save(body)
            self.assertIn(words, str(caught.exception))
        self.assertEqual(self.path.read_text(), "BITBUCKET_WORKSPACE=acme\n")

    def test_a_check_of_github_stops_at_a_refused_token(self):
        class Refused(github.Client):
            def request(self, method, url, body=None, **kw):
                raise BitbucketError(401, "Bad credentials")
        answer = account._check_github({"token": "t", "owner": "octo"}, client_factory=Refused)
        self.assertEqual([(r["label"], r["hint"]) for r in answer["results"]], [("Your user", "the token is wrong or expired")])
        self.assertFalse(answer["ok"])


class EngineHostTest(unittest.TestCase):
    def test_each_host_keeps_its_own_cache_and_team(self):
        bb_cache, bb_settings = engine_mod._files("bitbucket")
        gh_cache, gh_settings = engine_mod._files("github")
        self.assertEqual((bb_cache.name, bb_settings.name), ("cache.json", "settings.json"))
        self.assertEqual((gh_cache.name, gh_settings.name), ("cache-github.json", "settings-github.json"))

    def test_the_budget_follows_the_host(self):
        with settings(CODE_HOST="github", GITHUB_OWNER="octo", GITHUB_TOKEN="t"), \
                mock.patch.dict(os.environ, {"PIXELDECK_CACHE_DIR": str(_HOME / "c"), "PIXELDECK_CONFIG_DIR": str(_HOME / "f")}):
            eng = engine_mod.Engine(_HOME)
            self.assertEqual((forge.kind(eng.client), eng.budget()["limit"], eng._ceiling()), ("github", 5000, 4000))
            self.assertTrue(str(eng.cache_file).endswith("cache-github.json"))


if __name__ == "__main__":
    unittest.main()
