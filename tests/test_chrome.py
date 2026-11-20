"""The window's own title bar: what the page may ask the window to do, and whether you chose
the system title bar instead."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chrome  # noqa: E402


class ParseTest(unittest.TestCase):
    def test_the_page_may_ask_for_a_few_things_only(self):
        self.assertEqual(chrome.parse('{"action": "maximize"}'), {"action": "maximize"})
        self.assertEqual(chrome.parse({"action": "close"}), {"action": "close"})
        self.assertEqual(chrome.parse('{"action": "move", "x": 12.5, "y": 8, "button": 1}'), {"action": "move", "x": 12.5, "y": 8.0, "button": 1})
        self.assertEqual(chrome.parse('{"action": "titlebar", "system": true}'), {"action": "titlebar", "system": True})
        self.assertEqual(chrome.parse('{"action": "hello"}'), {"action": "hello"})

    def test_a_youtube_window_takes_a_video_id_only(self):
        self.assertEqual(chrome.parse({"action": "youtube", "id": "dQw4w9WgXcQ", "title": " Neon\u0007 Harbor "}),
                         {"action": "youtube", "id": "dQw4w9WgXcQ", "title": "Neon Harbor", "mode": "window"})
        self.assertEqual(chrome.parse({"action": "youtube", "id": "a-b_c-d_e-f", "mode": "dock"}), {"action": "youtube", "id": "a-b_c-d_e-f", "title": "", "mode": "dock"})
        self.assertEqual(chrome.parse({"action": "youtube", "id": "a-b_c-d_e-f", "mode": "elsewhere"})["mode"], "window")
        self.assertEqual(len(chrome.parse({"action": "youtube", "id": "dQw4w9WgXcQ", "title": "x" * 900})["title"]), 200)
        for vid in ("dQw4w9WgXc", "dQw4w9WgXcQQ", "https://evil.example/x", "dQw4w9WgX/Q", "../../etc/pa", None, 12345678901, ["dQw4w9WgXcQ"]):
            with self.subTest(vid=vid):
                self.assertIsNone(chrome.parse({"action": "youtube", "id": vid}))

    def test_the_tv_screen_place_and_the_youtube_commands(self):
        self.assertEqual(chrome.parse({"action": "ytplace", "x": 10.4, "y": 20.6, "w": 440, "h": 248, "show": True}),
                         {"action": "ytplace", "x": 10, "y": 21, "w": 440, "h": 248, "cx": 10, "cy": 21, "cw": 440, "ch": 248, "show": True})
        cut = chrome.parse({"action": "ytplace", "x": 10, "y": 300, "w": 440, "h": 248, "cx": 10, "cy": 300, "cw": 440, "ch": 120.4, "show": True})
        self.assertEqual((cut["cy"], cut["ch"]), (300, 120))
        self.assertIsNone(chrome.parse({"action": "ytplace", "x": 1, "y": 1, "w": 1, "h": 1, "cx": "a", "cy": 1, "cw": 1, "ch": 1, "show": True}))
        self.assertEqual(chrome.parse({"action": "ytplace", "x": -5, "y": 0, "w": -3, "h": 9, "show": False})["w"], 0)
        for bad in ({"x": "1", "y": 0, "w": 1, "h": 1, "show": True}, {"x": 1, "y": 0, "w": 1, "h": 1, "show": "yes"},
                    {"x": 1e9, "y": 0, "w": 1, "h": 1, "show": True}, {"x": True, "y": 0, "w": 1, "h": 1, "show": True}, {"x": 1, "y": 0, "w": 1, "show": True}):
            with self.subTest(bad=bad):
                self.assertIsNone(chrome.parse({"action": "ytplace", **bad}))
        for cmd in ("toggle", "pause", "stop", "popout", "dock", "page"):
            self.assertEqual(chrome.parse({"action": "ytctl", "cmd": cmd}), {"action": "ytctl", "cmd": cmd})
        for cmd in ("eval", "load", "", None, 1):
            self.assertIsNone(chrome.parse({"action": "ytctl", "cmd": cmd}))

    def test_anything_else_is_refused(self):
        for raw in ('{"action": "exec", "cmd": "rm"}', '{"action": "move", "x": "1", "y": 2, "button": 1}',
                    '{"action": "move", "x": 1, "y": 2, "button": 9}', '{"action": "move", "x": 1, "y": 2, "button": true}',
                    '{"action": "titlebar", "system": "yes"}', "not json", "[]", '"maximize"', None, 5):
            self.assertIsNone(chrome.parse(raw), raw)


class TitlebarModeTest(unittest.TestCase):
    def folder(self, values):
        d = Path(tempfile.mkdtemp(prefix="pixeldeck-chrome-"))
        if values is not None:
            (d / "ui.json").write_text(values if isinstance(values, str) else json.dumps({"stamp": 1, "values": values}))
        return d

    def test_reads_your_choice_from_the_kept_settings(self):
        self.assertEqual(chrome.titlebar_mode(self.folder({"prdesk.prefs.v1": json.dumps({"titlebar": "system"})})), "system")
        self.assertEqual(chrome.titlebar_mode(self.folder({"prdesk.prefs.v1": json.dumps({"theme": "grey"})})), "custom")

    def test_defaults_to_its_own_bar_when_nothing_is_readable(self):
        for values in (None, "{broken", {"prdesk.prefs.v1": "{broken"}, {"prdesk.prefs.v1": json.dumps(["x"])}, {}):
            self.assertEqual(chrome.titlebar_mode(self.folder(values)), "custom", values)


if __name__ == "__main__":
    unittest.main()
