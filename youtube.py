"""YouTube in the Music panel: a search, and the pictures that go with it, through the server.

Playing needs no key: the page embeds YouTube's own player (youtube-nocookie.com), and a
pasted link or video id is read here without asking Google anything. Only a search needs
YOUTUBE_API_KEY, a free key from a Google Cloud project with YouTube Data API v3 turned on.
The free quota is 10,000 units a day and a search costs 100, so about 100 searches a day:
the same search is answered from memory for a few minutes instead of asking again.

The key is saved from the settings (the credential store when the system has one, else the
settings file), goes to Google only, and never back to the page or into an error. Thumbnails
come from i.ytimg.com through here, because the page's CSP allows no other host for pictures.
"""

import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from html.parser import HTMLParser

import config
import credentials
import version

FEATURES = ["youtube", "youtube-key"]

SETTING = "YOUTUBE_API_KEY"
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
TIMEOUT = 10
READ_MAX = 1024 * 1024
RESULTS = 12
QUERY_MAX = 200
TEXT_MAX = 300
CACHE_TTL = 600            # ten minutes: a search repeated in that time costs nothing
CACHE_MAX = 64
THUMB_MAX = 256 * 1024
THUMB_KEEP = 300           # list thumbnails repeat: keep many, but no more than
THUMB_BYTES = 8 * 1024 * 1024  # this much in memory

# A Google API key: "AIza" and 35 characters more.
KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{35}")
VIDEO_ID_RE = re.compile(r"[0-9A-Za-z_-]{11}")
# A video's picture on YouTube's image host, by its id. Anything else is refused, so this
# route can never be pointed at another host, port, path or query.
THUMB_RE = re.compile(r"https://i\.ytimg\.com/vi(?:_webp)?/[0-9A-Za-z_-]{11}/[0-9A-Za-z_]{1,40}\.(?:jpg|webp)")
THUMB_TYPES = {"image/jpeg", "image/webp", "image/png"}
# Where a link to one video can live, once "www.", "m." or "music." is off the host.
VIDEO_HOSTS = {"youtube.com", "youtube-nocookie.com"}
VIDEO_PATHS = {"shorts", "embed", "live", "v", "e"}

NO_KEY = "Searching YouTube needs a free API key: Settings, Connected apps, YouTube. A pasted link plays without one."
QUOTA = ("YouTube's free quota for today is used up: 10,000 units a day, and a search costs 100, so about 100 searches. "
         "It starts again at midnight, Pacific time. A pasted link still plays.")
BAD_KEY = "Google refused the API key. Check it in Settings, Connected apps, YouTube: it starts with AIza."
NOT_ENABLED = "YouTube Data API v3 is not turned on for this key's project: enable it in the Google Cloud console, then search again."
RESTRICTED = ("This key may not call YouTube Data API v3 from here. In the Google Cloud console, Credentials, let the key "
              "use YouTube Data API v3, with no website or IP address restriction.")


class YouTubeError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class NoKey(PermissionError):
    """A search without an API key; a pasted link needs none."""


# ---------------------------------------------------------------- the key

def api_key():
    """The saved key: the environment, then the settings file, then the credential store."""
    return (config.get(SETTING) or credentials.store().get(SETTING) or "").strip()


def where():
    if os.environ.get(SETTING):
        return "environment"
    if config.file_value(SETTING):
        return "file"
    if credentials.store().get(SETTING):
        return "store"
    return None


def status():
    """What the page may know: whether a key is saved and where, never the key."""
    key = api_key()
    return {"key_set": bool(key), "key_ok": bool(KEY_RE.fullmatch(key)), "setting": SETTING, "where": where(),
            "store": credentials.store().name, "env_file": str(config.env_path())}


def save_key(body):
    """Save the API key from the settings, or remove it with "clear". A key goes to the
    credential store when there is one (and off the file), else to the settings file."""
    store = credentials.store()
    if body.get("clear"):
        config.save({SETTING: ""})
        store.delete(SETTING)
        CLIENT.forget()
        return status()
    raw = body.get("key")
    key = raw.strip() if isinstance(raw, str) else ""
    if not KEY_RE.fullmatch(key):
        raise ValueError("A YouTube API key starts with AIza and has 39 letters, digits, - and _. "
                         "Copy it again from the Google Cloud console, Credentials.")
    if store.name:
        store.set(SETTING, key)
        config.save({SETTING: ""})  # off the file, so the store's value is the one read
    else:
        config.save({SETTING: key})
    CLIENT.forget()
    return status()


# ---------------------------------------------------------------- links

def video_id(text, bare=True):
    """The video id in a YouTube link (watch?v=, youtu.be/, /shorts/, /embed/, /live/), or a
    bare 11 character id when bare is true; None for anything else. Read with urlsplit, so a
    link to another site that merely mentions YouTube is not one."""
    text = text.strip() if isinstance(text, str) else ""
    if not text or len(text) > 2000 or any(c.isspace() for c in text):
        return None
    if VIDEO_ID_RE.fullmatch(text):
        return text if bare else None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in ("http", "https") or port not in (None, 80, 443) or parts.username or parts.password:
        return None
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    segments = [s for s in parts.path.split("/") if s]
    found = None
    if host == "youtu.be" and segments:
        found = segments[0]
    elif host in VIDEO_HOSTS:
        if segments == ["watch"]:
            found = (urllib.parse.parse_qs(parts.query).get("v") or [""])[0]
        elif len(segments) >= 2 and segments[0] in VIDEO_PATHS:
            found = segments[1]
    return found if found and VIDEO_ID_RE.fullmatch(found) else None


def thumb_for(ident):
    return f"https://i.ytimg.com/vi/{ident}/mqdefault.jpg"


def thumb_url(value):
    """The picture URL when it is exactly a video picture on i.ytimg.com (see THUMB_RE), else ValueError."""
    if not isinstance(value, str) or not THUMB_RE.fullmatch(value):
        raise ValueError("a thumbnail must be a video picture on i.ytimg.com")
    return value


# ---------------------------------------------------------------- the transport

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # an answer that points elsewhere is an error, never a hop to another host


_opener = []  # built on first use: building it at import makes an SSL context, too early on some platforms


def urllib_transport(method, url, headers, body):
    """One HTTP exchange: (status, lower-cased headers, at most READ_MAX + 1 bytes). Its errors
    never carry the URL, which holds the key."""
    if not _opener:
        _opener.append(urllib.request.build_opener(_NoRedirect))
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _opener[0].open(request, timeout=TIMEOUT) as res:
            return res.status, {k.lower(): v for k, v in res.headers.items()}, res.read(READ_MAX + 1)
    except urllib.error.HTTPError as exc:
        with exc:
            raw = exc.read(READ_MAX + 1) if exc.fp else b""
            return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, raw
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else type(exc).__name__
        raise YouTubeError(0, f"YouTube could not be reached: {reason}") from None


def _text(value):
    """Plain text from YouTube's snippet, which escapes its titles as HTML."""
    return html.unescape(value)[:TEXT_MAX] if isinstance(value, str) and value.strip() else None


def _reasons(payload):
    """Every reason Google gives for a refusal, old style (errors[].reason) and new (details[].reason)."""
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        return set(), ""
    found = {e.get("reason") for e in err.get("errors") or [] if isinstance(e, dict)}
    found |= {d.get("reason") for d in err.get("details") or [] if isinstance(d, dict)}
    message = err.get("message") if isinstance(err.get("message"), str) else ""
    return {r for r in found if isinstance(r, str)}, message


class _Words(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _words(message):
    reader = _Words()
    reader.feed(message)
    reader.close()
    return "".join(reader.parts)


def refusal(status, raw, key=""):
    """A YouTubeError that says what to do about Google's answer."""
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        payload = {}
    reasons, message = _reasons(payload)
    if reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded", "RATE_LIMIT_EXCEEDED"} or status == 429:
        return YouTubeError(status, QUOTA)
    if reasons & {"keyInvalid", "keyExpired", "API_KEY_INVALID", "API_KEY_EXPIRED"} or (status == 400 and "api key" in message.lower()):
        return YouTubeError(status, BAD_KEY)
    if reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
        return YouTubeError(status, NOT_ENABLED)
    if reasons & {"API_KEY_SERVICE_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED", "API_KEY_IP_ADDRESS_BLOCKED",
                  "API_KEY_ANDROID_APP_BLOCKED", "API_KEY_IOS_APP_BLOCKED", "ipRefererBlocked"}:
        return YouTubeError(status, RESTRICTED)
    # Google writes some messages with links in them: the words only, without the key, and short.
    words = _words(message)
    if key:
        words = words.replace(key, "[key]")
    words = " ".join(words.split())[:200]
    return YouTubeError(status, f"YouTube answered {status}{f': {words}' if words else ''}")


# ---------------------------------------------------------------- the client

class Client:
    """Search and thumbnails. The transport, the key and the clock can be swapped in tests, so
    nothing there talks to Google."""

    def __init__(self, transport=None, key=None, clock=time.monotonic):
        self.transport = transport or urllib_transport
        self._key = key
        self.clock = clock
        self.lock = threading.Lock()
        self.cache = OrderedDict()   # query -> (when, answer)
        self.thumbs = OrderedDict()  # url -> (bytes, content type)
        self.thumb_bytes = 0

    def key(self):
        return (self._key if self._key is not None else api_key()).strip()

    def forget(self):
        with self.lock:
            self.cache.clear()

    def search(self, text):
        """{"query", "items", "link", "cached"}: a pasted link or id as one item, asking nobody;
        anything else searched on YouTube, with the key."""
        if not isinstance(text, str):
            raise ValueError("q must be text")
        query = " ".join(text.split())
        if not query:
            raise ValueError("Type something to search for, or paste a YouTube link.")
        if len(query) > QUERY_MAX:
            raise ValueError(f"A search is at most {QUERY_MAX} characters.")
        key = self.key()
        # With a key, a lone word of 11 letters ("programming") is a search; an id has a digit, - or _
        # nine times in ten, and YouTube finds the rest by their id anyway.
        ident = video_id(query, bare=not key or any(c.isdigit() or c in "-_" for c in query))
        if ident:
            return {"query": query, "link": True, "cached": False,
                    "items": [{"id": ident, "title": None, "channel": None, "thumb": thumb_for(ident), "live": False}]}
        if not key:
            raise NoKey(NO_KEY)
        folded = query.casefold()
        now = self.clock()
        with self.lock:
            hit = self.cache.get(folded)
            if hit and now - hit[0] < CACHE_TTL:
                self.cache.move_to_end(folded)
                return {**hit[1], "query": query, "cached": True}
        params = {"part": "snippet", "type": "video", "maxResults": RESULTS, "safeSearch": "moderate",
                  "videoEmbeddable": "true", "q": query, "key": key}
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        status, _headers, raw = self.transport("GET", url, {"Accept": "application/json", "User-Agent": version.USER_AGENT}, None)
        if status != 200:
            raise refusal(status, raw, key)
        if len(raw or b"") > READ_MAX:
            raise YouTubeError(502, "YouTube sent more than a search answer should be.")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            raise YouTubeError(502, "YouTube sent an answer PixelDeck cannot read.") from None
        answer = {"query": query, "link": False, "cached": False, "items": trim(payload)}
        with self.lock:
            self.cache[folded] = (now, answer)
            self.cache.move_to_end(folded)
            while len(self.cache) > CACHE_MAX:
                self.cache.popitem(last=False)
        return answer

    def thumb(self, url):
        url = thumb_url(url)
        with self.lock:
            if url in self.thumbs:
                self.thumbs.move_to_end(url)
                return self.thumbs[url]
        # a public image host: the key never goes with this request
        status, headers, raw = self.transport("GET", url, {"Accept": "image/*", "User-Agent": version.USER_AGENT}, None)
        ctype = (headers or {}).get("content-type", "").split(";")[0].strip().lower()
        if status != 200:
            raise YouTubeError(status, f"YouTube's image server answered {status}.")
        if ctype not in THUMB_TYPES or not raw or len(raw) > THUMB_MAX:
            raise YouTubeError(502, "YouTube's image server sent something that is not a small picture.")
        with self.lock:
            if url not in self.thumbs:
                self.thumbs[url] = (raw, ctype)
                self.thumb_bytes += len(raw)
            while self.thumbs and (len(self.thumbs) > THUMB_KEEP or self.thumb_bytes > THUMB_BYTES):
                self.thumb_bytes -= len(self.thumbs.popitem(last=False)[1][0])
        return raw, ctype


def trim(payload):
    """Search results as {id, title, channel, thumb, live}: only videos with a usable id."""
    out = []
    for item in (payload.get("items") if isinstance(payload, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        ident = (item.get("id") or {}).get("videoId") if isinstance(item.get("id"), dict) else None
        if not isinstance(ident, str) or not VIDEO_ID_RE.fullmatch(ident):
            continue
        snip = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        pics = snip.get("thumbnails") if isinstance(snip.get("thumbnails"), dict) else {}
        thumb = next((p.get("url") for p in (pics.get(size) for size in ("medium", "high", "default"))
                      if isinstance(p, dict) and isinstance(p.get("url"), str) and THUMB_RE.fullmatch(p["url"])), None)
        out.append({"id": ident, "title": _text(snip.get("title")) or "Untitled", "channel": _text(snip.get("channelTitle")),
                    "thumb": thumb or thumb_for(ident), "live": snip.get("liveBroadcastContent") == "live"})
    return out


CLIENT = Client()


# ---------------------------------------------------------------- routes

def _arg(query, key):
    value = (query.get(key) or [""])[0]
    return value if isinstance(value, str) else ""


def route(handler, engine, method, name, query, body):
    """Answer a route this module owns, or None. A dict goes out as JSON; a (bytes, content
    type) pair goes out as is."""
    if not name.startswith("youtube-"):
        return None
    query, body = query or {}, body if isinstance(body, dict) else {}
    if method == "GET" and name == "youtube-status":
        return status()
    if method == "POST" and name == "youtube-key":
        return save_key(body)
    if method == "GET" and name == "youtube-search":
        return CLIENT.search(_arg(query, "q"))
    if method == "GET" and name == "youtube-thumb":
        return CLIENT.thumb(_arg(query, "url"))
    return None
