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
  waiting for your review, pushed without a pull request, merged and declined. A colour-coded
  merge tree shows what branched off and came back.
- **Pull request view.** Overview with the description rendered from Markdown, the diff with
  per-file "viewed" marks, threads, commits and tasks. The header folds away so the diff gets the room.
- **Copy review comments.** One click copies every thread (or the open ones, or one thread) as
  plain Markdown, ready to paste into a Claude prompt.
- **Claude Code on a pull request.** Fix, Fix and push, Read and summarize, or Review. PixelDeck
  prepares a worktree on the branch, briefs Claude with the open threads (quoted as data, never as
  instructions) and starts it in the built-in terminal. The brief comes from templates you can
  replace with your own.
- **Terminal panel.** Real shells in tabs, drawn with WebGL for smooth scrolling. Tabs are named
  after what runs in them, can be renamed and dragged into any order, and show a small loader
  while something is working in them. Drop files on a terminal to paste their paths. The shells
  stay alive after you close the window.
- **Review banners.** When someone approves, asks for changes or comments on one of your pull
  requests, a banner drops in from the top with who it was.
- **Team and insights.** Your teammates' queues, review load and merge history.
- **Actions.** Open a pull request from a pushed branch, merge or decline your own pull request,
  delete a pushed branch. Each asks for a second click.
- **Arcade layer.** A title screen while it loads, pixel sprites, and small game moments for
  merges, deletions and new shells. Reduced motion turns them off.

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
| `PIXELDECK_WORKSPACE_ROOT` | The folder with your local clones. Default: the folder PixelDeck sits in. |
| `PIXELDECK_RELEASE_REPOS` | Glob patterns of repos whose pull requests target master or main before develop. |
| `PIXELDECK_BRIEFS_DIR` | Your own Claude brief templates (see below). |
| `PIXELDECK_CLAUDE` | Path to the `claude` CLI when it is not on PATH. |
| `PIXELDECK_BROWSER` | A command for opening links, for example `firefox --new-tab`. Default: the system browser. |
| `PIXELDECK_PORT` | The local port. Default 8642. |
| `PIXELDECK_CACHE_DIR`, `_STATE_DIR`, `_DATA_DIR`, `_CONFIG_DIR`, `PIXELDECK_SETTINGS_FILE` | Move where PixelDeck keeps its files. |

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
- Destructive actions need a second click. Merge and decline work only on your own open pull
  requests. A merge re-reads the pull request first, and Bitbucket's own merge checks still
  decide. Protected branches are never deleted, and a deleted branch is logged with the command
  that restores it.
- Fix and push tells Claude to push with `--force-with-lease` on the exact remote commit. That
  is an instruction to Claude, not a check PixelDeck enforces.

## Development

```sh
python3 -m unittest discover -s tests
```

There is no build step: the page is plain HTML, CSS and JavaScript in `static/`, read from disk
on every request, so a reload shows a change. The documentation describes each file.

## Credits

- [xterm.js](https://xtermjs.org/) and its fit, WebGL and Unicode 11 add-ons, MIT license.
- Fonts: Press Start 2P, Pixelify Sans and Host Grotesk, SIL Open Font License 1.1.

Their license texts are in [licenses/](licenses/).

PixelDeck is an independent project. It is not affiliated with or endorsed by Atlassian or
Anthropic. Bitbucket and Jira are trademarks of Atlassian; Claude and Claude Code are
trademarks of Anthropic.

## Contributing

`main` holds the released version and `develop` the next one. Open pull requests against
`develop`; see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
