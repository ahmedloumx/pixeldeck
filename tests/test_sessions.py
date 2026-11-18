"""Where you left off: kept terminals, the restore offer, and the page's kept settings."""

import json
import os
import sys
import tempfile
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

import sessions  # noqa: E402

SID = "761960e4-1c37-4442-a0e2-1e924b7f3940"


class FakeSession:
    def __init__(self, sid, title, cwd, screen="", alive=True, started=1.0):
        self.id, self.title, self.cwd, self.started, self.alive = sid, title, cwd, started, alive
        self.proc = None
        self.snapshot = {"text": screen, "pos": 10, "cols": 100, "rows": 30} if screen else None


class FakeTerms:
    def __init__(self, *sessions_):
        self.sessions = {s.id: s for s in sessions_}


class KeeperTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="pixeldeck-keep-"))
        self.claude = self.dir / "claude-sessions"
        self.claude.mkdir()

    def keeper(self, *s):
        return sessions.Keeper(FakeTerms(*s), folder=self.dir, sessions_dir=self.claude)

    def test_saves_open_terminals_and_offers_them_at_the_next_start(self):
        k = self.keeper(FakeSession("a1", "Shell", "/tmp", "$ ls\r\n"), FakeSession("b2", "Ended", "/tmp", alive=False))
        self.assertIsNone(k.offer, "a first start has nothing to offer")
        k.save()
        saved = json.loads((self.dir / "terminals.json").read_text())
        self.assertEqual([t["id"] for t in saved["terminals"]], ["a1"], "an ended terminal is not kept")
        self.assertEqual(oct((self.dir / "terminals.json").stat().st_mode & 0o777), "0o600")
        again = self.keeper()  # the next start
        offer = again.public_offer()
        self.assertEqual([t["id"] for t in offer["terminals"]], ["a1"])
        self.assertNotIn("screen", offer["terminals"][0], "the offer the page sees carries no screens")
        self.assertTrue((self.dir / "terminals.offer.json").exists())
        self.assertIsNotNone(self.keeper().offer, "an undecided offer survives another restart")

    def test_restore_spawns_the_chosen_ones_and_carries_on_past_a_failure(self):
        self.keeper(FakeSession("a1", "Shell", "/tmp"), FakeSession("b2", "Claude Code", "/tmp"), FakeSession("c3", "Other", "/tmp")).save()
        k = self.keeper()
        spawned = []

        def spawn(entry):
            if entry["id"] == "b2":
                raise RuntimeError("claude CLI not found")
            spawned.append(entry["id"])
            return FakeSession(f"new-{entry['id']}", entry["title"], entry["cwd"])
        moved = k.take(["a1", "b2"], spawn)
        self.assertEqual(spawned, ["a1"], "only the chosen ones")
        self.assertEqual(moved[0], {"old": "a1", "new": "new-a1"})
        self.assertEqual((moved[1]["new"], "claude" in moved[1]["error"]), (None, True))
        self.assertIsNone(k.offer)
        self.assertFalse((self.dir / "terminals.offer.json").exists())
        with self.assertRaises(ValueError):
            k.take(["a1"], spawn)

    def test_finds_claude_codes_session_by_pid_and_checks_its_shape(self):
        (self.claude / "41.json").write_text(json.dumps({"sessionId": SID, "cwd": "/work"}))
        (self.claude / "42.json").write_text(json.dumps({"sessionId": "../../etc/passwd"}))
        self.assertEqual(sessions._claude([7, 41], self.claude), {"session": SID, "cwd": "/work", "pid": 41})
        self.assertIsNone(sessions._claude([42], self.claude), "an id that is not a uuid is ignored")
        self.assertIsNone(sessions._claude([43], self.claude))

    def test_the_command_that_brings_a_terminal_back(self):
        shell = lambda: ["/bin/bash", "-l"]  # noqa: E731
        claude = lambda: "/usr/bin/claude"  # noqa: E731
        self.assertEqual(sessions.restore_argv({"kind": "claude", "claude_session": SID}, shell, claude), ["/usr/bin/claude", "--resume", SID])
        self.assertEqual(sessions.restore_argv({"kind": "claude", "claude_session": "--dangerous"}, shell, claude), ["/usr/bin/claude", "--continue"])
        self.assertEqual(sessions.restore_argv({"kind": "shell"}, shell, claude), ["/bin/bash", "-l"])

    def test_claude_comes_back_with_its_permission_flags_and_nothing_else(self):
        shell = lambda: ["/bin/bash", "-l"]  # noqa: E731
        claude = lambda: "/usr/bin/claude"  # noqa: E731
        skipped = {"kind": "claude", "claude_session": SID, "claude_flags": ["--dangerously-skip-permissions"]}
        self.assertEqual(sessions.restore_argv(skipped, shell, claude), ["/usr/bin/claude", "--dangerously-skip-permissions", "--resume", SID])
        # the saved file is only data: anything off the allowlist is dropped again at restore
        odd = {"kind": "claude", "claude_flags": ["--permission-mode", "plan", "--mcp-config", "/tmp/x.json", "--permission-mode", "nope"]}
        self.assertEqual(sessions.restore_argv(odd, shell, claude), ["/usr/bin/claude", "--permission-mode", "plan", "--continue"])
        # the popup's choice wins, both ways
        plain = {"kind": "claude", "claude_session": SID, "claude_flags": []}
        self.assertEqual(sessions.restore_argv(plain, shell, claude, True), ["/usr/bin/claude", "--dangerously-skip-permissions", "--resume", SID])
        self.assertEqual(sessions.restore_argv(skipped, shell, claude, False), ["/usr/bin/claude", "--resume", SID])
        bypass = {"kind": "claude", "claude_session": SID, "claude_flags": ["--permission-mode", "bypassPermissions"]}
        self.assertEqual(sessions.restore_argv(bypass, shell, claude, False), ["/usr/bin/claude", "--resume", SID])
        self.assertEqual(sessions.restore_argv({"kind": "shell"}, shell, claude, True), ["/bin/bash", "-l"], "a shell takes no flag")

    @unittest.skipUnless(Path("/proc/self/cmdline").exists(), "reads /proc")
    def test_reads_the_flags_from_the_running_claude_only_by_exact_token(self):
        import subprocess
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)", "--dangerously-skip-permissions",
                                 "--permission-mode=auto", "--model", "x", "a whole prompt --dangerously-skip-permissions-not"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        (self.claude / f"{proc.pid}.json").write_text(json.dumps({"sessionId": SID, "cwd": "/work"}))
        s = FakeSession("a1", "Claude Code", "/tmp")
        s.proc = proc
        saved = sessions.describe(s, self.claude)
        self.assertEqual(saved["claude_flags"], ["--dangerously-skip-permissions", "--permission-mode", "auto"])
        self.keeper(s).save()
        offer = self.keeper().public_offer()
        self.assertEqual((offer["terminals"][0]["kind"], offer["terminals"][0]["skips"]), ("claude", True))

    def test_kept_settings_take_only_known_keys_and_stay_safe_in_html(self):
        k = self.keeper()
        k.set_ui({"values": {"prdesk.layout.v2": '{"x":"</script><script>alert(1)</script>"}'}, "stamp": 5})
        k.set_ui({"values": {"prdesk.prefs.v1": '{"theme":"grey"}'}, "stamp": 3})
        ui = k.ui()
        self.assertEqual((ui["stamp"], sorted(ui["values"])), (5, ["prdesk.layout.v2", "prdesk.prefs.v1"]))
        for bad in ({"values": {"other.key": "x"}, "stamp": 1}, {"values": {"prdesk.layout.v2": "x" * (sessions.UI_VALUE_MAX + 1)}, "stamp": 1}, {"values": "x", "stamp": 1}):
            with self.assertRaises(ValueError):
                k.set_ui(bad)
        island = sessions.page_state(ui)
        self.assertNotIn("<", island)
        self.assertNotIn("</script", island.lower())
        self.assertEqual(json.loads(island)["values"]["prdesk.layout.v2"], ui["values"]["prdesk.layout.v2"])


if __name__ == "__main__":
    unittest.main()
