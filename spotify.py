"""The music player's routes: the Spotify connection and playback, through the server.

Sign-in is the Authorization Code flow with PKCE, so there is no client secret, only
SPOTIFY_CLIENT_ID from the .env (re-read when it changes). The system browser goes to
Spotify and comes back to /spotify/callback on this server. The tokens it earns are kept
in the state folder (mode 600) and never reach the page or a log: the page gets trimmed
answers, and album art fetched here from i.scdn.co, because its CSP allows no other host.
"""

import base64
import hashlib
import html
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path

import config
import opener

FEATURES = ["spotify"]

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = ("user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private user-read-private "
          "user-library-read user-library-modify user-read-recently-played playlist-read-collaborative")
# What the library asks for beyond the first sign-in. Someone who connected before these were
# asked for gets a "reconnect" answer from the route that needs one, never a failure.
LIBRARY_READ, LIBRARY_WRITE, RECENT = "user-library-read", "user-library-modify", "user-read-recently-played"
SETTING = "SPOTIFY_CLIENT_ID"

STATE_TTL = 600        # a sign-in link is good for ten minutes, once
PENDING_MAX = 8        # sign-ins started and not finished; the oldest is dropped first
PROFILE_TTL = 600      # the display name and plan change rarely
EARLY_REFRESH = 60     # renew a little before Spotify's hour is up
TIMEOUT = 10
READ_MAX = 2 * 1024 * 1024
ART_MAX = 512 * 1024
ART_KEEP = 400                  # list thumbnails repeat a lot: keep many, but no more than
ART_BYTES = 16 * 1024 * 1024    # this much in memory
PAGE = 50                       # Spotify's largest page for library lists
SMALL_PAGE = 10                 # search and artist albums: smaller pages, also the retry on a refused limit
WAIT_MAX = 3600        # a Retry-After beyond this is treated as an hour

# Album art lives on Spotify's image host under a hex id. Anything else is refused, so this
# route can never be pointed at another host, port, path or query.
ART_RE = re.compile(r"https://i\.scdn\.co/image/[0-9a-f]{16,64}")
ART_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
CLIENT_ID_RE = re.compile(r"[0-9A-Za-z]{16,64}")
PLAYLIST_ID_RE = re.compile(r"[0-9A-Za-z]{1,64}")
ID_RE = PLAYLIST_ID_RE  # Spotify ids are base62
TRACK_URI_RE = re.compile(r"spotify:(?:track|episode):[0-9A-Za-z]{1,64}")
CONTEXT_URI_RE = re.compile(r"spotify:(playlist|album|artist):[0-9A-Za-z]{1,64}")
SEARCH_TYPES = ("track", "album", "artist", "playlist")
DEVICE_ID_RE = re.compile(r"[0-9A-Za-z_-]{1,128}")
REPEAT_MODES = ("off", "context", "track")
DISALLOWS = ("pausing", "resuming", "seeking", "skipping_next", "skipping_prev", "toggling_repeat_context",
             "toggling_repeat_track", "toggling_shuffle", "transferring_playback")
TEXT_MAX = 300

PREMIUM = "Playback control needs Spotify Premium: play, pause, skip, seek, volume and devices are Premium only."
NOT_CONNECTED = "Spotify is not connected. Press Connect in the Music panel."
NO_DEVICE = "No active Spotify device. Open Spotify on a phone or computer, or pick a device in the list."
NOT_SHARED = "Spotify does not share this playlist with apps. Its own editorial playlists stay inside the Spotify app."
RECONNECT = "Spotify was connected before PixelDeck asked to read your library. Reconnect to unlock it."


class SpotifyError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class NotConnected(PermissionError):
    """No usable sign-in: the server answers 403 and the page shows Connect."""


# ---------------------------------------------------------------- plumbing

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # an answer that points elsewhere is an error, never a hop to another host


_opener = []  # built on first use: building it at import makes an SSL context, too early on some platforms


def urllib_transport(method, url, headers, body):
    """One HTTP exchange: (status, lower-cased headers, at most READ_MAX + 1 bytes)."""
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
        raise SpotifyError(0, f"Spotify could not be reached: {getattr(exc, 'reason', exc)}") from None


def _write_private(path, payload):
    """Same as the engine's writer: a temp file at mode 600, then an atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, separators=(",", ":")))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _json(raw):
    try:
        value = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _text(value):
    return str(value)[:TEXT_MAX] if isinstance(value, (str, int, float)) and value != "" else None


def _int(value, default=0):
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def pkce_verifier():
    # 86 characters of [A-Za-z0-9_-]: inside the 43 to 128 unreserved characters the spec asks for
    return secrets.token_urlsafe(64)


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_uri(port):
    # Spotify allows plain http only for a loopback IP literal; "localhost" is refused.
    return f"http://127.0.0.1:{int(port)}/spotify/callback"


def art_url(value):
    """The album art URL when it is exactly https://i.scdn.co/image/<hex id>, else ValueError."""
    if not isinstance(value, str) or not ART_RE.fullmatch(value):
        raise ValueError("art must be an https://i.scdn.co/image/ address")
    return value


# ---------------------------------------------------------------- trimming

def pick_art(images, at_least=300):
    """The smallest picture at least this wide. The player's sharpest look draws 128 pixels
    across, so 300 covers it; a list thumbnail asks for 64."""
    good = [i for i in images or [] if isinstance(i, dict) and isinstance(i.get("url"), str) and ART_RE.fullmatch(i["url"])]
    if not good:
        return None
    good.sort(key=lambda i: _int(i.get("width")))
    return next((i["url"] for i in good if _int(i.get("width")) >= at_least), good[-1]["url"])


def _pictures(images):
    return {"art": pick_art(images, 300), "thumb": pick_art(images, 64)}


def _uri(value, pattern):
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _ident(value):
    return value if isinstance(value, str) and ID_RE.fullmatch(value) else None


def _artists(raw):
    people = [a for a in raw or [] if isinstance(a, dict)]
    return [_text(a.get("name")) or "Unknown artist" for a in people][:6], [_ident(a.get("id")) for a in people][:6]


def trim_device(raw):
    if not isinstance(raw, dict):
        return None
    ident = raw.get("id") if isinstance(raw.get("id"), str) and DEVICE_ID_RE.fullmatch(raw["id"]) else None
    volume = raw.get("volume_percent")
    return {
        "id": ident, "name": _text(raw.get("name")) or "Unknown device", "type": _text(raw.get("type")) or "",
        "active": bool(raw.get("is_active")), "restricted": bool(raw.get("is_restricted")),
        "volume": volume if _int(volume, -1) in range(0, 101) else None,
        "supports_volume": bool(raw.get("supports_volume", volume is not None)),
    }


def trim_item(raw, album=None):
    """A track or an episode. album stands in when the list is that album's own tracks, which
    Spotify sends without the album (and so without its pictures)."""
    if not isinstance(raw, dict):
        return None
    if raw.get("type") == "episode":
        show = raw.get("show") if isinstance(raw.get("show"), dict) else {}
        artists, artist_ids = [_text(show.get("publisher")) or _text(show.get("name")) or "Podcast"], [None]
        album_raw, images = {"name": show.get("name"), "id": show.get("id")}, raw.get("images") or show.get("images")
    else:
        artists, artist_ids = _artists(raw.get("artists"))
        album_raw = raw.get("album") if isinstance(raw.get("album"), dict) else album if isinstance(album, dict) else {}
        images = album_raw.get("images")
    return {
        "id": _ident(raw.get("id")), "uri": _uri(raw.get("uri"), TRACK_URI_RE),
        "kind": "episode" if raw.get("type") == "episode" else "track",
        "name": _text(raw.get("name")) or "Untitled", "artists": artists, "artist_ids": artist_ids,
        "album": _text(album_raw.get("name")), "album_id": _ident(album_raw.get("id")),
        "number": _int(raw.get("track_number"), None), "duration_ms": max(0, _int(raw.get("duration_ms"))),
        "playable": raw.get("is_playable") is not False and not raw.get("is_local"), **_pictures(images),
    }


def trim_player(raw):
    """GET /me/player cut down to what the deck shows. None (a 204) means nothing is playing."""
    if not isinstance(raw, dict):
        return {"connected": True, "active": False, "is_playing": False, "progress_ms": 0, "shuffle": False,
                "repeat": "off", "device": None, "item": None, "playlist": None, "disallows": []}
    context = raw.get("context") if isinstance(raw.get("context"), dict) else {}
    uri = context.get("uri") if isinstance(context.get("uri"), str) else ""
    playlist = uri.rsplit(":", 1)[-1] if uri.startswith("spotify:playlist:") else None
    disallows = ((raw.get("actions") or {}).get("disallows") or {}) if isinstance(raw.get("actions"), dict) else {}
    return {
        "connected": True, "active": True, "is_playing": bool(raw.get("is_playing")),
        "progress_ms": max(0, _int(raw.get("progress_ms"))), "shuffle": bool(raw.get("shuffle_state")),
        "repeat": raw.get("repeat_state") if raw.get("repeat_state") in REPEAT_MODES else "off",
        "device": trim_device(raw.get("device")), "item": trim_item(raw.get("item")),
        "playlist": playlist if playlist and PLAYLIST_ID_RE.fullmatch(playlist) else None,
        "disallows": [k for k in DISALLOWS if isinstance(disallows, dict) and disallows.get(k)],
    }


def trim_playlist(raw):
    if not isinstance(raw, dict) or not _ident(raw.get("id")):
        return None
    owner = raw.get("owner") if isinstance(raw.get("owner"), dict) else {}
    tracks = raw.get("tracks") if isinstance(raw.get("tracks"), dict) else raw.get("items") if isinstance(raw.get("items"), dict) else {}
    return {"id": raw["id"], "uri": f"spotify:playlist:{raw['id']}", "name": _text(raw.get("name")) or "Untitled",
            "owner": _text(owner.get("display_name")), "by_spotify": owner.get("id") == "spotify",
            "tracks": _int(tracks.get("total"), None), **_pictures(raw.get("images"))}


def trim_album(raw):
    if not isinstance(raw, dict) or not _ident(raw.get("id")):
        return None
    artists, artist_ids = _artists(raw.get("artists"))
    tracks = raw.get("tracks") if isinstance(raw.get("tracks"), dict) else {}
    return {"id": raw["id"], "uri": f"spotify:album:{raw['id']}", "name": _text(raw.get("name")) or "Untitled",
            "artists": artists, "artist_ids": artist_ids, "year": (_text(raw.get("release_date")) or "")[:4] or None,
            "tracks": _int(raw.get("total_tracks"), _int(tracks.get("total"), None)), **_pictures(raw.get("images"))}


def trim_artist(raw):
    if not isinstance(raw, dict) or not _ident(raw.get("id")):
        return None
    genres = [_text(g) for g in raw.get("genres") or [] if _text(g)][:3]
    return {"id": raw["id"], "uri": f"spotify:artist:{raw['id']}", "name": _text(raw.get("name")) or "Unknown artist",
            "genres": genres, **_pictures(raw.get("images"))}


# ---------------------------------------------------------------- the client

class Client:
    """Spotify for one user. The transport, token file, client id and clock can be swapped in
    tests, so nothing there talks to Spotify or touches your real state folder."""

    def __init__(self, transport=None, store=None, client_id=None, clock=time.time):
        self.transport = transport or urllib_transport
        self._store = Path(store) if store else None
        self._client_id = client_id
        self.clock = clock
        self.lock = threading.RLock()
        self.pending = OrderedDict()   # state -> what the callback needs to finish that sign-in
        self.profile = None
        self.profile_at = 0
        self.not_before = 0            # Spotify asked for a pause (429) until then
        self.art = OrderedDict()
        self.art_bytes = 0
        self.art_lock = threading.Lock()
        self.items_path = None         # "items" or "tracks": which name this account's API answers for a playlist's songs

    # ---- settings and the token file ---------------------------------

    def client_id(self):
        value = self._client_id if self._client_id is not None else config.get(SETTING)
        return (value or "").strip()

    def store(self):
        return self._store or config.folder("state") / "spotify.json"

    def _load(self):
        try:
            data = json.loads(self.store().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("refresh_token"), str) or not isinstance(data.get("access_token"), str):
            return None
        # tokens belong to the app that earned them: a new client id needs a new sign-in
        return data if data.get("client_id") == self.client_id() else None

    def connected(self):
        return self._load() is not None

    def granted(self):
        """The scopes Spotify granted at sign-in (or the last renewal that named them)."""
        tok = self._load() or {}
        return set(tok.get("scope", "").split()) if isinstance(tok.get("scope"), str) else set()

    def forget(self):
        with self.lock:
            try:
                self.store().unlink()
            except FileNotFoundError:
                pass
            self.profile, self.profile_at = None, 0

    def _keep(self, payload, client_id, previous=None):
        access = payload.get("access_token")
        refresh = payload.get("refresh_token") or (previous or {}).get("refresh_token")
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise SpotifyError(502, "Spotify's answer carried no usable token.")
        expires = payload.get("expires_in")
        data = {
            "client_id": client_id, "access_token": access, "refresh_token": refresh,
            "expires_at": self.clock() + (expires if _int(expires, 0) > 0 else 3600),
            "scope": payload.get("scope") if isinstance(payload.get("scope"), str) else (previous or {}).get("scope", ""),
        }
        _write_private(self.store(), data)
        return data

    # ---- sign-in ------------------------------------------------------

    def begin(self, port):
        """A fresh authorize URL; its state and PKCE verifier stay here, in memory."""
        client_id = self.client_id()
        if not client_id:
            raise ValueError(f"Set {SETTING} in PixelDeck's .env first: {config.env_path()}")
        if not CLIENT_ID_RE.fullmatch(client_id):
            raise ValueError(f"{SETTING} does not look like a Spotify client ID (letters and digits, from the app's page in the Spotify dashboard).")
        verifier, state, redirect = pkce_verifier(), secrets.token_urlsafe(24), redirect_uri(port)
        with self.lock:
            now = self.clock()
            for key in [k for k, v in self.pending.items() if now - v["at"] > STATE_TTL]:
                del self.pending[key]
            self.pending[state] = {"verifier": verifier, "redirect_uri": redirect, "client_id": client_id, "at": now}
            while len(self.pending) > PENDING_MAX:
                self.pending.popitem(last=False)
        query = urllib.parse.urlencode({
            "client_id": client_id, "response_type": "code", "redirect_uri": redirect,
            "code_challenge_method": "S256", "code_challenge": pkce_challenge(verifier), "state": state, "scope": SCOPES,
        })
        return f"{AUTHORIZE_URL}?{query}"

    def take(self, state):
        """The pending sign-in for this state, once: it is removed before its age is checked."""
        if not isinstance(state, str) or not state:
            return None
        with self.lock:
            entry = self.pending.pop(state, None)
        if entry is None or self.clock() - entry["at"] > STATE_TTL:
            return None
        return entry

    def _token_request(self, form):
        body = urllib.parse.urlencode(form).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": "pixeldeck/1.0"}
        status, _headers, raw = self.transport("POST", TOKEN_URL, headers, body)
        return status, _json(raw)

    def finish(self, code, entry):
        """Trade the code from the callback for tokens, and keep them."""
        status, payload = self._token_request({
            "grant_type": "authorization_code", "code": code, "redirect_uri": entry["redirect_uri"],
            "client_id": entry["client_id"], "code_verifier": entry["verifier"],
        })
        if status != 200:
            raise SpotifyError(status, payload.get("error_description") or payload.get("error") or f"Spotify answered {status}.")
        with self.lock:
            self._keep(payload, entry["client_id"])
            self.profile, self.profile_at = None, 0

    def _access(self, stale=None):
        """A live access token. With stale, that one was refused: renew unless another thread already did."""
        with self.lock:
            tok = self._load()
            if tok is None:
                raise NotConnected(NOT_CONNECTED)
            if stale is not None and tok["access_token"] != stale:
                return tok["access_token"]
            if stale is None and tok.get("expires_at", 0) - EARLY_REFRESH > self.clock():
                return tok["access_token"]
            status, payload = self._token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": tok["client_id"]})
            if status == 200:
                return self._keep(payload, tok["client_id"], tok)["access_token"]
            if status in (400, 401) and payload.get("error") in ("invalid_grant", "invalid_client"):
                self.forget()  # revoked or expired for good: only a new sign-in helps
                raise NotConnected("Spotify ended PixelDeck's sign-in. Press Connect to sign in again.")
            raise SpotifyError(status, payload.get("error_description") or f"Spotify did not renew the sign-in ({status}).")

    # ---- the Web API ----------------------------------------------------

    def call(self, method, path, params=None, body=None):
        now = self.clock()
        if now < self.not_before:
            raise SpotifyError(429, f"Spotify asked PixelDeck to slow down. Trying again in {int(self.not_before - now) + 1} s.")
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        # an empty body on PUT and POST still sends Content-Length: 0, which Spotify wants
        data = json.dumps(body).encode() if body is not None else (b"" if method in ("PUT", "POST") else None)
        token = self._access()
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "pixeldeck/1.0"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            status, rheaders, raw = self.transport(method, url, headers, data)
            if status == 401 and attempt == 1:
                token = self._access(stale=token)  # renew once, then give up
                continue
            return self._answer(method, path, status, rheaders, raw)

    def _answer(self, method, path, status, headers, raw):
        if 200 <= status < 300:
            if status == 204 or not (raw or b"").strip():
                return None
            try:
                value = json.loads(raw)
            except ValueError:
                return None
            return value if isinstance(value, (dict, list)) else None  # /contains answers with a list
        error = _json(raw).get("error")
        error = error if isinstance(error, dict) else {"message": error} if isinstance(error, str) else {}
        message, reason = _text(error.get("message")) or "", error.get("reason")
        player = path.startswith("/me/player")
        if status == 429:
            try:
                wait = int((headers or {}).get("retry-after", "5"))
            except ValueError:
                wait = 5
            wait = min(max(wait, 1), WAIT_MAX)
            self.not_before = self.clock() + wait
            raise SpotifyError(429, f"Spotify asked PixelDeck to slow down. Trying again in {wait} s.")
        if status == 401:
            raise SpotifyError(401, "Spotify refused the sign-in. Disconnect and connect again.")
        if status == 403 and (reason == "PREMIUM_REQUIRED" or (player and method != "GET")):
            raise SpotifyError(403, PREMIUM)
        if status == 403:
            raise SpotifyError(403, f"Spotify refused this account{f': {message}' if message else ''}. In development mode only the "
                                    "app's owner and the users listed under User Management in the Spotify dashboard can use it.")
        if status == 404 and player and (reason == "NO_ACTIVE_DEVICE" or method != "GET"):
            raise SpotifyError(404, NO_DEVICE)
        raise SpotifyError(status, message or f"Spotify answered {status}.")

    def me(self):
        with self.lock:
            if self.profile and self.clock() - self.profile_at < PROFILE_TTL:
                return self.profile
        raw = self.call("GET", "/me") or {}
        product = raw.get("product") if isinstance(raw.get("product"), str) else None
        profile = {"name": _text(raw.get("display_name")) or _text(raw.get("id")) or "Spotify user",
                   "product": product, "premium": product == "premium"}
        with self.lock:
            self.profile, self.profile_at = profile, self.clock()
        return profile

    def picture(self, url):
        url = art_url(url)
        with self.art_lock:
            if url in self.art:
                self.art.move_to_end(url)
                return self.art[url]
        # a public image host: no token goes with this request
        status, headers, raw = self.transport("GET", url, {"Accept": "image/*", "User-Agent": "pixeldeck/1.0"}, None)
        ctype = (headers or {}).get("content-type", "").split(";")[0].strip().lower()
        if status != 200:
            raise SpotifyError(status, f"Spotify's image server answered {status}.")
        if ctype not in ART_TYPES or not raw or len(raw) > ART_MAX:
            raise SpotifyError(502, "Spotify's image server sent something that is not a small picture.")
        with self.art_lock:
            if url not in self.art:
                self.art[url] = (raw, ctype)
                self.art_bytes += len(raw)
            while self.art and (len(self.art) > ART_KEEP or self.art_bytes > ART_BYTES):
                self.art_bytes -= len(self.art.popitem(last=False)[1][0])
        return raw, ctype


CLIENT = Client()


# ---------------------------------------------------------------- routes

def _status(port):
    client = CLIENT
    client_id = client.client_id()
    out = {
        "configured": bool(client_id), "client_id_ok": bool(CLIENT_ID_RE.fullmatch(client_id)),
        "connected": False, "redirect_uri": redirect_uri(port), "setting": SETTING,
        "env_file": str(config.env_path()), "user": None, "error": None,
    }
    if not client.connected():
        return out
    out["connected"] = True
    out["scopes_missing"] = sorted(set(SCOPES.split()) - client.granted())
    try:
        out["user"] = client.me()
    except NotConnected:
        out["connected"] = False
    except SpotifyError as exc:
        out["error"] = exc.message
    return out


def _login(port):
    url = CLIENT.begin(port)
    try:
        opener.open_url(url)
        opened = True
    except Exception:  # noqa: BLE001 - no browser here: the page offers the link to copy instead
        opened = False
    return {"url": url, "opened": opened}


def _device_params(body):
    device = body.get("device_id")
    if device in (None, ""):
        return {}
    if not isinstance(device, str) or not DEVICE_ID_RE.fullmatch(device):
        raise ValueError("device_id is not a Spotify device id")
    return {"device_id": device}


def _bounded(value, low, high, what):
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{what} must be a whole number from {low} to {high}")
    return value


def _control(body):
    action, value = body.get("action"), body.get("value")
    params = _device_params(body)
    if action == "play":
        CLIENT.call("PUT", "/me/player/play", params)
    elif action == "pause":
        CLIENT.call("PUT", "/me/player/pause", params)
    elif action == "next":
        CLIENT.call("POST", "/me/player/next", params)
    elif action == "previous":
        CLIENT.call("POST", "/me/player/previous", params)
    elif action == "seek":
        CLIENT.call("PUT", "/me/player/seek", {**params, "position_ms": _bounded(value, 0, 24 * 3600 * 1000, "position")})
    elif action == "volume":
        CLIENT.call("PUT", "/me/player/volume", {**params, "volume_percent": _bounded(value, 0, 100, "volume")})
    elif action == "shuffle":
        if not isinstance(value, bool):
            raise ValueError("shuffle takes true or false")
        CLIENT.call("PUT", "/me/player/shuffle", {**params, "state": "true" if value else "false"})
    elif action == "repeat":
        if value not in REPEAT_MODES:
            raise ValueError("repeat takes off, context or track")
        CLIENT.call("PUT", "/me/player/repeat", {**params, "state": value})
    else:
        raise ValueError("unknown action")
    return {"ok": True}


def _now():
    if not CLIENT.connected():
        return {"connected": False}
    try:
        return trim_player(CLIENT.call("GET", "/me/player", {"additional_types": "track,episode"}))
    except NotConnected as exc:
        return {"connected": False, "error": str(exc)}


def _devices():
    raw = CLIENT.call("GET", "/me/player/devices") or {}
    devices = [trim_device(d) for d in raw.get("devices") or []]
    return {"devices": [d for d in devices if d and d["id"]][:20]}


def _transfer(body):
    device = _device_params(body).get("device_id")
    if not device:
        raise ValueError("device_id required")
    CLIENT.call("PUT", "/me/player", body={"device_ids": [device], "play": body.get("play") is True})
    return {"ok": True}


def _arg(source, key):
    value = source.get(key)
    return value[0] if isinstance(value, list) and value else value


def _need_id(source, key="id"):
    ident = _ident(_arg(source, key))
    if not ident:
        raise ValueError(f"{key} is not a Spotify id")
    return ident


def _offset(query):
    raw = _arg(query, "offset") or "0"
    if not isinstance(raw, str) or not raw.isdigit() or int(raw) > 100000:
        raise ValueError("offset must be a whole number")
    return int(raw)


def _locked(scope):
    """The answer for a list this sign-in may not read: the page offers Reconnect instead of an error."""
    return {"locked": True, "scope": scope, "message": RECONNECT, "items": [], "next": None, "total": 0}


def _gate(scope):
    if not CLIENT.connected():
        raise NotConnected(NOT_CONNECTED)
    return None if scope in CLIENT.granted() else _locked(scope)


def _page(path, params, offset, limit=PAGE):
    """One page of a Spotify list, and where the next one starts (None at the end)."""
    try:
        raw = CLIENT.call("GET", path, {**params, "limit": limit, "offset": offset}) or {}
    except SpotifyError as exc:
        if exc.status == 400 and limit > SMALL_PAGE:  # a page size this app may not ask for: ask for less
            return _page(path, params, offset, SMALL_PAGE)
        raise
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    return items, (offset + len(items) if raw.get("next") and items else None), _int(raw.get("total"), None)


def _listing(items, trim, nxt, total, **more):
    trimmed = [trim(i) for i in items]
    return {"items": [t for t in trimmed if t], "next": nxt, "total": total, **more}


def _entry(raw):
    """A saved or playlist entry wraps its song as "track" (or "item" in newer answers)."""
    return trim_item(raw.get("item") or raw.get("track")) if isinstance(raw, dict) else None


def _not_shared(call):
    try:
        return call()
    except SpotifyError as exc:
        if exc.status == 404:
            raise SpotifyError(404, NOT_SHARED) from None
        raise


def _playlists(query):
    items, nxt, total = _page("/me/playlists", {}, _offset(query))
    return _listing(items, trim_playlist, nxt, total)


def _playlist_tracks(query):
    ident, offset = _need_id(query), _offset(query)
    head = None
    if offset == 0:
        meta = _not_shared(lambda: CLIENT.call("GET", f"/playlists/{ident}", {"fields": "id,name,owner(id,display_name),images"}))
        head = trim_playlist(meta)
    # A playlist's songs answer as /items on newer API versions and /tracks on older ones:
    # try the one that worked last, and remember the other if it was the right one.
    paths = [CLIENT.items_path] if CLIENT.items_path else ["items", "tracks"]
    for i, name in enumerate(paths):
        try:
            items, nxt, total = _page(f"/playlists/{ident}/{name}", {}, offset)
            CLIENT.items_path = name
            break
        except SpotifyError as exc:
            if exc.status != 404 or i == len(paths) - 1:
                raise SpotifyError(404, NOT_SHARED) if exc.status == 404 else exc from None
    return _listing(items, _entry, nxt, total, playlist=head)


def _liked(query):
    locked = _gate(LIBRARY_READ)
    if locked:
        return locked
    items, nxt, total = _page("/me/tracks", {}, _offset(query))
    return _listing(items, _entry, nxt, total)


def _albums(query):
    locked = _gate(LIBRARY_READ)
    if locked:
        return locked
    items, nxt, total = _page("/me/albums", {}, _offset(query))
    return _listing(items, lambda i: trim_album(i.get("album")) if isinstance(i, dict) else None, nxt, total)


def _album(query):
    ident, offset = _need_id(query), _offset(query)
    raw = CLIENT.call("GET", f"/albums/{ident}") or {}
    head = trim_album(raw)
    album = {"id": raw.get("id"), "name": raw.get("name"), "images": raw.get("images")}
    if offset == 0 and isinstance(raw.get("tracks"), dict):
        page = raw["tracks"]
        items = page.get("items") if isinstance(page.get("items"), list) else []
        nxt, total = (len(items) if page.get("next") and items else None), _int(page.get("total"), None)
    else:
        items, nxt, total = _page(f"/albums/{ident}/tracks", {}, offset)
    return _listing(items, lambda t: trim_item(t, album), nxt, total, album=head)


def _recent(query):
    locked = _gate(RECENT)
    if locked:
        return locked
    before = _arg(query, "before")
    params = {"limit": PAGE}
    if before:
        if not isinstance(before, str) or not before.isdigit():
            raise ValueError("before must be a time in milliseconds")
        params["before"] = before
    raw = CLIENT.call("GET", "/me/player/recently-played", params) or {}
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    cursor = (raw.get("cursors") or {}).get("before") if isinstance(raw.get("cursors"), dict) else None
    out = _listing(items, _entry, None, None)
    out["before"] = str(cursor) if cursor and items and str(cursor).isdigit() else None
    return out


def _search(query):
    q = (_arg(query, "q") or "").strip()
    if not q or len(q) > 200:
        raise ValueError("search for 1 to 200 characters")
    kind = _arg(query, "type") or "all"
    if kind not in ("all", *SEARCH_TYPES):
        raise ValueError("type takes all, track, album, artist or playlist")
    offset = _offset(query) if kind != "all" else 0
    params = {"q": q, "type": ",".join(SEARCH_TYPES) if kind == "all" else kind, "limit": SMALL_PAGE, "offset": offset}
    raw = CLIENT.call("GET", "/search", params) or {}
    trims = {"track": trim_item, "album": trim_album, "artist": trim_artist, "playlist": trim_playlist}
    out = {}
    for name in SEARCH_TYPES if kind == "all" else (kind,):
        page = raw.get(f"{name}s") if isinstance(raw.get(f"{name}s"), dict) else {}
        items = [i for i in page.get("items") or [] if i]  # Spotify leaves holes (null) in some result pages
        nxt = offset + len(page.get("items") or []) if page.get("next") and items else None
        out[f"{name}s"] = _listing(items, trims[name], nxt, _int(page.get("total"), None))
    return out


def _artist(query):
    ident, offset = _need_id(query), _offset(query)
    head = trim_artist(CLIENT.call("GET", f"/artists/{ident}")) if offset == 0 else None
    items, nxt, total = _page(f"/artists/{ident}/albums", {"include_groups": "album,single"}, offset, SMALL_PAGE)
    return _listing(items, trim_album, nxt, total, artist=head)


def _queue():
    raw = CLIENT.call("GET", "/me/player/queue") or {}
    items = [trim_item(i) for i in raw.get("queue") or []]
    return {"current": trim_item(raw.get("currently_playing")), "items": [i for i in items if i][:50]}


def _queue_add(body):
    uri = _uri(body.get("uri"), TRACK_URI_RE)
    if not uri:
        raise ValueError("uri is not a Spotify song or episode")
    CLIENT.call("POST", "/me/player/queue", {**_device_params(body), "uri": uri})
    return {"ok": True}


def _ids(value, most=50):
    ids = value.split(",") if isinstance(value, str) else value
    if not isinstance(ids, list) or not 1 <= len(ids) <= most or not all(_ident(i) for i in ids):
        raise ValueError(f"ids takes 1 to {most} Spotify ids")
    return ids


def _liked_check(query):
    locked = _gate(LIBRARY_READ)
    if locked:
        return locked
    ids = _ids(_arg(query, "ids"))
    raw = CLIENT.call("GET", "/me/tracks/contains", {"ids": ",".join(ids)})
    answers = raw if isinstance(raw, list) else []
    return {"liked": {i: answers[n] is True for n, i in enumerate(ids) if n < len(answers)}}


def _like(body):
    locked = _gate(LIBRARY_WRITE)
    if locked:
        return locked
    ids, liked = _ids(body.get("ids")), body.get("liked")
    if not isinstance(liked, bool):
        raise ValueError("liked takes true or false")
    CLIENT.call("PUT" if liked else "DELETE", "/me/tracks", {"ids": ",".join(ids)})
    return {"ok": True, "liked": liked, "ids": ids}


def _play(body):
    """Play a playlist, album or artist (from a song in it), or a list of songs from one of them."""
    payload = {}
    context, uris, start = body.get("context"), body.get("uris"), body.get("offset_uri")
    if context is not None:
        match = CONTEXT_URI_RE.fullmatch(context) if isinstance(context, str) else None
        if not match:
            raise ValueError("context is not a Spotify playlist, album or artist")
        payload["context_uri"] = context
    elif uris is not None:
        if not isinstance(uris, list) or not 1 <= len(uris) <= 100 or not all(_uri(u, TRACK_URI_RE) for u in uris):
            raise ValueError("uris takes 1 to 100 Spotify songs")
        payload["uris"] = uris
    else:
        raise ValueError("play needs a context or uris")
    if start is not None:
        if not _uri(start, TRACK_URI_RE) or (uris is not None and start not in uris):
            raise ValueError("offset_uri is not a song in what plays")
        if not (context and match.group(1) == "artist"):  # an artist plays from its top: Spotify takes no offset there
            payload["offset"] = {"uri": start}
    CLIENT.call("PUT", "/me/player/play", _device_params(body), body=payload)
    return {"ok": True}


def _play_playlist(body):
    ident = body.get("id")
    if not isinstance(ident, str) or not PLAYLIST_ID_RE.fullmatch(ident):
        raise ValueError("id is not a Spotify playlist id")
    CLIENT.call("PUT", "/me/player/play", _device_params(body), body={"context_uri": f"spotify:playlist:{ident}"})
    return {"ok": True}


GETS = {
    "spotify-now": lambda q: _now(), "spotify-devices": lambda q: _devices(), "spotify-playlists": _playlists,
    "spotify-playlist": _playlist_tracks, "spotify-liked": _liked, "spotify-liked-check": _liked_check,
    "spotify-albums": _albums, "spotify-album": _album, "spotify-recent": _recent, "spotify-search": _search,
    "spotify-artist": _artist, "spotify-queue": lambda q: _queue(),
    "spotify-art": lambda q: CLIENT.picture(_arg(q, "url") or ""),
}
POSTS = {
    "spotify-control": _control, "spotify-transfer": _transfer, "spotify-playlist-play": _play_playlist,
    "spotify-play": _play, "spotify-queue-add": _queue_add, "spotify-like": _like,
}


def route(handler, engine, method, name, query, body):
    """Answer a route this module owns, or None. A dict or list goes out as JSON; a
    (bytes, content type) pair goes out as is."""
    if not name.startswith("spotify-"):
        return None
    query, body = query or {}, body if isinstance(body, dict) else {}
    if method == "GET" and name == "spotify-status":
        return _status(handler.port)
    if method == "POST" and name == "spotify-login":
        return _login(handler.port)
    if method == "POST" and name == "spotify-logout":
        with CLIENT.lock:
            CLIENT.pending.clear()
        CLIENT.forget()
        return _status(handler.port)
    if method == "GET" and name in GETS:
        return GETS[name](query)
    if method == "POST" and name in POSTS:
        return POSTS[name](body)
    return None


# ---------------------------------------------------------------- the callback page

# Static: no script, nothing loaded from elsewhere, every echoed word escaped.
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; font-src 'self'">
<meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PixelDeck: Spotify {word}</title>
<style>
@font-face {{ font-family: "Press Start 2P"; src: url("/static/fonts/press-start-2p.woff2") format("woff2"); }}
@font-face {{ font-family: "Pixelify Sans"; src: url("/static/fonts/pixelify-sans.woff2") format("woff2"); font-weight: 400 700; }}
:root {{ color-scheme: dark; --paper: oklch(17% 0.008 250); --paper3: oklch(13.5% 0.007 250); --rule: oklch(42% 0.012 250);
  --ink: oklch(95% 0.005 250); --muted: oklch(71% 0.01 250); --tone: {tone}; --lcd: oklch(22% 0.045 150); }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; min-height: 100%; }}
body {{ display: grid; place-items: center; min-height: 100vh; padding: 24px; background: var(--paper);
  background-image: repeating-linear-gradient(0deg, oklch(95% 0.005 250 / 0.025) 0 1px, transparent 1px 3px);
  color: var(--ink); font: 400 17px/1.55 "Pixelify Sans", system-ui, sans-serif; }}
main {{ width: min(30rem, 100%); padding: 28px; background: var(--paper3); border: 2px solid var(--rule);
  box-shadow: inset 0 0 0 2px var(--paper), inset 0 0 0 4px oklch(31% 0.01 250), 8px 8px 0 oklch(10% 0.006 250); text-align: center; }}
svg {{ display: block; margin: 0 auto 18px; }}
.lcd {{ margin: 0 0 18px; padding: 12px 10px; background: var(--lcd); border: 2px solid oklch(35% 0.06 150);
  color: var(--tone); font: 400 14px/1.4 "Press Start 2P", monospace; letter-spacing: 0.04em; text-shadow: 0 0 6px var(--tone); }}
h1 {{ margin: 0 0 10px; font-size: 22px; font-weight: 600; line-height: 1.25; }}
p {{ margin: 0; color: var(--muted); }}
</style></head>
<body><main>
<svg viewBox="0 0 22 14" width="132" height="84" shape-rendering="crispEdges" aria-hidden="true">{art}</svg>
<p class="lcd">{word}</p>
<h1>{title}</h1>
<p>{detail}</p>
</main></body></html>
"""

# A cassette in pixels: shell, label, the two reels and the tape window.
_CASSETTE = [
    ".kkkkkkkkkkkkkkkkkkkk.", "kSSSSSSSSSSSSSSSSSSSSk", "kSttttttttttttttttttSk", "kSttttttttttttttttttSk",
    "kSSSSSSSSSSSSSSSSSSSSk", "kSSkkkkSSSSSSSSkkkkSSk", "kSkwSSwkkkkkkkkwSSwkSk", "kSkwSSwkkkkkkkkwSSwkSk",
    "kSSkkkkSSSSSSSSkkkkSSk", "kSSSSSSSSSSSSSSSSSSSSk", "kSSSSSkkkkkkkkkkSSSSSk", "kSSSSkSSSSSSSSSSkSSSSk",
    "kSSSkSSSSSSSSSSSSkSSSk", ".kkkkkkkkkkkkkkkkkkkk.",
]
_FILL = {"k": "oklch(16% 0.01 250)", "S": "oklch(44% 0.01 250)", "w": "oklch(97% 0.005 250)", "t": "var(--tone)"}


def _art():
    out = []
    for y, row in enumerate(_CASSETTE):
        for x, ch in enumerate(row):
            if ch in _FILL:
                out.append(f'<rect x="{x}" y="{y}" width="1" height="1" fill="{_FILL[ch]}"/>')
    return "".join(out)


def page(ok, title, detail):
    tone = "oklch(86% 0.19 142)" if ok else "oklch(70% 0.17 25)"
    return _PAGE.format(word="CONNECTED" if ok else "NOT CONNECTED", tone=tone, art=_art(),
                        title=html.escape(title), detail=html.escape(detail))


def callback(query):
    """The page Spotify sends the browser back to after you sign in: (status, html)."""
    first = lambda key: str((query.get(key) or [""])[0])[:TEXT_MAX]  # noqa: E731
    entry = CLIENT.take(first("state"))
    if entry is None:
        return 400, page(False, "This sign-in link has expired or was already used.",
                         "Go back to PixelDeck and press Connect again: a sign-in link works once, for ten minutes.")
    error = first("error")
    if error:
        why = "You did not allow PixelDeck to use your Spotify account." if error == "access_denied" else f"Spotify said: {error}"
        return 400, page(False, why, "Go back to PixelDeck and press Connect to try again.")
    code = str((query.get("code") or [""])[0])
    if not code or len(code) > 4096:
        return 400, page(False, "Spotify sent no usable sign-in code back.", "Go back to PixelDeck and press Connect to try again.")
    try:
        CLIENT.finish(code, entry)
    except SpotifyError as exc:
        return 502, page(False, "Spotify did not finish the sign-in.", exc.message)
    try:
        name = CLIENT.me()["name"]
    except (SpotifyError, PermissionError):
        name = None
    return 200, page(True, f"Spotify is connected{f' as {name}' if name else ''}.",
                     "You can close this tab. PixelDeck's Music panel now shows what plays.")
