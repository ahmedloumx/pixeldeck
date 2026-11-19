# Team activity feed: data source and call budget

Status: built (Bitbucket only). Code: `teamfeed.py`, the feed section of `engine.py`,
`BitbucketHost.repo_activity` in `hosts.py`, `static/activity.js`.

## What it shows

One timeline, newest first, across the pull requests of you and the team you picked
(`Engine.team()` plus you): who commented, approved, asked for changes, updated or pushed,
merged, declined or opened which pull request, and when. Filters: one person or everyone,
project, repository, kind, and a date range. "Who" is the person who did the thing, so a
teammate's review on someone else's pull request counts, and an outsider's comment on a
team pull request does not.

## Decision: the repository activity log

Bitbucket Cloud has `GET /2.0/repositories/{workspace}/{repo_slug}/pullrequests/activity`.
The published OpenAPI spec (checked 2026-11-19) describes it with the same text as the
per-pull-request log: comments, updates (with `state` OPEN, MERGED or DECLINED), approvals
and request changes, each value carrying a stub `pull_request` (`id`, `title`, `links`).
It needs the `read:pullrequest:bitbucket` scope, which PixelDeck asks for already.

One call reads up to 50 events across every pull request of a repository, where the
per-pull-request log costs one call per pull request. For a team with 40 pull requests
moving in 10 repositories that is 10 calls instead of 40, and it also catches reviews the
team gives on pull requests the engine does not cache.

Rejected: the per-pull-request log for each cached pull request (one call each, every
refresh, and blind to reviews on other people's pull requests); and building the feed from
the cached lists alone (no comments, pushes or the order of reviews).

### What the docs do not promise, and how the code copes

- **Order.** Not documented. The walk back to the last entry kept stops early only while
  each page reads newest first (two minutes of tolerance); a page out of order makes it
  read on to the page cap. A later read also goes 10 minutes past the newest entry kept,
  and entries are merged by a stable id, so overlap never doubles an entry.
- **Paging.** Not documented. The code asks `pagelen=50`, as the per-pull-request log
  already does, and follows `next` links.
- **Opened.** The log has no "opened" event, so the feed makes one from each cached pull
  request's `created_on`. An update within a minute of the opening is taken as the opening.
- **Who merged or declined.** An update's `author` may be the pull request's author rather
  than whoever closed it, so the cached `closed_by` is used when it is known.
- **`changes_requested`.** The docs show no example. It is read like an approval
  (`date`, `user`), as `BitbucketHost.changes_requested` already does.

## Which repositories, how often

The repositories are where you or your team have a pull request open, or one merged or
declined in the last 30 days, inside the project the feed shows (the bar's project unless
the panel picks another). The engine knows them from its cache: no call. At most 30, the
most recently active first; the panel says when some are left out.

A repository's log is due 10 minutes after its last read, or sooner (never within a
minute) when one of the team's cached pull requests in it moved since: the engine reads
those lists anyway, so that signal is free.

Per repository and read:

| Read | Pages (calls) |
|---|---|
| First read | up to 4 (200 events, back to 30 days ago) |
| Later read, nothing new past the first page | 1 |
| Later read, a busy repository | up to 2; more than 100 new events leaves a gap the panel names |

Events older than 30 days are dropped, and at most 500 are kept per repository, in
`activity.json` next to the cache (`activity-<host>.json` for another host). It is kept
apart from `cache.json` because that file is rewritten whole on every save.

## When it reads

- **The page's poll (GET, every 30 seconds while the panel is open): no call.** It answers
  from the file.
- **Opening the panel, changing its project, or Read now (POST):** the due logs, oldest read
  first, at most 30 calls. Read now ignores the 10 minutes but not the one-minute floor.
- **The refresh loop:** while the page asked for the feed in the last 5 minutes, at most 2
  calls per 30-second tick. A closed panel costs nothing.
- **Every read stops** when the hour's calls pass 80% of Bitbucket's 1,000 (`host.busy()`),
  like the rest of the background work, and only one read runs at a time.

## Expected calls

With the panel open all hour and R repositories in scope, about 6 R calls an hour, plus
the reads that a moved pull request brings forward:

| Repositories | Calls per hour, panel open | Panel closed |
|---|---|---|
| 5 | about 30 | 0 |
| 10 | about 60 | 0 |
| 30 (the cap) | about 180 | 0 |

A first open costs up to 4 calls per repository (30 at once; the rest follow on the next
reads). From the cadences in `engine.py`, the rest of PixelDeck spends about 300 calls an
hour for a team of six with a few open pull requests (your open list every minute, each
teammate's every 5, full lists every 5 and 15, one "behind" look per open pull request every
3), so the feed fits under the 800-call line where background work stops.

## Not tested against Bitbucket

The tests use a scripted transport built from the documented shape. Not checked live: the
order of the log, that `pagelen=50` is accepted on this endpoint, the shape of
`changes_requested`, whether the first update of a pull request marks its opening, and
whose name an update carries on a merge.
