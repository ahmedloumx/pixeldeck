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
        e = engine([{"values": []}, behind, behind, {"values": []}, behind], [pr()])
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
