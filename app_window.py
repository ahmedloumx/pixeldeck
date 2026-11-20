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
import signal
import sys
from pathlib import Path
from urllib.parse import urlsplit

import gi

import chrome
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

# Swapping one panel for another (Music for Terminal) has left the old panel's pixels on
# screen, flickering against the new ones. The suspected cause is WebKitGTK composing only
# the parts of a frame it believes changed; with this off it composes every frame whole.
# Not reproduced off the real display, so the cause is unconfirmed and the cost unmeasured.
DAMAGE_FEATURES = ("UseDamagingInformationForCompositing",)


def _full_frames(settings):
    try:
        features = WebKit.Settings.get_all_features()
    except AttributeError:  # WebKitGTK older than 2.42: no feature list, nothing to turn off
        return
    for i in range(features.get_length()):
        feature = features.get(i)
        if feature.get_identifier() in DAMAGE_FEATURES:
            settings.set_feature_enabled(feature, False)


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
        # The page's top bar is the title bar: it asks here to move, maximize, minimize, close.
        self.content.register_script_message_handler("pixeldeck", None)
        self.content.connect("script-message-received::pixeldeck", self._on_chrome)
        self.titlebar = chrome.titlebar_mode(STATE)
        if self.selftest:
            self.content.add_script(WebKit.UserScript.new(
                CATCH_ERRORS, WebKit.UserContentInjectedFrames.TOP_FRAME,
                WebKit.UserScriptInjectionTime.START, None, None))
        # The page plays sound only when you ask it to (Play on a video, Spotify's keys), so the
        # window lets media start with sound: else YouTube's player, in its own frame, could not
        # start when you press Play outside it.
        policies = WebKit.WebsitePolicies(autoplay=WebKit.AutoplayPolicy.ALLOW)
        view = WebKit.WebView(network_session=self.session, user_content_manager=self.content, website_policies=policies)
        settings = view.get_settings()
        settings.set_enable_developer_extras(True)
        settings.set_javascript_can_open_windows_automatically(True)
        settings.set_enable_back_forward_navigation_gestures(False)
        _full_frames(settings)
        self.main = self._window(view, *self._geometry())
        # The dashboard sits in an overlay, so YouTube's own page can lie on the TV's screen.
        self.main_view, self.yt_view, self.yt_where, self.yt_window, self.yt_place = view, None, None, None, None
        self.main.set_child(None)
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(view)
        self.overlay.connect("get-child-position", self._yt_position)
        self.main.set_child(self.overlay)
        self.main.connect("close-request", self._save_geometry)
        self.main.connect("notify::maximized", lambda *_: self._chrome_state())
        self._apply_titlebar()
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

    # ------------------------------------------------------------ title bar

    def _apply_titlebar(self):
        # The title bar slot holds an empty box, which hides the system title bar and keeps the
        # window's edges for resizing. A header bar waits inside it, shown only when you choose
        # the system's: GTK refuses a new title bar once the window is up, but showing or hiding
        # a child is fine. Popped-out panels keep their usual title bar.
        if getattr(self, "headerbar", None) is None:
            self.headerbar = Gtk.HeaderBar(hexpand=True)
            slot = Gtk.Box()
            slot.append(self.headerbar)
            self.main.set_titlebar(slot)
            slot.remove_css_class("titlebar")  # the theme gives that class a height and a fill
        self.headerbar.set_visible(self.titlebar == "system")

    def _chrome_state(self):
        if not self.main:
            return
        state = json.dumps({"maximized": bool(self.main.is_maximized()), "titlebar": self.titlebar})
        self.main_view.evaluate_javascript(
            f"typeof PixelChrome !== 'undefined' && PixelChrome.state({state})", -1, None, None, None, None, None)

    def _on_chrome(self, _manager, value):
        try:
            request = chrome.parse(value.to_json(0))
        except (TypeError, GLib.Error):
            request = None
        if not request or not self.main:
            return
        action, win = request["action"], self.main
        if action == "youtube":
            self._youtube(request["id"], request["title"], request["mode"])
            return
        if action == "ytplace":
            self.yt_place = request
            self._yt_apply()
            return
        if action == "ytctl":
            self._yt_command(request["cmd"])
            return
        if action == "move":
            surface, seat = win.get_surface(), win.get_display().get_default_seat()
            device = seat.get_pointer() if seat else None
            if surface and device:
                surface.begin_move(device, request["button"], request["x"], request["y"], Gdk.CURRENT_TIME)
            return
        if action == "maximize":
            win.unmaximize() if win.is_maximized() else win.maximize()
        elif action == "minimize":
            win.minimize()
        elif action == "close":
            win.close()
            return
        elif action == "titlebar":
            self.titlebar = "system" if request["system"] else "custom"
            self._apply_titlebar()
        self._chrome_state()

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
        if kind == WebKit.PolicyDecisionType.RESPONSE:
            return self._on_response(decision)
        if kind not in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        action = decision.get_navigation_action()
        uri = action.get_request().get_uri()
        if self._inside(uri):
            return False
        # A frame in the page (YouTube's player in the Music panel) loads without a link being
        # followed, and WebKit does not say which frame a navigation is for. So an https load
        # that is not a followed link or a sent form goes on here, and the answer decides: the
        # main frame's goes to the browser (below), a frame's stays in its frame. A click on an
        # outside link, or a new window, still goes straight to the browser and loads nothing here.
        # (Not is_user_gesture(): a frame made by a click on Play carries that click.)
        followed = action.get_navigation_type() in (WebKit.NavigationType.LINK_CLICKED, WebKit.NavigationType.FORM_SUBMITTED,
                                                     WebKit.NavigationType.FORM_RESUBMITTED)
        if kind == WebKit.PolicyDecisionType.NAVIGATION_ACTION and not followed and urlsplit(uri).scheme == "https":
            return False
        decision.ignore()
        self._outside(uri)
        return True

    def _on_response(self, decision):
        if not decision.is_main_frame_main_resource():
            return False
        uri = decision.get_request().get_uri()
        if self._inside(uri):
            return False
        decision.ignore()
        self._outside(uri)
        return True

    def _outside(self, uri):
        if urlsplit(uri).scheme not in ("http", "https"):
            return
        if self.selftest:  # a check never opens a browser: it reports what would have gone there
            self.selftest.setdefault("outside", []).append(uri)
            return
        Gio.AppInfo.launch_default_for_uri(uri, None)

    # ------------------------------------------------------------ YouTube's own page, in the TV

    # Hosts YouTube's page may show: YouTube, and Google's sign-in and consent pages for it.
    YT_HOSTS = ("youtube.com", "google.com", "gstatic.com", "googleusercontent.com", "ytimg.com", "ggpht.com")
    YT_CSS = b"""
    window.pd-yt { background: #0b0d12; }
    .pd-yt-bar { background: #161a22; border-bottom: 3px solid #f6c445; padding: 4px 6px 4px 8px; }
    .pd-yt-tag { color: #0b0d12; background: #f6c445; font-family: monospace; font-weight: 800; font-size: 9px; letter-spacing: 1px; padding: 2px 6px; }
    .pd-yt-title { color: #e9ecf2; font-family: monospace; font-weight: 700; font-size: 12px; }
    .pd-yt-btn { min-height: 22px; min-width: 22px; padding: 0 6px; border-radius: 0; border: 2px solid #3a3f4b; background: #0f1218; color: #e9ecf2; font-family: monospace; font-weight: 800; box-shadow: none; }
    .pd-yt-btn:hover, .pd-yt-btn:checked { border-color: #f6c445; color: #f6c445; }
    .pd-yt-bezel { background: #1d212b; padding: 6px; border: 3px solid #2b303c; }
    .pd-yt-foot { color: #8b93a3; font-family: monospace; font-size: 9px; letter-spacing: 1px; padding: 3px 8px; background: #0f1218; }
    """
    # The crop: YouTube's own player, controls and all, fills the view; the rest of its page is
    # hidden around it, as a user style would. Nothing is drawn on the player.
    YT_CROP = """
    html, body, ytd-app, ytd-page-manager, ytd-watch-flexy { overflow: hidden !important; background: #000 !important; }
    ytd-masthead, #masthead-container, #below, #secondary, #guide, tp-yt-app-drawer, ytd-mini-guide-renderer { visibility: hidden !important; }
    #movie_player { position: fixed !important; inset: 0 !important; width: 100vw !important; height: 100vh !important;
      z-index: 2147483647 !important; visibility: visible !important; background: #000 !important; }
    #movie_player .html5-video-container, #movie_player video.html5-main-video { left: 0 !important; top: 0 !important;
      width: 100vw !important; height: 100vh !important; object-fit: contain !important; }
    """
    YT_STATE = ("(() => { const v = document.querySelector('video.html5-main-video') || document.querySelector('video');"
                " return JSON.stringify(v ? { playing: !v.paused && !v.ended, time: v.currentTime || 0,"
                " duration: isFinite(v.duration) ? v.duration : 0, ended: v.ended } : {}); })()")

    def _youtube(self, video_id, title, mode="window"):
        """youtube.com itself, for a video its owner keeps on YouTube: an embedded player refuses
        it, YouTube's own page plays it. By default it sits on the TV's screen in the dashboard
        (dock); it can pop out into a small framed window and back."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        view = self._yt_view()
        self.yt_title = title or "YouTube"
        self._yt_move(mode)
        view.load_uri("about:blank" if self.selftest and self.selftest.get("offline_youtube") else url)
        if self.selftest is not None:
            self.selftest.setdefault("youtube", []).append({"url": url, "mode": mode})
        if not getattr(self, "yt_poll", 0):
            self.yt_poll = GLib.timeout_add(1000, self._yt_report)

    def _yt_view(self):
        if getattr(self, "yt_view", None) is not None:
            return self.yt_view
        if not getattr(self, "yt_css", None):
            self.yt_css = Gtk.CssProvider()
            self.yt_css.load_from_data(self.YT_CSS, -1)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.yt_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # Its own content manager: YouTube's page never reaches the dashboard's message handler.
        self.yt_content = WebKit.UserContentManager()
        self.yt_crop = WebKit.UserStyleSheet.new(self.YT_CROP, WebKit.UserContentInjectedFrames.TOP_FRAME, WebKit.UserStyleLevel.USER,
                                                 ["https://www.youtube.com/watch*"], None)
        self.yt_content.add_style_sheet(self.yt_crop)
        self.yt_cropped = True
        policies = WebKit.WebsitePolicies(autoplay=WebKit.AutoplayPolicy.ALLOW)
        view = WebKit.WebView(network_session=self.session, user_content_manager=self.yt_content, website_policies=policies)
        view.set_vexpand(True)
        view.set_hexpand(True)
        view.connect("decide-policy", self._on_youtube_policy)
        view.connect("create", lambda _v, action: self._outside(action.get_request().get_uri()) or None)
        rgba = Gdk.RGBA()
        rgba.parse("#000000")
        view.set_background_color(rgba)
        self.yt_view = view
        return view

    def _yt_move(self, where):
        """Put YouTube's page on the TV's screen (dock) or in its own framed window."""
        view = self._yt_view()
        parent = view.get_parent()
        if parent is not None and where == self.yt_where:
            return
        if parent is self.overlay:
            self.overlay.remove_overlay(view)
        elif parent is not None:
            parent.remove(view)
        self.yt_where = where
        if where == "dock":
            if self.yt_window is not None:
                self.yt_window.set_visible(False)
            view.set_halign(Gtk.Align.START)
            view.set_valign(Gtk.Align.START)
            self.overlay.add_overlay(view)
            self.overlay.set_clip_overlay(view, True)
            self._yt_apply()
        else:
            view.set_halign(Gtk.Align.FILL)
            view.set_valign(Gtk.Align.FILL)
            view.set_visible(True)
            win = self._yt_frame()
            self.yt_bezel.append(view)
            self.yt_label.set_text(getattr(self, "yt_title", "YouTube"))
            win.set_title(f"YouTube: {self.yt_title}")
            win.present()
        self._yt_report()

    def _yt_position(self, _overlay, widget, allocation):
        # Docked, YouTube's page takes exactly the TV screen's box, whatever size it had before.
        p = self.yt_place
        if widget is not self.yt_view or self.yt_where != "dock" or not p:
            return False
        allocation.x, allocation.y, allocation.width, allocation.height = max(0, p["x"]), max(0, p["y"]), max(1, p["w"]), max(1, p["h"])
        return True

    def _yt_apply(self):
        view, p = self.yt_view, self.yt_place
        if view is None or self.yt_where != "dock":
            return
        if not p or not p["show"] or p["w"] < 40 or p["h"] < 30:
            view.set_visible(False)
            return
        view.set_visible(True)
        view.queue_resize()

    def _yt_frame(self):
        """The framed window YouTube's page pops out into: a bar with the bot, the title, the
        crop switch, back to the TV and close; a bezel; a line saying whose page it is."""
        if self.yt_window is not None:
            return self.yt_window
        win = Gtk.Window(application=self, title="YouTube", default_width=760, default_height=480)
        win.add_css_class("pd-yt")
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("pd-yt-bar")
        icon = Gtk.Image.new_from_icon_name(ICON)
        icon.set_pixel_size(24)
        tag = Gtk.Label(label="PLAYING ON YOUTUBE")
        tag.add_css_class("pd-yt-tag")
        label = Gtk.Label(label="YouTube", xalign=0, hexpand=True, ellipsize=3)  # 3: ellipsize at the end
        label.add_css_class("pd-yt-title")
        page = Gtk.ToggleButton(label="PAGE", tooltip_text="Show YouTube's whole page (to sign in, read, subscribe)")
        page.connect("toggled", lambda b: self._yt_page(b.get_active()))
        dock = Gtk.Button(label="TV", tooltip_text="Back into PixelDeck's TV")
        dock.connect("clicked", lambda _b: self._yt_command("dock"))
        close = Gtk.Button(label="X", tooltip_text="Stop and close")
        close.connect("clicked", lambda _b: self._yt_command("stop"))
        for b in (page, dock, close):
            b.add_css_class("pd-yt-btn")
        for w in (icon, tag, label, page, dock, close):
            bar.append(w)
        win.set_titlebar(Gtk.WindowHandle(child=bar))  # the bar moves the window, as a title bar does
        bezel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        bezel.add_css_class("pd-yt-bezel")
        foot = Gtk.Label(label="YOUTUBE'S OWN PAGE, IN A PIXELDECK WINDOW", xalign=0)
        foot.add_css_class("pd-yt-foot")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(bezel)
        body.append(foot)
        win.set_child(body)
        # closing the window stops the video, as the X does
        win.connect("close-request", lambda _w: (self._yt_command("stop"), True)[1])
        self.yt_window, self.yt_bezel, self.yt_label, self.yt_page_btn = win, bezel, label, page
        return win

    def _yt_page(self, whole):
        """The crop off (YouTube's whole page, to sign in or read) or back on."""
        if self.yt_view is None or whole == (not self.yt_cropped):
            return
        if whole:
            self.yt_content.remove_style_sheet(self.yt_crop)
        else:
            self.yt_content.add_style_sheet(self.yt_crop)
        self.yt_cropped = not whole

    def _yt_command(self, cmd):
        view = self.yt_view
        if view is None:
            return
        if cmd in ("toggle", "pause"):
            act = "v.paused ? v.play() : v.pause()" if cmd == "toggle" else "v.pause()"
            view.evaluate_javascript(f"(() => {{ const v = document.querySelector('video.html5-main-video') || document.querySelector('video'); if (v) {act}; }})()",
                                     -1, None, None, None, None, None)
        elif cmd == "popout":
            self._yt_move("window")
        elif cmd == "dock":
            self._yt_move("dock")
            if self.main:
                self.main.present()
        elif cmd == "page":
            self._yt_page(self.yt_cropped)
            if getattr(self, "yt_page_btn", None) is not None:
                self.yt_page_btn.set_active(not self.yt_cropped)
        elif cmd == "stop":
            view.load_uri("about:blank")
            if self.yt_where == "dock":
                view.set_visible(False)
            if self.yt_window is not None:
                self.yt_window.set_visible(False)
            if getattr(self, "yt_poll", 0):
                GLib.source_remove(self.yt_poll)
                self.yt_poll = 0
            self._yt_tell({"stopped": True})
            return
        self._yt_report()

    def _yt_report(self):
        """Every second while a video is on: tell the page where YouTube's page is and how it plays."""
        view = self.yt_view
        if view is None:
            self.yt_poll = 0
            return False

        def done(v, result, _data):
            try:
                state = json.loads(v.evaluate_javascript_finish(result).to_string() or "{}")
            except (GLib.Error, TypeError, ValueError):
                state = {}
            self._yt_tell(state if isinstance(state, dict) else {})
        view.evaluate_javascript(self.YT_STATE, -1, None, None, None, done, None)
        return True

    def _yt_tell(self, state):
        if not self.main_view:
            return
        state = {**state, "where": getattr(self, "yt_where", None), "page": not getattr(self, "yt_cropped", True)}
        self.main_view.evaluate_javascript(f"typeof MusicTV !== 'undefined' && MusicTV.nativeState && MusicTV.nativeState({json.dumps(state)})",
                                           -1, None, None, None, None, None)

    def _on_youtube_policy(self, _view, decision, kind):
        if kind == WebKit.PolicyDecisionType.RESPONSE:
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        parts = urlsplit(uri)
        host = (parts.hostname or "").lower()
        if uri.startswith("about:") or (parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in self.YT_HOSTS)):
            if kind == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
                decision.ignore()
                self._outside(uri)
                return True
            return False
        # anything else a YouTube page links to opens in your browser, not in this window
        decision.ignore()
        self._outside(uri)
        return True

    def _on_create(self, view, _action):
        # A panel popped out of the workspace gets a window of its own, same profile.
        child = WebKit.WebView(related_view=view, user_content_manager=self.content, website_policies=view.get_website_policies())
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
            report["sent_to_browser"] = self.selftest.get("outside", [])
            report["youtube_windows"] = self.selftest.get("youtube", [])
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
    # A stop (SIGTERM from a restart, Ctrl+C) quits like closing the window does, so WebKit ends
    # its page process itself instead of leaving it to abort ten seconds later.
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (app.quit(), GLib.SOURCE_REMOVE)[1])
    code = app.run([sys.argv[0]])
    sys.exit(app.status or code)


if __name__ == "__main__":
    main()
