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
os.environ["PIXELDECK_CREDENTIALS"] = "off"  # nor a token from the credential store
for _key in ("BITBUCKET_TOKEN", "BITBUCKET_EMAIL", "BITBUCKET_WORKSPACE", "TICKET_PREFIXES", "JIRA_SITE", "PIXELDECK_BRIEFS_DIR"):
    os.environ.pop(_key, None)

import bitbucket  # noqa: E402
import repos  # noqa: E402
from bitbucket import BitbucketError  # noqa: E402

HASH = "6297b36c80f500799e4f753fb45b6d13e46bd275"
OTHER = "b2ff6016b15df18ba490049a98b096ce723d66a5"
NEW = "c0ffee" + "0" * 34


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
            if path in self.metas:
                return self.metas[path]
            folder, _, name = path.rpartition("/")
            for value in self.pages.get(folder + "/") or []:  # the entry its folder lists
                if isinstance(value, dict) and value.get("path", "").rsplit("/", 1)[-1] == name:
                    return value
            raise BitbucketError(404, "No such file or directory")
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

    def post_form(self, path, fields, files):
        """The one write. Records what would go out; a test may make Bitbucket refuse it."""
        self.calls.append(("POST", path, {"fields": list(fields), "files": list(files)}))
        if getattr(self, "refuse", None):
            raise BitbucketError(self.refuse, "refused")
        target = dict(fields).get("branch")
        branches = self.pages.get("/repositories/acme/shop-api/refs/branches")
        if isinstance(branches, list) and target and not any(b["name"] == target for b in branches):
            branches.append(branch(target, NEW, "2026-11-13T00:00:00+00:00"))
        return {"status": 201, "location": None}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def engine(client, project="SHOP"):
    e = type("E", (), {})()
    e.client = client
    e.project = lambda: {"key": project, "name": "Shop"} if project else None
    e.root = tempfile.mkdtemp(prefix="pixeldeck-clones-")  # no clones: a pull request goes through Bitbucket's page
    e.pushed = lambda force=False: []
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

    def test_reading_never_writes(self):
        call(self.e, "repos")
        call(self.e, "repo-branches", repo="shop-api")
        call(self.e, "repo-tree", repo="shop-api", hash=HASH, path="")
        call(self.e, "repo-file", repo="shop-api", hash=HASH, path="README.md")
        call(self.e, "repo-commits", repo="shop-api", hash=HASH)
        call(self.e, "repo-ref", repo="shop-api", branch="develop")
        self.assertTrue(self.client.calls)
        self.assertEqual({c[0] for c in self.client.calls}, {"GET"})
        # The module's one way to write is post_form, called once, by the commit, to /src.
        source = (Path(__file__).resolve().parent.parent / "repos.py").read_text(encoding="utf-8")
        for write in (".send(", "PUT", "DELETE", "urlopen"):
            self.assertNotIn(write, source)
        self.assertEqual(source.count(".post_form("), 1)
        self.assertIn('engine.client.post_form(f"/repositories/{_ws(engine)}/{slug}/src"', source)
        self.assertEqual(set(repos.WRITES), {"repo-commit"})


class PairingTest(unittest.TestCase):
    def test_a_chart_is_its_service_plus_the_suffix(self):
        pairs = repos.pair_charts(["shop-api", "shop-api-helm-chart", "hub", "hub-service-helm-chart",
                                   "car-service", "car-service-helm-chart", "postgres-helm-chart", "tools"])
        self.assertEqual(pairs, {"shop-api-helm-chart": "shop-api", "hub-service-helm-chart": "hub",
                                 "car-service-helm-chart": "car-service"})

    def test_only_the_two_rules_pair(self):
        # Adding "-service" is not a rule: car-helm-chart does not deploy car-service.
        self.assertEqual(repos.pair_charts(["car-service", "car-helm-chart", "x-helm-chart-helm-chart"]), {})
        self.assertEqual(repos.pair_charts(["helm-chart", "-helm-chart"]), {})

    def test_two_charts_for_one_service_exact_first(self):
        repos.reset(Clock())
        client = shop_client(pages={LIST: [repo("hub", "2026-11-01T00:00:00+00:00"),
                                           repo("hub-service-helm-chart", "2026-11-02T00:00:00+00:00"),
                                           repo("hub-helm-chart", "2026-11-01T00:00:00+00:00"),
                                           repo("infra-helm-chart", "2026-11-01T00:00:00+00:00"),
                                           repo("e2e-tests", "2026-11-01T00:00:00+00:00")]})
        out = {r["slug"]: r for r in call(engine(client), "repos")["repos"]}
        self.assertEqual(out["hub"]["charts"], ["hub-helm-chart", "hub-service-helm-chart"])
        self.assertEqual(out["hub"]["kind"], "service")
        self.assertEqual((out["hub-service-helm-chart"]["kind"], out["hub-service-helm-chart"]["service"]), ("helm", "hub"))
        self.assertEqual((out["infra-helm-chart"]["kind"], out["infra-helm-chart"]["service"]), ("helm", ""))
        self.assertEqual((out["e2e-tests"]["kind"], out["e2e-tests"]["charts"]), ("other", []))


class MultipartTest(unittest.TestCase):
    def parse(self, boundary, body):
        from email.parser import BytesParser
        from email.policy import HTTP
        head = f"Content-Type: multipart/form-data; boundary={boundary}\r\n\r\n".encode()
        return BytesParser(policy=HTTP).parsebytes(head + body).iter_parts()

    def test_fields_and_file_bytes_come_back_exactly(self):
        data = "line one\r\nligne deux é\r\n\r\n--not a boundary\r\n".encode("utf-8")
        boundary, body = bitbucket.multipart([("message", "fix: é"), ("branch", "feat/x")], [("/docs/a b.md", "a b.md", data)])
        parts = list(self.parse(boundary, body))
        self.assertEqual([p.get_param("name", header="content-disposition") for p in parts], ["message", "branch", "/docs/a b.md"])
        self.assertEqual(parts[0].get_payload(decode=True).decode("utf-8"), "fix: é")
        self.assertEqual(parts[2].get_param("filename", header="content-disposition"), "a b.md")
        self.assertEqual(parts[2].get_payload(decode=True), data, "a file's bytes, CRLF included, go out untouched")

    def test_the_boundary_never_occurs_in_a_value(self):
        boundary, body = bitbucket.multipart([("message", "x--abc--y")], [], boundary="abc")
        self.assertNotEqual(boundary, "abc")
        self.assertEqual(body.count(f"--{boundary}".encode()), 2)

    def test_names_cannot_break_the_header(self):
        for bad in ('a"b', "a\r\nX-Evil: 1", "a\nb"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                bitbucket.multipart([], [(bad, "f", b"x")])
            with self.assertRaises(ValueError):
                bitbucket.multipart([], [("/f", bad, b"x")])


class CommitTest(unittest.TestCase):
    FEAT = f"/repositories/acme/shop-api/src/{OTHER}/"

    def setUp(self):
        repos.reset(Clock())
        self.client = shop_client()
        self.client.texts.update({
            SRC + "crlf.txt": "one\r\ntwo\r\n", SRC + "mixed.txt": "one\r\ntwo\n", SRC + "bad.txt": "caf\ufffd\n",
            SRC + "run.sh": "#!/bin/sh\n", self.FEAT + "Makefile": "all:\n\techo hi\n",
        })
        self.client.pages[SRC][2:2] = [entry("crlf.txt"), entry("mixed.txt"), entry("bad.txt"), entry("run.sh", attributes=["executable"])]
        self.client.pages[self.FEAT] = [entry("Makefile")]
        self.e = engine(self.client)

    def body(self, **over):
        b = {"repo": "shop-api", "branch": "develop", "parent": HASH, "path": "Makefile",
             "content": "all:\n\techo hello\n", "message": "fix: say hello", "new_branch": "fix/say-hello"}
        b.update(over)
        return b

    def commit(self, **over):
        return repos.route(None, self.e, "POST", "repo-commit", {}, self.body(**over))

    def posts(self):
        return [c for c in self.client.calls if c[0] == "POST"]

    def refused(self, error, **over):
        with self.assertRaises(error) as ctx:
            self.commit(**over)
        self.assertEqual(self.posts(), [], "refused before anything was sent")
        return str(ctx.exception)

    def test_a_new_branch_from_the_commit_you_edited(self):
        out = self.commit()
        [(method, path, sent)] = self.posts()
        self.assertEqual(path, "/repositories/acme/shop-api/src")
        fields = dict(sent["fields"])
        self.assertEqual(fields, {"message": "fix: say hello", "branch": "fix/say-hello", "parents": HASH},
                         "branch and parents always go: without branch Bitbucket would commit on the main branch")
        self.assertEqual(sent["files"], [("/Makefile", "Makefile", b"all:\n\techo hello\n")])
        self.assertEqual((out["branch"], out["from"], out["new_branch"], out["hash"]), ("fix/say-hello", "develop", True, NEW))
        self.assertEqual(out["pr"]["flow"], "web")
        self.assertIn("pull-requests/new?source=fix%2Fsay-hello&dest=develop", out["pr"]["url"])

    def test_straight_to_a_feature_branch_when_its_tip_is_the_parent(self):
        out = self.commit(branch="feat/a-b", parent=OTHER, new_branch="")
        [(_, _, sent)] = self.posts()
        self.assertEqual(dict(sent["fields"])["branch"], "feat/a-b")
        self.assertEqual(dict(sent["fields"])["parents"], OTHER)
        self.assertFalse(out["new_branch"])
        self.assertNotIn("pr", out)

    def test_never_straight_to_the_main_branches(self):
        self.assertIn("does not commit straight to develop", self.refused(ValueError, new_branch=""))
        for name in ("main", "master", "develop"):
            with self.subTest(name=name):
                self.refused(ValueError, new_branch=name)

    def test_a_branch_that_moved_is_refused(self):
        msg = self.refused(ValueError, branch="feat/a-b", parent=HASH, new_branch="")
        self.assertIn("moved since you opened the file", msg)

    def test_an_existing_branch_is_not_a_new_one(self):
        self.assertIn("exists already", self.refused(ValueError, new_branch="feat/a-b"))

    def test_the_request_is_checked_first(self):
        for over in ({"parent": HASH[:7]}, {"parent": "z" * 40}, {"path": "../x"}, {"path": ""}, {"path": 'a"b'},
                     {"message": ""}, {"message": "a\x00b"}, {"message": "x" * (repos.MESSAGE_MAX + 1)},
                     {"content": None}, {"content": "x" * (repos.FILE_MAX + 1)}, {"new_branch": "bad name"},
                     {"branch": 'a"b'}):
            with self.subTest(over=over):
                self.refused(ValueError, **over)
        self.refused(KeyError, repo="not-ours")

    def test_only_what_was_shown_whole_can_be_saved(self):
        self.assertIn("nothing changed", self.refused(ValueError, content="all:\n\techo hi\n"))
        self.assertIn("line endings", self.refused(ValueError, path="crlf.txt", content="one\ntwo\nthree\n"))
        self.commit(path="crlf.txt", content="one\r\ntwo\r\nthree\r\n", new_branch="fix/crlf")
        self.client.calls.clear()
        self.assertIn("mixes line endings", self.refused(ValueError, path="mixed.txt", content="x\n"))
        self.assertIn("UTF-8", self.refused(ValueError, path="bad.txt", content="cafe\n"))
        self.assertIn("Executable", self.refused(ValueError, path="run.sh", content="#!/bin/sh\necho\n"))
        self.assertIn("Binary", self.refused(ValueError, path="logo.png", content="x"))

    def test_a_token_without_write_is_named(self):
        self.client.refuse = 403
        with self.assertRaises(PermissionError) as ctx:
            self.commit()
        self.assertIn("lack repository write", str(ctx.exception))
        self.client.refuse = 409
        with self.assertRaises(ValueError):
            self.commit(new_branch="fix/again")

    def test_after_the_commit_nothing_can_fail_the_answer(self):
        real = self.client.get
        def flaky(path, params=None):
            if any(c[0] == "POST" for c in self.client.calls):
                raise BitbucketError(500, "down")
            return real(path, params)
        self.client.get = flaky
        out = self.commit()
        self.assertIsNone(out["hash"])
        self.assertEqual(out["pr"]["flow"], "web")

    def test_a_branch_in_your_clone_opens_pixeldecks_own_dialog(self):
        saved = (repos.gitscan.repo_dirs, repos.gitscan.git)
        ran = []
        repos.gitscan.repo_dirs = lambda root: {"shop-api": Path(root)}
        repos.gitscan.git = lambda repo_dir, *args, timeout=20: ran.append(args) or ""
        self.e.pushed = lambda force=False: [{"repo": "shop-api", "branch": "fix/say-hello"}]
        try:
            out = self.commit()
        finally:
            repos.gitscan.repo_dirs, repos.gitscan.git = saved
        self.assertEqual(out["pr"], {"flow": "preview"})
        self.assertEqual(ran, [("fetch", "--quiet", "origin", "fix/say-hello")])


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

    def post(self, route, payload):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/{route}", method="POST", data=payload,
                                     headers={"X-Desk-Token": self.server_mod.SECRET, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_a_commit_may_carry_a_big_file_other_routes_may_not(self):
        big = json.dumps({"repo": "shop-api", "branch": "develop", "parent": HASH, "path": "Makefile",
                          "message": "m", "content": "x" * 200_000}).encode()
        code, out = self.post("repo-commit", big)
        self.assertEqual(code, 400)
        self.assertIn("does not commit straight to develop", out["error"], "the body was read, the route refused")
        code, out = self.post("term-input", json.dumps({"id": "x", "data": "x" * 200_000}).encode())
        self.assertEqual((code, out["error"]), (400, "body too large"))
        self.assertEqual([c for c in self.client.calls if c[0] == "POST"], [])

    def test_a_token_without_write_answers_403_not_502(self):
        body = json.dumps({"repo": "shop-api", "branch": "develop", "parent": HASH, "path": "Makefile", "message": "m",
                           "content": "all:\n\techo hello\n", "new_branch": "fix/no-write"}).encode()
        self.client.refuse = 403
        try:
            code, out = self.post("repo-commit", body)
        finally:
            self.client.refuse = None
        self.assertEqual(code, 403, "a 502 would read as Bitbucket being down; a 401 would reload the page")
        self.assertIn("lack repository write", out["error"])

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
