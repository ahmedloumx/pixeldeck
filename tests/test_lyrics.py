"""Lyrics from LRCLIB: timed lines read from LRC, the lookup with its fallback, and the cache."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never read your .env, and keep their cache in a folder of their own.
_DIR = Path(tempfile.mkdtemp(prefix="pixeldeck-test-"))
(_DIR / ".env").write_text("BITBUCKET_WORKSPACE=acme\n")
os.environ["PIXELDECK_ENV"] = str(_DIR / ".env")
os.environ["PIXELDECK_CACHE_DIR"] = str(_DIR / "cache")

import lyrics  # noqa: E402

SONG = {"track": "Neon Harbor", "artist": "The Pixel Tides", "album": "Night Drive", "duration": 72}
RECORD = {"trackName": "Neon Harbor", "artistName": "The Pixel Tides", "duration": 72, "instrumental": False,
          "plainLyrics": "Streetlights hum\nThe harbor blinks\n", "syncedLyrics": "[00:03.20] Streetlights hum\n[00:06.60] The harbor blinks\n"}


class Fetch:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class ParseTest(unittest.TestCase):
    def test_reads_timed_lines_in_order(self):
        text = "[ar:Someone]\n[length: 1:12]\n[00:10.5] Later\n[00:01.20][00:30.00] Twice\n[01:02.345] Long\n[00:05.00]\nno stamp here\n"
        self.assertEqual(lyrics.parse_lrc(text), [[1200, "Twice"], [5000, ""], [10500, "Later"], [30000, "Twice"], [62345, "Long"]])

    def test_nothing_timed_is_nothing(self):
        self.assertEqual(lyrics.parse_lrc(""), [])
        self.assertEqual(lyrics.parse_lrc(None), [])
        self.assertEqual(lyrics.parse_lrc("just words\nno times"), [])


class LookupTest(unittest.TestCase):
    def setUp(self):
        lyrics.CACHE.clear()

    def test_an_exact_match_comes_back_timed_and_is_kept(self):
        fetch = Fetch([(200, RECORD)])
        out = lyrics.lookup(**SONG, fetch=fetch)
        self.assertEqual((out["found"], out["synced"], out["plain"].splitlines()[0]), (True, [[3200, "Streetlights hum"], [6600, "The harbor blinks"]], "Streetlights hum"))
        path, params = fetch.calls[0]
        self.assertEqual((path, params), ("/get", {"track_name": "Neon Harbor", "artist_name": "The Pixel Tides", "album_name": "Night Drive", "duration": 72}))
        self.assertEqual(lyrics.lookup(**SONG, fetch=Fetch([])), out, "a replay asks nothing")

    def test_without_an_exact_match_a_search_picks_the_same_length(self):
        far = {**RECORD, "duration": 200, "syncedLyrics": "[00:01.00] Wrong song"}
        plain_only = {**RECORD, "duration": 74, "syncedLyrics": None}
        fetch = Fetch([(404, None), (200, [far, plain_only, {**RECORD, "duration": 71}])])
        out = lyrics.lookup(**SONG, fetch=fetch)
        self.assertEqual(out["synced"][0], [3200, "Streetlights hum"], "the timed one within 3 s wins over a plain one")
        self.assertEqual(fetch.calls[1], ("/search", {"track_name": "Neon Harbor", "artist_name": "The Pixel Tides"}))

    def test_not_found_says_so_and_is_kept_for_a_while(self):
        fetch = Fetch([(404, None), (200, [{**RECORD, "duration": 300}])])
        out = lyrics.lookup(**SONG, fetch=fetch)
        self.assertEqual((out["found"], out["synced"], out["plain"]), (False, None, None))
        self.assertEqual(lyrics.lookup(**SONG, fetch=Fetch([])), out)

    def test_an_instrumental_is_found_with_no_words(self):
        out = lyrics.lookup(**SONG, fetch=Fetch([(200, {**RECORD, "instrumental": True, "plainLyrics": None, "syncedLyrics": None})]))
        self.assertEqual((out["found"], out["instrumental"], out["synced"]), (True, True, None))

    def test_a_failed_request_is_an_error_and_is_not_kept(self):
        with self.assertRaises(lyrics.LyricsError):
            lyrics.lookup(**SONG, fetch=Fetch([lyrics.LyricsError(0, "timed out")]))
        with self.assertRaises(lyrics.LyricsError):
            lyrics.lookup(**SONG, fetch=Fetch([(503, None)]))
        self.assertTrue(lyrics.lookup(**SONG, fetch=Fetch([(200, RECORD)]))["found"], "the next try asks again")

    def test_refuses_what_lrclib_would_refuse(self):
        for bad in ({**SONG, "track": ""}, {**SONG, "duration": 0}, {**SONG, "duration": 4000}, {**SONG, "artist": "x" * 301}):
            with self.assertRaises(ValueError):
                lyrics.lookup(**bad, fetch=Fetch([]))

    def test_the_route_reads_its_query(self):
        lyrics.CACHE.clear()
        fetch = Fetch([(200, RECORD)])
        out = lyrics.route(None, None, "GET", "lyrics", {"track": ["Neon Harbor"], "artist": ["The Pixel Tides"], "album": ["Night Drive"], "duration": ["72"]}, None, fetch=fetch)
        self.assertTrue(out["found"])
        self.assertIsNone(lyrics.route(None, None, "GET", "other", {}, None))
        with self.assertRaises(ValueError):
            lyrics.route(None, None, "GET", "lyrics", {"track": ["x"], "artist": ["y"], "duration": ["soon"]}, None, fetch=Fetch([]))


if __name__ == "__main__":
    unittest.main()
