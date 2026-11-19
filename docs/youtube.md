# YouTube: queue, playlists and sign-in

How the YouTube view reads YouTube, plays a queue, and signs in with Google, and why it is built
that way. The view itself (the switch, the pixel TV, search) is described in the README.

## Modules

| File | What it does |
|---|---|
| `youtube.py` | Links (video and playlist ids), search, playlists, your lists, thumbnails. Routes `youtube-*`. |
| `youtube_signin.py` | The Google sign-in: your OAuth client, PKCE, the callback page, the tokens. |
| `oauth.py` | What any OAuth sign-in needs here: PKCE, the pending sign-ins, the private token file. |
| `static/music-youtube.js` | The YouTube view: search, results, your lists, Up next, recently played, the bar's mini player. |
| `static/music-tv.js` | The pixel TV around YouTube's own player, docked or floating; talks to the player by postMessage. |
| `static/music-queue.js` | The queue's rules, as pure functions (tested in node). |

`youtube.py` asks `youtube_signin.ACCOUNT` for a token and knows nothing else of the sign-in.
`youtube_signin.py` does not import `youtube.py`.

## How the Data API is called

One way for every call (`Client._get`): with the sign-in's token when you are signed in, else with
the API key. A token Google refuses (401) is renewed once and the call asked again. A sign-in Google
ended falls back to the key when there is one.

| Question | Route | Data API | Cost (units) |
|---|---|---|---|
| A search | `youtube-search?q=` | `search.list` | 100 |
| A pasted link | `youtube-search?q=` | none: read here | 0 |
| A playlist's videos | `youtube-playlist?list=` | `playlistItems.list` | 1 per 50, 4 at most |
| Your playlists | `youtube-mine?what=playlists` | `playlists.list?mine=true` | 1 per 50, 4 at most |
| Your liked videos | `youtube-mine?what=liked` | `videos.list?myRating=like` | 1 per 50, 4 at most |
| The channels you follow | `youtube-mine?what=subscriptions` | `subscriptions.list?mine=true` | 1 per 50, 4 at most |

Every answer is kept in memory for ten minutes. Pages, not items, bound a list's cost, so a
playlist full of private videos cannot page on. A channel's uploads are the playlist `UU` + the
rest of its id (`UC...`), so opening one costs no extra call. Answers about your account are kept
under a mark of the signed-in account, so they are never served to the next one.

## The queue

- Kept per browser in `prdesk.youtube.v1` (with the recent list), 200 videos at most, each once.
- When a video ends, the first queued one plays, unless Autoplay is off. A video that cannot play
  here (the player's `onError`: its owner's choice, removed, private) ends it too: the queue moves
  on and the panel names it. Playlists, liked videos and uploads are not filtered for embedding
  the way a search is (`videoEmbeddable=true`), so this happens.
- The next video goes into the player already there, with the player's `loadVideoById`. Chromium
  holds back a new player's video in a hidden window, but the same media element plays on: checked
  in a minimized Edge window, where the next video started and played.
- A pasted playlist link plays its video and queues the rest of the playlist right after it. A
  video started by hand leaves the queue.

## Sign-in with Google

- **Your own client.** PixelDeck ships no Google client. You make one of type Desktop app in the
  Cloud project that has the API on, and paste its JSON file. A Web application client is refused
  by name. The client ID goes to the settings file; the secret to the credential store when there
  is one (Google calls a Desktop app's secret not confidential, but its token endpoint takes it).
- **The flow.** Authorization Code with PKCE (S256), `access_type=offline` and `prompt=consent`
  so a refresh token comes each time. The system browser goes to
  `accounts.google.com/o/oauth2/v2/auth` (the only page of that host `opener.py` lets out) and
  comes back to `http://127.0.0.1:<port>/youtube/callback`. A sign-in link works once, for ten
  minutes (`oauth.Pending`).
- **Scope.** `youtube.readonly` only. If Google does not grant it, there is no sign-in.
- **Tokens.** In `<state folder>/youtube.json`, mode 600, tied to the client that earned them. A
  renewal Google refuses with `invalid_grant` or `invalid_client` forgets them, and the page says
  why. Sign out and removing the client tell Google to revoke the grant, best effort.
- **The 7 days.** While the project's OAuth consent screen is in Testing (External), Google ends a
  sign-in after 7 days. The setup guide says to publish the app (In production); Google then calls
  it unverified, which is fine for your own client.

## Not checked against Google

- A real sign-in round trip, and so whether Google takes the callback's path
  (`/youtube/callback`) on a loopback redirect: its docs show the loopback form without a path and
  do not say. Checked live: the token endpoint refuses a made-up client with `invalid_client`, and
  the page then signs out and says so.
- `playlistItems.list`, `playlists.list`, `videos.list?myRating=like` and `subscriptions.list`
  with real answers. Their shapes come from the API reference; the tests use fakes.
