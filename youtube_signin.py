"""Sign in to YouTube with Google: your own playlists, liked videos and subscriptions.

Google's OAuth for installed apps: the Authorization Code flow with PKCE, the system browser
going to Google and coming back to /youtube/callback on this server. The OAuth client is one you
make yourself, of type Desktop app, in the Google Cloud project that has YouTube Data API v3 on.
Google treats a Desktop app's secret as not confidential, yet its token endpoint takes it, so it
is kept like the API key: the credential store when the system has one, else the settings file.
The tokens go to the state folder at mode 600, as Spotify's do, and never to the page. One
scope, read only: youtube.readonly.

While that project's OAuth consent screen is in Testing, Google ends a sign-in after 7 days.
The setup guide says to publish it (In production) so a sign-in lasts.
"""

import hashlib
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config
import credentials
import oauth
import opener
import version

FEATURES = ["youtube-signin", "youtube-client-file"]

SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNEL_URL = "https://www.googleapis.com/youtube/v3/channels"
ID_SETTING = "YOUTUBE_CLIENT_ID"
SECRET_SETTING = "YOUTUBE_CLIENT_SECRET"
TIMEOUT = 10
READ_MAX = 64 * 1024
TEXT_MAX = 300
EARLY_REFRESH = 60          # renew a little before Google's hour is up

# "<project number>-<letters and digits>.apps.googleusercontent.com"
CLIENT_ID_RE = re.compile(r"[0-9]{4,20}-[0-9a-z]{16,64}\.apps\.googleusercontent\.com")
# GOCSPX-... today; older secrets had no prefix
SECRET_RE = re.compile(r"[0-9A-Za-z_-]{16,64}")
# The file the Google Cloud console downloads for a client, named after its client ID.
CLIENT_FILE_RE = re.compile(r"client_secret_[0-9A-Za-z_.-]{1,200}\.apps\.googleusercontent\.com\.json")

NOT_SIGNED_IN = "Sign in with Google first: Settings, Connected apps, YouTube."
ENDED = ("Google ended PixelDeck's sign-in: sign in again from Settings, Connected apps, YouTube. If this happens every "
         "week, publish the app on the project's OAuth consent screen (In production): in Testing a sign-in lasts 7 days.")


class SignInError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class NotSignedIn(PermissionError):
    """Something that needs your account, with nobody signed in."""


# ---------------------------------------------------------------- the client you made

def read_client(text, secret=""):
    """(client id, secret) from what was pasted: the downloaded JSON file's text, or the client
    ID with its secret apart. A ValueError says what is wrong, never echoing what was pasted."""
    text = text.strip() if isinstance(text, str) else ""
    secret = secret.strip() if isinstance(secret, str) else ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("That JSON could not be read. Paste the whole downloaded file, from { to }.") from None
        if isinstance(data, dict) and "web" in data and "installed" not in data:
            raise ValueError("That is a Web application client. Create an OAuth client of type Desktop app: "
                             "PixelDeck signs in from this computer.")
        inner = data.get("installed") if isinstance(data, dict) else None
        if not isinstance(inner, dict):
            raise ValueError('That JSON is not an OAuth client file: it has no "installed" part.')
        text = inner.get("client_id") if isinstance(inner.get("client_id"), str) else ""
        secret = inner.get("client_secret") if isinstance(inner.get("client_secret"), str) else ""
    if not CLIENT_ID_RE.fullmatch(text):
        if text.startswith("AIza"):
            raise ValueError("That is an API key: it goes in the API key field. Here goes the OAuth client (Desktop app).")
        raise ValueError("A Google OAuth client ID ends with .apps.googleusercontent.com. Copy it from the Google Cloud "
                         "console, Credentials, or paste the downloaded JSON file.")
    if secret and not SECRET_RE.fullmatch(secret):
        raise ValueError("That client secret does not look like one: it starts with GOCSPX-.")
    return text, secret


def downloads():
    """Where a browser saves the client file; PIXELDECK_DOWNLOADS_DIR moves it (tests use their own)."""
    override = config.get("PIXELDECK_DOWNLOADS_DIR")
    return Path(override).expanduser() if override else Path.home() / "Downloads"


def client_files():
    """The client files in Downloads, by name only, newest first. Listing them opens none."""
    try:
        entries = list(os.scandir(downloads()))
    except OSError:
        return []
    found = []
    for entry in entries:
        try:
            if CLIENT_FILE_RE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                found.append((entry.stat(follow_symlinks=False).st_mtime, entry.name))
        except OSError:
            continue
    return [name for _at, name in sorted(found, reverse=True)][:5]


def read_client_file(name):
    """The text of one file client_files() lists, read now that you chose it, as if pasted. A link,
    a folder or any other name is refused, and the file itself is never changed."""
    if not isinstance(name, str) or name not in client_files():
        raise ValueError("That file is no longer in Downloads: choose the client file instead.")
    fd = os.open(downloads() / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as fh:
        raw = fh.read(READ_MAX + 1)
    try:
        if len(raw) > READ_MAX:
            raise ValueError
        return raw.decode("utf-8")
    except ValueError:
        raise ValueError("That file is not an OAuth client file.") from None


def redirect_uri(port):
    # Google lets an installed app come back to a loopback IP literal on any port
    return f"http://127.0.0.1:{int(port)}/youtube/callback"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = []


def urllib_transport(method, url, headers, body):
    """One HTTP exchange: (status, headers, body). Its errors never carry the URL or the form."""
    if not _opener:
        _opener.append(urllib.request.build_opener(_NoRedirect))
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _opener[0].open(request, timeout=TIMEOUT) as res:
            return res.status, {k.lower(): v for k, v in res.headers.items()}, res.read(READ_MAX + 1)
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read(READ_MAX + 1) if exc.fp else b""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else type(exc).__name__
        raise SignInError(0, f"Google could not be reached: {reason}") from None


def _json(raw):
    try:
        value = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------- the account

class Account:
    """Your Google sign-in for YouTube. The transport, token file and clock can be swapped in
    tests, so nothing there talks to Google or touches your real state folder."""

    def __init__(self, transport=None, store=None, clock=time.time):
        self.transport = transport or urllib_transport
        self._store = Path(store) if store else None
        self.clock = clock
        self.lock = threading.RLock()
        self.pending = oauth.Pending(clock=lambda: self.clock())

    def client(self):
        cid = (config.get(ID_SETTING) or "").strip()
        secret = (config.get(SECRET_SETTING) or credentials.store().get(SECRET_SETTING) or "").strip()
        return cid, secret

    def store(self):
        return self._store or config.folder("state") / "youtube.json"

    def _load(self):
        try:
            data = json.loads(self.store().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("refresh_token"), str) or not isinstance(data.get("access_token"), str):
            return None
        # tokens belong to the client that earned them: another client needs a new sign-in
        return data if data.get("client_id") == self.client()[0] else None

    def signed_in(self):
        return self._load() is not None

    def fingerprint(self):
        """Who is signed in, in a few letters that reveal nothing: answers kept for one account
        are never served to another."""
        tok = self._load()
        return hashlib.sha256(tok["refresh_token"].encode()).hexdigest()[:16] if tok else ""

    def status(self):
        """What the page may know: never a token, an ID or a secret."""
        cid, secret = self.client()
        tok = self._load()
        return {"client_set": bool(cid), "client_ok": bool(CLIENT_ID_RE.fullmatch(cid)), "secret_set": bool(secret),
                "signed_in": tok is not None, "channel": tok.get("channel") if tok else None}

    def save_client(self, body):
        """Save the OAuth client from the settings, or remove it (and the sign-in) with "clear"."""
        store = credentials.store()
        if body.get("clear"):
            self.forget(revoke=True)
            config.save({ID_SETTING: "", SECRET_SETTING: ""})
            store.delete(SECRET_SETTING)
            return self.status()
        cid, secret = read_client(body.get("client"), body.get("secret"))
        if cid != self.client()[0]:
            self.forget(revoke=True)
        if secret and store.name:
            store.set(SECRET_SETTING, secret)
            config.save({ID_SETTING: cid, SECRET_SETTING: ""})  # off the file, so the store's value is the one read
        else:
            if not secret:
                store.delete(SECRET_SETTING)
            config.save({ID_SETTING: cid, SECRET_SETTING: secret})
        return self.status()

    def forget(self, revoke=False):
        """Sign out. With revoke, Google is told to end the grant too, if it can be reached."""
        with self.lock:
            tok = self._load()
            try:
                self.store().unlink()
            except FileNotFoundError:
                pass
        if revoke and tok:
            try:
                self.transport("POST", REVOKE_URL, {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": version.USER_AGENT},
                               urllib.parse.urlencode({"token": tok["refresh_token"]}).encode())
            except SignInError:
                pass  # signed out here all the same; the grant ends by itself when unused

    # ---- sign-in ------------------------------------------------------

    def begin(self, port):
        """A fresh link to Google's sign-in; its state and PKCE verifier stay here, in memory."""
        cid, _secret = self.client()
        if not cid:
            raise ValueError("Save your OAuth client (Desktop app) in Settings, Connected apps, YouTube first.")
        if not CLIENT_ID_RE.fullmatch(cid):
            raise ValueError(f"{ID_SETTING} does not look like a Google OAuth client ID (it ends with .apps.googleusercontent.com).")
        verifier, redirect = oauth.pkce_verifier(), redirect_uri(port)
        state = self.pending.add({"verifier": verifier, "redirect_uri": redirect, "client_id": cid})
        return f"{AUTHORIZE_URL}?" + urllib.parse.urlencode({
            "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE, "state": state,
            "code_challenge": oauth.pkce_challenge(verifier), "code_challenge_method": "S256",
            # offline: a refresh token, so the sign-in outlives the hour; consent: a new one each time
            "access_type": "offline", "prompt": "consent",
        })

    def _token_request(self, form):
        _cid, secret = self.client()
        if secret:
            form = {**form, "client_secret": secret}
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": version.USER_AGENT}
        status, _headers, raw = self.transport("POST", TOKEN_URL, headers, urllib.parse.urlencode(form).encode())
        return status, _json(raw)

    def _keep(self, payload, client_id, previous=None):
        access = payload.get("access_token")
        refresh = payload.get("refresh_token") or (previous or {}).get("refresh_token")
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise SignInError(502, "Google's answer carried no usable token.")
        expires = payload.get("expires_in")
        expires = expires if isinstance(expires, int) and not isinstance(expires, bool) and expires > 0 else 3600
        data = {"client_id": client_id, "access_token": access, "refresh_token": refresh,
                "expires_at": self.clock() + expires, "channel": (previous or {}).get("channel")}
        oauth.write_private(self.store(), data)
        return data

    def finish(self, code, entry):
        """Trade the code from the callback for tokens, keep them, and learn the channel's name."""
        status, payload = self._token_request({
            "grant_type": "authorization_code", "code": code, "redirect_uri": entry["redirect_uri"],
            "client_id": entry["client_id"], "code_verifier": entry["verifier"],
        })
        if status != 200:
            raise SignInError(status, _said(payload, status))
        granted = payload.get("scope")
        if isinstance(granted, str) and SCOPE not in granted.split():
            raise SignInError(403, "Google did not grant read access to YouTube. Sign in again and allow it.")
        with self.lock:
            tok = self._keep(payload, entry["client_id"])
            tok["channel"] = self._channel(tok["access_token"])
            oauth.write_private(self.store(), tok)
        return tok["channel"]

    def _channel(self, access):
        """The signed-in channel's name, for the settings to show; None if it cannot be had."""
        url = f"{CHANNEL_URL}?" + urllib.parse.urlencode({"part": "snippet", "mine": "true"})
        try:
            status, _h, raw = self.transport("GET", url, {"Authorization": f"Bearer {access}", "Accept": "application/json",
                                                          "User-Agent": version.USER_AGENT}, None)
        except SignInError:
            return None
        items = _json(raw).get("items") if status == 200 else None
        snip = items[0].get("snippet") if isinstance(items, list) and items and isinstance(items[0], dict) else None
        title = snip.get("title") if isinstance(snip, dict) else None
        return html.unescape(title)[:TEXT_MAX] if isinstance(title, str) and title.strip() else None

    def access(self, stale=None):
        """A live access token. With stale, that one was refused: renew, unless another thread
        already did."""
        with self.lock:
            tok = self._load()
            if tok is None:
                raise NotSignedIn(NOT_SIGNED_IN)
            if stale is not None and tok["access_token"] != stale:
                return tok["access_token"]
            if stale is None and tok.get("expires_at", 0) - EARLY_REFRESH > self.clock():
                return tok["access_token"]
            status, payload = self._token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                                   "client_id": tok["client_id"]})
            if status == 200:
                return self._keep(payload, tok["client_id"], tok)["access_token"]
            if status in (400, 401) and payload.get("error") in ("invalid_grant", "invalid_client", "unauthorized_client"):
                self.forget()  # revoked, expired for good, or the client is gone: only a new sign-in helps
                raise NotSignedIn(ENDED)
            raise SignInError(status, _said(payload, status))


def _said(payload, status):
    words = payload.get("error_description") or payload.get("error")
    return f"Google answered {status}{f': {words}' if isinstance(words, str) and words else ''}"[:TEXT_MAX]


ACCOUNT = Account()


# ---------------------------------------------------------------- routes and the page Google sends back to

def _signin(port):
    url = ACCOUNT.begin(port)
    try:
        opened = bool(opener.open_url(url))
    except Exception:  # noqa: BLE001 - no browser here: the page offers the link instead
        opened = False
    return {"url": url, "opened": opened}


def route(handler, method, name, body):
    """Answer a route this module owns, or None."""
    body = body if isinstance(body, dict) else {}
    if method == "POST" and name == "youtube-client":
        return ACCOUNT.save_client(body)
    if method == "GET" and name == "youtube-client-files":
        return {"files": [] if ACCOUNT.client()[0] else client_files()}
    if method == "POST" and name == "youtube-client-file":
        return ACCOUNT.save_client({"client": read_client_file(body.get("name"))})
    if method == "POST" and name == "youtube-signin":
        return _signin(handler.port)
    if method == "POST" and name == "youtube-signout":
        ACCOUNT.forget(revoke=True)
        return ACCOUNT.status()
    return None


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; font-src 'self'">
<meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PixelDeck: YouTube {word}</title>
<style>
@font-face {{ font-family: "Press Start 2P"; src: url("/static/fonts/press-start-2p.woff2") format("woff2"); }}
@font-face {{ font-family: "Pixelify Sans"; src: url("/static/fonts/pixelify-sans.woff2") format("woff2"); font-weight: 400 700; }}
:root {{ color-scheme: dark; --paper: oklch(17% 0.008 250); --paper3: oklch(13.5% 0.007 250); --rule: oklch(42% 0.012 250);
  --ink: oklch(95% 0.005 250); --muted: oklch(71% 0.01 250); --tone: {tone}; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; min-height: 100%; }}
body {{ display: grid; place-items: center; min-height: 100vh; padding: 24px; background: var(--paper);
  color: var(--ink); font: 400 17px/1.55 "Pixelify Sans", system-ui, sans-serif; }}
main {{ width: min(30rem, 100%); padding: 28px; background: var(--paper3); border: 2px solid var(--rule);
  box-shadow: 8px 8px 0 oklch(10% 0.006 250); text-align: center; }}
.lcd {{ margin: 0 0 18px; padding: 12px 10px; background: oklch(20% 0.04 25); border: 2px solid oklch(40% 0.12 25);
  color: var(--tone); font: 400 14px/1.4 "Press Start 2P", monospace; letter-spacing: 0.04em; }}
h1 {{ margin: 0 0 10px; font-size: 22px; font-weight: 600; line-height: 1.25; }}
p {{ margin: 0; color: var(--muted); }}
</style></head>
<body><main><p class="lcd">{word}</p><h1>{title}</h1><p>{detail}</p></main></body></html>
"""


def page(ok, title, detail):
    return _PAGE.format(word="SIGNED IN" if ok else "NOT SIGNED IN", tone="oklch(86% 0.19 142)" if ok else "oklch(75% 0.17 25)",
                        title=html.escape(title), detail=html.escape(detail))


def callback(query):
    """The page Google sends the browser back to after you sign in: (status, html)."""
    first = lambda key: str((query.get(key) or [""])[0])[:TEXT_MAX]  # noqa: E731
    entry = ACCOUNT.pending.take(first("state"))
    if entry is None:
        return 400, page(False, "This sign-in link has expired or was already used.",
                         "Go back to PixelDeck and press Sign in with Google again: a sign-in link works once, for ten minutes.")
    error = first("error")
    if error:
        why = "You did not allow PixelDeck to read your YouTube account." if error == "access_denied" else f"Google said: {error}"
        return 400, page(False, why, "Go back to PixelDeck and press Sign in with Google to try again.")
    code = str((query.get("code") or [""])[0])
    if not code or len(code) > 4096:
        return 400, page(False, "Google sent no usable sign-in code back.", "Go back to PixelDeck and press Sign in with Google to try again.")
    try:
        channel = ACCOUNT.finish(code, entry)
    except SignInError as exc:
        return 502, page(False, "Google did not finish the sign-in.", exc.message)
    return 200, page(True, f"YouTube is signed in{f' as {channel}' if channel else ''}.",
                     "You can close this tab. Your playlists, liked videos and subscriptions are in PixelDeck's Music panel.")
