"""What every code host has in common: the error the engine catches, and one careful HTTP client
for the hosts that speak JSON over HTTPS with a personal token (GitHub and GitLab).

The token goes to the address it was saved for and nowhere else: a redirect or a "next page"
link that points at another origin is refused, never followed with the token on it. A GET
answer that carries an ETag is kept per address, so the next read sends If-None-Match and an
unchanged answer (304) comes from memory; GitHub does not count those against its hourly
limit. The rate limit headers of every answer are kept, so the status bar shows the host's own
count, and a limit that is spent is waited out instead of hammered.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict, deque

import version

TIMEOUT = 30
READ_MAX = 8 * 1024 * 1024  # a JSON answer or a diff larger than this is cut
ETAG_KEEP = 600  # answers kept for If-None-Match, least recently used out first
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class HostError(Exception):
    """A refusal or failure from the code host. status 0 is "never answered" (offline, no
    token); unset says the settings lack what the host needs, so the page asks for setup."""

    def __init__(self, status, message, detail=None, unset=False):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message
        self.detail = detail
        self.unset = unset


def check_base(url, name):
    """An API address: https, or plain http on this machine only (a local test server). The
    token is sent there, so anything else is refused before a request is made."""
    parts = urllib.parse.urlsplit(str(url or "").strip())
    if parts.scheme == "https" and parts.hostname:
        pass
    elif parts.scheme == "http" and (parts.hostname or "") in LOOPBACK:
        pass
    else:
        raise ValueError(f"The {name} address must start with https://")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError(f"The {name} address is a plain https:// address, with no query or login in it")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def origin(url):
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # Api follows a redirect itself, and only on its own origin


_opener = []


def urllib_transport(method, url, headers, body, timeout=TIMEOUT):
    """One HTTP exchange: (status, lower-cased headers, at most READ_MAX + 1 bytes)."""
    if not _opener:
        _opener.append(urllib.request.build_opener(_NoRedirect))
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _opener[0].open(request, timeout=timeout) as res:
            return res.status, {k.lower(): v for k, v in res.headers.items()}, res.read(READ_MAX + 1)
    except urllib.error.HTTPError as exc:
        with exc:
            raw = exc.read(READ_MAX + 1) if exc.fp else b""
            return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, raw
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HostError(0, f"could not be reached: {getattr(exc, 'reason', exc)}") from None


def next_link(header):
    """The rel="next" address of a Link header, or None."""
    for part in (header or "").split(","):
        url, _, rest = part.partition(";")
        if 'rel="next"' in rest.replace(" ", ""):
            return url.strip().strip("<>")
    return None


class Api:
    """JSON over HTTPS for one host. Subclasses name the host and say how the token is sent.

    transport(method, url, headers, body) -> (status, headers, bytes) is swapped in by tests;
    it is the only way out, so a test sees every request and no network is touched."""

    name = "The host"
    default_limit = 5000  # the meter's size until the host says its own

    def __init__(self, base, token, transport=None, clock=time.time):
        self.base = base.rstrip("/")
        self.origin = origin(self.base)
        self._token = token
        self.transport = transport or urllib_transport
        self.clock = clock
        self.lock = threading.Lock()
        self.etags = OrderedDict()  # url -> (etag, parsed body, headers worth keeping)
        self.limits = {}  # resource -> {"limit", "remaining", "reset"}
        self.calls = deque()
        self.last_error = None

    # ---- what each host says differently ------------------------------------

    def auth_headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def accept(self):
        return "application/json"

    def read_limits(self, headers, resource):
        """Keep what the answer says about the rate limit."""

    def message_of(self, status, payload, raw):
        if isinstance(payload, dict):
            for key in ("message", "error_description", "error"):
                if isinstance(payload.get(key), str) and payload[key]:
                    return payload[key][:240]
        return raw[:240] or f"answered {status}"

    def limited(self, status, headers, message):
        """Whether a refusal is the rate limit rather than the token."""
        return status == 429

    # ---- plumbing -------------------------------------------------------

    def url(self, path, params=None):
        url = path if path.startswith(("http://", "https://")) else self.base + path
        if origin(url) != self.origin:
            raise HostError(0, f"{self.name} pointed at another server ({origin(url)}); the token is not sent there")
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        return url

    def calls_last_hour(self):
        now = self.clock()
        with self.lock:
            while self.calls and now - self.calls[0] > 3600:
                self.calls.popleft()
            return len(self.calls)

    def budget(self):
        """Used and limit, as the status bar shows them: the host's own count when it sends
        one, else the calls made from here in the last hour."""
        with self.lock:
            core = dict(self.limits.get("core") or {})
        if core.get("limit"):
            return {"used": max(0, core["limit"] - core.get("remaining", core["limit"])), "limit": core["limit"]}
        return {"used": self.calls_last_hour(), "limit": self.default_limit}

    def busy(self, share=0.8):
        """The background refresh stops at the same share of the limit as for Bitbucket."""
        b = self.budget()
        return b["used"] > b["limit"] * share

    def _cool_down(self, resource, headers):
        """A refusal for the rate limit: no call for this resource until the host's reset, or
        the Retry-After it asks for, or a minute."""
        try:
            wait = int(headers.get("retry-after") or 0)
        except ValueError:
            wait = 0
        with self.lock:
            entry = dict(self.limits.get(resource) or {})
            reset = entry.get("reset") or 0
            if entry.get("remaining") != 0 or reset <= self.clock() or wait:
                reset = max(reset, self.clock() + (wait or 60))
            self.limits[resource] = {**entry, "remaining": 0, "reset": reset}

    def _spent(self, resource):
        with self.lock:
            limit = dict(self.limits.get(resource) or {})
        reset = limit.get("reset") or 0
        if limit.get("remaining") == 0 and reset > self.clock():
            wait = max(1, int(reset - self.clock()))
            raise HostError(429, f"{self.name} rate limit is spent; it resets in {wait} s")

    def request(self, method, path, params=None, body=None, accept=None, raw=False, resource="core", headers_out=None):
        """One call. JSON in and out unless raw: then (text, cut) for a diff or a file."""
        token = self._token() if callable(self._token) else self._token
        if not token:
            raise HostError(0, f"No token for {self.name} in the settings", unset=True)
        url = self.url(path, params)
        self._spent(resource)
        headers = {"Accept": accept or self.accept(), "User-Agent": version.USER_AGENT, **self.auth_headers(token)}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        cached = None
        if method == "GET" and not raw:
            with self.lock:
                cached = self.etags.get(url)
                if cached:
                    self.etags.move_to_end(url)
            if cached:
                headers["If-None-Match"] = cached[0]
        for _hop in range(4):
            with self.lock:
                self.calls.append(self.clock())
            try:
                status, answer_headers, content = self.transport(method, url, headers, data)
            except HostError as exc:
                self.last_error = {"status": 0, "message": exc.message, "at": self.clock()}
                raise HostError(0, f"{self.name} {exc.message}") from None
            self.read_limits(answer_headers, resource)
            if status in (301, 302, 303, 307, 308) and answer_headers.get("location"):
                target = urllib.parse.urljoin(url, answer_headers["location"])
                if origin(target) != self.origin:
                    raise HostError(status, f"{self.name} moved this to another server; the token is not sent there")
                url = target  # a renamed repository answers with its new address on the same host
                continue
            break
        else:
            raise HostError(310, f"{self.name} redirected too many times")
        if headers_out is not None:
            headers_out.update(answer_headers)
        if status == 304 and cached:
            self.last_error = None
            if headers_out is not None:
                headers_out.update(cached[2])
            return cached[1]
        text = content[:READ_MAX].decode("utf-8", "replace")
        if status >= 400:
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
            message = self.message_of(status, payload, text)
            code = 429 if self.limited(status, answer_headers, message) else status
            if code == 429:
                self._cool_down(resource, answer_headers)
            self.last_error = {"status": code, "message": message, "at": self.clock()}
            raise HostError(code, message, payload if isinstance(payload, dict) else None)
        self.last_error = None
        if raw:
            return text, len(content) > READ_MAX
        try:
            parsed = json.loads(text) if text.strip() else {}
        except ValueError:
            raise HostError(502, f"{self.name} answered something that is not JSON") from None
        etag = answer_headers.get("etag")
        if method == "GET" and etag:
            keep = {k: answer_headers[k] for k in ("link", "x-total", "x-next-page") if k in answer_headers}
            with self.lock:
                self.etags[url] = (etag, parsed, keep)
                self.etags.move_to_end(url)
                while len(self.etags) > ETAG_KEEP:
                    self.etags.popitem(last=False)
        return parsed

    def get(self, path, params=None, **kw):
        return self.request("GET", path, params, **kw)

    def paged(self, path, params=None, max_pages=10, meta=None, resource="core", items="items"):
        """Every item across pages, following the Link header on this host only. A page is a
        list, or an object holding the list under `items` (a search). meta, when given,
        receives the total the host reports."""
        url, query = path, dict(params or {})
        for _ in range(max_pages):
            headers = {}
            page = self.get(url, query, resource=resource, headers_out=headers)
            values = page if isinstance(page, list) else (page.get(items) or [] if isinstance(page, dict) else [])
            if meta is not None and "size" not in meta:
                if isinstance(page, dict) and isinstance(page.get("total_count"), int):
                    meta["size"] = page["total_count"]
                elif str(headers.get("x-total") or "").isdigit():
                    meta["size"] = int(headers["x-total"])
            yield from values
            url, query = next_link(headers.get("link")), None
            if not url:
                return
            if origin(url) != self.origin:
                return  # a next page somewhere else is not followed with the token
