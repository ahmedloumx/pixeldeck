"""Which PixelDeck this is: a version for people, and a fingerprint of the code for the launcher.

The version changes when we release. The fingerprint changes with any edit to the Python
files next to this one, so the launcher notices a server that still runs the code from before
a `git pull`, even when nobody changed the version. A packaged app has no Python files to
hash: the build writes build-info.json into it instead.
"""

import hashlib
import json

import config


def _baked():
    if not config.FROZEN:
        return {}
    try:
        info = json.loads((config.APP_DIR / "build-info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


_INFO = _baked()
VERSION = _INFO.get("version") or "0.1.0"
USER_AGENT = f"PixelDeck/{VERSION}"


def build():
    if config.FROZEN:
        return _INFO.get("build") or VERSION
    digest = hashlib.sha256()
    for path in sorted(config.APP_DIR.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]
