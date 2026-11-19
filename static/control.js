"use strict";

/* The control center: the app icons and the quick toggles behind the bell, and which of them
   sit in the status bar. Each app icon lives either in the bar or in the control center: drag
   it from one to the other, or press × on it in the bar and ↑ on it in the control center.
   Each toggle can be pinned to the bar. The order, the pins, the layout and the apps' places
   are kept with the other preferences (Prefs "bar"), so they come back after a restart.

   It only reads what the page already knows (the account, the music status) and, once after
   boot and after the settings dialog closes, /api/settings for Jira and Claude, which never
   calls Bitbucket. */

const ControlCenter = (() => {
  const LAYOUTS = [["grid", "Grid"], ["compact", "Compact"], ["list", "List"]];
  const has = (f) => (state.features || []).includes(f);
  let report = null, editing = false, dragApp = null, dragTile = null;

  /* ---------------------------------------------------------------- pictures */
  // 8-bit app icons: a row per line, a letter per colour.
  const APP_ART = {
    bitbucket: [["............", "bbbbbbbbbbbb", ".bbbbbbbbbb.", ".bbbbbbbbbb.", ".bbbwwwwbbb.", "..bbwbbwbb..", "..bbwbbwbb..", "..bbwwwwbb..", "...bbbbbb...", "...bbbbbb...", "............"], { b: "#2684ff", w: "#0a1a33" }],
    jira: [[".....bb.....", "....bbbb....", "...bb..bb...", "..bb.bb.bb..", ".bb.bbbb.bb.", "bb.bb..bb.bb", ".bb.bbbb.bb.", "..bb.bb.bb..", "...bb..bb...", "....bbbb....", ".....bb....."], { b: "#2684ff" }],
    spotify: [["....gggg....", "..gggggggg..", ".gggggggggg.", ".gkkkkkkggg.", "gggggggkkggg", "ggkkkkkkgggg", "gggggggkkggg", "gggkkkkkgggg", ".ggggggkggg.", ".gggggggggg.", "..gggggggg..", "....gggg...."], { g: "#1ed760", k: "#0a2a15" }],
    claude: [[".....c.....", ".c...c...c.", "..c..c..c..", "...c.c.c...", "....ccc....", "ccccccccccc", "....ccc....", "...c.c.c...", "..c..c..c..", ".c...c...c.", ".....c....."], { c: "#d97757" }],
    git: [["....o.......", "....o.......", "....o...o...", "....o..o....", "....o.o.....", "....oo......", "....o.......", "...ooo......", "..ooooo.....", "...ooo......"], { o: "#f05032" }],
    github: [[".L........L.", ".LL......LL.", ".LLLLLLLLLL.", "LLLLLLLLLLLL", "LLkkLLLLkkLL", "LLkkLLLLkkLL", "LLLLLLLLLLLL", ".LLLLLLLLLL.", "..LLLLLLLL..", "..L.LLLL.L..", "...LLLLLL..."], { L: "#c9d1d9", k: "#0d1117" }],
    gitlab: [[".o........o.", ".oo......oo.", "ooo......ooo", "oooOOOOOOooo", "ooOOOOOOOOoo", ".oOOOOOOOOo.", "..oOOOOOOo..", "...oOOOOo...", "....oOOo....", ".....oo....."], { o: "#fc6d26", O: "#e24329" }],
    gemini: [[".....v.....", ".....v.....", "....vvv....", "....vvv....", "..vvvvvvv..", "vvvvVvvvvvv", "..vvvvvvv..", "....vvv....", "....vvv....", ".....v.....", ".....v....."], { v: "#7aa7ff", V: "#c58cff" }],
    codex: [["kkkkkkkkkkk", "kWWWWWWWWWk", "kWkWWWWWWWk", "kWWkWWWWWWk", "kWkWWWWWWWk", "kWWWWkkkWWk", "kWWWWWWWWWk", "kkkkkkkkkkk"], { k: "#9aa3b2", W: "#161b22" }],
    youtube: [["............", "..rrrrrrrr..", ".rrrrrrrrrr.", ".rrrrwrrrrr.", ".rrrrwwrrrr.", ".rrrrwwwrrr.", ".rrrrwwrrrr.", ".rrrrwrrrrr.", ".rrrrrrrrrr.", "..rrrrrrrr.."], { r: "#ff2a2a", w: "#ffffff" }],
  };
  // Toggle glyphs, drawn in the current ink: # is a lit pixel.
  const GLYPHS = {
    note: ["...######", "...######", "...#....#", "...#....#", "...#....#", ".###..###", "####.####", "####.####", ".##...##."],
    moon: ["...###...", "..##.....", ".##......", ".##......", ".##......", ".##......", "..##...#.", "...#####.", "........."],
    lyrics: ["#########", ".........", "######...", ".........", "########.", ".........", "#####....", ".........", "#######.."],
    tray: ["####.####", "#..#.#..#", "#..#.#..#", "####.####", ".........", "####.####", "#..#.#..#", "#..#.#..#", "####.####"],
    meter: [".........", "#########", "#.#.#.#.#", "#.#.#.#.#", "#.#.#.#.#", "#########", "........."],
    smile: ["..#####..", ".#.....#.", "#..#.#..#", "#.......#", "#.#...#.#", "#..###..#", ".#.....#.", "..#####.."],
    wave: [".........", "..##...##", ".#..#.#..", "#....#...", ".........", "..##...##", ".#..#.#..", "#....#...", "........."],
    diamond: ["....#....", "...###...", "..#####..", ".#######.", "#########", ".#######.", "..#####..", "...###...", "....#...."],
    pop: ["....#####", ".....####", "....#####", "...###.##", "#.###...#", "#..#.....", "#........", "#........", "######..."],
    speaker: ["....#....", "...##..#.", "#####...#", "#####.#.#", "#####.#.#", "#####...#", "...##..#.", "....#....", "........."],
    padlock: ["..#####..", ".##...##.", ".#.....#.", ".#.....#.", "#########", "####.####", "####.####", "#########", "#########"],
  };
  function art(rows, colours, px) {
    const w = Math.max(...rows.map((r) => r.length)), h = rows.length;
    let rects = "";
    rows.forEach((row, y) => {
      let x = 0;
      while (x < row.length) {
        const c = row[x];
        let end = x + 1;
        while (end < row.length && row[end] === c) end++;
        const fill = colours[c];
        if (fill) rects += `<rect x="${x}" y="${y}" width="${end - x}" height="1" fill="${fill}"/>`;
        x = end;
      }
    });
    return `<svg class="cc-pix" viewBox="0 0 ${w} ${h}" width="${Math.round(w * px)}" height="${Math.round(h * px)}" shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`;
  }
  const appIcon = (id, px = 1.6) => (APP_ART[id] ? art(APP_ART[id][0], APP_ART[id][1], px) : "");
  const glyph = (name, px = 2) => art(GLYPHS[name], { "#": "currentColor" }, px);

  /* ---------------------------------------------------------------- apps */
  const musicOn = () => typeof Music !== "undefined" && !!Music.status()?.connected;
  const APPS = [
    // The code host in use: the tile keeps its id (and its place) whichever host that is.
    { id: "bitbucket", get label() { return state.host?.label || "Bitbucket"; }, art: () => state.host?.id || "bitbucket",
      live: () => (state.auth?.ok === false ? false : state.auth?.ok === true || !!state.me),
      yes: () => (state.me ? `connected as ${state.me.name}` : "connected"), no: "not connected", open: () => Settings.open(state.setupNeeded ? "setup" : "connections", { app: state.host?.id || "bitbucket" }) },
    { id: "jira", label: "Jira", live: () => (report?.jira ? !!(report.jira.site && report.jira.token_set) : null),
      yes: () => "set up", no: "not set up yet", open: () => Settings.open("connections", { app: "jira" }) },
    { id: "spotify", label: "Spotify", live: () => (typeof Music === "undefined" ? false : Music.status() ? musicOn() : null),
      yes: () => (typeof Music !== "undefined" && !Music.syncOn() ? "connected, sync off" : "connected"), no: "not connected",
      open: () => {
        if (!musicOn()) return Settings.open("connections", { app: "spotify" });
        if (Layout.visibility().find((v) => v.id === "music")?.hidden) Layout.show("music");
        Layout.activate("music");
      } },
    { id: "claude", label: "Claude Code", live: () => (report?.claude ? !!report.claude.ok : null),
      yes: () => (report?.claude?.version ? `ready, ${report.claude.version}` : "ready"), no: "not found", open: () => Settings.open("connections", { app: "claude" }) },
  ];
  const appTip = (a) => { const l = a.live(); return `${a.label}: ${l === true ? a.yes() : l === false ? a.no : "checking"}. Click to open, drag to move it`; };

  /* ---------------------------------------------------------------- toggles */
  const notifyOn = () => typeof Notify !== "undefined" && has("events");
  const TILES = [
    { id: "sync", label: "Spotify sync", glyph: "note", wide: true, ok: () => typeof Music !== "undefined",
      on: () => Prefs.get("spotifySync") !== false, set: (on) => Prefs.set({ spotifySync: on }),
      tip: "Off: PixelDeck stops asking Spotify what plays" },
    { id: "dnd", label: "Do not disturb", glyph: "moon", ok: notifyOn,
      on: () => Notify.dnd(), set: (on) => Notify.setDnd(on), tip: "No banners; news still lands behind the bell" },
    { id: "lyrics", label: "Lyrics", glyph: "lyrics", ok: () => typeof Music !== "undefined" && !!Music.setLyrics,
      on: () => Music.lyrics(), set: (on) => Music.setLyrics(on), tip: "Lyrics in place of the music library" },
    { id: "float", label: "Mini player", glyph: "pop", ok: () => typeof MusicFloat !== "undefined" && musicOn(),
      on: () => MusicFloat.isOpen(), set: (on) => (on ? MusicFloat.popOut() : MusicFloat.dock()), tip: "A small player floating over the layout" },
    { id: "tray", label: "Apps in the bar", glyph: "tray", ok: () => true,
      on: () => config().tray, set: (on) => save({ tray: on }), tip: "The app icons beside the bell" },
    { id: "api", label: "API meter", glyph: "meter", ok: () => true,
      on: () => config().api, set: (on) => save({ api: on }), tip: "Bitbucket calls left this hour, in the bar. It shows itself when they run low" },
    { id: "mascots", label: "Mascots", glyph: "smile", ok: () => true,
      on: () => !!Prefs.get("mascots"), set: (on) => Prefs.set({ mascots: on }), tip: "The bot and the blob" },
    { id: "calm", label: "Calm motion", glyph: "wave", ok: () => true,
      on: () => Prefs.get("motion") === "calm", set: (on) => Prefs.set({ motion: on ? "calm" : "auto" }), tip: "Fewer animations" },
    { id: "games", label: "Games", glyph: "diamond", ok: () => true,
      on: () => !!Prefs.get("games"), set: (on) => Prefs.set({ games: on }), tip: "Games beside the terminal" },
    { id: "sounds", label: "Sounds", glyph: "speaker", ok: () => true,
      on: () => Prefs.get("sounds") !== false, set: (on) => Prefs.set({ sounds: on }), tip: "The coin, the keys and the jingles" },
    { id: "lock", label: "Lock screen", glyph: "padlock", ok: () => typeof LockScreen !== "undefined" && has("lock"),
      on: () => Prefs.get("lockScreen") !== false, set: (on) => Prefs.set({ lockScreen: on }), tip: "Let's code, when you come back from locking the computer" },
  ];
  const tileOf = (id) => TILES.find((t) => t.id === id);

  /* ---------------------------------------------------------------- what is kept */
  const DEFAULTS = { order: TILES.map((t) => t.id), pins: [], layout: "grid", apps: { bitbucket: true, jira: true, spotify: true, claude: true }, tray: true, api: false };
  function config() {
    const c = Prefs.get("bar") || {};
    const known = new Set(TILES.map((t) => t.id));
    const order = (Array.isArray(c.order) ? c.order : []).filter((id) => known.has(id));
    for (const t of TILES) if (!order.includes(t.id)) order.push(t.id);
    return {
      ...DEFAULTS, ...c, order,
      pins: (Array.isArray(c.pins) ? c.pins : []).filter((id) => known.has(id)),
      layout: LAYOUTS.some(([l]) => l === c.layout) ? c.layout : "grid",
      apps: { ...DEFAULTS.apps, ...(c.apps || {}) },
      tray: c.tray !== false, api: !!c.api,
    };
  }
  function save(patch) { Prefs.set({ bar: { ...config(), ...patch } }); }
  function moveApp(id, inBar) {
    const c = config();
    if (!(id in c.apps) || c.apps[id] === inBar) return;
    save({ apps: { ...c.apps, [id]: inBar }, ...(inBar ? { tray: true } : {}) });
  }
  function flip(id) {
    const t = tileOf(id);
    if (!t || !t.ok()) return;
    try { t.set(!t.on()); } catch (err) { toast("That did not work.", err.message, true); }
    paint();
  }

  /* ---------------------------------------------------------------- the bar */
  function paintBar() {
    const c = config();
    const pins = document.getElementById("st-pins");
    if (pins) {
      pins.innerHTML = c.order.filter((id) => c.pins.includes(id)).map(tileOf).filter((t) => t.ok()).map((t) => {
        const on = t.on();
        return `<button class="st-icon cc-pin" type="button" data-cc-flip="${t.id}" aria-pressed="${on}" aria-label="${esc(t.label)}" data-tip="${esc(`${t.label}: ${on ? "on" : "off"}. ${t.tip}`)}">${glyph(t.glyph, 1.6)}</button>`;
      }).join("");
    }
    const tray = document.getElementById("st-tray");
    if (tray) {
      tray.hidden = !c.tray;
      const shown = APPS.filter((a) => c.apps[a.id]);
      tray.innerHTML = shown.map((a) => {
        const l = a.live();
        return `<span class="cc-tray-app${l === true ? " is-live" : l === false ? " is-off" : ""}" draggable="true" data-cc-app="${a.id}">
          <button class="cc-tray-btn" type="button" data-cc-open="${a.id}" aria-label="${esc(appTip(a))}" data-tip="${esc(appTip(a))}">${appIcon(a.art ? a.art() : a.id)}</button>
          <button class="cc-x" type="button" data-cc-hide="${a.id}" aria-label="Move ${esc(a.label)} to the control center" data-tip="Move to the control center">×</button></span>`;
      }).join("") || `<span class="cc-tray-empty">Drop apps here</span>`;
      tray.classList.toggle("is-empty", !shown.length);
    }
    const apiEl = document.getElementById("st-api");
    if (apiEl) apiEl.hidden = !(c.api || apiEl.classList.contains("is-low"));
  }

  /* ---------------------------------------------------------------- the bell's second tab */
  function html() {
    const c = config();
    const away = APPS.filter((a) => !c.apps[a.id]);
    const apps = away.map((a) => {
      const l = a.live();
      return `<span class="cc-app${l === true ? " is-live" : l === false ? " is-off" : ""}" draggable="true" data-cc-app="${a.id}">
        <button class="cc-app-btn" type="button" data-cc-open="${a.id}" data-tip="${esc(appTip(a))}">${appIcon(a.art ? a.art() : a.id, 2.4)}<small>${esc(a.label)}</small></button>
        <button class="cc-up" type="button" data-cc-show="${a.id}" aria-label="Show ${esc(a.label)} in the bar" data-tip="Show it in the bar">↑</button></span>`;
    }).join("") || `<p class="cc-none">Every app is in the bar. Drag one here to keep it out of the way.</p>`;
    const tiles = c.order.map(tileOf).filter((t) => t.ok()).map((t) => {
      const on = t.on(), pinned = c.pins.includes(t.id);
      return `<div class="cc-tile${on ? " is-on" : ""}${t.wide && c.layout === "grid" ? " is-wide" : ""}${pinned ? " is-pinned" : ""}" data-cc-tile="${t.id}" draggable="${editing}">
        <button class="cc-tile-btn" type="button" data-cc-flip="${t.id}" aria-pressed="${on}" data-tip="${esc(t.tip)}"${editing ? " tabindex=\"-1\"" : ""}>
          <span class="cc-glyph">${glyph(t.glyph, 2)}</span><span class="cc-tile-label">${esc(t.label)}</span><span class="cc-state num8">${on ? "ON" : "OFF"}</span></button>
        ${editing ? `<button class="cc-star" type="button" data-cc-pin="${t.id}" aria-pressed="${pinned}" aria-label="${pinned ? "Unpin" : "Pin"} ${esc(t.label)} ${pinned ? "from" : "to"} the bar">${pinned ? "★" : "☆"}</button>` : ""}</div>`;
    }).join("");
    const layouts = LAYOUTS.map(([id, label]) => `<button class="date-chip" type="button" data-cc-layout="${id}" aria-pressed="${c.layout === id}">${label}</button>`).join("");
    return `<div class="cc">
      <div class="cc-row"><h3 class="cc-title">Apps</h3><span class="cc-hint">Drag one onto the bar, or press ↑</span></div>
      <div class="cc-apps" data-cc-drop="cc">${apps}</div>
      <div class="cc-row"><h3 class="cc-title">Quick toggles</h3>
        <button class="btn btn--quiet cc-edit" type="button" data-cc-edit aria-pressed="${editing}">${editing ? "Done" : "Edit"}</button></div>
      ${editing ? `<div class="cc-editbar"><span>Layout</span>${layouts}<button class="link" type="button" data-cc-reset>Reset</button></div>
        <p class="cc-hint cc-hint--edit">Drag a tile to move it. ☆ pins it to the bar.</p>` : ""}
      <div class="cc-tiles is-${c.layout}${editing ? " is-editing" : ""}">${tiles}</div>
    </div>`;
  }
  function paint() {
    paintBar();
    if (typeof Notify !== "undefined") Notify.repaint?.();
  }

  /* ---------------------------------------------------------------- events */
  document.addEventListener("click", (e) => {
    const t = e.target;
    const fl = t.closest("[data-cc-flip]");
    if (fl) { if (!editing || !fl.closest(".cc-tiles")) flip(fl.dataset.ccFlip); return; }
    const op = t.closest("[data-cc-open]");
    if (op) { const a = APPS.find((x) => x.id === op.dataset.ccOpen); if (a) { if (typeof Notify !== "undefined") Notify.toggle(null, false); a.open(); } return; }
    const hide = t.closest("[data-cc-hide]");
    if (hide) { moveApp(hide.dataset.ccHide, false); paint(); return; }
    const show = t.closest("[data-cc-show]");
    if (show) { moveApp(show.dataset.ccShow, true); paint(); return; }
    const pin = t.closest("[data-cc-pin]");
    if (pin) {
      const c = config(), id = pin.dataset.ccPin;
      save({ pins: c.pins.includes(id) ? c.pins.filter((x) => x !== id) : [...c.pins, id] });
      paint();
      return;
    }
    if (t.closest("[data-cc-edit]")) { editing = !editing; paint(); return; }
    const lay = t.closest("[data-cc-layout]");
    if (lay) { save({ layout: lay.dataset.ccLayout }); paint(); return; }
    if (t.closest("[data-cc-reset]")) { Prefs.set({ bar: { ...DEFAULTS, apps: { ...DEFAULTS.apps } } }); paint(); }
  });

  // Dragging: apps between the bar's tray and the control center, tiles among themselves.
  document.addEventListener("dragstart", (e) => {
    const app = e.target.closest?.("[data-cc-app]");
    const tile = editing && e.target.closest?.("[data-cc-tile]");
    if (!app && !tile) return;
    if (app) dragApp = app.dataset.ccApp; else dragTile = tile.dataset.ccTile;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragApp || dragTile);
    (app || tile).classList.add("is-dragging");
    document.body.classList.toggle("cc-dragging-app", !!dragApp);
  });
  // A drop repaints, which detaches the dragged element: its dragend never reaches the
  // document, so the drop cleans up too.
  function dragDone() {
    dragApp = null; dragTile = null;
    document.body.classList.remove("cc-dragging-app");
    for (const el of document.querySelectorAll(".is-dragging, .is-over")) el.classList.remove("is-dragging", "is-over");
  }
  document.addEventListener("dragend", dragDone);
  const zoneOf = (target) => target.closest?.("#st-tray, [data-cc-drop='cc']");
  document.addEventListener("dragover", (e) => {
    if (dragApp) {
      const zone = zoneOf(e.target);
      if (!zone) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      zone.classList.add("is-over");
    } else if (dragTile) {
      const over = e.target.closest?.("[data-cc-tile]");
      if (!over) return;
      e.preventDefault();
      for (const el of document.querySelectorAll(".cc-tile.is-over")) if (el !== over) el.classList.remove("is-over");
      over.classList.add("is-over");
    }
  });
  document.addEventListener("dragleave", (e) => {
    const zone = dragApp ? zoneOf(e.target) : null;
    if (zone && !zone.contains(e.relatedTarget)) zone.classList.remove("is-over");
  });
  document.addEventListener("drop", (e) => {
    if (dragApp) {
      const zone = zoneOf(e.target);
      if (!zone) return;
      e.preventDefault();
      const id = dragApp;
      dragDone();
      moveApp(id, zone.id === "st-tray");
      paint();
    } else if (dragTile) {
      const over = e.target.closest?.("[data-cc-tile]");
      if (!over) return;
      e.preventDefault();
      const moving = dragTile, order = [...config().order], from = order.indexOf(moving), to = order.indexOf(over.dataset.ccTile);
      dragDone();
      if (from < 0 || to < 0 || from === to) return;
      order.splice(from, 1);
      order.splice(to, 0, moving);
      save({ order });
      paint();
    }
  });

  /* ---------------------------------------------------------------- start */
  async function readReport() {
    if (!has("settings")) return;
    try { report = await api("settings"); } catch { return; }
    paint();
  }
  function start() {
    paintBar();
    Prefs.onChange(() => paint());
    if (typeof Music !== "undefined") { Music.onStatus(() => paint()); Music.onLyrics?.(() => paint()); }
    if (typeof Notify !== "undefined") Notify.onChange(() => paintBar());
    document.getElementById("settings-dialog")?.addEventListener("close", () => readReport());
    setTimeout(readReport, 4000);
  }
  return { start, html, paint, paintBar, config, appIcon, glyph, apps: () => APPS.map((a) => ({ id: a.id, label: a.label, live: a.live() })),
    stopEditing: () => { editing = false; } };
})();
