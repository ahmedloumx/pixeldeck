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


if __name__ == "__main__":
    unittest.main()
