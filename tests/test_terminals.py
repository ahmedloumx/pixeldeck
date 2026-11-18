"""A program in a PixelDeck terminal owns that terminal: it hears a resize and Ctrl+C reaches it."""

import os
import sys
import tempfile
import time
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

import terminals  # noqa: E402

PROBE = """
import os, signal, sys, time
def size(*_):
    c, r = os.get_terminal_size(0)
    print(f"WINCH {c}x{r}", flush=True)
signal.signal(signal.SIGWINCH, size)
print("CTTY" if os.tcgetpgrp(0) == os.getpgrp() else "NOCTTY", flush=True)
try:
    time.sleep(20)
except KeyboardInterrupt:
    print("INTERRUPTED", flush=True)
"""


@unittest.skipIf(terminals.WINDOWS or terminals.UNAVAILABLE, "a Unix terminal is needed")
class ControllingTerminalTest(unittest.TestCase):
    def wait_for(self, session, text, timeout=5.0):
        end = time.time() + timeout
        seen = b""
        while time.time() < end:
            seen, _ = session.read_from(0, timeout=0.2)
            if text.encode() in seen:
                return seen.decode(errors="replace")
        self.fail(f"{text!r} never came; got {seen.decode(errors='replace')!r}")

    def test_a_program_hears_a_resize_and_ctrl_c(self):
        session = terminals.UnixSession("probe", [sys.executable, "-c", PROBE], tempfile.gettempdir(), cols=100, rows=30)
        self.addCleanup(session.close)
        self.wait_for(session, "CTTY")
        session.resize(180, 44)
        self.wait_for(session, "WINCH 180x44")
        session.write(b"\x03")  # Ctrl+C, as the page sends it
        self.wait_for(session, "INTERRUPTED")


if __name__ == "__main__":
    unittest.main()
