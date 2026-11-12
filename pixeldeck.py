#!/usr/bin/env python3
"""Start PixelDeck: run the local server if it is not running, then open its window.

  python3 pixeldeck.py            start (if needed) and open the window
  python3 pixeldeck.py --status   say whether the server runs
  python3 pixeldeck.py --stop     stop the server (and every terminal in it)
  python3 pixeldeck.py --server   run the server in this console, for debugging
  python3 pixeldeck.py --propose --repo R --branch B --title T [--description-file F]
                                  ask PixelDeck to open a pull request and wait for you
                                  (exit 0 opened, 2 declined, 3 still waiting, 4 error)

The window, best first:
  Linux    its own GTK 4 + WebKitGTK window, when PyGObject and WebKitGTK 6 are installed
  all      a Chromium-family browser (Chrome, Edge, Brave, Chromium) in app mode,
           with a profile of its own, so it opens as a separate app window
  all      the default browser
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import config

HERE = config.APP_DIR
WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"


def url():
    return f"http://127.0.0.1:{config.port()}/"


def alive():
    try:
        with urllib.request.urlopen(url() + "healthz", timeout=1) as res:
            return res.status == 200
    except OSError:
        return False


def state_file(name):
    folder = config.folder("state")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def start_server():
    log = open(state_file("server.log"), "ab")
    if WINDOWS:
        flags = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    else:
        flags = {"start_new_session": True}
    proc = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--port", str(config.port())],
                            cwd=str(HERE), stdin=subprocess.DEVNULL, stdout=log, stderr=log, **flags)
    state_file("server.pid").write_text(str(proc.pid))
    for _ in range(80):
        if alive():
            return
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    sys.exit(f"PixelDeck did not start; see {state_file('server.log')}")


def stop_server():
    pid_file = state_file("server.pid")
    # A pid file outlives a crash, and the system reuses numbers: signal only a server that answers.
    if not alive():
        pid_file.unlink(missing_ok=True)
        print("PixelDeck was not running")
        return
    try:
        os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
    except (OSError, ValueError):
        print(f"PixelDeck answers on {url()} but its pid file is missing or stale; stop it from its own console")
        return
    pid_file.unlink(missing_ok=True)
    print("PixelDeck stopped")


# ------------------------------------------------------------------ window

def has_webkit_window():
    if not sys.platform.startswith("linux"):
        return False
    probe = ('import gi; gi.require_version("Gtk", "4.0"); gi.require_version("WebKit", "6.0"); '
             'from gi.repository import WebKit')
    return subprocess.run([sys.executable, "-c", probe], capture_output=True).returncode == 0


def chromium_browsers():
    """Chromium-family browsers that can open a page as an app window, likeliest first."""
    if WINDOWS:
        roots = [os.environ.get(k) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        tails = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
                 r"BraveSoftware\Brave-Browser\Application\brave.exe"]
        paths = [str(Path(root) / tail) for tail in tails for root in roots if root]
        names = ["msedge", "chrome", "brave"]
    elif MACOS:
        apps = [("Google Chrome", "Google Chrome"), ("Microsoft Edge", "Microsoft Edge"),
                ("Brave Browser", "Brave Browser"), ("Chromium", "Chromium")]
        paths = [str(base / f"{app}.app" / "Contents" / "MacOS" / exe)
                 for app, exe in apps for base in (Path("/Applications"), Path.home() / "Applications")]
        names = []
    else:
        paths = []
        names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"]
    found = [p for p in paths if Path(p).is_file()]
    found += [shutil.which(n) for n in names if shutil.which(n)]
    return list(dict.fromkeys(found))


def detach():
    if WINDOWS:
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def open_window():
    if has_webkit_window():
        # Become the window, as the launcher's own process: GNOME ties the dock icon and the
        # startup spinner to the process it started.
        print(f"PixelDeck on {url()} in its own window", flush=True)
        os.execv(sys.executable, [sys.executable, str(HERE / "app_window.py"), "--url", url()])
    for browser in chromium_browsers():
        argv = [browser, f"--user-data-dir={state_file('chrome')}", "--no-first-run",
                "--no-default-browser-check", f"--app={url()}"]
        if sys.platform.startswith("linux"):
            argv.append(f"--class={config.APP_ID}")
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detach())
            return f"{Path(browser).stem} app window"
        except OSError:
            continue
    webbrowser.open(url(), new=1)
    return "default browser"


# ---------------------------------------------------------------- propose

def _call(method, path, body=None, key=""):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url() + "api/" + path, data=data, method=method,
                                     headers={"X-Agent-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as res:
            return json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get("error")
        except ValueError:
            message = None
        raise RuntimeError(message or f"HTTP {exc.code}") from None


def propose(args):
    """Ask PixelDeck to open a pull request, then wait for the person to decide.

    Asking again for the same branch returns the same proposal, so a caller cut off by its
    own time limit can run the very same command again to keep waiting or to read the answer.
    """
    try:
        key = json.loads(state_file("agent.key").read_text())["key"]
    except (OSError, ValueError, KeyError):
        print("PixelDeck is not running (no propose key). Start it, then run this again.", file=sys.stderr)
        return 4
    description = None
    if args.description_file:
        description = Path(args.description_file).read_text(encoding="utf-8")
    elif args.description is not None:
        description = args.description
    body = {"repo": args.repo, "branch": args.branch, "close_source_branch": not args.keep_branch}
    if args.dest:
        body["dest"] = args.dest
    if args.title:
        body["title"] = args.title
    if description is not None:
        body["description"] = description
    try:
        item = _call("POST", "propose", body, key)
    except (RuntimeError, OSError) as exc:
        print(f"PixelDeck refused the proposal: {exc}", file=sys.stderr)
        return 4
    if item["status"] == "pending":
        print(f"Waiting for you in PixelDeck: {item['repo']} {item['branch']} into {item['dest']}: {item['title']}", flush=True)
    deadline = time.time() + args.wait
    while item["status"] in ("pending", "opening") and time.time() < deadline:
        time.sleep(1.5)
        try:
            item = _call("GET", f"proposal?id={item['id']}", key=key)
        except (RuntimeError, OSError) as exc:
            print(f"Lost PixelDeck while waiting ({exc}). Run the same command again.", file=sys.stderr)
            return 4
    if item["status"] == "opened":
        result = item.get("result") or {}
        print(f"Opened pull request #{result.get('id')}: {result.get('url')}")
        return 0
    if item["status"] == "declined":
        print(f"Declined in PixelDeck: {item.get('reason') or 'no reason given'}")
        return 2
    print("Still waiting for you in PixelDeck. Run the same command again to keep waiting.")
    return 3


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--stop", action="store_true")
    group.add_argument("--server", action="store_true")
    group.add_argument("--propose", action="store_true", help="ask PixelDeck to open a pull request")
    parser.add_argument("--repo", help="--propose: the repository slug")
    parser.add_argument("--branch", help="--propose: the pushed branch")
    parser.add_argument("--dest", help="--propose: the branch to merge into (default: the repo's usual one)")
    parser.add_argument("--title", help="--propose: the title")
    parser.add_argument("--description", help="--propose: the description")
    parser.add_argument("--description-file", help="--propose: read the description from this file")
    parser.add_argument("--keep-branch", action="store_true", help="--propose: do not close the branch after the merge")
    parser.add_argument("--wait", type=int, default=100, help="--propose: seconds to wait before exiting with 3 (default 100)")
    args = parser.parse_args()
    if args.propose:
        if not args.repo or not args.branch:
            parser.error("--propose needs --repo and --branch")
        sys.exit(propose(args))
    if args.status:
        print(f"running on {url()} (log: {state_file('server.log')})" if alive() else "stopped")
        return
    if args.stop:
        stop_server()
        return
    if args.server:
        os.chdir(HERE)
        sys.argv = [str(HERE / "server.py"), "--port", str(config.port())]
        import server
        server.main()
        return
    if not alive():
        start_server()
    print(f"PixelDeck on {url()} in the {open_window()}")


if __name__ == "__main__":
    main()
