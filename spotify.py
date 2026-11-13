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
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private user-read-private"
SETTING = "SPOTIFY_CLIENT_ID"

STATE_TTL = 600        # a sign-in link is good for ten minutes, once
PENDING_MAX = 8        # sign-ins started and not finished; the oldest is dropped first
PROFILE_TTL = 600      # the display name and plan change rarely
EARLY_REFRESH = 60     # renew a little before Spotify's hour is up
TIMEOUT = 10
READ_MAX = 2 * 1024 * 1024
ART_MAX = 512 * 1024
ART_KEEP = 48
WAIT_MAX = 3600        # a Retry-After beyond this is treated as an hour

# Album art lives on Spotify's image host under a hex id. Anything else is refused, so this
# route can never be pointed at another host, port, path or query.
ART_RE = re.compile(r"https://i\.scdn\.co/image/[0-9a-f]{16,64}")
ART_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
CLIENT_ID_RE = re.compile(r"[0-9A-Za-z]{16,64}")
PLAYLIST_ID_RE = re.compile(r"[0-9A-Za-z]{1,64}")
DEVICE_ID_RE = re.compile(r"[0-9A-Za-z_-]{1,128}")
REPEAT_MODES = ("off", "context", "track")
DISALLOWS = ("pausing", "resuming", "seeking", "skipping_next", "skipping_prev", "toggling_repeat_context",
             "toggling_repeat_track", "toggling_shuffle", "transferring_playback")
TEXT_MAX = 300

PREMIUM = "Playback control needs Spotify Premium: play, pause, skip, seek, volume and devices are Premium only."
NOT_CONNECTED = "Spotify is not connected. Press Connect in the Music panel."
NO_DEVICE = "No active Spotify device. Open Spotify on a phone or computer, or pick a device in the list."


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

def pick_art(images):
    """The smallest picture at least 64 px wide: the page draws it 32 pixels across."""
    good = [i for i in images or [] if isinstance(i, dict) and isinstance(i.get("url"), str) and ART_RE.fullmatch(i["url"])]
    if not good:
        return None
    good.sort(key=lambda i: _int(i.get("width")))
    return next((i["url"] for i in good if _int(i.get("width")) >= 64), good[-1]["url"])


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


def trim_item(raw):
    if not isinstance(raw, dict):
        return None
    if raw.get("type") == "episode":
        show = raw.get("show") if isinstance(raw.get("show"), dict) else {}
        artists = [_text(show.get("publisher")) or _text(show.get("name"))]
        album, images = _text(show.get("name")), raw.get("images") or show.get("images")
    else:
        artists = [_text(a.get("name")) for a in raw.get("artists") or [] if isinstance(a, dict)]
        album_raw = raw.get("album") if isinstance(raw.get("album"), dict) else {}
        album, images = _text(album_raw.get("name")), album_raw.get("images")
    return {
        "id": _text(raw.get("id")), "kind": "episode" if raw.get("type") == "episode" else "track",
        "name": _text(raw.get("name")) or "Untitled", "artists": [a for a in artists if a][:6],
        "album": album, "duration_ms": max(0, _int(raw.get("duration_ms"))), "art": pick_art(images),
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
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not PLAYLIST_ID_RE.fullmatch(raw["id"]):
        return None
    owner = raw.get("owner") if isinstance(raw.get("owner"), dict) else {}
    tracks = raw.get("tracks") if isinstance(raw.get("tracks"), dict) else raw.get("items") if isinstance(raw.get("items"), dict) else {}
    return {"id": raw["id"], "name": _text(raw.get("name")) or "Untitled", "owner": _text(owner.get("display_name")),
            "tracks": _int(tracks.get("total"), None)}


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
        self.art_lock = threading.Lock()

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
            return None if status == 204 or not (raw or b"").strip() else (_json(raw) or None)
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
            self.art[url] = (raw, ctype)
            while len(self.art) > ART_KEEP:
                self.art.popitem(last=False)
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


def _playlists():
    raw = CLIENT.call("GET", "/me/playlists", {"limit": 30}) or {}
    lists = [trim_playlist(p) for p in raw.get("items") or []]
    return {"playlists": [p for p in lists if p]}


def _play_playlist(body):
    ident = body.get("id")
    if not isinstance(ident, str) or not PLAYLIST_ID_RE.fullmatch(ident):
        raise ValueError("id is not a Spotify playlist id")
    CLIENT.call("PUT", "/me/player/play", _device_params(body), body={"context_uri": f"spotify:playlist:{ident}"})
    return {"ok": True}


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
    if method == "GET" and name == "spotify-now":
        return _now()
    if method == "POST" and name == "spotify-control":
        return _control(body)
    if method == "GET" and name == "spotify-devices":
        return _devices()
    if method == "POST" and name == "spotify-transfer":
        return _transfer(body)
    if method == "GET" and name == "spotify-playlists":
        return _playlists()
    if method == "POST" and name == "spotify-playlist-play":
        return _play_playlist(body)
    if method == "GET" and name == "spotify-art":
        return CLIENT.picture((query.get("url") or [""])[0])
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
