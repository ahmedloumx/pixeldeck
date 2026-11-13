"""Jira, for one job: the ticket of a pull request you merge, its status, and moving it on.

The site comes from JIRA_SITE only, never from the page. The token is JIRA_TOKEN, with
JIRA_EMAIL (or BITBUCKET_EMAIL); the Bitbucket token is never sent here, so it still goes
to api.bitbucket.org only. A classic API token talks to the site itself; a
token with scopes must go through Atlassian's gateway (api.atlassian.com/ex/jira/<cloud
id>), so when the site refuses, the gateway is tried and whichever answers is kept.
"""

import base64
import json
import re
import threading
import urllib.error
import urllib.request

import config

KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,19}-[1-9][0-9]{0,8}$")
TIMEOUT = 15
GATEWAY = "https://api.atlassian.com/ex/jira/"


class JiraError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Jira:
    def __init__(self, opener=urllib.request.urlopen):
        self.open = opener
        self.lock = threading.Lock()
        self.base = None          # the base that answered last, for this site and token
        self.key = None           # (site, email, token) the base was found for
        self.state = {"ok": None, "me": None, "error": None}

    # ---- settings ----------------------------------------------------------

    @staticmethod
    def settings():
        return config.jira_site(), config.get("JIRA_EMAIL") or config.get("BITBUCKET_EMAIL"), config.get("JIRA_TOKEN"), "JIRA_TOKEN"

    def status(self):
        site, email, token, _ = self.settings()
        return {"site": site or None, "token_set": bool(token), "email": email or None, **self.state}

    # ---- transport ---------------------------------------------------------

    def _call(self, base, method, path, body=None, auth=True):
        site, email, token, _ = self.settings()
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        except ValueError as exc:
            raise JiraError(0, f"not a usable Jira address: {exc}") from None
        if auth:  # never carried to a redirect target
            req.add_unredirected_header("Authorization", "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode())
        try:
            with self.open(req, timeout=TIMEOUT) as res:
                raw = res.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read() or b"{}")
                text = "; ".join(detail.get("errorMessages") or []) or "; ".join(f"{k}: {v}" for k, v in (detail.get("errors") or {}).items())
            except ValueError:
                text = ""
            raise JiraError(exc.code, text or f"Jira answered {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JiraError(0, f"Jira could not be reached: {getattr(exc, 'reason', exc)}") from None

    def _gateway(self, site):
        info = self._call(site, "GET", "/_edge/tenant_info", auth=False)
        cloud = info.get("cloudId")
        if not cloud or not re.fullmatch(r"[0-9a-fA-F-]{16,64}", cloud):
            raise JiraError(0, "the site did not give its cloud id")
        return GATEWAY + cloud

    def _connect(self):
        """Find which base accepts the token, and who it is."""
        site, email, token, _ = self.settings()
        if not site:
            raise JiraError(0, "JIRA_SITE is not set")
        if not site.startswith("https://"):
            raise JiraError(0, "JIRA_SITE must start with https:// (the token is sent there)")
        if not token or not email:
            raise JiraError(0, "no Jira token: set JIRA_TOKEN (and JIRA_EMAIL if it differs from BITBUCKET_EMAIL)")
        key = (site, email, token)
        if self.base and self.key == key:
            return self.base
        last, refused = None, False
        for base in (site, None):
            try:
                base = base or self._gateway(site)
                me = self._call(base, "GET", "/rest/api/3/myself")
            except JiraError as exc:
                last = exc
                refused = refused or exc.status in (401, 403)
                if exc.status not in (401, 403):
                    break
                continue
            with self.lock:
                self.base, self.key = base, key
                self.state = {"ok": True, "me": me.get("displayName"), "error": None}
            return base
        message = "the token cannot read Jira: set JIRA_TOKEN to a token with Jira access" if refused else last.message
        self.state = {"ok": False, "me": None, "error": message}
        raise JiraError(401 if refused else last.status, message)

    def check(self):
        self.base = None
        try:
            self._connect()
        except JiraError:
            pass
        return self.status()

    def _request(self, method, path, body=None):
        base = self._connect()
        try:
            return self._call(base, method, path, body)
        except JiraError as exc:
            if exc.status in (401, 403):
                self.base = None  # the token changed or lost access: find the base again next time
            raise

    # ---- tickets -----------------------------------------------------------

    def ticket(self, key):
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise ValueError("not a ticket key")
        issue = self._request("GET", f"/rest/api/3/issue/{key}?fields=status,summary,assignee")
        moves = self._request("GET", f"/rest/api/3/issue/{key}/transitions")
        fields = issue.get("fields") or {}
        status = fields.get("status") or {}
        site, *_ = self.settings()
        return {
            "key": key, "url": f"{site}/browse/{key}", "summary": fields.get("summary") or "",
            "assignee": (fields.get("assignee") or {}).get("displayName"),
            "status": {"name": status.get("name"), "category": (status.get("statusCategory") or {}).get("key")},
            "transitions": [
                {"id": str(t.get("id")), "name": t.get("name"), "to": (t.get("to") or {}).get("name"),
                 "category": ((t.get("to") or {}).get("statusCategory") or {}).get("key")}
                for t in moves.get("transitions") or [] if t.get("id") is not None
            ],
        }

    def move(self, key, transition):
        """Move the ticket along one of the transitions Jira offers it right now."""
        current = self.ticket(key)
        if str(transition) not in {t["id"] for t in current["transitions"]}:
            raise ValueError(f"{key} cannot take that step from {current['status']['name']}; reload it")
        self._request("POST", f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": str(transition)}})
        return self.ticket(key)
