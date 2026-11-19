# Setting up Claude Code, Jira and Spotify without editing a file

Status: plan only.

The first plan promised "Nobody edits a file by hand". Pull request #18 kept that promise for
Bitbucket, with the Setup tab and `config.save`. This plan does the same for Claude Code,
Jira and Spotify, and builds on #18.

## The goal, in one check

Today, 10 places send people to a file or to a variable name (counted on #18's branch):

| Where | What it says today |
|---|---|
| Queue, no connection (`app.js`) | put a token in `.env` as `BITBUCKET_TOKEN=` |
| Merge popup (`app.js`) | set `JIRA_TOKEN` in the settings file |
| Connections, Jira card (`hub.js`) | set `JIRA_SITE` and `JIRA_TOKEN` |
| Connections, Claude card (`hub.js`) | point `PIXELDECK_CLAUDE` at it |
| Connections, Spotify card (`hub.js`, two lines) | missing `SPOTIFY_CLIENT_ID`; put the Client ID in it |
| Music setup, step 3 (`music.js`) | copy `SPOTIFY_CLIENT_ID=` into the settings file |
| Server errors (`jira.py`) | `JIRA_SITE is not set`, `set JIRA_TOKEN (and JIRA_EMAIL ...)` |
| Server error (`launcher.py`) | `set PIXELDECK_CLAUDE to its path` |
| Server errors (`spotify.py`) | `Set SPOTIFY_CLIENT_ID in PixelDeck's .env first` |
| Server errors (`bitbucket.py`) | `No BITBUCKET_TOKEN in <path>` |

**Acceptance:** none of these remains. Each one becomes a plain sentence and a button that
opens the right Setup card. The settings file stays, for people who like it and for the
advanced keys, but nothing requires it.

## How every card works

Setup gets one card per connection, in this order:

1. Bitbucket (required, done in #18);
2. your clones folder (required, done in #18);
3. Claude Code (recommended);
4. Jira (optional);
5. Spotify (optional);
6. agents (optional).

On a first start only the two required cards are open, and the optional ones are folded
underneath.

Each card has:

- **A status pill:** Ready, Needs attention, or Not set up.
- **One sentence on what it is for.**
- **Only the fields PixelDeck cannot work out itself,** filled in where it can guess.
- **Links that open the exact page,** and Copy buttons for anything to paste elsewhere.
- **Check**, which tries what PixelDeck will really do and says what is wrong in plain words,
  with the fix next to it.
- **Save**, which writes the settings file through `config.save` and puts tokens in the
  system's credential store (#18).

When a connection breaks later, for example a token expires or is revoked, the page shows a
notice with a button that opens that card. The person never has to work out what went wrong.

## Claude Code

What PixelDeck can find out by itself:

- **Where `claude` is.** PixelDeck looks in:
  - `PIXELDECK_CLAUDE`;
  - the PATH, including the login shell's PATH on macOS and Linux (see
    [packaging-macos-linux.md](packaging-macos-linux.md));
  - where the official installers put it: `~/.local/bin/claude` from the native installer
    (`claude.exe` under `%USERPROFILE%\.local\bin` on Windows), and the Homebrew folders.
- **Its version:** `claude --version`.
- **Whether it is signed in, and with which profile:** `claude auth status --json`. It reports
  `loggedIn`, the account's email, the plan and `configDirectory`. PixelDeck never reads
  Claude's own files.

What the card shows:

- **Ready:** "Claude Code 2.x, signed in as you@company.com, profile
  `~/.claude`."
- **Not installed:** the official install command for this system, with Copy, and "Install in
  a PixelDeck terminal". That button opens a terminal tab with the command typed but not run;
  the person presses Enter. The commands come from Claude Code's setup page:
  - macOS and Linux: `curl -fsSL https://claude.ai/install.sh | bash`;
  - Windows: `irm https://claude.ai/install.ps1 | iex`.
  Then "Check again".
- **Installed but signed out:** "Sign in", which opens a terminal running `claude auth login`.
- **Found somewhere unusual:** a path field that is checked with `--version` before it is saved.
- **Profile:** which Claude profile the Claude buttons use. By default PixelDeck uses the one
  `configDirectory` names. Setting another one saves it as `PIXELDECK_CLAUDE_CONFIG_DIR`, and
  every terminal PixelDeck starts runs with it as `CLAUDE_CONFIG_DIR`. Today the terminals
  inherit whatever the environment has, so a person with two profiles can't tell which one a
  pull request's Claude run uses.

## Agents asking to open pull requests

A card titled "Let agents ask you to open pull requests". It shows the exact line to put in an
agent's instructions (for Claude Code, its `CLAUDE.md`), with the right command:

- `pixeldeck --propose` for the packaged app;
- the full path of `pixeldeck.py` for a git clone.

It has a Copy button. PixelDeck does not write anyone's `CLAUDE.md` itself.

## Jira

What PixelDeck can fill in:

- **The site.** Any Jira link pasted in is cut down to its site
  (`https://acme.atlassian.net/browse/ABC-1` becomes `https://acme.atlassian.net`).
  Typing `acme` is enough.
- **The email,** taken from the Bitbucket card.
- **The ticket keys,** found in branch names and pull request titles. PixelDeck already finds
  them.

The token needs a sentence of explanation. Atlassian makes a token for one app at a time, so
Jira needs its own token, and the Bitbucket token is never sent to Jira (as today). The card
links to the token page.

**Check** calls exactly what PixelDeck uses:

- `GET /rest/api/3/myself`, on the site or through Atlassian's gateway;
- reading an issue and its transitions, on one ticket key found in your pull requests.

It names the failing step: "the token cannot read issues", "the site does not exist", and so
on.

**Ticket keys** (today `TICKET_PREFIXES`): Check lists the projects the token can see, as
checkboxes, with the ones seen in your branches already ticked. That needs one more call, the
project search, which is read only.

To research, not promised:

- **The scope names** for a scoped Jira token, so the card can list them as the Bitbucket card
  does.
- **Token expiry.** Atlassian API tokens now expire; check the longest lifetime, and whether
  the expiry date can be read, so the card can warn before it happens.
- **"Sign in with Atlassian"** (OAuth 2.0). It would remove token copying for Bitbucket and Jira
  both. But it needs an OAuth app that the project owns, a secret kept inside a desktop
  program, and Atlassian's rules for apps used by people outside the owner's organization.
  It is a separate decision, after the cards above.

## Spotify

The hard limit, from Spotify's quota page:

- An app in development mode works for its owner, who must have Premium, plus up to 5 people
  the owner adds by email.
- More users need "extended quota", which Spotify grants only to registered businesses with at
  least 250,000 monthly users.

So PixelDeck cannot ship one Spotify login for everyone. That leaves two ways to set it up,
and both end in the same paste field:

- **On your own:** create your own Spotify app (about two minutes), as today.
- **As a team of up to 6:** one person creates the app and adds the others by email. Everyone
  pastes the same Client ID. The redirect URI is the same on every computer
  (`http://127.0.0.1:8642/spotify/callback`) as long as nobody changes the port, so one app
  serves everyone.

What changes:

- **Step 3 of the Music setup** becomes a Client ID field with Save. It checks the format,
  saves the ID, and starts Connect right away. No file, no restart.
- **Premium comes first.** The card says, before anything else, that Spotify requires Premium
  for the app's owner, and for play, pause, skip, seek and volume.
- **Errors in plain words.** When Spotify sends the person back with an error, PixelDeck
  explains it. The main cases:
  - a redirect URI that doesn't match: shown with the exact URI to add, and Copy;
  - a user who is not on the allow-list: "ask the app's owner to add your Spotify email
    under User Management".
- **A changed port:** if the port is not the one the redirect URI was registered with, the card
  warns before Connect fails.

## Order of work

All of this can be built and tested on Windows, on top of #18:

1. Turn the 10 messages into plain sentences with Setup buttons.
2. The Claude Code card, including the profile.
3. The Jira card, with its Check and ticket keys.
4. The Spotify paste field and its error explanations.
5. The agents card.

Each step comes with tests for the new routes and a pass through the page. Before the pull
request, a search finds none of the 10 messages left.
