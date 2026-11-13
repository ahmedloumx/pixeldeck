# PixelDeck

A retro desk for your Bitbucket Cloud pull requests, with Claude Code running next to the diff.

PixelDeck is a small local app. It shows the pull requests that need you, the diffs and review
threads, your team's queue and merge history, and a built-in terminal where Claude Code can fix
review comments on the right branch. It looks and moves like an 8-bit game, and gets out of the
way when you want it to.

Full documentation, with the architecture, security model and measured performance:
[docs/pixeldeck-documentation.html](docs/pixeldeck-documentation.html) (open it in a browser).

## What it does

- **Queue.** Your pull requests by state: needs you, changes requested, open, waiting, approved,
  waiting for your review, pushed without a pull request, merged and declined. A date filter
  (today, 7 days, 30 days, or a range) narrows any list by when it was opened, merged or
  declined. A colour-coded merge tree shows what branched off and came back.
- **Pull request view.** Overview with the description rendered from Markdown, the diff with
  per-file "viewed" marks, threads, commits and tasks. The header folds away so the diff gets the room.
  **Copy link** puts the pull request's address on the clipboard in one click.
- **Copy review comments.** One click copies every thread (or the open ones, or one thread) as
  plain Markdown, ready to paste into a Claude prompt.
- **Claude Code on a pull request.** Fix, Fix and push, Read and summarize, or Review. PixelDeck
  prepares a worktree on the branch, briefs Claude with the open threads (quoted as data, never as
  instructions) and starts it in the built-in terminal. The brief comes from templates you can
  replace with your own.
- **Terminal panel.** Real shells in tabs, drawn with WebGL for smooth scrolling. Tabs are named
  after what runs in them, can be renamed and dragged into any order, and show a small loader
  while something is working in them. Drop files on a terminal to paste their paths. The shells
  stay alive after you close the window, and reopening it brings their screens back as they were.
- **Pull requests an agent asks to open.** Instead of opening a pull request itself, an agent
  such as Claude Code runs `pixeldeck.py --propose` and waits. A notification shows the branch,
  its target and its size: **Proceed** opens it as proposed, **Review** opens the popup (target
  branch, close the branch after the merge, the diff), **Decline** says no. See below.
- **Notifications.** Each refresh is compared with the last one: a pull request opened, an
  approval, changes asked, new comments, a review asked of you, a merge or a decline. News spins
  in as a slot machine banner (one per burst, with a count) and lands behind the bell with its
  history. Silence it per kind, keep only your own pull requests, or snooze it. Your own actions
  never notify you.
- **Status bar.** Bitbucket API calls used this hour, when your pull requests were read and the
  next read, the connection, the bell, settings and the guide.
- **Settings.** What PixelDeck is connected to (Bitbucket, Jira, the Claude Code CLI, git and
  terminals), what notifies you, and your look: accent colour, number font, motion, mascots.
- **Team and insights.** Your teammates' queues, review load and merge history. Insights pick a
  range, from 7 days to 120; a month or less is charted per day.
- **Repos.** A tab beside the queue: the project's repositories with a search, and for each one
  its code at any branch (folders, files with line numbers, the README), its branches, its recent
  commits and its open pull requests. Read only; code and commits are kept once read, so going
  back costs no API calls.
- **Panel copies.** Copy the Queue or Insights panel: each copy keeps its own person, section
  and range, so two people or two ranges sit side by side. Copies are saved with the layout and
  open in their own window too.
- **Reviews.** Approve or request changes on a teammate's open pull request, and take either
  back, from the pull request's toolbar.
- **Actions.** Open a pull request from a pushed branch; sync, merge or decline your own pull
  request; delete a pushed branch. Each asks for a second click in place. When your branch is behind its
  target, a bar says by how much and offers **Sync now**, like Bitbucket's.
- **Merge popup.** Like Bitbucket's: the strategies the target branch allows (merge commit,
  squash, fast forward and the rebase ones, its default picked), the commit message Bitbucket
  would write, closing the branch, and the ticket from Jira with its status. Pick the status it
  should move to and PixelDeck moves it once the merge is through (needs `JIRA_TOKEN`).
- **Arcade layer.** A title screen while it loads, pixel sprites, and small game moments for
  merges, deletions and new shells. Two mascots: the green slime does AI work (it comes when you
  start Claude and builds its terminal tab), the blue bot does PixelDeck's own work (it stands by
  a button while a merge, sync, decline or delete runs) and its eyes follow your pointer. They
  race each other along the loading bar. Reduced motion, or Calm in the settings, turns the
  motion off.
- **Arcade dock.** Tetris, Snake and Flappy, played with the slime and the bot, open beside the
  terminal from the Layout menu. While one of your pull requests waits on reviewers or on its
  merge, the bot on the terminal bar now and then suggests a round, with a line it has not said
  lately. It never takes focus from a terminal; switch it off in the Layout menu.
- **Music.** A retro boombox for Spotify: pixelated album art, a green LCD with the title
  scrolling, a block progress bar to seek, chunky transport keys, volume blocks, an equalizer,
  your devices and your playlists as cassettes; a mini player sits in the status bar. It drives
  your own Spotify app (desktop or phone) through Spotify's Web API. Each person uses their own
  Spotify app, see below. **Preview** in the player shows it with made-up music.
- **Guide.** A short tour on the first start, a spotlight on each part with a mascot telling it.
  The **?** in the status bar brings it back.

## Requirements

| | Linux | macOS | Windows |
|---|---|---|---|
| Python 3.10 or newer (tested on 3.12 and 3.14) | yes | yes | yes |
| git on PATH | yes | yes | yes |
| Claude Code CLI | optional, for the Claude actions | optional | optional |
| App window | its own GTK window when PyGObject, GTK 4 and WebKitGTK 6 are installed; otherwise Chrome, Chromium, Edge or Brave in app mode | Chrome, Edge or Brave in app mode, else the default browser | Edge (built in) in app mode |
| Terminal panel | built in | built in | `py -m pip install pywinpty` |

The server uses the Python standard library only. The one extra package is `pywinpty`, on
Windows, for the terminal panel. You also need a Bitbucket Cloud account and an API token.

**Platform status.** PixelDeck is developed and tested on Linux (Fedora, GNOME on Wayland).
The macOS and Windows code paths are written and covered by import tests that pretend to be
those systems, but they have not run on a real Mac or Windows machine yet. Reports and fixes
are welcome.

## Quick start

```sh
git clone https://github.com/ahmedloumx/pixeldeck.git
cd pixeldeck
cp .env.example .env        # Windows: copy .env.example .env
```

Edit `.env` and set at least `BITBUCKET_EMAIL`, `BITBUCKET_TOKEN` and `BITBUCKET_WORKSPACE`, and
`PIXELDECK_WORKSPACE_ROOT` (the folder that holds your clones). Then start it:

| | Start | Stop |
|---|---|---|
| Linux | `python3 pixeldeck.py` or `./run.sh` | `python3 pixeldeck.py --stop` |
| macOS | double-click `PixelDeck.command`, or `python3 pixeldeck.py` | `python3 pixeldeck.py --stop` |
| Windows | double-click `PixelDeck.cmd`, or `py pixeldeck.py` | `py pixeldeck.py --stop` |

On Linux, `./install-desktop.sh` adds PixelDeck to the app grid and pins it to the GNOME dock.

Closing the window leaves the server and its terminals running, so a Claude run is never cut
off. `--status` tells you whether it runs; `--server` runs the server in the console for debugging.

### The API token

Create an Atlassian API token with Bitbucket scopes at
<https://id.atlassian.com/manage-profile/security/api-tokens>. Give it the least it needs:
read access to your user, the workspace, repositories and pull requests, plus pull request write
if you want to open, merge and decline pull requests from PixelDeck. Nothing more: PixelDeck never needs
admin or delete scopes. Deleting a pushed branch and the Claude fixes go through git with your own
git credentials, not through the token.

The token lives only in `.env`, which `.gitignore` keeps out of git. PixelDeck sends it to
`api.bitbucket.org` and nowhere else.

## Configuration

Everything is in `.env`; [.env.example](.env.example) lists every key with a comment. A value
in the real environment wins over the file, and the file is re-read when it changes.

| Key | What it is |
|---|---|
| `BITBUCKET_EMAIL`, `BITBUCKET_TOKEN` | Your Atlassian account email and API token. Required. |
| `BITBUCKET_WORKSPACE` | The workspace slug, as in `bitbucket.org/<workspace>/<repo>`. Required. |
| `BITBUCKET_PROJECT` | Start scoped to one project key. Empty: every project. |
| `JIRA_SITE`, `TICKET_PREFIXES` | Turn ticket keys such as `ABC-123` in branches and titles into Jira links. |
| `SPOTIFY_CLIENT_ID` | The Client ID of your own Spotify app, for the music player (see below). |
| `JIRA_TOKEN`, `JIRA_EMAIL` | An Atlassian API token with Jira access, to show and move a ticket's status in the merge popup. Classic and scoped tokens both work; the Bitbucket token is never sent to Jira. `JIRA_EMAIL` defaults to `BITBUCKET_EMAIL`. |
| `PIXELDECK_WORKSPACE_ROOT` | The folder with your local clones. Default: the folder PixelDeck sits in. |
| `PIXELDECK_RELEASE_REPOS` | Glob patterns of repos whose pull requests target master or main before develop. |
| `PIXELDECK_BRIEFS_DIR` | Your own Claude brief templates (see below). |
| `PIXELDECK_CLAUDE` | Path to the `claude` CLI when it is not on PATH. |
| `PIXELDECK_BROWSER` | A command for opening links, for example `firefox --new-tab`. Default: the system browser. |
| `PIXELDECK_PORT` | The local port. Default 8642. |
| `PIXELDECK_CACHE_DIR`, `_STATE_DIR`, `_DATA_DIR`, `_CONFIG_DIR`, `PIXELDECK_SETTINGS_FILE` | Move where PixelDeck keeps its files. |

### Letting an agent open pull requests through you

```sh
python3 pixeldeck.py --propose --repo shop-api --branch feat/ABC-12-cart \
    --title "feat: cart totals (ABC-12)" --description-file pr.md
```

The command waits up to 100 seconds (`--wait` to change it) and then exits with **0** and the
pull request's link when you open it, **2** if you declined, **3** if you have not answered yet
(run the same command again: it picks up the same proposal), or **4** if PixelDeck is not
running. `--dest` picks another target branch and `--keep-branch` keeps the branch after the
merge. Approval is refused if the branch moved since it was proposed, so what opens is what you
saw. Put a line in your agent's instructions (for Claude Code, its `CLAUDE.md`) telling it to
open pull requests this way.

### The music player and your Spotify app

Spotify gives each app its own Client ID, so everyone who runs PixelDeck uses an app of their own:

1. At [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard), create an app
   (any name) and tick **Web API**.
2. Add the redirect URI `http://127.0.0.1:8642/spotify/callback` (your port, if you changed
   `PIXELDECK_PORT`). Spotify allows plain http only for `127.0.0.1`, never `localhost`.
3. Put the app's Client ID in `.env` as `SPOTIFY_CLIENT_ID=`. No client secret: PixelDeck signs in
   with PKCE, the flow made for apps that cannot keep one.
4. Press **Connect** in the Music panel and sign in to Spotify in your browser.

Spotify Premium is needed for the app's owner (in development mode) and for play, pause, skip,
seek and volume. In development mode an app works for its owner and up to 5 people added under
User Management. The player shows the exact redirect URI to register, with a copy button.

### Claude brief templates

The text Claude receives is built from `briefs/head.md` plus one of `summarize.md`, `review.md`,
`fix.md` and `fix_push.md`. To use your own house rules (a review rubric, commit conventions),
copy any of them to a folder of your own, edit them, and set `PIXELDECK_BRIEFS_DIR` to that
folder. Files you leave out come from `briefs/`. Placeholders such as `{source}`, `{dest}`,
`{cwd}` and `{ticket}` are filled in; the open threads are appended after the template.

## Security

- The server listens on `127.0.0.1` only, refuses other Host and Origin headers, and requires a
  secret, made at each start, on every API call. Another website open in your browser cannot drive it.
- Pull request text is untrusted: descriptions and comments are rendered by an escape-first
  Markdown renderer that only builds a fixed set of tags, and the Claude brief fences reviewer
  text as data. Tests feed both hostile input.
- Destructive actions ask for a second click in place (an agent's proposal needs your one click
  on Proceed). Merge and decline work only on your own open pull
  requests. A merge re-reads the pull request first, and Bitbucket's own merge checks still
  decide. Protected branches are never deleted, and a deleted branch is logged with the command
  that restores it.
- The propose key an agent uses can only ask and read the answer; only the page can approve.
  That makes the approval a cooperative step, not a barrier: any program running as you can
  reach PixelDeck, and your `.env`, anyway.
- Fix and push tells Claude to push with `--force-with-lease` on the exact remote commit. That
  is an instruction to Claude, not a check PixelDeck enforces.
- The settings report says whether a token is set and where the settings file is, never the
  token. The Jira site comes from `.env` only, never from the page, and a ticket key must look
  like one before it reaches a URL.
- The routes the page polls (events, status) answer from memory and never call Bitbucket.
- The Repos panel only reads (GET) and checks every repository, commit and path before it
  reaches a URL; file contents are shown as text, never as markup.
- Spotify tokens stay on the server, in the state folder with mode 600, and never reach the page
  or a log. The sign-in comes back to `/spotify/callback` with a single-use state that expires in
  10 minutes; album art is fetched by the server from Spotify's image host only.

## Development

```sh
python3 -m unittest discover -s tests
```

There is no build step: the page is plain HTML, CSS and JavaScript in `static/`, read from disk
on every request, so a reload shows a change. The documentation describes each file.

## Credits

- [xterm.js](https://xtermjs.org/) and its fit, WebGL, Unicode 11 and serialize add-ons, MIT license.
- Fonts: Press Start 2P, Pixelify Sans and Host Grotesk, SIL Open Font License 1.1.
- Numbers use the ArcadeClassic font by Pizzadude when it is installed on your computer. Its
  license allows personal use only, so PixelDeck does not ship it; without it, Press Start 2P
  stands in.

Their license texts are in [licenses/](licenses/).

PixelDeck is an independent project. It is not affiliated with or endorsed by Atlassian or
Anthropic. Bitbucket and Jira are trademarks of Atlassian; Claude and Claude Code are
trademarks of Anthropic.

## Contributing

`main` holds the released version and `develop` the next one. Open pull requests against
`develop`; see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
