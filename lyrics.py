"""Song lyrics, from LRCLIB (lrclib.net), a free and open lyrics database.

Spotify's public API has no lyrics. LRCLIB needs no account and no key: PixelDeck sends it the
song's title, artist, album and length, nothing else, and only when the lyrics are open. Many
songs come with a time on every line (LRC), so the lines can follow the music.

An exact match is asked first; without one, a search by title and artist takes the record of
the same length (3 s either way), a timed one before a plain one. Answers are kept in the cache
folder, so a replay asks nothing; a song LRCLIB lacks is asked again after a day.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import config

API = "https://lrclib.net/api"
USER_AGENT = "PixelDeck (https://github.com/ahmedloumx/pixeldeck)"
TIMEOUT = 8
TEXT_MAX = 300
NEAR_S = 3                 # a search result this close in length is the same recording
MISS_TTL = 24 * 3600       # a song LRCLIB lacks is asked again after this long
CACHE_MAX = 500
STAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")


class LyricsError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def parse_lrc(text):
    """LRC text as [[ms, line], ...] in time order. A line may carry several times; tags
    such as [ar:...] and lines without a time are left out."""
    out = []
    for raw in str(text or "").splitlines():
        stamps, rest = [], raw
        while True:
            m = STAMP.match(rest)
            if not m:
                break
            minutes, seconds, frac = int(m.group(1)), int(m.group(2)), m.group(3) or "0"
            stamps.append(minutes * 60000 + seconds * 1000 + int(frac.ljust(3, "0")[:3]))
            rest = rest[m.end():]
        for ms in stamps:
            out.append([ms, rest.strip()])
    out.sort(key=lambda pair: pair[0])
    return out


def _fetch(path, params):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise LyricsError(0, f"LRCLIB did not answer: {getattr(exc, 'reason', exc)}") from None


class _Cache:
    """Answers by song, kept in the cache folder across restarts."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items = None

    def _file(self):
        return config.folder("cache") / "lyrics.json"

    def _load(self):
        if self.items is None:
            try:
                data = json.loads(self._file().read_text(encoding="utf-8"))
                self.items = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self.items = {}
        return self.items

    def get(self, key):
        with self.lock:
            hit = self._load().get(key)
        if not isinstance(hit, dict):
            return None
        if not hit.get("found") and time.time() - hit.get("at", 0) > MISS_TTL:
            return None
        return {k: v for k, v in hit.items() if k != "at"}

    def put(self, key, value):
        with self.lock:
            items = self._load()
            items[key] = {**value, "at": time.time()}
            while len(items) > CACHE_MAX:
                items.pop(next(iter(items)))
            try:
                path = self._file()
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(items), encoding="utf-8")
                tmp.replace(path)
            except OSError:
                pass  # kept in memory for this run at least

    def clear(self):
        with self.lock:
            self.items = {}


CACHE = _Cache()


def _answer(record):
    if not isinstance(record, dict):
        return {"found": False, "instrumental": False, "synced": None, "plain": None}
    synced = parse_lrc(record.get("syncedLyrics"))
    plain = record.get("plainLyrics") if isinstance(record.get("plainLyrics"), str) and record["plainLyrics"].strip() else None
    return {"found": True, "instrumental": bool(record.get("instrumental")), "synced": synced or None, "plain": plain}


def _text(value, name, required=True):
    value = (value or "").strip() if isinstance(value, str) else ""
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > TEXT_MAX:
        raise ValueError(f"{name} is longer than {TEXT_MAX} characters")
    return value


def lookup(track, artist, album, duration, fetch=_fetch):
    track, artist, album = _text(track, "track"), _text(artist, "artist"), _text(album, "album", required=False)
    if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 3600:
        raise ValueError("duration must be between 1 and 3600 seconds")
    key = "|".join((track.lower(), artist.lower(), album.lower(), str(duration)))
    kept = CACHE.get(key)
    if kept is not None:
        return kept
    params = {"track_name": track, "artist_name": artist, "duration": duration}
    if album:
        params = {"track_name": track, "artist_name": artist, "album_name": album, "duration": duration}
    status, record = fetch("/get", params)
    if status == 404:
        status, found = fetch("/search", {"track_name": track, "artist_name": artist})
        if status != 200:
            raise LyricsError(status, f"LRCLIB answered {status}")
        near = [r for r in found or [] if isinstance(r, dict) and isinstance(r.get("duration"), (int, float)) and abs(r["duration"] - duration) <= NEAR_S]
        near.sort(key=lambda r: (not r.get("syncedLyrics"), abs(r["duration"] - duration)))
        record = near[0] if near else None
    elif status != 200:
        raise LyricsError(status, f"LRCLIB answered {status}")
    out = _answer(record)
    CACHE.put(key, out)
    return out


def route(handler, engine, method, name, query, body, fetch=_fetch):
    """GET lyrics?track=&artist=&album=&duration= (seconds); None for any other route."""
    if method != "GET" or name != "lyrics":
        return None
    q = lambda k: (query.get(k) or [""])[0]  # noqa: E731
    try:
        duration = int(q("duration"))
    except ValueError:
        raise ValueError("duration must be a whole number of seconds") from None
    return lookup(q("track"), q("artist"), q("album"), duration, fetch=fetch)
