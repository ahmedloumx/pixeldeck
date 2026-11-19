"""The HTTP plumbing the GitHub and GitLab clients share.

Every request is counted in a rolling hour, as Bitbucket's are, for the API meter and the
background budget. A refusal becomes a BitbucketError, the one error the engine and the
actions catch whatever the host. Pagination follows a Link header only to the same server and
path as the API itself, so a token is never sent anywhere else.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

import version
from bitbucket import BitbucketError


def same_api(url, base):
    """Whether url is under the API at base: same scheme, server and path prefix."""
    a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    prefix = b.path.rstrip("/") + "/"
    return a.scheme == b.scheme and a.netloc == b.netloc and (a.path.rstrip("/") + "/").startswith(prefix)


def next_link(header, base):
    """The rel="next" address of a Link header, when it is under the same API; else None."""
    for part in (header or "").split(","):
        url, _, rest = part.partition(";")
        if 'rel="next"' in rest:
            url = url.strip().strip("<>")
            return url if same_api(url, base) else None
    return None


class Client:
    """A JSON API with a token in a header. Subclasses give api, _auth() and _message()."""

    kind = None
    hourly_limit = 5000

    def __init__(self):
        self._calls = deque()
        self._lock = threading.Lock()
        self.last_error = None

    api = ""

    def _auth(self):
        raise NotImplementedError

    def _message(self, text):
        return text[:240]

    def calls_last_hour(self):
        now = time.time()
        with self._lock:
            while self._calls and now - self._calls[0] > 3600:
                self._calls.popleft()
            return len(self._calls)

    def url(self, path, params=None):
        url = path if path.startswith("http") else self.api + path
        if not same_api(url, self.api):
            raise BitbucketError(0, "PixelDeck only calls the API it is set up with")
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        return url

    def request(self, method, url, body=None, accept="application/json", raw=False, limit=None, headers_out=None):
        """One call. raw answers the bytes (at most limit + 1 of them); else parsed JSON."""
        headers = {"Accept": accept, "User-Agent": version.USER_AGENT, **self._auth()}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        with self._lock:
            self._calls.append(time.time())
        try:
            with urllib.request.urlopen(request, timeout=60 if raw else 30) as response:
                payload = response.read(limit + 1) if limit else response.read()
                if headers_out is not None:
                    headers_out.update({k.lower(): v for k, v in response.headers.items()})
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            message = self._message(text) or str(exc.reason)
            self.last_error = {"status": exc.code, "message": message, "at": time.time()}
            raise BitbucketError(exc.code, message) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = str(getattr(exc, "reason", exc))
            self.last_error = {"status": 0, "message": reason, "at": time.time()}
            raise BitbucketError(0, reason) from None
        self.last_error = None
        if raw:
            return payload
        return json.loads(payload) if payload.strip() else {}

    def get(self, path, params=None):
        return self.request("GET", self.url(path, params))

    def send(self, method, path, body=None, params=None):
        return self.request(method, self.url(path, params), body)

    def paged(self, path, params=None, max_pages=10, key=None, meta=None, total_header=None):
        """Yield the items of each page (the page itself, or page[key]), following Link."""
        url = self.url(path, params)
        for _ in range(max_pages):
            headers = {}
            page = self.request("GET", url, headers_out=headers)
            if meta is not None and "size" not in meta:
                if key and isinstance(page, dict) and key == "items":
                    meta["size"] = page.get("total_count")
                elif total_header and str(headers.get(total_header, "")).isdigit():
                    meta["size"] = int(headers[total_header])
            items = page.get(key) if key and isinstance(page, dict) else page
            for item in items if isinstance(items, list) else []:
                yield item
            url = next_link(headers.get("link"), self.api)
            if not url:
                return
