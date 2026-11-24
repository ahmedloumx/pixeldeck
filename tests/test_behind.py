"""News about your pull requests: a branch left behind by a merge into its target, approvals
taken back, new tasks."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env: they get settings of their own, and no token.
_ENV = Path(tempfile.mkdtemp(prefix="pixeldeck-test-")) / ".env"
_ENV.write_text("BITBUCKET_WORKSPACE=acme\n")
os.environ["PIXELDECK_ENV"] = str(_ENV)
os.environ["PIXELDECK_CREDENTIALS"] = "off"  # nor a token from the credential store
for _key in ("BITBUCKET_TOKEN", "BITBUCKET_EMAIL", "BITBUCKET_WORKSPACE"):
    os.environ.pop(_key, None)

import engine as engine_mod  # noqa: E402
import events  # noqa: E402
from bitbucket import BitbucketError  # noqa: E402

ME = {"uuid": "{me}", "name": "Me"}
ALICE = {"uuid": "{alice}", "name": "Alice"}


def pr(**over):
    base = {"repo": "shop-api", "id": 42, "title": "Cheaper bundle", "author": ME, "source": "feat/x", "dest": "develop",
            "comment_count": 0, "task_count": 0, "reviews": [], "url": "https://bitbucket.org/acme/shop-api/pull-requests/42"}
    base.update(over)
    return base


class Client:
    workspace = "acme"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def get(self, path, params=None):
        self.calls.append((path, params))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def paged(self, path, params=None, max_pages=10):
        yield from self.get(path, params).get("values") or []


def engine(answers, open_prs):
    eng = engine_mod.Engine.__new__(engine_mod.Engine)
    eng.lock = threading.RLock()
    eng.data = {"me": {"uuid": "{me}"}, "people": {"{me}": {"open": open_prs, "merged": [], "synced_at": 1}}}
    eng._save = lambda: None
    eng.events = events.Log(eng.data, lambda: None)
    eng.client = Client(answers)
    return eng


class BehindTest(unittest.TestCase):
    def test_falling_behind_is_news_once_and_a_sync_resets_it(self):
        behind = {"values": [{"hash": "a"}, {"hash": "b"}]}
        clean = {"values": []}  # each look at a branch behind also asks for its conflicts
        e = engine([{"values": []}, behind, clean, behind, clean, {"values": []}, behind, clean], [pr()])
        self.assertEqual(e.check_behind(), [], "the first look only records")
        path, params = e.client.calls[0]
        self.assertEqual((path, params["include"], params["exclude"]), ("/repositories/acme/shop-api/commits", "develop", "feat/x"))
        found = e.check_behind()
        self.assertEqual([(ev["kind"], ev["count"], ev["dest"]) for ev in found], [("behind", 2, "develop")])
        self.assertEqual(e.check_behind(), [], "still behind is not news again")
        e.mark_synced("shop-api", 42)
        self.assertEqual(e.behind(pr())["behind"], 0)
        self.assertEqual(e.check_behind(), [])
        self.assertEqual(len(e.check_behind()), 1, "behind again after a sync is news again")
        self.assertEqual([ev["kind"] for ev in e.events.read()["events"]], ["behind", "behind"])

    def test_a_failed_look_changes_nothing_and_closed_ones_are_forgotten(self):
        e = engine([{"values": []}, BitbucketError(500, "down")], [pr()])
        e.check_behind()
        e.check_behind()
        self.assertEqual(e.behind(pr())["behind"], 0)
        e.data["people"]["{me}"]["open"] = []
        e.check_behind()
        self.assertIsNone(e.behind(pr()))


class ConflictsTest(unittest.TestCase):
    """Bitbucket's yellow triangle: the files a sync would stop on, asked only of a branch behind."""

    LEVEL = {"values": []}
    BEHIND = {"values": [{"hash": "a"}, {"hash": "b"}]}

    def test_only_a_branch_behind_is_asked_and_its_files_are_kept(self):
        clash = {"values": [{"path": "app.txt"}, {"path": "api/x.py"}]}
        e = engine([self.LEVEL, self.BEHIND, clash], [pr()])
        e.check_behind()
        self.assertEqual(len(e.client.calls), 1, "a branch level with its target cannot conflict: not asked")
        self.assertEqual(e.behind(pr())["conflicts"], [])
        found = e.check_behind()
        self.assertEqual(e.client.calls[-1][0], "/repositories/acme/shop-api/pullrequests/42/conflicts")
        self.assertEqual(e.behind(pr())["conflicts"], ["app.txt", "api/x.py"])
        self.assertEqual([(ev["kind"], ev["count"], ev["conflicts"]) for ev in found], [("behind", 2, 2)],
                         "falling behind with conflicts is one piece of news that says both")

    def test_a_failed_ask_leaves_conflicts_unknown_and_behind_intact(self):
        e = engine([self.BEHIND, BitbucketError(500, "down")], [pr()])
        e.check_behind()
        self.assertEqual((e.behind(pr())["behind"], e.behind(pr())["conflicts"]), (2, None))

    def test_a_sync_clears_them_and_a_stopped_sync_sets_them(self):
        e = engine([self.BEHIND, {"values": [{"path": "app.txt"}]}], [pr()])
        e.check_behind()
        e.mark_synced("shop-api", 42)
        self.assertEqual((e.behind(pr())["behind"], e.behind(pr())["conflicts"]), (0, []))
        e.mark_conflicts("shop-api", 42, ["app.txt", "web/a b.css"], 3)
        self.assertEqual((e.behind(pr())["behind"], e.behind(pr())["conflicts"]), (3, ["app.txt", "web/a b.css"]))
        e.mark_conflicts("shop-api", 42, [])
        self.assertEqual((e.behind(pr())["behind"], e.behind(pr())["conflicts"]), (3, True),
                         "a stop that names no file still conflicts, and keeps the last count")

    def test_bitbucket_names_the_files_across_pages_once_each(self):
        import types  # noqa: PLC0415

        import bitbucket  # noqa: PLC0415
        import hosts  # noqa: PLC0415
        client = bitbucket.Client(workspace="acme")
        pages = [{"values": [{"path": "app.txt"}, {"path": "api/x.py"}], "next": "https://api.bitbucket.org/2.0/page-2"},
                 {"values": [{"path": "app.txt"}, {"path": "web/a b.css"}, {"scenario": "unknown"}]}]
        calls = []
        client.get = lambda path, params=None: calls.append((path, params)) or pages.pop(0)
        host = hosts.BitbucketHost(types.SimpleNamespace(client=client))
        self.assertEqual(host.conflicts(pr()), ["app.txt", "api/x.py", "web/a b.css"])
        self.assertEqual(calls, [("/repositories/acme/shop-api/pullrequests/42/conflicts", {"pagelen": 100, "fields": "values.path,next"}),
                                 ("https://api.bitbucket.org/2.0/page-2", None)])


class NewKindsTest(unittest.TestCase):
    def test_tasks_and_approvals_taken_back(self):
        before = pr(author=ALICE, task_count=1, reviews=[{"uuid": "{bob}", "name": "Bob", "state": "approved", "reviewer": True}])
        after = pr(author=ALICE, task_count=3, reviews=[{"uuid": "{bob}", "name": "Bob", "state": None, "reviewer": True}])
        out = {ev["kind"]: ev for ev in events.diff([before], [after], "{me}")}
        self.assertEqual(out["tasks"]["count"], 2)
        self.assertEqual(out["unapproved"]["actor"]["name"], "Bob")
        mine = pr(reviews=[{"uuid": "{me}", "name": "Me", "state": "approved", "reviewer": True}])
        self.assertEqual(events.diff([mine], [pr(reviews=[{"uuid": "{me}", "name": "Me", "state": None, "reviewer": True}])], "{me}"), [],
                         "taking back your own approval is not news to you")


if __name__ == "__main__":
    unittest.main()
