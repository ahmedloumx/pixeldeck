"""Run with: python3 -m unittest discover -s tests  (from the PixelDeck folder).

The page's scripts share one global scope. A second top-level declaration of the same name,
in the same file or another, silently replaces the first (a function) or stops the page (a
const): a helper added for one view once replaced another view's and broke the start."""

import collections
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
DECL = re.compile(r"^(?:async\s+)?(?:function\*?\s+|(?:const|let|var|class)\s+)([A-Za-z_$][\w$]*)", re.M)


class GlobalNamesTest(unittest.TestCase):
    def test_no_two_top_level_declarations_share_a_name(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        files = [f for f in re.findall(r'<script src="/static/([^"]+\.js)"', html) if not f.startswith("vendor/")]
        self.assertIn("app.js", files)
        seen = collections.defaultdict(list)
        for f in files:
            for name in DECL.findall((STATIC / f).read_text(encoding="utf-8")):
                seen[name].append(f)
        self.assertEqual({n: where for n, where in seen.items() if len(where) > 1}, {})


if __name__ == "__main__":
    unittest.main()
