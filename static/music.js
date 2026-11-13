"use strict";

/* The Music panel: a pixel boombox for Spotify, its library, a mini player in the status bar
   (with a dropdown), and a floating mini player (music-float.js). All of them read one
   state and one poll loop, kept here. What Spotify says arrives trimmed by the server
   (spotify.py) and is only ever written with esc() or textContent. Album art comes through
   /api/spotify-art, because the page's CSP allows no other host, and is drawn in one of
   three pixel looks (8, 16 or 32-bit) chosen by clicking the art.
   The preview (music-demo.js) plays made-up songs through the same render path.
   Views are built once and then patched: a poll never restarts a marquee, moves focus or
   closes an open picker. */

const Music = (() => {
  const POLL_SHOWN = 4000, POLL_IDLE = 20000, POLL_HIDDEN = 60000;
  const STATUS_EVERY = 3000, SIGN_IN_WAIT = 180000, HOLD_MS = 1500, DEVICES_EVERY = 60000;
  const VOL_BLOCKS = 10, BLOCKS = 32, EQ_BARS = 14, DECK_ART = 256;
  const LIBRARY_SCOPES = ["user-library-read", "user-library-modify", "user-read-recently-played"];
  const LOOKS = { 8: { grid: 32, label: "8-BIT" }, 16: { grid: 64, label: "16-BIT" }, 32: { grid: 128, label: "32-BIT" } };
  const LOOK_KEY = "prdesk.music.look.v1";
  const reducedQuery = matchMedia("(prefers-reduced-motion: reduce)");
  const still = () => reducedQuery.matches || document.documentElement.dataset.motion === "calm";

  let root = null, mini = null, els = {}, miniEls = {};
  let status = null, statusFailed = "", statusSeen = "";
  let view = "", mode = "boot";          // mode: boot, setup, live or demo
  let demo = false;
  let player = null, readAt = 0, holdUntil = 0, pollError = "";
  let devices = [], devicesAt = 0;
  let signIn = null;                     // { url, opened, until, scopes } while a sign-in is out in the browser
  let pollTimer = 0, statusTimer = 0, revealTimer = 0, seekTimer = 0, volTimer = 0;
  let pendingSeek = null, pendingVol = null;
  let lastFilled = -1, paintedItem = null, endAsked = false, trackSeen = null;
  const views = new Set();               // other views of the same state: { paint, tick, visible, relook }
  const trackListeners = new Set();
  const listeners = new Set();
  const likes = new Map();               // track id -> liked, as far as this page knows
  let look = (() => { try { return LOOKS[localStorage.getItem(LOOK_KEY)] ? localStorage.getItem(LOOK_KEY) : "16"; } catch { return "16"; } })();

  /* ------------------------------------------------------------ pixels */

  function rects(rows, map) {
    let out = "";
    rows.forEach((row, y) => {
      let x = 0;
      while (x < row.length) {
        const ch = row[x];
        let end = x + 1;
        while (end < row.length && row[end] === ch) end++;
        if (map[ch]) out += `<rect class="${map[ch]}" x="${x}" y="${y}" width="${end - x}" height="1"/>`;
        x = end;
      }
    });
    return out;
  }
  const pixelSvg = (cls, w, h, scale, inner) =>
    `<svg class="${cls}" viewBox="0 0 ${w} ${h}" width="${w * scale}" height="${h * scale}" shape-rendering="crispEdges" aria-hidden="true">${inner}</svg>`;

  const GLYPH = {
    play: ["##.......", "####.....", "######...", "########.", "#########", "########.", "######...", "####.....", "##......."],
    pause: Array(9).fill("###...###"),
    next: ["#...#....##", "##..##...##", "###.###..##", "########.##", "###.###..##", "##..##...##", "#...#....##"],
    heart: [".##...##.", "####.####", "#########", "#########", ".#######.", "..#####..", "...###...", "....#...."],
    plus: ["...##...", "...##...", "...##...", "########", "########", "...##...", "...##...", "...##..."],
    list: ["##.######", "##.######", ".........", "##.######", "##.######", ".........", "##.######", "##.######"],
    pop: ["....#####", ".....####", "....#####", "...###.##", "#.###...#", "#..#.....", "#........", "#........", "######..."],
    dock: ["#########", "#.......#", "#.......#", "#########", "#########", "#########", "#########"],
    close: ["##....##", "###..###", ".######.", "..####..", "..####..", ".######.", "###..###", "##....##"],
    lock: ["..####..", ".##..##.", ".#....#.", ".#....#.", "########", "###..###", "###..###", "########"],
    back: ["...##", "..##.", ".##..", "##...", ".##..", "..##.", "...##"],
  };
  GLYPH.prev = GLYPH.next.map((r) => [...r].reverse().join(""));
  const glyph = (name, px = 3) => {
    const rows = GLYPH[name];
    return pixelSvg(`mu-glyph mu-glyph--${name}`, rows[0].length, rows.length, px, rects(rows, { "#": "mu-ink" }));
  };

  const SPK = { k: "mu-p-k", s: "mu-p-s", r: "mu-p-r", c: "mu-p-c", h: "mu-p-h", w: "mu-p-w" };
  function speaker(n, scale, cls) {
    const c = (n - 1) / 2, R = n / 2, ring = [], cone = [];
    for (let y = 0; y < n; y++) {
      let a = "", b = "";
      for (let x = 0; x < n; x++) {
        const d = Math.hypot(x - c, y - c);
        let r = ".", k = ".";
        if (d <= R - 0.25) {
          if (d > R - 1.5) r = "k";
          else if (d > R * 0.8) r = "s";
          else if (d > R * 0.8 - 1) r = "k";
          else if (d > R * 0.27) k = Math.floor(d) % 3 === 0 ? "r" : "c";
          else k = d > R * 0.12 ? "h" : "w";
        }
        a += r; b += k;
      }
      ring.push(a); cone.push(b);
    }
    return pixelSvg(cls, n, n, scale, `<g>${rects(ring, SPK)}</g><g class="mu-cone">${rects(cone, SPK)}</g>`);
  }

  const CASSETTE = [
    ".kkkkkkkkkkkkkkkkkkkk.", "kSSSSSSSSSSSSSSSSSSSSk", "kSttttttttttttttttttSk", "kSwwwwwwwwwwwwwwwwwwSk",
    "kSSSSSSSSSSSSSSSSSSSSk", "kSSkkkkSSSSSSSSkkkkSSk", "kSkwSSwkkkkkkkkwSSwkSk", "kSkwSSwkkkkkkkkwSSwkSk",
    "kSSkkkkSSSSSSSSkkkkSSk", "kSSSSSSSSSSSSSSSSSSSSk", "kSSSSSkkkkkkkkkkSSSSSk", "kSSSSkSSSSSSSSSSkSSSSk",
    "kSSSkSSSSSSSSSSSSkSSSk", ".kkkkkkkkkkkkkkkkkkkk.",
  ];
  const cassette = (scale) => pixelSvg("mu-cassette", 22, 14, scale, rects(CASSETTE, { k: "mu-p-k", S: "mu-p-s", w: "mu-p-w", t: "mu-p-t" }));

  /* ------------------------------------------------------------ the picture: sources, looks, frames */

  function canvas(size) {
    const c = document.createElement("canvas");
    c.width = c.height = size;
    return c;
  }
  // A song without a picture gets a striped tape label made from its own name.
  function plainCover(seed) {
    const c = canvas(64), g = c.getContext("2d"), rand = MusicDemo.rng(seed), hue = Math.floor(rand() * 360);
    g.fillStyle = `hsl(${hue} 30% 16%)`; g.fillRect(0, 0, 64, 64);
    for (let y = 12; y < 52; y += 8) { g.fillStyle = `hsl(${(hue + y * 5) % 360} 70% ${48 + (y % 16)}%)`; g.fillRect(0, y, 64, 4); }
    g.fillStyle = "#f4f1e6"; g.fillRect(8, 24, 48, 16);
    g.fillStyle = "#2a2a3a";
    for (let i = 0; i < 5; i++) g.fillRect(12 + i * 8, 30, 4, 4);
    return c;
  }
  // No signal: colour bars over a strip of noise, drawn at the size asked for.
  function noSignal(size) {
    const c = canvas(size), g = c.getContext("2d"), rand = MusicDemo.rng("no signal");
    const bars = ["#c8c8c8", "#c8c800", "#00c8c8", "#00c800", "#c800c8", "#c80000", "#0000c8"];
    const top = Math.round(size * 0.66), mid = Math.round(size * 0.75), u = size / 7;
    bars.forEach((color, i) => {
      g.fillStyle = color; g.fillRect(Math.round(i * u), 0, Math.ceil(u), top);
      g.fillStyle = bars[6 - i]; g.fillRect(Math.round(i * u), top, Math.ceil(u), mid - top);
    });
    for (let y = mid; y < size; y++) for (let x = 0; x < size; x++) { const v = 20 + Math.floor(rand() * 200); g.fillStyle = `rgb(${v},${v},${v})`; g.fillRect(x, y, 1, 1); }
    return c;
  }
  async function fetchArt(url) {
    const res = await fetch(`/api/spotify-art?url=${encodeURIComponent(url)}`, { headers: { "X-Desk-Token": TOKEN } });
    if (!res.ok) throw new Error(`art ${res.status}`);
    return createImageBitmap(await res.blob());
  }

  const sources = new Map(), frames = new Map();
  let inflight = 0;
  const waiting = [];
  // Real pictures come four at a time, so a long list never floods the server.
  function queued(task) {
    return new Promise((resolve, reject) => {
      const run = () => { inflight++; task().then(resolve, reject).finally(() => { inflight--; waiting.shift()?.(); }); };
      if (inflight < 4) run(); else waiting.push(run);
    });
  }
  function sourceOf(key) {
    if (sources.has(key)) return sources.get(key);
    const job = key.startsWith("demo:") ? Promise.resolve(MusicDemo.cover(key.slice(5)))
      : key.startsWith("plain:") ? Promise.resolve(plainCover(key))
        : queued(() => (mode === "demo" ? Promise.reject(new Error("no art in the preview")) : fetchArt(key)));
    sources.set(key, job);
    job.catch(() => sources.delete(key));
    while (sources.size > 300) sources.delete(sources.keys().next().value);
    return job;
  }
  // Smaller in halves first: one big step down drops detail unevenly in some browsers.
  function shrink(src, size) {
    let cur = src, w = src.width;
    while (w / 2 >= size * 1.5) {
      const half = canvas(Math.round(w / 2));
      const g = half.getContext("2d");
      g.imageSmoothingQuality = "high";
      g.drawImage(cur, 0, 0, half.width, half.height);
      cur = half; w = half.width;
    }
    const out = canvas(size), g = out.getContext("2d", { willReadFrequently: true });
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = "high";
    g.drawImage(cur, 0, 0, size, size);
    return out;
  }
  // 8-bit: 8 levels a channel and a strong ordered dither. 16-bit: RGB565 (32, 64 and 32
  // levels) and a light one. 32-bit: the colours as they are.
  const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];
  function quantize(c, lk) {
    if (lk === "32") return c;
    const g = c.getContext("2d", { willReadFrequently: true }), img = g.getImageData(0, 0, c.width, c.height), d = img.data, w = c.width;
    const levels = lk === "8" ? [7, 7, 7] : [31, 63, 31], spread = lk === "8" ? 30 : 0.5;
    for (let i = 0; i < d.length; i += 4) {
      const p = i >> 2, x = p % w, y = (p / w) | 0, t = BAYER[(y & 3) * 4 + (x & 3)] / 16 - 0.5;
      for (let k = 0; k < 3; k++) {
        const n = levels[k], step = 255 / n, v = d[i + k] + t * (lk === "8" ? spread : step * spread);
        d[i + k] = Math.max(0, Math.min(255, Math.round(Math.round(v / step) * step)));
      }
      d[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
    return c;
  }
  // The grid a view draws: the look's own size, never more than twice the space it has.
  const gridFor = (css, lk = look) => Math.max(8, Math.min(LOOKS[lk].grid, Math.round(css * 2)));
  async function frame(key, css, lk = look) {
    const size = gridFor(css, lk), id = `${key}|${lk}|${size}`;
    if (frames.has(id)) return frames.get(id);
    const out = key === "none" ? noSignal(size) : quantize(shrink(await sourceOf(key), size), lk);
    frames.set(id, out);
    while (frames.size > 500) frames.delete(frames.keys().next().value);
    return out;
  }
  /** Draw a picture into a canvas the view owns; css is its size on screen. */
  async function drawArt(c, key, css, { reveal = false } = {}) {
    if (!c) return;
    c.dataset.art = key || "";
    let out;
    try { out = await frame(key || "none", css); } catch { out = await frame(`plain:${key}`, css); }
    if (c.dataset.art !== (key || "")) return;  // a newer picture was asked for meanwhile
    if (c.width !== out.width) { c.width = c.height = out.width; }
    c.style.imageRendering = out.width > css ? "auto" : "pixelated";
    const g = c.getContext("2d");
    if (!reveal || still() || !c.isConnected || !c.getClientRects().length) { clearInterval(c._reveal); g.drawImage(out, 0, 0); return; }
    // the new picture rolls in from the top, an eighth at a time, like a set changing channel
    clearInterval(c._reveal);
    let row = 0;
    const step = Math.max(1, out.width / 8);
    c._reveal = setInterval(() => {
      row = Math.min(out.width, row + step);
      g.drawImage(out, 0, 0, out.width, row, 0, 0, out.width, row);
      if (row < out.width) { g.fillStyle = "rgba(210, 255, 220, 0.7)"; g.fillRect(0, row, out.width, Math.max(1, step / 4)); } else clearInterval(c._reveal);
    }, 34);
  }
  const artKey = (item) => (item ? item.art || `plain:${item.id || item.name}` : "none");
  function setLook(next) {
    if (!LOOKS[next] || next === look) return;
    look = next;
    try { localStorage.setItem(LOOK_KEY, next); } catch { /* storage blocked: the look lasts this visit */ }
    if (els.look) els.look.textContent = LOOKS[look].label;
    if (els.art) { els.art.dataset.art = ""; drawArt(els.art, artKey(player?.item), artCss()); }
    if (miniEls.art) drawArt(miniEls.art, artKey(player?.item), 18);
    for (const v of views) v.relook?.();
  }
  const cycleLook = () => setLook({ 8: "16", 16: "32", 32: "8" }[look]);
  const artCss = () => (els.art?.clientWidth || DECK_ART);

  /* ------------------------------------------------------------ sources */

  const q = (params) => Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
  const Live = {
    now: () => api("spotify-now"),
    control: (action, value, device) => api("spotify-control", { method: "POST", body: { action, value, ...(device ? { device_id: device } : {}) } }),
    devices: async () => (await api("spotify-devices")).devices || [],
    transfer: (id, play) => api("spotify-transfer", { method: "POST", body: { device_id: id, play } }),
    playlists: (offset = 0) => api(`spotify-playlists?${q({ offset })}`),
    playlist: (id, offset = 0) => api(`spotify-playlist?${q({ id, offset })}`),
    liked: (offset = 0) => api(`spotify-liked?${q({ offset })}`),
    albums: (offset = 0) => api(`spotify-albums?${q({ offset })}`),
    album: (id, offset = 0) => api(`spotify-album?${q({ id, offset })}`),
    recent: (before) => api(`spotify-recent?${q({ before })}`),
    search: (text, type = "all", offset = 0) => api(`spotify-search?${q({ q: text, type, offset })}`),
    artist: (id, offset = 0) => api(`spotify-artist?${q({ id, offset })}`),
    queue: () => api("spotify-queue"),
    queueAdd: (uri) => api("spotify-queue-add", { method: "POST", body: { uri } }),
    likedCheck: (ids) => api(`spotify-liked-check?${q({ ids: ids.join(",") })}`),
    like: (ids, on) => api("spotify-like", { method: "POST", body: { ids, liked: on } }),
    play: (opts) => api("spotify-play", { method: "POST", body: opts }),
  };
  const source = () => (mode === "demo" ? MusicDemo.source : Live);

  /* ------------------------------------------------------------ helpers */

  function fmt(ms) {
    const total = Math.max(0, Math.floor((ms || 0) / 1000)), h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), sec = total % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  }
  const shown = () => !!root && root.isConnected && root.getClientRects().length > 0;
  const miniShown = () => !!mini && !mini.hidden && mini.getClientRects().length > 0;
  const anyShown = () => shown() || miniShown() || [...views].some((v) => v.visible?.());
  const playing = () => !!player?.is_playing && !!player?.item;
  const can = (what) => !(player?.disallows || []).includes(what);
  const itemKey = () => player?.item?.id ?? player?.item?.name ?? null;
  function position() {
    if (!player?.item) return 0;
    const run = player.is_playing ? performance.now() - readAt : 0;
    return Math.min(player.item.duration_ms, player.progress_ms + run);
  }
  const missing = (scope) => mode === "live" && (status?.scopes_missing || []).includes(scope);
  const libraryLocked = () => mode === "live" && LIBRARY_SCOPES.some((s) => (status?.scopes_missing || []).includes(s));

  /* ------------------------------------------------------------ views */

  function bootHtml() {
    return `<div class="mu" data-view="boot"><div class="mu-boot">${cassette(4)}<p class="num8">${statusFailed ? "NO SIGNAL" : "TUNING IN"}</p>
      ${statusFailed ? `<p class="mu-hint">${esc(statusFailed)}</p><p><button class="btn" type="button" data-music="retry">Try again</button></p>` : ""}</div></div>`;
  }

  function setupHtml() {
    const s = status || {};
    const uri = s.redirect_uri || `http://127.0.0.1:${location.port}/spotify/callback`;
    const step = (n, done, title, body) => `<li class="mu-step${done ? " is-done" : ""}"><span class="mu-step-n num8" aria-hidden="true">${done ? "OK" : n}</span>
      <div class="mu-step-body"><h4>${title}${done ? '<span class="visually-hidden"> (done)</span>' : ""}</h4>${body}</div></li>`;
    const copy = (value, label = "Copy") => `<button class="btn btn--quiet mu-copy-btn" type="button" data-music="copy" data-copy="${esc(value)}">${label}</button>`;
    return `<div class="mu" data-view="setup"><section class="mu-setup" aria-labelledby="mu-setup-title">
      <header class="mu-setup-head"><span class="mu-setup-art">${cassette(5)}</span>
        <div><p class="mu-kicker num8">MUSIC</p><h2 id="mu-setup-title">Put your Spotify on the deck</h2>
        <p class="mu-lead">PixelDeck shows what plays and controls it through Spotify's Web API. Four steps, once.</p></div></header>
      <ol class="mu-steps">
        ${step(1, s.configured, "Create a Spotify app", `<p>In Spotify's developer dashboard, create an app, any name, and tick <b>Web API</b>.</p>
          <p><button class="btn" type="button" data-external="https://developer.spotify.com/dashboard">Open the Spotify dashboard</button></p>`)}
        ${step(2, false, "Add this redirect URI", `<p>In the app's settings, paste it under Redirect URIs exactly as written, then save.</p>
          <p class="mu-code"><code>${esc(uri)}</code>${copy(uri)}</p>
          <p class="mu-hint">Spotify takes plain http only for 127.0.0.1, so it is not localhost. It follows the port PixelDeck runs on.</p>`)}
        ${step(3, s.configured && s.client_id_ok, "Give PixelDeck the Client ID", `<p>Copy the app's Client ID into PixelDeck's settings file. No client secret, and no restart.</p>
          <p class="mu-code"><code>SPOTIFY_CLIENT_ID=</code>${copy("SPOTIFY_CLIENT_ID=")}</p>
          ${s.env_file ? `<p class="mu-hint">The settings file: <code>${esc(s.env_file)}</code></p>` : ""}
          ${s.configured && !s.client_id_ok ? '<p class="mu-warn">That Client ID does not look right: it is the letters and digits on the app\'s page.</p>' : ""}`)}
        ${step(4, false, "Connect", `<p>Sign in with Spotify in your browser and allow PixelDeck.</p>
          <p class="mu-actions"><button class="btn btn--primary" type="button" data-music="connect"${s.configured && s.client_id_ok ? "" : " disabled"}>Connect Spotify</button>
          <button class="btn btn--quiet" type="button" data-music="retry">Check again</button></p>${waitingHtml()}`)}
      </ol>
      <p class="mu-premium"><span class="mu-premium-tag num8">PREMIUM</span><span>Spotify Premium is needed for the app's owner, and for play, pause, skip, seek and volume.
        While the app is in development mode it works for its owner and up to 5 people added under User Management.</span></p>
      <footer class="mu-setup-foot"><span>Just looking?</span><button class="btn" type="button" data-music="preview">Preview with made-up music</button></footer>
    </section></div>`;
  }
  function waitingHtml() {
    if (!signIn) return "";
    return `<div class="mu-wait" role="status"><span class="mu-wait-led" aria-hidden="true"></span>
      <span>${signIn.opened ? "Spotify's sign-in page is open in your browser. Sign in there; this page notices by itself." : "PixelDeck could not open a browser here. Copy the sign-in link into one:"}</span>
      <button class="btn btn--quiet mu-copy-btn" type="button" data-music="copy" data-copy="${esc(signIn.url)}">Copy the sign-in link</button></div>`;
  }

  function deckHtml() {
    const eqLevels = [37.5, 25, 50, 12.5, 37.5, 62.5, 25, 50, 37.5, 62.5, 50, 75, 62.5, 75];
    const eq = Array.from({ length: EQ_BARS }, (_, i) =>
      `<i class="mu-eq-${"abc"[i % 3]}" style="--d:${440 + ((i * 7) % 5) * 90}ms;--o:-${(i * 173) % 700}ms;--rest:${eqLevels[i]}%"></i>`).join("");
    const blocks = "<i></i>".repeat(BLOCKS);
    const vol = Array.from({ length: VOL_BLOCKS }, (_, i) => `<i style="--h:${28 + i * 8}%"></i>`).join("");
    const woofer = speaker(26, 4, "mu-woofer"), tweeter = speaker(10, 4, "mu-tweeter");
    const mq = (ref, cls) => `<div class="mu-mq ${cls}" data-ref="${ref}"><span class="mu-mq-track"><span class="mu-mq-text"></span><span class="mu-mq-text" aria-hidden="true"></span></span></div>`;
    return `<div class="mu" data-view="deck">
      <div class="mu-top"><span class="mu-kicker num8">MUSIC</span><span class="mu-from" data-ref="from"></span>
        <span class="mu-top-tools"><button class="btn btn--quiet mu-tool" type="button" data-music="popout" data-tip="Float a mini player over the layout">${glyph("pop", 2)}<span>Pop out</span></button></span></div>
      <div class="mu-ribbon" data-ref="ribbon" hidden></div>
      <div class="mu-stage">
        <div class="mu-crew" aria-hidden="true"><span class="mu-slime">${Sprites.svg("buddy", { scale: 4 })}</span><span class="mu-bot">${Sprites.svg("bot", { scale: 4 })}</span></div>
        <div class="mu-box">
          <div class="mu-handle" aria-hidden="true"></div>
          <div class="mu-brand" aria-hidden="true"><span class="mu-power" data-ref="power"></span><b>PIXELDECK</b><small>PD-808 STEREO CASSETTE</small></div>
          <div class="mu-face">
            <div class="mu-spk" aria-hidden="true">${tweeter}${woofer}</div>
            <div class="mu-crt"><button class="mu-screen" type="button" data-music="look" data-ref="screen" aria-label="Album art. Click to change the pixel look">
                <canvas class="mu-art" data-ref="art" width="64" height="64"></canvas></button>
              <span class="mu-look num8" data-ref="look" aria-hidden="true">${LOOKS[look].label}</span><span class="mu-crt-knobs" aria-hidden="true"><i></i><i></i></span></div>
            <div class="mu-lcd" data-ref="lcd">
              <div class="mu-lcd-row"><span class="mu-mode" data-ref="mode">STOP</span><span class="mu-dev" data-ref="dev"></span></div>
              ${mq("title", "mu-mq--title")}${mq("sub", "mu-mq--sub")}${mq("album", "mu-mq--album")}
              <div class="mu-lcd-row mu-times"><span class="num8 mu-pos" data-ref="pos">0:00</span><span class="num8 mu-dur" data-ref="dur">0:00</span></div>
              <div class="mu-seek" data-ref="seek" data-music="seek" role="slider" tabindex="0" aria-label="Position in the song" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0">${blocks}</div>
              <div class="mu-eq" data-ref="eq" aria-hidden="true">${eq}</div>
            </div>
            <div class="mu-spk" aria-hidden="true">${tweeter}${woofer}</div>
          </div>
          <div class="mu-keys">
            <div class="mu-transport" role="group" aria-label="Playback">
              <button class="mu-key" type="button" data-music="previous" data-ref="prev" aria-label="Previous">${glyph("prev")}<span>PREV</span></button>
              <button class="mu-key mu-key--play" type="button" data-music="toggle" data-ref="play" aria-label="Play">${glyph("play")}${glyph("pause")}<span data-ref="playLabel">PLAY</span></button>
              <button class="mu-key" type="button" data-music="next" data-ref="next" aria-label="Next">${glyph("next")}<span>NEXT</span></button>
            </div>
            <div class="mu-toggles" role="group" aria-label="Order">
              <button class="mu-key mu-key--toggle mu-key--like" type="button" data-music="like" data-ref="like" aria-pressed="false" aria-label="Like this song">${glyph("heart", 2)}<span>LIKE</span></button>
              <button class="mu-key mu-key--toggle" type="button" data-music="shuffle" data-ref="shuffle" aria-pressed="false"><i class="mu-led" aria-hidden="true"></i><span>SHUF</span></button>
              <button class="mu-key mu-key--toggle" type="button" data-music="repeat" data-ref="repeat" aria-pressed="false"><i class="mu-led" aria-hidden="true"></i><span data-ref="repeatLabel">RPT</span></button>
            </div>
            <div class="mu-vol"><span class="mu-vol-label num8" aria-hidden="true">VOL</span>
              <div class="mu-vol-blocks" data-ref="vol" data-music="volume" role="slider" tabindex="0" aria-label="Volume" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">${vol}</div>
              <span class="num8 mu-vol-num" data-ref="volnum" aria-hidden="true">0</span></div>
            <label class="mu-device"><span class="mu-device-label num8">PLAY ON</span><span class="visually-hidden">Play on</span><select data-retro data-ref="device"></select></label>
          </div>
        </div>
      </div>
      <p class="mu-note" data-ref="note" role="status" hidden></p>
      <div class="mu-lib" data-ref="lib"></div>
    </div>`;
  }

  function build() {
    const next = mode === "boot" ? "boot" : mode === "setup" ? "setup" : "deck";
    if (!root) return;
    if (next === "deck" && view === "deck") { paintAll(); return; }
    view = next;
    root.innerHTML = next === "boot" ? bootHtml() : next === "setup" ? setupHtml() : deckHtml();
    els = {};
    for (const el of root.querySelectorAll("[data-ref]")) els[el.dataset.ref] = el;
    if (next === "deck") {
      lastFilled = -1;
      paintedItem = undefined;
      RetroSelect.enhanceAll(root);
      if (typeof MusicLibrary !== "undefined") MusicLibrary.mount(els.lib, core);
      paintAll();
    }
  }

  /* ------------------------------------------------------------ painting */

  function marquee(el, text) {
    if (!el) return;
    if (el._text !== text) {
      el._text = text;
      for (const span of el.querySelectorAll(".mu-mq-text")) span.textContent = text;
    }
    fit(el);
  }
  function fit(el) {
    const track = el?.firstElementChild, first = track?.firstElementChild;
    if (!first) return;
    const box = el.clientWidth, width = first.getBoundingClientRect().width;
    const run = box > 0 && width > box + 1 && !still();
    if (run) {
      const dist = Math.round(width + 48);
      if (track.style.getPropertyValue("--mq-dist") !== `${dist}px`) {
        track.style.setProperty("--mq-dist", `${dist}px`);
        track.style.setProperty("--mq-steps", String(Math.max(1, Math.round(dist / 2))));
        track.style.setProperty("--mq-dur", `${(dist / 30).toFixed(2)}s`);
      }
    }
    el.classList.toggle("is-running", run);
  }
  function refit() {
    for (const el of [els.title, els.sub, els.album, miniEls.mq]) fit(el);
    for (const v of views) v.refit?.();
  }

  function ribbon() {
    if (!els.ribbon) return;
    let html = "";
    if (mode === "demo") {
      html = `<span class="mu-ribbon-tag num8">PREVIEW</span><span>Made-up songs and artists, drawn here. Nothing plays and nothing reaches Spotify.</span>
        <span class="mu-ribbon-tools">${status?.connected ? "" : status?.configured && status?.client_id_ok ? '<button class="btn btn--primary" type="button" data-music="connect">Connect Spotify</button>' : '<button class="btn" type="button" data-music="leave">Set up Spotify</button>'}
        <button class="btn btn--quiet" type="button" data-music="leave">${status?.connected ? "Back to my Spotify" : "Leave the preview"}</button></span>`;
    } else if (libraryLocked()) {
      html = `<span class="mu-ribbon-tag num8">LIBRARY</span><span>Reconnect Spotify to unlock Liked Songs, saved albums and Recently played: they need permissions PixelDeck did not ask for when you first connected.</span>
        <span class="mu-ribbon-tools"><button class="btn btn--primary" type="button" data-music="connect">Reconnect Spotify</button></span>`;
    } else if (status?.user && !status.user.premium) {
      html = `<span class="mu-ribbon-tag num8">FREE</span><span>Spotify Free: the deck shows what plays. Play, pause, skip, seek and volume need Premium.</span>`;
    }
    if (els.ribbon._html !== html) { els.ribbon.innerHTML = html; els.ribbon._html = html; }
    els.ribbon.hidden = !html;
  }

  function paintAll() {
    ribbon();
    paint();
    paintDevices();
    for (const v of views) v.paintAll?.();
  }

  function fromText(p) {
    const ctx = p?.context;
    if (!ctx) return "";
    return { playlist: "FROM A PLAYLIST", album: "FROM AN ALBUM", artist: "FROM AN ARTIST" }[ctx.type] || "";
  }

  function paint() {
    paintMini();
    const item = player?.item;
    const key = item?.id ?? item?.name ?? null;
    if (key !== trackSeen) { trackSeen = key; for (const fn of trackListeners) fn(item); if (item?.id) checkLikes([item.id]); }
    for (const v of views) v.paint?.();
    if (view !== "deck" || !els.lcd) return;
    const p = player, on = playing();
    paintedItem = key;
    root.firstElementChild.classList.toggle("is-playing", on);
    root.firstElementChild.classList.toggle("is-idle", !item);
    els.mode.textContent = !p ? "TUNING" : !item ? "NO TAPE" : on ? "PLAY" : "PAUSE";
    els.mode.dataset.state = !item ? "stop" : on ? "play" : "pause";
    els.dev.textContent = p?.device?.name ? p.device.name.toUpperCase() : "";
    els.from.textContent = fromText(p);
    marquee(els.title, item ? item.name : p ? "Nothing is playing" : "Tuning in");
    marquee(els.sub, item ? item.artists.join(", ") : p ? "Start Spotify on a phone or computer," : "");
    marquee(els.album, item ? (item.album ? `${item.kind === "episode" ? "SHOW" : "FROM"} ${item.album}` : "") : p ? "or pick something in the library below." : "");
    els.play.classList.toggle("is-playing", on);
    els.play.setAttribute("aria-label", on ? "Pause" : "Play");
    els.playLabel.textContent = on ? "PAUSE" : "PLAY";
    els.play.disabled = !p || (on ? !can("pausing") : (!item && !els.device?.value) || !can("resuming"));
    els.prev.disabled = !item || !can("skipping_prev");
    els.next.disabled = !item || !can("skipping_next");
    const liked = item?.id ? likes.get(item.id) : undefined;
    els.like.setAttribute("aria-pressed", String(liked === true));
    els.like.disabled = !item?.id || item.kind === "episode";
    els.like.setAttribute("aria-label", liked ? "Liked: click to remove from Liked Songs" : "Like this song");
    els.shuffle.setAttribute("aria-pressed", String(!!p?.shuffle));
    els.shuffle.disabled = !item || !can("toggling_shuffle");
    const rep = p?.repeat || "off";
    els.repeat.setAttribute("aria-pressed", String(rep !== "off"));
    els.repeat.dataset.mode = rep;
    els.repeatLabel.textContent = rep === "track" ? "RPT 1" : "RPT";
    els.repeat.setAttribute("aria-label", rep === "track" ? "Repeat: this song" : rep === "context" ? "Repeat: the whole list" : "Repeat: off");
    els.repeat.disabled = !item;
    const vol = p?.device?.volume;
    const lit = vol == null ? 0 : Math.round(vol / 10);
    [...els.vol.children].forEach((b, i) => { b.classList.toggle("is-on", i < lit); b.classList.toggle("is-hot", i < lit && i >= 7); });
    els.vol.setAttribute("aria-valuenow", String(vol ?? 0));
    els.vol.setAttribute("aria-disabled", String(vol == null || p?.device?.supports_volume === false));
    els.volnum.textContent = vol == null ? "--" : String(vol);
    els.power.classList.toggle("is-on", on);
    els.note.textContent = pollError;
    els.note.hidden = !pollError;
    els.look.textContent = LOOKS[look].label;
    els.screen.setAttribute("aria-label", `Album art, ${LOOKS[look].label.toLowerCase()} look. Click to change the pixel look`);
    const want = artKey(p ? item : null);
    if (els.art.dataset.art !== want) drawArt(els.art, want, artCss(), { reveal: true });
    tick();
  }

  function tick() {
    if (view === "deck" && els.lcd) {
      const dur = player?.item?.duration_ms || 0, pos = position();
      els.pos.textContent = fmt(pos);
      els.dur.textContent = fmt(dur);
      const filled = dur ? Math.min(BLOCKS, Math.floor((pos / dur) * BLOCKS + 0.0001)) : 0;
      if (filled !== lastFilled) {
        lastFilled = filled;
        [...els.seek.children].forEach((b, i) => { b.classList.toggle("is-on", i < filled); b.classList.toggle("is-head", i === filled && !!dur); });
      }
      els.seek.setAttribute("aria-valuemax", String(Math.round(dur / 1000)));
      els.seek.setAttribute("aria-valuenow", String(Math.round(pos / 1000)));
      els.seek.setAttribute("aria-valuetext", `${fmt(pos)} of ${fmt(dur)}`);
      els.seek.setAttribute("aria-disabled", String(!dur || !can("seeking")));
    }
    for (const v of views) v.tick?.();
    if (mode === "live" && playing() && position() >= player.item.duration_ms && !endAsked) {
      endAsked = true;  // the song is over here: ask what plays next a moment after Spotify moved on
      schedule(800);
    }
  }

  function paintMini() {
    if (!mini) return;
    const on = mode === "live" || mode === "demo";
    mini.hidden = !on;
    if (!on) return;
    if (!miniEls.play) {
      mini.innerHTML = `<button class="mm-play" type="button" data-music="toggle" aria-label="Play">${glyph("play", 2)}${glyph("pause", 2)}</button>
        <button class="mm-title" type="button" data-music="drop" aria-haspopup="dialog" aria-expanded="false"><canvas class="mm-art" width="18" height="18" aria-hidden="true"></canvas>
        <span class="mu-mq mm-mq"><span class="mu-mq-track"><span class="mu-mq-text"></span><span class="mu-mq-text" aria-hidden="true"></span></span></span>
        <span class="mm-eq" aria-hidden="true"><i></i><i></i><i></i></span></button>`;
      miniEls = { play: mini.querySelector(".mm-play"), title: mini.querySelector(".mm-title"), art: mini.querySelector(".mm-art"), mq: mini.querySelector(".mm-mq") };
    }
    const item = player?.item, isOn = playing();
    mini.classList.toggle("is-playing", isOn);
    miniEls.play.classList.toggle("is-playing", isOn);
    miniEls.play.setAttribute("aria-label", isOn ? "Pause" : "Play");
    miniEls.play.disabled = !item;
    marquee(miniEls.mq, item ? `${item.name} · ${item.artists.join(", ")}` : player ? "Nothing playing" : "Music");
    miniEls.title.setAttribute("aria-label", item ? `Now playing: ${item.name} by ${item.artists.join(", ")}. Open the music dropdown.` : "Open the music dropdown");
    const want = artKey(player ? item : null);
    if (miniEls.art.dataset.art !== want) drawArt(miniEls.art, want, 18);
  }

  function paintDevices() {
    const sel = els.device;
    if (!sel) return;
    const list = [...devices];
    const cur = player?.device;
    if (cur?.id && !list.some((d) => d.id === cur.id)) list.unshift({ ...cur });
    const html = list.length ? list.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("")
      : '<option value="">No device: open Spotify somewhere</option>';
    if (sel._html !== html) { sel.innerHTML = html; sel._html = html; }
    const want = cur?.id && list.some((d) => d.id === cur.id) ? cur.id : (list.find((d) => d.active)?.id || sel.value);
    if (want && sel.value !== want) sel.value = want;
    RetroSelect.sync(sel);
  }

  /* ------------------------------------------------------------ status and polling */

  function notify() { for (const fn of listeners) { try { fn(status); } catch { /* a listener's own trouble */ } } }

  function decide() {
    const was = mode;
    mode = demo ? "demo" : status?.connected ? "live" : status ? "setup" : "boot";
    const deck = mode === "live" || mode === "demo";
    if (mode !== was && deck) {
      player = null; devices = []; devicesAt = 0; pollError = ""; likes.clear(); trackSeen = undefined;
      if (mode === "demo") MusicDemo.source.reset();
    }
    if (!deck) { clearTimeout(pollTimer); player = null; }
    if (mode !== was || !deck) build(); else paintAll();
    paintMini();
    for (const v of views) v.mode?.(mode, was);
    if (mode !== was && deck) { poll(); loadDevices(true); }
  }

  async function refreshStatus() {
    const before = status;
    try {
      status = await api("spotify-status");
      statusFailed = "";
    } catch (err) {
      if (err.message === "reloading") return status;
      statusFailed = err.status === 404 ? "This PixelDeck server has no music player yet: restart it once." : err.message;
      if (!status) { decide(); notify(); return status; }
    }
    if (status?.connected && before && !before.connected) {
      signIn = null;
      clearTimeout(statusTimer);
      toast("Spotify connected.", status.user ? `Signed in as ${status.user.name}.` : "");
    } else if (status?.connected && before?.connected && (before.scopes_missing || []).length && !(status.scopes_missing || []).length) {
      signIn = null;
      clearTimeout(statusTimer);
      toast("Library unlocked.", "Liked Songs, saved albums and Recently played are in the Music panel now.");
    }
    // Nothing new: leave the page alone, or the setup card and the Settings dialog would be
    // rebuilt (and scrolled back up) every few seconds while a sign-in is out in the browser.
    const seen = `${JSON.stringify(status)}|${statusFailed}`;
    if (seen === statusSeen && mode !== "boot") return status;
    statusSeen = seen;
    decide();
    notify();
    return status;
  }

  function watchSignIn() {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(async function again() {
      await refreshStatus();
      const done = status?.connected && !(signIn?.scopes && (status.scopes_missing || []).length);
      if (done || !signIn) { signIn = null; return; }
      if (Date.now() < signIn.until) statusTimer = setTimeout(again, STATUS_EVERY);
      else { signIn = null; if (mode === "setup") build(); }
    }, STATUS_EVERY);
  }

  function cadence() {
    if (document.hidden) return POLL_HIDDEN;
    return anyShown() ? POLL_SHOWN : POLL_IDLE;
  }
  function schedule(ms) {
    clearTimeout(pollTimer);
    if (mode === "live" || mode === "demo") pollTimer = setTimeout(poll, ms);
  }
  async function poll() {
    clearTimeout(pollTimer);
    const asked = mode;
    if (asked !== "live" && asked !== "demo") return;
    try {
      const next = await source().now();
      if (mode !== asked) return;
      if (next?.connected === false) { await refreshStatus(); return; }
      pollError = "";
      if (performance.now() >= holdUntil) accept(next);
    } catch (err) {
      if (err.message === "reloading" || mode !== asked) return;
      if (err.status === 403 && !err.data?.upstream) { await refreshStatus(); return; }
      pollError = err.message;
    }
    paint();
    schedule(cadence());
  }
  function accept(next) {
    player = next;
    readAt = performance.now();
    endAsked = false;
  }

  function loadDevices(force = false) {
    if (mode !== "live" && mode !== "demo") return;
    const asked = mode, now = Date.now();
    if (!force && now - devicesAt < DEVICES_EVERY) return;
    devicesAt = now;
    source().devices().then((d) => { if (mode === asked) { devices = d; paintDevices(); paint(); } }).catch(() => {});
  }

  /* ------------------------------------------------------------ likes */

  let likeBatch = new Set(), likeTimer = 0;
  // Ask which songs are liked, at most 50 at a time, a moment after the lists stop asking.
  function checkLikes(ids) {
    if ((mode !== "live" && mode !== "demo") || missing("user-library-read")) return;
    for (const id of ids) if (id && !likes.has(id)) likeBatch.add(id);
    if (!likeBatch.size) return;
    clearTimeout(likeTimer);
    likeTimer = setTimeout(async () => {
      const batch = [...likeBatch].slice(0, 50);
      batch.forEach((id) => likeBatch.delete(id));
      try {
        const out = await source().likedCheck(batch);
        if (out.locked) return;
        for (const [id, on] of Object.entries(out.liked || {})) likes.set(id, on);
        paint();
        for (const v of views) v.likes?.();
      } catch { /* the hearts stay hollow */ }
      if (likeBatch.size) checkLikes([]);
    }, 120);
  }
  async function like(ids, on) {
    const before = ids.map((id) => [id, likes.get(id)]);
    ids.forEach((id) => likes.set(id, on));
    paint();
    for (const v of views) v.likes?.();
    try {
      const out = await source().like(ids, on);
      if (out.locked) throw Object.assign(new Error(out.message), { locked: true });
      return true;
    } catch (err) {
      before.forEach(([id, was]) => (was === undefined ? likes.delete(id) : likes.set(id, was)));
      paint();
      for (const v of views) v.likes?.();
      if (err.message === "reloading") return false;
      if (err.locked) toast("Reconnect Spotify to like songs.", err.message, true);
      else toast("Spotify did not change Liked Songs.", err.message, true);
      return false;
    }
  }

  /* ------------------------------------------------------------ actions */

  const deviceChoice = () => (player?.active ? undefined : els.device?.value || undefined);

  // Another key goes after a seek or volume still waiting to be sent, so that one goes first.
  // A skip drops a waiting seek: it was meant for the song being left.
  async function flushPending(action) {
    clearTimeout(seekTimer);
    clearTimeout(volTimer);
    const seek = action === "seek" || action === "next" || action === "previous" ? null : pendingSeek;
    const vol = action === "volume" ? null : pendingVol;
    pendingSeek = pendingVol = null;
    try {
      if (seek && seek.item === itemKey()) await source().control("seek", seek.value);
      if (vol) await source().control("volume", vol.value);
    } catch { /* the action that follows reports what Spotify refuses */ }
  }

  async function act(action, value) {
    if (mode !== "live" && mode !== "demo") return;
    await flushPending(action);
    const before = player ? JSON.parse(JSON.stringify(player)) : null;
    const pos = position();
    let command = action;
    if (player) {
      player.progress_ms = pos;
      readAt = performance.now();
      if (action === "toggle") { command = player.is_playing ? "pause" : "play"; player.is_playing = command === "play"; }
      else if (action === "seek") player.progress_ms = value;
      else if (action === "volume" && player.device) player.device.volume = value;
      else if (action === "shuffle") player.shuffle = value;
      else if (action === "repeat") player.repeat = value;
    } else if (action === "toggle") command = "play";
    holdUntil = performance.now() + HOLD_MS;
    endAsked = false;
    paint();
    try {
      await source().control(command, value, command === "play" ? deviceChoice() : undefined);
      if (mode === "demo") { holdUntil = 0; await poll(); return; }
      // Spotify's own state trails a skip by a moment; asking at once would show the old song.
      if (action === "next" || action === "previous") { holdUntil = 0; schedule(700); return; }
      schedule(HOLD_MS + 200);
    } catch (err) {
      if (err.message === "reloading") return;
      player = before;
      readAt = performance.now();
      holdUntil = 0;
      paint();
      toast("Spotify did not do that.", err.message, true);
      if (err.status === 403 && !err.data?.upstream) refreshStatus();
    }
  }

  /** Play a playlist, album or artist, or a list of songs, from one song in it. */
  async function playFrom(opts) {
    try {
      const device = deviceChoice();
      await source().play({ ...opts, ...(device ? { device_id: device } : {}) });
      holdUntil = 0;
      setTimeout(poll, mode === "demo" ? 0 : 700);
      return true;
    } catch (err) {
      if (err.message !== "reloading") toast("Spotify did not play that.", err.message, true);
      return false;
    }
  }
  async function queueAdd(uri, name) {
    try {
      await source().queueAdd(uri);
      toast("Added to the queue.", name || "");
      for (const v of views) v.queued?.();
      return true;
    } catch (err) {
      if (err.message !== "reloading") toast("Spotify did not queue that.", err.message, true);
      return false;
    }
  }

  async function transfer(id) {
    if (!id || id === player?.device?.id) return;
    try {
      await source().transfer(id, playing());
      devicesAt = 0;
      setTimeout(() => { poll(); loadDevices(); }, mode === "demo" ? 0 : 900);
    } catch (err) {
      if (err.message !== "reloading") toast("Spotify did not move the music.", err.message, true);
      paintDevices();
    }
  }

  async function connect(button) {
    if (button) button.disabled = true;
    const reconnect = !!status?.connected;
    try {
      const out = await api("spotify-login", { method: "POST", body: {} });
      signIn = { url: out.url, opened: !!out.opened, until: Date.now() + SIGN_IN_WAIT, scopes: reconnect };
      toast(out.opened ? "Spotify's sign-in page is open in your browser." : "Open Spotify's sign-in page.",
        out.opened ? "Sign in there and come back: PixelDeck notices by itself." : "PixelDeck could not open a browser. Copy the link from the Music panel.");
      demo = false;
      if (mode === "demo") decide(); else if (mode === "setup") build();
      watchSignIn();
    } catch (err) {
      if (err.message !== "reloading") toast("Could not start the Spotify sign-in.", err.message, true);
    } finally {
      if (button?.isConnected) button.disabled = false;
    }
  }

  async function disconnect(button) {
    if (button) button.disabled = true;
    try {
      status = await api("spotify-logout", { method: "POST", body: {} });
      signIn = null;
      toast("Spotify disconnected.", "PixelDeck forgot its sign-in. Connect again whenever you like.");
      decide();
      notify();
    } catch (err) {
      if (err.message !== "reloading") toast("Could not disconnect Spotify.", err.message, true);
    } finally {
      if (button?.isConnected) button.disabled = false;
    }
  }

  function startDemo() {
    demo = true;
    decide();
    if (!Layout.isShown("music") && !(typeof MusicFloat !== "undefined" && MusicFloat.isOpen())) Layout.activate("music");
  }
  function stopDemo() {
    demo = false;
    decide();
  }
  function openPanel() {
    Layout.activate("music");
    root?.querySelector(".mu")?.scrollIntoView?.({ block: "start" });
  }

  function seekAt(el, clientX) {
    const dur = player?.item?.duration_ms;
    if (!dur || !can("seeking")) return;
    const r = el.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    act("seek", Math.round(frac * dur));
  }
  function volumeAt(el, clientX) {
    if (player?.device?.volume == null || player.device.supports_volume === false) {
      toast("This device takes no volume from Spotify.", "Change it on the device itself.");
      return;
    }
    const r = el.getBoundingClientRect();
    const index = Math.min(VOL_BLOCKS - 1, Math.max(0, Math.floor(((clientX - r.left) / r.width) * VOL_BLOCKS)));
    const value = index === 0 && player.device.volume === 10 ? 0 : (index + 1) * 10;
    act("volume", value);
  }
  // Keys move the position or the volume at once on the page; Spotify hears the last one.
  function nudge(kind, key) {
    if (kind === "seek") {
      const dur = player?.item?.duration_ms;
      if (!dur || !can("seeking")) return false;
      const steps = { ArrowRight: 5000, ArrowUp: 5000, ArrowLeft: -5000, ArrowDown: -5000, PageUp: 30000, PageDown: -30000 };
      let target = key === "Home" ? 0 : key === "End" ? Math.max(0, dur - 2000) : key in steps ? position() + steps[key] : null;
      if (target == null) return false;
      target = Math.round(Math.min(dur - 1000, Math.max(0, target)));
      player.progress_ms = target;
      readAt = performance.now();
      holdUntil = performance.now() + HOLD_MS;
      tick();
      clearTimeout(seekTimer);
      pendingSeek = { value: target, item: itemKey() };
      seekTimer = setTimeout(() => { const job = pendingSeek; pendingSeek = null; if (job && job.item === itemKey()) act("seek", job.value); }, 350);
      return true;
    }
    const vol = player?.device?.volume;
    if (vol == null || player.device.supports_volume === false) return false;
    const steps = { ArrowRight: 10, ArrowUp: 10, ArrowLeft: -10, ArrowDown: -10, PageUp: 30, PageDown: -30 };
    let target = key === "Home" ? 0 : key === "End" ? 100 : key in steps ? vol + steps[key] : null;
    if (target == null) return false;
    target = Math.min(100, Math.max(0, Math.round(target / 10) * 10));
    player.device.volume = target;
    holdUntil = performance.now() + HOLD_MS;
    paint();
    clearTimeout(volTimer);
    pendingVol = { value: target };
    volTimer = setTimeout(() => { const job = pendingVol; pendingVol = null; if (job) act("volume", job.value); }, 350);
    return true;
  }

  async function onClick(e) {
    const t = e.target.closest?.("[data-music]");
    if (!t || t.disabled) return;
    const what = t.dataset.music;
    if (what === "toggle" || what === "next" || what === "previous") return act(what);
    if (what === "shuffle") return act("shuffle", !player?.shuffle);
    if (what === "repeat") return act("repeat", { off: "context", context: "track", track: "off" }[player?.repeat || "off"]);
    if (what === "like") { const id = player?.item?.id; return id && like([id], likes.get(id) !== true); }
    if (what === "seek") return seekAt(t, e.clientX);
    if (what === "volume") return volumeAt(t, e.clientX);
    if (what === "look") return cycleLook();
    if (what === "open") {
      const dialog = t.closest("dialog");
      if (dialog?.open) dialog.close();
      if (typeof MusicFloat !== "undefined") MusicFloat.closeDropdown();
      return openPanel();
    }
    if (what === "drop") {
      if (typeof MusicFloat !== "undefined") return MusicFloat.toggleDropdown(mini);
      return openPanel();
    }
    if (what === "popout") { if (typeof MusicFloat !== "undefined") MusicFloat.popOut(); return; }
    if (what === "connect") return connect(t);
    if (what === "disconnect") return disconnect(t);
    if (what === "preview") return startDemo();
    if (what === "leave") return stopDemo();
    if (what === "retry") { t.disabled = true; await refreshStatus(); if (t.isConnected) t.disabled = false; return; }
    if (what === "copy") {
      const ok = await copyText(t.dataset.copy);
      return ok ? toast("Copied.", t.dataset.copy) : toast("Could not copy.", t.dataset.copy, true);
    }
  }

  /* ------------------------------------------------------------ what the other views use */

  const core = {
    get player() { return player; }, get status() { return status; }, get mode() { return mode; }, get look() { return look; },
    source, position, playing, fmt, glyph, cassette, marquee, fit, still, can,
    act, playFrom, queueAdd, like, checkLikes, liked: (id) => likes.get(id), missing, libraryLocked,
    drawArt, artKey, cycleLook, lookLabel: () => LOOKS[look].label,
    addView: (v) => { views.add(v); return v; }, onTrack: (fn) => trackListeners.add(fn),
    openPanel, shown, connect,
  };

  /* ------------------------------------------------------------ mount */

  function mount(el) {
    root = el;
    root.classList.add("mu-panel");
    mini = document.getElementById("music-mini");
    document.addEventListener("click", (e) => { onClick(e); });
    document.addEventListener("keydown", (e) => {
      const slider = e.target.closest?.("[data-music='seek'], [data-music='volume']");
      if (slider && nudge(slider.dataset.music, e.key)) e.preventDefault();
    });
    root.addEventListener("change", (e) => { if (e.target === els.device) transfer(e.target.value); });
    // Showing the panel again wakes it: measure the marquees, ask what plays if that is stale.
    let wasShown = false;
    new ResizeObserver(() => {
      const now = shown();
      refit();
      if (now && !wasShown && (mode === "live" || mode === "demo")) { poll(); loadDevices(); }
      if (now && !wasShown && els.art && player) { els.art.dataset.art = ""; drawArt(els.art, artKey(player.item), artCss()); }
      wasShown = now;
    }).observe(root);
    document.addEventListener("visibilitychange", () => { if (!document.hidden && (mode === "live" || mode === "demo")) poll(); });
    addEventListener("focus", () => { if (signIn || mode === "setup") refreshStatus(); });
    // The local clock: time, blocks and the end of a song, four times a second while it plays.
    setInterval(async () => {
      if (document.hidden || !playing() || !anyShown()) return;
      if (mode === "demo" && performance.now() >= holdUntil) accept(await MusicDemo.source.now());
      const item = player?.item;
      if ((item?.id ?? item?.name ?? null) !== paintedItem || trackSeen !== (item?.id ?? item?.name ?? null)) paint(); else tick();
    }, 250);
    const calm = () => { refit(); if (view === "deck") paint(); };
    reducedQuery.addEventListener?.("change", calm);
    if (typeof Prefs !== "undefined") Prefs.onChange(calm);
    if (typeof MusicFloat !== "undefined") MusicFloat.mount(core);
    demo = new URLSearchParams(location.search).has("musicdemo");
    build();
    if (demo) {
      decide();
      setTimeout(() => { if (!Layout.isShown("music") && !(typeof MusicFloat !== "undefined" && MusicFloat.isOpen())) Layout.activate("music"); }, 0);
    }
    refreshStatus();
  }

  return {
    mount,
    status: () => status,
    refresh: refreshStatus,
    onStatus: (fn) => listeners.add(fn),
    connect, disconnect,
    preview: startDemo,
    core,
  };
})();
