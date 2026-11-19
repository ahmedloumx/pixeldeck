"""Run with: python3 -m unittest discover -s tests  (from the PixelDeck folder).

Every frame a sprite has must have a CSS rule that shows it: a frame without one draws nothing,
and the sprite blinks out while it plays."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# sprites.js needs a few browser globals to load; this answers them with nothing.
_STUBS = """
globalThis.addEventListener = () => {};
globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.setInterval = () => 0;
globalThis.document = { addEventListener() {}, querySelectorAll: () => [], getElementById: () => null, hidden: false };
"""


@unittest.skipUnless(shutil.which("node"), "needs node")
class FramesShownTest(unittest.TestCase):
    def test_every_frame_of_every_sprite_has_a_rule(self):
        src = (ROOT / "static" / "sprites.js").read_text(encoding="utf-8")
        defs = src[src.index("const DEFS = {"):]
        names = re.findall(r"^    (\w+): \{", defs[:defs.index("\n  };")], re.M)
        self.assertIn("bot", names)
        script = _STUBS + src + f"""
const out = {{}};
for (const n of {json.dumps(names)}) out[n] = (Sprites.svg(n).match(/<g data-f="/g) || []).length;
process.stdout.write(JSON.stringify(out));"""
        counts = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout)
        css = (ROOT / "static" / "arcade.css").read_text(encoding="utf-8")
        shown = {int(a) for a, b in re.findall(r'\.spr\[data-frame="(\d+)"\] g\[data-f="(\d+)"\]', css) if a == b}
        for name, n in counts.items():
            with self.subTest(sprite=name):
                self.assertTrue(set(range(n)) <= shown, f"{name} has {n} frames; arcade.css shows {sorted(shown)}")

    def test_the_wiggle_only_uses_frames_the_bot_has(self):
        src = (ROOT / "static" / "sprites.js").read_text(encoding="utf-8")
        steps = re.search(r"function wiggle\(el\) \{.*?const steps = (\[\[.*?\]\]);", src, re.S).group(1)
        frames = {f for f, _ms in json.loads(steps)}
        script = _STUBS + src + "\nprocess.stdout.write(String((Sprites.svg('bot').match(/<g data-f=\"/g) || []).length));"
        bot = int(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout)
        self.assertTrue(frames <= set(range(bot)), (frames, bot))


if __name__ == "__main__":
    unittest.main()
