"use strict";

/* The hub: the status bar (API calls this hour, last and next sync, connection, the bell,
   settings, the guide), the settings dialog (what PixelDeck is connected to, what notifies
   you, how it looks) and the personal preferences it keeps per browser. It answers from
   /api/status and /api/settings, which never call Bitbucket; an older server without them
   leaves the bar as it was. */

const Prefs = (() => {
  const KEY = "prdesk.prefs.v1";
  const DEFAULTS = { accent: "amber", motion: "auto", numbers: "arcade", mascots: true, bubbles: true, games: false };
  let prefs = (() => { try { return { ...DEFAULTS, ...(JSON.parse(localStorage.getItem(KEY)) || {}) }; } catch { return { ...DEFAULTS }; } })();
  const listeners = new Set();
  function apply() {
    const root = document.documentElement;
    root.dataset.accent = prefs.accent;
    root.dataset.motion = prefs.motion === "calm" ? "calm" : "";
    root.dataset.numbers = prefs.numbers;
    root.classList.toggle("no-mascots", !prefs.mascots);
  }
  function set(patch) {
    prefs = { ...prefs, ...patch };
    try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* storage blocked */ }
    apply();
    for (const fn of listeners) fn(prefs, patch);
  }
  apply();
  return { get: (k) => (k ? prefs[k] : { ...prefs }), set, onChange: (fn) => listeners.add(fn) };
})();

/* Pixel icons: each row is a string, # is a lit pixel. */
const PixelIcon = (() => {
  const ROWS = {
    bell: [".....#.....", "....###....", "...#####...", "..#######..", "..#######..", "..#######..", "..#######..", ".#########.", "###########", "...........", "....###...."],
    gear: ["....###....", ".##.###.##.", ".#########.", "..##...##..", "###.....###", "###.....###", "###.....###", "..##...##..", ".#########.", ".##.###.##.", "....###...."],
    help: ["..#####..", ".##...##.", ".##...##.", ".....##..", "....##...", "...##....", "...##....", ".........", "...##....", "...##....", "........."],
    plug: ["..#...#..", "..#...#..", "#########", "#########", ".#######.", ".#######.", "..#####..", "....#....", "....#....", "....#...."],
  };
  function svg(name, px = 2) {
    const rows = ROWS[name];
    const w = Math.max(...rows.map((r) => r.length)), h = rows.length;
    let rects = "";
    rows.forEach((row, y) => [...row].forEach((c, x) => { if (c === "#") rects += `<rect x="${x}" y="${y}" width="1" height="1"/>`; }));
    return `<svg class="pix-icon" viewBox="0 0 ${w} ${h}" width="${w * px}" height="${h * px}" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`;
  }
  return { svg };
})();

const StatusBar = (() => {
  const POLL_MS = 10000;
  let status = null, skew = 0, timer = 0, tick = 0, offline = false;
  const $id = (id) => document.getElementById(id);
  const two = (n) => String(n).padStart(2, "0");
  const has = (f) => (state.features || []).includes(f);

  function paintApi() {
    const { used, limit } = state.budget;
    const left = Math.max(0, Math.ceil((1 - used / limit) * 10));
    const energy = $id("energy");
    if (energy) {
      energy.innerHTML = Array.from({ length: 10 }, (_, i) => `<i class="${i < left ? "on" : ""}"></i>`).join("");
      energy.classList.toggle("is-low", left <= 3);
    }
    const num = $id("st-api-num");
    if (num) num.textContent = `${used}/${limit}`;
    const api = $id("st-api");
    if (api) {
      api.classList.toggle("is-low", left <= 3);
      api.dataset.tip = `${used} of ${limit} Bitbucket API calls used in the last hour, ${limit - used} left. The background refresh pauses at ${Math.round(limit * 0.8)}.`;
    }
  }
  function connection() {
    if (offline) return ["bad", "PixelDeck's server does not answer. Is it still running?"];
    const auth = status?.auth || state.auth || {};
    if (auth.ok === false) return ["bad", `Bitbucket refused the token: ${auth.error || "unknown error"}`];
    if (status?.schedule?.paused) return ["warn", "Background refresh paused: 80% of the hourly API calls are used. It resumes as the hour rolls over."];
    if (auth.ok === true || state.me) return ["ok", `Connected to Bitbucket${state.me ? ` as ${state.me.name}` : ""}.`];
    return ["warn", "Connecting to Bitbucket…"];
  }
  function paintSync() {
    const [tone, tip] = connection();
    const dot = $id("st-dot");
    if (dot) dot.dataset.tone = tone;
    const sync = $id("st-sync");
    const sched = status?.schedule;
    const synced = sched?.open_at || sched?.synced_at || currentData()?.synced_at;
    const text = $id("sync-text");
    if (text) text.textContent = synced ? `synced ${clock(synced)}` : "not synced";
    const nextEl = $id("st-next");
    let next = "";
    if (sched?.next_at && !sched.paused && !offline) {
      const secs = Math.round(sched.next_at - (Date.now() / 1000 - skew));
      next = secs <= 0 ? "next: now" : `next ${Math.floor(secs / 60)}:${two(secs % 60)}`;
    }
    if (nextEl) nextEl.textContent = next;
    if (sync) sync.dataset.tip = `${tip}${synced ? ` Your pull requests were read at ${clock(synced)}.` : ""}${sched?.team_at ? ` Teammates' last read: ${clock(sched.team_at)}.` : ""} PixelDeck reads your open pull requests every minute, the rest every 5 to 15 minutes.`;
  }
  function paintBell() {
    const n = typeof Notify !== "undefined" ? Notify.unread() : 0;
    const badge = $id("bell-badge");
    if (badge) { badge.hidden = !n; badge.textContent = n > 99 ? "99+" : String(n); }
    const bell = $id("bell");
    if (bell) {
      bell.classList.toggle("is-snoozed", typeof Notify !== "undefined" && Notify.snoozed());
      bell.setAttribute("aria-label", n ? `Notifications, ${n} new` : "Notifications");
    }
  }
  function paint() { paintApi(); paintSync(); paintBell(); }
  async function poll() {
    clearTimeout(timer);
    timer = setTimeout(poll, POLL_MS);
    if (!has("status")) { paint(); return; }
    try {
      status = await api("status");
      skew = Date.now() / 1000 - status.now;
      state.budget = status.budget || state.budget;
      if (status.auth?.ok !== null && status.auth) state.auth = status.auth;
      offline = false;
    } catch { offline = true; }
    paint();
  }
  function start() {
    $id("bell").innerHTML = `${PixelIcon.svg("bell")}<span class="st-badge" id="bell-badge" hidden></span>`;
    $id("settings-btn").innerHTML = PixelIcon.svg("gear");
    $id("guide-btn").innerHTML = PixelIcon.svg("help");
    $id("bell").hidden = !has("events");
    $id("settings-btn").addEventListener("click", () => Settings.open());
    $id("guide-btn").addEventListener("click", () => { if (typeof Guide !== "undefined") Guide.start(); });
    if (typeof Notify !== "undefined") Notify.onChange(paintBell);
    clearInterval(tick);
    tick = setInterval(paintSync, 1000);
    poll();
  }
  return { start, paint, poll };
})();

const Settings = (() => {
  const TABS = [["connections", "Connections"], ["notifications", "Notifications"], ["look", "Personalise"], ["about", "About"]];
  let tab = "connections", report = null, loading = false;
  const dialog = () => document.getElementById("settings-dialog");
  const pill = (ok, yes, no) => `<span class="set-pill is-${ok === true ? "ok" : ok === false ? "bad" : "warn"}">${esc(ok === true ? yes : ok === false ? no : "Checking")}</span>`;
  const row = (label, value, opts = {}) => value == null || value === "" ? "" :
    `<div class="set-row"><dt>${esc(label)}</dt><dd>${opts.code ? `<code>${esc(value)}</code>` : esc(value)}${opts.copy ? ` <button class="link" type="button" data-copy-value="${esc(value)}">Copy</button>` : ""}</dd></div>`;

  function connectionsHtml() {
    if (!report) return `<p class="set-hint">${loading ? "Asking the server…" : "This needs a newer PixelDeck server: restart it once."}</p>`;
    const bb = report.bitbucket, cl = report.claude, git = report.git, app = report.app;
    const bbOk = bb.auth?.ok === true ? true : bb.auth?.ok === false || !bb.token_set ? false : null;
    const used = bb.budget?.used || 0, limit = bb.budget?.limit || 1000;
    const jira = report.jira;
    return `<section class="set-card"><header><h3>Bitbucket</h3>${pill(bbOk, bb.me ? `Connected as ${bb.me.name}` : "Connected", bb.token_set ? "Token refused" : "No token yet")}</header>
        ${bb.auth?.error ? `<p class="set-error">${esc(bb.auth.error)}</p>` : ""}
        <dl class="set-rows">${row("Workspace", bb.workspace)}${row("Account", bb.email)}${row("Project", bb.project ? bb.project.name : "Every project")}${row("Token", bb.token_set ? "set (never shown)" : "missing")}${row("Settings file", bb.env_file, { code: true, copy: true })}</dl>
        <div class="set-meter" data-tip="${used} of ${limit} calls used in the last hour"><span style="--w:${Math.min(1, used / limit).toFixed(3)}"></span></div>
        <p class="set-hint">${used} of ${limit} API calls used in the last hour. Change the token in the settings file; PixelDeck picks it up within a minute.</p>
        <p><button class="btn" type="button" data-settings-test>Test the connection</button></p></section>
      ${jira ? `<section class="set-card"><header><h3>Jira</h3>${pill(jira.ok, jira.me ? `Connected as ${jira.me}` : "Connected", jira.site ? (jira.token_set ? "Token refused" : "No Jira token") : "Not set up")}</header>
        ${jira.error ? `<p class="set-error">${esc(jira.error)}</p>` : ""}
        <dl class="set-rows">${row("Site", jira.site)}${row("Token", jira.token_set ? "set (never shown)" : "missing")}</dl>
        <p class="set-hint">Used to show and move a ticket's status when you merge. Set <code>JIRA_SITE</code> and <code>JIRA_TOKEN</code>, an API token with Jira access; the Bitbucket token is never sent to Jira.</p></section>` : ""}
      <section class="set-card"><header><h3>Claude Code</h3>${pill(cl.ok, "Ready", cl.path ? "Not answering" : "Not found")}</header>
        ${cl.error ? `<p class="set-error">${esc(cl.error)}</p>` : ""}
        <dl class="set-rows">${row("Program", cl.path, { code: true })}${row("Version", cl.version)}</dl>
        <p class="set-hint">The Claude buttons on a pull request start it in a terminal here. Point <code>${esc(cl.setting)}</code> at it when it is not on your PATH.</p></section>
      <section class="set-card"><header><h3>Git and terminals</h3>${pill(git.ok && !app.terminal, "Ready", git.ok ? "Terminals unavailable" : "git missing")}</header>
        <dl class="set-rows">${row("git", git.version)}${row("Terminals", app.terminal || "ready")}${row("Your clones", app.workspace_root, { code: true })}${row("Brief templates", app.briefs_dir, { code: true })}</dl></section>`;
  }
  function lookHtml() {
    const p = Prefs.get();
    const choice = (key, value, label, extra = "") => `<button class="date-chip" type="button" data-pref="${key}" data-value="${value}" aria-pressed="${p[key] === value}"${extra}>${label}</button>`;
    const swatches = [["amber", "Amber"], ["cool", "Arcade blue"], ["slime", "Slime green"], ["pink", "Neon pink"]]
      .map(([v, label]) => `<button class="swatch-btn" type="button" data-pref="accent" data-value="${v}" aria-pressed="${p.accent === v}" data-swatch="${v}"><span aria-hidden="true"></span>${label}</button>`).join("");
    const toggle = (key, label, hint) => `<label class="switch"><input type="checkbox" data-pref-toggle="${key}"${p[key] ? " checked" : ""}><span class="switch-knob" aria-hidden="true"></span><span>${esc(label)}${hint ? `<small>${esc(hint)}</small>` : ""}</span></label>`;
    const fontOk = (() => { try { return document.fonts.check('16px "ArcadeClassic"'); } catch { return false; } })();
    return `<div class="set-block"><h3>Accent</h3><div class="swatches">${swatches}</div></div>
      <div class="set-block"><h3>Numbers</h3><div class="date-bar">${choice("numbers", "arcade", "Arcade")}${choice("numbers", "press", "Press Start")}${choice("numbers", "plain", "Plain")}</div>
        <p class="set-hint">Arcade uses the ArcadeClassic font when it is installed on this computer${fontOk ? " (it is)" : " (it is not, so Press Start stands in)"}.</p></div>
      <div class="set-block"><h3>Motion</h3><div class="date-bar">${choice("motion", "auto", "Follow the system")}${choice("motion", "calm", "Calm")}</div></div>
      <div class="set-block"><h3>Arcade</h3><div class="switch-grid">${toggle("mascots", "Mascots", "the slime and the bot")}${toggle("bubbles", "Bot ideas", "the bot suggests a game while you wait")}${toggle("games", "Games beside the terminal", "Tetris, Snake and Flappy")}</div></div>
      <div class="set-block"><h3>Guide</h3><p><button class="btn" type="button" data-guide-again>Show the guide again</button></p></div>`;
  }
  function aboutHtml() {
    const app = report?.app;
    return `<div class="set-about">${typeof Sprites !== "undefined" ? `<span class="set-mascots">${Sprites.svg("buddy", { scale: 3 })}${Sprites.svg("bot", { scale: 3 })}</span>` : ""}
      <h3>PixelDeck</h3><p>Your pull requests, your team's, and the terminals and agents that work on them, in one window.</p>
      <dl class="set-rows">${row("Server", app ? `127.0.0.1:${app.port}` : "")}${row("Python", app?.python)}${row("Installed in", app?.app_dir, { code: true })}</dl>
      <p><button class="link" type="button" data-external="https://github.com/ahmedloumx/pixeldeck">github.com/ahmedloumx/pixeldeck</button></p>
      <p><button class="btn btn--quiet" type="button" data-action="open-keys">Keyboard keys</button></p></div>`;
  }
  function paint() {
    const d = dialog();
    if (!d) return;
    const body = tab === "connections" ? connectionsHtml() : tab === "notifications" ? (typeof Notify !== "undefined" ? Notify.settingsHtml() : "") : tab === "look" ? lookHtml() : aboutHtml();
    d.innerHTML = `<div class="set-shell">
      <nav class="set-tabs" role="tablist" aria-label="Settings">${TABS.map(([id, label]) => `<button class="set-tab" type="button" role="tab" data-settings-tab="${id}" aria-selected="${tab === id}">${label}</button>`).join("")}</nav>
      <section class="set-body" role="tabpanel"><header class="set-head"><h2 id="settings-title">${esc(TABS.find(([id]) => id === tab)[1])}</h2>
        <button class="btn btn--quiet" type="button" data-settings-close aria-label="Close settings">Close</button></header>${body}</section></div>`;
  }
  async function load(test = false) {
    if (!(state.features || []).includes("settings")) { report = null; paint(); return; }
    loading = true; paint();
    try { report = await api(test ? "settings-test" : "settings", test ? { method: "POST", body: {} } : undefined); } catch (err) { toast("Could not read the settings.", err.message, true); }
    loading = false;
    if (dialog()?.open) paint();
  }
  function open(which) {
    if (which) tab = which;
    const d = dialog();
    paint();
    if (!d.open) d.showModal();
    load();
  }
  document.addEventListener("click", async (e) => {
    const t = e.target;
    if (!t.closest("#settings-dialog")) return;
    const tabBtn = t.closest("[data-settings-tab]");
    if (tabBtn) { tab = tabBtn.dataset.settingsTab; return paint(); }
    if (t.closest("[data-settings-close]")) return dialog().close();
    if (t.closest("[data-settings-test]")) {
      const b = t.closest("[data-settings-test]");
      await busy(b, () => load(true));
      return;
    }
    const copy = t.closest("[data-copy-value]");
    if (copy) { if (await copyText(copy.dataset.copyValue)) toast("Copied.", copy.dataset.copyValue); return; }
    const pref = t.closest("[data-pref]");
    if (pref) { Prefs.set({ [pref.dataset.pref]: pref.dataset.value }); return paint(); }
    if (t.closest("[data-guide-again]")) { dialog().close(); if (typeof Guide !== "undefined") Guide.start(); return; }
    if (t === dialog()) dialog().close();  // a click on the backdrop
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.matches("[data-pref-toggle]")) Prefs.set({ [t.dataset.prefToggle]: t.checked });
    if (t.closest("#settings-dialog") && (t.matches("[data-notify-kind]") || t.matches("[data-notify-scope]"))) setTimeout(paint, 0);
  });
  if (typeof Notify !== "undefined") Notify.onChange(() => { if (dialog()?.open && tab === "notifications") paint(); });
  return { open, paint };
})();
