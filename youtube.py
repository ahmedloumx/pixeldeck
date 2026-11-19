"""YouTube in the Music panel: a search, and the pictures that go with it, through the server.

Playing needs no key: the page embeds YouTube's own player (youtube-nocookie.com), and a
pasted link or video id is read here without asking Google anything. A search, and the videos
of a pasted playlist, need YOUTUBE_API_KEY, a free key from a Google Cloud project with YouTube
Data API v3 turned on. The free quota is 10,000 units a day; a search costs 100 (about 100 a
day), a playlist 1 for each 50 videos. The same question is answered from memory for a few
minutes instead of asking again.

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
import youtube_signin

FEATURES = ["youtube", "youtube-key", "youtube-playlist", "youtube-mine", *youtube_signin.FEATURES]

SETTING = "YOUTUBE_API_KEY"
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
PLAYLIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
MY_PLAYLISTS_URL = "https://www.googleapis.com/youtube/v3/playlists"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
SUBSCRIPTIONS_URL = "https://www.googleapis.com/youtube/v3/subscriptions"
CHANNEL_ID_RE = re.compile(r"UC[0-9A-Za-z_-]{22}")
PLAYLIST_PAGE = 50          # the most one call may ask for
PLAYLIST_MAX = 200          # four calls at most, so a huge playlist costs 4 units, not 100
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
# What an OAuth client looks like when pasted instead: its ID, its secret, or its JSON file.
OAUTH_RE = re.compile(r"\.apps\.googleusercontent\.com|GOCSPX-|\"client_secret\"|\"installed\"|\"web\"\s*:")
VIDEO_ID_RE = re.compile(r"[0-9A-Za-z_-]{11}")
# PL..., UU..., OLAK5uy_... and the like: never shorter than a video id, never anything but these.
PLAYLIST_ID_RE = re.compile(r"[0-9A-Za-z_-]{12,64}")
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
NO_KEY_LIST = "Queueing a playlist needs a free API key: Settings, Connected apps, YouTube. A link to one video plays without one."
PRIVATE_LIST = "That playlist is private, or it does not exist."
NO_CHANNEL = "This Google account has no YouTube channel yet, so it has no playlists or likes to show."
SCOPE_MISSING = "PixelDeck's sign-in may not read your YouTube account: sign in again and allow it."


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
    """What the page may know: whether a key is saved and where, and whether you are signed in;
    never the key, the client or a token."""
    key = api_key()
    return {"key_set": bool(key), "key_ok": bool(KEY_RE.fullmatch(key)), "setting": SETTING, "where": where(),
            "store": credentials.store().name, "env_file": str(config.env_path()), **youtube_signin.ACCOUNT.status()}


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
        # An OAuth client (its ID, its secret, or its downloaded JSON) is the other choice under
        # Create credentials, and the easy one to take by mistake: say so, never echo it.
        if OAUTH_RE.search(key):
            raise ValueError("That is an OAuth client, not an API key: it goes under Sign in with Google, below. For this "
                             "field, in the Google Cloud console, Credentials, press Create credentials, then API key.")
        raise ValueError("A YouTube API key starts with AIza (A, capital I, z, a) and has 39 letters, digits, - and _. "
                         "Copy it again from the Google Cloud console, Credentials.")
    if store.name:
        store.set(SETTING, key)
        config.save({SETTING: ""})  # off the file, so the store's value is the one read
    else:
        config.save({SETTING: key})
    CLIENT.forget()
    return status()


# ---------------------------------------------------------------- links

def _link(text):
    """(host without www./m./music., path segments, query) of an http(s) link on the default
    port with no user in it, or None. Read with urlsplit, so a link to another site that
    merely mentions YouTube is not one."""
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
    return host, [s for s in parts.path.split("/") if s], urllib.parse.parse_qs(parts.query)


def _clean_text(text):
    text = text.strip() if isinstance(text, str) else ""
    return None if not text or len(text) > 2000 or any(c.isspace() for c in text) else text


def video_id(text, bare=True):
    """The video id in a YouTube link (watch?v=, youtu.be/, /shorts/, /embed/, /live/), or a
    bare 11 character id when bare is true; None for anything else."""
    text = _clean_text(text)
    if not text:
        return None
    if VIDEO_ID_RE.fullmatch(text):
        return text if bare else None
    link = _link(text)
    if not link:
        return None
    host, segments, query = link
    found = None
    if host == "youtu.be" and segments:
        found = segments[0]
    elif host in VIDEO_HOSTS:
        if segments == ["watch"]:
            found = (query.get("v") or [""])[0]
        elif len(segments) >= 2 and segments[0] in VIDEO_PATHS:
            found = segments[1]
    return found if found and VIDEO_ID_RE.fullmatch(found) else None


def playlist_id(text):
    """The playlist id in a YouTube link's list= (playlist?list=, watch?v=...&list=,
    youtu.be/ID?list=, embed/videoseries?list=), or None. A bare id is never one: it could be
    a word to search for."""
    text = _clean_text(text)
    link = _link(text) if text and not VIDEO_ID_RE.fullmatch(text) else None
    if not link:
        return None
    host, segments, query = link
    if not (host == "youtu.be" or (host in VIDEO_HOSTS and segments and segments[0] in ("playlist", "watch", "embed"))):
        return None
    found = (query.get("list") or [""])[0]
    return found if PLAYLIST_ID_RE.fullmatch(found) else None


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
    if reasons & {"playlistNotFound", "playlistItemsNotAccessible", "playlistForbidden"}:
        return YouTubeError(status, PRIVATE_LIST)
    if reasons & {"youtubeSignupRequired", "channelNotFound"}:
        return YouTubeError(status, NO_CHANNEL)
    if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        return YouTubeError(status, SCOPE_MISSING)
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

    def __init__(self, transport=None, key=None, clock=time.monotonic, account=None):
        self.transport = transport or urllib_transport
        self._key = key
        self._account = account
        self.clock = clock
        self.lock = threading.Lock()
        self.cache = OrderedDict()   # query -> (when, answer)
        self.thumbs = OrderedDict()  # url -> (bytes, content type)
        self.thumb_bytes = 0

    def key(self):
        return (self._key if self._key is not None else api_key()).strip()

    @property
    def account(self):
        return self._account or youtube_signin.ACCOUNT

    def can_ask(self):
        """Whether the API can be asked at all, without asking anyone yet."""
        return self.account.signed_in() or bool(self.key())

    def auth(self):
        """How to call the API: ("bearer", token) when you are signed in, else ("key", key), else
        None. A sign-in Google ended falls back to the key when there is one."""
        if self.account.signed_in():
            try:
                return "bearer", self.account.access()
            except youtube_signin.NotSignedIn:
                if not self.key():
                    raise
        key = self.key()
        return ("key", key) if key else None

    def forget(self):
        with self.lock:
            self.cache.clear()

    def _get(self, base, params, auth):
        """One Data API call, answered as parsed JSON, or a YouTubeError. A token Google refuses
        is renewed once and the call asked again."""
        kind, secret = auth
        for attempt in (1, 2):
            headers = {"Accept": "application/json", "User-Agent": version.USER_AGENT}
            if kind == "bearer":
                headers["Authorization"] = f"Bearer {secret}"
            query = {**params, "key": secret} if kind == "key" else params
            status, _headers, raw = self.transport("GET", f"{base}?{urllib.parse.urlencode(query)}", headers, None)
            if status == 401 and kind == "bearer" and attempt == 1:
                secret = self.account.access(stale=secret)
                continue
            break
        if status != 200:
            raise refusal(status, raw, secret)
        if len(raw or b"") > READ_MAX:
            raise YouTubeError(502, "YouTube sent more than an answer should be.")
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise YouTubeError(502, "YouTube sent an answer PixelDeck cannot read.") from None

    def _cached(self, slot):
        with self.lock:
            hit = self.cache.get(slot)
            if hit and self.clock() - hit[0] < CACHE_TTL:
                self.cache.move_to_end(slot)
                return hit[1]
        return None

    def _keep(self, slot, answer, when):
        with self.lock:
            self.cache[slot] = (when, answer)
            self.cache.move_to_end(slot)
            while len(self.cache) > CACHE_MAX:
                self.cache.popitem(last=False)

    def search(self, text):
        """{"query", "items", "link", "cached"}: a pasted link or id as one item, asking nobody
        (with "list" when the link names a playlist too); anything else searched on YouTube, with the key."""
        if not isinstance(text, str):
            raise ValueError("q must be text")
        query = " ".join(text.split())
        if not query:
            raise ValueError("Type something to search for, or paste a YouTube link.")
        if len(query) > QUERY_MAX:
            raise ValueError(f"A search is at most {QUERY_MAX} characters.")
        can = self.can_ask()
        # Able to search, a lone word of 11 letters ("programming") is a search; an id has a digit,
        # - or _ nine times in ten, and YouTube finds the rest by their id anyway.
        ident = video_id(query, bare=not can or any(c.isdigit() or c in "-_" for c in query))
        listed = playlist_id(query)
        if ident or listed:
            out = {"query": query, "link": True, "cached": False,
                   "items": [{"id": ident, "title": None, "channel": None, "thumb": thumb_for(ident), "live": False}] if ident else []}
            return {**out, "list": listed} if listed else out
        if not can:
            raise NoKey(NO_KEY)
        folded = query.casefold()
        hit = self._cached(folded)
        if hit:
            return {**hit, "query": query, "cached": True}
        now = self.clock()
        payload = self._get(SEARCH_URL, {"part": "snippet", "type": "video", "maxResults": RESULTS, "safeSearch": "moderate",
                                         "videoEmbeddable": "true", "q": query}, self.auth())
        answer = {"query": query, "link": False, "cached": False, "items": trim(payload)}
        self._keep(folded, answer, now)
        return answer

    def playlist(self, ident):
        """{"list", "items", "more", "cached"}: the playable videos of a playlist, in its order,
        at most PLAYLIST_MAX; "more" says it has others past that."""
        if not isinstance(ident, str) or not PLAYLIST_ID_RE.fullmatch(ident):
            raise ValueError("That is not a YouTube playlist id.")
        if not self.can_ask():
            raise NoKey(NO_KEY_LIST)
        # Upper case, as searches are kept casefolded; the account's mark, as a signed-in answer may
        # hold its private videos.
        slot = f"LIST {ident} {self.account.fingerprint()}"
        hit = self._cached(slot)
        if hit:
            return {**hit, "cached": True}
        now = self.clock()
        items, more = self._pages(PLAYLIST_URL, {"part": "snippet,status", "playlistId": ident}, trim_playlist)
        answer = {"list": ident, "items": items, "more": more, "cached": False}
        self._keep(slot, answer, now)
        return answer

    MINE = {
        "playlists": (MY_PLAYLISTS_URL, {"part": "snippet,contentDetails", "mine": "true"}),
        "liked": (VIDEOS_URL, {"part": "snippet", "myRating": "like"}),
        "subscriptions": (SUBSCRIPTIONS_URL, {"part": "snippet", "mine": "true", "order": "alphabetical"}),
    }

    def mine(self, what):
        """{"what", "items", "more", "cached"}: your playlists, liked videos or subscriptions, from
        your sign-in. A playlist or a subscription answers a list id to open with playlist()."""
        if what not in self.MINE:
            raise ValueError("what must be playlists, liked or subscriptions")
        if not self.account.signed_in():
            raise youtube_signin.NotSignedIn(youtube_signin.NOT_SIGNED_IN)
        slot = f"MINE {what} {self.account.fingerprint()}"
        hit = self._cached(slot)
        if hit:
            return {**hit, "cached": True}
        now = self.clock()
        base, params = self.MINE[what]
        trimmer = {"playlists": trim_my_playlists, "liked": trim_videos, "subscriptions": trim_subscriptions}[what]
        items, more = self._pages(base, params, trimmer)
        answer = {"what": what, "items": items, "more": more, "cached": False}
        self._keep(slot, answer, now)
        return answer

    def _pages(self, base, params, trimmer):
        """(items, more): up to PLAYLIST_MAX // PLAYLIST_PAGE pages of 50. Pages, not items, bound
        the cost: a list of private videos would otherwise page on."""
        items, token, auth = [], None, self.auth()
        for _page in range(PLAYLIST_MAX // PLAYLIST_PAGE):
            payload = self._get(base, {**params, "maxResults": PLAYLIST_PAGE, **({"pageToken": token} if token else {})}, auth)
            items += trimmer(payload)
            token = payload.get("nextPageToken") if isinstance(payload, dict) else None
            if not isinstance(token, str) or not token:
                return items, False
        return items, True

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


def _items(payload):
    found = payload.get("items") if isinstance(payload, dict) else None
    return [(item, item["snippet"] if isinstance(item.get("snippet"), dict) else {}) for item in found or [] if isinstance(item, dict)]


def _thumb(snip):
    """The snippet's medium picture, else another size, when it is a video picture on i.ytimg.com."""
    pics = snip.get("thumbnails") if isinstance(snip.get("thumbnails"), dict) else {}
    return next((p.get("url") for p in (pics.get(size) for size in ("medium", "high", "default"))
                 if isinstance(p, dict) and isinstance(p.get("url"), str) and THUMB_RE.fullmatch(p["url"])), None)


def _video(ident, snip, channel_key="channelTitle"):
    return {"id": ident, "title": _text(snip.get("title")) or "Untitled", "channel": _text(snip.get(channel_key)),
            "thumb": _thumb(snip) or thumb_for(ident), "live": snip.get("liveBroadcastContent") == "live"}


def trim(payload):
    """Search results as {id, title, channel, thumb, live}: only videos with a usable id."""
    out = []
    for item, snip in _items(payload):
        ident = (item.get("id") or {}).get("videoId") if isinstance(item.get("id"), dict) else None
        if isinstance(ident, str) and VIDEO_ID_RE.fullmatch(ident):
            out.append(_video(ident, snip))
    return out


def trim_videos(payload):
    """A videos.list answer (your liked videos) as search results: there the id is the item's own."""
    return [_video(item["id"], snip) for item, snip in _items(payload)
            if isinstance(item.get("id"), str) and VIDEO_ID_RE.fullmatch(item["id"])]


def trim_my_playlists(payload):
    """Your playlists as {list, title, count, thumb, channel: False}."""
    out = []
    for item, snip in _items(payload):
        ident = item.get("id")
        if not isinstance(ident, str) or not PLAYLIST_ID_RE.fullmatch(ident):
            continue
        details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
        count = details.get("itemCount")
        out.append({"list": ident, "title": _text(snip.get("title")) or "Untitled", "channel": False,
                    "count": count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None,
                    "thumb": _thumb(snip)})
    return out


def trim_subscriptions(payload):
    """Your subscriptions as lists to open: a channel's uploads are the playlist UU + the rest of
    its id (UC...), so no call is spent to find it. Channel pictures live on a host the page may
    not load from, so there is no thumb."""
    out = []
    for _item, snip in _items(payload):
        res = snip.get("resourceId") if isinstance(snip.get("resourceId"), dict) else {}
        cid = res.get("channelId")
        if res.get("kind") != "youtube#channel" or not isinstance(cid, str) or not CHANNEL_ID_RE.fullmatch(cid):
            continue
        out.append({"list": "UU" + cid[2:], "title": _text(snip.get("title")) or "A channel", "channel": True, "count": None, "thumb": None})
    return out


def trim_playlist(payload):
    """A playlist page as {id, title, channel, thumb, live}: only videos that can play, so not
    the private or deleted ones a playlist keeps a place for."""
    out = []
    for item, snip in _items(payload):
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        res = snip.get("resourceId") if isinstance(snip.get("resourceId"), dict) else {}
        ident = res.get("videoId")
        if res.get("kind") != "youtube#video" or not isinstance(ident, str) or not VIDEO_ID_RE.fullmatch(ident):
            continue
        if status.get("privacyStatus") not in ("public", "unlisted"):
            continue
        # channelTitle is the playlist's owner; the video's own channel is videoOwnerChannelTitle
        out.append({**_video(ident, snip, "videoOwnerChannelTitle"), "live": False})
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
    if method == "GET" and name == "youtube-playlist":
        return CLIENT.playlist(_arg(query, "list"))
    if method == "GET" and name == "youtube-mine":
        return CLIENT.mine(_arg(query, "what"))
    signed = youtube_signin.route(handler, method, name, body)
    if signed is not None:
        return signed
    return None
