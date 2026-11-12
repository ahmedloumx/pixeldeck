"""Open Bitbucket and Jira links outside the app window.

Links go to the system's default browser, or to PIXELDECK_BROWSER when it is set
(for example: firefox --new-tab). Only https links to Bitbucket and to the
configured Jira site are accepted, and the URL is passed as one argv element,
never through a shell.
"""

import shlex
import subprocess
import sys
import webbrowser
from urllib.parse import urlsplit

import config


def allowed_hosts():
    hosts = {"bitbucket.org"}
    site = urlsplit(config.jira_site()).hostname
    if site:
        hosts.add(site)
    return hosts


def open_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in allowed_hosts() or parts.username or parts.password:
        raise ValueError("only https links to Bitbucket or the configured Jira site can be opened")
    command = config.get("PIXELDECK_BROWSER")
    if command:
        argv = shlex.split(command, posix=sys.platform != "win32") + [url]
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
        return argv[0]
    webbrowser.open(url, new=2)
    return "default browser"
