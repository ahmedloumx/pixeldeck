# Installers and updates

Status: proposal, partly built. Code changes 1 to 9 below are built, and so is a Windows
installer made by hand (`packaging/build.py`, see the README). Not built yet: the macOS and
Linux packages, the release workflow, the website, signing and the in-app updates. This
document describes what we want a colleague to experience, what has to change in the code to
get there, and the order we build it in.

Decided:

- PixelDeck is shared as an executable per operating system, not as a git clone.
- A website of ours serves the downloads and the update information.
- The first users are colleagues on Windows, macOS and Linux who do not want to set up Python.

## Expected outcome

What a colleague sees, from download to the next update.

1. **Download.** The website shows one button per system: Windows, macOS, Linux. Each gives
   one file. No Python, no pip, no git clone, no `.env` to copy.
2. **Install.** Double-click, a few seconds, no administrator rights. PixelDeck shows up where
   apps normally live: the Start menu on Windows, Applications on macOS, the app grid on Linux.
   A `pixeldeck` command is on the PATH too, so `pixeldeck --stop`, `pixeldeck --status` and
   `pixeldeck --propose` (what an agent runs to ask for a pull request) work from any terminal.
3. **First start.** A setup screen inside the app asks for the Atlassian email, the API token
   and the workspace, and for the folder that holds the local clones. It links to the token page
   with the list of scopes to tick. A Check button calls each Bitbucket endpoint PixelDeck needs
   and names any scope that is missing ("add User: Read"), instead of a bare 403 later. Nobody
   edits a file by hand.
4. **Daily use.** Same app as today. On Windows no console window flashes when PixelDeck runs
   git or checks a program's version.
5. **Update.** PixelDeck checks the website at start and every few hours. When a new version
   exists, a notification says "PixelDeck 1.5.0 is ready" with a link to the release notes.
   One click: it downloads the new version, checks it is genuine (see below), stops the server,
   offers to bring the open terminals back, installs, and reopens on the new version. The page
   and the server can never run different versions, so errors like `no route GET repos` after
   an update cannot happen.
6. **Safe updates.** An update is installed only when its signature and its SHA-256 match.
   A tampered file, a tampered update list or an older version is refused with a clear message.
   This matters because PixelDeck holds a Bitbucket token and starts shells and Claude Code:
   whoever could push an update could run code on every colleague's machine.
7. **Uninstall.** From the system's usual place (Apps settings, the Trash, deleting the
   AppImage). Settings and the token stay unless the person ticks "also remove my settings".

Still needed on the machine, and checked by the setup screen and Settings, Connected apps:
git with access to the Bitbucket repositories, and the Claude Code CLI for the Claude actions.

### Acceptance checks

- On a Windows 11 machine without Python, a colleague goes from download to their pull
  request queue in under five minutes, token creation included.
- The same on a recent macOS and on a recent Fedora or Ubuntu.
- No console window appears during normal use on Windows.
- Version N updates itself to N+1 from the notification with no manual step, and the server
  reports N+1 afterwards.
- A modified installer or a modified update list is refused.
- Running from source (`python3 pixeldeck.py`) still works for development.

## What changes in the code

These are needed before any executable works. Each one is based on how the code works today.

1. **One program, several modes.** `pixeldeck.py` starts the server as
   `python server.py` and the GTK window as `python app_window.py`, by file path. A packaged
   app has no such files. The launcher must start itself with a flag instead
   (`pixeldeck --server`, `pixeldeck --window`); `--server` exists already.
   Done: a packaged app starts its server as `pixeldeck --server`. On Windows the bundle has
   two programs, `pixeldeck.exe` (console: terminals, agents, the server) and `pixeldeckw.exe`
   (no console: the Start menu). `--window` is not needed: the packaged app uses a browser
   window on every system (see Packaging).
2. **Bundled files.** `static/` and `briefs/` are read relative to `config.APP_DIR`. In a
   packaged app they come from the bundle's resource folder. Done: `config.APP_DIR` is the
   bundle folder in a packaged app.
3. **Settings out of the program folder.** The `.env` sits next to the code today, so an
   update would replace or lose it. It moves to the per-user config folder that `config.py`
   already knows (`%APPDATA%\PixelDeck`, `~/Library/Application Support/PixelDeck`,
   `~/.config/pixeldeck`). A `.env` next to the code keeps working for source runs, and the
   setup screen offers to import one it finds. Done, with an import from a path you give
   rather than a search: Settings, Setup writes the file (`config.save` keeps its comments
   and other keys).
4. **The token in the system's credential store** (Windows Credential Manager, macOS
   Keychain, Secret Service on Linux) instead of a plain text file, with the file as a
   fallback where no store exists. Done for Windows Credential Manager (`credentials.py`,
   through ctypes, nothing to install). Keychain and Secret Service are not written yet, so
   macOS and Linux keep the file. The environment and the file win over the store, so a
   token set by hand still works.
5. **The clones folder.** `PIXELDECK_WORKSPACE_ROOT` defaults to the folder above the code,
   which means nothing for an installed app. The setup screen asks for it. Done; until it is
   set, the packaged app uses the home folder.
6. **A version number.** Done: `version.py` holds it (`0.1.0` for now), Settings shows it and
   `/healthz` returns it. Also done: the build sets it from the `v*` tag (`build-info.json`
   in the bundle), and the User-Agent says `PixelDeck/<version>`.
7. **No stale server.** Done. After `git pull` the browser loads the new page from disk while
   the old server keeps running. The launcher now compares a fingerprint of the Python files the
   server started with against the files on disk, and restarts the server when they differ. It
   never does while a terminal is open or an agent waits on a proposal, since a stop cuts them
   off; then the launcher and the page say how to finish. A packaged app gets the same check
   from its version.
8. **Windows: no console windows.** Done. The server runs without a console, so every
   `subprocess.run` of a console program (git, `claude --version`) opened a console window.
   They pass `CREATE_NO_WINDOW` on Windows.
9. **Windows: one server per port.** Done. Python's `HTTPServer` sets `SO_REUSEADDR`, which on
   Windows let a second PixelDeck server bind the same port while the first ran; `--stop` then
   stopped only one of them. The server no longer sets it on Windows and takes the port before
   anything else, and the launcher writes the pid file for the server that answers.

## Packaging (proposal, to be tried)

| System  | File                                 | How                                                                                                      |
|---------|--------------------------------------|----------------------------------------------------------------------------------------------------------|
| Windows | `PixelDeck-Setup-<version>-x64.exe`  | PyInstaller (one-folder mode) plus an Inno Setup installer, per-user install, includes pywinpty.          |
| macOS   | `PixelDeck-<version>.dmg`            | PyInstaller `.app`, Apple Silicon and Intel builds (or one universal build).                              |
| Linux   | `PixelDeck-<version>-x86_64.AppImage` | PyInstaller plus appimagetool. Packages for dnf or apt can follow if people ask.                         |

Notes:

- One-folder mode rather than one-file: one-file unpacks itself into a temp folder at each
  start, which is slower and is flagged by antivirus more often.
- The window stays as today: Edge, Chrome or Brave in app mode. The Linux GTK window needs the
  system's PyGObject and WebKitGTK, which do not bundle well; on Linux the packaged app uses the
  browser window, and the GTK window remains for source runs.
- **Signing.** Without a Windows code signature, Windows shows "Windows protected your PC" on
  first run. Without an Apple Developer ID signature and notarization, macOS refuses to open
  the app. Both cost money and need an owner (see open questions). Linux needs no signature.

## Build and release (proposal)

A GitHub Actions workflow runs when a `v*` tag is pushed:

1. Run the tests on each system.
2. Build Windows, macOS (both chips) and Linux in parallel.
3. Sign where we have certificates.
4. Compute the SHA-256 of each file.
5. Upload the files to a GitHub release.
6. Write and sign the update list, and publish it to the website.

Binaries can stay on GitHub Releases, which is free and fast, with the website linking to
them. Or the website hosts them too. The update list is the only file the app trusts, and it
is signed, so where the binaries live does not change the security.

## The website and the update list

The website is a static site: a download page, release notes and one update list per channel
(`stable.json`, later `beta.json`). Example:

```json
{
  "version": "1.5.0",
  "published": "2026-12-01",
  "notes": "https://<website>/releases/1.5.0",
  "files": {
    "windows-x64": { "url": "https://.../PixelDeck-Setup-1.5.0-x64.exe", "sha256": "…", "size": 41234567 },
    "macos-arm64": { "url": "https://.../PixelDeck-1.5.0-arm64.dmg", "sha256": "…", "size": 39876543 },
    "macos-x64":   { "url": "https://.../PixelDeck-1.5.0-x64.dmg", "sha256": "…", "size": 40123456 },
    "linux-x64":   { "url": "https://.../PixelDeck-1.5.0-x86_64.AppImage", "sha256": "…", "size": 45678901 }
  },
  "signature": "ed25519 signature of everything above"
}
```

How the app uses it:

- It fetches the list over HTTPS at start and every few hours. The request carries nothing
  about the user: no token, no workspace, no name.
- It checks the signature with a public key built into the app. The private key lives in the
  release workflow's secrets (or offline), never on the web server, so a hacked website
  cannot ship an update.
- It refuses any version lower than the installed one.
- After the download, it checks the size and the SHA-256 before running anything.
- It installs the update like this:
  - Windows: stop the server, run the new installer silently, reopen.
  - macOS: replace `PixelDeck.app`, reopen.
  - Linux: replace the AppImage, reopen.
- "Skip this version" and "Remind me later" are respected.

An existing updater library for PyInstaller apps, such as tufup (built on The Update
Framework), handles signing, expiry and rollback protection for us. We should try it before
writing our own.

## Order of work

1. **Ready to package.** Code changes 1 to 5, and a Windows build made by hand and shared by
   link with two or three colleagues. The code and the build are done; sharing is next.
2. **All three systems.** The release workflow builds Windows, macOS and Linux on a tag.
3. **Updates.** The website, the signed update list, and the in-app check and install.
4. **Signing.** Windows signing and macOS notarization, once we know who pays for and owns
   the certificates.

## Open questions

- The website's domain and host.
- Windows signing (for example a signing service or a certificate) and the Apple Developer
  account: who pays, and whose name is on them.
- Who keeps the update signing key, and how we rotate it.
- The oldest systems we support (Windows 10 or only 11, which macOS version).
- Linux: the AppImage only, or packages for dnf and apt too.
- Updates: always ask, or install quietly and say so afterwards.
