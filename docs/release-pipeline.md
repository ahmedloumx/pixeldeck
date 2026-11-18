# Release pipeline: from a tag to the download page

Status: plan only. Nothing here is built or switched on yet.

This refines two sections of [installer-and-updates.md](installer-and-updates.md): "Build and
release" and "The website and the update list". It does not reopen what that plan decided.
It needs the Windows installer from the `packaging/` folder (pull request #18).

## In short

1. A maintainer pushes a tag such as `v0.2.0` on `main`.
2. GitHub Actions runs the tests, builds the Windows installer, installs it on a clean
   machine to test it, and drafts a GitHub Release with the file, its SHA-256 and a build
   attestation.
3. A maintainer approves. The release is published and the website is deployed.
4. Without a domain, the website is `https://ahmedloumx.github.io/pixeldeck/`. A domain is
   needed before the in-app updater ships, not before.

## Two decisions first

### Where the installer files live

The download button is on our website either way. The question is where the bytes are served
from.

| | A. GitHub Releases, linked from the site (proposed) | B. On the site itself |
|---|---|---|
| Size limits | each file under 2 GiB, no limit on total size or bandwidth | the published site may be at most 1 GB |
| Traffic | no limit | soft limit of 100 GB a month |
| Old versions | kept on the Releases page | must be pruned to stay under 1 GB |
| Address | `github.com/.../releases/download/...` | the site's own address |

A Windows installer is about 10 MB today. With macOS and Linux builds, a release will be
around 40 to 50 MB, so option B fills 1 GB after about 20 releases. Option A has no such
ceiling and costs nothing. The update list (later) is signed, so where the bytes come from does
not change what the app accepts.

### What the missing domain blocks

- **Builds, releases and the download page:** nothing. They work at
  `ahmedloumx.github.io/pixeldeck`.
- **The in-app updater:** the address it checks ships inside every installed copy. Changing it
  later strands the copies already out there. GitHub's documentation does not promise that a
  `github.io` address redirects once a custom domain is set, and it can't once the site leaves
  GitHub Pages. So: **buy the domain before the first release that contains the updater.**
- **Links people share:** a `github.io` link breaks if the site ever moves off Pages. Minor.

## The workflows

Three files under `.github/workflows/`.

### `ci.yml`: every pull request and every push to `main`

- **Tests** on `ubuntu-latest`, `windows-2025` and `macos-15`, Python 3.12:
  `python -m unittest discover -s tests`.
  - Three tests fail on Windows today: `test_macos` and two checks of Unix file modes.
  - Fix them, or skip them on Windows, before CI is switched on; otherwise it starts red.
- **Packaged smoke test** on `windows-2025`:
  - Build the app folder with `python packaging/build.py --app-only`.
  - Run a new script, `packaging/smoke.py`, which also runs on a developer's machine. It:
    - starts the server on a free port with scratch folders;
    - checks `/healthz`;
    - fetches every file under the bundle's `static/`;
    - opens a terminal through the API and runs a command;
    - checks that `pixeldeck --propose` prints its answer and returns its exit code.
  - These checks catch the two bugs found by hand while building #18: a shell that died
    because `OpenConsole.exe` was not bundled, and every static file answering 404 when the
    app was reached through a short path name (`C:\Users\JOHNDO~1`). The smoke test runs once
    from a short path for that reason.

### `release.yml`: a pushed tag `v*`

| Job | Runs on | What it does |
|---|---|---|
| check | ubuntu | Checks out with every tag (`fetch-depth: 0`). Fails unless the tagged commit is on `main` and the tag equals `v` + `VERSION` in `version.py`. |
| test | 3 systems | The same tests as `ci.yml`. |
| build-windows | windows-2025 | Installs the build tools from a pinned list (see Security), runs `python packaging/build.py`, keeps `dist/` as an artifact. |
| install-test | a fresh windows-2025 | Installs the Setup silently into a temporary folder, runs the smoke test against the installed copy, uninstalls, and checks that the folder and the uninstall entry are gone. |
| draft | ubuntu | `gh release create <tag> --draft --verify-tag --generate-notes`, uploads the installer and `SHA256SUMS.txt`, and attests the build (`actions/attest-build-provenance`). |
| publish | ubuntu, environment `release` | Waits for a maintainer's approval, publishes the release, builds the site and deploys it to Pages. |

Notes:

- **Installer switches go in PowerShell steps, never bash.** Git Bash rewrites `/VERYSILENT`
  into a file path, and the installer then shows its wizard with every default task.
- The windows-2025 runner already has Inno Setup 6 and Python 3.12. Still, the job checks the
  Inno Setup version, so a runner update cannot change the installer unnoticed.
- `publish` deploys the site itself. A release published with the workflow's own token does
  not start other workflows, so a separate "on release" workflow would never run.

### `site.yml`: a change under `site/` on `main`

It deploys the site again, with the latest published release read from
`gh release view --json`. This lets the page change between releases.

## The website, before there is a domain

- **Hosting.** GitHub Pages, with source "GitHub Actions". Address:
  `https://ahmedloumx.github.io/pixeldeck/`.
- **Content.** A `site/` folder in the repo:
  - `index.html`: one page in PixelDeck's look. It has the download button with version, size
    and SHA-256, links to the release notes, every release and the source, and a note on the
    Windows warning while the installer is unsigned.
  - `downloads.json`: written at deploy time. It holds the version, the date, and the address,
    size and SHA-256 of each file.
  - Later, `updates/stable.json`: the signed update list from the first plan.
- **Nothing that tracks visitors.** No analytics, no cookies, no third-party scripts.
- **When the domain is bought:**
  1. In Pages settings, set it as the custom domain and enforce HTTPS.
  2. At the DNS provider, add a `CNAME` record to `ahmedloumx.github.io` for a subdomain, or
     the `A`/`AAAA` records GitHub lists for the bare domain.
  3. Put the site's address in a repository variable, `SITE_URL`. The build writes it into
     `build-info.json`, so the updater reads it from there and a new address needs no code
     change.

## Security

- **Permissions per job.** The default is `contents: read`. Only `draft` and `publish` get
  `contents: write`. Only the Pages deploy gets `pages: write` and `id-token: write`. Only the
  attestation gets `attestations: write`.
- **Actions pinned** to a full commit SHA, with Dependabot proposing updates.
- **Build tools pinned with hashes.** `packaging/requirements-build.txt` lists pyinstaller,
  its hooks, and pywinpty with their dependencies, installed with
  `pip install --require-hashes`. The hand-made build in #18 took pywinpty from the machine's
  own Python; CI must not.
- **Tag rules.** Only maintainers may create `v*` tags, and tags can't be moved or deleted.
- **Environment `release`.** It needs a reviewer's approval. The signing credentials, and later
  the update list's key, are secrets of this environment only. Pull requests from forks never
  see secrets (GitHub's default).
- **Checking a download.** Anyone can run
  `gh attestation verify PixelDeck-Setup-<version>-x64.exe -R ahmedloumx/pixeldeck`.
- **Licenses before the first public download.** The installer now ships Python, OpenSSL,
  pywinpty and OpenConsole. Their license notices must be in it, next to the ones already in
  `licenses/`.

## Signing

This is still an open question from the first plan. Here is what is known now. Nothing below
was tried.

- **Windows, SignPath Foundation.** Free for open source projects with an OSI license in a
  public repository; PixelDeck is MIT and public. The publisher shown is "SignPath
  Foundation", not us. It needs their approval and a code signing policy page, and it works
  with GitHub Actions. Only part of the requirements were checked: read signpath.org first.
- **Windows, Microsoft Artifact Signing** (formerly Trusted Signing). A low monthly fee
  (price not checked). Answers on Microsoft's Q&A say identity validation for individuals is
  open only in the US and Canada.
- **Windows, a certificate from a certificate authority.** A yearly fee. The key must live in
  hardware or a cloud HSM, so CI signs through the authority's cloud signing service.
- **Until one of these is in place,** the installer is unsigned. Windows SmartScreen warns on
  the first run, and the download page says so and shows the SHA-256.
- **macOS.** The Apple Developer Program ($99 a year) gives Developer ID signing and
  notarization. CI notarizes with `notarytool` and an App Store Connect API key stored in the
  `release` environment.
- **Linux.** The AppImage is not signed. `SHA256SUMS.txt` and the attestation cover it.

## macOS and Linux

Their packaging code is written ([packaging-macos-linux.md](packaging-macos-linux.md)) but has
never run. Once a person has tested each one, the build matrix adds:

- `macos-15` (Apple Silicon);
- `macos-15-intel`: GitHub ends Intel macOS runners after the macOS 15 image is retired, in
  fall 2027;
- `ubuntu-22.04`: an older glibc, so the AppImage runs on more systems.

## The in-app updater (later)

It works as the first plan describes. It checks a signed list, refuses a lower version, and
checks the size and SHA-256 before running anything.

It still needs:
- **Ed25519 verification in the app.** The standard library has none. Options: a small
  vendored verifier, the `cryptography` package, or tufup. tufup's current state was not
  checked here.
- **A signing key.** Generate it offline. The private key lives only in the `release`
  environment, and the public key is built into the app.

## Order of work

0. **Before anything is public:**
   - license notices in the installer;
   - the three Windows test failures fixed or skipped.
1. **A dry run on a contributor's fork:** `ci.yml`, `release.yml` and a test tag such as
   `v0.1.0-rc1`. This publishes a real, public page and release on that fork, so its owner
   says yes first.
2. **A pull request to this repository** with the workflows, `site/`,
   `packaging/requirements-build.txt` and `packaging/smoke.py`.
3. **The owner's settings** (below), then the tag `v0.1.0`.
4. **The domain,** then the custom domain on Pages.
5. **Signing.**
6. **The updater.**
7. **macOS and Linux.**

## Who does what

Some steps need admin rights on the repository. For a repository owned by a personal account,
only the owner has them:

- Pages: source "GitHub Actions", and later the custom domain;
- the `release` environment, with its reviewers and secrets (GitHub: in a personal account
  repository, only the owner can create environment secrets);
- the tag ruleset.

Everything else comes as pull requests: the workflows, the site, the smoke test and the
pinned build tools.

## Costs

- **Free:** GitHub Actions on standard runners (free for public repositories), Pages and
  Releases.
- **The domain:** a yearly fee that depends on the name and the ending.
- **Signing:** see above.

## Open questions

- Files on Releases (A) or on the site (B).
- The domain name, and whether it is bought before the first public release or only before
  the updater.
- Who approves releases.
- Which Windows signing route.
- One channel (`stable`) at first, or a `beta` channel too.
