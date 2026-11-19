"""GitLab as the code host, against a scripted transport: no network, no real token."""

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
import gitlab  # noqa: E402
import model  # noqa: E402
from test_github import FakeGitHub as FakeGitLab, answer  # noqa: E402

API = "https://gitlab.com/api/v4"
TOKEN = "glpat-test-secret-token"
PROJECT = f"{API}/projects/acme%2Fweb%2Fshop"


def gl_user(name, full=None):
    return {"id": abs(hash(name)) % 1000, "username": name, "name": full or name.title()}


def mr(iid, state="opened", author="me", reviewers=(), updated="2026-11-10T10:00:00Z", merged_at=None, merged_by=None):
    return {"iid": iid, "title": "Cheaper bundle", "state": state, "draft": False, "author": gl_user(author),
            "source_branch": "feat/ABC-12-bundle", "target_branch": "develop", "sha": "b" * 40,
            "created_at": "2026-11-01T09:00:00Z", "updated_at": updated, "merged_at": merged_at, "closed_at": None,
            "merge_user": gl_user(merged_by) if merged_by else None, "user_notes_count": 4,
            "web_url": f"https://gitlab.com/acme/web/shop/-/merge_requests/{iid}", "references": {"full": f"acme/web/shop!{iid}"},
            "reviewers": [gl_user(r) for r in reviewers], "description": "Why: cheaper."}


class GitLabBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pixeldeck-gitlab-"))
        self.env_file = self.tmp / ".env"
        self.env_file.write_text(f"PIXELDECK_CODE_HOST=gitlab\nGITLAB_TOKEN={TOKEN}\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "GITLAB_", "BITBUCKET_", "PIXELDECK_"))}
        env.update(PIXELDECK_ENV=str(self.env_file), PIXELDECK_CREDENTIALS="off", PIXELDECK_CACHE_DIR=str(self.tmp / "cache"),
                   PIXELDECK_SETTINGS_FILE=str(self.tmp / "settings.json"), PIXELDECK_STATE_DIR=str(self.tmp / "state"))
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = [1_900_000_000.0]
        self.fake = FakeGitLab()

    def host(self, engine=None):
        return gitlab.GitLabHost(engine or types.SimpleNamespace(person=lambda uuid: None), transport=self.fake,
                                 clock=lambda: self.now[0])


class GitLabTest(GitLabBase):
    def test_a_merge_request_reads_as_the_page_expects(self):
        self.fake.on("GET", f"{API}/merge_requests", answer(200, [mr(12, reviewers=["alice", "bob", "me"])]))
        self.fake.on("GET", f"{PROJECT}/merge_requests/12/approvals", answer(200, {"approved_by": [{"user": gl_user("alice")}]}))
        [pr] = self.host().prs("me", "OPEN")
        self.assertEqual({k: pr[k] for k in ("repo", "id", "state", "source", "dest", "comment_count", "url")},
                         {"repo": "acme/web/shop", "id": 12, "state": "OPEN", "source": "feat/ABC-12-bundle", "dest": "develop",
                          "comment_count": 4, "url": "https://gitlab.com/acme/web/shop/-/merge_requests/12"})
        self.assertEqual({r["uuid"]: r["state"] for r in pr["reviews"]}, {"alice": "approved", "bob": "pending"})
        listed = self.fake.calls[0]
        self.assertIn("scope=all", listed.url)
        self.assertIn("author_username=me", listed.url)
        self.assertEqual(listed.headers["PRIVATE-TOKEN"], TOKEN)
        self.assertNotIn("Authorization", listed.headers)

    def test_merged_ones_need_no_call_each_and_a_group_scopes_the_list(self):
        self.env_file.write_text(self.env_file.read_text() + "GITLAB_GROUP=acme/web\n")
        rows = [mr(n, state="merged", merged_at=f"2026-11-{10 + n:02d}T08:00:00Z", merged_by="bob") for n in range(1, 4)]
        self.fake.on("GET", f"{API}/groups/acme%2Fweb/merge_requests", answer(200, rows, {"x-total": "3"}))
        meta = {}
        out = self.host().prs("me", "MERGED", newer_than="2026-11-12T00:00:00+00:00", meta=meta)
        self.assertEqual([(p["id"], p["closed_by"]["uuid"]) for p in out], [(2, "bob"), (3, "bob")])
        self.assertEqual(meta["size"], 3)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertIn("updated_after=2026-11-12", self.fake.calls[0].url)

    def test_discussions_carry_their_resolution(self):
        self.fake.on("GET", f"{PROJECT}/merge_requests/12", answer(200, mr(12)))
        self.fake.on("GET", f"{PROJECT}/merge_requests/12/approvals", answer(200, {"approved_by": []}))
        note = lambda i, who, body, **more: {"id": i, "author": gl_user(who), "body": body, "created_at": f"2026-11-0{i}T10:00:00Z",  # noqa: E731
                                             "updated_at": f"2026-11-0{i}T10:00:00Z", **more}
        self.fake.on("GET", f"{PROJECT}/merge_requests/12/discussions", answer(200, [
            {"id": "d1", "notes": [note(1, "alice", "Why?", type="DiffNote", resolvable=True, resolved=True, resolved_by=gl_user("me"),
                                        position={"new_path": "app.py", "new_line": 4})]},
            {"id": "d2", "notes": [note(2, "bob", "Index?", type="DiffNote", resolvable=True, resolved=False,
                                        position={"new_path": "db.py", "new_line": 9})]},
            {"id": "d3", "notes": [note(3, "carol", "Nice")]},
            {"id": "d4", "notes": [note(4, "bob", "added 1 commit", system=True)]},
        ]))
        out = self.host().detail("acme/web/shop", 12)
        threads = {t["id"]: t for t in out["threads"]}
        self.assertEqual(sorted(threads), ["d1", "d2", "d3"], "a system note is not a thread")
        self.assertTrue(threads["d1"]["resolved"])
        self.assertTrue(threads["d2"]["awaiting_author"])
        self.assertFalse(threads["d3"]["inline"])
        self.assertEqual(model.attention_of(out["threads"], out["tasks"])["unresolved"], 1)
        self.assertEqual(out["description"], "Why: cheaper.")

    def test_the_diff_is_put_back_together_for_the_page(self):
        self.fake.on("GET", f"{PROJECT}/merge_requests/12/diffs", answer(200, [
            {"old_path": "a.py", "new_path": "a.py", "diff": "@@ -1 +1 @@\n-x\n+y\n"},
            {"old_path": "n.py", "new_path": "n.py", "new_file": True, "diff": "@@ -0,0 +1 @@\n+new\n"}]))
        text, cut = self.host().diff("acme/web/shop", 12)
        files = model.parse_diff(text)
        self.assertEqual([(f["path"], f["status"], f["added"], f["removed"]) for f in files], [("a.py", "modified", 1, 1), ("n.py", "added", 1, 0)])
        with self.assertRaises(ValueError):
            self.host().diff("../x", 1)

    def test_a_spent_minute_is_waited_out_and_the_meter_is_hourly(self):
        self.fake.on("GET", f"{API}/user", answer(429, {"message": "Retry later"}, {"ratelimit-remaining": "0",
                                                                                    "ratelimit-reset": str(int(self.now[0]) + 40)}))
        host = self.host()
        with self.assertRaises(codehost.HostError) as caught:
            host.me()
        self.assertEqual(caught.exception.status, 429)
        with self.assertRaises(codehost.HostError):
            host.me()
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(host.budget(), {"used": 1, "limit": gitlab.GitLabApi.default_limit})

    def test_the_engine_reads_gitlab_with_its_own_files(self):
        self.fake.on("GET", f"{API}/user", answer(200, gl_user("me", "Me Myself")))
        eng = engine_mod.Engine(self.tmp)
        eng._host = self.host(eng)
        eng.refresh_identity()
        self.assertEqual(eng.me()["uuid"], "me")
        self.assertTrue(eng.cache_file.name.endswith("cache-gitlab.json"))
        self.assertFalse(eng.host.serves("merge"))

    def test_setup_saves_and_checks_without_showing_the_token(self):
        out = account.save({"host": "gitlab", "token": "glpat-new-secret", "url": "https://git.example.com", "group": "acme"})
        text = self.env_file.read_text()
        self.assertIn("GITLAB_URL=https://git.example.com", text)
        self.assertIn("GITLAB_GROUP=acme", text)
        self.assertEqual((out["host"], out["gitlab"]["token"]), ("gitlab", "file"))
        self.assertNotIn("secret", json.dumps(out))
        self.fake.on("GET", "https://git.example.com/api/v4/user", answer(200, gl_user("me")))
        self.fake.on("GET", "https://git.example.com/api/v4/groups/acme", answer(200, {"id": 1}))
        self.fake.on("GET", "https://git.example.com/api/v4/merge_requests", answer(200, [mr(12)]))
        self.fake.on("GET", "https://git.example.com/api/v4/projects/acme%2Fweb%2Fshop/repository/commits", answer(200, []))
        checked = gitlab.check({"token": "glpat-typed-secret"}, transport=self.fake)
        self.assertTrue(checked["ok"], checked)
        self.assertNotIn("secret", json.dumps(checked))
        self.assertTrue(all(c.url.startswith("https://git.example.com/api/v4/") for c in self.fake.calls))


if __name__ == "__main__":
    unittest.main()
