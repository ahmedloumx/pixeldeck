"use strict";

/* The first start: "Welcome" in eight languages, drawn as pixels, until you insert a coin;
   then five short stages with the crew (the bot, your agent; the blob, your AI helper), where
   your code lives, your tools and your look. SKIP leaves at any moment and nothing is
   required. Help, Welcome plays it again.

   PixelWords draws any script the same way: the word is written small on a hidden canvas, read
   back pixel by pixel and drawn large, so Arabic, Japanese and Russian get the same pixel look
   as the Latin words. Sound plays the arcade's few beeps, only when Sounds is on. */

const Sound = (() => {
  let ctx = null;
  const on = () => typeof Prefs === "undefined" || Prefs.get("sounds") !== false;
  function tone(freq, len, { type = "square", gain = 0.04, at = 0 } = {}) {
    if (!on()) return;
    try {
      ctx = ctx || new AudioContext();
      const o = ctx.createOscillator(), g = ctx.createGain(), t = ctx.currentTime + at;
      o.type = type;
      o.frequency.value = freq;
      g.gain.setValueAtTime(gain, t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + len);
      o.connect(g);
      g.connect(ctx.destination);
      o.start(t);
      o.stop(t + len + 0.02);
    } catch { /* no audio here */ }
  }
  return {
    on,
    set: (v) => Prefs.set({ sounds: !!v }),
    coin: () => { tone(660, 0.07); tone(990, 0.16, { at: 0.11 }); },
    key: (n = 0) => tone(600 + n * 70, 0.035, { gain: 0.03 }),
    step: () => tone(520, 0.05, { gain: 0.03 }),
    ok: () => { tone(784, 0.08); tone(988, 0.08, { at: 0.09 }); tone(1319, 0.2, { at: 0.18 }); },
    wrong: () => tone(150, 0.22, { type: "sawtooth", gain: 0.035 }),
    start: () => [523, 659, 784, 1047].forEach((f, i) => tone(f, 0.1, { at: i * 0.08 })),
  };
})();

const PixelWords = (() => {
  const W = 240, H = 60, P = 4;
  const FONT = '"Pixelify Sans", "Noto Sans CJK JP", "Noto Sans JP", "Noto Sans Arabic", "Noto Sans", sans-serif';
  const PALETTES = [["#ffcf3a", "#ff6b3a"], ["#4d8cff", "#a46bff"], ["#46d16a", "#c8ff5a"], ["#ff5a8c", "#ffcf3a"],
    ["#5ae4ff", "#4d8cff"], ["#ff8a3a", "#ff3a5a"], ["#a46bff", "#5ae4ff"], ["#ffcf3a", "#46d16a"]];
  let tiny = null;
  const still = () => matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.motion === "calm";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // The lit pixels of a word, written small and read back.
  function pixels(text) {
    tiny = tiny || Object.assign(document.createElement("canvas"), { width: W, height: H });
    const t = tiny.getContext("2d", { willReadFrequently: true });
    t.clearRect(0, 0, W, H);
    let size = 40;
    const font = () => `700 ${size}px ${FONT}`;
    t.font = font();
    while (t.measureText(text).width > W - 12 && size > 14) { size -= 2; t.font = font(); }
    t.textAlign = "center";
    t.textBaseline = "middle";
    t.fillStyle = "#fff";
    t.fillText(text, W / 2, H / 2 + 2);
    const data = t.getImageData(0, 0, W, H).data, on = [];
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (data[(y * W + x) * 4 + 3] > 110) on.push([x, y]);
    return on;
  }
  // A word is drawn once into its own canvas, glow included: one blur for the whole word. The
  // reveal then only copies a slice of it each frame. Blurring every pixel on every frame (as
  // this did first) froze WebKitGTK's page for up to half a second at a time.
  function render(on, colors) {
    const c = Object.assign(document.createElement("canvas"), { width: W * P, height: H * P });
    const ctx = c.getContext("2d");
    const path = new Path2D();
    for (const [x, y] of on) path.rect(x * P, y * P, P - 1, P - 1);
    const g = ctx.createLinearGradient(0, 0, W * P, 0);
    g.addColorStop(0, colors[0]);
    g.addColorStop(1, colors[1]);
    ctx.fillStyle = g;
    ctx.shadowColor = colors[0];
    ctx.shadowBlur = 16;
    ctx.fill(path);
    return { canvas: c, fill: g };
  }
  // The word breaking into falling pixels: one path, one fill, no blur, a few frames only.
  function crumble(ctx, on, fill, broken) {
    const path = new Path2D();
    for (const [x, y] of on) {
      const seed = (x * 7 + y * 13) % 100;
      if (seed < broken) continue;
      path.rect(x * P, y * P + Math.max(0, broken - seed) * 0.6, P - 1, P - 1);
    }
    ctx.clearRect(0, 0, W * P, H * P);
    ctx.fillStyle = fill;
    ctx.fill(path);
  }
  /** Cycle words on a canvas (960 x 240) until the returned stop() is called. onWord(i) tells
      which word shows, for a label beside it. Nothing is drawn while the page is hidden. */
  function cycle(canvas, words, { onWord, hold = 1500, shift = 0, breakUp = true } = {}) {
    canvas.width = W * P;
    canvas.height = H * P;
    const ctx = canvas.getContext("2d");
    let live = true, i = 0;
    const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
    (async () => {
      try { await document.fonts.ready; } catch { /* draw with what is there */ }
      while (live) {
        while (live && document.hidden) await sleep(500);
        const word = words[i % words.length], colors = PALETTES[(i + shift) % PALETTES.length];
        onWord?.(i % words.length);
        const on = pixels(word);
        if (!on.length) { i++; continue; }
        const xs = on.map((p) => p[0]);
        const from = Math.min(...xs) * P, to = (Math.max(...xs) + 1) * P;
        const rtl = /[\u0590-\u08ff]/.test(word);
        const { canvas: word2, fill } = render(on, colors);
        if (still()) {
          ctx.clearRect(0, 0, W * P, H * P);
          ctx.drawImage(word2, 0, 0);
          await sleep(hold + 1200);
        } else {
          // Right-to-left words appear from the right. The 16 px margin keeps the glow.
          const start = performance.now(), REVEAL = 520;
          for (let t = 0; live && t < 1;) {
            t = Math.min(1, (performance.now() - start) / REVEAL);
            const w = (to - from) * t + 16;
            const sx = rtl ? Math.max(0, to + 16 - w) : 0, sw = rtl ? W * P - sx : Math.min(W * P, from + w);
            ctx.clearRect(0, 0, W * P, H * P);
            if (sw > 0) ctx.drawImage(word2, sx, 0, sw, H * P, sx, 0, sw, H * P);
            await frame();
          }
          if (!live) return;
          ctx.clearRect(0, 0, W * P, H * P);
          ctx.drawImage(word2, 0, 0);
          await sleep(hold);
          if (breakUp) for (let b = 0; b <= 100 && live; b += 5) { crumble(ctx, on, fill, b); await frame(); }
        }
        i++;
      }
    })();
    return () => { live = false; };
  }
  return { cycle, pixels };
})();

const Welcome = (() => {
  const KEY = "prdesk.welcome.v1";
  const HELLO = [["Welcome", "ENGLISH"], ["Bienvenue", "FRANÇAIS"], ["مرحبا بيك", "TOUNSI"], ["Willkommen", "DEUTSCH"],
    ["ようこそ", "日本語"], ["Добро пожаловать", "РУССКИЙ"], ["Bem-vindo", "PORTUGUÊS"], ["Bienvenido", "ESPAÑOL"]];
  let el = null, stop = null, stage = -1, credits = 0, done = null, report = null;
  const icon = (id, px) => (typeof ControlCenter !== "undefined" ? ControlCenter.appIcon(id, px) : "");
  const sprite = (name, scale, idle = "") => (typeof Sprites !== "undefined" ? Sprites.svg(name, { scale, idle }) : "");

  function due() {
    const q = new URLSearchParams(location.search);
    if (q.has("noguide") || q.has("panel") || q.has("nowelcome") || (typeof Layout !== "undefined" && Layout.isSolo())) return false;
    try { return !localStorage.getItem(KEY); } catch { return false; }
  }

  /* ---------------------------------------------------------------- the stages */
  function hostCard(id, name, line, pill, attrs = "") {
    return `<button class="wl-host" type="button" ${attrs}><span class="wl-host-icon">${icon(id, 4)}</span><b>${esc(name)}</b><small>${esc(line)}</small>${pill ? `<span class="wl-pill">${esc(pill)}</span>` : ""}</button>`;
  }
  const STAGES = [
    { title: "Meet your crew", sub: "Two of them work beside you.", html: () => {
      const cl = report?.claude;
      return `<div class="wl-crew">
        <div class="wl-crew-card"><p class="wl-bubble">I do PixelDeck's own work: merges, syncs, new shells, the clean-up. Every click, I'm the one running.</p>
          <span class="wl-crew-art">${sprite("bot", 7, "blink")}</span><h4>The bot, your agent</h4><p>Only what you ask, nothing more.</p></div>
        <div class="wl-crew-card"><p class="wl-bubble">I bring the AI: fixes, reviews and summaries, through the coding assistant you use.</p>
          <span class="wl-crew-art">${sprite("buddy", 6)}</span><h4>The blob, your AI helper</h4>
          <div class="wl-choices"><span class="wl-choice is-on">${icon("claude", 2)} Claude Code <small>${cl ? (cl.ok ? "FOUND" : "NOT FOUND") : ""}</small></span>
            <span class="wl-choice">${icon("gemini", 2)} Gemini CLI <small>SOON</small></span><span class="wl-choice">${icon("codex", 2)} Codex CLI <small>SOON</small></span></div></div>
      </div>`;
    } },
    { title: "Where does your code live?", sub: "Your pull requests and your team's, on Bitbucket, GitHub or GitLab. Pick one; you can switch in Settings, Setup.", html: () => {
      // The host in use says so; the others open their setup, where saving switches to them.
      const hosts = (state.features || []).includes("hosts");
      const now = state.host?.id || "bitbucket";
      const pill = (id) => (id === now ? (state.me ? "CONNECTED" : "SET IT UP NEXT") : "PICK IT");
      const card = (id, name, line) => (hosts || id === "bitbucket")
        ? hostCard(id, name, line, pill(id), `data-wl-host="${id}"`) : hostCard(id, name, line, "SOON", "disabled");
      return `<div class="wl-hosts">${card("bitbucket", "Bitbucket", "A cloud workspace and an API token")}
        ${card("github", "GitHub", "github.com or Enterprise")}${card("gitlab", "GitLab", "gitlab.com or your own server")}
        ${hostCard("git", "Any git", "Clones on this computer", report?.git?.ok ? "READY" : "", 'data-wl-app="git"')}</div>`;
    } },
    { title: "Bring your tools", sub: "All optional, all later is fine. A click opens the app's guide, with the link to the page you need.", html: () => {
      const sp = typeof Music !== "undefined" ? Music.status() : null;
      const jira = report?.jira;
      return `<div class="wl-hosts">${hostCard("jira", "Jira", "Ticket status when you merge", jira?.site && jira?.token_set ? "READY" : "SET UP", 'data-wl-app="jira"')}
        ${hostCard("spotify", "Spotify", "Music while you work", sp?.connected ? "READY" : "SET UP", 'data-wl-app="spotify"')}
        ${hostCard("claude", "Claude Code", "Fixes and reviews, in a terminal or a chat panel", report?.claude?.ok ? "READY" : "SET UP", 'data-wl-app="claude"')}
        ${hostCard("youtube", "YouTube", "Videos floating while you work", "SOON", "disabled")}</div>`;
    } },
    { title: "Your look", sub: "A theme and an accent. The bot stays blue; the blob takes the theme's colour. All of it is in Settings, Personalise.", html: () => {
      const p = Prefs.get();
      const theme = (THEMES.find(([id]) => id === p.theme) || THEMES[0])[0];
      return `<div class="themes wl-themes">${THEMES.map(([v, label, tone]) => `<button class="theme-card" type="button" data-wl-theme="${v}" aria-pressed="${theme === v}" data-theme-card="${v}">
          <span class="theme-shot" aria-hidden="true"><i></i><i></i></span><b>${esc(label)}</b><small>${tone.toUpperCase()}</small></button>`).join("")}</div>
        <div class="swatches wl-swatches">${ACCENTS.map(([v, label]) => `<button class="swatch-btn" type="button" data-wl-accent="${v}" aria-pressed="${p.accent === v}" data-swatch="${v}"><span aria-hidden="true"></span>${esc(label)}</button>`).join("")}</div>
        <div class="wl-look-crew" aria-hidden="true">${sprite("bot", 5, "blink")}${sprite("buddy", 5)}</div>`;
    } },
    { title: "Ready, player one", sub: "Your pull requests are loading. The bot and the blob are standing by.", html: () => `
      <div class="wl-ready" aria-hidden="true">${sprite("bot", 9, "blink")}${sprite("buddy", 7)}</div>
      <button class="wl-start" type="button" data-wl-finish>PRESS START</button>` },
  ];

  function showStage(i) {
    stage = Math.max(0, Math.min(STAGES.length - 1, i));
    const s = STAGES[stage];
    el.querySelector(".wl-progress").innerHTML = STAGES.map((_, k) => `<i class="${k <= stage ? "is-on" : ""}"></i>`).join("");
    el.querySelector(".wl-stage-host").innerHTML = `<div class="wl-stage">
      <p class="wl-stage-name">STAGE 1-${stage + 1}</p><h2 class="wl-stage-title" id="wl-title">${esc(s.title)}</h2><p class="wl-stage-sub">${esc(s.sub)}</p>
      ${s.html()}
      <div class="wl-foot">${stage > 0 ? '<button class="btn btn--quiet" type="button" data-wl-step="-1">Back</button>' : ""}
        ${stage < STAGES.length - 1 ? '<button class="btn btn--primary" type="button" data-wl-step="1">Next</button>' : ""}</div></div>`;
    fit();
    el.querySelector(stage < STAGES.length - 1 ? "[data-wl-step='1']" : "[data-wl-finish]")?.focus({ preventScroll: true });
    if (typeof RetroSelect !== "undefined") RetroSelect.enhanceAll?.(el);
  }

  // A stage taller or wider than the window is zoomed down to fit it, never cut or scrolled.
  function fit() {
    const host = el?.querySelector(".wl-setup"), st = el?.querySelector(".wl-stage");
    if (!host || !st || host.hidden) return;
    st.style.zoom = "";
    const cs = getComputedStyle(host);
    const h = host.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    const w = host.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const k = Math.min(1, h / st.scrollHeight, w / st.scrollWidth);
    if (k < 0.995) st.style.zoom = Math.max(0.45, k).toFixed(3);
  }
  let fitQueued = false;
  const onResize = () => { if (!fitQueued) { fitQueued = true; requestAnimationFrame(() => { fitQueued = false; fit(); }); } };

  /* ---------------------------------------------------------------- the coin */
  function insertCoin() {
    if (credits || !el) return;
    credits = 1;
    Sound.coin();
    el.querySelector(".wl-coin")?.classList.add("is-dropping");
    setTimeout(() => {
      if (!el) return;
      el.querySelector(".wl-credit").textContent = "CREDIT 01";
      flash();
      stop?.();
      el.querySelector(".wl-hello").hidden = true;
      el.querySelector(".wl-setup").hidden = false;
      showStage(0);
    }, matchMedia("(prefers-reduced-motion: reduce)").matches ? 60 : 820);
  }
  function flash() {
    const f = el?.querySelector(".wl-flash");
    if (!f) return;
    f.classList.remove("is-on");
    void f.offsetWidth;
    f.classList.add("is-on");
  }

  function finish(then) {
    if (!el) return;
    try { localStorage.setItem(KEY, "done"); } catch { /* storage blocked */ }
    stop?.();
    el.classList.add("is-leaving");
    const gone = el;
    el = null;
    setTimeout(() => gone.remove(), 360);
    removeEventListener("keydown", onKey, true);
    removeEventListener("resize", onResize);
    const resolve = done;
    done = null;
    // "app": an app's guide was asked for, so the caller leaves the next screen to it
    resolve?.(then ? "app" : "done");
    then?.();
  }

  function onKey(e) {
    if (!el) return;
    if (e.key === "Escape") { e.preventDefault(); return finish(); }
    if (!credits) {
      if (e.ctrlKey || e.metaKey || e.altKey || ["Shift", "Control", "Alt", "Meta", "Tab"].includes(e.key)) return;
      e.preventDefault();
      return insertCoin();
    }
    if (e.target.closest?.("input, textarea")) return;
    if (e.key === "ArrowRight") { e.preventDefault(); Sound.step(); showStage(stage + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); Sound.step(); showStage(stage - 1); }
  }

  /** Play it; the promise resolves when it is over (finished or skipped). */
  function play() {
    if (el) return Promise.resolve();
    credits = 0;
    stage = -1;
    el = document.createElement("div");
    el.className = "wl";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-label", "Welcome to PixelDeck");
    el.innerHTML = `<div class="wl-cabinet">
      <button class="wl-skip" type="button" data-wl-skip>SKIP</button>
      <span class="wl-credit num8">CREDIT 00</span>
      <span class="wl-flash" aria-hidden="true"></span>
      <div class="wl-scene wl-hello">
        <canvas class="wl-canvas" aria-hidden="true"></canvas>
        <p class="wl-lang num8" aria-live="off"></p>
        <h1 class="visually-hidden">Welcome to PixelDeck</h1>
        <div class="wl-coin-row">
          <button class="wl-insert" type="button" data-wl-coin>INSERT COIN</button>
          <button class="wl-slot" type="button" data-wl-coin aria-label="Insert a coin"><span class="wl-coin" aria-hidden="true">${coinSvg()}</span><small>OR PRESS ANY KEY</small></button>
        </div>
      </div>
      <div class="wl-scene wl-setup" hidden><div class="wl-progress" aria-hidden="true"></div><div class="wl-stage-host" aria-labelledby="wl-title"></div></div>
    </div>`;
    document.body.append(el);
    stop = PixelWords.cycle(el.querySelector(".wl-canvas"), HELLO.map(([w]) => w), { onWord: (i) => { const l = el?.querySelector(".wl-lang"); if (l) l.textContent = HELLO[i][1]; } });
    el.querySelector(".wl-insert").focus();
    addEventListener("keydown", onKey, true);
    addEventListener("resize", onResize);
    if ((state.features || []).includes("settings")) api("settings").then((r) => { report = r; if (el && stage >= 0) showStage(stage); }).catch(() => {});
    return new Promise((resolve) => { done = resolve; });
  }
  function coinSvg() {
    const rows = ["...yyyy...", ".yyYYYYyy.", ".yYyyyyYy.", "yYyyYYyyYy", "yYyYyyYyYy", "yYyYyyYyYy", "yYyyYYyyYy", ".yYyyyyYy.", ".yyYYYYyy.", "...yyyy..."];
    let r = "";
    rows.forEach((row, y) => [...row].forEach((c, x) => { if (c !== ".") r += `<rect x="${x}" y="${y}" width="1" height="1" fill="${c === "y" ? "#ffcf3a" : "#b98a12"}"/>`; }));
    return `<svg viewBox="0 0 10 10" width="34" height="34" shape-rendering="crispEdges">${r}</svg>`;
  }

  document.addEventListener("click", (e) => {
    if (!el || !el.contains(e.target)) return;
    const t = e.target;
    if (t.closest("[data-wl-coin]")) return insertCoin();
    if (t.closest("[data-wl-skip]")) { Sound.step(); return finish(); }
    const st = t.closest("[data-wl-step]");
    if (st) { Sound.step(); return showStage(stage + Number(st.dataset.wlStep)); }
    if (t.closest("[data-wl-finish]")) { Sound.start(); flash(); return setTimeout(() => finish(), 260); }
    const theme = t.closest("[data-wl-theme]");
    if (theme) { Prefs.set({ theme: theme.dataset.wlTheme }); Sound.key(2); return showStage(stage); }
    const accent = t.closest("[data-wl-accent]");
    if (accent) { Prefs.set({ accent: accent.dataset.wlAccent }); Sound.key(4); return showStage(stage); }
    const host = t.closest("[data-wl-host]");
    if (host) {
      const id = host.dataset.wlHost;
      // The host in use, set up: its card. Another one, or none yet: the setup, on that host.
      return finish(() => (id === (state.host?.id || "bitbucket") && !state.setupNeeded
        ? Settings.open("connections", { app: id }) : Settings.open("setup", { host: id })));
    }
    const app = t.closest("[data-wl-app]");
    if (app) {
      const id = app.dataset.wlApp;
      return finish(() => (id === "bitbucket" && state.setupNeeded ? Settings.open("setup") : Settings.open("connections", { app: id })));
    }
  });

  return { due, play, isOpen: () => !!el };
})();
