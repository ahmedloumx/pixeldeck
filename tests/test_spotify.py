"""Run with: python3 -m unittest discover -s tests  (from the PixelDeck folder).

Spotify is faked here: every request goes to a scripted transport that fails on anything it
was not told to expect, tokens go to a temp folder, and no browser is ever opened."""

import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env: they get settings of their own, and no token.
_ENV = Path(tempfile.mkdtemp(prefix="pixeldeck-test-")) / ".env"
_ENV.write_text("BITBUCKET_WORKSPACE=acme\nTICKET_PREFIXES=ABC,XYZ\nJIRA_SITE=https://acme.atlassian.net\n")
os.environ["PIXELDECK_ENV"] = str(_ENV)
for _key in ("BITBUCKET_TOKEN", "BITBUCKET_EMAIL", "BITBUCKET_WORKSPACE", "TICKET_PREFIXES", "JIRA_SITE", "PIXELDECK_BRIEFS_DIR",
             "SPOTIFY_CLIENT_ID"):
    os.environ.pop(_key, None)

import opener  # noqa: E402
import spotify  # noqa: E402

CID = "0123456789abcdef0123456789abcdef"
ACCESS, REFRESH = "AT-very-secret-access", "RT-very-secret-refresh"
ART = "https://i.scdn.co/image/ab67616d00004851" + "0" * 24
HANDLER = types.SimpleNamespace(port=8645)
TOKEN_URL = spotify.TOKEN_URL
API = spotify.API


def answer(status=200, payload=None, headers=None, raw=None):
    body = raw if raw is not None else (b"" if payload is None else json.dumps(payload).encode())
    return status, {"content-type": "application/json", **(headers or {})}, body


class FakeSpotify:
    """Answers queued per (method, URL prefix). The last answer repeats; an unknown URL fails the test."""

    def __init__(self):
        self.calls = []
        self.routes = []

    def on(self, method, prefix, *answers):
        self.routes.insert(0, (method, prefix, list(answers)))

    def __call__(self, method, url, headers, body):
        self.calls.append(types.SimpleNamespace(method=method, url=url, headers=dict(headers), body=body))
        for m, prefix, answers in self.routes:
            if m == method and url.startswith(prefix):
                return answers.pop(0) if len(answers) > 1 else answers[0]
        raise AssertionError(f"unexpected request {method} {url}")

    def to(self, prefix):
        return [c for c in self.calls if c.url.startswith(prefix)]


class SpotifyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pixeldeck-spotify-"))
        self.now = [1_000_000.0]
        self.fake = FakeSpotify()
        self.client = spotify.Client(transport=self.fake, store=self.tmp / "spotify.json", client_id=CID, clock=lambda: self.now[0])
        self.saved = (spotify.CLIENT, opener.open_url)
        spotify.CLIENT = self.client
        self.opened = []
        opener.open_url = lambda url: self.opened.append(url) or "test browser"

    def tearDown(self):
        spotify.CLIENT, opener.open_url = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def route(self, method, name, query=None, body=None):
        return spotify.route(HANDLER, None, method, name, query or {}, body)

    def login(self):
        url = self.route("POST", "spotify-login", body={})["url"]
        return parse_qs(urlsplit(url).query)["state"][0], url

    def connect(self, access=ACCESS, refresh=REFRESH, name="Pixel Pat", product="premium", scope=spotify.SCOPES):
        self.fake.on("POST", TOKEN_URL, answer(payload={"access_token": access, "refresh_token": refresh, "expires_in": 3600, "scope": scope}))
        self.fake.on("GET", API + "/me", answer(payload={"display_name": name, "product": product, "id": "pat"}))
        state, _url = self.login()
        status, html = spotify.callback({"state": [state], "code": ["the-code"]})
        self.assertEqual(status, 200, html)
        return html

    def stored(self):
        return json.loads((self.tmp / "spotify.json").read_text())


class SignInTest(SpotifyBase):
    def test_pkce_challenge_is_s256_of_the_verifier(self):
        # the worked example in RFC 7636, appendix B
        self.assertEqual(spotify.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        verifier = spotify.pkce_verifier()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertRegex(verifier, r"^[A-Za-z0-9._~-]+$")
        state, url = self.login()
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        pending = self.client.pending[state]
        expected = base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(q["code_challenge"], expected)
        self.assertEqual((q["code_challenge_method"], q["response_type"], q["client_id"]), ("S256", "code", CID))
        self.assertEqual(q["redirect_uri"], "http://127.0.0.1:8645/spotify/callback")
        self.assertEqual(q["scope"], spotify.SCOPES)
        self.assertNotIn(pending["verifier"], url)
        self.assertEqual(self.opened, [url])
        self.assertTrue(url.startswith("https://accounts.spotify.com/authorize?"))
        self.assertTrue(opener.allowed(url))
        self.assertEqual(self.fake.calls, [])

    def test_state_is_single_use_and_expires(self):
        self.fake.on("POST", TOKEN_URL, answer(payload={"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600}))
        self.fake.on("GET", API + "/me", answer(payload={"display_name": "Pat", "product": "premium"}))
        first, _ = self.login()
        second, _ = self.login()
        self.assertEqual(spotify.callback({"state": [first], "code": ["c1"]})[0], 200)
        status, html = spotify.callback({"state": [first], "code": ["c1"]})
        self.assertEqual(status, 400)
        self.assertIn("already used", html)
        self.now[0] += spotify.STATE_TTL + 1
        self.assertEqual(spotify.callback({"state": [second], "code": ["c2"]})[0], 400)
        self.assertNotIn(second, self.client.pending, "an expired state is gone, not kept for later")
        self.assertEqual(spotify.callback({"state": ["made-up"], "code": ["c3"]})[0], 400)
        self.assertEqual(spotify.callback({})[0], 400)
        self.assertEqual(len(self.fake.to(TOKEN_URL)), 1, "only the first, fresh state reached Spotify")

    def test_code_exchange_uses_the_verifier_and_no_secret(self):
        self.connect()
        call = self.fake.to(TOKEN_URL)[0]
        form = {k: v[0] for k, v in parse_qs(call.body.decode()).items()}
        self.assertEqual(set(form), {"grant_type", "code", "redirect_uri", "client_id", "code_verifier"})
        self.assertEqual((form["grant_type"], form["code"], form["client_id"]), ("authorization_code", "the-code", CID))
        self.assertEqual(form["redirect_uri"], "http://127.0.0.1:8645/spotify/callback")
        self.assertEqual(spotify.pkce_challenge(form["code_verifier"]), parse_qs(urlsplit(self.opened[0]).query)["code_challenge"][0])
        self.assertNotIn("Authorization", call.headers)
        self.assertEqual(call.headers["Content-Type"], "application/x-www-form-urlencoded")
        saved = self.stored()
        self.assertEqual((saved["access_token"], saved["refresh_token"], saved["client_id"]), (ACCESS, REFRESH, CID))
        if os.name != "nt":
            self.assertEqual((self.tmp / "spotify.json").stat().st_mode & 0o777, 0o600)

    def test_refresh_keeps_the_old_refresh_token_when_spotify_sends_none(self):
        self.connect()
        self.now[0] += 3600
        self.fake.on("POST", TOKEN_URL, answer(payload={"access_token": "AT-2", "expires_in": 3600}))
        self.fake.on("GET", API + "/me/player", answer(204))
        self.assertEqual(self.route("GET", "spotify-now")["active"], False)
        form = {k: v[0] for k, v in parse_qs(self.fake.to(TOKEN_URL)[-1].body.decode()).items()}
        self.assertEqual(form, {"grant_type": "refresh_token", "refresh_token": REFRESH, "client_id": CID})
        self.assertEqual(self.fake.to(API + "/me/player")[-1].headers["Authorization"], "Bearer AT-2")
        self.assertEqual((self.stored()["access_token"], self.stored()["refresh_token"]), ("AT-2", REFRESH))
        self.now[0] += 3600
        self.fake.on("POST", TOKEN_URL, answer(payload={"access_token": "AT-3", "refresh_token": "RT-2", "expires_in": 3600}))
        self.route("GET", "spotify-now")
        self.assertEqual(self.stored()["refresh_token"], "RT-2")

    def test_a_refused_refresh_disconnects(self):
        self.connect()
        self.now[0] += 3600
        self.fake.on("POST", TOKEN_URL, answer(400, {"error": "invalid_grant", "error_description": "Refresh token revoked"}))
        self.assertEqual(self.route("GET", "spotify-now")["connected"], False)
        self.assertFalse((self.tmp / "spotify.json").exists())
        with self.assertRaises(PermissionError):
            self.route("POST", "spotify-control", body={"action": "pause"})

    def test_a_new_client_id_needs_a_new_sign_in(self):
        self.connect()
        self.client._client_id = "fedcba9876543210fedcba9876543210"
        self.assertFalse(self.route("GET", "spotify-status")["connected"])

    def test_not_configured(self):
        self.client._client_id = ""
        status = self.route("GET", "spotify-status")
        self.assertEqual((status["configured"], status["connected"]), (False, False))
        self.assertEqual(status["redirect_uri"], "http://127.0.0.1:8645/spotify/callback")
        with self.assertRaises(ValueError):
            self.route("POST", "spotify-login", body={})
        self.client._client_id = "not a client id!"
        with self.assertRaises(ValueError):
            self.route("POST", "spotify-login", body={})
        self.assertEqual((self.opened, self.fake.calls), ([], []))

    def test_logout_forgets_the_tokens(self):
        self.connect()
        status = self.route("POST", "spotify-logout", body={})
        self.assertFalse(status["connected"])
        self.assertFalse((self.tmp / "spotify.json").exists())
        self.assertEqual(self.route("GET", "spotify-now"), {"connected": False})


class ApiTest(SpotifyBase):
    def test_401_renews_the_token_once(self):
        self.connect()
        self.fake.on("POST", TOKEN_URL, answer(payload={"access_token": "AT-2", "expires_in": 3600}))
        self.fake.on("GET", API + "/me/player/devices", answer(401, {"error": {"status": 401, "message": "The access token expired"}}),
                     answer(payload={"devices": [{"id": "dev1", "name": "Desk", "type": "Computer", "is_active": True, "volume_percent": 40}]}))
        self.assertEqual(self.route("GET", "spotify-devices")["devices"][0]["name"], "Desk")
        self.assertEqual(len(self.fake.to(TOKEN_URL)), 2, "the sign-in, then one renewal")
        self.fake.on("GET", API + "/me/player/devices", answer(401, {"error": {"status": 401, "message": "Invalid access token"}}))
        with self.assertRaises(spotify.SpotifyError) as caught:
            self.route("GET", "spotify-devices")
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(len(self.fake.to(TOKEN_URL)), 3, "a second refusal does not renew again")

    def test_403_on_a_player_command_asks_for_premium(self):
        self.connect(product="free")
        self.fake.on("PUT", API + "/me/player/play", answer(403, {"error": {"status": 403, "message": "Player command failed: Premium required", "reason": "PREMIUM_REQUIRED"}}))
        self.fake.on("POST", API + "/me/player/next", answer(403, {"error": {"status": 403, "message": "Forbidden"}}))
        for action in ("play", "next"):
            with self.assertRaises(spotify.SpotifyError) as caught:
                self.route("POST", "spotify-control", body={"action": action})
            self.assertEqual(caught.exception.status, 403)
            self.assertIn("needs Spotify Premium", caught.exception.message)
        self.assertFalse(self.route("GET", "spotify-status")["user"]["premium"])

    def test_404_with_no_device_says_so(self):
        self.connect()
        self.fake.on("PUT", API + "/me/player/pause", answer(404, {"error": {"status": 404, "message": "Player command failed: No active device found", "reason": "NO_ACTIVE_DEVICE"}}))
        with self.assertRaises(spotify.SpotifyError) as caught:
            self.route("POST", "spotify-control", body={"action": "pause"})
        self.assertEqual(caught.exception.message, spotify.NO_DEVICE)

    def test_429_waits_as_long_as_spotify_asks(self):
        self.connect()
        self.fake.on("GET", API + "/me/player", answer(429, headers={"retry-after": "7"}), answer(204))
        with self.assertRaises(spotify.SpotifyError) as caught:
            self.route("GET", "spotify-now")
        self.assertIn("7 s", caught.exception.message)
        sent = len(self.fake.calls)
        self.now[0] += 5
        with self.assertRaises(spotify.SpotifyError):
            self.route("GET", "spotify-now")
        self.assertEqual(len(self.fake.calls), sent, "nothing goes to Spotify before Retry-After is up")
        self.now[0] += 3
        self.assertFalse(self.route("GET", "spotify-now")["active"])

    def test_controls_are_checked_before_spotify_hears_of_them(self):
        self.connect()
        sent = len(self.fake.calls)
        for body in ({"action": "explode"}, {"action": "volume", "value": 101}, {"action": "volume", "value": True},
                     {"action": "volume", "value": 4.5}, {"action": "seek", "value": -1}, {"action": "shuffle", "value": "yes"},
                     {"action": "repeat", "value": "all"}, {"action": "play", "device_id": "../me"}, {}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.route("POST", "spotify-control", body=body)
        for body in ({"id": "spotify:playlist:x"}, {"id": ""}, {}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.route("POST", "spotify-playlist-play", body=body)
        with self.assertRaises(ValueError):
            self.route("POST", "spotify-transfer", body={"device_id": "a b"})
        self.assertEqual(len(self.fake.calls), sent)
        self.fake.on("PUT", API + "/me/player", answer(204))
        self.route("POST", "spotify-control", body={"action": "volume", "value": 40, "device_id": "dev1"})
        self.route("POST", "spotify-control", body={"action": "repeat", "value": "track"})
        self.route("POST", "spotify-playlist-play", body={"id": "37i9dQZF1DXcBWIGoYBM5M"})
        self.route("POST", "spotify-transfer", body={"device_id": "dev1", "play": True})
        urls = [c.url for c in self.fake.calls[sent:]]
        self.assertEqual(urls[:2], [API + "/me/player/volume?device_id=dev1&volume_percent=40", API + "/me/player/repeat?state=track"])
        self.assertEqual(json.loads(self.fake.calls[sent + 2].body), {"context_uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"})
        self.assertEqual(json.loads(self.fake.calls[sent + 3].body), {"device_ids": ["dev1"], "play": True})
        self.assertEqual(self.fake.calls[sent].body, b"", "a body-less PUT still sends Content-Length: 0")

    def test_now_playing_is_trimmed(self):
        self.connect()
        self.fake.on("GET", API + "/me/player", answer(payload={
            "is_playing": True, "progress_ms": 61000, "shuffle_state": True, "repeat_state": "context",
            "device": {"id": "dev1", "name": "Desk", "type": "Computer", "is_active": True, "volume_percent": 55, "supports_volume": True},
            "context": {"type": "playlist", "uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"},
            "actions": {"disallows": {"resuming": True, "made_up": True}},
            "item": {"type": "track", "id": "t1", "name": "Song", "duration_ms": 200000, "artists": [{"name": "A"}, {"name": "B"}],
                     "album": {"name": "Album", "images": [{"url": ART.replace("4851", "b273"), "width": 640}, {"url": ART, "width": 64},
                                                           {"url": "https://evil.example/image/abc", "width": 300}]}},
            "device_secret": "nope",
        }))
        now = self.route("GET", "spotify-now")
        self.assertEqual(now["item"], {"id": "t1", "uri": None, "kind": "track", "name": "Song", "artists": ["A", "B"], "artist_ids": [None, None],
                                       "album": "Album", "album_id": None, "number": None, "duration_ms": 200000, "playable": True,
                                       "art": ART.replace("4851", "b273"), "thumb": ART})
        self.assertEqual((now["is_playing"], now["progress_ms"], now["shuffle"], now["repeat"]), (True, 61000, True, "context"))
        self.assertEqual((now["device"]["volume"], now["playlist"], now["disallows"]), (55, "37i9dQZF1DXcBWIGoYBM5M", ["resuming"]))
        self.assertNotIn("device_secret", json.dumps(now))
        self.assertTrue(self.fake.to(API + "/me/player")[-1].url.endswith("additional_types=track%2Cepisode"))

    def test_tokens_never_reach_a_route_answer(self):
        html = self.connect()
        self.fake.on("GET", API + "/me/player", answer(payload={"is_playing": False, "item": {"type": "track", "name": "x"}}))
        self.fake.on("GET", API + "/me/player/devices", answer(payload={"devices": [{"id": "dev1", "name": "Desk"}]}))
        self.fake.on("GET", API + "/me/playlists", answer(payload={"items": [{"id": "p1", "name": "Mix", "tracks": {"total": 3}, "owner": {"display_name": "Pat"}}]}))
        self.fake.on("PUT", API + "/me/player", answer(204))
        self.fake.on("POST", API + "/me/player", answer(204))
        self.fake.on("GET", "https://i.scdn.co/", (200, {"content-type": "image/jpeg"}, b"\xff\xd8 picture"))
        answers = [html, self.route("GET", "spotify-status"), self.route("GET", "spotify-now"), self.route("GET", "spotify-devices"),
                   self.route("GET", "spotify-playlists"), self.route("POST", "spotify-control", body={"action": "next"}),
                   self.route("POST", "spotify-transfer", body={"device_id": "dev1"}),
                   self.route("POST", "spotify-playlist-play", body={"id": "p1"}), self.route("POST", "spotify-login", body={}),
                   self.route("GET", "spotify-art", {"url": [ART]})[0].decode("latin-1"), self.route("POST", "spotify-logout", body={})]
        for out in answers:
            text = out if isinstance(out, str) else json.dumps(out)
            self.assertNotIn(ACCESS, text)
            self.assertNotIn(REFRESH, text)
        self.assertNotIn("Authorization", self.fake.to("https://i.scdn.co/")[0].headers, "the image host never sees the token")

    def test_art_only_comes_from_spotifys_image_host(self):
        for bad in ("http://i.scdn.co/image/ab67616d0000485100", "https://i.scdn.co.evil.example/image/ab67616d00004851",
                    "https://evil.example/https://i.scdn.co/image/ab67616d00004851", "https://user@i.scdn.co/image/ab67616d00004851",
                    "https://i.scdn.co:444/image/ab67616d00004851", "https://i.scdn.co/image/ab67616d00004851?x=1",
                    "https://i.scdn.co/image/ab67616d00004851#x", "https://i.scdn.co/image/../ab67616d00004851",
                    "https://i.scdn.co/images/ab67616d00004851", "https://i.scdn.co/image/AB67616D00004851",
                    "https://i.scdn.co/image/ab67616d00004851/", "https://I.SCDN.CO/image/ab67616d00004851",
                    "https://i.scdn.co/image/ab67616d00004851\n", "https://i.scdn.co/image/", "", None, ["x"]):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                spotify.art_url(bad)
        with self.assertRaises(ValueError):
            self.route("GET", "spotify-art", {"url": ["https://evil.example/image/ab67616d00004851"]})
        self.assertEqual(self.fake.calls, [])
        self.fake.on("GET", ART, (200, {"content-type": "image/jpeg"}, b"\xff\xd8 picture"))
        self.assertEqual(self.route("GET", "spotify-art", {"url": [ART]}), (b"\xff\xd8 picture", "image/jpeg"))
        self.route("GET", "spotify-art", {"url": [ART]})
        self.assertEqual(len(self.fake.calls), 1, "the second ask comes from the cache")
        other = ART[:-1] + "1"
        self.fake.on("GET", other, (200, {"content-type": "text/html"}, b"<script>"))
        with self.assertRaises(spotify.SpotifyError):
            self.route("GET", "spotify-art", {"url": [other]})
        big = ART[:-1] + "2"
        self.fake.on("GET", big, (200, {"content-type": "image/png"}, b"x" * (spotify.ART_MAX + 1)))
        with self.assertRaises(spotify.SpotifyError):
            self.route("GET", "spotify-art", {"url": [big]})


OLD_SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private user-read-private"
T1 = {"type": "track", "id": "t1", "uri": "spotify:track:t1", "name": "One", "duration_ms": 1000, "track_number": 1,
      "artists": [{"id": "a1", "name": "Ann"}], "album": {"id": "al1", "name": "Al", "images": [{"url": ART, "width": 64}]}}


def page(items, nxt=False, total=None):
    return answer(payload={"items": items, "next": "https://api.spotify.com/next" if nxt else None, "total": total if total is not None else len(items)})


class LibraryTest(SpotifyBase):
    def test_an_old_sign_in_gets_reconnect_answers_not_errors(self):
        self.connect(scope=OLD_SCOPES)
        status = self.route("GET", "spotify-status")
        self.assertEqual(status["scopes_missing"], sorted(["user-library-read", "user-library-modify", "user-read-recently-played", "playlist-read-collaborative"]))
        sent = len(self.fake.calls)
        for method, name, query, body, scope in (("GET", "spotify-liked", {}, None, "user-library-read"),
                                                 ("GET", "spotify-albums", {}, None, "user-library-read"),
                                                 ("GET", "spotify-liked-check", {"ids": ["t1"]}, None, "user-library-read"),
                                                 ("GET", "spotify-recent", {}, None, "user-read-recently-played"),
                                                 ("POST", "spotify-like", {}, {"ids": ["t1"], "liked": True}, "user-library-modify")):
            with self.subTest(name=name):
                out = self.route(method, name, query, body)
                self.assertEqual((out["locked"], out["scope"], out["items"]), (True, scope, []))
                self.assertIn("Reconnect", out["message"])
        self.assertEqual(len(self.fake.calls), sent, "a locked list never reaches Spotify")
        self.assertEqual(set(parse_qs(urlsplit(self.login()[1]).query)["scope"][0].split()), set(spotify.SCOPES.split()))

    def test_a_renewal_without_a_scope_keeps_the_granted_one(self):
        self.connect(scope=OLD_SCOPES)
        self.now[0] += 3600
        self.fake.on("POST", TOKEN_URL, answer(payload={"access_token": "AT-2", "expires_in": 3600}))
        self.fake.on("GET", API + "/me/player", answer(204))
        self.route("GET", "spotify-now")
        self.assertEqual(self.client.granted(), set(OLD_SCOPES.split()))

    def test_liked_songs_page_and_retry_a_refused_page_size(self):
        self.connect()
        self.fake.on("GET", API + "/me/tracks?limit=50", answer(400, {"error": {"status": 400, "message": "Invalid limit"}}))
        self.fake.on("GET", API + "/me/tracks?limit=10", page([{"added_at": "x", "track": T1}, {"track": None}], nxt=True, total=12))
        out = self.route("GET", "spotify-liked")
        self.assertEqual([t["name"] for t in out["items"]], ["One"])
        self.assertEqual((out["next"], out["total"]), (2, 12))
        self.assertEqual(out["items"][0]["thumb"], ART)
        self.assertEqual(out["items"][0]["artist_ids"], ["a1"])
        with self.assertRaises(ValueError):
            self.route("GET", "spotify-liked", {"offset": ["-1"]})

    def test_playlist_songs_learn_which_path_answers(self):
        self.connect()
        self.fake.on("GET", API + "/playlists/p1?fields", answer(payload={"id": "p1", "name": "Mix", "owner": {"id": "spotify", "display_name": "Spotify"}, "images": []}))
        self.fake.on("GET", API + "/playlists/p1/items", answer(404, {"error": {"status": 404, "message": "Not found"}}))
        self.fake.on("GET", API + "/playlists/p1/tracks", page([{"track": T1}], nxt=True, total=60))
        out = self.route("GET", "spotify-playlist", {"id": ["p1"]})
        self.assertEqual((out["playlist"]["name"], out["playlist"]["by_spotify"], out["next"]), ("Mix", True, 1))
        self.assertEqual(self.client.items_path, "tracks")
        sent = len(self.fake.to(API + "/playlists/p1/items"))
        self.route("GET", "spotify-playlist", {"id": ["p1"], "offset": ["50"]})
        self.assertEqual(len(self.fake.to(API + "/playlists/p1/items")), sent, "the path that worked is used straight away")
        self.assertIn("offset=50", self.fake.to(API + "/playlists/p1/tracks")[-1].url)
        self.fake.on("GET", API + "/playlists/ed1?fields", answer(404, {"error": {"status": 404, "message": "Resource not found"}}))
        with self.assertRaises(spotify.SpotifyError) as caught:
            self.route("GET", "spotify-playlist", {"id": ["ed1"]})
        self.assertEqual((caught.exception.status, caught.exception.message), (404, spotify.NOT_SHARED))
        with self.assertRaises(ValueError):
            self.route("GET", "spotify-playlist", {"id": ["../me"]})

    def test_album_songs_carry_the_album_pictures(self):
        self.connect()
        song = {k: v for k, v in T1.items() if k != "album"}
        self.fake.on("GET", API + "/albums/al1", answer(payload={"id": "al1", "name": "Al", "release_date": "2021-04-01", "total_tracks": 51,
                                                                 "artists": [{"id": "a1", "name": "Ann"}], "images": [{"url": ART, "width": 64}],
                                                                 "tracks": {"items": [song], "next": "x", "total": 51}}))
        out = self.route("GET", "spotify-album", {"id": ["al1"]})
        self.assertEqual((out["album"]["year"], out["items"][0]["thumb"], out["items"][0]["album"], out["next"]), ("2021", ART, "Al", 1))
        self.fake.on("GET", API + "/albums/al1/tracks", page([song]))
        self.route("GET", "spotify-album", {"id": ["al1"], "offset": ["50"]})
        self.assertIn("offset=50", self.fake.to(API + "/albums/al1/tracks")[-1].url)

    def test_search_asks_once_for_every_kind_and_skips_holes(self):
        self.connect()
        self.fake.on("GET", API + "/search", answer(payload={
            "tracks": {"items": [T1], "next": "x", "total": 40}, "albums": {"items": [None], "next": None, "total": 0},
            "artists": {"items": [{"id": "a1", "name": "Ann", "images": []}], "next": None, "total": 1},
            "playlists": {"items": [None, {"id": "p1", "name": "Mix", "owner": {"display_name": "Pat"}}], "next": None, "total": 2}}))
        out = self.route("GET", "spotify-search", {"q": ["neon <b>"]})
        q = parse_qs(urlsplit(self.fake.to(API + "/search")[-1].url).query)
        self.assertEqual((q["type"][0], q["limit"][0], q["q"][0]), ("track,album,artist,playlist", "10", "neon <b>"))
        self.assertEqual((len(out["tracks"]["items"]), out["tracks"]["next"], out["albums"]["items"], out["playlists"]["items"][0]["name"]), (1, 1, [], "Mix"))
        self.route("GET", "spotify-search", {"q": ["neon"], "type": ["track"], "offset": ["10"]})
        q = parse_qs(urlsplit(self.fake.to(API + "/search")[-1].url).query)
        self.assertEqual((q["type"][0], q["offset"][0]), ("track", "10"))
        for bad in ({"q": [""]}, {"q": ["x" * 201]}, {"q": ["x"], "type": ["show"]}):
            with self.subTest(query=bad), self.assertRaises(ValueError):
                self.route("GET", "spotify-search", bad)

    def test_recent_pages_back_with_the_cursor(self):
        self.connect()
        self.fake.on("GET", API + "/me/player/recently-played", answer(payload={"items": [{"track": T1, "played_at": "x"}], "cursors": {"before": "1700000000000"}}))
        out = self.route("GET", "spotify-recent")
        self.assertEqual((out["items"][0]["id"], out["before"]), ("t1", "1700000000000"))
        self.route("GET", "spotify-recent", {"before": ["1700000000000"]})
        self.assertIn("before=1700000000000", self.fake.to(API + "/me/player/recently-played")[-1].url)
        with self.assertRaises(ValueError):
            self.route("GET", "spotify-recent", {"before": ["soon"]})

    def test_artist_and_queue(self):
        self.connect()
        self.fake.on("GET", API + "/artists/a1", answer(payload={"id": "a1", "name": "Ann", "genres": ["synth", "pop", "x", "y"], "images": []}))
        self.fake.on("GET", API + "/artists/a1/albums", page([{"id": "al1", "name": "Al"}]))
        out = self.route("GET", "spotify-artist", {"id": ["a1"]})
        self.assertEqual((out["artist"]["genres"], out["items"][0]["uri"]), (["synth", "pop", "x"], "spotify:album:al1"))
        self.assertIn("limit=10", self.fake.to(API + "/artists/a1/albums")[-1].url)
        self.fake.on("GET", API + "/me/player/queue", answer(payload={"currently_playing": T1, "queue": [T1, None]}))
        out = self.route("GET", "spotify-queue")
        self.assertEqual((out["current"]["id"], len(out["items"])), ("t1", 1))
        self.fake.on("POST", API + "/me/player/queue", answer(204))
        self.route("POST", "spotify-queue-add", body={"uri": "spotify:track:t1"})
        self.assertTrue(self.fake.to(API + "/me/player/queue")[-1].url.endswith("uri=spotify%3Atrack%3At1"))
        for bad in ("spotify:album:x", "https://open.spotify.com/track/x", "", None):
            with self.subTest(uri=bad), self.assertRaises(ValueError):
                self.route("POST", "spotify-queue-add", body={"uri": bad})

    def test_like_check_and_unlike(self):
        self.connect()
        self.fake.on("GET", API + "/me/tracks/contains", answer(payload=[True, False]))
        self.assertEqual(self.route("GET", "spotify-liked-check", {"ids": ["t1,t2"]})["liked"], {"t1": True, "t2": False})
        self.fake.on("PUT", API + "/me/tracks", answer(200))
        self.fake.on("DELETE", API + "/me/tracks", answer(200))
        self.route("POST", "spotify-like", body={"ids": ["t1"], "liked": True})
        self.route("POST", "spotify-like", body={"ids": ["t1", "t2"], "liked": False})
        self.assertEqual([(c.method, c.url) for c in self.fake.to(API + "/me/tracks?")][-2:],
                         [("PUT", API + "/me/tracks?ids=t1"), ("DELETE", API + "/me/tracks?ids=t1%2Ct2")])
        for body in ({"ids": [], "liked": True}, {"ids": ["t1"] * 51, "liked": True}, {"ids": ["a/b"], "liked": True}, {"ids": ["t1"], "liked": "yes"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.route("POST", "spotify-like", body=body)

    def test_play_from_a_song_in_a_list(self):
        self.connect()
        self.fake.on("PUT", API + "/me/player/play", answer(204))
        self.route("POST", "spotify-play", body={"context": "spotify:playlist:p1", "offset_uri": "spotify:track:t1", "device_id": "dev1"})
        self.route("POST", "spotify-play", body={"uris": ["spotify:track:t1", "spotify:track:t2"], "offset_uri": "spotify:track:t2"})
        self.route("POST", "spotify-play", body={"context": "spotify:artist:a1", "offset_uri": "spotify:track:t1"})
        calls = self.fake.to(API + "/me/player/play")[-3:]
        self.assertTrue(calls[0].url.endswith("?device_id=dev1"))
        self.assertEqual([json.loads(c.body) for c in calls], [
            {"context_uri": "spotify:playlist:p1", "offset": {"uri": "spotify:track:t1"}},
            {"uris": ["spotify:track:t1", "spotify:track:t2"], "offset": {"uri": "spotify:track:t2"}},
            {"context_uri": "spotify:artist:a1"}])
        for body in ({}, {"context": "spotify:user:me:collection"}, {"uris": []}, {"uris": ["spotify:album:x"]},
                     {"uris": ["spotify:track:t1"], "offset_uri": "spotify:track:t9"}, {"context": "spotify:album:a", "offset_uri": "x"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.route("POST", "spotify-play", body=body)

    def test_the_art_cache_is_capped_by_bytes(self):
        self.client.art_bytes = 0
        saved = spotify.ART_BYTES
        spotify.ART_BYTES = 25
        try:
            for n in range(4):
                url = ART[:-1] + str(n)
                self.fake.on("GET", url, (200, {"content-type": "image/png"}, b"x" * 10))
                self.route("GET", "spotify-art", {"url": [url]})
            self.assertEqual((len(self.client.art), self.client.art_bytes), (2, 20))
        finally:
            spotify.ART_BYTES = saved


class CallbackTest(SpotifyBase):
    def test_the_callback_page_escapes_what_it_echoes(self):
        hostile = '<script>alert(1)</script><img src=x onerror=alert(2)>'
        state, _ = self.login()
        status, html = spotify.callback({"state": [state], "error": [hostile]})
        self.assertEqual(status, 400)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<img", html)
        html = self.connect(name=hostile)
        self.assertIn("&lt;img src=x onerror=alert(2)&gt;", html)
        for page in (html, spotify.callback({"state": ["x"], "code": [hostile]})[1]):
            self.assertNotIn("<script", page.lower())
            self.assertNotIn("onerror=", page.replace("onerror=alert(2)&gt;", ""))
            self.assertIn("default-src 'none'", page)

    def test_the_callback_never_echoes_the_code_or_state(self):
        state, _ = self.login()
        self.fake.on("POST", TOKEN_URL, answer(400, {"error": "invalid_grant", "error_description": "Invalid authorization code"}))
        status, html = spotify.callback({"state": [state], "code": ["secret-code-123"]})
        self.assertEqual(status, 502)
        self.assertIn("Invalid authorization code", html)
        self.assertNotIn("secret-code-123", html)
        self.assertNotIn(state, html)
        self.assertFalse((self.tmp / "spotify.json").exists())


class OpenerTest(unittest.TestCase):
    def test_spotify_links_that_may_open(self):
        for url in ("https://accounts.spotify.com/authorize?client_id=x&state=y", "https://developer.spotify.com/dashboard",
                    "https://bitbucket.org/acme/shop-api"):
            self.assertTrue(opener.allowed(url), url)
        for url in ("https://accounts.spotify.com/api/token", "https://accounts.spotify.com/", "http://accounts.spotify.com/authorize",
                    "https://accounts.spotify.com:8443/authorize", "https://me@accounts.spotify.com/authorize",
                    "https://accounts.spotify.com.evil.example/authorize", "https://accounts.spotify.com/authorize/../login",
                    "https://open.spotify.com/", "http://developer.spotify.com/dashboard", "javascript:alert(1)"):
            self.assertFalse(opener.allowed(url), url)
            with self.assertRaises(ValueError):
                opener.open_url(url)


class ThroughTheServerTest(unittest.TestCase):
    """The same rules seen from the page: status codes and one error shape."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        import server
        cls.server_mod = server
        cls.tmp = Path(tempfile.mkdtemp(prefix="pixeldeck-spotify-routes-"))
        cls.fake = FakeSpotify()
        cls.saved = (spotify.CLIENT, server.Desk.engine, server.Desk.port)
        spotify.CLIENT = spotify.Client(transport=cls.fake, store=cls.tmp / "spotify.json", client_id=CID)
        server.Desk.engine = type("E", (), {"auth": {}, "me": lambda self: {"uuid": "{me}"}})()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Desk)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        server.Desk.port = cls.port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        spotify.CLIENT, cls.server_mod.Desk.engine, cls.server_mod.Desk.port = cls.saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, token=True):
        import urllib.error
        import urllib.request
        headers = {"Content-Type": "application/json", **({"X-Desk-Token": self.server_mod.SECRET} if token else {})}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                return res.status, res.headers.get("Content-Type"), res.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.headers.get("Content-Type"), exc.read()

    def test_routes_answer_with_the_desk_error_shape(self):
        self.assertEqual(self.call("GET", "/api/spotify-status", token=False)[0], 401)
        code, _, raw = self.call("GET", "/api/spotify-status")
        status = json.loads(raw)
        self.assertEqual((code, status["configured"], status["connected"]), (200, True, False))
        self.assertEqual(status["redirect_uri"], f"http://127.0.0.1:{self.port}/spotify/callback")
        code, _, raw = self.call("GET", "/api/spotify-art?url=https%3A%2F%2Fevil.example%2Fimage%2Fab67616d00004851")
        self.assertEqual((code, set(json.loads(raw))), (400, {"error"}))
        code, _, raw = self.call("POST", "/api/spotify-control", {"action": "play"})
        self.assertEqual((code, json.loads(raw)["error"]), (403, spotify.NOT_CONNECTED))
        self.assertEqual(json.loads(self.call("GET", "/api/spotify-now")[2]), {"connected": False})
        self.assertEqual(self.call("GET", "/api/spotify-nothing")[0], 404)
        self.fake.on("GET", ART, (200, {"content-type": "image/png"}, b"\x89PNG picture"))
        code, ctype, raw = self.call("GET", "/api/spotify-art?url=" + ART)
        self.assertEqual((code, ctype, raw), (200, "image/png", b"\x89PNG picture"))

    def test_a_premium_refusal_is_a_bad_gateway_with_the_message(self):
        (self.tmp / "spotify.json").write_text(json.dumps({"client_id": CID, "access_token": ACCESS, "refresh_token": REFRESH, "expires_at": 9e12}))
        self.fake.on("PUT", API + "/me/player/pause", answer(403, {"error": {"status": 403, "message": "Premium required", "reason": "PREMIUM_REQUIRED"}}))
        code, _, raw = self.call("POST", "/api/spotify-control", {"action": "pause"})
        self.assertEqual((code, json.loads(raw)), (502, {"error": spotify.PREMIUM, "upstream": 403}))
        (self.tmp / "spotify.json").unlink()

    def test_the_callback_needs_no_page_token_and_runs_no_script(self):
        code, ctype, raw = self.call("GET", "/spotify/callback?state=nope&error=%3Cb%3E", token=False)
        self.assertEqual((code, ctype), (400, "text/html; charset=utf-8"))
        self.assertNotIn(b"<script", raw)
        self.assertIn(b"expired or was already used", raw)


if __name__ == "__main__":
    unittest.main()
