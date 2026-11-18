#!/usr/bin/env python3
"""Start PixelDeck: run the local server if it is not running, then open its window.

  python3 pixeldeck.py            start (if needed) and open the window
  python3 pixeldeck.py --status   say whether the server runs
  python3 pixeldeck.py --stop     stop the server (and every terminal in it)
  python3 pixeldeck.py --server   run the server in this console, for debugging
  python3 pixeldeck.py --propose --repo R --branch B --title T [--description-file F]
                                  ask PixelDeck to open a pull request and wait for you
                                  (exit 0 opened, 2 declined, 3 still waiting, 4 error)
  python3 pixeldeck.py --stop-if-idle    stop the server unless a terminal or a proposal is
                                  open (exit 5 then); the installer runs it before an update
  python3 pixeldeck.py --forget-tokens   remove the tokens from the system's credential store

The packaged app is the same program, named pixeldeck (and pixeldeckw, without a console, on Windows).

The window, best first:
  Linux    its own GTK 4 + WebKitGTK window, when PyGObject and WebKitGTK 6 are installed
  all      a Chromium-family browser (Chrome, Edge, Brave, Chromium) in app mode,
           with a profile of its own, so it opens as a separate app window
  all      the default browser
"""

import argparse
import json
import os
import secrets
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
import credentials
import version

HERE = config.APP_DIR
WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
# The packaged app is one program in two forms: pixeldeck (a console program, for terminals and
# agents) and, on Windows, pixeldeckw (no console, for the Start menu). Either one starts the
# server as `pixeldeck --server`.
CLI = "pixeldeck" if config.FROZEN else "pixeldeck.py"


def tell(message, error=False):
    """Print it, or show it in a box from pixeldeckw, whose output nobody would see."""
    if sys.stdout is not None:
        print(message, file=sys.stderr if error else sys.stdout, flush=True)
    elif WINDOWS:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, config.APP_NAME, 0x10 if error else 0x40)


# Where the common systems keep their certificate bundle: Debian and Ubuntu, Fedora and RHEL
# (two names), macOS and Alpine.
CERT_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
                "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", "/etc/ssl/cert.pem")


def prepare_environment(environ=None, verify_paths=None, isfile=os.path.isfile):
    """For the packaged app on macOS and Linux, before it starts anything.

    PyInstaller points LD_LIBRARY_PATH at the bundle and keeps the old value in
    LD_LIBRARY_PATH_ORIG. Every program PixelDeck starts (git, ssh, claude, the terminals'
    shells) would load the bundled libraries; they get the old value back. This process loaded
    its own already. And the bundled OpenSSL looks for certificates where the build machine
    keeps them; when they are not there, it gets this system's bundle (SSL_CERT_FILE)."""
    environ = os.environ if environ is None else environ
    if not config.FROZEN or WINDOWS:
        return
    for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        if f"{name}_ORIG" in environ:
            environ[name] = environ.pop(f"{name}_ORIG")
        elif name == "LD_LIBRARY_PATH":
            environ.pop(name, None)
    if "SSL_CERT_FILE" not in environ:
        import ssl
        paths = verify_paths() if verify_paths else ssl.get_default_verify_paths()
        if not (paths.cafile or paths.capath):  # None when the compiled-in place does not exist
            found = next((path for path in CERT_BUNDLES if isfile(path)), None)
            if found:
                environ["SSL_CERT_FILE"] = found


def server_argv():
    if not config.FROZEN:
        return [sys.executable, str(HERE / "server.py"), "--port", str(config.port())]
    image = config.appimage()
    if image:
        # Through the AppImage file, so the server gets a mount of its own: the one this
        # launcher runs from goes away when it exits, and the server's files with it.
        return [str(image), "--server"]
    # The console form, so the server's output reaches its log even when pixeldeckw starts it.
    console = Path(sys.executable).with_name("pixeldeck.exe")
    return [str(console if console.is_file() else sys.executable), "--server"]


def url():
    return f"http://127.0.0.1:{config.port()}/"


def health(timeout=1.0):
    """What the running server says about itself, or None when nothing answers."""
    try:
        with urllib.request.urlopen(url() + "healthz", timeout=timeout) as res:
            raw = res.read()
    except OSError:
        return None
    try:
        info = json.loads(raw)
    except ValueError:
        info = None
    return info if isinstance(info, dict) else {"ok": True}  # an older server answers a bare "ok"


def alive(timeout=1.0):
    return health(timeout) is not None


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
    # The server echoes this back, so we know it is ours. Its pid is not enough: a venv's
    # python.exe on Windows runs the real interpreter as a child, with a pid of its own.
    start = secrets.token_hex(8)
    with log:  # the server has its own handle on it
        # Not from inside this launcher's AppImage mount, which must be free to go when it exits.
        cwd = config.folder("state") if config.appimage() else HERE
        proc = subprocess.Popen(server_argv(), cwd=str(cwd), env={**os.environ, "PIXELDECK_START_ID": start},
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **flags)
    for _ in range(80):
        info = health()
        if info and info.get("start") == start:
            state_file("server.pid").write_text(str(info["pid"]))
            return
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    # Ours lost the port to a server that was already there (too busy to answer the first
    # check, or started a moment earlier): that one is PixelDeck, and the pid file names it.
    info = health(timeout=3)
    if info:
        if info.get("pid"):
            state_file("server.pid").write_text(str(info["pid"]))
        return
    tell(f"PixelDeck did not start; see {state_file('server.log')}", error=True)
    sys.exit(1)


def stop_server():
    pid_file = state_file("server.pid")
    # A pid file outlives a crash, and the system reuses numbers: signal only a server that
    # answers, and believe what it says its pid is over the file.
    info = health(timeout=3)
    if info is None:
        pid_file.unlink(missing_ok=True)
        print("PixelDeck was not running")
        return
    try:
        pid = int(info.get("pid") or pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        print(f"PixelDeck answers on {url()} but its pid file is missing or stale; stop it from its own console")
        return
    pid_file.unlink(missing_ok=True)
    for _ in range(100):  # a start right after must find the port free
        if not alive(timeout=0.5):
            break
        time.sleep(0.1)
    print("PixelDeck stopped")


def stop_if_idle():
    """For the installer: stop the server, unless that would cut off a terminal or a proposal."""
    info = health(timeout=3)
    if info is None:
        return 0
    # An older server does not say (None): it counts as busy.
    if info.get("terminals") != 0 or info.get("proposals") != 0:
        tell(f"PixelDeck has open terminals or a pending proposal. Finish them, or run  {CLI} --stop  "
             "to close them, then try again.", error=True)
        return 5
    stop_server()
    return 0


def forget_tokens():
    store = credentials.store()
    for key in config.SECRET_KEYS:
        store.delete(key)
    print(f"Removed PixelDeck's tokens from {store.name}" if store.name else "This system has no credential store: nothing to remove")


# ------------------------------------------------------------------ window

def has_webkit_window():
    # The packaged app cannot run app_window.py, and GTK does not bundle well: it uses a browser.
    if config.FROZEN or not sys.platform.startswith("linux"):
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
    prepare_environment()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--stop", action="store_true")
    group.add_argument("--server", action="store_true")
    group.add_argument("--propose", action="store_true", help="ask PixelDeck to open a pull request")
    group.add_argument("--stop-if-idle", action="store_true", help="stop the server unless a terminal or a proposal is open")
    group.add_argument("--forget-tokens", action="store_true", help="remove the tokens from the credential store")
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
    if args.stop_if_idle:
        sys.exit(stop_if_idle())
    if args.forget_tokens:
        forget_tokens()
        return
    if args.server:
        os.chdir(HERE)
        sys.argv = [str(HERE / "server.py"), "--port", str(config.port())]
        import server
        server.main()
        return
    info = health(timeout=3)
    if info is None:
        start_server()
    elif info.get("build") != version.build():
        # After an update the page comes from disk while the server still runs the old code,
        # and routes the new page needs are missing. Restart, but never under open terminals or
        # a pending proposal: a stop ends a Claude run, and the waiting agent loses its answer.
        busy = (info.get("terminals"), info.get("proposals"))
        if busy == (0, 0):
            print("PixelDeck changed since its server started; restarting the server")
            stop_server()
            start_server()
        elif None in busy:  # a server from before /healthz said what a stop would cut off
            tell(f"PixelDeck changed since its server started. To load the new code, run  {CLI} --stop "
                 "(it also closes PixelDeck's terminals)  and start PixelDeck again.")
        else:
            tell("PixelDeck changed since its server started, and it has open terminals or a pending proposal. "
                 f"When they are done, run  {CLI} --stop  and start PixelDeck again.")
    print(f"PixelDeck on {url()} in the {open_window()}")


if __name__ == "__main__":
    main()
