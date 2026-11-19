"""Open Bitbucket, Jira, Spotify and Google Cloud links outside the app window.

Links go to the system's default browser, or to PIXELDECK_BROWSER when it is set
(for example: firefox --new-tab). Only https links to Bitbucket, the configured Jira
site, Spotify's developer site and the Google Cloud console are accepted, plus the
sign-in pages of Spotify and Google and no other page on their accounts sites. The URL
is passed as one argv element, never through a shell.
"""

import shlex
import subprocess
import sys
import webbrowser
from urllib.parse import urlsplit

import config


# Of an accounts site only the sign-in page opens: the music player and the YouTube settings send you there.
SPOTIFY_SIGN_IN = ("accounts.spotify.com", "/authorize")
SIGN_IN_PAGES = dict([SPOTIFY_SIGN_IN, ("accounts.google.com", "/o/oauth2/v2/auth")])


def allowed_hosts():
    # the Google Cloud console: where the YouTube setup guide sends you for a key and a client
    hosts = {"bitbucket.org", "developer.spotify.com", "console.cloud.google.com"}
    site = urlsplit(config.jira_site()).hostname
    if site:
        hosts.add(site)
    return hosts


def allowed(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    if parts.hostname in SIGN_IN_PAGES:
        return parts.port is None and parts.path == SIGN_IN_PAGES[parts.hostname]
    return parts.hostname in allowed_hosts()


def open_link(url):
    """A link clicked in a terminal: any web address, once the page has asked you. Never a
    file, a script or a link with a password in it."""
    parts = urlsplit(url) if isinstance(url, str) else None
    if not parts or parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or len(url) > 4096:
        raise ValueError("only http and https links open from a terminal")
    if any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError("that link has control characters in it")
    return _launch(url)


def _launch(url):
    command = config.get("PIXELDECK_BROWSER")
    if command:
        argv = shlex.split(command, posix=sys.platform != "win32") + [url]
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
        return argv[0]
    webbrowser.open(url, new=2)
    return "default browser"


def open_url(url):
    if not allowed(url):
        raise ValueError("only https links to Bitbucket, the configured Jira site or Spotify can be opened")
    command = config.get("PIXELDECK_BROWSER")
    if command:
        argv = shlex.split(command, posix=sys.platform != "win32") + [url]
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
        return argv[0]
    webbrowser.open(url, new=2)
    return "default browser"
