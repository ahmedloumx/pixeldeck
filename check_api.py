"""Check the Bitbucket responses PixelDeck relies on, printing shapes only.

Run: python3 check_api.py
Prints key names, enum values and counts. Never prints comment or PR text.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bitbucket import BitbucketError, Client  # noqa: E402

FIELDS = "+values.participants,+values.reviewers"


def show(label, value):
    print(f"  {label:<38} {value}")


def main():
    client = Client()
    me = client.me()
    print("GET /user: ok")
    show("keys", sorted(me))
    uuid = me["uuid"]
    base = f"/workspaces/{client.workspace}/pullrequests/{uuid}"

    for state in ("OPEN", "MERGED"):
        print(f"\nGET {base.replace(uuid, '{me}')} state={state}")
        try:
            page = client.get(base, {"state": state, "pagelen": 50, "fields": FIELDS, "sort": "-updated_on"})
            show("sort accepted", True)
        except BitbucketError as exc:
            show("sort rejected", f"{exc.status} {exc.message}")
            page = client.get(base, {"state": state, "pagelen": 50, "fields": FIELDS})
        values = page.get("values", [])
        show("page keys", sorted(page))
        show("items on page 1 / size", f"{len(values)} / {page.get('size')}")
        if not values:
            continue
        stamps = [v.get("updated_on") or "" for v in values]
        show("newest first", all(a >= b for a, b in zip(stamps, stamps[1:])))
        show("first / last updated_on", f"{stamps[0][:16]} / {stamps[-1][:16]}")
        first = values[0]
        show("pr keys", sorted(first))
        show("has participants / reviewers", f"{'participants' in first} / {'reviewers' in first}")
        show("draft present", "draft" in first)
        states = Counter()
        roles = Counter()
        for pr in values:
            for p in pr.get("participants") or []:
                states[str(p.get("state"))] += 1
                roles[str(p.get("role"))] += 1
        show("participants[].state values", dict(states))
        show("participants[].role values", dict(roles))
        if first.get("participants"):
            show("participant keys", sorted(first["participants"][0]))
        show("destination.repository keys", sorted((first.get("destination") or {}).get("repository") or {}))

    # Comments and tasks on the merged PR with the most comments on page 1.
    merged = client.get(base, {"state": "MERGED", "pagelen": 50, "fields": FIELDS}).get("values", [])
    target = max(merged, key=lambda p: p.get("comment_count") or 0, default=None)
    if target:
        repo = target["destination"]["repository"]["full_name"].split("/", 1)[1]
        print(f"\nComments on {repo} #{target['id']} ({target.get('comment_count')} comments)")
        comments = client.comments(repo, target["id"])
        keys = Counter(k for c in comments for k in c)
        show("comment key counts", dict(keys))
        inline_keys = Counter(k for c in comments for k in (c.get("inline") or {}))
        show("inline key counts", dict(inline_keys))
        res = [c["resolution"] for c in comments if c.get("resolution")]
        show("comments with resolution", len(res))
        if res:
            show("resolution keys / type", f"{sorted(res[0])} / {res[0].get('type')}")
        show("with parent / deleted / pending", f"{sum(1 for c in comments if c.get('parent'))} / "
             f"{sum(1 for c in comments if c.get('deleted'))} / {sum(1 for c in comments if c.get('pending'))}")
        tasks = client.tasks(repo, target["id"])
        show("tasks / state values", f"{len(tasks)} / {dict(Counter(t.get('state') for t in tasks))}")
        if tasks:
            show("task keys", sorted(tasks[0]))

    print("\nGET /workspaces/{ws}/members")
    try:
        members = client.members()
        show("members", len(members))
        if members:
            show("member user keys", sorted(members[0]))
    except BitbucketError as exc:
        show("members refused", f"{exc.status} {exc.message}")
    print(f"\ncalls used: {client.calls_last_hour()}")


if __name__ == "__main__":
    main()
