"""The Claude panel: a chat runs Claude Code without a terminal, shows what it does, asks before
a tool and keeps its history; Claude Code's own models, commands, account, usage and sessions
are read the way the editor extensions read them. A stand-in (fake_claude.py) speaks the CLI's
JSON lines here."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env: they get settings of their own, and no token.
_HOME = Path(tempfile.mkdtemp(prefix="pixeldeck-test-"))
(_HOME / ".env").write_text("BITBUCKET_WORKSPACE=acme\n")
os.environ["PIXELDECK_ENV"] = str(_HOME / ".env")
os.environ["PIXELDECK_CREDENTIALS"] = "off"

import chats  # noqa: E402

FAKE = Path(__file__).resolve().parent / "fake_claude.py"
COMMAND = chats.command  # the real one; the tests below run the stand-in instead
STARTED = []  # the options each stand-in was started with


def fake_command(model="", mode="default", session=None, effort="", add_dirs=()):
    STARTED.append({"model": model, "mode": mode, "session": session, "effort": effort, "add_dirs": [str(d) for d in add_dirs]})
    return [sys.executable, str(FAKE)] + (["--resume", session] if session else [])


def until(chat, test, timeout=20):
    """Read the chat until test(events, chat) holds."""
    end = time.time() + timeout
    while time.time() < end:
        seen = chat.read(0)
        if test(seen["events"], seen["chat"]):
            return seen
        time.sleep(0.05)
    seen = chat.read(0)
    raise AssertionError(f"timed out: {[e['kind'] for e in seen['events']]}, status {seen['chat']['status']}, stderr {chat.stderr}")


def kinds(events):
    return [e["kind"] for e in events]


class ChatTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=_HOME))
        self.store = chats.Chats(self.folder / "state")
        patches = [mock.patch.object(chats, "command", fake_command),
                   mock.patch.object(chats, "drops_folder", lambda: self.folder / "drops")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.store.stop_all)

    def ask(self, chat, text, n):
        chat.send(text)
        return until(chat, lambda ev, c: c["status"] == "waiting" and f"ask-{n}" in c["pending"])

    def test_a_turn_streams_asks_before_the_tool_and_finishes(self):
        chat = self.store.start(self.folder, "Ask about the cart")
        seen = self.ask(chat, "hello", 1)
        order = kinds(seen["events"])
        self.assertIn("user", order)
        self.assertIn("system", order)
        self.assertEqual([k for k in order if k in ("assistant", "tool", "permission")], ["assistant", "tool", "permission"])
        ask = seen["events"][-1]
        self.assertEqual((ask["request"], ask["title"], ask["description"], ask["always"]), ("ask-1", "Bash", "Print hi", True))
        self.assertIn("echo hi", ask["input"])
        self.assertEqual(seen["chat"]["session"], "sess-1")

        chat.answer("ask-1", True, always=True)
        done = until(chat, lambda ev, c: c["status"] == "idle")
        tail = done["events"][len(seen["events"]):]
        self.assertEqual(kinds(tail), ["answer", "tool_result", "assistant", "result"])
        self.assertEqual((tail[0]["allow"], tail[0]["always"]), (True, True))
        self.assertEqual((tail[1]["id"], tail[1]["error"], tail[1]["text"]), ("tool-1", False, "hi"))
        self.assertEqual(tail[2]["text"], "Ran it (always, tool tool-1).",
                         "the yes went back with the tool's id and the rule Claude Code suggested")
        self.assertEqual((tail[3]["ok"], tail[3]["turns"], tail[3]["cost"]), (True, 2, 0.01))
        self.assertEqual(done["live"], "", "the text being written ends with the message")
        self.assertEqual(STARTED[-1]["add_dirs"], [str(self.folder / "drops")], "attached files are readable to Claude")

    def test_no_is_told_to_claude(self):
        chat = self.store.start(self.folder, "No")
        self.ask(chat, "hello", 1)
        chat.answer("ask-1", False)
        done = until(chat, lambda ev, c: c["status"] == "idle")
        result = [e for e in done["events"] if e["kind"] == "tool_result"][-1]
        self.assertEqual((result["error"], result["text"]), (True, "The user said no in PixelDeck."))
        self.assertEqual(done["events"][-2]["text"], "You said no.")
        with self.assertRaises(ValueError):
            chat.answer("ask-1", True)  # no longer waiting

    def test_a_question_pixeldeck_cannot_answer_gets_an_error_not_silence(self):
        chat = self.store.start(self.folder, "Hook")
        self.ask(chat, "hook please", 1)
        texts = [e["text"] for e in chat.read(0)["events"] if e["kind"] == "assistant"]
        self.assertIn("host said error", texts)

    def test_history_is_kept_and_a_stopped_chat_resumes_its_session(self):
        chat = self.store.start(self.folder, "Keep")
        self.ask(chat, "hello", 1)
        chat.answer("ask-1", True)
        until(chat, lambda ev, c: c["status"] == "idle")
        chat.stop()
        until(chat, lambda ev, c: c["status"] == "stopped")
        count = chat.read(0)["chat"]["events"]

        again = chats.Chats(self.folder / "state")
        listed = again.list()
        self.assertEqual([(c["title"], c["status"], c["session"]) for c in listed], [("Keep", "stopped", "sess-1")])
        back = again.get(listed[0]["id"])
        self.assertEqual(len(back.read(0)["events"]), count)
        seen = self.ask(back, "again", 1)  # a new process, on the same session
        self.assertEqual(seen["events"][-1]["kind"], "permission")
        self.assertEqual(STARTED[-1]["session"], "sess-1")
        back.answer("ask-1", True)
        until(back, lambda ev, c: c["status"] == "idle")
        back.stop()

    def test_model_effort_and_mode_change_on_a_live_session(self):
        for bad in ({"mode": "bypassPermissions"}, {"model": "bad model; x"}, {"effort": "huge"}):
            with self.assertRaises(ValueError, msg=bad):
                self.store.start(self.folder, "x", **bad)
        chat = self.store.start(self.folder, "Modes", mode="plan", model="haiku", effort="low")
        self.ask(chat, "hello", 1)
        self.assertEqual({k: STARTED[-1][k] for k in ("model", "mode", "effort")}, {"model": "haiku", "mode": "plan", "effort": "low"})
        chat.set_mode("auto")
        chat.set_model("default", "max")
        context = chat.context()
        self.assertEqual((context["percentage"], context["maxTokens"], context["model"]), (3, 1000000, "default"),
                         "Claude Code's own count, after its own model change")
        chat.interrupt()
        self.assertEqual((chat.public()["mode"], chat.public()["model"], chat.public()["effort"]), ("auto", "default", "max"))
        with self.assertRaises(ValueError):
            chat.set_mode("dontAsk")
        chat.answer("ask-1", True)
        until(chat, lambda ev, c: c["status"] == "idle")
        chat.stop()
        until(chat, lambda ev, c: c["status"] == "stopped")
        self.assertEqual(chat.context()["percentage"], 3, "a stopped chat shows the last count")

    def test_the_command_line(self):
        with mock.patch.object(chats.launcher, "claude_binary", lambda: "claude"):
            argv = COMMAND("haiku", "plan", "sess-9")
            plain = COMMAND("", "default")
            full = COMMAND("claude-fable-5-1[1m]", "auto", None, "max", ["/drops"])
            default = COMMAND("default", "default")
        self.assertEqual(argv[:9], ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                                    "--include-partial-messages", "--permission-prompt-tool"])
        self.assertEqual(argv[9:], ["stdio", "--permission-mode", "plan", "--model", "haiku", "--resume", "sess-9"])
        self.assertNotIn("--permission-mode", plain, "ask first is Claude Code's own default")
        self.assertEqual(full[9:], ["stdio", "--permission-mode", "auto", "--model", "claude-fable-5-1[1m]", "--effort", "max", "--add-dir", "/drops"])
        self.assertNotIn("--model", default, "the default model is Claude Code's own")

    def test_a_nested_claude_starts_clean(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "KEEP_ME": "yes"}):
            env = chats._env()
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", env)
        self.assertEqual(env["KEEP_ME"], "yes")


class InfoTest(unittest.TestCase):
    """Models, commands, account and usage come from Claude Code, as the extensions read them."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=_HOME))
        patches = [mock.patch.object(chats, "command", fake_command), mock.patch.object(chats, "CHATS", chats.Chats(self.folder / "state"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_a_short_lived_claude_answers_and_the_answer_is_kept(self):
        info = chats.Info()
        first = info.get(self.folder)
        self.assertIsNone(first["error"])
        self.assertEqual([m["value"] for m in first["models"]], ["default", "haiku"], "a value that is no model name is dropped")
        self.assertEqual(first["models"][0]["supportedEffortLevels"][-1], "max")
        self.assertEqual([c["name"] for c in first["commands"]], ["compact", "review"])
        self.assertEqual(first["account"], {"email": "someone@example.com", "plan": "Claude Pro", "organization": None})
        self.assertEqual(first["usage"]["five_hour"], {"utilization": 38, "resets_at": "2026-11-19T21:00:00+00:00"})
        self.assertEqual(first["usage"]["seven_day"]["utilization"], 79)
        starts = len(STARTED)
        info.get(self.folder)
        self.assertEqual(len(STARTED), starts, "within a minute, nothing is asked again")
        info.get(self.folder, force=True)
        self.assertEqual(len(STARTED), starts + 1)

    def test_no_claude_code_says_so(self):
        info = chats.Info()
        with mock.patch.object(chats, "command", lambda *a, **k: [str(self.folder / "no-such-claude.exe")]):
            answer = info.get(self.folder)
        self.assertTrue(answer["error"])
        self.assertEqual(answer["models"], [])


def write_session(folder, session, lines):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session}.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


SID = "12345678-1234-1234-1234-123456789abc"


class SessionsTest(unittest.TestCase):
    """The sessions Claude Code keeps on this computer, listed and opened with their history."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(dir=_HOME))
        self.cwd = Path(tempfile.mkdtemp(dir=_HOME))
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.where = self.home / "projects" / "".join(c if c.isalnum() else "-" for c in str(self.cwd))
        self.path = write_session(self.where, SID, [
            {"type": "user", "message": {"role": "user", "content": "Fix the cart totals"}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "<system-reminder>x</system-reminder>"}},
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "Reading cart.py."},
                                                          {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "cart.py"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "def total(): ..."}]}},
            {"type": "user", "isSidechain": True, "message": {"content": "an agent's own turn"}},
            {"type": "ai-title", "aiTitle": "Cart totals fix", "sessionId": SID},
            {"type": "last-prompt", "lastPrompt": "Fix the cart totals", "sessionId": SID},
        ])
        write_session(self.where, "not-a-session", [{"type": "user", "message": {"content": "x"}}])
        older = write_session(self.where, "87654321-4321-4321-4321-cba987654321", [{"type": "last-prompt", "lastPrompt": "Old   question"}])
        os.utime(older, (time.time() - 3600, time.time() - 3600))

    def test_a_folder_s_sessions_newest_first_with_their_title(self):
        listed = chats.sessions(self.cwd)
        self.assertEqual([(s["id"], s["title"]) for s in listed],
                         [(SID, "Cart totals fix"), ("87654321-4321-4321-4321-cba987654321", "Old question")])
        self.assertEqual(chats.sessions(self.cwd / "elsewhere"), [])

    def test_the_end_of_a_session_reads_as_the_panel_s_events(self):
        events = chats.history(self.path)
        self.assertEqual([(e["kind"], e.get("text") or e.get("name")) for e in events],
                         [("system", "Earlier in this session (its end, as Claude Code kept it)"), ("user", "Fix the cart totals"),
                          ("assistant", "Reading cart.py."), ("tool", "Read"), ("tool_result", "def total(): ...")])

    def test_opening_a_session_through_the_routes_resumes_it(self):
        state = Path(tempfile.mkdtemp(dir=_HOME))
        patches = [mock.patch.object(chats, "command", fake_command), mock.patch.object(chats, "CHATS", chats.Chats(state)),
                   mock.patch.object(chats, "folders", lambda root: [{"id": "", "label": "All your clones", "path": str(self.cwd)}]),
                   mock.patch.object(chats, "drops_folder", lambda: state / "drops")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: chats.CHATS.stop_all())
        call = lambda method, name, body=None, **q: chats.route(None, None, method, name, {k: [str(v)] for k, v in q.items()}, body or {})  # noqa: E731
        listed = call("GET", "claude-sessions", folder="")
        self.assertEqual([(s["title"], s["chat"]) for s in listed["sessions"]][0], ("Cart totals fix", None))
        with self.assertRaises(ValueError):
            call("POST", "chat-open", {"folder": "", "session": "../../etc"})
        opened = call("POST", "chat-open", {"folder": "", "session": SID})
        self.assertEqual((opened["title"], opened["session"], opened["events"]), ("Cart totals fix", SID, 5))
        self.assertEqual(call("POST", "chat-open", {"folder": "", "session": SID})["id"], opened["id"], "one chat per session")
        self.assertEqual(call("GET", "claude-sessions", folder="")["sessions"][0]["chat"], opened["id"])
        chat = chats.CHATS.get(opened["id"])
        chat.send("go on")
        until(chat, lambda ev, c: c["status"] == "waiting")
        self.assertEqual(STARTED[-1]["session"], SID, "it goes on with --resume")
        chat.answer("ask-1", True)
        until(chat, lambda ev, c: c["status"] == "idle")


class RouteTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=_HOME))
        patches = [mock.patch.object(chats, "command", fake_command),
                   mock.patch.object(chats, "CHATS", chats.Chats(self.folder / "state")),
                   mock.patch.object(chats, "drops_folder", lambda: self.folder / "drops"),
                   mock.patch.object(chats, "folders", lambda root: [{"id": "", "label": "All your clones", "path": str(self.folder)}])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: chats.CHATS.stop_all())

    def call(self, method, name, body=None, **query):
        return chats.route(None, None, method, name, {k: [str(v)] for k, v in query.items()}, body or {})

    def test_start_answer_and_read_through_the_routes(self):
        self.assertIsNone(self.call("GET", "settings"))
        with mock.patch.object(chats.launcher, "claude_binary", lambda: "claude"):
            listing = self.call("GET", "chats")
        self.assertEqual((listing["chats"], listing["folders"][0]["id"], listing["problem"]), ([], "", None))
        self.assertEqual(listing["efforts"], ["low", "medium", "high", "xhigh", "max"])
        self.assertNotIn("path", listing["folders"][0], "the page names a folder, it never sees or gives a path")
        with self.assertRaises(ValueError):
            self.call("POST", "chat-start", {"folder": "../elsewhere", "text": "hi"})
        started = self.call("POST", "chat-start", {"folder": "", "text": "hello", "mode": "default", "model": "haiku", "effort": "high"})
        chat = chats.CHATS.get(started["id"])
        until(chat, lambda ev, c: c["status"] == "waiting")
        self.assertEqual(self.call("GET", "chat-context", id=started["id"])["context"]["percentage"], 3)
        self.assertEqual(self.call("POST", "chat-model", {"id": started["id"], "model": "default", "effort": "max"})["effort"], "max")
        self.call("POST", "chat-answer", {"id": started["id"], "request": "ask-1", "allow": True})
        until(chat, lambda ev, c: c["status"] == "idle")
        page = self.call("GET", "chat", id=started["id"], after=0, live=0, wait=1)
        self.assertEqual(kinds(page["events"])[-1], "result")
        self.assertEqual(self.call("POST", "chat-forget", {"id": started["id"]})["chats"], [])
        with self.assertRaises(KeyError):
            self.call("GET", "chat", id=started["id"])


if __name__ == "__main__":
    unittest.main()
