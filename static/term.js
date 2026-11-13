"use strict";

/* Terminal panel: xterm.js tabs over one multiplexed output stream.
   Keystrokes go up in order through a small send queue; output comes down as
   {id, p, d} lines on a single long-lived request shared by every terminal. */

const Term = (() => {
  const sessions = new Map();
  let root = null, tabsEl = null, panesEl = null, current = null;
  let sendChain = Promise.resolve();
  let streaming = false;

  // A shell tab is named by what runs in it: the window title the program sets (a login shell
  // writes user@host:folder, Claude Code writes its own title behind a status glyph). A tab
  // started from a pull request keeps the name that says which one. Double-click renames any
  // tab; that name wins. Both are kept per session here, since a reload only recovers the
  // program's title if it is still in the replayed scrollback.
  const NAMES_KEY = "prdesk.termnames.v1", TITLES_KEY = "prdesk.termtitles.v1";
  const stored = (key) => { try { return JSON.parse(localStorage.getItem(key)) || {}; } catch { return {}; } };
  const names = () => stored(NAMES_KEY);
  function keep(key, id, value) {
    const all = stored(key);
    if (value) all[id] = value; else delete all[id];
    const ids = Object.keys(all);
    for (const k of ids.slice(0, Math.max(0, ids.length - 60))) delete all[k];
    try { localStorage.setItem(key, JSON.stringify(all)); } catch { /* storage blocked */ }
  }
  const saveName = (id, name) => keep(NAMES_KEY, id, name);
  function cleanTitle(raw) {
    let t = String(raw || "").replace(/[\u0000-\u001f\u007f]/g, "").trim();
    t = t.replace(/^[^\p{L}\p{N}~/.]+/u, "").trim();
    const shell = t.match(/^[^@\s]+@[^:\s]+:\s*(.+)$/);
    if (shell) {
      const path = shell[1].replace(/\/+$/, "");
      t = path === "~" ? "~" : path.split("/").pop() || path;
    }
    return t.slice(0, 80);
  }
  const baseLabel = (s) => s.custom || (isShell(s) ? s.auto || s.title : s.title);
  // Two tabs with the same name (two shells in one folder) get a number, in tab order.
  function tabLabel(s) {
    const base = baseLabel(s);
    if (s.custom) return base;
    const same = [...sessions.values()].filter((o) => !o.custom && baseLabel(o) === base);
    return same.length > 1 ? `${base} ${same.indexOf(s) + 1}` : base;
  }
  const tabEl = (id) => tabsEl?.querySelector(`.term-tab[data-term-id="${CSS.escape(id)}"]`);

  // Tabs keep the order you dragged them into, across reloads.
  const ORDER_KEY = "prdesk.termorder.v1";
  // WebGL draws the terminal on the GPU: smoother scrolling and fast output. Set this key to
  // "dom" (localStorage, then reload) to use the plain renderer if a graphics driver misbehaves.
  const RENDERER_KEY = "prdesk.term.renderer";
  const wantWebgl = () => { try { return localStorage.getItem(RENDERER_KEY) !== "dom"; } catch { return true; } };
  const DROP_MAX = 50 * 1024 * 1024;
  // A tab is "working" while output keeps coming that is not the echo of your own typing:
  // Claude Code redraws its spinner as it works and goes quiet when it waits for you.
  const ECHO_MS = 350, BUSY_MS = 1500;
  let dragging = null, swallowClick = false;

  function cssColor(value) {
    const c = document.createElement("canvas").getContext("2d");
    c.fillStyle = "#000";
    c.fillStyle = value.trim();
    return c.fillStyle;
  }
  function theme() {
    const v = (name) => cssColor(getComputedStyle(document.documentElement).getPropertyValue(name) || "#888");
    return {
      background: v("--color-paper-3"), foreground: v("--color-ink"), cursor: v("--color-accent"),
      cursorAccent: v("--color-paper-3"), selectionBackground: v("--color-cool-soft"),
    };
  }

  function mount(el) {
    root = el;
    root.classList.add("term-root");
    root.innerHTML = `<div class="term-bar"><div class="term-tabs" role="tablist" aria-label="Terminals"></div>
      <span class="term-mascot">${Sprites.svg("bot", { scale: 2, idle: "blink", follow: true })}</span>
      <button class="btn btn--quiet" type="button" data-term-new>New shell</button></div>
      <div class="term-split"><div class="term-panes"><div class="empty term-empty">${Sprites.svg("buddy", { scale: 3 })}<p>No terminal yet. The Claude button on a pull request opens one here, or start a shell.</p></div></div>
      <aside class="games-dock" aria-label="Arcade" hidden><div class="dock-handle" role="separator" aria-orientation="vertical" aria-label="Resize the arcade"></div>
        <header class="dock-head"><span>Arcade</span><button class="slot-tool" type="button" data-dock-close aria-label="Close the arcade" title="Close the arcade">×</button></header>
        <div class="games-slot"></div></aside></div>`;
    tabsEl = root.querySelector(".term-tabs");
    panesEl = root.querySelector(".term-panes");
    root.addEventListener("click", (e) => {
      if (swallowClick) { swallowClick = false; return; } // the end of a tab drag, not a click
      if (e.target.closest("[data-term-new]")) return newShell();
      const close = e.target.closest("[data-term-close]");
      if (close) { e.stopPropagation(); return closeSession(close.dataset.termClose, close); }
      const tab = e.target.closest("[data-term-tab]");
      if (tab) select(tab.dataset.termTab);
    });
    root.addEventListener("dblclick", (e) => { const name = e.target.closest(".term-tab-name"); if (name) rename(name.dataset.termTab); });
    root.addEventListener("keydown", (e) => {
      const name = e.target.closest?.(".term-tab-name");
      if (!name) return;
      if (e.key === "F2") { e.preventDefault(); rename(name.dataset.termTab); }
      if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); moveTab(name.dataset.termTab, e.key === "ArrowLeft" ? -1 : 1); }
    });
    tabsEl.addEventListener("pointerdown", startDrag);
    // Files dropped anywhere else must not open in the window; on a terminal they paste paths.
    document.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
    document.addEventListener("drop", (e) => { if (hasFiles(e) && !panesEl.contains(e.target)) e.preventDefault(); });
    panesEl.addEventListener("dragover", (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = "copy"; panesEl.classList.add("is-drop"); });
    panesEl.addEventListener("dragleave", (e) => { if (!panesEl.contains(e.relatedTarget)) panesEl.classList.remove("is-drop"); });
    panesEl.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      panesEl.classList.remove("is-drop");
      dropFiles(e.dataTransfer);
    });
    new ResizeObserver(() => fitCurrent()).observe(panesEl);
    if (!streaming) stream();
  }

  function attach(id, title) {
    if (sessions.has(id)) { select(id); return sessions.get(id); }
    const pane = document.createElement("div");
    pane.className = "term-pane";
    pane.hidden = true;
    panesEl.append(pane);
    const term = new Terminal({
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--font-term").trim() || "monospace",
      fontSize: 13, lineHeight: 1.12, cursorBlink: true, scrollback: 8000, theme: theme(),
      // The wheel glides instead of jumping; output that arrives still scrolls at once.
      smoothScrollDuration: 90, fastScrollSensitivity: 5,
      allowProposedApi: true, // the Unicode 11 width tables go through the proposed unicode API
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    const s = { id, title, term, fit, pane, alive: true, pos: 0, size: "", auto: stored(TITLES_KEY)[id] || "", custom: names()[id] || "", stage: "" };
    term.onData((data) => { s.lastKey = Date.now(); send(id, data); });
    term.onTitleChange((raw) => {
      const auto = cleanTitle(raw);
      if (!auto || auto === s.auto) return;
      s.auto = auto;
      keep(TITLES_KEY, id, auto);
      paintTabs();
    });
    term.onResize(({ cols, rows }) => {
      if (cols < 20 || rows < 4) return; // a collapsed panel: leave the program its real size
      const key = `${cols}x${rows}`;
      if (s.alive && key !== s.size) { s.size = key; api("term-resize", { method: "POST", body: { id, cols, rows } }).catch(() => {}); }
    });
    sessions.set(id, s);
    orderSessions();
    term.open(pane);
    s.renderer = loadRenderer(s);
    if (typeof SerializeAddon !== "undefined") {
      try { s.serializer = new SerializeAddon.SerializeAddon(); term.loadAddon(s.serializer); } catch { /* no snapshots then */ }
    }
    s.pending = 0;
    s.parsed = 0;
    if (typeof Unicode11Addon !== "undefined") {
      try { term.loadAddon(new Unicode11Addon.Unicode11Addon()); term.unicode.activeVersion = "11"; } catch { /* xterm's own widths stay */ }
    }
    paintTabs();
    select(id);
    return s;
  }

  function setBusy(s, on) {
    if (on) s.busyUntil = Date.now() + BUSY_MS;
    if (s.busy === on) return;
    s.busy = on;
    if (!on && s.id !== current) s.done = true; // finished out of sight: a dot until you look
    const el = tabEl(s.id);
    el?.classList.toggle("is-busy", on);
    el?.classList.toggle("is-done", !!s.done);
  }
  setInterval(() => {
    const now = Date.now();
    for (const s of sessions.values()) if (s.busy && now > s.busyUntil) setBusy(s, false);
  }, 400);

  function loadRenderer(s) {
    if (!wantWebgl() || typeof WebglAddon === "undefined") return "dom";
    try {
      const gl = new WebglAddon.WebglAddon();
      // A lost GPU context (driver reset, too many contexts) falls back to the DOM renderer.
      gl.onContextLoss(() => { gl.dispose(); s.renderer = "dom"; });
      s.term.loadAddon(gl);
      return "webgl";
    } catch { return "dom"; }
  }

  /* ------------------------------------------------------------ tab order */

  function savedOrder() { try { return JSON.parse(localStorage.getItem(ORDER_KEY)) || []; } catch { return []; } }
  function orderSessions() {
    const saved = savedOrder();
    const rank = (id) => { const i = saved.indexOf(id); return i < 0 ? Infinity : i; };
    const entries = [...sessions.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
    sessions.clear();
    for (const [key, value] of entries) sessions.set(key, value);
  }
  function setOrder(ids) {
    const entries = ids.filter((id) => sessions.has(id)).map((id) => [id, sessions.get(id)]);
    sessions.clear();
    for (const [key, value] of entries) sessions.set(key, value);
    try { localStorage.setItem(ORDER_KEY, JSON.stringify(ids)); } catch { /* storage blocked */ }
    paintTabs();
  }
  function moveTab(id, step) {
    const ids = [...sessions.keys()];
    const from = ids.indexOf(id), to = Math.max(0, Math.min(ids.length - 1, from + step));
    if (from < 0 || from === to) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    setOrder(ids);
    tabEl(id)?.querySelector(".term-tab-name")?.focus();
  }
  // Drag a tab sideways to move it. A press that barely moves stays a click.
  function startDrag(e) {
    const name = e.target.closest(".term-tab-name");
    const el = name?.closest(".term-tab");
    if (!el || e.button !== 0 || el.querySelector(".term-tab-input") || sessions.size < 2) return;
    const x0 = e.clientX;
    let target = -1;
    const others = () => [...tabsEl.querySelectorAll(".term-tab")].filter((t) => t !== el);
    const mark = () => others().forEach((t, i) => {
      t.classList.toggle("is-drop-before", i === target);
      t.classList.toggle("is-drop-after", target === others().length && i === others().length - 1);
    });
    const move = (ev) => {
      const dx = ev.clientX - x0;
      if (!dragging && Math.abs(dx) < 6) return;
      if (!dragging) { dragging = el.dataset.termId; el.classList.add("is-dragging"); }
      el.style.transform = `translateX(${dx}px)`;
      const r = el.getBoundingClientRect(), mid = r.left + r.width / 2;
      target = others().filter((t) => { const b = t.getBoundingClientRect(); return b.left + b.width / 2 < mid; }).length;
      mark();
    };
    const up = () => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", up);
      if (!dragging) return;
      swallowClick = true;
      setTimeout(() => { swallowClick = false; }, 0);
      const id = dragging;
      dragging = null;
      el.classList.remove("is-dragging");
      el.style.transform = "";
      target = Math.max(0, target);
      const ids = others().map((t) => t.dataset.termId);
      ids.splice(target, 0, id);
      target = -1;
      mark();
      setOrder(ids);
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
    addEventListener("pointercancel", up);
  }

  /* ------------------------------------------------------------ file drops */

  // Dropped files become their paths at the prompt, quoted and never run. A file manager can
  // hand over real paths (text/uri-list); when it does not, the file is copied into PixelDeck's
  // drop folder through the server and that copy's path is pasted instead.
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  const platform = () => (typeof state !== "undefined" && state.platform) || "";
  function fromUriList(text) {
    const out = [];
    for (const line of String(text || "").split(/\r?\n/)) {
      const item = line.trim();
      if (!item || item.startsWith("#")) continue;
      try {
        const url = new URL(item);
        if (url.protocol !== "file:") continue;
        let path = decodeURIComponent(url.pathname);
        if (/^\/[A-Za-z]:\//.test(path)) path = path.slice(1); // file:///C:/x on Windows
        if (!/[\u0000-\u001f\u007f]/.test(path)) out.push(path);
      } catch { /* not a URL */ }
    }
    return out;
  }
  function quotePath(path) {
    if (platform() === "win32") return `"${path}"`;
    return /^[A-Za-z0-9_./~+@%=:,-]+$/.test(path) ? path : `'${path.replace(/'/g, "'\\''")}'`;
  }
  async function upload(file) {
    if (file.size > DROP_MAX) throw new Error(`It is over ${DROP_MAX / 1048576} MB.`);
    const res = await fetch("/api/term-drop", {
      method: "POST",
      headers: { "X-Desk-Token": TOKEN, "X-File-Name": encodeURIComponent(file.name), "Content-Type": "application/octet-stream" },
      body: file,
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.path) return data.path;
    if (/JSON body/.test(data.error || "")) throw new Error("This needs the newer PixelDeck server: restart it once.");
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  // The native window hands over real paths (see app_window.py: WebKitGTK hides dropped
  // files from the page). They go to the terminal you are on, wherever they were dropped.
  function dropPaths(paths) {
    const s = sessions.get(current);
    const clean = (paths || []).filter((p) => typeof p === "string" && p && !/[\u0000-\u001f\u007f]/.test(p)).slice(0, 10);
    if (!clean.length) return false;
    if (!s || !s.alive) { toast("Open a terminal first.", "Then drop the files on it."); return false; }
    if (typeof Layout !== "undefined") Layout.activate("terminal");
    s.term.paste(`${clean.map(quotePath).join(" ")} `);
    s.term.focus();
    return true;
  }
  window.PixelDeckDrop = (paths) => dropPaths(paths);

  async function dropFiles(dt) {
    // Both lists can only be read during the drop event itself, so before any await.
    const files = [...(dt.files || [])].slice(0, 10);
    let paths = fromUriList(dt.getData("text/uri-list"));
    const s = sessions.get(current);
    if (!s || !s.alive) { toast("Open a terminal first.", "Then drop the files on it."); return; }
    if (!paths.length || paths.length < files.length) {
      paths = [];
      for (const file of files) {
        try { paths.push(await upload(file)); } catch (err) { toast(`Could not drop ${file.name}.`, err.message, true); }
      }
    }
    if (paths.length) s.term.paste(`${paths.map(quotePath).join(" ")} `);
    s.term.focus();
  }

  function send(id, data) {
    sendChain = sendChain.then(() => api("term-input", { method: "POST", body: { id, data } })).catch(() => {});
  }

  function select(id) {
    const s = sessions.get(id);
    if (!s) return;
    current = id;
    s.done = false;
    for (const other of sessions.values()) other.pane.hidden = other.id !== id;
    const empty = panesEl.querySelector(".term-empty");
    if (empty) empty.hidden = true;
    paintTabs();
    requestAnimationFrame(() => { fitCurrent(); s.term.focus(); });
  }

  function fitCurrent() {
    const s = sessions.get(current);
    // Not while its history is being redrawn: it must land at the size it was written for.
    if (!s || s.replaying || !s.pane.isConnected || s.pane.hidden || !s.pane.clientWidth) return;
    try { s.fit.fit(); } catch { /* not laid out yet */ }
  }

  const isShell = (s) => /^shell\b/i.test(s.title || "");
  // Tabs update in place, so a click, a rename or the builder bot never loses its element.
  function paintTabs() {
    if (!tabsEl) return;
    for (const s of sessions.values()) {
      let el = tabEl(s.id);
      if (!el) {
        el = document.createElement("span");
        el.className = "term-tab";
        el.dataset.termId = s.id;
        el.setAttribute("role", "tab");
        el.innerHTML = `<button type="button" class="term-tab-name" data-term-tab="${esc(s.id)}"${isShell(s) ? "" : " data-spr-hover"}>${isShell(s) ? "" : Sprites.svg("buddy", { scale: 1 })}<span class="term-tab-label"></span></button>
          <button type="button" class="term-tab-close" data-term-close="${esc(s.id)}">×</button>`;
        tabsEl.append(el);
      }
      const label = `${tabLabel(s)}${s.alive ? "" : " (ended)"}`;
      el.classList.toggle("is-current", s.id === current);
      el.classList.toggle("is-ended", !s.alive);
      el.classList.toggle("is-scaffold", s.stage !== "");
      el.classList.toggle("is-building", s.stage === "building");
      el.classList.toggle("is-busy", !!s.busy);
      el.classList.toggle("is-done", !!s.done);
      el.setAttribute("aria-selected", String(s.id === current));
      const text = el.querySelector(".term-tab-label");
      if (text.textContent !== label) text.textContent = label;
      const full = s.auto && s.auto !== s.title ? `${s.title}: ${s.auto}` : s.title;
      el.querySelector(".term-tab-name").dataset.tip = `${s.custom ? `${s.custom} (${full})` : full}. Double-click to rename.`;
      el.querySelector(".term-tab-close").setAttribute("aria-label", `Close ${tabLabel(s)}`);
    }
    for (const el of tabsEl.querySelectorAll(".term-tab")) if (!sessions.has(el.dataset.termId)) el.remove();
    if (!dragging) {
      let prev = null;
      for (const s of sessions.values()) {
        const el = tabEl(s.id);
        const want = prev ? prev.nextElementSibling : tabsEl.firstElementChild;
        if (el && el !== want) tabsEl.insertBefore(el, want);
        prev = el || prev;
      }
    }
    const empty = panesEl.querySelector(".term-empty");
    if (empty) empty.hidden = sessions.size > 0;
    if (typeof Layout !== "undefined") Layout.setBadge("terminal", [...sessions.values()].filter((s) => s.alive).length || "");
  }

  function rename(id) {
    const s = sessions.get(id), el = tabEl(id);
    const btn = el?.querySelector(".term-tab-name");
    if (!s || !btn || el.querySelector(".term-tab-input")) return;
    const input = document.createElement("input");
    input.className = "term-tab-input";
    input.value = tabLabel(s);
    input.maxLength = 60;
    input.size = Math.max(8, input.value.length + 1);
    input.setAttribute("aria-label", "Tab name, Enter to keep, Escape to cancel, empty to go back to the session's own");
    btn.hidden = true;
    btn.after(input);
    let done = false;
    const finish = (keep) => {
      if (done) return;
      done = true;
      if (keep) {
        const v = input.value.trim();
        s.custom = v && v !== (s.auto || s.title) ? v : "";
        saveName(id, s.custom);
      }
      input.remove();
      btn.hidden = false;
      paintTabs();
      btn.focus();
    };
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); finish(true); } else if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("dblclick", (e) => e.stopPropagation());
    // The click that selected the tab focuses its terminal a frame later; take focus after that.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      input.focus();
      input.select();
      input.addEventListener("blur", () => finish(true));
    }));
  }

  /* New shell: the bot beams out of its seat while the shell starts, beams in beside the
     new tab, hammers it together, and beams home. The shell is usable the whole time. */
  async function build(actor, id) {
    const s = id && sessions.get(id);
    try {
      const el = s && tabEl(id);
      el?.scrollIntoView({ block: "nearest", inline: "nearest" });
      if (s && el?.getBoundingClientRect().width) {
        s.stage = "scaffold";
        paintTabs();
        await Sprites.construct(actor, () => { s.stage = "building"; paintTabs(); }, () => tabEl(id)?.getBoundingClientRect());
        Sprites.spawn(tabEl(id));
      }
    } finally {
      // Whatever happens mid-show, the tab ends whole and the bot ends on its seat.
      if (s) { s.stage = ""; paintTabs(); }
      await Sprites.goHome(actor);
    }
  }
  async function newShell() {
    const actor = Sprites.builder(root.querySelector(".term-mascot"));
    let id = null;
    try {
      const info = await api("terms", { method: "POST", body: { kind: "shell" } });
      id = info.id;
      const s = attach(info.id, info.title);
      if (actor) { s.stage = "scaffold"; paintTabs(); }
      Layout.activate("terminal");
    } catch (err) {
      toast("Could not start a shell.", err.message, true);
    }
    if (actor) await build(actor, id);
    else if (id) Sprites.spawn(tabEl(id));
  }
  /** The help dialog's replay: the bot rebuilds the current tab. */
  /** A Claude launch: the green slime that answered the button builds the new tab. */
  function buildWith(actor, id) {
    if (actor) build(actor, id);
  }
  function showBuild() {
    const actor = Sprites.builder(root?.querySelector(".term-mascot"));
    if (actor) build(actor, current);
  }

  // Closing a live terminal stops what runs in it, so the first click only arms the x
  // (the tab turns red and asks); a second click within 3 s closes it.
  async function closeSession(id, button) {
    const s = sessions.get(id);
    if (!s) return;
    const el = tabEl(id);
    if (s.alive && button && el && !el.classList.contains("is-armed")) {
      el.classList.add("is-armed");
      button.textContent = "CLOSE?";
      button.setAttribute("aria-label", `Click again to close ${tabLabel(s)} and stop what runs in it`);
      clearTimeout(el._disarm);
      el._disarm = setTimeout(() => { el.classList.remove("is-armed"); button.textContent = "×"; button.setAttribute("aria-label", `Close ${tabLabel(s)}`); }, 3000);
      return;
    }
    if (el) clearTimeout(el._disarm);
    await api("term-close", { method: "POST", body: { id } }).catch(() => {});
    drop(id);
  }

  function drop(id) {
    const s = sessions.get(id);
    if (!s) return;
    s.term.dispose();
    s.pane.remove();
    sessions.delete(id);
    if (current === id) current = sessions.size ? [...sessions.keys()].pop() : null;
    if (current) select(current); else paintTabs();
  }

  const decode = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

  async function stream() {
    streaming = true;
    for (;;) {
      try {
        const cursors = [...sessions.values()].map((s) => `${s.id}:${s.pos}`).join(",");
        const res = await fetch(`/api/term-stream?cursors=${encodeURIComponent(cursors)}`, { headers: { "X-Desk-Token": TOKEN } });
        if (res.status === 401) { location.reload(); return; }
        const reader = res.body.getReader();
        const text = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += text.decode(value, { stream: true });
          let nl;
          while ((nl = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, nl);
            buf = buf.slice(nl + 1);
            if (!line || line === "{}") continue;
            handle(JSON.parse(line));
          }
        }
      } catch { /* server restarting or network blip */ }
      await new Promise((r) => setTimeout(r, 1000));
    }
  }

  function handle(msg) {
    if (msg.gone) { drop(msg.id); return; }
    let s = sessions.get(msg.id);
    if (!s) {
      if ("exit" in msg && !msg.d) return; // an ended session nobody had open
      s = attach(msg.id, msg.t || "Terminal");
    }
    if ("snap" in msg) {
      // The screen as a page last drew it: start from it at its size, then the output after it.
      s.replaying = true;
      s.term.reset();
      if (msg.cols >= 20 && msg.rows >= 4) s.term.resize(msg.cols, msg.rows);
      write(s, msg.snap, msg.sp);
      s.pos = msg.sp; // a reconnect asks for what follows the snapshot, not the whole history
      return;
    }
    if (!s.ready && msg.cols >= 20 && msg.rows >= 4 && !s.replaying) {
      // No snapshot: replay the history at the size the program has now, not at 80 columns.
      s.replaying = true;
      s.term.resize(msg.cols, msg.rows);
    }
    if (msg.d || !s.ready) {
      write(s, msg.d ? decode(msg.d) : "", typeof msg.p === "number" ? msg.p : s.parsed, () => {
        if (!s.replaying) return;
        s.replaying = false;
        if (s.id === current) fitCurrent();
      });
      // The first chunk after attaching is the replayed scrollback, not new work.
      if (s.ready && msg.d && Date.now() - (s.lastKey || 0) > ECHO_MS) setBusy(s, true);
      s.ready = true;
    }
    if (typeof msg.p === "number") s.pos = msg.p;
    if ("exit" in msg && s.alive) {
      s.alive = false;
      s.term.write(`\r\n\x1b[2m[ended with code ${msg.exit}]\x1b[0m\r\n`);
      paintTabs();
    }
  }

  // Every write remembers the stream offset it ends at, once xterm has drawn it.
  function write(s, data, upTo, done) {
    s.pending++;
    s.term.write(data, () => {
      s.pending--;
      if (upTo > s.parsed) s.parsed = upTo;
      done?.();
    });
  }

  /* Snapshots: what each terminal shows now, with the offset it was drawn up to, kept by the
     server for the next page. Taken only while nothing is half-drawn, so the picture and the
     offset match exactly; every 15 s, and when the page is hidden or closed. */
  const SNAPSHOT_KEEP = 2000; // scrollback lines in a snapshot
  function saveSnapshots(leaving = false) {
    if (typeof state === "undefined" || !(state.features || []).includes("snapshots")) return;
    for (const s of sessions.values()) {
      if (!s.serializer || s.pending || s.replaying || s.parsed === s.savedAt || !s.ready) continue;
      let text;
      try { text = s.serializer.serialize({ scrollback: SNAPSHOT_KEEP }); } catch { continue; }
      if (text.length > 3_500_000) continue;
      s.savedAt = s.parsed;
      const q = `id=${encodeURIComponent(s.id)}&pos=${s.parsed}&cols=${s.term.cols}&rows=${s.term.rows}`;
      fetch(`/api/term-snapshot?${q}`, {
        method: "POST", body: text, keepalive: leaving && text.length < 60000,
        headers: { "X-Desk-Token": TOKEN, "Content-Type": "text/plain; charset=utf-8" },
      }).catch(() => { s.savedAt = -1; });
    }
  }
  setInterval(saveSnapshots, 15000);
  document.addEventListener("visibilitychange", () => { if (document.hidden) saveSnapshots(); });
  addEventListener("pagehide", () => saveSnapshots(true));

  return { mount, attach, select, newShell, showBuild, buildWith, fit: fitCurrent, saveSnapshots, count: () => sessions.size,
    renderers: () => [...sessions.values()].map((s) => s.renderer), order: () => [...sessions.keys()], dropPaths,
    // The last lines of the current terminal as text: with WebGL the screen is not in the DOM.
    lines: (n = 5) => {
      const buf = sessions.get(current)?.term.buffer.active;
      if (!buf) return [];
      const out = [];
      for (let y = Math.max(0, buf.length - 200); y < buf.length; y++) out.push(buf.getLine(y)?.translateToString(true) || "");
      return out.filter((line) => line.trim()).slice(-n);
    } };
})();
