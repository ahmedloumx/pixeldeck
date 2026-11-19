"""PixelDeck's own Spotify player: when spotifyd starts, with what, and what the page is told."""

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env, never find your spotifyd, and keep their state apart.
_DIR = Path(tempfile.mkdtemp(prefix="pixeldeck-test-"))
(_DIR / ".env").write_text("BITBUCKET_WORKSPACE=acme\n")


def isolate(test):
    """This test's own settings, state folder and home, and no spotifyd of yours on the PATH."""
    env = {k: v for k, v in os.environ.items() if k not in ("SPOTIFYD", "SPOTIFYD_PATH", "SPOTIFYD_DEVICE_NAME")}
    env.update(PIXELDECK_ENV=str(_DIR / ".env"), PIXELDECK_STATE_DIR=str(_DIR / "state"), PATH="/usr/bin:/bin", HOME=str(_DIR / "home"))
    patcher = mock.patch.dict(os.environ, env, clear=True)
    patcher.start()
    test.addCleanup(patcher.stop)

import spotifyd  # noqa: E402

# A stand-in spotifyd: prints what it was asked to do and stays up until stopped.
FAKE = """#!/bin/sh
echo "$@" > "$(dirname "$0")/args"
if [ "$1" = "authenticate" ]; then
  echo "Browse to: https://accounts.spotify.com/authorize?client_id=x&redirect_uri=http%3A%2F%2F127.0.0.1%3A8000%2Flogin"
  mkdir -p "$3/oauth" && echo '{}' > "$3/oauth/credentials.json"
  exit 0
fi
exec sleep 30
"""


def fake_binary():
    path = _DIR / "bin" / "spotifyd"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FAKE)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class StartTest(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.player = spotifyd.Player()
        self.addCleanup(self.player.stop)
        os.environ.pop("SPOTIFYD_PATH", None)
        creds = spotifyd.folder() / "oauth" / "credentials.json"
        if creds.exists():
            creds.unlink()

    def test_not_installed_says_so_and_starts_nothing(self):
        out = self.player.start()
        self.assertEqual((out["installed"], out["state"]), (False, "missing"))
        self.assertIsNone(self.player.proc)

    def test_installed_but_signed_out_waits_for_the_sign_in(self):
        os.environ["SPOTIFYD_PATH"] = str(fake_binary())
        out = self.player.start()
        self.assertEqual((out["installed"], out["signed_in"], out["state"]), (True, False, "signed-out"))
        self.assertIn("authenticate --cache-path", out["signin_command"])

    def test_signed_in_it_runs_quietly_on_the_network_and_stops_with_the_server(self):
        exe = fake_binary()
        os.environ["SPOTIFYD_PATH"] = str(exe)
        (spotifyd.folder() / "oauth").mkdir(parents=True, exist_ok=True)
        (spotifyd.folder() / "oauth" / "credentials.json").write_text("{}")
        out = self.player.start()
        self.assertEqual(out["state"], "running")
        for _ in range(50):
            if (exe.parent / "args").exists():
                break
            time.sleep(0.05)
        args = (exe.parent / "args").read_text().split()
        self.assertIn("--no-daemon", args)
        self.assertIn("--disable-discovery=true", args)
        self.assertEqual(args[args.index("--device-name") + 1], "PixelDeck")
        self.assertEqual(args[args.index("--cache-path") + 1], str(spotifyd.folder()))
        self.assertEqual(oct(spotifyd.folder().stat().st_mode & 0o777), "0o700", "the login is readable by you only")
        pid = self.player.proc.pid
        self.player.stop()
        time.sleep(0.2)
        with self.assertRaises(OSError):
            os.kill(pid, 0)
        self.assertEqual(self.player.status()["state"], "off")

    def test_turned_off_starts_nothing(self):
        os.environ["SPOTIFYD_PATH"] = str(fake_binary())
        os.environ["SPOTIFYD"] = "off"
        self.assertEqual(self.player.start()["state"], "off")
        self.assertIsNone(self.player.proc)


class SignInTest(unittest.TestCase):
    def setUp(self):
        isolate(self)

    def test_finds_only_a_spotify_login_link(self):
        self.assertEqual(spotifyd.find_login_url("Browse to: https://accounts.spotify.com/authorize?x=1 now"), "https://accounts.spotify.com/authorize?x=1")
        self.assertIsNone(spotifyd.find_login_url("go to https://evil.example/accounts.spotify.com/ please"))
        self.assertIsNone(spotifyd.find_login_url("nothing here"))

    def test_signing_in_opens_the_link_then_starts_the_player(self):
        os.environ["SPOTIFYD_PATH"] = str(fake_binary())
        opened = []
        real = spotifyd.opener.open_link
        spotifyd.opener.open_link = lambda url: opened.append(url) or "browser"
        self.addCleanup(setattr, spotifyd.opener, "open_link", real)
        player = spotifyd.Player()
        self.addCleanup(player.stop)
        player.sign_in()
        for _ in range(60):
            if player.signin["state"] == "done":
                break
            time.sleep(0.05)
        self.assertEqual(player.signin["state"], "done")
        self.assertTrue(opened and opened[0].startswith("https://accounts.spotify.com/"))
        self.assertTrue(spotifyd.signed_in())
        self.assertEqual(player.status()["state"], "running")


if __name__ == "__main__":
    unittest.main()
