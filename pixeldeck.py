#!/usr/bin/env python3
"""Start PixelDeck: run the local server if it is not running, then open its window.

  python3 pixeldeck.py            start (if needed) and open the window
  python3 pixeldeck.py --status   say whether the server runs
  python3 pixeldeck.py --stop     stop the server (and every terminal in it)
  python3 pixeldeck.py --server   run the server in this console, for debugging

The window, best first:
  Linux    its own GTK 4 + WebKitGTK window, when PyGObject and WebKitGTK 6 are installed
  all      a Chromium-family browser (Chrome, Edge, Brave, Chromium) in app mode,
           with a profile of its own, so it opens as a separate app window
  all      the default browser
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--stop", action="store_true")
    group.add_argument("--server", action="store_true")
    args = parser.parse_args()
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
