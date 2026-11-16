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
    def test_the_page_may_ask_for_five_things_only(self):
        self.assertEqual(chrome.parse('{"action": "maximize"}'), {"action": "maximize"})
        self.assertEqual(chrome.parse({"action": "close"}), {"action": "close"})
        self.assertEqual(chrome.parse('{"action": "move", "x": 12.5, "y": 8, "button": 1}'), {"action": "move", "x": 12.5, "y": 8.0, "button": 1})
        self.assertEqual(chrome.parse('{"action": "titlebar", "system": true}'), {"action": "titlebar", "system": True})
        self.assertEqual(chrome.parse('{"action": "hello"}'), {"action": "hello"})

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
