"""Which PixelDeck this is: a version for people, and a fingerprint of the code for the launcher.

The version changes when we release. The fingerprint changes with any edit to the Python
files next to this one, so the launcher notices a server that still runs the code from before
a `git pull`, even when nobody changed the version.
"""

import hashlib

import config

VERSION = "0.1.0"


def build():
    digest = hashlib.sha256()
    for path in sorted(config.APP_DIR.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]
