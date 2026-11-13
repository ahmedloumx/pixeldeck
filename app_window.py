#!/usr/bin/env python3
"""PixelDeck's own window on Linux: GTK 4 and WebKitGTK 6, no browser around it.

macOS and Windows open the page in a browser's app mode instead (see pixeldeck.py).

server.py serves the page on 127.0.0.1. This process only shows it, so closing the
window leaves the server and any terminal sessions running. Any page that is not
the dashboard opens in the default browser instead of inside the window.

  app_window.py [--url URL]
  app_window.py --selftest [--seconds N] [--script FILE] [--snapshot OUT.png]
      load the page with an empty profile, run a check, print JSON, quit
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

import gi

import config

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gdk, Gio, GLib, Gtk, WebKit  # noqa: E402

APP_ID = config.APP_ID
ICON = config.ICON_NAME
TITLE = "PixelDeck"
STATE = config.folder("state")
DATA = config.folder("data") / "webview"
CACHE = config.folder("cache") / "webview"
GEOMETRY = STATE / "window.json"
PAPER = "#14171b"  # the page background, so loading never flashes white

# Self-test only: keep every page error so the report can list them.
CATCH_ERRORS = """
window.__deskErrors = [];
addEventListener('error', (e) => __deskErrors.push(String(e.message || e)));
addEventListener('unhandledrejection', (e) => __deskErrors.push('rejection: ' + String(e.reason && e.reason.message || e.reason)));
const __ce = console.error.bind(console);
console.error = (...a) => { __deskErrors.push(a.map(String).join(' ')); __ce(...a); };
"""

DEFAULT_CHECK = """
return JSON.stringify({
  title: document.title,
  areas: [...document.querySelectorAll('.slot')].filter((s) => !s.hidden).map((s) => s.dataset.area),
  chips: document.querySelectorAll('.sec-chip').length,
  errors: window.__deskErrors || [],
});
"""


class DeskApp(Gtk.Application):
    def __init__(self, url, selftest=None):
        flags = Gio.ApplicationFlags.NON_UNIQUE if selftest else Gio.ApplicationFlags.DEFAULT_FLAGS
        super().__init__(application_id=APP_ID, flags=flags)
        self.url = url
        self.base = urlsplit(url)
        self.selftest = selftest
        self.main = None
        self.status = 0
        self.connect("activate", self._on_activate)

    # ---------------------------------------------------------------- set up

    def _on_activate(self, _app):
        if self.main:
            self.main.present()
            return
        Gtk.Settings.get_default().props.gtk_application_prefer_dark_theme = True
        Gtk.Window.set_default_icon_name(ICON)
        self._actions()
        if self.selftest:
            self.session = WebKit.NetworkSession.new_ephemeral()
        else:
            DATA.mkdir(parents=True, exist_ok=True)
            CACHE.mkdir(parents=True, exist_ok=True)
            self.session = WebKit.NetworkSession.new(str(DATA), str(CACHE))
        self.content = WebKit.UserContentManager()
        if self.selftest:
            self.content.add_script(WebKit.UserScript.new(
                CATCH_ERRORS, WebKit.UserContentInjectedFrames.TOP_FRAME,
                WebKit.UserScriptInjectionTime.START, None, None))
        view = WebKit.WebView(network_session=self.session, user_content_manager=self.content)
        settings = view.get_settings()
        settings.set_enable_developer_extras(True)
        settings.set_javascript_can_open_windows_automatically(True)
        settings.set_enable_back_forward_navigation_gestures(False)
        self.main = self._window(view, *self._geometry())
        self.main.connect("close-request", self._save_geometry)
        if self.selftest:
            view.connect("load-changed", self._selftest_loaded)
            GLib.timeout_add_seconds(self.selftest["seconds"] + 90, self._selftest_timeout)
        view.load_uri(self.url)
        self.main.present()

    def _window(self, view, width, height, maximized=False):
        win = Gtk.ApplicationWindow(application=self, title=TITLE)
        win.set_default_size(width, height)
        if maximized:
            win.maximize()
        rgba = Gdk.RGBA()
        rgba.parse(PAPER)
        view.set_background_color(rgba)
        win.set_child(view)
        view.connect("notify::title", lambda v, _p: win.set_title(v.get_title() or TITLE))
        view.connect("decide-policy", self._on_policy)
        view.connect("create", self._on_create)
        view.connect("close", lambda _v: win.destroy())
        view.connect("load-failed", self._on_load_failed)
        self._file_drops(view)
        return win

    def _file_drops(self, view):
        """Files dragged in from a file manager reach the page as their paths.

        WebKitGTK shows the page a dragged file as a link (text/uri-list) and hides both the
        file and its path, so the page alone cannot paste it. A drop target in the capture
        phase takes file drops before WebKit does and hands the real paths to the page.
        """
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_drop(_target, value, x, y):
            paths = [f.get_path() for f in value.get_files() if f.get_path()]
            if not paths:
                return False
            zoom = view.get_zoom_level() or 1.0
            # WebKit refuses a string array as an argument, so the paths travel as JSON.
            script = "return window.PixelDeckDrop ? window.PixelDeckDrop(JSON.parse(paths), x, y) : false"
            args = GLib.Variant("a{sv}", {
                "paths": GLib.Variant("s", json.dumps(paths[:10])),
                "x": GLib.Variant("d", x / zoom),
                "y": GLib.Variant("d", y / zoom),
            })
            view.call_async_javascript_function(script, -1, args, None, None, None, None, None)
            return True

        target.connect("drop", on_drop)
        view.add_controller(target)

    def _actions(self):
        def add(name, keys, handler):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_: handler())
            self.add_action(action)
            self.set_accels_for_action(f"app.{name}", keys)
        # Keys a shell or Claude has little use for: Ctrl+R, Ctrl+Q and Ctrl+minus all mean
        # something in a terminal, and an application shortcut could take them first.
        add("reload", ["F5"], lambda: self._view() and self._view().reload())
        add("zoom-in", ["<Control><Alt>plus", "<Control><Alt>equal"], lambda: self._zoom(0.1))
        add("zoom-out", ["<Control><Alt>minus"], lambda: self._zoom(-0.1))
        add("zoom-reset", ["<Control><Alt>0"], lambda: self._zoom(None))
        add("inspect", ["<Control><Shift>i"], lambda: self._view() and self._view().get_inspector().show())

    def _view(self):
        win = self.get_active_window()
        return win.get_child() if win else None

    def _zoom(self, step):
        view = self._view()
        if view:
            view.set_zoom_level(1.0 if step is None else min(2.0, max(0.5, view.get_zoom_level() + step)))

    # ------------------------------------------------------------- navigation

    def _inside(self, uri):
        if not uri or uri.startswith("about:"):
            return True
        parts = urlsplit(uri)
        return parts.scheme == self.base.scheme and parts.netloc == self.base.netloc

    def _on_policy(self, _view, decision, kind):
        if kind not in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if self._inside(uri):
            return False
        decision.ignore()
        if urlsplit(uri).scheme in ("http", "https"):
            Gio.AppInfo.launch_default_for_uri(uri, None)
        return True

    def _on_create(self, view, _action):
        # A panel popped out of the workspace gets a window of its own, same profile.
        child = WebKit.WebView(related_view=view, user_content_manager=self.content)
        child.set_settings(view.get_settings())
        win = self._window(child, 1180, 780)
        child.connect("ready-to-show", lambda _v: win.present())
        return child

    def _on_load_failed(self, view, _event, uri, error):
        if not self._inside(uri) or error.matches(WebKit.NetworkError.quark(), WebKit.NetworkError.CANCELLED):
            return False
        view.load_alternate_html(
            f"<body style='background:{PAPER};color:#dde;font:15px system-ui;padding:48px'>"
            "<h2>The dashboard server is not running</h2>"
            "<p>Start it again from the app icon, or run <code>tools/pr-dashboard/run.sh</code>, then press F5.</p></body>",
            uri, None)
        return True

    # --------------------------------------------------------------- geometry

    def _geometry(self):
        try:
            g = json.loads(GEOMETRY.read_text())
            return max(800, int(g["width"])), max(560, int(g["height"])), bool(g.get("maximized"))
        except (OSError, ValueError, KeyError, TypeError):
            return 1600, 980, False

    def _save_geometry(self, win):
        if self.selftest:
            return False
        try:
            STATE.mkdir(parents=True, exist_ok=True)
            GEOMETRY.write_text(json.dumps({"width": win.get_width(), "height": win.get_height(), "maximized": win.is_maximized()}))
        except OSError:
            pass
        return False

    # --------------------------------------------------------------- self-test

    def _selftest_loaded(self, view, event):
        if event == WebKit.LoadEvent.FINISHED and not self.selftest.get("started"):
            self.selftest["started"] = True
            GLib.timeout_add_seconds(self.selftest["seconds"], self._selftest_run, view)

    def _selftest_run(self, view):
        body = self.selftest["script"] or DEFAULT_CHECK
        view.call_async_javascript_function(body, -1, None, None, None, None, self._selftest_result, view)
        return False

    def _selftest_result(self, view, result, _data):
        try:
            value = view.call_async_javascript_function_finish(result)
            text = value.to_string() if value.is_string() else value.to_json(0)
        except GLib.Error as err:
            text = json.dumps({"error": err.message})
        try:
            report = json.loads(text)
        except (TypeError, ValueError):
            report = {"result": text}
        if isinstance(report, dict):
            report["windows"] = len(self.get_windows())
            report["webkit"] = f"{WebKit.get_major_version()}.{WebKit.get_minor_version()}.{WebKit.get_micro_version()}"
        print(json.dumps(report, indent=1), flush=True)
        if isinstance(report, dict) and (report.get("errors") or report.get("error")):
            self.status = 1
        if self.selftest["snapshot"]:
            view.get_snapshot(WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE, None, self._selftest_snapshot, None)
        else:
            self.quit()

    def _selftest_snapshot(self, view, result, _data):
        try:
            view.get_snapshot_finish(result).save_to_png(self.selftest["snapshot"])
        except GLib.Error as err:
            print(f"snapshot failed: {err.message}", file=sys.stderr)
            self.status = 1
        self.quit()

    def _selftest_timeout(self):
        print(json.dumps({"error": "self-test timed out"}), flush=True)
        self.status = 1
        self.quit()
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=f"http://127.0.0.1:{config.port()}/")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--seconds", type=int, default=6, help="self-test: wait this long after the load")
    parser.add_argument("--script", help="self-test: file holding an async function body that returns JSON text")
    parser.add_argument("--snapshot", help="self-test: save the visible page to this PNG")
    args = parser.parse_args()
    selftest = None
    if args.selftest:
        selftest = {"seconds": args.seconds, "snapshot": args.snapshot,
                    "script": Path(args.script).read_text() if args.script else None}
    app = DeskApp(args.url, selftest)
    code = app.run([sys.argv[0]])
    sys.exit(app.status or code)


if __name__ == "__main__":
    main()
