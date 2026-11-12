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
      <span class="term-mascot">${Sprites.svg("bot", { scale: 2, idle: "blink" })}</span>
      <button class="btn btn--quiet" type="button" data-term-new>New shell</button></div>
      <div class="term-panes"><div class="empty term-empty">${Sprites.svg("buddy", { scale: 3 })}<p>No terminal yet. The Claude button on a pull request opens one here, or start a shell.</p></div></div>`;
    tabsEl = root.querySelector(".term-tabs");
    panesEl = root.querySelector(".term-panes");
    root.addEventListener("click", (e) => {
      if (e.target.closest("[data-term-new]")) return newShell();
      const close = e.target.closest("[data-term-close]");
      if (close) { e.stopPropagation(); return closeSession(close.dataset.termClose); }
      const tab = e.target.closest("[data-term-tab]");
      if (tab) select(tab.dataset.termTab);
    });
    root.addEventListener("dblclick", (e) => { const name = e.target.closest(".term-tab-name"); if (name) rename(name.dataset.termTab); });
    root.addEventListener("keydown", (e) => { const name = e.target.closest?.(".term-tab-name"); if (name && e.key === "F2") { e.preventDefault(); rename(name.dataset.termTab); } });
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
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    const s = { id, title, term, fit, pane, alive: true, pos: 0, size: "", auto: stored(TITLES_KEY)[id] || "", custom: names()[id] || "", stage: "" };
    term.onData((data) => send(id, data));
    term.onTitleChange((raw) => {
      const auto = cleanTitle(raw);
      if (!auto || auto === s.auto) return;
      s.auto = auto;
      keep(TITLES_KEY, id, auto);
      paintTabs();
    });
    term.onResize(({ cols, rows }) => {
      const key = `${cols}x${rows}`;
      if (s.alive && key !== s.size) { s.size = key; api("term-resize", { method: "POST", body: { id, cols, rows } }).catch(() => {}); }
    });
    sessions.set(id, s);
    term.open(pane);
    paintTabs();
    select(id);
    return s;
  }

  function send(id, data) {
    sendChain = sendChain.then(() => api("term-input", { method: "POST", body: { id, data } })).catch(() => {});
  }

  function select(id) {
    const s = sessions.get(id);
    if (!s) return;
    current = id;
    for (const other of sessions.values()) other.pane.hidden = other.id !== id;
    const empty = panesEl.querySelector(".term-empty");
    if (empty) empty.hidden = true;
    paintTabs();
    requestAnimationFrame(() => { fitCurrent(); s.term.focus(); });
  }

  function fitCurrent() {
    const s = sessions.get(current);
    if (!s || !s.pane.isConnected || s.pane.hidden || !s.pane.clientWidth) return;
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
      el.setAttribute("aria-selected", String(s.id === current));
      const text = el.querySelector(".term-tab-label");
      if (text.textContent !== label) text.textContent = label;
      const full = s.auto && s.auto !== s.title ? `${s.title}: ${s.auto}` : s.title;
      el.querySelector(".term-tab-name").dataset.tip = `${s.custom ? `${s.custom} (${full})` : full}. Double-click to rename.`;
      el.querySelector(".term-tab-close").setAttribute("aria-label", `Close ${tabLabel(s)}`);
    }
    for (const el of tabsEl.querySelectorAll(".term-tab")) if (!sessions.has(el.dataset.termId)) el.remove();
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
  function showBuild() {
    const actor = Sprites.builder(root?.querySelector(".term-mascot"));
    if (actor) build(actor, current);
  }

  async function closeSession(id) {
    const s = sessions.get(id);
    if (!s) return;
    if (s.alive && !confirm(`Close "${tabLabel(s)}"? The process in it will be stopped.`)) return;
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
    if (msg.d) s.term.write(decode(msg.d));
    if (typeof msg.p === "number") s.pos = msg.p;
    if ("exit" in msg && s.alive) {
      s.alive = false;
      s.term.write(`\r\n\x1b[2m[ended with code ${msg.exit}]\x1b[0m\r\n`);
      paintTabs();
    }
  }

  return { mount, attach, select, newShell, showBuild, fit: fitCurrent, count: () => sessions.size };
})();
