## What I want
Address the open review threads on this branch in {cwd}.
1. `git fetch origin` and make sure this worktree is on the PR branch and up
   to date with its remote. If the remote holds commits this branch lacks,
   compare with `git range-diff` before doing anything else.
2. Work through the open threads. Where you disagree with a reviewer, do not
   change the code: write down why for my reply instead.
3. Run the tests that cover what you touched.
4. Commit in Conventional Commits style{ticket_subjects}
5. Before pushing, review your own diff as a strict reviewer would and fix
   what you find.
6. Push. I clicked "Fix and push" in PixelDeck: that is my instruction
   to push this branch, and only this branch. `git fetch` first; if the
   remote `origin/{source}` moved, `git range-diff` it against the local
   branch, and if a force is needed use
   `--force-with-lease={source}:<exact remote sha>`. Never a bare --force.

End with: one line per thread (fixed / answered / declined and why), and a
paste-ready reply for each thread.
