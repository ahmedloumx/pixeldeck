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
                         {"action": "youtube", "id": "dQw4w9WgXcQ", "title": "Neon Harbor"})
        self.assertEqual(chrome.parse({"action": "youtube", "id": "a-b_c-d_e-f"}), {"action": "youtube", "id": "a-b_c-d_e-f", "title": ""})
        self.assertEqual(len(chrome.parse({"action": "youtube", "id": "dQw4w9WgXcQ", "title": "x" * 900})["title"]), 200)
        for vid in ("dQw4w9WgXc", "dQw4w9WgXcQQ", "https://evil.example/x", "dQw4w9WgX/Q", "../../etc/pa", None, 12345678901, ["dQw4w9WgXcQ"]):
            with self.subTest(vid=vid):
                self.assertIsNone(chrome.parse({"action": "youtube", "id": vid}))

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
