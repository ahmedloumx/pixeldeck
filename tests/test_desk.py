"""Run with: python3 -m unittest discover -s tests  (from the PixelDeck folder)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env: they get settings of their own, and no token.
_ENV = Path(tempfile.mkdtemp(prefix="pixeldeck-test-")) / ".env"
_ENV.write_text("BITBUCKET_WORKSPACE=acme\nTICKET_PREFIXES=ABC,XYZ\nJIRA_SITE=https://acme.atlassian.net\n")
os.environ["PIXELDECK_ENV"] = str(_ENV)
for _key in ("BITBUCKET_TOKEN", "BITBUCKET_EMAIL", "BITBUCKET_WORKSPACE", "TICKET_PREFIXES", "JIRA_SITE", "PIXELDECK_BRIEFS_DIR"):
    os.environ.pop(_key, None)

import config  # noqa: E402
import launcher  # noqa: E402
import model  # noqa: E402

AUTHOR = {"uuid": "{author}", "display_name": "Pat Author"}
ALICE = {"uuid": "{alice}", "display_name": "Alice Reviewer"}
BOB = {"uuid": "{bob}", "display_name": "Bob Reviewer"}
CARL = {"uuid": "{carl}", "display_name": "Carl Passerby"}


def raw_pr(**over):
    pr = {
        "id": 42,
        "title": "feat(shop): suggest a cheaper bundle (ABC-9001)",
        "state": "OPEN",
        "author": AUTHOR,
        "source": {"branch": {"name": "feat/ABC-9001-suggest-cheaper-bundle"}, "commit": {"hash": "abc123"}},
        "destination": {"branch": {"name": "develop"}, "repository": {"full_name": "acme/shop-api"}},
        "created_on": "2026-11-01T09:00:00+00:00",
        "updated_on": "2026-11-10T09:00:00+00:00",
        "comment_count": 3,
        "task_count": 1,
        "links": {"html": {"href": "https://bitbucket.org/acme/shop-api/pull-requests/42"}},
        "reviewers": [ALICE, BOB],
        "participants": [
            {"user": AUTHOR, "role": "PARTICIPANT", "approved": False, "state": None},
            {"user": ALICE, "role": "REVIEWER", "approved": False, "state": "changes_requested"},
            {"user": CARL, "role": "PARTICIPANT", "approved": True, "state": "approved"},
        ],
    }
    pr.update(over)
    return pr


def comment(cid, user, raw, parent=None, inline=None, resolution=None, deleted=False, created="2026-11-02T10:00:00+00:00"):
    c = {"id": cid, "user": user, "content": {"raw": raw}, "created_on": created, "updated_on": created,
         "deleted": deleted, "links": {"html": {"href": f"https://x/#comment-{cid}"}}}
    if parent:
        c["parent"] = {"id": parent}
    if inline:
        c["inline"] = inline
    if resolution:
        c["resolution"] = resolution
    return c


class SlimPrTest(unittest.TestCase):
    def test_review_states_merge_reviewers_and_participants(self):
        pr = model.slim_pr(raw_pr())
        states = {r["name"]: (r["state"], r["reviewer"]) for r in pr["reviews"]}
        self.assertEqual(states["Alice Reviewer"], ("changes_requested", True))
        self.assertEqual(states["Bob Reviewer"], ("pending", True))
        self.assertEqual(states["Carl Passerby"], ("approved", False))
        self.assertNotIn("Pat Author", states)
        self.assertEqual(pr["reviews"][0]["name"], "Alice Reviewer")

    def test_repo_branch_and_ticket(self):
        pr = model.slim_pr(raw_pr())
        self.assertEqual(pr["repo"], "shop-api")
        self.assertEqual(pr["source"], "feat/ABC-9001-suggest-cheaper-bundle")
        self.assertEqual(pr["ticket"]["key"], "ABC-9001")

    def test_bug_prefix_is_not_a_jira_key(self):
        self.assertIsNone(model.ticket_of("fix/XYZ-BUG-99-rotate-acks"))
        self.assertEqual(model.ticket_of("feat/ABC-9002-ABC-9003-invoices")["key"], "ABC-9002")

    def test_missing_participants_does_not_crash(self):
        pr = model.slim_pr(raw_pr(participants=None, reviewers=None))
        self.assertEqual(pr["reviews"], [])


class ThreadTest(unittest.TestCase):
    def setUp(self):
        inline = {"path": "app/api/packs.py", "to": 88, "from": None}
        self.comments = [
            comment(1, ALICE, "This query runs per row.", inline=inline),
            comment(2, AUTHOR, "Batched it.", parent=1, created="2026-11-03T10:00:00+00:00"),
            comment(3, ALICE, "Still N+1 on the pack join.", parent=2, created="2026-11-04T10:00:00+00:00"),
            comment(4, BOB, "Name nit.", inline={"path": "app/models.py", "to": 12}, resolution={"type": "comment_resolution", "user": AUTHOR}),
            comment(5, BOB, "Looks good overall."),
            comment(6, CARL, "gone", deleted=True),
            comment(7, ALICE, "Answered already?", inline={"path": "README.md", "to": 3}),
            comment(8, AUTHOR, "Yes, see above.", parent=7, created="2026-11-05T10:00:00+00:00"),
        ]

    def test_threads(self):
        threads = model.build_threads(self.comments, AUTHOR["uuid"])
        by_id = {t["id"]: t for t in threads}
        self.assertNotIn(6, by_id, "fully deleted thread is dropped")
        self.assertEqual([m["id"] for m in by_id[1]["messages"]], [1, 2, 3])
        self.assertTrue(by_id[1]["awaiting_author"])
        self.assertEqual((by_id[1]["path"], by_id[1]["line"], by_id[1]["side"]), ("app/api/packs.py", 88, "new"))
        self.assertTrue(by_id[4]["resolved"])
        self.assertFalse(by_id[4]["unresolved"])
        self.assertFalse(by_id[5]["unresolved"], "general comments are never counted as unresolved")
        self.assertTrue(by_id[7]["unresolved"])
        self.assertFalse(by_id[7]["awaiting_author"], "author had the last word")
        att = model.attention_of(threads, model.slim_tasks([{"id": 1, "state": "UNRESOLVED", "content": {"raw": "x"}}]))
        self.assertEqual(att, {"unresolved": 2, "awaiting": 1, "tasks_open": 1, "threads": 4})

    def test_pending_drafts_are_ignored(self):
        drafts = [dict(comment(9, ALICE, "draft", inline={"path": "a.py", "to": 1}), pending=True)]
        self.assertEqual(model.build_threads(drafts, AUTHOR["uuid"]), [])


class BriefTest(unittest.TestCase):
    def _detail(self, raw_text):
        pr = model.slim_pr(raw_pr())
        threads = model.build_threads([comment(1, ALICE, raw_text, inline={"path": "a.py", "to": 5})], AUTHOR["uuid"])
        return pr, {"threads": threads, "tasks": []}

    def test_reviewer_backticks_cannot_close_the_fence(self):
        pr, detail = self._detail("try ```\nrm -rf /\n``` and `x`")
        brief = launcher.build_brief("fix", pr, detail, "/tmp/wt", True)
        self.assertIn("````text\ntry ```", brief)
        self.assertIn("\n````", brief)

    def test_summarize_forbids_edits_and_fix_forbids_push(self):
        pr, detail = self._detail("hi")
        self.assertIn("Do not edit files, commit, or push", launcher.build_brief("summarize", pr, detail, "/r", True))
        self.assertIn("Do NOT push", launcher.build_brief("fix", pr, detail, "/r", True))
        push = launcher.build_brief("fix_push", pr, detail, "/r", True)
        self.assertIn("--force-with-lease=feat/ABC-9001-suggest-cheaper-bundle:<exact remote sha>", push)
        self.assertIn("ABC-9001 in every subject", push)

    def test_fix_refused_on_someone_elses_pr(self):
        pr, detail = self._detail("hi")
        with self.assertRaises(PermissionError):
            launcher.launch("/nonexistent", "fix_push", pr, detail, is_mine=False)

    def test_unknown_action_rejected(self):
        pr, detail = self._detail("hi")
        with self.assertRaises(ValueError):
            launcher.launch("/nonexistent", "shell", pr, detail, is_mine=True)



class DiffTest(unittest.TestCase):
    def test_line_numbers_follow_the_hunk_header(self):
        text = "\n".join([
            "diff --git a/app/x.py b/app/x.py",
            "index 1..2 100644",
            "--- a/app/x.py",
            "+++ b/app/x.py",
            "@@ -10,3 +10,4 @@ def f():",
            " keep",
            "-old",
            "+new",
            "+added",
            " tail",
            "diff --git a/img.png b/img.png",
            "new file mode 100644",
            "Binary files /dev/null and b/img.png differ",
        ])
        files = model.parse_diff(text)
        self.assertEqual([f["path"] for f in files], ["app/x.py", "img.png"])
        lines = files[0]["hunks"][0]["lines"]
        self.assertEqual(lines[0], [" ", 10, 10, "keep"])
        self.assertEqual(lines[1], ["-", 11, None, "old"])
        self.assertEqual(lines[2], ["+", None, 11, "new"])
        self.assertEqual(lines[3], ["+", None, 12, "added"])
        self.assertEqual(lines[4], [" ", 12, 13, "tail"])
        self.assertEqual((files[0]["added"], files[0]["removed"]), (2, 1))
        self.assertTrue(files[1]["binary"])
        self.assertEqual(files[1]["status"], "added")


class LauncherIdTest(unittest.TestCase):
    def test_desktop_entry_names_the_window_app_id(self):
        # GNOME groups a Wayland window under the launcher whose file name is the window's
        # app id. If the two drift apart, the dock shows a generic icon and a second entry.
        root = Path(__file__).resolve().parent.parent
        install = (root / "install-desktop.sh").read_text(encoding="utf-8")
        self.assertIn(f'ID="{config.APP_ID}"', install)
        self.assertIn("APP_ID = config.APP_ID", (root / "app_window.py").read_text(encoding="utf-8"))
        self.assertIn("StartupWMClass=$ID", install)


# The page renders teammates' Markdown in a window that also drives terminals, so its output
# is pinned here: only these tags and attributes, and only these link schemes, ever come out.
MD_TAGS = {"p", "br", "strong", "em", "del", "code", "pre", "a", "h4", "h5", "h6", "ul", "ol", "li", "hr",
           "blockquote", "div", "table", "thead", "tbody", "tr", "th", "td", "input"}
MD_ATTRS = {"href", "target", "rel", "class", "start", "type", "disabled", "checked", "aria-label"}
MD_HOSTILE = [
    "<script>alert(1)</script><img src=x onerror=alert(1)>",
    "[a](javascript:alert) [b](JAVASCRIPT:alert) [c](data:text/html,x) [d](//evil.example)",
    '[a"onmouseover="x](https://x.example/"onmouseover="alert(1))',
    "https://x.example/\"><svg onload=alert(1)>",
    "[a](https://a/<https://b/onmouseover=alert(1)//>)",
    '[a](https://a/`x"y`) ![i](https://a/\\"z) <https://a/`b"c`>',
    "x\u00000\u0000y [a](https://a/\u00001\u0000)",
    "| <img src=x> | b |\n|---|---|\n| <svg onload=x> | 2 |",
    "## <i onclick=x>h</i>\n- <b>li</b>\n> <u>q</u>\n- [ ] <b>t</b>",
    "```\n</code></pre><script>x</script>\n```",
]


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.attrs, self.hrefs = set(), set(), []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        for name, value in attrs:
            self.attrs.add(name)
            if name == "href":
                self.hrefs.append(value or "")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class MarkdownSafetyTest(unittest.TestCase):
    def render(self, texts):
        src = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")
        code = src[src.index("function esc(value)"):src.index("function initials(")] + src[src.index("const MD_FENCE"):src.index("function trail(")]
        script = code + "\nprocess.stdout.write(JSON.stringify(JSON.parse(require('fs').readFileSync(0, 'utf8')).map(md)));"
        out = subprocess.run(["node", "-e", script], input=json.dumps(texts), capture_output=True, text=True, check=True, timeout=30)
        return json.loads(out.stdout)

    def test_hostile_input_yields_only_known_markup(self):
        for text, html in zip(MD_HOSTILE, self.render(MD_HOSTILE)):
            seen = _Collect()
            seen.feed(html)
            with self.subTest(text=text):
                self.assertLessEqual(seen.tags, MD_TAGS)
                self.assertLessEqual(seen.attrs, MD_ATTRS)
                for href in seen.hrefs:
                    self.assertRegex(href, r"^(https?:|mailto:)")
                    self.assertNotRegex(href, r'[<>"]')

    def test_markdown_blocks_render(self):
        table, nested, heading = self.render([
            "| Figure | develop | this PR |\n|---|---|---|\n| Net battery | 0 | 109 |",
            "1. first\n   - inner `code`\n2. second",
            "## What changes\ntext",
        ])
        self.assertIn('<td class="is-num">109</td>', table)
        self.assertIn("<ol><li><p>first</p><ul><li><p>inner <code>code</code></p></li></ul></li>", nested)
        self.assertIn('<h5 class="md-h md-h2">What changes</h5>', heading)


class ConfigTest(unittest.TestCase):
    def test_env_file_syntax(self):
        text = """# comment
export BITBUCKET_EMAIL=me@example.com
BITBUCKET_TOKEN="tok en#1"
JIRA_SITE=https://acme.atlassian.net   # trailing note
EMPTY=
bad line
1BAD=x
"""
        self.assertEqual(config.parse_env(text), {
            "BITBUCKET_EMAIL": "me@example.com", "BITBUCKET_TOKEN": "tok en#1",
            "JIRA_SITE": "https://acme.atlassian.net", "EMPTY": ""})

    def test_environment_wins_over_the_file(self):
        self.assertEqual(config.workspace(), "acme")
        os.environ["BITBUCKET_WORKSPACE"] = "other"
        try:
            self.assertEqual(config.workspace(), "other")
        finally:
            del os.environ["BITBUCKET_WORKSPACE"]

    def test_folders_follow_overrides(self):
        os.environ["PIXELDECK_CACHE_DIR"] = "~/somewhere"
        try:
            self.assertEqual(config.folder("cache"), Path.home() / "somewhere")
        finally:
            del os.environ["PIXELDECK_CACHE_DIR"]

    def test_generic_tickets_without_a_prefix_list(self):
        os.environ["TICKET_PREFIXES"] = " "
        try:
            self.assertEqual(model.ticket_of("feat/QA-12-x")["key"], "QA-12")
            self.assertIsNone(model.ticket_of("fix: handle UTF-8 names"))
            self.assertIsNone(model.ticket_of("fix/XYZ-BUG-99"))
        finally:
            del os.environ["TICKET_PREFIXES"]


class BriefTemplateTest(unittest.TestCase):
    def test_own_templates_win_and_missing_ones_fall_back(self):
        own = Path(tempfile.mkdtemp(prefix="pixeldeck-briefs-"))
        (own / "summarize.md").write_text("## Mine\nOnly {source}, {title}\n")
        os.environ["PIXELDECK_BRIEFS_DIR"] = str(own)
        try:
            pr = model.slim_pr(raw_pr(title="Use {source} braces"))
            brief = launcher.build_brief("summarize", pr, {"threads": [], "tasks": []}, "/r", True)
            self.assertIn("## Mine\nOnly feat/ABC-9001-suggest-cheaper-bundle, Use {source} braces", brief)
            self.assertIn("# Pull request #42 in acme/shop-api", brief, "head.md comes from the defaults")
        finally:
            del os.environ["PIXELDECK_BRIEFS_DIR"]

    def test_windows_gets_the_brief_by_file(self):
        state = Path(tempfile.mkdtemp(prefix="pixeldeck-state-"))
        os.environ["PIXELDECK_STATE_DIR"] = str(state)
        os.environ["PIXELDECK_CLAUDE"] = sys.executable
        try:
            argv = launcher.brief_argv("the brief", "shop-api-42-fix", by_file=True)
            self.assertEqual(argv[0], sys.executable)
            self.assertEqual((state / "briefs" / "shop-api-42-fix.md").read_text(encoding="utf-8"), "the brief")
            self.assertIn(str(state / "briefs" / "shop-api-42-fix.md"), argv[1])
        finally:
            del os.environ["PIXELDECK_STATE_DIR"], os.environ["PIXELDECK_CLAUDE"]


class PortabilityTest(unittest.TestCase):
    """Every module imports on Windows and macOS. Run in a child Python that pretends."""

    def _import_as(self, platform):
        probe = f"""
import builtins, sys
# The standard library picks its own platform code at import, so load it first, then pretend.
import shutil, subprocess, signal, webbrowser, shlex, mimetypes, secrets, threading, urllib.request, http.server, fnmatch, json, base64
sys.platform = {platform!r}
real = builtins.__import__
def guard(name, *a, **k):
    if sys.platform == "win32" and name in ("fcntl", "pty", "termios"):
        raise ImportError(name)
    if name == "winpty":
        raise ImportError(name)
    return real(name, *a, **k)
builtins.__import__ = guard
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
import config, terminals, launcher, opener, actions, engine, gitscan, server, pixeldeck
# shutil.which itself needs the real Windows API, so the shell is only asked for on macOS here.
shell = "-" if sys.platform == "win32" else server.shell_argv()[0]
print(terminals.SessionType.__name__, "|", terminals.UNAVAILABLE or "", "|", config.folder("cache"), "|", shell)
"""
        env = {**os.environ, "PIXELDECK_ENV": str(_ENV), "APPDATA": "C:/Users/u/AppData/Roaming", "LOCALAPPDATA": "C:/Users/u/AppData/Local"}
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return out.stdout.strip().split(" | ")

    def test_windows(self):
        kind, why, cache, _shell = self._import_as("win32")
        self.assertEqual(kind, "WindowsSession")
        self.assertIn("pywinpty", why)
        self.assertTrue(cache.replace("\\", "/").endswith("AppData/Local/PixelDeck/Cache"), cache)

    def test_macos(self):
        kind, why, cache, shell = self._import_as("darwin")
        self.assertEqual((kind, why), ("UnixSession", ""))
        self.assertTrue(shell, "a login shell is chosen")
        self.assertTrue(cache.endswith("Library/Caches/PixelDeck"), cache)


class ServerErrorTest(unittest.TestCase):
    def test_a_bitbucket_error_answers_instead_of_dropping_the_request(self):
        # A missing token used to raise inside the error handler and close the connection.
        import server
        sent = {}
        desk = server.Desk.__new__(server.Desk)
        desk._send = lambda code, body: sent.update(code=code, body=body)
        desk._error(502, "No BITBUCKET_TOKEN", upstream=0, auth={"ok": False})
        self.assertEqual(sent["code"], 502)
        self.assertEqual(sent["body"]["upstream"], 0)
        source = (Path(__file__).resolve().parent.parent / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("_error(HTTPStatus.BAD_GATEWAY, exc.message, status=", source)


class _FakeClient:
    workspace = "acme"

    def __init__(self, live_state="OPEN", strategy="squash", open_lists=()):
        self.calls, self.live_state, self.strategy = [], live_state, strategy
        self.open_lists = list(open_lists)

    def get(self, path, params=None):
        self.calls.append(("GET", path, None))
        return {"state": self.live_state, "destination": {"branch": {"name": "develop", "default_merge_strategy": self.strategy}}}

    def send(self, method, path, body=None, params=None):
        self.calls.append((method, path, body))
        return {"state": "MERGED"}

    def user_prs(self, uuid, state, **kw):
        return self.open_lists.pop(0)

    def calls_last_hour(self):
        return 0


class MergeTest(unittest.TestCase):
    def _engine(self, author="{me}", **client):
        import actions
        pr = model.slim_pr(raw_pr(author={"uuid": author, "display_name": "Someone"}))
        engine = type("E", (), {})()
        engine.client = _FakeClient(**client)
        engine.find_pr = lambda repo, pr_id: pr if (repo, pr_id) == (pr["repo"], pr["id"]) else None
        engine.me = lambda: {"uuid": "{me}"}
        engine.refresh_person = lambda uuid: None
        engine.pushed = lambda force=False: []
        engine.filed = []
        engine.file_merged = lambda uuid, raw: engine.filed.append((uuid, raw)) or {"id": pr["id"], "state": "MERGED"}
        return actions, engine, pr

    def test_merges_with_the_destination_default_strategy(self):
        actions, engine, pr = self._engine()
        out = actions.merge(engine, {"repo": pr["repo"], "id": pr["id"]})
        self.assertEqual(out["state"], "MERGED")
        self.assertEqual(out["pr"]["state"], "MERGED", "the merged pull request comes back to show at once")
        self.assertEqual(engine.filed[0][0], "{me}")
        method, path, body = engine.client.calls[-1]
        self.assertEqual((method, path), ("POST", "/repositories/acme/shop-api/pullrequests/42/merge"))
        self.assertEqual(body, {"type": "pullrequest", "merge_strategy": "squash"})

    def test_refuses_someone_elses_pull_request(self):
        actions, engine, pr = self._engine(author="{other}")
        with self.assertRaises(PermissionError):
            actions.merge(engine, {"repo": pr["repo"], "id": pr["id"]})
        self.assertEqual(engine.client.calls, [])

    def test_refuses_one_that_bitbucket_no_longer_has_open(self):
        actions, engine, pr = self._engine(live_state="MERGED")
        with self.assertRaises(ValueError):
            actions.merge(engine, {"repo": pr["repo"], "id": pr["id"]})
        self.assertNotIn("POST", [c[0] for c in engine.client.calls])


class OpenLaneTest(unittest.TestCase):
    def _engine(self, open_lists):
        import engine as engine_mod
        eng = engine_mod.Engine.__new__(engine_mod.Engine)
        eng.lock = __import__("threading").RLock()
        eng.client = _FakeClient(open_lists=open_lists)
        a, b = model.slim_pr(raw_pr()), model.slim_pr(raw_pr(id=43))
        eng.data = {"people": {"{me}": {"open": [a, b], "merged": [], "synced_at": 1, "open_at": 1}}}
        eng.saved = 0
        eng._save = lambda: setattr(eng, "saved", eng.saved + 1)
        eng.full = 0
        eng.refresh_person = lambda uuid: setattr(eng, "full", eng.full + 1)
        return eng, a, b

    def test_a_new_approval_is_taken_in_one_call(self):
        eng, a, b = self._engine([])
        approved = raw_pr(participants=[{"user": ALICE, "role": "REVIEWER", "approved": True, "state": "approved"}])
        eng.client.open_lists = [[approved, raw_pr(id=43)]]
        eng.refresh_open("{me}")
        reviews = {r["name"]: r["state"] for r in eng.data["people"]["{me}"]["open"][0]["reviews"]}
        self.assertEqual(reviews["Alice Reviewer"], "approved")
        self.assertEqual((eng.saved, eng.full), (1, 0))

    def test_a_pull_request_that_left_the_list_gets_the_full_refresh(self):
        eng, a, b = self._engine([])
        eng.client.open_lists = [[raw_pr()]]
        eng.refresh_open("{me}")
        self.assertEqual(eng.full, 1)


class DropTest(unittest.TestCase):
    def setUp(self):
        import io
        import server
        self.server, self.io = server, io
        self.folder = Path(tempfile.mkdtemp(prefix="pixeldeck-drops-")) / "drops"

    def save(self, name, data, length=None):
        return self.server.save_drop(self.folder, name, self.io.BytesIO(data), len(data) if length is None else length)

    def test_keeps_only_the_file_name(self):
        path = self.save("../../etc/passwd", b"x")
        self.assertEqual(path.parent, self.folder)
        self.assertTrue(path.name.endswith("-passwd"))
        path = self.save("C:\\Users\\me\\shot.png", b"png")
        self.assertTrue(path.name.endswith("-shot.png"))
        self.assertEqual(path.read_bytes(), b"png")

    def test_refuses_bad_names_sizes_and_short_uploads(self):
        for name in ("", "..", "a\nb.txt", "tab\tname"):
            with self.assertRaises(ValueError):
                self.save(name, b"x")
        with self.assertRaises(ValueError):
            self.save("empty.txt", b"")
        with self.assertRaises(ValueError):
            self.save("big.bin", b"x", length=self.server.DROP_MAX + 1)
        with self.assertRaises(ValueError):
            self.save("cut.txt", b"abc", length=10)
        self.assertEqual([p.name for p in self.folder.iterdir()], [], "a failed upload leaves nothing behind")

    def test_never_reads_past_the_length_and_never_replaces(self):
        a = self.save("same.txt", b"hello world", length=5)
        b = self.save("same.txt", b"other")
        self.assertEqual(a.read_bytes(), b"hello")
        self.assertNotEqual(a, b)
        if os.name != "nt":
            self.assertEqual(a.stat().st_mode & 0o777, 0o600)


class FileMergedTest(unittest.TestCase):
    def test_moves_the_pull_request_from_open_to_merged(self):
        import engine as engine_mod
        eng = engine_mod.Engine.__new__(engine_mod.Engine)
        eng.lock = __import__("threading").RLock()
        a, b = model.slim_pr(raw_pr()), model.slim_pr(raw_pr(id=43))
        eng.data = {"people": {"{me}": {"open": [a, b], "merged": [model.slim_pr(raw_pr(id=7, state="MERGED"))]}}}
        eng._save = lambda: None
        eng.file_merged("{me}", raw_pr(state="MERGED"))
        entry = eng.data["people"]["{me}"]
        self.assertEqual([p["id"] for p in entry["open"]], [43])
        self.assertEqual([(p["id"], p["state"]) for p in entry["merged"]], [(42, "MERGED"), (7, "MERGED")])


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class SyncTest(unittest.TestCase):
    """Real git: a bare remote, a clone, a feature branch behind develop."""

    def setUp(self):
        import actions
        self.actions = actions
        self.tmp = Path(tempfile.mkdtemp(prefix="pixeldeck-sync-"))
        os.environ["PIXELDECK_STATE_DIR"] = str(self.tmp / "state")
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"}
        remote, self.clone = self.tmp / "remote.git", self.tmp / "clone"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "develop", str(remote))
        self.git(self.tmp, "clone", "-q", str(remote), str(self.clone))
        # The sync's own merge commit runs through gitscan, whose environment is fixed at import:
        # the identity lives in the clone's config, which its temporary worktree shares.
        self.git(self.clone, "config", "user.name", "T")
        self.git(self.clone, "config", "user.email", "t@example.com")
        self.commit("app.txt", "one\n", "first")
        self.git(self.clone, "push", "-q", "origin", "HEAD:develop")
        self.git(self.clone, "switch", "-q", "-c", "feat/x")
        self.commit("feature.txt", "feature\n", "feature")
        self.git(self.clone, "push", "-q", "origin", "feat/x")
        self.git(self.clone, "switch", "-q", "develop")
        self.commit("app.txt", "one\ntwo\n", "develop moves on")
        self.git(self.clone, "push", "-q", "origin", "develop")
        actions._clone = lambda root, repo: self.clone
        pr = model.slim_pr(raw_pr(author={"uuid": "{me}", "display_name": "Me"},
                                  source={"branch": {"name": "feat/x"}, "commit": {"hash": "x"}},
                                  destination={"branch": {"name": "develop"}, "repository": {"full_name": "acme/shop-api"}}))
        self.engine = type("E", (), {})()
        self.engine.find_pr = lambda repo, pr_id: pr
        self.engine.me = lambda: {"uuid": "{me}"}
        self.engine.refresh_person = lambda uuid: None
        self.engine.pushed = lambda force=False: []
        self.body = {"repo": pr["repo"], "id": pr["id"]}

    def tearDown(self):
        os.environ.pop("PIXELDECK_STATE_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=self.env).stdout

    def commit(self, name, text, message):
        (self.clone / name).write_text(text)
        self.git(self.clone, "add", name)
        self.git(self.clone, "commit", "-q", "-m", message)

    def remote_log(self, ref):
        return self.git(self.clone, "log", "--format=%s", f"origin/{ref}")

    def test_status_then_sync_merges_develop_and_pushes(self):
        status = self.actions.sync_status(self.engine, self.tmp, "shop-api", 42)
        self.assertEqual((status["behind"], status["ahead"]), (1, 1))
        out = self.actions.sync(self.engine, self.tmp, self.body)
        self.assertEqual((out["state"], out["merged"]), ("synced", 1))
        self.git(self.clone, "fetch", "-q", "origin")
        self.assertEqual(self.remote_log("feat/x").splitlines()[0], "Merged develop into feat/x")
        self.assertEqual(self.actions.sync_status(self.engine, self.tmp, "shop-api", 42)["behind"], 0)
        self.assertEqual(self.actions.sync(self.engine, self.tmp, self.body)["state"], "up to date")
        self.assertEqual(self.git(self.clone, "branch", "--show-current").strip(), "develop", "your checkout is untouched")
        self.assertEqual(list((self.tmp / "state" / "sync").iterdir()), [], "the temporary worktree is gone")

    def test_a_conflict_pushes_nothing(self):
        self.git(self.clone, "switch", "-q", "feat/x")
        self.commit("app.txt", "one\nmine\n", "clash")
        self.git(self.clone, "push", "-q", "origin", "feat/x")
        before = self.remote_log("feat/x")
        with self.assertRaises(self.actions.SyncConflict) as caught:
            self.actions.sync(self.engine, self.tmp, self.body)
        self.assertEqual(caught.exception.files, ["app.txt"])
        self.git(self.clone, "fetch", "-q", "origin")
        self.assertEqual(self.remote_log("feat/x"), before)

    def test_only_your_own_pull_requests(self):
        self.engine.me = lambda: {"uuid": "{someone-else}"}
        with self.assertRaises(PermissionError):
            self.actions.sync(self.engine, self.tmp, self.body)


class ProposalTest(unittest.TestCase):
    """The waiting room for pull requests an agent asks to open."""

    def setUp(self):
        import actions
        import proposals
        self.actions, self.store = actions, proposals.Proposals()
        self.created, self.tip = [], "aaa111"
        self.saved = (actions.preview, actions.create, actions.remote_tip)
        actions.preview = lambda engine, root, repo, branch: {
            "dest": "develop", "dests": ["develop", "main"], "title": "feat: from git", "description": "- one",
            "tip": "aaa111", "ahead": 2, "behind": 0, "file_count": 3, "added": 10, "removed": 1}
        actions.create = lambda engine, root, body: self.created.append(body) or {"id": 7, "url": "https://bitbucket.org/acme/shop-api/pull-requests/7", "reviewers": 1}
        actions.remote_tip = lambda root, repo, branch: self.tip

    def tearDown(self):
        self.actions.preview, self.actions.create, self.actions.remote_tip = self.saved

    def ask(self, **over):
        return self.store.propose(None, "/root", {"repo": "shop-api", "branch": "feat/x", "title": "feat: asked", **over})

    def test_asking_twice_returns_the_same_proposal(self):
        a, b = self.ask(), self.ask(title="something else")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual((a["title"], a["dest"], a["status"]), ("feat: asked", "develop", "pending"))

    def test_approve_opens_it_once_with_the_fields_from_the_popup(self):
        pid = self.ask()["id"]
        out = self.store.decide(None, "/root", pid, "approve", {"dest": "main", "close_source_branch": False})
        again = self.store.decide(None, "/root", pid, "approve")
        self.assertEqual((out["status"], again["status"]), ("opened", "opened"))
        self.assertEqual(len(self.created), 1, "a second click opens nothing")
        self.assertEqual((self.created[0]["dest"], self.created[0]["close_source_branch"], self.created[0]["title"]), ("main", False, "feat: asked"))
        self.assertEqual(self.ask()["status"], "opened", "the agent's next call reads the answer")

    def test_refuses_when_the_branch_moved_since_it_was_shown(self):
        pid = self.ask()["id"]
        self.tip = "bbb222"
        with self.assertRaises(ValueError):
            self.store.decide(None, "/root", pid, "approve")
        self.assertEqual(self.store.get(pid)["status"], "pending")
        self.assertEqual(self.created, [])

    def test_decline_and_other_paths_end_it(self):
        pid = self.ask()["id"]
        self.assertEqual(self.store.decide(None, "/root", pid, "decline")["status"], "declined")
        other = self.store.propose(None, "/root", {"repo": "shop-api", "branch": "feat/y"})
        self.store.opened_elsewhere("shop-api", "feat/y", {"id": 9})
        self.assertEqual(self.store.get(other["id"])["status"], "opened")

    def test_limits(self):
        with self.assertRaises(ValueError):
            self.ask(title="x" * 300)
        with self.assertRaises(ValueError):
            self.ask(dest="production")


class ProposalRouteTest(unittest.TestCase):
    """The real server in-process: what the propose key may and may not do, and the real command."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        import actions
        import server
        cls.server_mod, cls.actions = server, actions
        cls.saved = (actions.preview, actions.create, actions.remote_tip)
        actions.preview = lambda engine, root, repo, branch: {
            "dest": "develop", "dests": ["develop"], "title": "t", "description": "", "tip": "aaa", "ahead": 1,
            "behind": 0, "file_count": 1, "added": 1, "removed": 0}
        actions.create = lambda engine, root, body: {"id": 11, "url": "https://bitbucket.org/acme/shop-api/pull-requests/11", "reviewers": 0}
        actions.remote_tip = lambda root, repo, branch: "aaa"
        cls.state = Path(tempfile.mkdtemp(prefix="pixeldeck-routes-"))
        os.environ["PIXELDECK_STATE_DIR"] = str(cls.state)
        server.Desk.engine = type("E", (), {"auth": {}, "me": lambda self: {"uuid": "{me}"}})()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Desk)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        server.Desk.port = cls.port
        server.write_agent_key(cls.port)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.actions.preview, cls.actions.create, cls.actions.remote_tip = cls.saved
        os.environ.pop("PIXELDECK_STATE_DIR", None)

    def call(self, method, route, body=None, key=None, page=False):
        import urllib.error
        import urllib.request
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-Agent-Key"] = key
        if page:
            headers["X-Desk-Token"] = self.server_mod.SECRET
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/{route}", method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_the_propose_key_can_ask_but_never_decide(self):
        key = json.loads((self.state / "agent.key").read_text())["key"]
        self.assertEqual((self.state / "agent.key").stat().st_mode & 0o777, 0o600)
        code, item = self.call("POST", "propose", {"repo": "shop-api", "branch": "feat/k"}, key=key)
        self.assertEqual(code, 200)
        self.assertEqual(self.call("GET", f"proposal?id={item['id']}", key=key)[0], 200)
        for method, route, body in (("POST", "proposal-decide", {"id": item["id"], "action": "approve"}),
                                    ("GET", "proposals", None), ("POST", "merge", {"repo": "shop-api", "id": 1}),
                                    ("POST", "pr-create", {"repo": "shop-api", "branch": "feat/k"})):
            self.assertEqual(self.call(method, route, body, key=key)[0], 401, route)
        self.assertEqual(self.call("POST", "proposal-decide", {"id": item["id"], "action": "decline"}, page=True)[1]["status"], "declined")

    def run_cli(self, branch, *extra):
        env = {**os.environ, "PIXELDECK_PORT": str(self.port), "PIXELDECK_STATE_DIR": str(self.state)}
        root = Path(__file__).resolve().parent.parent
        return subprocess.Popen([sys.executable, str(root / "pixeldeck.py"), "--propose", "--repo", "shop-api", "--branch", branch,
                                 "--title", "feat: from the agent", *extra], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_pending(self, branch):
        for _ in range(50):
            items = [p for p in self.call("GET", "proposals", page=True)[1]["proposals"] if p["branch"] == branch]
            if items:
                return items[0]["id"]
            __import__("time").sleep(0.1)
        self.fail("the proposal never arrived")

    def test_the_command_waits_for_proceed_and_prints_the_link(self):
        proc = self.run_cli("feat/cli")
        pid = self.wait_pending("feat/cli")
        self.call("POST", "proposal-decide", {"id": pid, "action": "approve"}, page=True)
        out, _ = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("https://bitbucket.org/acme/shop-api/pull-requests/11", out)

    def test_the_command_exits_3_while_waiting_and_2_when_declined(self):
        proc = self.run_cli("feat/slow", "--wait", "1")
        proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 3)
        pid = self.wait_pending("feat/slow")
        self.call("POST", "proposal-decide", {"id": pid, "action": "decline"}, page=True)
        again = self.run_cli("feat/slow", "--wait", "1")
        again.communicate(timeout=20)
        self.assertEqual(again.returncode, 2)


class SnapshotTest(unittest.TestCase):
    def session(self):
        import threading
        import terminals
        s = terminals.Session.__new__(terminals.Session)
        s.buf, s.base, s.cond, s.snapshot, s.exit_code = bytearray(b"0123456789"), 100, threading.Condition(), None, None
        s.cols, s.rows, s.title = 120, 30, "Shell"
        return s

    def test_keeps_a_picture_only_while_its_tail_is_in_the_buffer(self):
        s = self.session()  # offsets 100 to 110 are in the buffer
        self.assertFalse(s.remember("old", 99, 80, 24), "its tail scrolled out")
        self.assertFalse(s.remember("future", 111, 80, 24))
        self.assertTrue(s.remember("screen at 105", 105, 200, 50))
        self.assertFalse(s.remember("older", 103, 80, 24), "an older picture never replaces a newer one")
        self.assertEqual(s.snapshot["pos"], 105)

    def test_a_new_page_gets_the_picture_then_only_what_followed(self):
        import base64
        import urllib.request
        import server
        s = self.session()
        s.remember("PICTURE", 105, 200, 50)
        import threading
        from http.server import ThreadingHTTPServer
        server.TERMS.sessions["snap1"] = s
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Desk)
        httpd.daemon_threads = True
        server.Desk.port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{server.Desk.port}/api/term-stream?cursors=", headers={"X-Desk-Token": server.SECRET})
            with urllib.request.urlopen(req, timeout=10) as res:
                lines = [json.loads(res.readline()), json.loads(res.readline())]
        finally:
            del server.TERMS.sessions["snap1"]
            httpd.shutdown()
            httpd.server_close()
        snap = next(x for x in lines if "snap" in x)
        data = next(x for x in lines if "d" in x)
        self.assertEqual((snap["snap"], snap["sp"], snap["cols"], snap["rows"]), ("PICTURE", 105, 200, 50))
        self.assertEqual(base64.b64decode(data["d"]), b"56789", "only the output after the picture")


if __name__ == "__main__":
    unittest.main()
