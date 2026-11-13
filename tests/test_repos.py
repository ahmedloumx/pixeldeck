"""The Repos panel's routes. Run with: python3 -m unittest discover -s tests"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env: they get settings of their own, and no token.
_ENV = Path(tempfile.mkdtemp(prefix="pixeldeck-test-")) / ".env"
_ENV.write_text("BITBUCKET_WORKSPACE=acme\nTICKET_PREFIXES=ABC,XYZ\nJIRA_SITE=https://acme.atlassian.net\n")
os.environ["PIXELDECK_ENV"] = str(_ENV)
for _key in ("BITBUCKET_TOKEN", "BITBUCKET_EMAIL", "BITBUCKET_WORKSPACE", "TICKET_PREFIXES", "JIRA_SITE", "PIXELDECK_BRIEFS_DIR"):
    os.environ.pop(_key, None)

import repos  # noqa: E402
from bitbucket import BitbucketError  # noqa: E402

HASH = "6297b36c80f500799e4f753fb45b6d13e46bd275"
OTHER = "b2ff6016b15df18ba490049a98b096ce723d66a5"


def repo(slug, updated, **over):
    raw = {"slug": slug, "name": slug, "description": f"{slug} things", "language": "python",
           "updated_on": updated, "mainbranch": {"name": "develop"}}
    raw.update(over)
    return raw


def branch(name, sha, date):
    return {"name": name, "target": {"hash": sha, "date": date, "message": "fix: the thing\n\nbody",
                                     "author": {"raw": "Pat Author <pat@acme.test>"}}}


def entry(path, kind="commit_file", size=10, attributes=()):
    raw = {"path": path, "type": kind}
    if kind == "commit_file":
        raw.update(size=size, attributes=list(attributes), mimetype=None)
    return raw


class FakeClient:
    """Answers by URL path. Records every request with its method; a write would be recorded too."""

    workspace = "acme"

    def __init__(self, pages=None, texts=None, metas=None):
        self.pages = pages or {}   # path -> list of values (paged) or dict (get)
        self.texts = texts or {}   # path -> text
        self.metas = metas or {}   # path -> raw meta
        self.calls = []

    def send(self, method, path, body=None, params=None):
        self.calls.append((method, path, params))
        raise AssertionError(f"the Repos routes must never send {method}")

    def get(self, path, params=None):
        self.calls.append(("GET", path, dict(params or {})))
        if (params or {}).get("format") == "meta":
            if path not in self.metas:
                raise BitbucketError(404, "No such file or directory")
            return self.metas[path]
        if path not in self.pages:
            raise BitbucketError(404, "not found")
        hit = self.pages[path]
        return hit if isinstance(hit, dict) else {"values": hit}

    def paged(self, path, params=None, max_pages=10, stop=None, meta=None):
        self.calls.append(("GET", path, dict(params or {}, max_pages=max_pages)))
        if path not in self.pages:
            raise BitbucketError(404, "not found")
        values = self.pages[path]
        if meta is not None:
            meta["size"] = len(values)
        yield from values[:max_pages * (params or {}).get("pagelen", 10)]

    def text(self, path, limit=4 * 1024 * 1024):
        self.calls.append(("GET", path, {"limit": limit}))
        raw = self.texts[path]
        return raw[:limit], len(raw) > limit


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def engine(client, project="SHOP"):
    e = type("E", (), {})()
    e.client = client
    e.project = lambda: {"key": project, "name": "Shop"} if project else None
    return e


LIST = "/repositories/acme"
SRC = f"/repositories/acme/shop-api/src/{HASH}/"


def shop_client(**more):
    pages = {
        LIST: [repo("old-tool", "2026-01-01T00:00:00+00:00"), repo("shop-api", "2026-11-10T00:00:00+00:00"),
               repo("../evil", "2026-11-12T00:00:00+00:00")],
        "/repositories/acme/shop-api/refs/branches": [
            branch("develop", HASH, "2026-11-12T00:00:00+00:00"), branch("feat/a-b", OTHER, "2026-11-11T00:00:00+00:00")],
        SRC: [entry("src", "commit_directory"), entry("README.md", size=40), entry("Makefile"), entry("assets", "commit_directory"),
              entry("logo.png", size=900, attributes=["binary"])],
        SRC + "src/": [entry("src/app.py", size=20)],
        f"/repositories/acme/shop-api/commits/{HASH}": {"values": [
            {"hash": HASH, "date": "2026-11-12T00:00:00+00:00", "message": "feat: one\n\nmore", "author": {"user": {"display_name": "Pat"}}}],
            "next": "https://api.bitbucket.org/next"},
    }
    pages.update(more.pop("pages", {}))
    texts = {SRC + "README.md": "# Shop\n", SRC + "Makefile": "all:\n\techo hi\n", SRC + "src/app.py": "print('hi')\n",
             SRC + "blob.dat": "abc\x00def"}
    texts.update(more.pop("texts", {}))
    return FakeClient(pages=pages, texts=texts, **more)


def call(e, name, method="GET", **query):
    return repos.route(None, e, method, name, {k: [v] for k, v in query.items()}, None)


class ValidationTest(unittest.TestCase):
    def test_hashes_are_hex_seven_to_forty(self):
        self.assertEqual(repos.check_hash("ABCDEF1"), "abcdef1")
        self.assertEqual(repos.check_hash(HASH), HASH)
        for bad in ("abc12", "g" * 7, HASH + "0", "", "develop", f"{HASH[:10]}/x", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                repos.check_hash(bad)

    def test_paths_stay_inside_the_repository(self):
        for good in ("", "a", "src/app.py", "a.b/c", "docs/my file #1.md", "ü/ñ.txt", ".github/workflows", "a..b/c", "a/b..", "v1..v2.txt"):
            with self.subTest(good=good):
                self.assertEqual(repos.check_path(good), good)
        for bad in ("/etc/passwd", "..", "a/../b", "a/..", "../a", "a/./b", "./a", "a//b", "a/", "a\\b", "a\x00b", "a\nb", "a\x7fb",
                    "x" * (repos.PATH_MAX + 1), None, 5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                repos.check_path(bad)

    def test_branches_cannot_break_out_of_the_quoted_query(self):
        for good in ("develop", "feat/one-thing", "release/1.2", "user@host", "a+b"):
            with self.subTest(good=good):
                self.assertEqual(repos.check_branch(good), good)
        for bad in ('a"b', "a\\b", "a b", "a..b", "-x", "/x", "x/", "x.", "x.lock", "a//b", "@{u}", "a~1", "a^", "a:b",
                    "a?", "a*", "a[", "a\tb", "", "x" * 256, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                repos.check_branch(bad)

    def test_search_text_cannot_hold_quotes(self):
        self.assertEqual(repos.check_search("  feat "), "feat")
        self.assertEqual(repos.check_search(""), "")
        for bad in ('a"b', "a\\b", "a\nb", "x" * 101, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                repos.check_search(bad)

    def test_path_segments_are_encoded_one_by_one(self):
        self.assertEqual(repos._quoted("docs/my file #1?.md"), "docs/my%20file%20%231%3F.md")
        self.assertEqual(repos._quoted("a%2F..%2Fb"), "a%252F..%252Fb")


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        repos.reset(self.clock)
        self.client = shop_client()
        self.e = engine(self.client)

    def gets(self):
        return [c for c in self.client.calls if c[0] == "GET"]

    def test_the_list_is_the_projects_newest_first_and_skips_odd_slugs(self):
        out = call(self.e, "repos")
        self.assertEqual([r["slug"] for r in out["repos"]], ["shop-api", "old-tool"])
        self.assertEqual(out["repos"][0]["url"], "https://bitbucket.org/acme/shop-api")
        self.assertEqual(out["repos"][0]["mainbranch"], "develop")
        params = self.client.calls[0][2]
        self.assertEqual(params["q"], 'project.key="SHOP"')
        self.assertEqual(params["sort"], "-updated_on")
        self.assertTrue(params["fields"].startswith("next,"), "without next the walk stops at page one")

    def test_no_project_lists_the_workspace(self):
        call(engine(self.client, project=None), "repos")
        self.assertNotIn("q", self.client.calls[0][2])

    def test_the_list_is_kept_a_few_minutes_per_project(self):
        call(self.e, "repos")
        call(self.e, "repos")
        self.assertEqual(len(self.client.calls), 1)
        call(engine(self.client, project="OTHER"), "repos")
        self.assertEqual(len(self.client.calls), 2, "another project is another list")
        self.clock.now += repos.LIST_TTL + 1
        call(self.e, "repos")
        self.assertEqual(len(self.client.calls), 3)
        call(self.e, "repos", force="1")
        self.assertEqual(len(self.client.calls), 4)

    def test_only_listed_repositories_are_read(self):
        with self.assertRaises(KeyError):
            call(self.e, "repo-branches", repo="not-ours")
        with self.assertRaises(ValueError):
            call(self.e, "repo-branches", repo="../evil")
        with self.assertRaises(ValueError):
            call(self.e, "repo-tree", repo="shop-api/../../x", hash=HASH)
        self.assertTrue(all(c[1] == LIST for c in self.client.calls), "nothing past the list was asked")

    def test_branches_newest_first_with_a_name_search(self):
        out = call(self.e, "repo-branches", repo="shop-api")
        self.assertEqual([b["name"] for b in out["branches"]], ["develop", "feat/a-b"])
        self.assertEqual(out["branches"][0]["author"], "Pat Author", "the mail stays out")
        self.assertEqual(out["branches"][0]["message"], "fix: the thing")
        self.assertTrue(out["complete"])
        call(self.e, "repo-branches", repo="shop-api", q="feat")
        params = self.client.calls[-1][2]
        self.assertEqual(params["q"], 'name~"feat"')
        self.assertEqual(params["sort"], "-target.date")
        with self.assertRaises(ValueError):
            call(self.e, "repo-branches", repo="shop-api", q='x" OR name~"')

    def test_a_branch_resolves_from_the_list_without_a_call(self):
        call(self.e, "repo-branches", repo="shop-api")
        before = len(self.client.calls)
        self.assertEqual(call(self.e, "repo-ref", repo="shop-api", branch="feat/a-b")["hash"], OTHER)
        self.assertEqual(len(self.client.calls), before)

    def test_a_branch_outside_the_list_is_one_exact_lookup(self):
        path = "/repositories/acme/shop-api/refs/branches"
        self.client.pages[path] = {"values": [branch("old/one", OTHER, "2025-01-01T00:00:00+00:00")]}
        self.assertEqual(call(self.e, "repo-ref", repo="shop-api", branch="old/one")["hash"], OTHER)
        self.assertEqual(self.client.calls[-1][2]["q"], 'name="old/one"')
        with self.assertRaises(KeyError):
            call(self.e, "repo-ref", repo="shop-api", branch="gone")
        with self.assertRaises(ValueError):
            call(self.e, "repo-ref", repo="shop-api", branch='x" OR name~"')

    def test_a_folder_lists_folders_first_and_names_its_readme(self):
        out = call(self.e, "repo-tree", repo="shop-api", hash=HASH, path="")
        self.assertEqual([e["name"] for e in out["entries"]], ["assets", "src", "logo.png", "Makefile", "README.md"])
        self.assertEqual(out["readme"], "README.md")
        self.assertEqual(out["entries"][2]["attributes"], ["binary"])
        sub = call(self.e, "repo-tree", repo="shop-api", hash=HASH.upper(), path="src")
        self.assertEqual(self.client.calls[-1][1], SRC + "src/")
        self.assertEqual(sub["entries"][0]["path"], "src/app.py")

    def test_a_folder_at_a_hash_is_kept_for_good(self):
        call(self.e, "repo-tree", repo="shop-api", hash=HASH, path="")
        n = len(self.client.calls)
        self.clock.now += 10 * 24 * 3600
        call(self.e, "repo-tree", repo="shop-api", hash=HASH, path="")
        call(self.e, "repo-commits", repo="shop-api", hash=HASH)
        call(self.e, "repo-commits", repo="shop-api", hash=HASH)
        # the list expired meanwhile: one call for it, one for the commits, none for the folder again
        self.assertEqual(len(self.client.calls), n + 2)

    def test_folder_paths_are_encoded(self):
        self.client.pages[SRC + "my%20docs/%231/"] = [entry("my docs/#1/a.txt")]
        out = call(self.e, "repo-tree", repo="shop-api", hash=HASH, path="my docs/#1")
        self.assertEqual(out["entries"][0]["name"], "a.txt")
        with self.assertRaises(ValueError):
            call(self.e, "repo-tree", repo="shop-api", hash=HASH, path="../../../user")

    def test_a_file_from_a_listed_folder_needs_one_call(self):
        call(self.e, "repo-tree", repo="shop-api", hash=HASH, path="")
        n = len(self.client.calls)
        out = call(self.e, "repo-file", repo="shop-api", hash=HASH, path="Makefile")
        self.assertEqual(out["text"], "all:\n\techo hi\n")
        self.assertFalse(out["binary"] or out["cut"])
        self.assertEqual(len(self.client.calls), n + 1)
        self.assertEqual(self.client.calls[-1][2], {"limit": repos.FILE_MAX})
        call(self.e, "repo-file", repo="shop-api", hash=HASH, path="Makefile")
        self.assertEqual(len(self.client.calls), n + 1, "kept by hash")

    def test_a_binary_file_is_never_downloaded(self):
        call(self.e, "repo-tree", repo="shop-api", hash=HASH, path="")
        n = len(self.client.calls)
        out = call(self.e, "repo-file", repo="shop-api", hash=HASH, path="logo.png")
        self.assertTrue(out["binary"])
        self.assertIsNone(out["text"])
        self.assertEqual(len(self.client.calls), n)

    def test_a_file_opened_cold_asks_for_its_metadata_first(self):
        self.client.metas[SRC + "blob.dat"] = entry("blob.dat", size=7)
        out = call(self.e, "repo-file", repo="shop-api", hash=HASH, path="blob.dat")
        self.assertEqual(self.client.calls[-2][2], {"format": "meta"})
        self.assertTrue(out["binary"], "a NUL byte marks it binary")
        self.assertIsNone(out["text"])
        self.client.metas[SRC + "src"] = entry("src", "commit_directory")
        with self.assertRaises(ValueError):
            call(self.e, "repo-file", repo="shop-api", hash=HASH, path="src")
        with self.assertRaises(ValueError):
            call(self.e, "repo-file", repo="shop-api", hash=HASH, path="")

    def test_a_big_file_is_cut_and_says_so(self):
        self.client.texts[SRC + "big.txt"] = "x" * (repos.FILE_MAX + 10)
        self.client.metas[SRC + "big.txt"] = entry("big.txt", size=repos.FILE_MAX + 10)
        out = call(self.e, "repo-file", repo="shop-api", hash=HASH, path="big.txt")
        self.assertTrue(out["cut"])
        self.assertEqual(len(out["text"]), repos.FILE_MAX)

    def test_commits_come_from_the_hash(self):
        out = call(self.e, "repo-commits", repo="shop-api", hash=HASH)
        self.assertEqual(out["commits"][0]["message"], "feat: one")
        self.assertEqual(out["commits"][0]["author"], "Pat")
        self.assertTrue(out["more"])
        self.assertEqual(self.client.calls[-1][2]["pagelen"], repos.COMMITS)

    def test_other_routes_and_methods_are_not_ours(self):
        self.assertIsNone(call(self.e, "events"))
        self.assertIsNone(call(self.e, "status"))
        self.assertIsNone(call(self.e, "repos", method="POST"))
        self.assertEqual(self.client.calls, [])

    def test_nothing_but_get_is_ever_sent(self):
        call(self.e, "repos")
        call(self.e, "repo-branches", repo="shop-api")
        call(self.e, "repo-tree", repo="shop-api", hash=HASH, path="")
        call(self.e, "repo-file", repo="shop-api", hash=HASH, path="README.md")
        call(self.e, "repo-commits", repo="shop-api", hash=HASH)
        self.assertTrue(self.client.calls)
        self.assertEqual({c[0] for c in self.client.calls}, {"GET"})
        source = (Path(__file__).resolve().parent.parent / "repos.py").read_text(encoding="utf-8")
        for write in (".send(", "POST", "PUT", "DELETE", "urlopen"):
            self.assertNotIn(write, source)


class CacheTest(unittest.TestCase):
    def test_bounded_by_bytes_oldest_out_first(self):
        cache = repos._Cache()
        saved = repos.KEEP_BYTES
        repos.KEEP_BYTES = 100
        try:
            cache.put("a", 1, cost=60)
            cache.put("b", 2, cost=30)
            cache.get("a")  # a is now the most recent
            cache.put("c", 3, cost=30)
            self.assertIsNone(cache.get("b"))
            self.assertEqual((cache.get("a"), cache.get("c")), (1, 3))
            self.assertEqual(cache.bytes, 90)
        finally:
            repos.KEEP_BYTES = saved

    def test_expired_entries_are_gone(self):
        clock = Clock()
        cache = repos._Cache(clock)
        cache.put("k", "v", ttl=5)
        clock.now += 4
        self.assertEqual(cache.get("k"), "v")
        clock.now += 2
        self.assertIsNone(cache.get("k"))


class ServerRouteTest(unittest.TestCase):
    """Through the real server: the error shape the page sees for each refusal."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        import server
        cls.server_mod = server
        cls.saved = (getattr(server.Desk, "engine", None), getattr(server.Desk, "port", None))
        repos.reset()
        cls.client = shop_client()
        server.Desk.engine = engine(cls.client)
        server.Desk.engine.auth = {}
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Desk)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        server.Desk.port = cls.port  # the host check compares against it
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.server_mod.Desk.engine, cls.server_mod.Desk.port = cls.saved
        repos.reset()

    def get(self, path):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/{path}", headers={"X-Desk-Token": self.server_mod.SECRET})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_answers_and_refusals(self):
        code, out = self.get("repos")
        self.assertEqual(code, 200)
        self.assertEqual(out["repos"][0]["slug"], "shop-api")
        code, out = self.get(f"repo-tree?repo=shop-api&hash={HASH}&path=src")
        self.assertEqual((code, out["entries"][0]["name"]), (200, "app.py"))
        self.assertEqual(self.get("repo-tree?repo=shop-api&hash=nothex1&path=")[0], 400)
        self.assertEqual(self.get(f"repo-tree?repo=shop-api&hash={HASH}&path=..%2F..%2Fuser")[0], 400)
        code, out = self.get("repo-branches?repo=not-ours")
        self.assertEqual(code, 404)
        self.assertIn("not-ours", out["error"])
        code, out = self.get(f"repo-tree?repo=shop-api&hash={OTHER}&path=")
        self.assertEqual((code, out.get("upstream")), (502, 404), "a Bitbucket miss keeps its status for the page")


if __name__ == "__main__":
    unittest.main()
