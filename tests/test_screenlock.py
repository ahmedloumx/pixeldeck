"""Run with: python3 -m unittest discover -s tests  (from the PixelDeck folder).

The lock screen's server half: the system's locks read from dbus-monitor's output (fed here
line by line, no bus), and the passcode kept as a salted hash in a temp folder."""

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import screenlock  # noqa: E402

GNOME_ON = [
    "signal time=1.0 sender=org.freedesktop.DBus -> destination=:1.9 serial=2 path=/org/freedesktop/DBus; interface=org.freedesktop.DBus; member=NameAcquired",
    '   string ":1.9"',
    "signal time=2.0 sender=:1.30 -> destination=(null destination) serial=88 path=/org/gnome/ScreenSaver; interface=org.gnome.ScreenSaver; member=ActiveChanged",
    "   boolean true",
]
GNOME_OFF = [
    "signal time=9.0 sender=:1.30 -> destination=(null destination) serial=91 path=/org/gnome/ScreenSaver; interface=org.gnome.ScreenSaver; member=ActiveChanged",
    "   boolean false",
]
KDE_ON = [
    "signal time=3.0 sender=:1.40 -> destination=(null destination) serial=7 path=/ScreenSaver; interface=org.freedesktop.ScreenSaver; member=ActiveChanged",
    "   boolean true",
]


class WatcherTest(unittest.TestCase):
    def watcher(self):
        return screenlock.Watcher(which=lambda _n: "/usr/bin/dbus-monitor", clock=lambda: 100.0)

    def test_a_lock_counts_once_and_the_unlock_clears_it(self):
        w = self.watcher()
        for line in GNOME_ON + GNOME_ON[2:] + GNOME_OFF:  # the desktop may say "locked" twice
            w.feed(line)
        st = w.status()
        self.assertEqual((st["locks"], st["locked"], st["locked_at"]), (1, False, 100.0))
        for line in KDE_ON:
            w.feed(line)
        self.assertEqual((w.status()["locks"], w.status()["locked"]), (2, True))

    def test_other_signals_and_booleans_are_ignored(self):
        w = self.watcher()
        for line in ["signal time=1 sender=:1.2 -> destination=x serial=1 path=/x; interface=org.example.Thing; member=ActiveChanged",
                     "   boolean true", "   boolean true", "garbage", ""]:
            w.feed(line)
        self.assertEqual(w.status()["locks"], 0)

    def test_run_reads_dbus_monitor_and_asks_for_the_signal_only(self):
        seen = {}

        def popen(argv, **kw):
            seen["argv"] = argv
            w.stopped = True  # one round, then stop
            return types.SimpleNamespace(stdout=io.StringIO("\n".join(GNOME_ON) + "\n"), poll=lambda: 0)

        w = screenlock.Watcher(which=lambda _n: "/usr/bin/dbus-monitor", popen=popen)
        with mock.patch.object(screenlock.sys, "platform", "linux"), mock.patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}):
            w.run()
        self.assertEqual(w.status()["locks"], 1)
        self.assertEqual(seen["argv"][:2], ["dbus-monitor", "--session"])
        self.assertTrue(all("member='ActiveChanged'" in m for m in seen["argv"][2:]))

    def test_unavailable_without_a_session_bus_or_the_program(self):
        with mock.patch.object(screenlock.sys, "platform", "linux"), mock.patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": ""}):
            self.assertFalse(screenlock.Watcher(which=lambda _n: "/usr/bin/dbus-monitor").available())
        with mock.patch.object(screenlock.sys, "platform", "linux"), mock.patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}):
            self.assertFalse(screenlock.Watcher(which=lambda _n: None).available())
        with mock.patch.object(screenlock.sys, "platform", "win32"), mock.patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}):
            self.assertFalse(screenlock.Watcher(which=lambda _n: "x").available())


class PasscodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pixeldeck-lock-"))
        self.now = [1000.0]
        self.code = screenlock.Passcode(path=self.tmp / "lock.json", clock=lambda: self.now[0])
        self.saved = screenlock.PASSCODE
        screenlock.PASSCODE = self.code

    def tearDown(self):
        screenlock.PASSCODE = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def route(self, method, name, body=None):
        return screenlock.route(None, None, method, name, {}, body)

    def test_no_code_lets_anyone_in(self):
        self.assertEqual(self.route("POST", "lock-check", {"code": ""}), {"ok": True, "wait": 0})
        self.assertFalse(self.route("GET", "lock-state")["has_code"])

    def test_set_check_and_only_a_hash_is_kept(self):
        self.assertEqual(self.route("POST", "lock-code", {"code": "4821"}), {"has_code": True})
        raw = (self.tmp / "lock.json").read_text()
        self.assertNotIn("4821", raw)
        self.assertEqual(set(json.loads(raw)), {"salt", "hash", "iterations"})
        self.assertEqual(stat.S_IMODE((self.tmp / "lock.json").stat().st_mode), 0o600)
        self.assertTrue(self.route("POST", "lock-check", {"code": "4821"})["ok"])
        self.assertFalse(self.route("POST", "lock-check", {"code": "1234"})["ok"])
        for answer in (self.route("GET", "lock-state"), self.route("POST", "lock-check", {"code": "4821"})):
            self.assertNotIn(json.loads(raw)["hash"], json.dumps(answer))

    def test_codes_are_4_to_8_digits(self):
        for bad in ("123", "123456789", "12a4", " 1234", None, 1234, ["1234"]):
            with self.subTest(code=bad), self.assertRaises(ValueError):
                self.route("POST", "lock-code", {"code": bad})
        self.assertFalse((self.tmp / "lock.json").exists())

    def test_five_wrong_codes_wait_30_seconds(self):
        self.route("POST", "lock-code", {"code": "4821"})
        answers = [self.route("POST", "lock-check", {"code": "0000"}) for _ in range(5)]
        self.assertEqual([a["wait"] for a in answers], [0, 0, 0, 0, 30])
        self.assertEqual(self.route("POST", "lock-check", {"code": "4821"}), {"ok": False, "wait": 30}, "even the right code waits")
        self.now[0] += 31
        self.assertTrue(self.route("POST", "lock-check", {"code": "4821"})["ok"])

    def test_changing_or_clearing_asks_for_the_current_code(self):
        self.route("POST", "lock-code", {"code": "4821"})
        with self.assertRaises(ValueError):
            self.route("POST", "lock-code", {"code": "5555"})
        with self.assertRaises(ValueError):
            self.route("POST", "lock-code", {"clear": True, "current": "0000"})
        self.assertEqual(self.route("POST", "lock-code", {"code": "5555", "current": "4821"}), {"has_code": True})
        self.assertTrue(self.route("POST", "lock-check", {"code": "5555"})["ok"])
        self.assertEqual(self.route("POST", "lock-code", {"clear": True, "current": "5555"}), {"has_code": False})
        self.assertFalse((self.tmp / "lock.json").exists())

    def test_forget_removes_it(self):
        self.route("POST", "lock-code", {"code": "4821"})
        self.assertTrue(self.code.forget())
        self.assertFalse(self.code.forget())
        self.assertTrue(self.route("POST", "lock-check", {"code": ""})["ok"])

    def test_a_broken_file_counts_as_no_code(self):
        (self.tmp / "lock.json").write_text("{not json")
        self.assertFalse(self.code.is_set())
        (self.tmp / "lock.json").write_text(json.dumps({"salt": 1, "hash": "x", "iterations": "y"}))
        self.assertFalse(self.code.is_set())


if __name__ == "__main__":
    unittest.main()
