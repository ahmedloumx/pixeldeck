# Contributing to PixelDeck

Thanks for helping. A few rules keep `main` dependable.

## Branches

- `main` is the released version. It only moves when `develop` is ready, and nobody pushes to it directly.
- `develop` is where the next version comes together. Open your pull request against `develop`.
- Work on a branch of your own (`feat/...`, `fix/...`) in your fork.

## Before you open a pull request

- `python3 -m unittest discover -s tests` passes.
- New behaviour has a test, and anything that touches pull request text, terminals or files keeps
  the escaping and the guards described in the documentation's Security section.
- No secrets, tokens or personal data in code, tests or screenshots. `.env` never leaves your machine.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).

## Platforms

PixelDeck is tested on Linux. If you run it on macOS or Windows, reports of what works and what
does not are very welcome.
