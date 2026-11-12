## What I want
Review this pull request as a reviewer (I am not the author). Read only: no edits,
no commits, no pushes. Diff with `git fetch origin {source} {dest}` then
`git diff origin/{dest}...origin/{source}`.
Look for correctness bugs first, then missing tests, then anything that will be
hard to maintain. Do not repeat points already raised in the threads below; say
where you agree or disagree with them. Output a findings table (severity, file
and new-file line, finding, suggested fix) and a verdict.
