"use strict";

/* The Music panel: a pixel boombox for Spotify, and its mini player in the status bar.
   What Spotify says arrives trimmed by the server (spotify.py) and is only ever written
   with esc() or textContent. Album art comes through /api/spotify-art, because the page's
   CSP allows no other host, and is drawn 32 pixels across on a canvas that CSS scales up.
   A preview plays made-up songs with art drawn in code, so the deck can be seen without
   an account; it goes through the same render path as the real thing.
   The deck is built once per view, then patched: a poll never restarts the marquee,
   moves focus or closes an open device picker. */

const Music = (() => {
  const POLL_SHOWN = 4000, POLL_IDLE = 20000, POLL_HIDDEN = 60000;
  const STATUS_EVERY = 3000, SIGN_IN_WAIT = 180000, HOLD_MS = 1500, LISTS_EVERY = 300000, DEVICES_EVERY = 60000;
  const ART = 32, MINI_ART = 12, BLOCKS = 32, VOL_BLOCKS = 10, EQ_BARS = 14;
  const reducedQuery = matchMedia("(prefers-reduced-motion: reduce)");
  const still = () => reducedQuery.matches || document.documentElement.dataset.motion === "calm";

  let root = null, mini = null, els = {}, miniEls = {};
  let status = null, statusFailed = "";
  let view = "", mode = "boot";          // mode: boot, setup, live or demo
  let demo = false;
  let player = null, readAt = 0, holdUntil = 0, pollError = "";
  let devices = [], lists = null, listsAt = 0, devicesAt = 0;
  let signIn = null;                     // { url, opened, until } while a sign-in is out in the browser
  let pollTimer = 0, statusTimer = 0, revealTimer = 0, seekTimer = 0, volTimer = 0;
  let pendingSeek = null, pendingVol = null;  // a key-held seek or volume, sent once the keys rest
  let artKey = null, artTicket = 0, artFrame = null, lastFilled = -1, paintedItem = null, endAsked = false;
  const artCache = new Map();
  const listeners = new Set();

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
  };
  GLYPH.prev = GLYPH.next.map((r) => [...r].reverse().join(""));
  const glyph = (name, px = 3) => {
    const rows = GLYPH[name];
    return pixelSvg(`mu-glyph mu-glyph--${name}`, rows[0].length, rows.length, px, rects(rows, { "#": "mu-ink" }));
  };

  // A loudspeaker drawn as pixel rings; the cone is its own group so it can thump.
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

  /* ------------------------------------------------------------ art drawn in code */

  function rng(seed) {
    let a = 0;
    for (const ch of String(seed)) a = (Math.imul(a ^ ch.charCodeAt(0), 2654435761) >>> 0);
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function canvas(size = ART) {
    const c = document.createElement("canvas");
    c.width = c.height = size;
    return c;
  }
  const dot = (g, x, y, color, w = 1, h = 1) => { g.fillStyle = color; g.fillRect(x, y, w, h); };
  function line(g, x0, y0, x1, y1, color) {
    const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    let err = dx + dy, guard = 0;
    while (guard++ < 200) {
      dot(g, x0, y0, color);
      if (x0 === x1 && y0 === y1) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
  }
  function disc(g, cx, cy, r, color, keep = () => true) {
    for (let y = Math.floor(cy - r); y <= cy + r; y++) {
      for (let x = Math.floor(cx - r); x <= cx + r; x++) if (Math.hypot(x - cx, y - cy) <= r && keep(x, y)) dot(g, x, y, color);
    }
  }
  function stars(g, rand, n, maxY, colors = ["#ffffff", "#b9c7ff", "#ffe9a8"]) {
    for (let i = 0; i < n; i++) dot(g, Math.floor(rand() * ART), Math.floor(rand() * maxY), colors[i % colors.length]);
  }

  const COVERS = {
    sunset(g, rand) {
      const sky = ["#1b1036", "#2a1252", "#43156a", "#6c1c7a", "#9c2a7e", "#d0407c", "#f36a62", "#ffa05a"];
      for (let y = 0; y < 21; y++) dot(g, 0, y, sky[Math.min(7, Math.floor(y / 2.6))], ART, 1);
      stars(g, rand, 10, 8);
      disc(g, 16, 15, 8.4, "#ffd35c", (x, y) => y <= 20 && !(y >= 13 && y % 2 === 0));
      for (let y = 7; y < 21; y++) for (let x = 7; x < 26; x++) if (Math.hypot(x - 16, y - 15) <= 8.4 && y > 15 && y % 2 && y <= 20) dot(g, x, y, "#ff7a8a");
      const hills = [20, 19, 18, 18, 17, 17, 18, 19, 20, 20, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 20, 19, 18, 18, 17, 18, 18, 19, 20];
      hills.forEach((top, x) => { for (let y = top; y < 21; y++) dot(g, x, y, "#1d0d2e"); });
      dot(g, 0, 21, "#ff4fd8", ART, 1);
      dot(g, 0, 22, "#120a24", ART, 10);
      for (const y of [23, 25, 28, 31]) dot(g, 0, y, "#a1309e", ART, 1);
      // the floor's lines run to a vanishing point at the sun; the canvas edge cuts the outer ones
      for (let k = -5; k <= 5; k++) line(g, 16 + k * 2, 22, 16 + k * 9, 31, "#c13cbc");
    },
    heart(g, rand) {
      for (let y = 0; y < ART; y += 4) for (let x = 0; x < ART; x += 4) dot(g, x, y, (x + y) % 8 ? "#3b1d5e" : "#4c2577", 4, 4);
      const H = [".###...###.", "#####.#####", "###########", "###########", "###########", ".#########.", "..#######..", "...#####...", "....###....", ".....#....."];
      H.forEach((row, y) => [...row].forEach((ch, x) => { if (ch === "#") dot(g, 5 + x * 2, 6 + y * 2, "#ff3d6e", 2, 2); }));
      for (const [x, y] of [[7, 8], [9, 8], [7, 10], [21, 8]]) dot(g, x, y, "#ffd0dc", 2, 2);
      for (let i = 0; i < 4; i++) { const x = 2 + Math.floor(rand() * 28), y = 1 + Math.floor(rand() * 4) + (i % 2) * 25; dot(g, x, y, "#fff3a8"); dot(g, x - 1, y, "#ffd35c"); dot(g, x + 1, y, "#ffd35c"); }
    },
    slime(g, rand) {
      for (let y = 0; y < ART; y++) dot(g, 0, y, y < 22 ? (y < 8 ? "#0d2b33" : y < 16 ? "#113a40" : "#164a4b") : (y % 2 ? "#0b2126" : "#0f2b30"), ART, 1);
      for (let y = 22; y < ART; y += 2) for (let x = (y / 2) % 2 ? 0 : 4; x < ART; x += 8) dot(g, x, y, "#1d5a52", 4, 2);
      const grid = typeof Sprites !== "undefined" ? Sprites.grid("buddy", 0) : null;
      if (grid) grid.rows.forEach((row, y) => [...row].forEach((ch, x) => { if (grid.colors[ch]) dot(g, x * 2 - 0, 6 + y * 2, grid.colors[ch], 2, 2); }));
      for (const [x, y] of [[4, 3], [26, 5], [22, 1]]) { dot(g, x, y, "#ffe66b", 2, 1); dot(g, x + 1, y + 1, "#ffe66b", 1, 3); dot(g, x - 1, y + 3, "#ffe66b", 2, 2); }
      stars(g, rand, 4, 6, ["#7cf2c8"]);
    },
    space(g, rand) {
      dot(g, 0, 0, "#0a0f2e", ART, ART);
      stars(g, rand, 26, ART);
      disc(g, 16, 16, 8, "#e88a3a");
      for (let y = 8; y <= 24; y++) for (let x = 8; x <= 24; x++) if (Math.hypot(x - 16, y - 16) <= 8 && (y === 13 || y === 18 || y === 19)) dot(g, x, y, "#c0602b");
      for (let y = 8; y <= 24; y++) for (let x = 8; x <= 24; x++) if (Math.hypot(x - 14, y - 13) <= 3 && Math.hypot(x - 16, y - 16) <= 8) dot(g, x, y, "#f6b56a");
      for (let x = 2; x < 30; x++) { const y = Math.round(16 + (x - 16) * 0.32); if (Math.abs(x - 16) > 6 || y > 17) dot(g, x, y, "#ffd98a"); if (Math.abs(x - 16) > 7) dot(g, x, y + 1, "#b98a4a"); }
      const duck = ["..yy.", ".yyky", "yyyyo", ".yyy."];
      duck.forEach((row, y) => [...row].forEach((ch, x) => { const c = { y: "#ffd84a", k: "#111", o: "#ff8a2a" }[ch]; if (c) dot(g, 24 + x, 4 + y, c); }));
    },
    floppy(g, rand) {
      for (let y = 0; y < ART; y++) for (let x = 0; x < ART; x++) dot(g, x, y, (x + y) % 6 < 3 ? "#167c7c" : "#1a8f8c");
      dot(g, 6, 5, "#0e1430", 21, 23); dot(g, 5, 6, "#0e1430", 23, 21);
      dot(g, 7, 6, "#232c5e", 19, 21);
      dot(g, 11, 6, "#aab3c2", 11, 8); dot(g, 18, 7, "#232c5e", 2, 6);
      dot(g, 9, 17, "#f4f1e6", 15, 9);
      for (const y of [19, 21, 23]) dot(g, 11, y, "#9aa6c9", 11, 1);
      for (const [x, y] of [[19, 18], [21, 18], [18, 19], [19, 19], [20, 19], [21, 19], [22, 19], [19, 20], [20, 20], [21, 20], [20, 21]]) dot(g, x, y, "#ff4f73");
      stars(g, rand, 5, ART, ["#7ff0e0"]);
    },
    lighthouse(g, rand) {
      for (let y = 0; y < 22; y++) dot(g, 0, y, ["#0b1233", "#101a45", "#16235a", "#1e2f6e"][Math.min(3, Math.floor(y / 6))], ART, 1);
      stars(g, rand, 12, 14);
      disc(g, 6, 6, 3, "#f3efd0"); disc(g, 7, 5, 2.2, "#0b1233", (x, y) => Math.hypot(x - 6, y - 6) <= 3);
      for (let x = 1; x < 22; x++) { const spread = Math.round((22 - x) * 0.32); for (let y = 9 - spread; y <= 9 + spread; y++) if ((x + y) % 2 === 0) dot(g, x, y, "#fff2a6"); }
      for (let y = 10; y < 26; y++) dot(g, 21, y, Math.floor((y - 10) / 3) % 2 ? "#f4f1e6" : "#e0453a", 5, 1);
      dot(g, 20, 6, "#2a2a3a", 7, 1); dot(g, 21, 7, "#ffe66b", 5, 3); dot(g, 20, 10, "#2a2a3a", 7, 1); dot(g, 22, 4, "#2a2a3a", 3, 2);
      for (let y = 22; y < ART; y++) dot(g, 0, y, y % 2 ? "#0d2c55" : "#123a6b", ART, 1);
      for (let i = 0; i < 14; i++) dot(g, Math.floor(rand() * 30), 23 + Math.floor(rand() * 9), "#5f8fd0", 2, 1);
      dot(g, 18, 26, "#3a3a4a", 11, 2);
    },
    city(g, rand) {
      for (let y = 0; y < ART; y++) dot(g, 0, y, ["#2a1145", "#3a1652", "#4d1b5c", "#6a2160", "#8a2a5e"][Math.min(4, Math.floor(y / 6))], ART, 1);
      stars(g, rand, 8, 9);
      dot(g, 5, 3, "#0a1a12", 22, 9); dot(g, 6, 4, "#0f2a1c", 20, 7);
      const check = [[9, 8], [10, 9], [11, 10], [12, 9], [13, 8], [14, 7], [15, 6], [16, 5]];
      for (const [x, y] of check) { dot(g, x + 3, y - 1, "#3dff8a", 2, 1); dot(g, x + 3, y, "#2ad46f", 2, 1); }
      dot(g, 10, 12, "#555", 1, 4); dot(g, 21, 12, "#555", 1, 4);
      let x = 0;
      while (x < ART) {
        const w = 3 + Math.floor(rand() * 4), h = 8 + Math.floor(rand() * 12);
        dot(g, x, ART - h, "#120a1e", w, h);
        for (let wy = ART - h + 2; wy < ART - 1; wy += 2) for (let wx = x + 1; wx < x + w - 1; wx += 2) if (rand() > 0.45) dot(g, wx, wy, rand() > 0.3 ? "#ffd86b" : "#7cffb0");
        x += w + (rand() > 0.6 ? 1 : 0);
      }
    },
  };
  function painted(name) {
    const c = canvas();
    const g = c.getContext("2d");
    (COVERS[name] || COVERS.space)(g, rng(name));
    return c;
  }
  // A song without a picture gets a tape label in stripes from its own name.
  function plainCover(seed) {
    const c = canvas(), g = c.getContext("2d"), rand = rng(seed), hue = Math.floor(rand() * 360);
    dot(g, 0, 0, `hsl(${hue} 30% 16%)`, ART, ART);
    for (let y = 6; y < 26; y += 4) dot(g, 0, y, `hsl(${(hue + y * 9) % 360} 70% ${48 + (y % 8)}%)`, ART, 2);
    dot(g, 4, 12, "#f4f1e6", 24, 8);
    for (let i = 0; i < 5; i++) dot(g, 6 + i * 4, 15, "#2a2a3a", 2, 2);
    return c;
  }
  // What a set shows with no signal: colour bars and a strip of noise.
  function noSignal() {
    const c = canvas(), g = c.getContext("2d"), rand = rng("no signal");
    const bars = ["#c8c8c8", "#c8c800", "#00c8c8", "#00c800", "#c800c8", "#c80000", "#0000c8"], widths = [5, 5, 4, 5, 4, 5, 4];
    let x = 0;
    bars.forEach((color, i) => { dot(g, x, 0, color, widths[i], 21); dot(g, x, 21, bars[6 - i], widths[i], 3); x += widths[i]; });
    for (let y = 24; y < ART; y++) for (let nx = 0; nx < ART; nx++) { const v = Math.floor(rand() * 200) + 20; dot(g, nx, y, `rgb(${v},${v},${v})`); }
    return c;
  }

  // Real album art, shrunk to 32 pixels: an ordered dither and 8 levels a channel make it read as pixel art.
  const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];
  function pixelate(image) {
    const c = canvas(), g = c.getContext("2d", { willReadFrequently: true });
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = "high";
    g.drawImage(image, 0, 0, ART, ART);
    const img = g.getImageData(0, 0, ART, ART), d = img.data;
    for (let i = 0; i < d.length; i += 4) {
      const p = i >> 2, t = (BAYER[((p >> 5) & 3) * 4 + (p & 3)] / 16 - 0.5) * 30;
      for (let k = 0; k < 3; k++) d[i + k] = Math.max(0, Math.min(255, Math.round((d[i + k] + t) / 36) * 36));
      d[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
    return c;
  }
  async function fetchArt(url) {
    const res = await fetch(`/api/spotify-art?url=${encodeURIComponent(url)}`, { headers: { "X-Desk-Token": TOKEN } });
    if (!res.ok) throw new Error(`art ${res.status}`);
    const bitmap = await createImageBitmap(await res.blob());
    try { return pixelate(bitmap); } finally { bitmap.close?.(); }
  }

  /* ------------------------------------------------------------ sources */

  // The preview: made-up songs and artists, a local clock, controls that work on this page only.
  const Demo = (() => {
    const TRACKS = [
      { id: "demo-1", name: "Neon Harbor", artists: ["The Pixel Tides"], album: "Night Drive EP", duration_ms: 222000, art: "demo:sunset" },
      { id: "demo-2", name: "Cartridge Hearts", artists: ["Bit Lagoon"], album: "Save Slot One", duration_ms: 185000, art: "demo:heart" },
      { id: "demo-3", name: "Slime Time Boogie", artists: ["Gelatin Groove", "DJ Wobble"], album: "Bouncy Castle", duration_ms: 168000, art: "demo:slime" },
      { id: "demo-4", name: "Orbit of the Rubber Duck", artists: ["Captain Lint"], album: "Debug Odyssey", duration_ms: 256000, art: "demo:space" },
      { id: "demo-5", name: "Floppy Disk Romance", artists: ["Magnetic Avenue"], album: "1.44 MB of Love", duration_ms: 207000, art: "demo:floppy" },
      { id: "demo-6", name: "Lighthouse Lo-Fi", artists: ["Harbor Static"], album: "Foghorn Tapes", duration_ms: 179000, art: "demo:lighthouse" },
      { id: "demo-7", name: "Green Build Groove", artists: ["The Merge Conflicts"], album: "All Checks Passed", duration_ms: 213000, art: "demo:city" },
    ];
    const LISTS = [
      { id: "demoFocus", name: "Focus Mode: Pixels", owner: "You", tracks: 24 },
      { id: "demoMerge", name: "Merge Day Anthems", owner: "You", tracks: 18 },
      { id: "demoDuck", name: "Rubber Duck Radio", owner: "Pixel Pat", tracks: 31 },
      { id: "demoRebase", name: "Late Night Rebase", owner: "You", tracks: 12 },
      { id: "demoGreen", name: "Green Build Grooves", owner: "Robo Rita", tracks: 27 },
      { id: "demoStandup", name: "Standup Shuffle", owner: "You", tracks: 9 },
    ];
    const DEVICES = [
      { id: "demoDesk", name: "Desk Speakers", type: "Computer", volume: 70, supports_volume: true },
      { id: "demoPhone", name: "Pocket Phone", type: "Smartphone", volume: 40, supports_volume: true },
      { id: "demoKitchen", name: "Kitchen Radio", type: "Speaker", volume: 55, supports_volume: true },
    ];
    let s = null;
    function reset() { s = { i: 0, playing: true, pos: 71000, at: performance.now(), shuffle: false, repeat: "context", volume: 70, device: "demoDesk", list: "demoFocus" }; }
    const step = (dir) => (s.shuffle && dir > 0 ? (s.i + 3) % TRACKS.length : (s.i + dir + TRACKS.length) % TRACKS.length);
    function clock() {
      const t = performance.now();
      if (s.playing) s.pos += t - s.at;
      s.at = t;
      for (let guard = 0; s.pos >= TRACKS[s.i].duration_ms && guard < 50; guard++) {
        s.pos -= TRACKS[s.i].duration_ms;
        if (s.repeat !== "track") s.i = step(1);
      }
    }
    function now() {
      if (!s) reset();
      clock();
      const dev = DEVICES.find((d) => d.id === s.device);
      return {
        connected: true, active: true, is_playing: s.playing, progress_ms: Math.floor(s.pos), shuffle: s.shuffle, repeat: s.repeat,
        device: { ...dev, active: true, restricted: false, volume: s.volume }, item: { ...TRACKS[s.i], kind: "track" }, playlist: s.list, disallows: [],
      };
    }
    async function control(action, value) {
      clock();
      if (action === "play") s.playing = true;
      else if (action === "pause") s.playing = false;
      else if (action === "next") { s.i = step(1); s.pos = 0; }
      else if (action === "previous") { if (s.pos > 3000) s.pos = 0; else { s.i = step(-1); s.pos = 0; } }
      else if (action === "seek") s.pos = value;
      else if (action === "volume") s.volume = value;
      else if (action === "shuffle") s.shuffle = value;
      else if (action === "repeat") s.repeat = value;
      return { ok: true };
    }
    return {
      now: async () => now(), control, reset,
      devices: async () => { if (!s) reset(); return DEVICES.map((d) => ({ ...d, active: d.id === s.device, volume: d.id === s.device ? s.volume : d.volume })); },
      transfer: async (id) => { const d = DEVICES.find((x) => x.id === id); if (d) { s.device = id; s.volume = d.volume; } return { ok: true }; },
      playlists: async () => LISTS,
      playPlaylist: async (id) => { clock(); s.list = id; s.i = [...id].reduce((a, ch) => a + ch.charCodeAt(0), 0) % TRACKS.length; s.pos = 0; s.playing = true; return { ok: true }; },
      art: async (item) => painted(String(item.art).slice(5)),
    };
  })();

  const Live = {
    now: () => api("spotify-now"),
    control: (action, value, device) => api("spotify-control", { method: "POST", body: { action, value, ...(device ? { device_id: device } : {}) } }),
    devices: async () => (await api("spotify-devices")).devices || [],
    transfer: (id, play) => api("spotify-transfer", { method: "POST", body: { device_id: id, play } }),
    playlists: async () => (await api("spotify-playlists")).playlists || [],
    playPlaylist: (id, device) => api("spotify-playlist-play", { method: "POST", body: { id, ...(device ? { device_id: device } : {}) } }),
    art: (item) => fetchArt(item.art),
  };
  const source = () => (mode === "demo" ? Demo : Live);

  /* ------------------------------------------------------------ helpers */

  function fmt(ms) {
    const total = Math.max(0, Math.floor((ms || 0) / 1000)), h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), sec = total % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  }
  const shown = () => !!root && root.isConnected && root.getClientRects().length > 0;
  const miniShown = () => !!mini && !mini.hidden && mini.getClientRects().length > 0;
  const playing = () => !!player?.is_playing && !!player?.item;
  const can = (what) => !(player?.disallows || []).includes(what);
  function position() {
    if (!player?.item) return 0;
    const run = player.is_playing ? performance.now() - readAt : 0;
    return Math.min(player.item.duration_ms, player.progress_ms + run);
  }
  function hash(text) { let h = 0; for (const ch of String(text)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h; }
  const TAPE_TONES = ["--sprite-orange", "--sprite-blue", "--sprite-yellow", "--sprite-red", "--sprite-grass", "--color-cool"];

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
    const waiting = signIn ? `<div class="mu-wait" role="status"><span class="mu-wait-led" aria-hidden="true"></span>
        <span>${signIn.opened ? "Spotify's sign-in page is open in your browser. Sign in there; this page notices by itself." : "PixelDeck could not open a browser here. Copy the sign-in link into one:"}</span>
        ${copy(signIn.url, "Copy the sign-in link")}</div>` : "";
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
          <button class="btn btn--quiet" type="button" data-music="retry">Check again</button></p>${waiting}`)}
      </ol>
      <p class="mu-premium"><span class="mu-premium-tag num8">PREMIUM</span><span>Spotify Premium is needed for the app's owner, and for play, pause, skip, seek and volume.
        While the app is in development mode it works for its owner and up to 5 people added under User Management.</span></p>
      <footer class="mu-setup-foot"><span>Just looking?</span><button class="btn" type="button" data-music="preview">Preview with made-up music</button></footer>
    </section></div>`;
  }

  function deckHtml() {
    const eqLevels = [37.5, 25, 50, 12.5, 37.5, 62.5, 25, 50, 37.5, 62.5, 50, 75, 62.5, 75];
    const eq = Array.from({ length: EQ_BARS }, (_, i) =>
      `<i class="mu-eq-${"abc"[i % 3]}" style="--d:${440 + ((i * 7) % 5) * 90}ms;--o:-${(i * 173) % 700}ms;--rest:${eqLevels[i]}%"></i>`).join("");
    const blocks = "<i></i>".repeat(BLOCKS);
    const vol = Array.from({ length: VOL_BLOCKS }, (_, i) => `<i style="--h:${28 + i * 8}%"></i>`).join("");
    const woofer = speaker(26, 4, "mu-woofer"), tweeter = speaker(10, 4, "mu-tweeter");
    return `<div class="mu" data-view="deck">
      <div class="mu-ribbon" data-ref="ribbon" hidden></div>
      <div class="mu-stage">
        <div class="mu-crew" aria-hidden="true"><span class="mu-slime">${Sprites.svg("buddy", { scale: 4 })}</span><span class="mu-bot">${Sprites.svg("bot", { scale: 4 })}</span></div>
        <div class="mu-box">
          <div class="mu-handle" aria-hidden="true"></div>
          <div class="mu-brand" aria-hidden="true"><span class="mu-power" data-ref="power"></span><b>PIXELDECK</b><small>PD-808 STEREO CASSETTE</small></div>
          <div class="mu-face">
            <div class="mu-spk" aria-hidden="true">${tweeter}${woofer}</div>
            <div class="mu-crt"><canvas class="mu-art" data-ref="art" width="${ART}" height="${ART}" role="img" aria-label="Album art"></canvas><span class="mu-crt-knobs" aria-hidden="true"><i></i><i></i></span></div>
            <div class="mu-lcd" data-ref="lcd">
              <div class="mu-lcd-row"><span class="mu-mode" data-ref="mode">STOP</span><span class="mu-dev" data-ref="dev"></span></div>
              <div class="mu-mq mu-mq--title" data-ref="title"><span class="mu-mq-track"><span class="mu-mq-text"></span><span class="mu-mq-text" aria-hidden="true"></span></span></div>
              <div class="mu-mq mu-mq--sub" data-ref="sub"><span class="mu-mq-track"><span class="mu-mq-text"></span><span class="mu-mq-text" aria-hidden="true"></span></span></div>
              <div class="mu-mq mu-mq--album" data-ref="album"><span class="mu-mq-track"><span class="mu-mq-text"></span><span class="mu-mq-text" aria-hidden="true"></span></span></div>
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
      <section class="mu-tapes" aria-labelledby="mu-tapes-title"><header class="mu-tapes-head"><h3 id="mu-tapes-title">Tapes</h3><span class="mu-hint">Your playlists. Pick one to play it.</span></header>
        <div class="mu-tape-list" data-ref="tapes"></div></section>
    </div>`;
  }

  function build() {
    const next = mode === "boot" ? "boot" : mode === "setup" ? "setup" : "deck";
    if (!root) return;
    clearInterval(revealTimer);
    if (next === "deck" && view === "deck") { paintAll(); return; }
    view = next;
    root.innerHTML = next === "boot" ? bootHtml() : next === "setup" ? setupHtml() : deckHtml();
    els = {};
    for (const el of root.querySelectorAll("[data-ref]")) els[el.dataset.ref] = el;
    if (next === "deck") {
      artKey = null;
      lastFilled = -1;
      if (artFrame) els.art.getContext("2d").drawImage(artFrame, 0, 0);
      RetroSelect.enhanceAll(root);
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
  }

  function ribbon() {
    if (!els.ribbon) return;
    let html = "";
    if (mode === "demo") {
      html = `<span class="mu-ribbon-tag num8">PREVIEW</span><span>Made-up songs and artists, drawn here. Nothing plays and nothing reaches Spotify.</span>
        <span class="mu-ribbon-tools">${status?.connected ? "" : status?.configured && status?.client_id_ok ? '<button class="btn btn--primary" type="button" data-music="connect">Connect Spotify</button>' : '<button class="btn" type="button" data-music="leave">Set up Spotify</button>'}
        <button class="btn btn--quiet" type="button" data-music="leave">${status?.connected ? "Back to my Spotify" : "Leave the preview"}</button></span>`;
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
    paintTapes();
  }

  function paint() {
    paintMini();
    if (view !== "deck" || !els.lcd) return;
    const p = player, item = p?.item, on = playing();
    paintedItem = item?.id ?? item?.name ?? null;
    root.firstElementChild.classList.toggle("is-playing", on);
    root.firstElementChild.classList.toggle("is-idle", !item);
    els.mode.textContent = !p ? "TUNING" : !item ? "NO TAPE" : on ? "PLAY" : "PAUSE";
    els.mode.dataset.state = !item ? "stop" : on ? "play" : "pause";
    els.dev.textContent = p?.device?.name ? p.device.name.toUpperCase() : "";
    marquee(els.title, item ? item.name : p ? "Nothing is playing" : "Tuning in");
    marquee(els.sub, item ? item.artists.join(", ") : p ? "Start Spotify on a phone or computer," : "");
    marquee(els.album, item ? (item.album ? `${item.kind === "episode" ? "SHOW" : "FROM"} ${item.album}` : "") : p ? "or pick a tape below." : "");
    els.play.classList.toggle("is-playing", on);
    els.play.setAttribute("aria-label", on ? "Pause" : "Play");
    els.playLabel.textContent = on ? "PAUSE" : "PLAY";
    els.play.disabled = !p || (on ? !can("pausing") : (!item && !els.device?.value) || !can("resuming"));
    els.prev.disabled = !item || !can("skipping_prev");
    els.next.disabled = !item || !can("skipping_next");
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
    markTape();
    wantArt(item, !!p);
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
        <button class="mm-title" type="button" data-music="open" data-tip="Open the music player"><canvas class="mm-art" width="${MINI_ART}" height="${MINI_ART}" aria-hidden="true"></canvas>
        <span class="mu-mq mm-mq"><span class="mu-mq-track"><span class="mu-mq-text"></span><span class="mu-mq-text" aria-hidden="true"></span></span></span>
        <span class="mm-eq" aria-hidden="true"><i></i><i></i><i></i></span></button>`;
      miniEls = { play: mini.querySelector(".mm-play"), title: mini.querySelector(".mm-title"), art: mini.querySelector(".mm-art"), mq: mini.querySelector(".mm-mq") };
      if (artFrame) drawMiniArt(artFrame);
    }
    const item = player?.item, isOn = playing();
    mini.classList.toggle("is-playing", isOn);
    miniEls.play.classList.toggle("is-playing", isOn);
    miniEls.play.setAttribute("aria-label", isOn ? "Pause" : "Play");
    miniEls.play.disabled = !item;
    const text = item ? `${item.name} · ${item.artists.join(", ")}` : player ? "Nothing playing" : "Music";
    marquee(miniEls.mq, text);
    miniEls.title.setAttribute("aria-label", item ? `Now playing: ${item.name} by ${item.artists.join(", ")}. Open the music player.` : "Open the music player");
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

  function paintTapes() {
    const box = els.tapes;
    if (!box) return;
    let html;
    if (lists === null) html = '<p class="mu-tapes-empty">Reading your playlists</p>';
    else if (!lists.length) html = '<p class="mu-tapes-empty">No playlists yet. The ones you make or follow in Spotify show up here.</p>';
    else {
      html = lists.map((p) => {
        const meta = [p.tracks != null ? `${p.tracks} ${p.tracks === 1 ? "song" : "songs"}` : "", p.owner || ""].filter(Boolean).join("  ");
        return `<button class="mu-tape" type="button" data-music="playlist" data-id="${esc(p.id)}" style="--tape:var(${TAPE_TONES[hash(p.id) % TAPE_TONES.length]})" aria-label="Play the playlist ${esc(p.name)}">
          <span class="mu-tape-label"><span class="mu-tape-top"><span class="mu-tape-side num8" aria-hidden="true">A</span><b>${esc(p.name)}</b></span>
            <small>${esc(meta)}</small>
            <span class="mu-tape-window" aria-hidden="true"><i class="mu-reel"></i><span class="mu-tape-run"></span><i class="mu-reel"></i></span></span>
          <span class="mu-tape-foot" aria-hidden="true"><i></i><i></i><i></i></span><span class="mu-tape-sticker num8" aria-hidden="true">ON AIR</span></button>`;
      }).join("");
    }
    if (box._html !== html) { box.innerHTML = html; box._html = html; }
    markTape();
  }
  function markTape() {
    if (!els.tapes) return;
    for (const t of els.tapes.querySelectorAll(".mu-tape")) {
      const on = !!player?.playlist && t.dataset.id === player.playlist;
      t.classList.toggle("is-on", on);
      t.setAttribute("aria-current", on ? "true" : "false");
    }
  }

  /* ------------------------------------------------------------ the picture */

  function wantArt(item, known) {
    const key = item ? item.art || `plain:${item.id || item.name}` : known ? "none" : "boot";
    if (key === artKey) return;
    artKey = key;
    const ticket = ++artTicket;
    if (!item) { show(noSignal()); return; }
    if (artCache.has(key)) { show(artCache.get(key)); return; }
    const done = (frame) => {
      artCache.set(key, frame);
      while (artCache.size > 24) artCache.delete(artCache.keys().next().value);
      if (ticket === artTicket) show(frame);
    };
    if (!item.art) { done(plainCover(key)); return; }
    source().art(item).then(done, () => { if (ticket === artTicket) show(plainCover(key)); });
  }
  function drawMiniArt(frame) {
    const g = miniEls.art?.getContext("2d");
    if (!g) return;
    g.imageSmoothingEnabled = true;
    g.clearRect(0, 0, MINI_ART, MINI_ART);
    g.drawImage(frame, 0, 0, MINI_ART, MINI_ART);
  }
  // A new picture rolls in from the top, four rows a frame, like a set changing channel.
  function show(frame) {
    artFrame = frame;
    drawMiniArt(frame);
    const g = els.art?.getContext("2d");
    if (!g) return;
    clearInterval(revealTimer);
    if (still() || !shown()) { g.drawImage(frame, 0, 0); return; }
    let row = 0;
    revealTimer = setInterval(() => {
      row = Math.min(ART, row + 4);
      g.drawImage(frame, 0, 0, ART, row, 0, 0, ART, row);
      if (row < ART) { g.fillStyle = "rgba(210, 255, 220, 0.7)"; g.fillRect(0, row, ART, 1); } else clearInterval(revealTimer);
    }, 34);
  }

  /* ------------------------------------------------------------ status and polling */

  function notify() { for (const fn of listeners) { try { fn(status); } catch { /* a listener's own trouble */ } } }

  function decide() {
    const was = mode;
    mode = demo ? "demo" : status?.connected ? "live" : status ? "setup" : "boot";
    const deck = mode === "live" || mode === "demo";
    if (mode !== was && deck) {
      player = null; lists = null; devices = []; listsAt = devicesAt = 0; pollError = "";
      if (mode === "demo") Demo.reset();
    }
    if (!deck) { clearTimeout(pollTimer); player = null; }
    if (mode !== was || !deck) build(); else paintAll();
    paintMini();
    if (mode !== was && deck) { poll(); loadSide(true); }
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
    }
    decide();
    notify();
    return status;
  }

  function watchSignIn() {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(async function again() {
      await refreshStatus();
      if (status?.connected || !signIn) return;
      if (Date.now() < signIn.until) statusTimer = setTimeout(again, STATUS_EVERY);
      else { signIn = null; if (mode === "setup") build(); }
    }, STATUS_EVERY);
  }

  function cadence() {
    if (document.hidden) return POLL_HIDDEN;
    return shown() || miniShown() ? POLL_SHOWN : POLL_IDLE;
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

  async function loadSide(force = false) {
    if (mode !== "live" && mode !== "demo") return;
    const asked = mode, now = Date.now();
    if (force || now - devicesAt > DEVICES_EVERY) {
      devicesAt = now;
      source().devices().then((d) => { if (mode === asked) { devices = d; paintDevices(); paint(); } }).catch(() => {});
    }
    if (force || now - listsAt > LISTS_EVERY) {
      listsAt = now;
      source().playlists().then((l) => { if (mode === asked) { lists = l; paintTapes(); } })
        .catch((err) => { if (mode === asked) { lists = lists || []; paintTapes(); pollError = pollError || err.message; paint(); } });
    }
  }

  /* ------------------------------------------------------------ actions */

  const deviceChoice = () => (player?.active ? undefined : els.device?.value || undefined);

  const itemKey = () => player?.item?.id ?? player?.item?.name ?? null;
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
      if (mode === "demo" || action === "next" || action === "previous") { holdUntil = 0; await poll(); return; }
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

  async function playList(id) {
    const tape = els.tapes?.querySelector(`.mu-tape[data-id="${CSS.escape(id)}"]`);
    tape?.classList.add("is-loading");
    try {
      await source().playPlaylist(id, deviceChoice());
      if (player) player.playlist = id;
      markTape();
      holdUntil = 0;
      setTimeout(poll, mode === "demo" ? 0 : 700);
    } catch (err) {
      if (err.message !== "reloading") toast("Spotify did not play that tape.", err.message, true);
    } finally {
      tape?.classList.remove("is-loading");
    }
  }

  async function transfer(id) {
    if (!id || id === player?.device?.id) return;
    try {
      await source().transfer(id, playing());
      devicesAt = 0;
      setTimeout(() => { poll(); loadSide(); }, mode === "demo" ? 0 : 900);
    } catch (err) {
      if (err.message !== "reloading") toast("Spotify did not move the music.", err.message, true);
      paintDevices();
    }
  }

  async function connect(button) {
    if (button) button.disabled = true;
    try {
      const out = await api("spotify-login", { method: "POST", body: {} });
      signIn = { url: out.url, opened: !!out.opened, until: Date.now() + SIGN_IN_WAIT };
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
    if (!Layout.isShown("music")) Layout.activate("music");
  }
  function stopDemo() {
    demo = false;
    decide();
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
    if (what === "seek") return seekAt(t, e.clientX);
    if (what === "volume") return volumeAt(t, e.clientX);
    if (what === "playlist") return playList(t.dataset.id);
    if (what === "open") {
      const dialog = t.closest("dialog");
      if (dialog?.open) dialog.close();
      return Layout.activate("music");
    }
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

  /* ------------------------------------------------------------ mount */

  function mount(el) {
    root = el;
    root.classList.add("mu-panel");
    mini = document.getElementById("music-mini");
    document.addEventListener("click", (e) => { onClick(e); });
    root.addEventListener("keydown", (e) => {
      const slider = e.target.closest?.("[data-music='seek'], [data-music='volume']");
      if (slider && nudge(slider.dataset.music, e.key)) e.preventDefault();
    });
    root.addEventListener("change", (e) => { if (e.target === els.device) transfer(e.target.value); });
    // Showing the panel again wakes it: measure the marquee, ask what plays if that is stale.
    let wasShown = false;
    new ResizeObserver(() => {
      const now = shown();
      refit();
      if (now && !wasShown && (mode === "live" || mode === "demo")) { poll(); loadSide(); }
      wasShown = now;
    }).observe(root);
    document.addEventListener("visibilitychange", () => { if (!document.hidden && (mode === "live" || mode === "demo")) poll(); });
    addEventListener("focus", () => { if (signIn || mode === "setup") refreshStatus(); });
    // The local clock: time, blocks and the end of a song, four times a second while it plays.
    setInterval(async () => {
      if (document.hidden || !playing() || !(shown() || miniShown())) return;
      if (mode === "demo" && performance.now() >= holdUntil) accept(await Demo.now());
      const item = player?.item;
      if ((item?.id ?? item?.name ?? null) !== paintedItem) paint(); else tick();
    }, 250);
    const calm = () => { refit(); if (view === "deck") paint(); };
    reducedQuery.addEventListener?.("change", calm);
    if (typeof Prefs !== "undefined") Prefs.onChange(calm);
    demo = new URLSearchParams(location.search).has("musicdemo");
    build();
    if (demo) {
      decide();
      setTimeout(() => { if (!Layout.isShown("music")) Layout.activate("music"); }, 0);
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
  };
})();
