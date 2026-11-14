"use strict";

/* The Music preview: made-up songs, artists, albums and playlists, and cover art drawn in
   code. Covers are smooth 256 px pictures, like real album art, so they go through the
   same pixel looks (8, 16 and 32-bit) as art from Spotify. MusicDemo.source() answers
   in the same shapes as the server's /api/spotify-* routes, and plays on a local clock. */

const MusicDemo = (() => {
  const SIZE = 256;

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

  /* ------------------------------------------------------------ covers (drawn on a 100 unit square) */

  // roundRect is new in some browsers (older WebKitGTK lacks it): draw the corners by hand
  function rounded(g, x, y, w, h, r) {
    g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }
  const linear = (g, x0, y0, x1, y1, stops) => { const gr = g.createLinearGradient(x0, y0, x1, y1); stops.forEach(([at, c]) => gr.addColorStop(at, c)); return gr; };
  const radial = (g, x, y, r0, r1, stops) => { const gr = g.createRadialGradient(x, y, r0, x, y, r1); stops.forEach(([at, c]) => gr.addColorStop(at, c)); return gr; };
  function stars(g, rand, n, maxY) {
    for (let i = 0; i < n; i++) {
      g.fillStyle = `rgba(255, 255, 255, ${0.35 + rand() * 0.65})`;
      const s = rand() > 0.85 ? 0.9 : 0.5;
      g.fillRect(rand() * 100, rand() * maxY, s, s);
    }
  }
  function sparkle(g, x, y, r, color) {
    g.fillStyle = color;
    g.beginPath();
    g.moveTo(x, y - r); g.quadraticCurveTo(x, y, x + r, y); g.quadraticCurveTo(x, y, x, y + r);
    g.quadraticCurveTo(x, y, x - r, y); g.quadraticCurveTo(x, y, x, y - r);
    g.fill();
  }
  function note(g, x, y, color) {
    g.fillStyle = color; g.strokeStyle = color; g.lineWidth = 1.2;
    g.beginPath(); g.ellipse(x, y, 2.6, 2, -0.4, 0, Math.PI * 2); g.fill();
    g.beginPath(); g.moveTo(x + 2.3, y - 0.5); g.lineTo(x + 2.3, y - 11); g.quadraticCurveTo(x + 6, y - 9, x + 6, y - 5); g.stroke();
  }

  const COVERS = {
    sunset(g, rand) {
      const sky = linear(g, 0, 0, 0, 62, [[0, "#140a30"], [0.45, "#4a1670"], [0.8, "#c2357d"], [1, "#ff8a5c"]]);
      g.fillStyle = sky; g.fillRect(0, 0, 100, 62);
      stars(g, rand, 50, 34);
      g.save();
      g.beginPath(); g.arc(50, 47, 24, 0, Math.PI * 2); g.clip();
      g.fillStyle = linear(g, 0, 23, 0, 62, [[0, "#ffe66b"], [0.55, "#ff9a5a"], [1, "#ff4f8b"]]);
      g.fillRect(20, 20, 60, 42);
      g.fillStyle = sky;
      for (let k = 0; k < 6; k++) g.fillRect(20, 45 + k * 3, 60, 0.5 + k * 0.38);
      g.restore();
      g.fillStyle = "#1c0a31";
      g.beginPath(); g.moveTo(0, 62); g.lineTo(0, 51); g.lineTo(11, 44); g.lineTo(20, 52); g.lineTo(29, 48); g.lineTo(39, 62); g.fill();
      g.beginPath(); g.moveTo(100, 62); g.lineTo(100, 49); g.lineTo(87, 42); g.lineTo(77, 51); g.lineTo(67, 47); g.lineTo(59, 62); g.fill();
      g.fillStyle = linear(g, 0, 62, 0, 100, [[0, "#2c0b40"], [1, "#0a0514"]]); g.fillRect(0, 62, 100, 38);
      g.fillStyle = "#ff4fd8"; g.fillRect(0, 61.4, 100, 1.2);
      g.strokeStyle = "rgba(255, 79, 216, 0.8)"; g.lineWidth = 0.55;
      for (let k = -10; k <= 10; k++) { g.beginPath(); g.moveTo(50 + k * 3.2, 62); g.lineTo(50 + k * 19, 100); g.stroke(); }
      for (let k = 1; k < 8; k++) { const y = 62 + Math.pow(k / 7, 2) * 38; g.beginPath(); g.moveTo(0, y); g.lineTo(100, y); g.stroke(); }
    },
    heart(g, rand) {
      g.fillStyle = radial(g, 50, 45, 4, 80, [[0, "#7a35b0"], [1, "#180a2c"]]); g.fillRect(0, 0, 100, 100);
      g.fillStyle = "rgba(255, 255, 255, 0.05)";
      for (let y = 0; y < 100; y += 10) for (let x = (y / 10) % 2 ? 0 : 10; x < 100; x += 20) g.fillRect(x, y, 10, 10);
      const heart = () => {
        g.beginPath(); g.moveTo(50, 82);
        g.bezierCurveTo(18, 61, 12, 40, 25, 27); g.bezierCurveTo(36, 17, 48, 23, 50, 34);
        g.bezierCurveTo(52, 23, 64, 17, 75, 27); g.bezierCurveTo(88, 40, 82, 61, 50, 82); g.closePath();
      };
      g.save(); g.shadowColor = "#ff3d6e"; g.shadowBlur = 28; g.fillStyle = "#ff3d6e"; heart(); g.fill(); g.restore();
      g.fillStyle = linear(g, 28, 24, 70, 82, [[0, "#ff8fae"], [0.5, "#ff3d6e"], [1, "#b8123f"]]); heart(); g.fill();
      g.fillStyle = "rgba(255, 255, 255, 0.6)"; g.beginPath(); g.ellipse(34, 36, 6.5, 4, -0.7, 0, Math.PI * 2); g.fill();
      for (const [x, y, r] of [[18, 16, 4], [82, 20, 3], [86, 74, 4.5], [14, 80, 3]]) sparkle(g, x, y, r, "#fff0a8");
      stars(g, rand, 14, 100);
    },
    slime(g) {
      g.fillStyle = linear(g, 0, 0, 0, 70, [[0, "#06232b"], [1, "#145150"]]); g.fillRect(0, 0, 100, 70);
      g.fillStyle = radial(g, 50, 0, 2, 75, [[0, "rgba(180, 255, 230, 0.35)"], [1, "rgba(180, 255, 230, 0)"]]);
      g.beginPath(); g.moveTo(40, 0); g.lineTo(60, 0); g.lineTo(88, 78); g.lineTo(12, 78); g.fill();
      g.fillStyle = linear(g, 0, 70, 0, 100, [[0, "#1d6e60"], [1, "#0b2a28"]]); g.fillRect(0, 70, 100, 30);
      g.fillStyle = "rgba(255, 255, 255, 0.06)";
      for (let k = 0; k < 6; k++) g.fillRect(0, 72 + k * 5, 100, 1);
      g.fillStyle = "rgba(0, 0, 0, 0.35)"; g.beginPath(); g.ellipse(50, 79, 30, 4.5, 0, 0, Math.PI * 2); g.fill();
      const body = () => { g.beginPath(); g.moveTo(21, 78); g.bezierCurveTo(19, 52, 33, 33, 50, 33); g.bezierCurveTo(67, 33, 81, 52, 79, 78); g.closePath(); };
      g.fillStyle = linear(g, 30, 35, 70, 80, [[0, "#8ff58a"], [1, "#279c3f"]]); body(); g.fill();
      g.strokeStyle = "#16652a"; g.lineWidth = 1.4; body(); g.stroke();
      g.fillStyle = "rgba(255, 255, 255, 0.45)"; g.beginPath(); g.ellipse(36, 45, 5, 3, -0.6, 0, Math.PI * 2); g.fill();
      for (const x of [41, 59]) {
        g.fillStyle = "#fff"; g.beginPath(); g.ellipse(x, 56, 5, 6, 0, 0, Math.PI * 2); g.fill();
        g.fillStyle = "#14141c"; g.beginPath(); g.arc(x + 1.2, 57, 2.8, 0, Math.PI * 2); g.fill();
        g.fillStyle = "#fff"; g.beginPath(); g.arc(x + 2.2, 55.6, 0.9, 0, Math.PI * 2); g.fill();
      }
      g.strokeStyle = "#14141c"; g.lineWidth = 1.4; g.beginPath(); g.arc(50, 63, 5, 0.2, Math.PI - 0.2); g.stroke();
      g.fillStyle = "rgba(255, 120, 150, 0.45)";
      for (const x of [33, 67]) { g.beginPath(); g.ellipse(x, 64, 3.5, 2, 0, 0, Math.PI * 2); g.fill(); }
      note(g, 16, 28, "#ffe66b"); note(g, 80, 20, "#ffe66b"); note(g, 88, 44, "#7cf2c8");
    },
    space(g, rand) {
      g.fillStyle = radial(g, 30, 30, 5, 110, [[0, "#1b1f5c"], [1, "#05061a"]]); g.fillRect(0, 0, 100, 100);
      g.fillStyle = radial(g, 78, 76, 2, 40, [[0, "rgba(180, 80, 255, 0.35)"], [1, "rgba(180, 80, 255, 0)"]]); g.fillRect(0, 0, 100, 100);
      stars(g, rand, 90, 100);
      const ring = (front) => {
        g.save();
        g.beginPath(); front ? g.rect(0, 52, 100, 48) : g.rect(0, 0, 100, 52); g.clip();
        g.strokeStyle = "#ffe0a3"; g.lineWidth = 2.6; g.beginPath(); g.ellipse(50, 52, 40, 9, -0.22, 0, Math.PI * 2); g.stroke();
        g.strokeStyle = "rgba(200, 150, 90, 0.8)"; g.lineWidth = 1.2; g.beginPath(); g.ellipse(50, 52, 33, 7, -0.22, 0, Math.PI * 2); g.stroke();
        g.restore();
      };
      ring(false);
      g.save(); g.beginPath(); g.arc(50, 52, 22, 0, Math.PI * 2); g.clip();
      g.fillStyle = radial(g, 42, 44, 2, 30, [[0, "#ffd29a"], [0.5, "#e7783a"], [1, "#6e2810"]]); g.fillRect(25, 25, 50, 55);
      g.fillStyle = "rgba(120, 40, 10, 0.35)";
      for (const [y, h] of [[40, 3], [48, 2], [56, 4], [64, 2]]) g.fillRect(25, y, 50, h);
      g.restore();
      ring(true);
      g.fillStyle = "#ffd84a"; g.beginPath(); g.ellipse(80, 22, 6, 4.2, 0, 0, Math.PI * 2); g.fill();
      g.beginPath(); g.arc(84.5, 17.5, 3.4, 0, Math.PI * 2); g.fill();
      g.fillStyle = "#ff8a2a"; g.beginPath(); g.moveTo(87.5, 17.5); g.lineTo(91.5, 18.5); g.lineTo(87.5, 19.6); g.fill();
      g.fillStyle = "#111"; g.beginPath(); g.arc(85.3, 16.6, 0.7, 0, Math.PI * 2); g.fill();
    },
    floppy(g) {
      g.fillStyle = linear(g, 0, 0, 100, 100, [[0, "#1aa39d"], [1, "#0c5f63"]]); g.fillRect(0, 0, 100, 100);
      g.strokeStyle = "rgba(255, 255, 255, 0.06)"; g.lineWidth = 3;
      for (let k = -100; k < 100; k += 9) { g.beginPath(); g.moveTo(k, 0); g.lineTo(k + 100, 100); g.stroke(); }
      g.save(); g.shadowColor = "rgba(0, 0, 0, 0.5)"; g.shadowBlur = 18; g.shadowOffsetY = 6;
      g.fillStyle = "#1b2350"; rounded(g, 18, 13, 64, 74, 3); g.fill(); g.restore();
      g.fillStyle = "#232e66"; rounded(g, 21, 16, 58, 68, 2); g.fill();
      g.fillStyle = linear(g, 34, 13, 66, 38, [[0, "#e6ebf2"], [1, "#8c97a8"]]); g.fillRect(33, 13, 34, 24);
      g.fillStyle = "#1b2350"; g.fillRect(55, 17, 7, 16);
      g.fillStyle = "#f6f2e4"; g.fillRect(26, 50, 48, 32);
      g.fillStyle = "#e84a6a"; g.fillRect(26, 50, 48, 4);
      g.fillStyle = "#9aa6c9";
      for (const y of [59, 65, 71, 77]) g.fillRect(30, y, 30, 0.9);
      g.fillStyle = "#ff4f73"; g.beginPath(); g.moveTo(68, 70); g.bezierCurveTo(62, 66, 62, 61, 66, 61); g.bezierCurveTo(67.5, 61, 68, 62, 68, 63);
      g.bezierCurveTo(68, 62, 68.5, 61, 70, 61); g.bezierCurveTo(74, 61, 74, 66, 68, 70); g.fill();
    },
    lighthouse(g, rand) {
      g.fillStyle = linear(g, 0, 0, 0, 70, [[0, "#070c2b"], [1, "#1f3378"]]); g.fillRect(0, 0, 100, 70);
      stars(g, rand, 40, 50);
      g.fillStyle = "#f6f1cf"; g.beginPath(); g.arc(18, 17, 7, 0, Math.PI * 2); g.fill();
      g.fillStyle = "#0a1032"; g.beginPath(); g.arc(21, 15, 6.2, 0, Math.PI * 2); g.fill();
      g.fillStyle = linear(g, 68, 33, 0, 33, [[0, "rgba(255, 240, 160, 0.75)"], [1, "rgba(255, 240, 160, 0)"]]);
      g.beginPath(); g.moveTo(68, 31); g.lineTo(0, 14); g.lineTo(0, 50); g.lineTo(68, 35); g.fill();
      g.fillStyle = linear(g, 0, 68, 0, 100, [[0, "#14386b"], [1, "#061229"]]); g.fillRect(0, 68, 100, 32);
      g.strokeStyle = "rgba(120, 170, 240, 0.45)"; g.lineWidth = 0.8;
      for (let k = 0; k < 6; k++) { g.beginPath(); for (let x = 0; x <= 100; x += 2) g.lineTo(x, 72 + k * 5 + Math.sin(x / 5 + k) * 0.8); g.stroke(); }
      g.fillStyle = "rgba(255, 230, 120, 0.35)";
      for (let k = 0; k < 7; k++) g.fillRect(64 + Math.sin(k) * 3, 72 + k * 4, 6 - k * 0.6, 1.2);
      g.fillStyle = "#121524"; g.beginPath(); g.moveTo(52, 100); g.lineTo(56, 78); g.lineTo(70, 74); g.lineTo(86, 79); g.lineTo(92, 100); g.fill();
      g.save(); g.beginPath(); g.moveTo(62, 76); g.lineTo(64, 38); g.lineTo(72, 38); g.lineTo(74, 76); g.clip();
      for (let k = 0; k < 8; k++) { g.fillStyle = k % 2 ? "#f4f1e6" : "#e0453a"; g.fillRect(60, 38 + k * 5, 16, 5); }
      g.restore();
      g.save(); g.shadowColor = "#ffe66b"; g.shadowBlur = 24; g.fillStyle = "#ffe66b"; g.fillRect(63.5, 30, 9, 8); g.restore();
      g.fillStyle = "#2a2a3a"; g.fillRect(62, 37.5, 12, 1.6); g.fillRect(62, 29, 12, 1.4);
      g.fillStyle = "#9b2a22"; g.beginPath(); g.moveTo(61.5, 29.5); g.lineTo(68, 23); g.lineTo(74.5, 29.5); g.fill();
    },
    city(g, rand) {
      g.fillStyle = linear(g, 0, 0, 0, 100, [[0, "#1d0b3a"], [0.6, "#6a1f62"], [1, "#e0566a"]]); g.fillRect(0, 0, 100, 100);
      stars(g, rand, 30, 40);
      g.fillStyle = "#3b3f4a"; g.fillRect(28, 38, 1.4, 14); g.fillRect(70.6, 38, 1.4, 14);
      g.fillStyle = "#101318"; g.fillRect(24, 10, 52, 30);
      g.strokeStyle = "#2b3140"; g.lineWidth = 1.2; g.strokeRect(24, 10, 52, 30);
      g.save(); g.shadowColor = "#3dff8a"; g.shadowBlur = 22; g.strokeStyle = "#3dff8a"; g.lineWidth = 4; g.lineCap = "round"; g.lineJoin = "round";
      g.beginPath(); g.moveTo(37, 25); g.lineTo(46, 33); g.lineTo(63, 16); g.stroke(); g.restore();
      g.strokeStyle = "#d7ffe6"; g.lineWidth = 1.2; g.lineCap = "round";
      g.beginPath(); g.moveTo(37, 25); g.lineTo(46, 33); g.lineTo(63, 16); g.stroke();
      let x = 0;
      while (x < 100) {
        const w = 7 + rand() * 9, h = 22 + rand() * 32;
        g.fillStyle = `hsl(${250 + rand() * 30} 30% ${7 + rand() * 6}%)`; g.fillRect(x, 100 - h, w, h);
        for (let wy = 100 - h + 3; wy < 98; wy += 4) for (let wx = x + 1.5; wx < x + w - 2; wx += 3) {
          if (rand() > 0.5) { g.fillStyle = rand() > 0.3 ? "rgba(255, 216, 107, 0.9)" : "rgba(124, 255, 176, 0.85)"; g.fillRect(wx, wy, 1.4, 1.8); }
        }
        x += w + (rand() > 0.7 ? 1.5 : 0);
      }
    },
    vinyl(g, rand) {
      const hue = Math.floor(rand() * 360);
      g.fillStyle = linear(g, 0, 0, 100, 100, [[0, `hsl(${hue} 70% 55%)`], [1, `hsl(${(hue + 60) % 360} 70% 30%)`]]); g.fillRect(0, 0, 100, 100);
      g.fillStyle = "#111"; g.beginPath(); g.arc(62, 50, 34, 0, Math.PI * 2); g.fill();
      g.strokeStyle = "rgba(255, 255, 255, 0.08)"; g.lineWidth = 0.6;
      for (let r = 14; r < 33; r += 2.5) { g.beginPath(); g.arc(62, 50, r, 0, Math.PI * 2); g.stroke(); }
      g.fillStyle = `hsl(${(hue + 180) % 360} 70% 60%)`; g.beginPath(); g.arc(62, 50, 11, 0, Math.PI * 2); g.fill();
      g.fillStyle = "#111"; g.beginPath(); g.arc(62, 50, 1.6, 0, Math.PI * 2); g.fill();
      g.fillStyle = "rgba(255, 255, 255, 0.92)"; g.fillRect(8, 12, 30, 76);
      g.fillStyle = `hsl(${hue} 50% 30%)`;
      for (let k = 0; k < 5; k++) g.fillRect(12, 20 + k * 12, 22 - k * 3, 3);
    },
    tape(g, rand) {
      const hue = Math.floor(rand() * 360);
      g.fillStyle = `hsl(${hue} 45% 18%)`; g.fillRect(0, 0, 100, 100);
      for (let k = 0; k < 8; k++) { g.fillStyle = `hsl(${(hue + k * 25) % 360} 75% ${45 + (k % 3) * 8}%)`; g.fillRect(0, 18 + k * 8, 100, 4); }
      g.fillStyle = "#26272e"; rounded(g, 14, 30, 72, 44, 4); g.fill();
      g.fillStyle = "#f4f1e6"; g.fillRect(20, 35, 60, 20);
      g.fillStyle = "#111"; g.fillRect(34, 58, 32, 10);
      for (const cx of [40, 60]) { g.fillStyle = "#e9e9ee"; g.beginPath(); g.arc(cx, 63, 4, 0, Math.PI * 2); g.fill(); }
      g.fillStyle = `hsl(${hue} 60% 40%)`; g.fillRect(24, 40, 40, 2.5); g.fillRect(24, 46, 28, 2.5);
    },
  };

  const coverCache = new Map();
  /** A demo cover as a 256 px canvas, made once. */
  function cover(name) {
    if (coverCache.has(name)) return coverCache.get(name);
    const c = document.createElement("canvas");
    c.width = c.height = SIZE;
    const g = c.getContext("2d");
    g.scale(SIZE / 100, SIZE / 100);
    const [kind, seed] = String(name).split(".");
    (COVERS[kind] || COVERS.vinyl)(g, rng(name + (seed || "")));
    coverCache.set(name, c);
    return c;
  }

  /* ------------------------------------------------------------ the made-up library */

  const ARTISTS = [
    ["The Pixel Tides", "synthwave"], ["Bit Lagoon", "chiptune"], ["Gelatin Groove", "disco"], ["Captain Lint", "space funk"],
    ["Magnetic Avenue", "city pop"], ["Harbor Static", "lo-fi"], ["Squash and the Rebasers", "garage"], ["Sir Wobbleton 3000", "bounce"],
    ["The Dial-Up Duchess", "dream pop"], ["Scanline Sisters", "electro"],
  ].map(([name, genre], i) => ({ id: `demoArtist${i + 1}`, name, genres: [genre, "made up"] }));
  const ALBUM_LIST = [
    ["Night Drive EP", 0, "2023", "sunset", "Neon Harbor"], ["Save Slot One", 1, "2022", "heart", "Cartridge Hearts"],
    ["Bouncy Castle", 2, "2024", "slime", "Slime Time Boogie"], ["Debug Odyssey", 3, "2021", "space", "Orbit of the Rubber Duck"],
    ["1.44 MB of Love", 4, "2020", "floppy", "Floppy Disk Romance"], ["Foghorn Tapes", 5, "2023", "lighthouse", "Lighthouse Lo-Fi"],
    ["All Checks Passed", 6, "2024", "city", "Green Build Groove"], ["Wobble Theory", 7, "2022", "vinyl.a", "Jelly Physics"],
    ["Modem Lullabies", 8, "2019", "tape.b", "Handshake Serenade"], ["Phosphor Nights", 9, "2025", "vinyl.c", "Green Glow Waltz"],
  ];
  const WORD_A = ["Bitmap", "Rubber Duck", "Semicolon", "Joystick", "Pixel Puddle", "Cache Miss", "Tab Key", "Patch Notes", "Hot Reload", "Coin Slot",
    "Save Point", "Cursor", "Dial-Up", "Byte Order", "Scanline", "Hex Key", "Sprite Sheet", "Null Island", "Lint Roller", "Keycap"];
  const WORD_B = ["Polka", "Waltz", "Samba", "Shuffle", "Lullaby", "Boogie", "Serenade", "Stomp", "Two-Step", "Rhapsody",
    "Mambo", "Jig", "Bossa", "Tango", "Hop", "Swing", "Cha-Cha", "Reel", "Ballad", "Disco"];
  const ALBUMS = [], TRACKS = [];
  {
    const rand = rng("pixeldeck library");
    const used = new Set();
    let n = 0;
    ALBUM_LIST.forEach(([name, artist, year, coverName, hit], a) => {
      const album = { id: `demoAlbum${a + 1}`, name, artist, year, cover: coverName, tracks: [] };
      ALBUMS.push(album);
      for (let k = 0; k < 8; k++) {
        let title = k === 0 ? hit : "";
        while (!title || used.has(title)) title = `${WORD_A[Math.floor(rand() * WORD_A.length)]} ${WORD_B[Math.floor(rand() * WORD_B.length)]}`;
        used.add(title);
        const guest = a === 2 && k === 0 ? [7] : rand() > 0.88 ? [Math.floor(rand() * ARTISTS.length)] : [];
        const people = [artist, ...guest.filter((g) => g !== artist)];
        const track = { id: `demoTrack${++n}`, name: title, people, album: a, number: k + 1, duration_ms: (140 + Math.floor(rand() * 140)) * 1000 };
        TRACKS.push(track);
        album.tracks.push(track);
      }
    });
  }
  const pick = (seed, count) => {
    const rand = rng(seed), pool = TRACKS.map((t) => t.id);
    for (let i = pool.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [pool[i], pool[j]] = [pool[j], pool[i]]; }
    return pool.slice(0, count);
  };
  const PLAYLISTS = [
    ["demoFocus", "Focus Mode: Pixels", "You", 24], ["demoMerge", "Merge Day Anthems", "You", 18], ["demoDuck", "Rubber Duck Radio", "Pixel Pat", 31],
    ["demoRebase", "Late Night Rebase", "You", 12], ["demoGreen", "Green Build Grooves", "Robo Rita", 27], ["demoStandup", "Standup Shuffle", "You", 9],
  ].map(([id, name, owner, count]) => ({ id, name, owner, tracks: pick(id, count) }));
  PLAYLISTS[0].tracks = [TRACKS[0].id, ...PLAYLISTS[0].tracks.filter((t) => t !== TRACKS[0].id)].slice(0, 24);
  const byId = new Map(TRACKS.map((t) => [t.id, t]));
  const liked = new Set(pick("liked", 64));
  liked.add(TRACKS[0].id);
  const RECENT = pick("recent", 22);
  const SAVED_ALBUMS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];

  const art = (album) => `demo:${album.cover}`;
  function song(t) {
    const album = ALBUMS[t.album];
    return {
      id: t.id, uri: `spotify:track:${t.id}`, kind: "track", name: t.name, artists: t.people.map((p) => ARTISTS[p].name),
      artist_ids: t.people.map((p) => ARTISTS[p].id), album: album.name, album_id: album.id, number: t.number,
      duration_ms: t.duration_ms, playable: true, art: art(album), thumb: art(album),
    };
  }
  const albumCard = (a) => ({ id: a.id, uri: `spotify:album:${a.id}`, name: a.name, artists: [ARTISTS[a.artist].name], artist_ids: [ARTISTS[a.artist].id],
    year: a.year, tracks: a.tracks.length, art: art(a), thumb: art(a) });
  const listCard = (p) => { const first = byId.get(p.tracks[0]); return { id: p.id, uri: `spotify:playlist:${p.id}`, name: p.name, owner: p.owner,
    by_spotify: false, tracks: p.tracks.length, art: art(ALBUMS[first.album]), thumb: art(ALBUMS[first.album]) }; };
  const artistCard = (a, i) => ({ id: a.id, uri: `spotify:artist:${a.id}`, name: a.name, genres: a.genres, art: art(ALBUMS[ALBUM_LIST.findIndex((x) => x[1] === i)] || ALBUMS[0]),
    thumb: art(ALBUMS[ALBUM_LIST.findIndex((x) => x[1] === i)] || ALBUMS[0]) });
  function page(all, offset, size = 50) {
    const items = all.slice(offset, offset + size);
    return { items, next: offset + size < all.length ? offset + size : null, total: all.length };
  }
  const idOf = (uri) => String(uri || "").split(":").pop();

  /* ------------------------------------------------------------ playback on a local clock */

  const DEVICES = [
    { id: "demoDesk", name: "Desk Speakers", type: "Computer", volume: 70, supports_volume: true },
    { id: "demoPhone", name: "Pocket Phone", type: "Smartphone", volume: 40, supports_volume: true },
    { id: "demoKitchen", name: "Kitchen Radio", type: "Speaker", volume: 55, supports_volume: true },
  ];
  let s = null;
  function reset() {
    s = { list: PLAYLISTS[0].tracks.slice(), i: 0, queue: [], current: PLAYLISTS[0].tracks[0], playing: true, pos: 71000, at: performance.now(),
      shuffle: false, repeat: "context", volume: 70, device: "demoDesk", context: { type: "playlist", id: PLAYLISTS[0].id, uri: `spotify:playlist:${PLAYLISTS[0].id}` } };
  }
  const now_ = () => { if (!s) reset(); };
  function advance(dir) {
    if (dir > 0 && s.queue.length) { s.current = s.queue.shift(); s.pos = 0; return; }
    const step = s.shuffle && dir > 0 ? 3 : dir;
    s.i = (s.i + step + s.list.length) % s.list.length;
    s.current = s.list[s.i];
    s.pos = 0;
  }
  function clock() {
    const t = performance.now();
    if (s.playing) s.pos += t - s.at;
    s.at = t;
    for (let guard = 0; s.pos >= byId.get(s.current).duration_ms && guard < 50; guard++) {
      const over = s.pos - byId.get(s.current).duration_ms;
      if (s.repeat === "track") s.pos = over; else { advance(1); s.pos = over; }
    }
  }
  function now() {
    now_();
    clock();
    const dev = DEVICES.find((d) => d.id === s.device);
    return {
      connected: true, active: true, is_playing: s.playing, progress_ms: Math.floor(s.pos), shuffle: s.shuffle, repeat: s.repeat,
      device: { ...dev, active: true, restricted: false, volume: s.volume }, item: song(byId.get(s.current)),
      playlist: s.context?.type === "playlist" ? s.context.id : null, context: s.context, disallows: [],
    };
  }
  async function control(action, value) {
    now_();
    clock();
    if (action === "play") s.playing = true;
    else if (action === "pause") s.playing = false;
    else if (action === "next") advance(1);
    else if (action === "previous") { if (s.pos > 3000) s.pos = 0; else advance(-1); }
    else if (action === "seek") s.pos = value;
    else if (action === "volume") s.volume = value;
    else if (action === "shuffle") s.shuffle = value;
    else if (action === "repeat") s.repeat = value;
    return { ok: true };
  }
  function contextTracks(uri) {
    const [, kind, id] = String(uri).split(":");
    if (kind === "playlist") return PLAYLISTS.find((p) => p.id === id)?.tracks || [];
    if (kind === "album") return ALBUMS.find((a) => a.id === id)?.tracks.map((t) => t.id) || [];
    if (kind === "artist") { const i = ARTISTS.findIndex((a) => a.id === id); return TRACKS.filter((t) => t.people[0] === i).map((t) => t.id); }
    return [];
  }
  async function play({ context, uris, offset_uri }) {
    now_();
    clock();
    const list = context ? contextTracks(context) : (uris || []).map(idOf).filter((id) => byId.has(id));
    if (!list.length) throw new Error("Nothing to play there.");
    const start = offset_uri ? Math.max(0, list.indexOf(idOf(offset_uri))) : 0;
    s.list = list; s.i = start; s.current = list[start]; s.pos = 0; s.playing = true;
    const [, kind, id] = String(context || "").split(":");
    s.context = context ? { type: kind, id, uri: context } : null;
    return { ok: true };
  }

  function search(q, type, offset) {
    const needle = q.trim().toLowerCase();
    const hit = (...texts) => texts.some((t) => String(t).toLowerCase().includes(needle));
    const all = {
      tracks: TRACKS.filter((t) => hit(t.name, ALBUMS[t.album].name, ...t.people.map((p) => ARTISTS[p].name))).map(song),
      albums: ALBUMS.filter((a) => hit(a.name, ARTISTS[a.artist].name)).map(albumCard),
      artists: ARTISTS.map((a, i) => [a, i]).filter(([a]) => hit(a.name, ...a.genres)).map(([a, i]) => artistCard(a, i)),
      playlists: PLAYLISTS.filter((p) => hit(p.name, p.owner)).map(listCard),
    };
    if (type === "all") return Object.fromEntries(Object.entries(all).map(([k, v]) => [k, page(v, 0, 10)]));
    return { [`${type}s`]: page(all[`${type}s`], offset, 10) };
  }

  const source = {
    now: async () => now(), control, reset,
    devices: async () => { now_(); return DEVICES.map((d) => ({ ...d, active: d.id === s.device, volume: d.id === s.device ? s.volume : d.volume })); },
    transfer: async (id) => { now_(); const d = DEVICES.find((x) => x.id === id); if (d) { s.device = id; s.volume = d.volume; } return { ok: true }; },
    playlists: async (offset = 0) => page(PLAYLISTS.map(listCard), offset),
    playlist: async (id, offset = 0) => {
      const p = PLAYLISTS.find((x) => x.id === id);
      if (!p) throw new Error("No such playlist in the preview.");
      return { ...page(p.tracks.map((t) => song(byId.get(t))), offset), playlist: offset ? null : listCard(p) };
    },
    liked: async (offset = 0) => page(TRACKS.filter((t) => liked.has(t.id)).map(song), offset),
    albums: async (offset = 0) => page(SAVED_ALBUMS.map((i) => albumCard(ALBUMS[i])), offset),
    album: async (id, offset = 0) => { const a = ALBUMS.find((x) => x.id === id); return { ...page(a.tracks.map(song), offset), album: albumCard(a) }; },
    recent: async () => ({ items: RECENT.map((t) => song(byId.get(t))), before: null, next: null, total: RECENT.length }),
    search: async (q, type = "all", offset = 0) => search(q, type, offset),
    artist: async (id, offset = 0) => {
      const i = ARTISTS.findIndex((a) => a.id === id);
      return { ...page(ALBUMS.filter((a) => a.artist === i).map(albumCard), offset, 10), artist: offset ? null : artistCard(ARTISTS[i], i) };
    },
    queue: async () => {
      now_(); clock();
      const upcoming = [...s.queue, ...Array.from({ length: Math.min(10, s.list.length - 1) }, (_, k) => s.list[(s.i + 1 + k) % s.list.length])];
      return { current: song(byId.get(s.current)), items: upcoming.map((t) => song(byId.get(t))) };
    },
    queueAdd: async (uri) => { now_(); const id = idOf(uri); if (byId.has(id)) s.queue.push(id); return { ok: true }; },
    likedCheck: async (ids) => ({ liked: Object.fromEntries(ids.map((id) => [id, liked.has(id)])) }),
    like: async (ids, on) => { for (const id of ids) on ? liked.add(id) : liked.delete(id); return { ok: true, liked: on, ids }; },
    play,
    art: async (item) => cover(String(item.art || item.thumb).slice(5)),
  };

  return { source, cover, rng };
})();
