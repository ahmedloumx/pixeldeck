"""Open Bitbucket, Jira and Spotify links outside the app window.

Links go to the system's default browser, or to PIXELDECK_BROWSER when it is set
(for example: firefox --new-tab). Only https links to Bitbucket, the configured Jira
site and Spotify's developer site are accepted, plus Spotify's sign-in page and no
other page on its accounts site. The URL is passed as one argv element, never
through a shell.
"""

import shlex
import subprocess
import sys
import webbrowser
from urllib.parse import urlsplit

import config


# Of Spotify's accounts site only the sign-in page opens: the music player sends you there.
SPOTIFY_SIGN_IN = ("accounts.spotify.com", "/authorize")


def allowed_hosts():
    hosts = {"bitbucket.org", "developer.spotify.com"}
    site = urlsplit(config.jira_site()).hostname
    if site:
        hosts.add(site)
    return hosts


def allowed(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    if parts.hostname == SPOTIFY_SIGN_IN[0]:
        return parts.port is None and parts.path == SPOTIFY_SIGN_IN[1]
    return parts.hostname in allowed_hosts()


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
