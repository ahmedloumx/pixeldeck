"""Jira from the settings: the site, the email and an API token saved without editing a file,
the token never sent back to the page."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env: they write one of their own.
_DIR = Path(tempfile.mkdtemp(prefix="pixeldeck-test-"))
_ENV = _DIR / ".env"
_START = "BITBUCKET_WORKSPACE=acme\nBITBUCKET_EMAIL=me@example.com\n# a comment that stays\n"
os.environ.setdefault("PIXELDECK_ENV", str(_ENV))

import jira  # noqa: E402


class SaveTest(unittest.TestCase):
    def setUp(self):
        # Each test gets this file's settings, whatever another test module set at its import.
        _ENV.write_text(_START)
        env = {k: v for k, v in os.environ.items() if k not in ("BITBUCKET_TOKEN", "JIRA_TOKEN", "JIRA_SITE", "JIRA_EMAIL")}
        env.update(PIXELDECK_ENV=str(_ENV), PIXELDECK_CREDENTIALS="off")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.checked = []
        self.j = jira.Jira(opener=self._no_network)
        self.j.check = lambda: (self.checked.append(1), self.j.status())[1]

    @staticmethod
    def _no_network(*_a, **_k):
        raise AssertionError("a save test must not reach the network")

    def test_saves_site_email_and_token_and_never_returns_the_token(self):
        out = jira.save_settings(self.j, {"site": "https://acme.atlassian.net/", "email": "me@acme.com", "token": "ATATT-secret"})
        text = _ENV.read_text()
        self.assertIn("JIRA_SITE=https://acme.atlassian.net\n", text)
        self.assertIn("JIRA_EMAIL=me@acme.com\n", text)
        self.assertIn("JIRA_TOKEN=ATATT-secret\n", text)
        self.assertIn("# a comment that stays", text)
        self.assertEqual((out["site"], out["email"], out["token_set"]), ("https://acme.atlassian.net", "me@acme.com", True))
        self.assertNotIn("ATATT-secret", repr(out), "the token never goes back to the page")
        self.assertEqual(self.checked, [1], "a save is tried at once")

    def test_an_empty_token_keeps_the_saved_one_and_an_empty_email_falls_back(self):
        jira.save_settings(self.j, {"site": "https://acme.atlassian.net", "email": "x@acme.com", "token": "ATATT-one"})
        out = jira.save_settings(self.j, {"site": "https://other.atlassian.net", "email": "", "token": ""})
        text = _ENV.read_text()
        self.assertIn("JIRA_TOKEN=ATATT-one", text)
        self.assertNotIn("JIRA_EMAIL=", text, "no email of its own: the Bitbucket one is used")
        self.assertEqual(out["email"], "me@example.com")

    def test_refuses_what_would_send_the_token_somewhere_unsafe(self):
        for body in ({"site": "http://acme.atlassian.net", "token": "t"}, {"site": "acme.atlassian.net", "token": "t"},
                     {"site": "https://acme.atlassian.net/jira/path?x=1", "token": "t"}, {"site": "https://acme.atlassian.net", "email": "not-an-email", "token": "t"},
                     {"site": "https://acme.atlassian.net", "token": "two words"}, {"site": "", "token": "t"}):
            with self.assertRaises(ValueError, msg=body):
                jira.save_settings(self.j, body)

    def test_clearing_takes_everything_out(self):
        jira.save_settings(self.j, {"site": "https://acme.atlassian.net", "email": "x@acme.com", "token": "ATATT-one"})
        out = jira.save_settings(self.j, {"clear": True})
        text = _ENV.read_text()
        self.assertNotIn("JIRA_", text)
        self.assertEqual((out["site"], out["token_set"]), (None, False))


if __name__ == "__main__":
    unittest.main()
