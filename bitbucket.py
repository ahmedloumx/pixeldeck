"""Bitbucket Cloud REST 2.0 client.

Credentials come from the .env (BITBUCKET_EMAIL, BITBUCKET_TOKEN), see config.py.
The file is re-read when it changes, so a replaced token works without a restart.
Every request is counted in a rolling one-hour window, because Bitbucket rate
limits user API tokens per hour and the dashboard polls in the background.
"""

import base64
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

import config

API = "https://api.bitbucket.org/2.0"
HOURLY_LIMIT = 1000


def multipart(fields, files, boundary=None):
    """A multipart/form-data body: fields are (name, text) pairs, files (field name, file
    name, bytes). The bytes go out untouched. A name holding a quote, CR or LF is refused,
    not escaped: it would end the header it sits in. The boundary never occurs in a value."""
    for name in [f[0] for f in fields] + [n for f in files for n in f[:2]]:
        if not isinstance(name, str) or any(c in name for c in '"\r\n'):
            raise ValueError("a form field name cannot hold quotes or line breaks")
    values = [v.encode("utf-8") for _, v in fields] + [f[2] for f in files]
    boundary = boundary or secrets.token_hex(16)
    while any(boundary.encode() in v for v in values):
        boundary = secrets.token_hex(16)
    out = []
    for (name, _), value in zip(fields, values):
        out += [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"), value, b"\r\n"]
    for name, filename, data in files:
        out += [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n".encode("utf-8"), data, b"\r\n"]
    out.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(out)


class BitbucketError(Exception):
    def __init__(self, status, message):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class Client:
    def __init__(self, workspace=None):
        self.workspace = workspace or config.workspace()
        self._calls = deque()
        self._lock = threading.Lock()
        self.last_error = None
        self.sort_supported = None

    # ---- plumbing -------------------------------------------------------

    def _auth_header(self):
        token = config.get("BITBUCKET_TOKEN")
        if not token:
            raise BitbucketError(0, f"No BITBUCKET_TOKEN in {config.env_path()}")
        if not self.workspace:
            self.workspace = config.workspace()
        if not self.workspace:
            raise BitbucketError(0, f"No BITBUCKET_WORKSPACE in {config.env_path()}")
        email = config.get("BITBUCKET_EMAIL")
        if email:
            pair = f"{email}:{token}".encode()
            return "Basic " + base64.b64encode(pair).decode()
        return "Bearer " + token

    def calls_last_hour(self):
        now = time.time()
        with self._lock:
            while self._calls and now - self._calls[0] > 3600:
                self._calls.popleft()
            return len(self._calls)

    def get(self, path, params=None):
        return self.send("GET", path, params=params)

    def send(self, method, path, body=None, params=None, headers_out=None):
        """headers_out, a dict when given, receives the answer's headers (lower-case names)."""
        url = path if path.startswith("http") else API + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        headers = {
            "Accept": "application/json",
            "Authorization": self._auth_header(),
            "User-Agent": "pixeldeck/1.0",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        with self._lock:
            self._calls.append(time.time())
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                if headers_out is not None:
                    headers_out.update({k.lower(): v for k, v in response.headers.items()})
                body = json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                message = json.loads(raw)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = raw[:240] or str(exc.reason)
            self.last_error = {"status": exc.code, "message": message, "at": time.time()}
            raise BitbucketError(exc.code, message) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = str(getattr(exc, "reason", exc))
            self.last_error = {"status": 0, "message": reason, "at": time.time()}
            raise BitbucketError(0, reason) from None
        self.last_error = None
        return body

    def text(self, path, limit=4 * 1024 * 1024):
        url = path if path.startswith("http") else API + path
        request = urllib.request.Request(url, headers={
            "Accept": "text/plain", "Authorization": self._auth_header(), "User-Agent": "pr-desk/1.0"})
        with self._lock:
            self._calls.append(time.time())
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            raise BitbucketError(exc.code, exc.read().decode("utf-8", "replace")[:240] or str(exc.reason)) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise BitbucketError(0, str(getattr(exc, "reason", exc))) from None
        return raw[:limit].decode("utf-8", "replace"), len(raw) > limit

    def post_form(self, path, fields, files):
        """POST a multipart/form-data body (Bitbucket takes commits this way, at /src).
        Answers the status and the Location header; errors as send() raises them."""
        boundary, data = multipart(fields, files)
        url = path if path.startswith("http") else API + path
        request = urllib.request.Request(url, data=data, method="POST", headers={
            "Accept": "application/json", "Authorization": self._auth_header(), "User-Agent": "pixeldeck/1.0",
            "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with self._lock:
            self._calls.append(time.time())
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                response.read()
                return {"status": response.status, "location": response.headers.get("Location")}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                message = json.loads(raw)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = raw[:240] or str(exc.reason)
            raise BitbucketError(exc.code, message) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise BitbucketError(0, str(getattr(exc, "reason", exc))) from None

    def paged(self, path, params=None, max_pages=10, stop=None, meta=None):
        """Yield values across pages; stop(value) returning True ends the walk.
        meta, when given, receives the collection's total ``size``."""
        url, query = path, dict(params or {})
        for _ in range(max_pages):
            page = self.get(url, query)
            if meta is not None and "size" not in meta:
                meta["size"] = page.get("size")
            for value in page.get("values", []):
                if stop and stop(value):
                    return
                yield value
            url, query = page.get("next"), None
            if not url:
                return

    # ---- endpoints ------------------------------------------------------

    def me(self):
        return self.get("/user")

    def members(self):
        path = f"/workspaces/{self.workspace}/members"
        return [m.get("user") or {} for m in self.paged(path, {"pagelen": 100}, max_pages=10)]

    def user_prs(self, user_uuid, state, newer_than=None, max_pages=6, meta=None):
        """Pull requests authored by one user across the whole workspace.

        participants and reviewers are not part of list responses unless asked
        for, and they carry the approve / request-changes state.
        """
        path = f"/workspaces/{self.workspace}/pullrequests/{urllib.parse.quote(user_uuid)}"
        params = {
            "state": state,
            "pagelen": 50,
            "fields": "+values.participants,+values.reviewers",
        }
        if self.sort_supported is not False:
            params["sort"] = "-updated_on"
        stop = None
        if newer_than and self.sort_supported:
            stop = lambda pr: (pr.get("updated_on") or "") < newer_than  # noqa: E731
        try:
            values = list(self.paged(path, params, max_pages=max_pages, stop=stop, meta=meta))
        except BitbucketError as exc:
            if exc.status != 400 or "sort" not in params:
                raise
            self.sort_supported = False
            params.pop("sort")
            values = list(self.paged(path, params, max_pages=max_pages, meta=meta))
        else:
            if self.sort_supported is None and "sort" in params:
                # Accepting the parameter is not honouring it: the early stop
                # is only safe once the order is seen to be newest first.
                stamps = [v.get("updated_on") or "" for v in values]
                if len(stamps) > 1:
                    self.sort_supported = all(a >= b for a, b in zip(stamps, stamps[1:]))
        if newer_than:
            values = [v for v in values if (v.get("updated_on") or "") >= newer_than]
        return values

    def pull_request(self, repo, pr_id):
        return self.get(f"/repositories/{self.workspace}/{repo}/pullrequests/{int(pr_id)}")

    def comments(self, repo, pr_id):
        path = f"/repositories/{self.workspace}/{repo}/pullrequests/{int(pr_id)}/comments"
        return list(self.paged(path, {"pagelen": 100}, max_pages=10))

    def activity(self, repo, pr_id):
        path = f"/repositories/{self.workspace}/{repo}/pullrequests/{int(pr_id)}/activity"
        return list(self.paged(path, {"pagelen": 50}, max_pages=4))

    def tasks(self, repo, pr_id):
        path = f"/repositories/{self.workspace}/{repo}/pullrequests/{int(pr_id)}/tasks"
        try:
            return list(self.paged(path, {"pagelen": 100}, max_pages=5))
        except BitbucketError as exc:
            if exc.status in (403, 404):
                return []
            raise
