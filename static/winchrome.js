"use strict";

/* The top bar as the window's title bar. Inside the PixelDeck window (not a browser, not a
   panel popped out into its own window) the bar's empty parts move the window, a double
   click maximizes it, and its own buttons minimize, maximize and close it. The window
   answers with its state, so the maximize button shows restore when it should. */

const PixelChrome = (() => {
  const handler = () => window.webkit?.messageHandlers?.pixeldeck;
  let current = { maximized: false, titlebar: "custom" };
  // Anything that does something on a press keeps it; only bare bar moves the window.
  const ACTIVE = "button, a, input, select, textarea, label, summary, [role='menuitem'], [tabindex], .st-music, .scope, .mb-pop, .jump-btn, .st-tray, .st-pins";

  const available = () => !!handler() && !(typeof Layout !== "undefined" && Layout.isSolo());
  function send(action, extra = {}) {
    if (!available()) return false;
    try { handler().postMessage({ action, ...extra }); return true; } catch { return false; }
  }
  const own = () => available() && current.titlebar !== "system";

  function paint() {
    const box = document.getElementById("winctl");
    document.documentElement.classList.toggle("has-own-titlebar", own());
    document.documentElement.classList.toggle("is-maximized", !!current.maximized);
    if (!box) return;
    box.hidden = !own();
    const max = box.querySelector("[data-win='maximize']");
    if (max) {
      max.setAttribute("aria-label", current.maximized ? "Restore" : "Maximize");
      max.dataset.tip = current.maximized ? "Restore" : "Maximize";
    }
  }
  function state(next) {
    current = { ...current, ...(next || {}) };
    paint();
  }

  function mount(bar) {
    if (!available()) return;
    bar.addEventListener("pointerdown", (e) => {
      if (!own() || e.button !== 0 || e.target.closest(ACTIVE)) return;
      e.preventDefault();
      send("move", { x: e.clientX, y: e.clientY, button: 1 });
    });
    bar.addEventListener("dblclick", (e) => {
      if (own() && !e.target.closest(ACTIVE)) send("maximize");
    });
    document.getElementById("winctl")?.addEventListener("click", (e) => {
      const b = e.target.closest("[data-win]");
      if (b) send(b.dataset.win);
    });
    paint();
    send("hello");
  }

  return { available, send, state, mount, own, current: () => ({ ...current }) };
})();
