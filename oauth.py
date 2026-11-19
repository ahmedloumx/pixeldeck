"""What a sign-in with OAuth needs on this side, whatever the service: PKCE, the sign-ins started
and not finished yet, and a private file for the tokens."""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from collections import OrderedDict

STATE_TTL = 600     # a sign-in link is good for ten minutes, once
PENDING_MAX = 8     # sign-ins started and not finished; the oldest is dropped first


def pkce_verifier():
    # 86 characters of [A-Za-z0-9_-]: inside the 43 to 128 unreserved characters the spec asks for
    return secrets.token_urlsafe(64)


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def write_private(path, payload):
    """A temp file at mode 600, then an atomic replace: no reader ever sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":")))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Pending:
    """Sign-ins started and not finished: a random state names what its callback needs. Each is
    taken once, and only within STATE_TTL, so a link from history or another tab cannot finish."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.items = OrderedDict()

    def add(self, entry):
        state = secrets.token_urlsafe(24)
        with self.lock:
            now = self.clock()
            for key in [k for k, v in self.items.items() if now - v["at"] > STATE_TTL]:
                del self.items[key]
            self.items[state] = {**entry, "at": now}
            while len(self.items) > PENDING_MAX:
                self.items.popitem(last=False)
        return state

    def take(self, state):
        """The entry for this state, once: it is removed before its age is checked."""
        if not isinstance(state, str) or not state:
            return None
        with self.lock:
            entry = self.items.pop(state, None)
        if entry is None or self.clock() - entry["at"] > STATE_TTL:
            return None
        return entry
