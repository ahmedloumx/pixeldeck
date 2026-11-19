"use strict";

/* The hub: the status bar (API calls this hour, last and next sync, connection, the bell,
   settings, the guide), the settings dialog (what PixelDeck is connected to, what notifies
   you, how it looks) and the personal preferences it keeps per browser. It answers from
   /api/status and /api/settings, which never call Bitbucket; an older server without them
   leaves the bar as it was. */

// The themes and accents PixelDeck knows; anything else falls back to the first.
const THEMES = [["night", "Night", "dark"], ["midnight", "Midnight", "dark"], ["daylight", "Daylight", "light"], ["grey", "Retro grey", "light"]];
const ACCENTS = [["amber", "Amber"], ["cool", "Arcade blue"], ["slime", "Slime green"], ["pink", "Neon pink"], ["purple", "Purple"], ["turquoise", "Turquoise"]];
const Prefs = (() => {
  const KEY = "prdesk.prefs.v1";
  const DEFAULTS = { theme: "night", accent: "amber", motion: "auto", numbers: "arcade", numSize: "small", mascots: true, bubbles: true, games: false, titlebar: "custom" };
  let prefs = (() => { try { return { ...DEFAULTS, ...(JSON.parse(localStorage.getItem(KEY)) || {}) }; } catch { return { ...DEFAULTS }; } })();
  const listeners = new Set();
  function apply() {
    const root = document.documentElement;
    const theme = THEMES.find(([id]) => id === prefs.theme) || THEMES[0];
    root.dataset.accent = ACCENTS.some(([id]) => id === prefs.accent) ? prefs.accent : ACCENTS[0][0];
    root.dataset.theme = theme[0];
    root.dataset.tone = theme[2];
    root.dataset.motion = prefs.motion === "calm" ? "calm" : "";
    root.dataset.numbers = prefs.numbers;
    root.dataset.numSize = prefs.numSize;
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
/* The blob is an AI helper, and each theme repaints it. After a theme change, the next few
   times you hover a blob it has something to say about its CSS. */
const BlobTalk = (() => {
  const COLOUR = { night: "green", midnight: "cyan", daylight: "orange", grey: "purple" };
  const JOKES = [
    (was) => `Someone changed my CSS again. I was ${was} five minutes ago.`,
    () => "My colour got washed out by the last update. Classic cascade.",
    () => "I asked for !important. Still overridden.",
    () => "Felt cute, lost to a more specific selector.",
    () => "They rotated my hue. Nobody asked the blob.",
    () => "I'm an AI blob: my colour is a prompt, and you just rewrote it.",
  ];
  let left = 0, next = 0, was = "green", bubble = null, timer = 0;
  Prefs.onChange((p, patch) => {
    if (!("theme" in patch)) return;
    left = 3;
    bubble?.remove();
    bubble = null;
  });
  // the colour it had before: remembered at each change, from the theme that was on
  let current = document.documentElement.dataset.theme;
  Prefs.onChange(() => { const now = document.documentElement.dataset.theme; if (now !== current) { was = COLOUR[current] || "green"; current = now; } });
  function hide() { clearTimeout(timer); bubble?.remove(); bubble = null; }
  function say(blob, text) {
    hide();
    const r = blob.getBoundingClientRect();
    bubble = document.createElement("div");
    bubble.className = "blob-say";
    bubble.setAttribute("role", "status");
    bubble.textContent = text;
    bubble._for = blob;
    document.body.append(bubble);
    const w = bubble.offsetWidth, h = bubble.offsetHeight;
    const above = r.top > h + 18;
    bubble.classList.toggle("is-below", !above);
    bubble.style.left = `${Math.round(Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)))}px`;
    bubble.style.top = `${Math.round(above ? r.top - h - 12 : r.bottom + 12)}px`;
    timer = setTimeout(hide, 5000);
  }
  document.addEventListener("mouseover", (e) => {
    const blob = e.target.closest?.("svg.spr-buddy");
    if (!blob || !left || bubble?._for === blob) return;
    left--;
    say(blob, JOKES[next++ % JOKES.length](was));
  });
  document.addEventListener("mouseout", (e) => {
    const blob = e.target.closest?.("svg.spr-buddy");
    if (blob && bubble?._for === blob && !blob.contains(e.relatedTarget)) setTimeout(() => { if (bubble?._for === blob) hide(); }, 1200);
  });
  return { say };
})();

const PixelIcon = (() => {
  const ROWS = {
    bell: [".....#.....", "....###....", "...#####...", "..#######..", "..#######..", "..#######..", "..#######..", ".#########.", "###########", "...........", "....###...."],
    gear: ["....###....", ".##.###.##.", ".#########.", "..##...##..", "###.....###", "###.....###", "###.....###", "..##...##..", ".#########.", ".##.###.##.", "....###...."],
    help: ["..#####..", ".##...##.", ".##...##.", ".....##..", "....##...", "...##....", "...##....", ".........", "...##....", "...##....", "........."],
    plug: ["..#...#..", "..#...#..", "#########", "#########", ".#######.", ".#######.", "..#####..", "....#....", "....#....", "....#...."],
    refresh: ["...####..", "..#....#.", ".#....###", ".#.....#.", ".#.......", ".#.....#.", "..#...#..", "...###...", "........."],
    min: ["..........", "..........", "..........", "..........", "..........", "..........", "..........", "..........", "##########", "##########"],
    max: ["##########", "##########", "#........#", "#........#", "#........#", "#........#", "#........#", "#........#", "#........#", "##########"],
    restore: ["...#######", "...#######", "...#.....#", "#######..#", "#######..#", "#.....#..#", "#.....####", "#.....#...", "#.....#...", "#######..."],
    close: ["##......##", "###....###", ".###..###.", "..######..", "...####...", "...####...", "..######..", ".###..###.", "###....###", "##......##"],
    layout: ["###########", "#...#.....#", "#...#.....#", "#...#.....#", "#...#.....#", "#...#######", "#...#.....#", "#...#.....#", "#...#.....#", "#...#.....#", "###########"],
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
      // The meter is a quick toggle, off by default; it shows itself when the calls run low.
      if (typeof ControlCenter !== "undefined") api.hidden = !(ControlCenter.config().api || left <= 3);
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
    const { used, limit } = state.budget;
    if (sync) sync.dataset.tip = `${tip}${synced ? ` Your pull requests were read at ${clock(synced)}.` : ""}${sched?.team_at ? ` Teammates' last read: ${clock(sched.team_at)}.` : ""} PixelDeck reads your open pull requests every minute, the rest every 5 to 15 minutes. ${used} of ${limit} API calls used this hour.`;
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
      if (typeof LockScreen !== "undefined") LockScreen.seen(status.lock);
    } catch { offline = true; }
    paint();
  }
  function start() {
    $id("bell").innerHTML = `${PixelIcon.svg("bell")}<span class="st-badge" id="bell-badge" hidden></span>`;
    $id("refresh").insertAdjacentHTML("beforeend", PixelIcon.svg("refresh"));
    for (const b of document.querySelectorAll("#winctl [data-win]")) {
      b.innerHTML = b.dataset.win === "maximize"
        ? `<span class="win-max">${PixelIcon.svg("max", 1)}</span><span class="win-restore">${PixelIcon.svg("restore", 1)}</span>`
        : PixelIcon.svg(b.dataset.win === "minimize" ? "min" : "close", 1);
    }
    // The bell always shows: behind it are the notifications and the quick toggles.
    $id("bell").hidden = false;
    if (typeof Notify !== "undefined") Notify.onChange(paintBell);
    if (typeof ControlCenter !== "undefined") ControlCenter.start();
    clearInterval(tick);
    tick = setInterval(paintSync, 1000);
    poll();
  }
  return { start, paint, poll };
})();

const Settings = (() => {
  const TABS = [["setup", "Setup"], ["connections", "Connected apps"], ["notifications", "Notifications"], ["look", "Personalise"], ["about", "About"]];
  let tab = "connections", report = null, loading = false, musicHooked = false, appOpen = null;
  // The setup form is redrawn with the rest of the pane, so what you typed lives here until saved.
  let setup = null, draft = {}, checks = null;
  let player = null, jiraOpen = false, playerPoll = 0;
  const TOKEN_PAGE = "https://id.atlassian.com/manage-profile/security/api-tokens";
  const dialog = () => document.getElementById("settings-dialog");
  const pill = (ok, yes, no) => `<span class="set-pill is-${ok === true ? "ok" : ok === false ? "bad" : "warn"}">${esc(ok === true ? yes : ok === false ? no : "Checking")}</span>`;
  const row = (label, value, opts = {}) => value == null || value === "" ? "" :
    `<div class="set-row"><dt>${esc(label)}</dt><dd>${opts.code ? `<code>${esc(value)}</code>` : esc(value)}${opts.copy ? ` <button class="link" type="button" data-copy-value="${esc(value)}">Copy</button>` : ""}</dd></div>`;

  // The cards each app had: in the guide drawer now, under the steps.
  function bitbucketCard() {
    const bb = report.bitbucket;
    const bbOk = bb.auth?.ok === true ? true : bb.auth?.ok === false || !bb.token_set ? false : null;
    const used = bb.budget?.used || 0, limit = bb.budget?.limit || 1000;
    return `<section class="set-card"><header><h3>Bitbucket</h3>${pill(bbOk, bb.me ? `Connected as ${bb.me.name}` : "Connected", bb.token_set ? "Token refused" : "No token yet")}</header>
        ${bb.auth?.error ? `<p class="set-error">${esc(bb.auth.error)}</p>` : ""}
        <dl class="set-rows">${row("Workspace", bb.workspace)}${row("Account", bb.email)}${row("Project", bb.project ? bb.project.name : "Every project")}${row("Token", bb.token_set ? "set (never shown)" : "missing")}${row("Settings file", bb.env_file, { code: true, copy: true })}</dl>
        <div class="set-meter" data-tip="${used} of ${limit} calls used in the last hour"><span style="--w:${Math.min(1, used / limit).toFixed(3)}"></span></div>
        <p class="set-hint">${used} of ${limit} API calls used in the last hour. Change the account or the token in <button class="link" type="button" data-settings-tab="setup">Setup</button>.</p>
        <p><button class="btn" type="button" data-settings-test>Test the connection</button></p></section>`;
  }
  function claudeCard() {
    const cl = report.claude;
    return `<section class="set-card"><header><h3>Claude Code</h3>${pill(cl.ok, "Ready", cl.path ? "Not answering" : "Not found")}</header>
        ${cl.error ? `<p class="set-error">${esc(cl.error)}</p>` : ""}
        <dl class="set-rows">${row("Program", cl.path, { code: true })}${row("Version", cl.version)}</dl>
        <p class="set-hint">The Claude buttons on a pull request start it in a terminal here. Point <code>${esc(cl.setting)}</code> at it when it is not on your PATH.</p></section>`;
  }
  function gitCard() {
    const git = report.git, app = report.app;
    return `<section class="set-card"><header><h3>Git and terminals</h3>${pill(git.ok && !app.terminal, "Ready", git.ok ? "Terminals unavailable" : "git missing")}</header>
        <dl class="set-rows">${row("git", git.version)}${row("Terminals", app.terminal || "ready")}${row("Your clones", app.workspace_root, { code: true })}${row("Brief templates", app.briefs_dir, { code: true })}</dl></section>`;
  }
  // Spotify: the Client ID of your own Spotify app, saved from here. It is not shown back.
  function spotifyIdForm(sp) {
    if (!(state.features || []).includes("spotify-client-id")) return "";
    return `<form class="set-form" data-spotify-form autocomplete="off">
        <label class="field-block"><span>Client ID</span><input name="client_id" data-keep="spotify-cid" spellcheck="false" autocomplete="off"
          placeholder="${sp?.configured ? "Saved: paste another to change it" : "The line of letters and digits under your app's name"}"></label>
        <p class="set-error" data-spotify-error hidden></p>
        <p class="set-actions"><button class="btn btn--primary" type="submit">Save</button>${sp?.configured ? ' <button class="btn btn--quiet" type="button" data-spotify-clear>Remove</button>' : ""}</p></form>`;
  }

  /* Connected apps: what PixelDeck connects to, grouped, each with an 8-bit icon and its state.
     A click opens its guide beside the list: the steps, each with the link to the exact page,
     then its fields and buttons. The same guides are in the README, for a new colleague. */
  const GUIDE = "https://github.com/ahmedloumx/pixeldeck#";
  const GROUPS = [["code", "Where your code lives"], ["ai", "AI helpers"], ["tickets", "Tickets"], ["media", "Music and video"]];
  const ext = (url, label) => `<button class="link" type="button" data-external="${esc(url)}">${esc(label)}</button>`;
  const toSetup = '<button class="link" type="button" data-settings-tab="setup">Setup</button>';
  function appsModel() {
    const bb = report.bitbucket, cl = report.claude, git = report.git, app = report.app, jira = report.jira;
    const bbOk = bb.auth?.ok === true ? true : bb.auth?.ok === false ? false : null;
    const scopes = ["read:user", "read:workspace", "read:repository", "read:pullrequest"].map((x) => `<code>${x}:bitbucket</code>`).join(", ");
    const apps = [
      { id: "bitbucket", group: "code", label: "Bitbucket", anchor: "set-up-bitbucket", card: bitbucketCard,
        tone: bbOk === true ? "ok" : !bb.token_set ? "off" : bbOk === false ? "bad" : "warn",
        pill: bbOk === true ? "Connected" : !bb.token_set ? "Not set up" : bbOk === false ? "Token refused" : "Checking",
        line: bb.me ? `${bb.workspace}, signed in as ${bb.me.name}` : bb.workspace ? `Workspace ${bb.workspace}` : "Your pull requests and your team's",
        steps: [`Open ${ext(TOKEN_PAGE, "id.atlassian.com, Security, API tokens")} and press <b>Create API token with scopes</b>.`,
          `Pick the app <b>Bitbucket</b> and tick ${scopes}. Add <code>write:pullrequest:bitbucket</code> to merge from here.`,
          `Paste it in ${toSetup} with your Atlassian email and your workspace, then press <b>Check</b> and <b>Save</b>.`] },
      { id: "git", group: "code", label: "Git and terminals", anchor: "set-up-git", card: gitCard,
        tone: git.ok && !app.terminal ? "ok" : git.ok ? "warn" : "bad", pill: git.ok && !app.terminal ? "Ready" : git.ok ? "No terminals" : "git missing",
        line: git.ok ? `${git.version || "git"}, your clones and the terminals` : "Install git for the terminals and the Repos panel",
        steps: [`Install git from ${ext("https://git-scm.com/downloads", "git-scm.com/downloads")}.`,
          `Point <b>Your clones</b> in ${toSetup} at the folder that holds your repositories.`] },
      { id: "github", group: "code", label: "GitHub", soon: true, line: "Pull requests from github.com" },
      { id: "gitlab", group: "code", label: "GitLab", soon: true, line: "gitlab.com or your own server" },
      { id: "claude", group: "ai", label: "Claude Code", anchor: "set-up-claude-code", card: claudeCard,
        tone: cl.ok ? "ok" : cl.path ? "bad" : "off", pill: cl.ok ? "Ready" : cl.path ? "Not answering" : "Not found",
        line: cl.ok ? `${cl.version || "Found"}: fixes and reviews in a terminal` : "Fixes and reviews pull requests in a terminal here",
        steps: [`Install Claude Code: ${ext("https://docs.claude.com/en/docs/claude-code/overview", "docs.claude.com, Claude Code")}.`,
          "Run <code>claude</code> once in a terminal and sign in.",
          `Not on your PATH? Put its full path in <code>${esc(cl.setting)}</code> in the settings file.`] },
      { id: "gemini", group: "ai", label: "Gemini CLI", soon: true, line: "Another brain for the blob" },
      { id: "codex", group: "ai", label: "Codex CLI", soon: true, line: "Another brain for the blob" },
    ];
    if (jira) {
      const setUp = !!(jira.site && jira.token_set);
      apps.push({ id: "jira", group: "tickets", label: "Jira", anchor: "set-up-jira", card: () => jiraHtml(jira, bb),
        tone: jira.ok === false && setUp ? "bad" : setUp ? "ok" : jira.site ? "warn" : "off",
        // The settings read does not call Jira, so a saved token not tried yet is just set up.
        pill: jira.ok === true ? "Connected" : setUp && jira.ok === false ? "Token refused" : setUp ? "Set up" : jira.site ? "Needs a token" : "Not set up",
        line: jira.site ? `${jira.site.replace(/^https:\/\//, "")}${jira.me ? `, as ${jira.me}` : ""}` : "Ticket status in the merge popup",
        steps: [`Open ${ext(TOKEN_PAGE, "id.atlassian.com, Security, API tokens")}, signed in as the account you use for Jira.`,
          "Press <b>Create API token</b>, name it PixelDeck, and copy it.",
          "Paste it below with your Jira address, then press <b>Save and test</b>."] });
    }
    if (typeof Music !== "undefined" && (state.features || []).includes("spotify")) {
      const sp = Music.status();
      apps.push({ id: "spotify", group: "media", label: "Spotify", anchor: "set-up-spotify", card: () => spotifyIdForm(sp) + spotifyHtml(),
        tone: !sp ? "off" : sp.connected ? "ok" : sp.configured && !sp.client_id_ok ? "bad" : sp.configured ? "warn" : "off",
        pill: !sp ? "Checking" : sp.connected ? "Connected" : sp.configured && !sp.client_id_ok ? "Check the ID" : sp.configured ? "Not signed in" : "Not set up",
        line: sp?.connected ? `${sp.user ? `${sp.user.name}, ` : ""}${sp.user?.premium ? "Premium" : "the boombox"}` : "The boombox and the mini player",
        steps: [`Open ${ext("https://developer.spotify.com/dashboard", "developer.spotify.com/dashboard")}, create an app (any name) and tick <b>Web API</b>.`,
          `Add this redirect URI to it: <code>${esc(sp?.redirect_uri || "")}</code>${sp?.redirect_uri ? ` <button class="link" type="button" data-copy-value="${esc(sp.redirect_uri)}">Copy</button>` : ""}.`,
          "Paste the app's <b>Client ID</b> below and save it, then press <b>Connect</b> and sign in to Spotify."] });
    }
    apps.push({ id: "youtube", group: "media", label: "YouTube", soon: true, line: "Videos in a floating pixel TV" });
    return apps;
  }
  const appIcon = (id, px) => (typeof ControlCenter !== "undefined" ? ControlCenter.appIcon(id, px) : "");
  function appCard(a) {
    const text = `<span class="capp-icon">${appIcon(a.id, 2.6)}</span><span class="capp-text"><b>${esc(a.label)}</b><small>${esc(a.line)}</small></span>`;
    if (a.soon) return `<div class="capp is-soon" aria-disabled="true">${text}<span class="set-pill is-off">Soon</span></div>`;
    return `<button class="capp is-${a.tone}" type="button" data-capp="${a.id}" aria-pressed="${appOpen === a.id}">${text}<span class="set-pill is-${a.tone}">${esc(a.pill)}</span></button>`;
  }
  function drawerHtml(a) {
    return `<aside class="capp-drawer" aria-label="${esc(a.label)}: how to connect it">
        <header class="capp-drawer-head"><span class="capp-icon">${appIcon(a.id, 3.4)}</span><h3>${esc(a.label)}</h3><span class="set-pill is-${a.tone}">${esc(a.pill)}</span>
          <button class="btn btn--quiet capp-close" type="button" data-capp-close aria-label="Close the guide">×</button></header>
        <ol class="capp-steps">${a.steps.map((x) => `<li>${x}</li>`).join("")}</ol>
        ${a.card()}
        <p class="set-hint capp-more">${ext(GUIDE + a.anchor, "The whole guide, online")}</p></aside>`;
  }
  function connectionsHtml() {
    if (!report) return `<p class="set-hint">${loading ? "Asking the server…" : "This needs a newer PixelDeck server: restart it once."}</p>`;
    const apps = appsModel();
    const open = apps.find((a) => a.id === appOpen && !a.soon);
    const groups = GROUPS.map(([g, title]) => {
      const list = apps.filter((a) => a.group === g);
      return list.length ? `<h3 class="capps-title">${esc(title)}</h3><div class="capps">${list.map(appCard).join("")}</div>` : "";
    }).join("");
    return `<p class="set-hint">Green: working. Yellow: set up, but it needs you. Grey: not connected yet. Click one for its guide: every step links to the page it needs.</p>
      <div class="capps-wrap${open ? " has-drawer" : ""}"><div class="capps-list">${groups}</div>${open ? drawerHtml(open) : ""}</div>`;
  }
  function setupHtml() {
    if (!(state.features || []).includes("setup")) return `<p class="set-hint">This needs a newer PixelDeck server: restart it once.</p>`;
    if (!setup) return `<p class="set-hint">${loading ? "Asking the server…" : "Could not read the setup."}</p>`;
    const value = (key) => esc(draft[key] ?? setup[key] ?? "");
    const where = { store: `in ${setup.store}`, file: "in the settings file", environment: "in the environment" }[setup.token];
    const scopes = ["read:user", "read:workspace", "read:repository", "read:pullrequest"].map((s) => `<code>${s}:bitbucket</code>`).join(", ");
    const results = checks ? `<ul class="setup-checks">${checks.results.map((r) => `<li class="is-${r.ok ? "ok" : r.ok === false ? "bad" : "warn"}">
        <span>${esc(r.label)}</span><code>${esc(r.scope)}</code>${r.ok ? "<small>ok</small>" : `<small>${esc(r.hint || "")}</small>`}</li>`).join("")}</ul>
        ${checks.ok ? `<p class="set-hint">${checks.me ? `Signed in as ${esc(checks.me)}. ` : ""}Everything PixelDeck reads answers. Save to use it.</p>` : ""}` : "";
    return `<section class="set-card"><header><h3>Bitbucket</h3>${pill(!setup.needed, "Set up", "Not set up")}</header>
        <p class="set-hint">PixelDeck reads your pull requests with an Atlassian API token. Create one at
          <button class="link" type="button" data-external="${TOKEN_PAGE}">id.atlassian.com</button> with
          <em>Create API token with scopes</em>, app Bitbucket, and tick ${scopes}. Add <code>write:pullrequest:bitbucket</code>
          to open, merge and decline pull requests here, and <code>write:repository:bitbucket</code> to commit from the Repos panel.</p>
        <form class="setup-form" data-setup-form autocomplete="off">
          <label class="field-block">Atlassian email<input name="email" type="email" value="${value("email")}" placeholder="you@company.com"></label>
          <label class="field-block">API token<input name="token" type="password" value="${esc(draft.token || "")}" placeholder="${setup.token ? "saved: leave empty to keep it" : "paste the token"}"></label>
          <label class="field-block">Workspace<input name="workspace" value="${value("workspace")}" placeholder="${esc("bitbucket.org/<this part>/repo")}" spellcheck="false"></label>
          <label class="field-block">Your clones<input name="root" value="${value("root")}" placeholder="${esc(setup.root_default)}" spellcheck="false"></label>
          <p class="set-hint field-wide">${setup.token ? `A token is saved ${esc(where)}.` : "No token yet."}
            ${setup.store ? `Saving puts the token in ${esc(setup.store)}, not in a file.` : "Saving puts the token in the settings file."}
            Your clones is the folder that holds your local repositories.</p>
          ${setup.from_environment.length ? `<p class="set-error field-wide">Set in the environment, which wins over what you save here: ${setup.from_environment.map(esc).join(", ")}</p>` : ""}
          <p class="setup-actions field-wide"><button class="btn" type="button" data-setup-check>Check</button><button class="btn btn--primary" type="submit">Save</button></p>
        </form>${results}</section>
      <section class="set-card"><header><h3>Import</h3></header>
        <p class="set-hint">Ran PixelDeck from a git clone before? Copy its settings and token: give the clone's folder or its <code>.env</code>.</p>
        <form class="setup-form" data-setup-import autocomplete="off">
          <label class="field-block field-wide">Folder or .env file<input name="path" value="${esc(draft.path || "")}" placeholder="${esc("C:\\Users\\you\\pixeldeck")}" spellcheck="false"></label>
          <p class="setup-actions field-wide"><button class="btn" type="submit">Import</button></p>
        </form></section>
      <dl class="set-rows">${row("Settings file", setup.env_file, { code: true, copy: true })}</dl>`;
  }
  async function saveSetup(name, form, button) {
    const before = setup?.needed;
    try {
      const answer = await busy(button, () => api(name, { method: "POST", body: Object.fromEntries(new FormData(form)) }));
      setup = answer; draft = {}; checks = null;
      toast("Saved.", answer.imported ? `Imported ${answer.imported.length} settings.` : answer.store && answer.token === "store" ? `The token is in ${answer.store}.` : "");
      // The first setup: start over, so the page connects with the new account.
      if (before && !answer.needed) { setTimeout(() => location.reload(), 700); return; }
      paint(); load();
    } catch (err) { toast("Not saved.", err.message, true); }
  }
  // Jira: what is set, and a form to set it up or change it. The token is written, never read back.
  function jiraHtml(jira, bb) {
    const setUp = !!(jira.site && jira.token_set);
    const form = `<form class="set-form" data-jira-form>
        <label class="field-block"><span>Site</span><input type="url" name="site" data-keep="jira-site" placeholder="https://your-team.atlassian.net" value="${esc(jira.site || "")}" autocomplete="off" required></label>
        <label class="field-block"><span>Email</span><input type="email" name="email" data-keep="jira-email" placeholder="${esc(bb?.email || "you@company.com")}" value="${esc(jira.email && jira.email !== bb?.email ? jira.email : "")}" autocomplete="off"></label>
        <label class="field-block"><span>API token</span><input type="password" name="token" data-keep="jira-token" placeholder="${jira.token_set ? "Saved: leave empty to keep it" : "Paste the token"}" autocomplete="new-password"></label>
        <p class="set-hint">Create a token at <button class="link" type="button" data-external="https://id.atlassian.com/manage-profile/security/api-tokens">id.atlassian.com, Security, API tokens</button>, signed in as the Atlassian account you use for Jira. Leave the email empty when it is the Bitbucket one${bb?.email ? ` (${esc(bb.email)})` : ""}.</p>
        <p class="set-error" data-jira-error hidden></p>
        <p class="set-actions"><button class="btn btn--primary" type="submit">Save and test</button>${setUp ? ' <button class="btn btn--quiet" type="button" data-jira-clear>Remove</button>' : ""}</p></form>`;
    return `<section class="set-card"><header><h3>Jira</h3>${pill(jira.ok, jira.me ? `Connected as ${jira.me}` : "Connected", jira.site ? (jira.token_set ? "Token refused" : "No Jira token") : "Not set up")}</header>
        ${jira.error ? `<p class="set-error">${esc(jira.error)}</p>` : ""}
        <dl class="set-rows">${row("Site", jira.site)}${row("Email", jira.email)}${row("Token", jira.token_set ? "set (never shown)" : "missing")}</dl>
        <p class="set-hint">Shows a ticket's status in the merge popup and moves it once the merge is through. The Bitbucket token is never sent to Jira.</p>
        ${setUp ? `<details class="set-more"${jiraOpen ? " open" : ""}><summary>Change the Jira settings</summary>${form}</details>` : form}</section>`;
  }
  // PixelDeck's own Spotify player (spotifyd): installed, signed in, running.
  function playerHtml() {
    const pl = player;
    if (!pl) return "";
    const label = !pl.enabled ? "Turned off" : !pl.installed ? "Not installed" : !pl.signed_in ? "Not signed in"
      : pl.state === "running" ? "Playing as " + pl.device_name : pl.state === "stopped" ? "Stopped, restarting" : pl.state === "error" ? "Could not start" : "Starting";
    const tone = pl.state === "running" ? "ok" : !pl.installed || pl.state === "error" ? "bad" : "warn";
    const si = pl.signin || {};
    let action = "";
    if (!pl.installed) {
      action = `<p class="set-hint">Install spotifyd from <button class="link" type="button" data-external="${esc(pl.install_url)}">its releases page</button> (the Linux file ending in <code>x86_64-full.tar.gz</code>) and put <code>spotifyd</code> in <code>~/.local/bin</code>, then open these settings again.</p>`;
    } else if (si.state === "waiting") {
      action = `<p class="set-hint">Waiting for you in the browser: log in to Spotify there.${si.url ? ` No browser opened? <button class="link" type="button" data-external="${esc(si.url)}">Open the login page</button>.` : ""}</p>`;
    } else if (!pl.signed_in) {
      action = `<p class="set-hint">Sign in once, on Spotify's own login page: PixelDeck never sees your password. It needs Spotify Premium.</p>
        ${si.state === "failed" ? `<p class="set-error">${esc(si.error || "The sign-in did not finish.")}</p>` : ""}
        <p class="set-actions"><button class="btn btn--primary" type="button" data-spotifyd-signin>Sign in to Spotify</button></p>
        <dl class="set-rows">${row("Or in a terminal", pl.signin_command, { code: true, copy: true })}</dl>`;
    } else {
      action = `${pl.error ? `<p class="set-error">${esc(pl.error)}</p>` : ""}<p class="set-actions"><button class="btn" type="button" data-spotifyd-restart>Restart the player</button></p>`;
    }
    return `<div class="set-sub"><header><h4>PixelDeck's player</h4><span class="set-pill is-${tone}">${esc(label)}</span></header>
        <p class="set-hint">Plays without the Spotify app: spotifyd, an open-source Spotify Connect player, runs while PixelDeck runs and shows in your devices as <b>${esc(pl.device_name)}</b>. It is not made by Spotify.</p>
        ${action}</div>`;
  }
  // The music player owns the Spotify connection; this card shows it and borrows its buttons.
  function spotifyHtml() {
    if (typeof Music === "undefined" || !(state.features || []).includes("spotify")) return "";
    const sp = Music.status();
    if (!sp) return `<section class="set-card"><header><h3>Spotify</h3>${pill(null)}</header><p class="set-hint">Asking the server…</p></section>`;
    const tone = sp.connected ? "ok" : sp.configured ? "warn" : "bad";
    const label = sp.connected ? (sp.user ? `Connected as ${sp.user.name}` : "Connected") : sp.configured ? "Not connected" : "Not set up";
    const plan = sp.user ? (sp.user.premium ? "Premium" : "Free: shows what plays, cannot control it") : "";
    const buttons = sp.connected
      ? '<button class="btn" type="button" data-music="open">Open the player</button> <button class="btn btn--quiet" type="button" data-music="disconnect">Disconnect</button>'
      : `<button class="btn btn--primary" type="button" data-music="connect"${sp.configured && sp.client_id_ok ? "" : " disabled"}>Connect</button> <button class="btn btn--quiet" type="button" data-music="open">${sp.configured ? "Open the player" : "How to set it up"}</button>`;
    return `<section class="set-card"><header><h3>Spotify</h3><span class="set-pill is-${tone}">${esc(label)}</span></header>
        ${sp.error ? `<p class="set-error">${esc(sp.error)}</p>` : ""}
        <dl class="set-rows">${row("Plan", plan)}${row("Client ID", sp.configured ? (sp.client_id_ok ? "set" : "set, but it does not look like a Spotify client ID") : `missing: ${sp.setting}`)}${row("Redirect URI", sp.redirect_uri, { code: true, copy: true })}</dl>
        <p class="set-hint">For the Music panel. Register the redirect URI exactly as shown in your Spotify app, and save its Client ID above (it lands in <code>${esc(sp.setting)}</code>). Play, pause, skip, seek and volume need Spotify Premium, for you and for the app's owner.</p>
        <p>${buttons}</p>${playerHtml()}</section>`;
  }
  function lookHtml() {
    const p = Prefs.get();
    const choice = (key, value, label, extra = "") => `<button class="date-chip" type="button" data-pref="${key}" data-value="${value}" aria-pressed="${p[key] === value}"${extra}>${label}</button>`;
    const swatches = ACCENTS
      .map(([v, label]) => `<button class="swatch-btn" type="button" data-pref="accent" data-value="${v}" aria-pressed="${p.accent === v}" data-swatch="${v}"><span aria-hidden="true"></span>${label}</button>`).join("");
    const toggle = (key, label, hint) => `<label class="switch"><input type="checkbox" data-pref-toggle="${key}"${p[key] ? " checked" : ""}><span class="switch-knob" aria-hidden="true"></span><span>${esc(label)}${hint ? `<small>${esc(hint)}</small>` : ""}</span></label>`;
    // fonts.check() answers true for any font that is not a web font, installed or not; the
    // "Arcade Local" face knows whether its local() source loaded.
    const arcade = [...document.fonts].find((f) => f.family.replace(/"/g, "") === "Arcade Local");
    if (arcade?.status === "unloaded") arcade.load().catch(() => {});
    const arcadeHint = { loaded: " (it is)", error: " (it is not, so Press Start stands in)" }[arcade?.status] || "";
    const current = (THEMES.find(([id]) => id === p.theme) || THEMES[0])[0];
    const themes = THEMES.map(([v, label, tone]) => `<button class="theme-card" type="button" data-pref="theme" data-value="${v}" aria-pressed="${current === v}" data-theme-card="${v}">
        <span class="theme-shot" aria-hidden="true"><i></i><i></i></span><b>${label}</b><small>${tone.toUpperCase()}</small></button>`).join("");
    return `<div class="set-block"><h3>Theme</h3><div class="themes">${themes}</div><div class="look-crew" aria-hidden="true">${Sprites.svg("bot", { scale: 4, idle: "blink" })}${Sprites.svg("buddy", { scale: 4 })}</div>
        <p class="set-hint">The bot stays blue in every theme; the blob, the AI helper, takes each theme's colour. Hover it after a change.</p></div>
      <div class="set-block"><h3>Accent</h3><div class="swatches">${swatches}</div></div>
      <div class="set-block"><h3>Numbers</h3><div class="date-bar">${choice("numbers", "arcade", "Arcade")}${choice("numbers", "press", "Press Start")}${choice("numbers", "pixelify", "Pixelify")}${choice("numbers", "plain", "Plain")}</div>
        <div class="date-bar">${choice("numSize", "small", "Small")}${choice("numSize", "medium", "Medium")}${choice("numSize", "large", "Large")}</div>
        <p class="set-hint">Counts, badges and the small labels. Arcade uses the ArcadeClassic font when it is installed on this computer${arcadeHint}.</p></div>
      <div class="set-block"><h3>Motion</h3><div class="date-bar">${choice("motion", "auto", "Follow the system")}${choice("motion", "calm", "Calm")}</div></div>
      ${typeof PixelChrome !== "undefined" && PixelChrome.available() ? `<div class="set-block"><h3>Title bar</h3><div class="date-bar">${choice("titlebar", "custom", "PixelDeck's menu bar")}${choice("titlebar", "system", "The system's")}</div>
        <p class="set-hint">PixelDeck's own bar moves the window from its empty parts and carries the window buttons. The system's brings back the usual title bar above it.</p></div>` : ""}
      <div class="set-block"><h3>Arcade</h3><div class="switch-grid">${toggle("mascots", "Mascots", "the slime and the bot")}${toggle("bubbles", "Bot ideas", "the bot suggests a game while you wait")}${toggle("games", "Games beside the terminal", "Tetris, Snake and Flappy")}</div></div>
      ${typeof LockScreen !== "undefined" ? LockScreen.settingsHtml() : ""}
      <div class="set-block"><h3>Guide</h3><p><button class="btn" type="button" data-guide-again>Show the guide again</button> <button class="btn btn--quiet" type="button" data-welcome-again>Play the welcome again</button></p></div>`;
  }
  function aboutHtml() {
    const app = report?.app;
    return `<div class="set-about">${typeof Sprites !== "undefined" ? `<span class="set-mascots">${Sprites.svg("buddy", { scale: 3 })}${Sprites.svg("bot", { scale: 3 })}</span>` : ""}
      <h3>PixelDeck</h3><p>Your pull requests, your team's, and the terminals and agents that work on them, in one window.</p>
      <dl class="set-rows">${row("Version", app?.version)}${row("Server", app ? `127.0.0.1:${app.port}` : "")}${row("Python", app?.python)}${row("Installed in", app?.app_dir, { code: true })}</dl>
      <p><button class="link" type="button" data-external="https://github.com/ahmedloumx/pixeldeck">github.com/ahmedloumx/pixeldeck</button></p>
      <p><button class="btn btn--quiet" type="button" data-action="open-keys">Keyboard keys</button></p></div>`;
  }
  function paint() {
    const d = dialog();
    if (!d) return;
    const body = tab === "setup" ? setupHtml() : tab === "connections" ? connectionsHtml() : tab === "notifications" ? (typeof Notify !== "undefined" ? Notify.settingsHtml() : "") : tab === "look" ? lookHtml() : aboutHtml();
    // The list of sections is drawn once: redrawing it would replay its entrance on every
    // click. Only the section itself changes, and a redraw of the same one keeps its scroll.
    let pane = d.querySelector(":scope > .set-shell > .set-body");
    if (!pane) {
      d.innerHTML = `<div class="set-shell">
        <nav class="set-tabs" role="tablist" aria-label="Settings">${TABS.map(([id, label]) => `<button class="set-tab" type="button" role="tab" data-settings-tab="${id}" aria-selected="false">${label}</button>`).join("")}</nav>
        <section class="set-body" role="tabpanel"></section></div>`;
      pane = d.querySelector(".set-body");
    }
    for (const b of d.querySelectorAll("[data-settings-tab]")) b.setAttribute("aria-selected", String(b.dataset.settingsTab === tab));
    const same = pane.dataset.tab === tab;
    const top = same ? pane.scrollTop : 0;
    pane.dataset.tab = tab;
    const kept = {};
    for (const el of pane.querySelectorAll("[data-keep]")) kept[el.dataset.keep] = el.value;
    const focused = document.activeElement?.dataset?.keep;
    jiraOpen = !!pane.querySelector("details.set-more[open]") || jiraOpen;
    pane.innerHTML = `<header class="set-head"><h2 id="settings-title">${esc(TABS.find(([id]) => id === tab)[1])}</h2>
        <button class="btn btn--quiet" type="button" data-settings-close aria-label="Close settings">Close</button></header>${body}`;
    if (same) {
      for (const el of pane.querySelectorAll("[data-keep]")) if (el.dataset.keep in kept) el.value = kept[el.dataset.keep];
      if (focused) pane.querySelector(`[data-keep="${focused}"]`)?.focus();
    }
    pane.scrollTop = top;
    if (!same) { pane.classList.remove("is-switching"); void pane.offsetWidth; pane.classList.add("is-switching"); }
  }
  async function load(test = false) {
    if (typeof Music !== "undefined" && (state.features || []).includes("spotify")) {
      // music.js loads after this file, so the card subscribes the first time the dialog opens
      if (!musicHooked) { musicHooked = true; Music.onStatus(() => { if (dialog()?.open && tab === "connections") paint(); }); }
      Music.refresh();
    }
    if (!(state.features || []).includes("settings")) { report = null; paint(); return; }
    loading = true; paint();
    const wantSetup = (state.features || []).includes("setup");
    try {
      [report, setup, player] = await Promise.all([api(test ? "settings-test" : "settings", test ? { method: "POST", body: {} } : undefined),
        wantSetup ? api("setup") : null, (state.features || []).includes("spotifyd") ? api("spotifyd").catch(() => null) : null]);
    } catch (err) { toast("Could not read the settings.", err.message, true); }
    loading = false;
    if (dialog()?.open) paint();
  }
  function open(which, opts = {}) {
    if (which) tab = which;
    if ("app" in opts) appOpen = opts.app;
    const d = dialog();
    paint();
    if (!d.open) d.showModal();
    load();
    // whether a lock screen passcode is set, for Personalise
    if (typeof LockScreen !== "undefined") LockScreen.refreshCode().then(() => { if (d.open && tab === "look") paint(); });
  }
  document.addEventListener("click", async (e) => {
    const t = e.target;
    if (!t.closest("#settings-dialog")) return;
    const tabBtn = t.closest("[data-settings-tab]");
    if (tabBtn) { tab = tabBtn.dataset.settingsTab; return paint(); }
    if (t.closest("[data-settings-close]")) return dialog().close();
    const capp = t.closest("[data-capp]");
    if (capp) {
      appOpen = appOpen === capp.dataset.capp ? null : capp.dataset.capp;
      paint();
      // stacked under a narrow dialog, the guide is above the list: bring its top into view
      const pane = dialog().querySelector(".set-body"), dr = pane?.querySelector(".capp-drawer");
      const above = dr ? pane.getBoundingClientRect().top - dr.getBoundingClientRect().top : 0;
      if (above > 0) pane.scrollTop -= above + 8;
      return;
    }
    if (t.closest("[data-capp-close]")) { appOpen = null; return paint(); }
    if (t.closest("[data-spotify-clear]")) {
      const b = t.closest("[data-spotify-clear]");
      if (!armed(b, "Sure? Remove it")) return;
      try { await busy(b, () => api("spotify-client-id", { method: "POST", body: { clear: true } })); await Music.refresh(); paint(); toast("Spotify's Client ID removed."); }
      catch (err) { toast("Could not remove it.", err.message, true); }
      return;
    }
    if (t.closest("[data-jira-clear]")) {
      const b = t.closest("[data-jira-clear]");
      if (!armed(b, "Sure? Remove Jira")) return;
      try { const out = await busy(b, () => api("jira-settings", { method: "POST", body: { clear: true } })); report.jira = out.jira; jiraOpen = false; paint(); toast("Jira settings removed."); }
      catch (err) { toast("Could not remove the Jira settings.", err.message, true); }
      return;
    }
    if (t.closest("[data-spotifyd-signin]")) {
      const b = t.closest("[data-spotifyd-signin]");
      try { player = await busy(b, () => api("spotifyd-signin", { method: "POST", body: {} })); } catch (err) { toast("Could not start the sign-in.", err.message, true); return; }
      paint();
      followPlayer();
      return;
    }
    if (t.closest("[data-spotifyd-restart]")) {
      const b = t.closest("[data-spotifyd-restart]");
      try { player = await busy(b, () => api("spotifyd-restart", { method: "POST", body: {} })); paint(); } catch (err) { toast("Could not restart the player.", err.message, true); }
      return;
    }
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
    if (t.closest("[data-welcome-again]")) { dialog().close(); if (typeof Welcome !== "undefined") Welcome.play(); return; }
    const check = t.closest("[data-setup-check]");
    if (check) {
      try { checks = await busy(check, () => api("setup-check", { method: "POST", body: Object.fromEntries(new FormData(check.form)) })); paint(); }
      catch (err) { toast("Could not check.", err.message, true); }
      return;
    }
    if (t === dialog()) dialog().close();  // a click on the backdrop
  });
  document.addEventListener("input", (e) => {
    const t = e.target;
    if (t.closest("#settings-dialog") && t.closest("[data-setup-form], [data-setup-import]") && t.name) draft[t.name] = t.value;
  });
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (!form.closest("#settings-dialog")) return;
    if (form.matches("[data-setup-form]")) { e.preventDefault(); saveSetup("setup", form, form.querySelector("[type=submit]")); }
    if (form.matches("[data-setup-import]")) { e.preventDefault(); saveSetup("setup-import", form, form.querySelector("[type=submit]")); }
  });
  // While a sign-in waits on the browser, ask every two seconds how it went (five minutes at most).
  function followPlayer(started = Date.now()) {
    clearTimeout(playerPoll);
    if ((player?.signin?.state !== "waiting") || Date.now() - started > 300000) { if (typeof Music !== "undefined") Music.refresh(); return; }
    playerPoll = setTimeout(async () => {
      try { player = await api("spotifyd"); } catch { /* the next look */ }
      if (dialog()?.open && tab === "connections") paint();
      if (player?.signin?.state === "done") toast("Signed in: PixelDeck's player is on.", `It shows in your Spotify devices as ${player.device_name}.`);
      followPlayer(started);
    }, 2000);
  }
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest?.("[data-jira-form]");
    if (!form) return;
    e.preventDefault();
    const b = form.querySelector("[type=submit]"), err = form.querySelector("[data-jira-error]");
    err.hidden = true;
    const body = { site: form.site.value, email: form.email.value, token: form.token.value };
    try {
      const out = await busy(b, () => api("jira-settings", { method: "POST", body }));
      report.jira = out.jira;
      jiraOpen = !out.jira.ok;
      for (const el of form.querySelectorAll("[data-keep]")) el.value = "";
      paint();
      if (out.jira.ok) toast(`Jira connected${out.jira.me ? ` as ${out.jira.me}` : ""}.`, "Tickets show their status in the merge popup now.");
      else toast("Saved, but Jira refused it.", out.jira.error || "", true);
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  });
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest?.("[data-spotify-form]");
    if (!form) return;
    e.preventDefault();
    const b = form.querySelector("[type=submit]"), err = form.querySelector("[data-spotify-error]");
    err.hidden = true;
    try {
      await busy(b, () => api("spotify-client-id", { method: "POST", body: { client_id: form.client_id.value } }));
      form.client_id.value = "";
      if (typeof Music !== "undefined") await Music.refresh();
      paint();
      toast("Saved.", "Now press Connect and sign in to Spotify in your browser.");
    } catch (ex) { err.textContent = ex.message; err.hidden = false; }
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.matches("[data-pref-toggle]")) Prefs.set({ [t.dataset.prefToggle]: t.checked });
    if (t.closest("#settings-dialog") && (t.matches("[data-notify-kind]") || t.matches("[data-notify-scope]"))) setTimeout(paint, 0);
  });
  if (typeof Notify !== "undefined") Notify.onChange(() => { if (dialog()?.open && tab === "notifications") paint(); });
  return { open, paint };
})();
