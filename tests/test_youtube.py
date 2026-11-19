"""Run with: python3 -m unittest discover -s tests  (from the PixelDeck folder).

YouTube is faked here: every request goes to a scripted transport that fails on anything it
was not told to expect, and the API key lives in the test's own settings file."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env: they write one of their own.
_ENV = Path(tempfile.mkdtemp(prefix="pixeldeck-test-")) / ".env"
_START = "BITBUCKET_WORKSPACE=acme\n# a comment that stays\n"
os.environ.setdefault("PIXELDECK_ENV", str(_ENV))

import youtube  # noqa: E402
import youtube_signin  # noqa: E402

KEY = "AIza" + "Sy0123456789abcdefghijklmnopqrstu_-"  # made up: the shape of a key, 39 characters
OTHER = "AIza" + "Zz9876543210ZYXWVUTSRQPONMLKJIHGF-_"
VID = "dQw4w9WgXcQ"
HANDLER = types.SimpleNamespace(port=8644)
SEARCH = youtube.SEARCH_URL


def answer(status=200, payload=None, headers=None, raw=None):
    body = raw if raw is not None else (b"" if payload is None else json.dumps(payload).encode())
    return status, {"content-type": "application/json", **(headers or {})}, body


def google_error(code, reason, message="", detail=None):
    err = {"code": code, "message": message, "errors": [{"message": message, "domain": "youtube", "reason": reason}]}
    if detail:
        err["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": detail, "domain": "googleapis.com"}]
    return answer(code, {"error": err})


def snippet(ident, title, channel="Lofi Cat", live="none", thumb=None):
    pics = {"default": {"url": f"https://i.ytimg.com/vi/{ident}/default.jpg"},
            "medium": {"url": thumb or f"https://i.ytimg.com/vi/{ident}/mqdefault.jpg"}}
    return {"kind": "youtube#searchResult", "id": {"kind": "youtube#video", "videoId": ident},
            "snippet": {"title": title, "channelTitle": channel, "liveBroadcastContent": live, "thumbnails": pics}}


class FakeTube:
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
        # Each test gets this file's settings, whatever another test module set at its import.
        _ENV.write_text(_START)
        env = {k: v for k, v in os.environ.items() if k != youtube.SETTING}
        env.update(PIXELDECK_ENV=str(_ENV), PIXELDECK_CREDENTIALS="off")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = [1000.0]
        self.fake = FakeTube()
        self.saved = youtube.CLIENT
        youtube.CLIENT = youtube.Client(transport=self.fake, clock=lambda: self.now[0])
        self.addCleanup(setattr, youtube, "CLIENT", self.saved)
        # nobody signed in, in a token file of the test's own: never your real state folder
        tokens = Path(tempfile.mkdtemp(prefix="pixeldeck-test-")) / "youtube.json"
        patcher = mock.patch.object(youtube_signin, "ACCOUNT", youtube_signin.Account(transport=self.fake, store=tokens))
        patcher.start()
        self.addCleanup(patcher.stop)

    def route(self, method, name, query=None, body=None):
        return youtube.route(HANDLER, None, method, name, query or {}, body)

    def save(self, key=KEY):
        return self.route("POST", "youtube-key", body={"key": key})


class KeyTest(Base):
    """The API key is set from the settings, into the settings file, and never read back."""

    def test_saving_writes_the_settings_file_and_never_answers_the_key(self):
        status = self.save(f"  {KEY} ")
        text = _ENV.read_text()
        self.assertIn(f"YOUTUBE_API_KEY={KEY}\n", text)
        self.assertIn("# a comment that stays", text)
        self.assertEqual((status["key_set"], status["key_ok"], status["where"], status["setting"]), (True, True, "file", "YOUTUBE_API_KEY"))
        self.assertNotIn(KEY, json.dumps(status), "the key never goes back to the page")
        self.assertNotIn(KEY, json.dumps(self.route("GET", "youtube-status")))
        self.assertEqual(self.fake.calls, [], "saving asks nobody")

    def test_a_key_that_is_not_one_is_refused_and_nothing_is_written(self):
        for bad in ("", "   ", KEY[:-1], KEY + "x", "AIzb" + KEY[4:], KEY.lower(), KEY[:20] + " " + KEY[21:], KEY[:-1] + "!",
                    f"{KEY}\nEVIL=1", "BITBUCKET_TOKEN=x", None, 12345, [KEY], {"key": KEY}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.save(bad)
        self.assertEqual(_ENV.read_text(), _START)
        self.assertFalse(self.route("GET", "youtube-status")["key_set"])

    def test_an_oauth_client_pasted_instead_is_named_and_never_echoed(self):
        # Made up, and put together here so no file holds anything shaped like a real credential.
        client_id = "-".join(["123456789012", "x" * 32]) + ".apps.googleusercontent" + ".com"
        secret = "GOCSPX" + "-" + "y" * 28
        blob = json.dumps({"installed": {"client_id": client_id, "client_secret": secret, "project_id": "p"}})
        for pasted in (client_id, secret, blob):
            with self.subTest(pasted=pasted[:12]), self.assertRaises(ValueError) as caught:
                self.save(pasted)
            self.assertIn("OAuth client, not an API key", str(caught.exception))
            self.assertIn("Sign in with Google", str(caught.exception), "it says where the client goes instead")
            self.assertNotIn(secret, str(caught.exception))
            self.assertNotIn(client_id, str(caught.exception))
        self.assertEqual(_ENV.read_text(), _START)
        with self.assertRaises(ValueError) as caught:
            self.save("Alza" + KEY[4:])  # a lowercase L where the capital I goes
        self.assertIn("capital I", str(caught.exception))

    def test_clear_removes_it_and_forgets_the_searches(self):
        self.save()
        youtube.CLIENT.cache["lofi"] = (self.now[0], {"items": []})
        status = self.route("POST", "youtube-key", body={"clear": True})
        self.assertNotIn("YOUTUBE_API_KEY", _ENV.read_text())
        self.assertEqual((status["key_set"], status["where"]), (False, None))
        self.assertEqual(len(youtube.CLIENT.cache), 0)

    def test_a_credential_store_takes_the_key_off_the_file(self):
        store = FakeStore()
        with mock.patch.object(youtube.credentials, "store", lambda: store):
            _ENV.write_text(_START + f"YOUTUBE_API_KEY={OTHER}\n")
            status = self.save()
            self.assertEqual(store.items, {"YOUTUBE_API_KEY": KEY})
            self.assertNotIn("YOUTUBE_API_KEY", _ENV.read_text(), "the file's old key would win over the store's")
            self.assertEqual((status["key_set"], status["where"], status["store"]), (True, "store", "Test credential store"))
            self.assertNotIn(KEY, json.dumps(status))
            self.assertEqual(youtube.api_key(), KEY)
            self.route("POST", "youtube-key", body={"clear": True})
            self.assertEqual(store.items, {})

    def test_the_environment_wins_and_says_so(self):
        os.environ["YOUTUBE_API_KEY"] = OTHER
        self.assertEqual(self.route("GET", "youtube-status")["where"], "environment")


class SearchTest(Base):
    def setUp(self):
        super().setUp()
        self.save()

    def results(self, *items):
        self.fake.on("GET", SEARCH, answer(200, {"kind": "youtube#searchListResponse", "items": list(items)}))

    def test_a_search_asks_for_embeddable_videos_and_answers_trimmed(self):
        self.results(
            snippet("abcdefghijk", "Lofi hip hop radio &#39;24/7&#39; &amp; beats", live="live"),
            snippet("ABCDEFGHIJ_", "Synthwave <mix>", channel=None, thumb="https://evil.example/x.jpg"),
            {"id": {"kind": "youtube#channel", "channelId": "UC123"}, "snippet": {"title": "A channel"}},
            snippet("short", "an id that is not one"),
            "not an item",
        )
        out = self.route("GET", "youtube-search", {"q": ["  lofi   hip hop "]})
        self.assertEqual(out["items"], [
            {"id": "abcdefghijk", "title": "Lofi hip hop radio '24/7' & beats", "channel": "Lofi Cat",
             "thumb": "https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg", "live": True},
            {"id": "ABCDEFGHIJ_", "title": "Synthwave <mix>", "channel": None,
             "thumb": "https://i.ytimg.com/vi/ABCDEFGHIJ_/default.jpg", "live": False},
        ])
        self.assertEqual((out["query"], out["link"], out["cached"]), ("lofi hip hop", False, False))
        call = self.fake.calls[0]
        parts = urlsplit(call.url)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", SEARCH)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.assertEqual(q, {"part": "snippet", "type": "video", "maxResults": "12", "safeSearch": "moderate",
                             "videoEmbeddable": "true", "q": "lofi hip hop", "key": KEY})
        self.assertNotIn(KEY, json.dumps(out), "the key never goes back to the page")

    def test_the_same_search_is_answered_from_memory_for_a_few_minutes(self):
        self.results(snippet("abcdefghijk", "One"))
        first = self.route("GET", "youtube-search", {"q": ["Lofi Hip Hop"]})
        again = self.route("GET", "youtube-search", {"q": [" lofi  hip hop"]})
        self.assertEqual(len(self.fake.calls), 1, "a repeated search costs no quota")
        self.assertEqual((first["cached"], again["cached"], again["items"]), (False, True, first["items"]))
        self.now[0] += youtube.CACHE_TTL + 1
        self.route("GET", "youtube-search", {"q": ["lofi hip hop"]})
        self.assertEqual(len(self.fake.calls), 2, "after a while it asks again")

    def test_the_memory_is_capped(self):
        self.results(snippet("abcdefghijk", "One"))
        for n in range(youtube.CACHE_MAX + 5):
            self.route("GET", "youtube-search", {"q": [f"song {n}"]})
        self.assertEqual(len(youtube.CLIENT.cache), youtube.CACHE_MAX)
        self.assertNotIn("song 0", youtube.CLIENT.cache)

    def test_the_quota_says_what_it_is_and_is_not_kept(self):
        self.fake.on("GET", SEARCH, google_error(403, "quotaExceeded", "The request cannot be completed because you have exceeded your <a href='/quota'>quota</a>."),
                     answer(200, {"items": [snippet("abcdefghijk", "One")]}))
        with self.assertRaises(youtube.YouTubeError) as caught:
            self.route("GET", "youtube-search", {"q": ["lofi"]})
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("10,000 units a day", caught.exception.message)
        self.assertIn("about 100 searches", caught.exception.message)
        self.assertEqual(len(self.route("GET", "youtube-search", {"q": ["lofi"]})["items"]), 1, "a refusal is not kept")
        self.assertEqual(len(self.fake.calls), 2)

    def test_a_bad_key_and_other_refusals_say_what_to_do(self):
        cases = [
            (google_error(400, "badRequest", "API key not valid. Please pass a valid API key.", "API_KEY_INVALID"), youtube.BAD_KEY),
            (google_error(400, "keyInvalid", "Bad Request"), youtube.BAD_KEY),
            (google_error(403, "accessNotConfigured", "YouTube Data API v3 has not been used in project 1 before", "SERVICE_DISABLED"), youtube.NOT_ENABLED),
            (google_error(403, "forbidden", "Requests to this API are blocked.", "API_KEY_SERVICE_BLOCKED"), youtube.RESTRICTED),
            (google_error(403, "forbidden", "Requests from referer <empty> are blocked.", "API_KEY_HTTP_REFERRER_BLOCKED"), youtube.RESTRICTED),
            (google_error(403, "rateLimitExceeded", "slow down"), youtube.QUOTA),
        ]
        for n, (reply, message) in enumerate(cases):
            with self.subTest(case=n):
                self.fake.on("GET", SEARCH, reply)
                with self.assertRaises(youtube.YouTubeError) as caught:
                    self.route("GET", "youtube-search", {"q": [f"case {n}"]})
                self.assertEqual(caught.exception.message, message)

    def test_an_unknown_refusal_keeps_googles_words_but_never_the_key(self):
        self.fake.on("GET", SEARCH, google_error(500, "backendError", f"<b>Backend</b> broke for key={KEY}, sorry"))
        with self.assertRaises(youtube.YouTubeError) as caught:
            self.route("GET", "youtube-search", {"q": ["lofi"]})
        self.assertEqual(caught.exception.message, "YouTube answered 500: Backend broke for key=[key], sorry")
        self.fake.on("GET", SEARCH, answer(502, raw=b"<html>gateway</html>"))
        with self.assertRaises(youtube.YouTubeError) as caught:
            self.route("GET", "youtube-search", {"q": ["other"]})
        self.assertEqual(caught.exception.message, "YouTube answered 502")

    def test_a_network_failure_never_shows_the_url(self):
        def down(*_a, **_k):
            raise youtube.urllib.error.URLError("Name or service not known")
        opener = types.SimpleNamespace(open=down)
        with mock.patch.object(youtube, "_opener", [opener]):
            with self.assertRaises(youtube.YouTubeError) as caught:
                youtube.urllib_transport("GET", f"{SEARCH}?key={KEY}", {}, None)
        self.assertEqual(caught.exception.status, 0)
        self.assertNotIn(KEY, caught.exception.message)
        self.assertIn("could not be reached", caught.exception.message)

    def test_what_the_search_refuses_before_asking(self):
        for bad in ([""], ["   "], ["x" * (youtube.QUERY_MAX + 1)]):
            with self.subTest(q=bad), self.assertRaises(ValueError):
                self.route("GET", "youtube-search", {"q": bad})
        with self.assertRaises(ValueError):
            youtube.CLIENT.search(None)
        self.assertEqual(self.fake.calls, [])

    def test_without_a_key_a_search_is_refused_and_asks_nobody(self):
        self.route("POST", "youtube-key", body={"clear": True})
        with self.assertRaises(youtube.NoKey) as caught:
            self.route("GET", "youtube-search", {"q": ["lofi hip hop"]})
        self.assertIn("pasted link plays without one", str(caught.exception))
        self.assertEqual(self.fake.calls, [])


class ThroughTheServerTest(Base):
    """The page sees one error shape: the server's, with the status a refusal deserves."""

    def setUp(self):
        super().setUp()
        import server  # noqa: PLC0415 - as in test_desk: the server module is heavy to load
        self.sent = []
        self.desk = server.Desk.__new__(server.Desk)
        self.desk.engine = None
        self.desk.keeper = None
        self.desk._send = lambda code, body, *rest: self.sent.append((int(code), body, rest))

    def call(self, method, route, query=None, body=None):
        self.sent.clear()
        self.desk._api(method, route, query or {}, body)
        return self.sent[-1]

    def test_errors_answer_in_the_servers_shape(self):
        code, body, _ = self.call("GET", "youtube-search", {"q": ["lofi"]})
        self.assertEqual((code, body["error"]), (403, youtube.NO_KEY))
        self.save()
        self.fake.on("GET", SEARCH, google_error(403, "quotaExceeded", "quota"))
        code, body, _ = self.call("GET", "youtube-search", {"q": ["lofi"]})
        self.assertEqual((code, body["error"], body["upstream"]), (502, youtube.QUOTA, 403))
        code, body, _ = self.call("GET", "youtube-search", {"q": ["x" * 300]})
        self.assertEqual(code, 400)
        code, body, _ = self.call("POST", "youtube-key", body={"key": "nope"})
        self.assertEqual(code, 400)
        code, body, _ = self.call("GET", "youtube-thumb", {"url": ["https://evil.example/vi/abcdefghijk/mqdefault.jpg"]})
        self.assertEqual(code, 400)

    def test_a_link_plays_through_the_server_without_a_key(self):
        code, body, _ = self.call("GET", "youtube-search", {"q": [f"https://youtu.be/{VID}?t=42"]})
        self.assertEqual((code, body["link"], body["items"][0]["id"]), (200, True, VID))
        self.assertEqual(self.fake.calls, [])

    def test_a_thumbnail_goes_out_as_a_picture(self):
        self.fake.on("GET", "https://i.ytimg.com/", answer(200, raw=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}))
        code, body, rest = self.call("GET", "youtube-thumb", {"url": [f"https://i.ytimg.com/vi/{VID}/mqdefault.jpg"]})
        self.assertEqual((code, body, rest), (200, b"\xff\xd8jpeg", ("image/jpeg",)))

    def test_the_page_may_frame_youtubes_player_and_nothing_else(self):
        import server  # noqa: PLC0415
        headers = {}
        self.desk._send = lambda code, body, ctype=None, extra=None: headers.update(extra or {})
        self.desk._index()
        policy = dict(part.strip().split(" ", 1) for part in headers["Content-Security-Policy"].split(";") if part.strip())
        self.assertEqual(policy["frame-src"], "https://www.youtube-nocookie.com")
        self.assertEqual(policy["img-src"], "'self' data:", "pictures still come through the server only")
        self.assertEqual(policy["frame-ancestors"], "'none'")
        self.assertIn("youtube", server.FEATURES)


class ThumbTest(Base):
    def test_thumbnails_only_come_from_youtubes_image_host(self):
        good = f"https://i.ytimg.com/vi/{VID}/mqdefault.jpg"
        for bad in ("http://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg", "https://i.ytimg.com.evil.example/vi/dQw4w9WgXcQ/mqdefault.jpg",
                    "https://evil.example/https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg", "https://user@i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
                    "https://i.ytimg.com:444/vi/dQw4w9WgXcQ/mqdefault.jpg", good + "?x=1", good + "#x", good + "\n",
                    "https://i.ytimg.com/vi/../dQw4w9WgXcQ/mqdefault.jpg", "https://i.ytimg.com/vi/dQw4w9WgXc/mqdefault.jpg",
                    "https://i.ytimg.com/vi/dQw4w9WgXcQ/mq/default.jpg", "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.png",
                    "https://I.YTIMG.COM/vi/dQw4w9WgXcQ/mqdefault.jpg", "https://i9.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
                    "https://i.ytimg.com/an_webp/dQw4w9WgXcQ/mqdefault.webp", "https://img.youtube.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
                    "", None, [good]):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                youtube.thumb_url(bad)
        for ok in (good, f"https://i.ytimg.com/vi/{VID}/mqdefault_live.jpg", f"https://i.ytimg.com/vi_webp/{VID}/hqdefault.webp"):
            with self.subTest(url=ok):
                self.assertEqual(youtube.thumb_url(ok), ok)
        self.assertEqual(self.fake.calls, [])

    def test_a_thumbnail_is_fetched_once_without_the_key(self):
        self.save()
        url = f"https://i.ytimg.com/vi/{VID}/mqdefault.jpg"
        self.fake.on("GET", "https://i.ytimg.com/", answer(200, raw=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg; charset=binary"}))
        self.assertEqual(self.route("GET", "youtube-thumb", {"url": [url]}), (b"\xff\xd8jpeg", "image/jpeg"))
        self.assertEqual(self.route("GET", "youtube-thumb", {"url": [url]}), (b"\xff\xd8jpeg", "image/jpeg"))
        self.assertEqual(len(self.fake.calls), 1, "kept in memory")
        call = self.fake.calls[0]
        self.assertEqual(call.url, url)
        self.assertNotIn(KEY, json.dumps(call.headers))

    def test_what_is_not_a_small_picture_is_refused(self):
        for n, reply in enumerate((answer(200, raw=b"<html>", headers={"content-type": "text/html"}),
                                   answer(200, raw=b"x" * (youtube.THUMB_MAX + 1), headers={"content-type": "image/jpeg"}),
                                   answer(404, raw=b"", headers={"content-type": "image/jpeg"}))):
            with self.subTest(case=n):
                self.fake.on("GET", "https://i.ytimg.com/", reply)
                with self.assertRaises(youtube.YouTubeError):
                    youtube.CLIENT.thumb(f"https://i.ytimg.com/vi/{VID}/{'abc'[n]}default.jpg")

    def test_the_thumbnail_memory_is_capped_by_bytes(self):
        self.fake.on("GET", "https://i.ytimg.com/", answer(200, raw=b"x" * 200_000, headers={"content-type": "image/jpeg"}))
        for n in range(60):
            youtube.CLIENT.thumb(f"https://i.ytimg.com/vi/{VID[:-2]}{n:02d}/mqdefault.jpg")
        self.assertLessEqual(youtube.CLIENT.thumb_bytes, youtube.THUMB_BYTES)
        self.assertEqual(youtube.CLIENT.thumb_bytes, sum(len(v[0]) for v in youtube.CLIENT.thumbs.values()))


class LinkTest(Base):
    """A pasted link or id plays without the API: read here, asking nobody."""

    def test_the_links_youtube_gives_out(self):
        for text in (f"https://www.youtube.com/watch?v={VID}", f"https://youtube.com/watch?v={VID}&t=42s&list=PL1",
                     f"https://www.youtube.com/watch?feature=share&v={VID}", f"http://www.youtube.com/watch?v={VID}",
                     f"https://m.youtube.com/watch?v={VID}", f"https://music.youtube.com/watch?v={VID}",
                     f"https://youtu.be/{VID}", f"https://youtu.be/{VID}?si=abc&t=10", f"youtu.be/{VID}",
                     f"www.youtube.com/watch?v={VID}", f"youtube.com/watch?v={VID}", f"https://www.youtube.com/shorts/{VID}",
                     f"https://www.youtube.com/embed/{VID}?start=3", f"https://www.youtube-nocookie.com/embed/{VID}",
                     f"https://www.youtube.com/live/{VID}?feature=shared", f"https://WWW.YOUTUBE.COM/watch?v={VID}",
                     f"https://www.youtube.com:443/watch?v={VID}", f"  https://youtu.be/{VID}  ", VID):
            with self.subTest(text=text):
                self.assertEqual(youtube.video_id(text), VID)

    def test_what_is_not_a_link_to_one_video(self):
        for text in (f"https://evil.example/watch?v={VID}", f"https://youtube.com.evil.example/watch?v={VID}",
                     f"https://evil.example/?u=https://youtu.be/{VID}", f"https://notyoutube.com/watch?v={VID}",
                     f"https://youtu.be.evil.example/{VID}", "https://www.youtube.com/playlist?list=PL0123456789",
                     "https://www.youtube.com/@lofigirl", f"https://www.youtube.com/watch?v={VID[:-1]}",
                     f"https://www.youtube.com/watch?v={VID}x", f"https://www.youtube.com/watch/{VID}",
                     f"ftp://youtube.com/watch?v={VID}", f"javascript:alert(1)//youtu.be/{VID}",
                     f"https://user@youtube.com/watch?v={VID}", f"https://youtube.com:8080/watch?v={VID}",
                     f"https://youtu.be/{VID} and more", f"https://youtube.com/watch?v={VID[:5]}%20{VID[5:]}",
                     "dQw4w9WgXc", "dQw4w9WgXcQQ", "dQw4w9WgXc!", "", "   ", None, 42, "https://[::1/watch?v=x"):
            with self.subTest(text=text):
                self.assertIsNone(youtube.video_id(text))
        self.assertIsNone(youtube.video_id(VID, bare=False), "a bare id only when asked for")

    def test_a_link_answers_without_a_key_or_a_call(self):
        out = self.route("GET", "youtube-search", {"q": [f"https://www.youtube.com/watch?v={VID}"]})
        self.assertEqual(out, {"query": f"https://www.youtube.com/watch?v={VID}", "link": True, "cached": False,
                               "items": [{"id": VID, "title": None, "channel": None, "thumb": f"https://i.ytimg.com/vi/{VID}/mqdefault.jpg", "live": False}]})
        self.assertEqual(self.route("GET", "youtube-search", {"q": [VID]})["items"][0]["id"], VID)
        self.assertEqual(self.fake.calls, [])

    def test_with_a_key_a_lone_word_is_a_search_and_an_id_is_still_an_id(self):
        self.save()
        self.fake.on("GET", SEARCH, answer(200, {"items": [snippet("abcdefghijk", "Programming music")]}))
        self.assertFalse(self.route("GET", "youtube-search", {"q": ["programming"]})["link"])
        self.assertEqual(len(self.fake.calls), 1)
        self.assertTrue(self.route("GET", "youtube-search", {"q": [VID]})["link"], "an id has a digit, - or _")
        self.assertTrue(self.route("GET", "youtube-search", {"q": ["https://youtu.be/abcdefghijk"]})["link"], "a link is a link, whatever its id")
        self.assertEqual(len(self.fake.calls), 1)


LIST = "PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG"


def entry(ident, title, privacy="public", owner="Lofi Cat"):
    return {"kind": "youtube#playlistItem", "status": {"privacyStatus": privacy},
            "snippet": {"title": title, "channelTitle": "Who made the list", "videoOwnerChannelTitle": owner,
                        "resourceId": {"kind": "youtube#video", "videoId": ident},
                        "thumbnails": {"medium": {"url": f"https://i.ytimg.com/vi/{ident}/mqdefault.jpg"}}}}


def page(*items, token=None):
    payload = {"kind": "youtube#playlistItemListResponse", "items": list(items)}
    return answer(200, {**payload, "nextPageToken": token} if token else payload)


class PlaylistLinkTest(Base):
    """A playlist is named by a link's list=; a link to one video in a playlist names both."""

    def test_the_playlist_links_youtube_gives_out(self):
        for text in (f"https://www.youtube.com/playlist?list={LIST}", f"https://music.youtube.com/playlist?list={LIST}",
                     f"https://www.youtube.com/watch?v={VID}&list={LIST}&index=3", f"https://youtu.be/{VID}?list={LIST}",
                     f"https://www.youtube-nocookie.com/embed/videoseries?list={LIST}", f"youtube.com/playlist?list={LIST}"):
            with self.subTest(text=text):
                self.assertEqual(youtube.playlist_id(text), LIST)

    def test_what_is_not_a_playlist_link(self):
        for text in (LIST, f"https://evil.example/playlist?list={LIST}", f"https://www.youtube.com/@lofi?list={LIST}",
                     f"https://www.youtube.com/playlist?list={LIST}<script>", "https://www.youtube.com/playlist?list=PL1",
                     f"https://www.youtube.com/watch?v={VID}", f"https://user@youtube.com/playlist?list={LIST}", "", None):
            with self.subTest(text=text):
                self.assertIsNone(youtube.playlist_id(text))

    def test_a_link_answers_with_its_list_without_a_key_or_a_call(self):
        both = self.route("GET", "youtube-search", {"q": [f"https://www.youtube.com/watch?v={VID}&list={LIST}"]})
        self.assertEqual((both["link"], [i["id"] for i in both["items"]], both["list"]), (True, [VID], LIST))
        only = self.route("GET", "youtube-search", {"q": [f"https://www.youtube.com/playlist?list={LIST}"]})
        self.assertEqual((only["link"], only["items"], only["list"]), (True, [], LIST))
        self.assertNotIn("list", self.route("GET", "youtube-search", {"q": [f"https://youtu.be/{VID}"]}))
        self.assertEqual(self.fake.calls, [])


class PlaylistTest(Base):
    def setUp(self):
        super().setUp()
        self.save()

    def items(self, **query):
        return self.route("GET", "youtube-playlist", {"list": [LIST], **query})

    def test_a_playlist_answers_its_playable_videos_in_order(self):
        self.fake.on("GET", youtube.PLAYLIST_URL, page(
            entry("abcdefghijk", "One &amp; two"), entry("ABCDEFGHIJ_", "Private video", privacy="private"),
            entry("bcdefghijkl", "Deleted video", privacy="privacyStatusUnspecified"),
            {"snippet": {"resourceId": {"kind": "youtube#channel", "channelId": "UC1"}}, "status": {"privacyStatus": "public"}},
            entry("cdefghijklm", "Three", privacy="unlisted", owner=None), "not an item"))
        out = self.items()
        self.assertEqual(out, {"list": LIST, "more": False, "cached": False, "items": [
            {"id": "abcdefghijk", "title": "One & two", "channel": "Lofi Cat", "thumb": "https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg", "live": False},
            {"id": "cdefghijklm", "title": "Three", "channel": None, "thumb": "https://i.ytimg.com/vi/cdefghijklm/mqdefault.jpg", "live": False}]})
        q = {k: v[0] for k, v in parse_qs(urlsplit(self.fake.calls[0].url).query).items()}
        self.assertEqual(q, {"part": "snippet,status", "playlistId": LIST, "maxResults": "50", "key": KEY})
        self.assertNotIn(KEY, json.dumps(out))

    def test_it_pages_and_stops_after_four_calls(self):
        fifty = lambda n: [entry(f"{n:02d}{i:02d}abcdefg", f"{n}-{i}") for i in range(50)]  # noqa: E731
        self.fake.on("GET", youtube.PLAYLIST_URL, page(*fifty(1), token="p2"), page(*fifty(2), token="p3"),
                     page(*fifty(3), token="p4"), page(*fifty(4), token="p5"))
        out = self.items()
        self.assertEqual((len(out["items"]), out["more"], len(self.fake.calls)), (200, True, 4))
        tokens = [parse_qs(urlsplit(c.url).query).get("pageToken", [None])[0] for c in self.fake.calls]
        self.assertEqual(tokens, [None, "p2", "p3", "p4"])

    def test_a_list_of_private_videos_costs_four_calls_at_most(self):
        self.fake.on("GET", youtube.PLAYLIST_URL, page(entry("abcdefghijk", "Private video", privacy="private"), token="again"))
        out = self.items()
        self.assertEqual((out["items"], out["more"], len(self.fake.calls)), ([], True, 4))

    def test_the_same_playlist_is_answered_from_memory(self):
        self.fake.on("GET", youtube.PLAYLIST_URL, page(entry("abcdefghijk", "One")))
        first, again = self.items(), self.items()
        self.assertEqual((first["cached"], again["cached"], len(self.fake.calls)), (False, True, 1))
        self.now[0] += youtube.CACHE_TTL + 1
        self.items()
        self.assertEqual(len(self.fake.calls), 2)

    def test_refusals_and_bad_ids(self):
        self.fake.on("GET", youtube.PLAYLIST_URL, google_error(404, "playlistNotFound", "The playlist cannot be found."))
        with self.assertRaises(youtube.YouTubeError) as caught:
            self.items()
        self.assertEqual(caught.exception.message, youtube.PRIVATE_LIST)
        for bad in ("PL1", f"{LIST}/../x", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.route("GET", "youtube-playlist", {"list": [bad]})

    def test_without_a_key_it_asks_nobody(self):
        self.route("POST", "youtube-key", body={"clear": True})
        with self.assertRaises(youtube.NoKey) as caught:
            self.items()
        self.assertEqual(str(caught.exception), youtube.NO_KEY_LIST)
        self.assertEqual(self.fake.calls, [])


@unittest.skipUnless(shutil.which("node"), "needs node")
class QueueTest(unittest.TestCase):
    """static/music-queue.js, run in node: it needs no page."""

    def run_js(self, body):
        src = (Path(__file__).resolve().parent.parent / "static" / "music-queue.js").read_text(encoding="utf-8")
        script = src + "\nconst v = (id) => ({ id, title: id });\nconst ids = (q) => q.map((x) => x.id);\n" + \
            f"process.stdout.write(JSON.stringify((() => {{ {body} }})()));"
        return json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout)

    def test_add_skips_what_is_queued_and_what_is_not_a_video(self):
        out = self.run_js("""
            const q = [v("aaaaaaaaaaa"), v("bbbbbbbbbbb")];
            const added = TubeQueue.add(q, [v("bbbbbbbbbbb"), v("ccccccccccc"), v("ccccccccccc"), v("short"), null, "x"]);
            return { added: ids(added), same: ids(q) };""")
        self.assertEqual(out, {"added": ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"], "same": ["aaaaaaaaaaa", "bbbbbbbbbbb"]})

    def test_next_puts_videos_in_front_and_moves_queued_ones_there(self):
        out = self.run_js("""
            const q = [v("aaaaaaaaaaa"), v("bbbbbbbbbbb")];
            return ids(TubeQueue.add(q, [v("ccccccccccc"), v("bbbbbbbbbbb")], { next: true }));""")
        self.assertEqual(out, ["ccccccccccc", "bbbbbbbbbbb", "aaaaaaaaaaa"])

    def test_take_remove_after_and_the_cap(self):
        out = self.run_js("""
            const q = [v("aaaaaaaaaaa"), v("bbbbbbbbbbb"), v("ccccccccccc")];
            const t = TubeQueue.take(q), e = TubeQueue.take([]);
            const many = Array.from({ length: 250 }, (_, i) => v(String(i).padStart(11, "x")));
            return { item: t.item.id, rest: ids(t.rest), empty: [e.item, e.rest.length], removed: ids(TubeQueue.remove(q, 1)),
                     after: ids(TubeQueue.after(q, "aaaaaaaaaaa")), notIn: ids(TubeQueue.after(q, "zzzzzzzzzzz")),
                     last: ids(TubeQueue.after(q, "ccccccccccc")), capped: TubeQueue.add([], many).length };""")
        self.assertEqual(out, {"item": "aaaaaaaaaaa", "rest": ["bbbbbbbbbbb", "ccccccccccc"], "empty": [None, 0],
                               "removed": ["aaaaaaaaaaa", "ccccccccccc"], "after": ["bbbbbbbbbbb", "ccccccccccc"],
                               "notIn": ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"], "last": [], "capped": 200})


if __name__ == "__main__":
    unittest.main()
