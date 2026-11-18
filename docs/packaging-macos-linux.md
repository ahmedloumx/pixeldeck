# macOS and Linux packages

Status: plan only. Nothing here is built.

This refines the Packaging table of [installer-and-updates.md](installer-and-updates.md), and
does not change the formats it chose: a `.dmg` for macOS and an AppImage for Linux. It builds on
the Windows packaging from pull request #18. The builds run in the pipeline described in
[release-pipeline.md](release-pipeline.md).

Every system behaviour below that nobody has run yet is marked **to verify**, with where it
gets checked.

## What a person gets

### macOS

- **Download:** one `.dmg` per chip: `PixelDeck-<version>-arm64.dmg` for Apple Silicon and
  `PixelDeck-<version>-x64.dmg` for Intel.
- **Install:** open it and drag PixelDeck to Applications.
- **First start:** the Setup screen from #18, the same as on Windows.
- **Terminal command:** About offers "Install the `pixeldeck` command". It makes the link
  `~/.local/bin/pixeldeck`, which needs no administrator password; the Claude Code installer
  uses the same folder. If that folder is not on the PATH, it says which line to add.
- **Uninstall:** drag PixelDeck to the Bin. Settings stay in
  `~/Library/Application Support/PixelDeck` unless you click "Remove my settings" in About
  first.

### Linux

- **Download:** `PixelDeck-<version>-x86_64.AppImage`. A plain `.tar.gz` of the same folder
  is published next to it for people who don't want AppImages.
- **Install:** mark it executable and run it. Setup offers two buttons:
  - "Add PixelDeck to the app menu" writes a `.desktop` file and the icon under
    `~/.local/share`, as `install-desktop.sh` does for a git clone;
  - "Install the `pixeldeck` command" makes the link `~/.local/bin/pixeldeck`.
- **If the AppImage moves:** both links break. At each start PixelDeck checks them against
  its own path (`$APPIMAGE`) and offers to fix them.
- **Uninstall:** "Remove from the app menu", then delete the file.

## How it is built

The plan is one spec for all three systems: `packaging/pixeldeck.spec` and
`packaging/build.py`, which today build Windows only.

| | macOS | Linux |
|---|---|---|
| Built on | `macos-15` (arm64) and `macos-15-intel`: one build per chip, not one universal build | `ubuntu-22.04` (x86_64); arm64 later |
| Output | PyInstaller with a `BUNDLE` step makes `PixelDeck.app` (bundle id `io.github.ahmedloumx.PixelDeck`) | the PyInstaller folder becomes an AppDir, with `AppRun`, the `.desktop` file and the icon |
| Package | `hdiutil create` makes the `.dmg` (built into macOS), with a link to Applications | `appimagetool` with the static type-2 runtime |
| Icon | `make_ico.py` already draws PNGs; `iconutil` (built into macOS) turns them into an `.icns` | a 256 px PNG from the same code |

Why these choices:

- **One build per chip, not universal2.** A universal build needs a universal Python and
  universal versions of every compiled module. Two builds are simpler and use runners GitHub
  already has.
- **The Intel build has an end date.** GitHub stops Intel macOS runners after the macOS 15
  image is retired, in fall 2027. After that, the Intel build stops or moves to another builder.
- **The oldest Ubuntu runner.** Building on `ubuntu-22.04` links against an older glibc
  (2.35), so the AppImage runs on more systems: Ubuntu 22.04 and later, Debian 12, Fedora 36
  and later. To verify: on Fedora, and on an Ubuntu 22.04 machine.
- **The static type-2 runtime.** Older AppImages needed `libfuse2`, which recent Ubuntu does
  not install. The static runtime carries what it needs. To verify on a recent Ubuntu, which is
  where the old runtime failed.
- **No Flatpak or Snap.** Their sandbox keeps the app away from the host's shells, git and
  `claude`. Every terminal and every git call would have to go through `flatpak-spawn --host`,
  and that is most of what PixelDeck does.

## Code changes before the first build

1. **The PATH of a desktop-started app.** This is the largest risk.
   - macOS gives an app started from the Dock or Finder a short PATH:
     `/usr/bin:/bin:/usr/sbin:/sbin`. Linux desktops often leave out what `.bashrc` adds.
   - PixelDeck's terminals start a login shell, so they are fine. But the server itself looks
     for `claude` and `git` with its own PATH (`launcher.claude_binary()`,
     `connections.git()`), and would report them missing.
   - The fix: at server start on macOS and Linux, read the PATH of the user's login shell once,
     with a timeout, and add what is missing. Several desktop editors do the same.
   - Also look in the places the official installers use: `~/.local/bin` (native installer),
     `/opt/homebrew/bin` and `/usr/local/bin` (Homebrew).
   - Acceptance test on both systems: started from the Dock, the app menu or Finder, the
     packaged app finds Claude and git, and Settings shows their versions.
2. **The token store.** `credentials.py` gets two more stores:
   - **macOS Keychain**, through the `security` command. To keep the token off the command
     line, where other programs can see it, the write goes through `security -i` with the
     command on standard input. To verify on a Mac.
   - **Linux Secret Service** (GNOME Keyring; KWallet 6 serves the same interface), through
     `secret-tool`, which reads the secret from standard input. When `secret-tool` is missing or
     the service does not answer, the token stays in the settings file with mode 600, and Setup
     says so. To verify on Fedora (GNOME) and on a KDE system.
3. **The `.app` layout.** Inside `PixelDeck.app`, the program is
   `Contents/MacOS/pixeldeck`. `server_argv()` already starts the program itself with
   `--server`; it needs no console form on macOS or Linux.
4. **The window.** As on Windows: Chrome, Edge or Brave in app mode, else the default browser.
   The GTK window stays a git-clone feature (decided in the first plan).

## The first-open warning

- **macOS without a Developer ID signature:** macOS 15 refuses to open a downloaded app, and
  right-click Open no longer gets around it. The person must go to System Settings, Privacy &
  Security, and click Open Anyway. The download page shows these steps with pictures. To
  verify on a Mac.
- **Proper fix: the Apple Developer Program ($99 a year).** CI signs with the Developer ID,
  turns on the hardened runtime, notarizes with `notarytool` and staples the ticket. A
  PyInstaller app may need entitlements under the hardened runtime; find them during the first
  signed build.
- **Linux** has no such warning. The AppImage only needs to be marked executable.

## What CI checks, and what needs a person

CI on each system:

- builds the package;
- unpacks it: mounts the `.dmg`, or runs the AppImage with `--appimage-extract`;
- runs the smoke test from the release plan: the server starts, every asset loads, and a
  terminal opens and runs a command.

CI cannot check:

- the first-open warning and its steps;
- the drag-to-Applications flow;
- the window;
- the PATH of an app started from the Dock or the app menu;
- the Keychain or Secret Service prompts.

So each system needs a person before its first release:

- **Linux:** Ahmed, on Fedora with GNOME. Ideally someone on Ubuntu too.
- **macOS:** someone with a Mac. Without one, the macOS package should not be offered.

## Order of work

1. Code changes 1 and 2 (PATH and the token store), with unit tests that pretend to be each
   system, as the existing tests do.
2. The Linux package, built in CI and tested by Ahmed on Fedora.
3. The macOS package, built in CI and tested by a Mac user. It is unsigned at first, with the
   Open Anyway steps on the download page.
4. Signing for macOS, when there is a Developer account.

## Open questions

- Who tests on a Mac.
- The Apple Developer account: who pays, and whose name is on it (already open in the first
  plan).
- Linux arm64: now, or when someone asks.
- `.deb` and `.rpm` packages: only if people ask (as the first plan says).
