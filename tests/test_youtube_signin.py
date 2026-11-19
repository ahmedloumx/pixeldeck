"""Run with: python3 -m unittest discover -s tests  (from the PixelDeck folder).

The Google sign-in for YouTube. Google is faked: every request goes to a scripted transport
that fails on anything it was not told to expect. The OAuth client and the tokens live in the
test's own settings file and state folder, and the fake credentials are put together at run
time, so no file holds anything shaped like a real one."""

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_ENV = Path(tempfile.mkdtemp(prefix="pixeldeck-test-")) / ".env"
os.environ.setdefault("PIXELDECK_ENV", str(_ENV))

import oauth  # noqa: E402
import youtube  # noqa: E402
import youtube_signin  # noqa: E402

CLIENT_ID = "-".join(["123456789012", "a1" * 16]) + ".apps.googleusercontent" + ".com"
SECRET = "GOCSPX" + "-" + "s" * 28
KEY = "AIza" + "Sy0123456789abcdefghijklmnopqrstu_-"
HANDLER = types.SimpleNamespace(port=8644)
REDIRECT = "http://127.0.0.1:8644/youtube/callback"


def answer(status=200, payload=None):
    return status, {"content-type": "application/json"}, b"" if payload is None else json.dumps(payload).encode()


def tokens(access="ya29.first", refresh="1//refresh-one", expires=3599, scope=youtube_signin.SCOPE):
    return answer(200, {"access_token": access, "refresh_token": refresh, "expires_in": expires, "scope": scope, "token_type": "Bearer"})


class FakeGoogle:
    """Answers queued per (method, URL prefix). The last answer repeats; an unknown URL fails the test."""

    def __init__(self):
        self.calls, self.routes = [], []

    def on(self, method, prefix, *answers):
        self.routes.insert(0, (method, prefix, list(answers)))

    def __call__(self, method, url, headers, body):
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()} if body else {}
        self.calls.append(types.SimpleNamespace(method=method, url=url, headers=dict(headers), form=form))
        for m, prefix, answers in self.routes:
            if m == method and url.startswith(prefix):
                return answers.pop(0) if len(answers) > 1 else answers[0]
        raise AssertionError(f"unexpected request {method} {url}")

    def to(self, prefix):
        return [c for c in self.calls if c.url.startswith(prefix)]


class FakeStore:
    name = "Test credential store"

    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)

    def set(self, key, value):
        self.items[key] = value

    def delete(self, key):
        self.items.pop(key, None)


class Base(unittest.TestCase):
    def setUp(self):
        _ENV.write_text("BITBUCKET_WORKSPACE=acme\n")
        env = {k: v for k, v in os.environ.items() if k not in (youtube.SETTING, youtube_signin.ID_SETTING, youtube_signin.SECRET_SETTING)}
        env.update(PIXELDECK_ENV=str(_ENV), PIXELDECK_CREDENTIALS="off")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = [1_000_000.0]
        self.google = FakeGoogle()
        self.tokens = Path(tempfile.mkdtemp(prefix="pixeldeck-test-")) / "youtube.json"
        self.account = youtube_signin.Account(transport=self.google, store=self.tokens, clock=lambda: self.now[0])
        for target, value in ((youtube_signin, self.account),):
            p = mock.patch.object(target, "ACCOUNT", value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(youtube, "CLIENT", youtube.Client(transport=self.google, clock=lambda: self.now[0]))
        p.start()
        self.addCleanup(p.stop)

    def route(self, method, name, query=None, body=None):
        return youtube.route(HANDLER, None, method, name, query or {}, body)

    def save_client(self, client=CLIENT_ID, secret=SECRET):
        return self.route("POST", "youtube-client", body={"client": client, "secret": secret})

    def sign_in(self, channel="Lofi Cat"):
        """The whole round: save the client, start, and come back from Google with a code."""
        self.save_client()
        url = self.account.begin(HANDLER.port)
        state = parse_qs(urlsplit(url).query)["state"][0]
        self.google.on("POST", youtube_signin.TOKEN_URL, tokens())
        self.google.on("GET", youtube_signin.CHANNEL_URL, answer(200, {"items": [{"snippet": {"title": channel}}]}))
        return youtube_signin.callback({"state": [state], "code": ["4/the-code"]}), url


class ClientTest(Base):
    """The OAuth client you made: pasted as its JSON file, or as its ID and secret."""

    def test_the_downloaded_file_or_the_id_and_secret(self):
        blob = json.dumps({"installed": {"client_id": CLIENT_ID, "client_secret": SECRET, "project_id": "p"}})
        self.assertEqual(youtube_signin.read_client(blob), (CLIENT_ID, SECRET))
        self.assertEqual(youtube_signin.read_client(f"  {CLIENT_ID} ", f" {SECRET} "), (CLIENT_ID, SECRET))
        self.assertEqual(youtube_signin.read_client(CLIENT_ID), (CLIENT_ID, ""))

    def test_what_is_not_one_is_named_and_never_echoed(self):
        web = json.dumps({"web": {"client_id": CLIENT_ID, "client_secret": SECRET}})
        cases = {web: "Web application", "{not json": "could not be read", '{"other": 1}': '"installed"',
                 KEY: "API key", "12345.apps.googleusercontent.com": ".apps.googleusercontent.com", "": ".apps.googleusercontent.com"}
        for pasted, said in cases.items():
            with self.subTest(pasted=pasted[:12]), self.assertRaises(ValueError) as caught:
                youtube_signin.read_client(pasted)
            self.assertIn(said, str(caught.exception))
            for secret in (SECRET, CLIENT_ID, KEY):
                self.assertNotIn(secret, str(caught.exception))
        with self.assertRaises(ValueError):
            youtube_signin.read_client(CLIENT_ID, "not a secret!")

    def test_saved_the_id_to_the_file_the_secret_to_the_store_and_never_answered(self):
        store = FakeStore()
        with mock.patch("credentials.store", return_value=store):
            out = self.save_client()
            self.assertEqual(store.items, {youtube_signin.SECRET_SETTING: SECRET})
            self.assertIn(f"{youtube_signin.ID_SETTING}={CLIENT_ID}", _ENV.read_text())
            self.assertNotIn(SECRET, _ENV.read_text())
            self.assertEqual(out, {"client_set": True, "client_ok": True, "secret_set": True, "signed_in": False, "channel": None})
            for value in (SECRET, CLIENT_ID):
                self.assertNotIn(value, json.dumps(youtube.status()))

    def test_without_a_store_the_secret_goes_to_the_file(self):
        self.save_client()
        self.assertIn(f"{youtube_signin.SECRET_SETTING}={SECRET}", _ENV.read_text())


class SignInTest(Base):
    def test_the_link_asks_for_read_access_with_pkce_and_comes_back_here(self):
        self.save_client()
        url = self.account.begin(HANDLER.port)
        parts = urlsplit(url)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", youtube_signin.AUTHORIZE_URL)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        entry = self.account.pending.items[q["state"]]
        self.assertEqual(q["code_challenge"], oauth.pkce_challenge(entry["verifier"]))
        self.assertEqual({k: q[k] for k in ("client_id", "redirect_uri", "response_type", "scope", "code_challenge_method", "access_type", "prompt")},
                         {"client_id": CLIENT_ID, "redirect_uri": REDIRECT, "response_type": "code", "scope": youtube_signin.SCOPE,
                          "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
        self.assertNotIn(SECRET, url)

    def test_no_client_no_link(self):
        with self.assertRaises(ValueError):
            self.account.begin(HANDLER.port)

    def test_coming_back_trades_the_code_keeps_the_tokens_and_names_the_channel(self):
        (status, page), _url = self.sign_in(channel="Lofi <Cat>")
        self.assertEqual(status, 200)
        self.assertIn("Lofi &lt;Cat&gt;", page)
        trade = self.google.to(youtube_signin.TOKEN_URL)[0].form
        self.assertEqual(set(trade), {"grant_type", "code", "redirect_uri", "client_id", "code_verifier", "client_secret"})
        self.assertEqual((trade["grant_type"], trade["code"], trade["redirect_uri"], trade["client_secret"]),
                         ("authorization_code", "4/the-code", REDIRECT, SECRET))
        who = self.google.to(youtube_signin.CHANNEL_URL)[0]
        self.assertEqual(who.headers["Authorization"], "Bearer ya29.first")
        saved = json.loads(self.tokens.read_text())
        self.assertEqual((saved["refresh_token"], saved["client_id"], saved["channel"]), ("1//refresh-one", CLIENT_ID, "Lofi <Cat>"))
        self.assertEqual(youtube.status()["signed_in"], True)
        for value in ("ya29.first", "1//refresh-one", SECRET, CLIENT_ID):
            self.assertNotIn(value, json.dumps(youtube.status()))

    def test_a_link_works_once_and_a_refusal_says_so(self):
        (_status, _page), url = self.sign_in()
        state = parse_qs(urlsplit(url).query)["state"][0]
        again = youtube_signin.callback({"state": [state], "code": ["4/the-code"]})
        self.assertEqual(again[0], 400)
        self.assertIn("expired or was already used", again[1])
        url = self.account.begin(HANDLER.port)
        state = parse_qs(urlsplit(url).query)["state"][0]
        denied = youtube_signin.callback({"state": [state], "error": ["access_denied"]})
        self.assertEqual(denied[0], 400)
        self.assertIn("did not allow", denied[1])

    def test_a_link_older_than_ten_minutes_is_refused(self):
        self.save_client()
        state = parse_qs(urlsplit(self.account.begin(HANDLER.port)).query)["state"][0]
        self.now[0] += oauth.STATE_TTL + 1
        self.assertEqual(youtube_signin.callback({"state": [state], "code": ["4/x"]})[0], 400)
        self.assertEqual(self.google.calls, [])

    def test_read_access_not_granted_is_no_sign_in(self):
        self.save_client()
        state = parse_qs(urlsplit(self.account.begin(HANDLER.port)).query)["state"][0]
        self.google.on("POST", youtube_signin.TOKEN_URL, tokens(scope="openid"))
        status, page = youtube_signin.callback({"state": [state], "code": ["4/x"]})
        self.assertEqual(status, 502)
        self.assertIn("did not grant read access", page)
        self.assertFalse(self.account.signed_in())


class TokenTest(Base):
    def setUp(self):
        super().setUp()
        self.sign_in()
        self.google.calls.clear()

    def test_a_live_token_is_used_as_is_and_an_old_one_renewed(self):
        self.assertEqual(self.account.access(), "ya29.first")
        self.assertEqual(self.google.calls, [])
        self.now[0] += 3600
        self.google.on("POST", youtube_signin.TOKEN_URL, answer(200, {"access_token": "ya29.second", "expires_in": 3599}))
        self.assertEqual(self.account.access(), "ya29.second")
        renew = self.google.to(youtube_signin.TOKEN_URL)[0].form
        self.assertEqual((renew["grant_type"], renew["refresh_token"], renew["client_secret"]), ("refresh_token", "1//refresh-one", SECRET))
        self.assertEqual(json.loads(self.tokens.read_text())["refresh_token"], "1//refresh-one", "a renewal keeps the refresh token")

    def test_a_sign_in_google_ended_is_forgotten_and_says_why(self):
        self.now[0] += 3600
        self.google.on("POST", youtube_signin.TOKEN_URL, answer(400, {"error": "invalid_grant"}))
        with self.assertRaises(youtube_signin.NotSignedIn) as caught:
            self.account.access()
        self.assertIn("7 days", str(caught.exception))
        self.assertFalse(self.account.signed_in())

    def test_another_client_needs_a_new_sign_in(self):
        other = "-".join(["123456789012", "b2" * 16]) + ".apps.googleusercontent" + ".com"
        self.google.on("POST", youtube_signin.REVOKE_URL, answer(200, {}))
        self.save_client(client=other, secret="")
        self.assertFalse(self.account.signed_in())
        self.assertEqual(self.google.to(youtube_signin.REVOKE_URL)[0].form, {"token": "1//refresh-one"})

    def test_sign_out_revokes_and_forgets_even_when_google_is_away(self):
        def away(*_args):
            raise youtube_signin.SignInError(0, "Google could not be reached: offline")
        self.account.transport = away
        out = self.route("POST", "youtube-signout")
        self.assertEqual((out["signed_in"], self.tokens.exists()), (False, False))


class SignedInApiTest(Base):
    """Signed in, the Data API is called with the token, and your own lists are there."""

    def setUp(self):
        super().setUp()
        self.sign_in()
        self.google.calls.clear()

    def test_a_search_uses_the_token_not_a_key_and_a_refused_token_is_renewed_once(self):
        self.google.on("GET", youtube.SEARCH_URL, answer(401, {"error": {"code": 401, "message": "Invalid Credentials"}}),
                       answer(200, {"items": []}))
        self.google.on("POST", youtube_signin.TOKEN_URL, answer(200, {"access_token": "ya29.second", "expires_in": 3599}))
        self.route("GET", "youtube-search", {"q": ["lofi"]})
        asks = self.google.to(youtube.SEARCH_URL)
        self.assertEqual([c.headers["Authorization"] for c in asks], ["Bearer ya29.first", "Bearer ya29.second"])
        self.assertNotIn("key", parse_qs(urlsplit(asks[0].url).query))

    def test_your_playlists_liked_videos_and_subscriptions(self):
        thumb = "https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg"
        self.google.on("GET", youtube.MY_PLAYLISTS_URL, answer(200, {"items": [
            {"id": "PLmine0123456789", "snippet": {"title": "Focus", "thumbnails": {"medium": {"url": thumb}}}, "contentDetails": {"itemCount": 12}},
            {"id": "x", "snippet": {"title": "not an id"}}]}))
        self.google.on("GET", youtube.VIDEOS_URL, answer(200, {"items": [
            {"id": "abcdefghijk", "snippet": {"title": "Liked &amp; loved", "channelTitle": "Lofi Cat"}}]}))
        self.google.on("GET", youtube.SUBSCRIPTIONS_URL, answer(200, {"items": [
            {"snippet": {"title": "Lofi Girl", "resourceId": {"kind": "youtube#channel", "channelId": "UC" + "S" * 22}}},
            {"snippet": {"title": "nope", "resourceId": {"kind": "youtube#channel", "channelId": "UCshort"}}}]}))
        self.assertEqual(self.route("GET", "youtube-mine", {"what": ["playlists"]})["items"],
                         [{"list": "PLmine0123456789", "title": "Focus", "channel": False, "count": 12, "thumb": thumb}])
        self.assertEqual(self.route("GET", "youtube-mine", {"what": ["liked"]})["items"],
                         [{"id": "abcdefghijk", "title": "Liked & loved", "channel": "Lofi Cat", "thumb": thumb, "live": False}])
        self.assertEqual(self.route("GET", "youtube-mine", {"what": ["subscriptions"]})["items"],
                         [{"list": "UU" + "S" * 22, "title": "Lofi Girl", "channel": True, "count": None, "thumb": None}])
        liked = {k: v[0] for k, v in parse_qs(urlsplit(self.google.to(youtube.VIDEOS_URL)[0].url).query).items()}
        self.assertEqual(liked, {"part": "snippet", "myRating": "like", "maxResults": "50"})
        self.assertEqual(self.route("GET", "youtube-mine", {"what": ["liked"]})["cached"], True)
        with self.assertRaises(ValueError):
            self.route("GET", "youtube-mine", {"what": ["history"]})

    def test_answers_kept_for_one_account_are_not_served_to_the_next(self):
        self.google.on("GET", youtube.VIDEOS_URL, answer(200, {"items": []}))
        self.route("GET", "youtube-mine", {"what": ["liked"]})
        self.google.on("POST", youtube_signin.REVOKE_URL, answer(200, {}))
        self.route("POST", "youtube-signout")
        with self.assertRaises(youtube_signin.NotSignedIn):
            self.route("GET", "youtube-mine", {"what": ["liked"]})
        self.google.on("POST", youtube_signin.TOKEN_URL, tokens(access="ya29.other", refresh="1//refresh-two"))
        state = parse_qs(urlsplit(self.account.begin(HANDLER.port)).query)["state"][0]
        youtube_signin.callback({"state": [state], "code": ["4/again"]})
        self.assertFalse(self.route("GET", "youtube-mine", {"what": ["liked"]})["cached"])

    def test_signed_in_a_playlist_needs_no_key(self):
        self.google.on("GET", youtube.PLAYLIST_URL, answer(200, {"items": []}))
        self.assertEqual(self.route("GET", "youtube-playlist", {"list": ["PLmine0123456789"]})["items"], [])
        self.assertEqual(self.google.to(youtube.PLAYLIST_URL)[0].headers["Authorization"], "Bearer ya29.first")


class NotSignedInTest(Base):
    def test_your_lists_need_a_sign_in_and_ask_nobody(self):
        with self.assertRaises(youtube_signin.NotSignedIn):
            self.route("GET", "youtube-mine", {"what": ["playlists"]})
        self.assertEqual(self.google.calls, [])

    def test_through_the_server_it_is_a_403(self):
        import server  # noqa: PLC0415 - as in test_desk: the server module is heavy to load
        sent = []
        desk = server.Desk.__new__(server.Desk)
        desk.engine, desk.keeper = None, None
        desk._send = lambda code, body, *rest: sent.append((int(code), body))
        desk._api("GET", "youtube-mine", {"what": ["liked"]}, None)
        self.assertEqual((sent[-1][0], sent[-1][1]["error"]), (403, youtube_signin.NOT_SIGNED_IN))


if __name__ == "__main__":
    unittest.main()
