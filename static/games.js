"use strict";

/* Three small arcade games for a PixelDeck panel: Tetris, Snake and Flappy, starring the
   two mascots. The blue bot is PixelDeck's own deterministic hand; the green slime stands
   for AI. Each game draws on a canvas at a small logical size that CSS scales by a whole
   number, so pixels stay square. The animation frame loop only runs while a game is played:
   a title screen, a pause or a hidden panel runs no script (only the stepped CSS blink of
   PRESS SPACE on the title screen). */

const Games = (() => {
  const NAMES = Object.freeze(["tetris", "snake", "flappy"]);
  const TITLES = { tetris: "Tetris", snake: "Snake", flappy: "Flappy" };
  const STEP = 1000 / 120; // fixed simulation step (ms), so game speed never follows the refresh rate
  const RETRY_LOCK = 600; // Space mashed at the moment of death should not skip the game over screen
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const calm = () => motion.matches;
  const live = new Set();
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const mod = (a, n) => ((a % n) + n) % n;

  /* ------------------------------------------------------------- best scores */
  const bestKey = (name) => `prdesk.games.best.${name}`;
  function loadBest(name) {
    try { return Math.max(0, parseInt(localStorage.getItem(bestKey(name)), 10) || 0); } catch { return 0; }
  }
  function saveBest(name, value) {
    try { localStorage.setItem(bestKey(name), String(value)); } catch { /* blocked storage: the best lives for this visit only */ }
  }

  /* ----------------------------------------------------------------- colours */
  // Tokens resolve once the stylesheet is in. An empty value would make the canvas silently
  // keep its previous fill, so nothing is cached until every token has a value.
  const TOKENS = {
    bg: "--color-paper-3", well: "--color-paper", grid: "--color-paper-2", rule2: "--color-rule-2", ink: "--color-ink",
    muted: "--color-muted", cool: "--color-cool", dark: "--sprite-dark", white: "--sprite-white", blue: "--sprite-blue",
    yellow: "--sprite-yellow", red: "--sprite-red", grass: "--sprite-grass", orange: "--sprite-orange",
    orangeDeep: "--sprite-orange-deep", stone: "--sprite-stone", stoneDeep: "--sprite-stone-deep",
  };
  let palette = null;
  function colors() {
    if (palette) return palette;
    const css = getComputedStyle(document.documentElement);
    const out = {};
    for (const [k, v] of Object.entries(TOKENS)) out[k] = css.getPropertyValue(v).trim();
    if (Object.values(out).every(Boolean)) palette = out;
    return out;
  }

  /* ----------------------------------------------------------------- sprites */
  // Sprites.grid reads computed styles on every call, so each frame is drawn once into a
  // tiny offscreen canvas and blitted from there.
  const sheet = new Map();
  function sprite(name, frame = 0, flip = "") {
    const key = `${name}:${frame}:${flip}`;
    if (sheet.has(key)) return sheet.get(key);
    const grid = typeof Sprites !== "undefined" ? Sprites.grid(name, frame) : null;
    if (!grid) return null;
    const c = document.createElement("canvas");
    c.width = grid.width;
    c.height = grid.height;
    const x = c.getContext("2d");
    let whole = true;
    grid.rows.forEach((row, r) => {
      for (let i = 0; i < row.length; i++) {
        if (row[i] === ".") continue;
        const fill = grid.colors[row[i]];
        if (!fill) { whole = false; continue; }
        x.fillStyle = fill;
        x.fillRect(flip.includes("x") ? grid.width - 1 - i : i, flip.includes("y") ? grid.height - 1 - r : r, 1, 1);
      }
    });
    if (whole) sheet.set(key, c);
    return c;
  }
  function blit(g, name, frame, x, y, flip = "") {
    const c = sprite(name, frame, flip);
    if (c) g.drawImage(c, Math.round(x), Math.round(y));
  }

  /* -------------------------------------------- a 3 by 5 font for the labels */
  // Only what the Tetris side panels print. Each digit of a glyph is one row, its bits the pixels.
  const GLYPHS = {
    0: "75557", 1: "26227", 2: "61247", 3: "61216", 4: "55711", 5: "74616", 6: "34652", 7: "71222", 8: "25252", 9: "25316",
    E: "74647", I: "72227", L: "44447", N: "9db99", S: "34216", T: "72222", V: "55552", X: "55255",
  };
  const glyphW = (ch) => (ch === "N" ? 4 : 3);
  const textW = (str) => [...str].reduce((w, ch) => w + glyphW(ch) + 1, -1);
  function text(g, str, cx, y, fill) {
    str = String(str);
    let x = Math.round(cx - textW(str) / 2);
    g.fillStyle = fill;
    for (const ch of str) {
      const rows = GLYPHS[ch], w = glyphW(ch);
      if (rows) {
        for (let r = 0; r < 5; r++) {
          const bits = parseInt(rows[r], 16);
          for (let c = 0; c < w; c++) if (bits & (1 << (w - 1 - c))) g.fillRect(x + c, y + r, 1, 1);
        }
      }
      x += w + 1;
    }
  }

  function rng(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), a | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ================================================================== TETRIS */
  const COLS = 10, ROWS = 20;
  const PIECES = {
    I: ["....", "IIII", "....", "...."], J: ["J..", "JJJ", "..."], L: ["..L", "LLL", "..."], O: ["OO", "OO"],
    S: [".SS", "SS.", "..."], T: [".T.", "TTT", "..."], Z: ["ZZ.", ".ZZ", "..."],
  };
  const PIECE_FILL = { I: "cool", J: "blue", L: "orange", O: "yellow", S: "grass", T: "stone", Z: "red" };
  // Simple kicks: in place, one step aside, one up; only the long bar may shove two cells.
  const KICKS = [[0, 0], [-1, 0], [1, 0], [0, -1], [-1, -1], [1, -1], [-2, 0], [2, 0]];
  const CLEAR_POINTS = [0, 100, 300, 500, 800];

  function tetris(api) {
    const W = 112, H = 102, C = 5;
    const BX = Math.floor((W - COLS * C) / 2), BY = Math.floor((H - ROWS * C) / 2);
    const RX = BX + COLS * C + 1;
    let s = null;

    const shape = (t) => PIECES[t].map((row) => [...row].map((ch) => ch !== "."));
    const rotate = (m, dir) => m.map((row, r) => row.map((_, c) => (dir > 0 ? m[m.length - 1 - c][r] : m[c][m.length - 1 - r])));
    const gravity = () => Math.max(60, Math.round(800 * 0.82 ** (s.level - 1)));
    function* cells(m, x, y) {
      for (let r = 0; r < m.length; r++) for (let c = 0; c < m[r].length; c++) if (m[r][c]) yield [x + c, y + r];
    }
    function fits(m, x, y) {
      for (const [cx, cy] of cells(m, x, y)) {
        if (cx < 0 || cx >= COLS || cy >= ROWS) return false;
        if (cy >= 0 && s.board[cy][cx]) return false; // above the top is open sky
      }
      return true;
    }
    function bag() {
      const b = Object.keys(PIECES);
      for (let i = b.length - 1; i > 0; i--) { const j = api.int(i + 1); [b[i], b[j]] = [b[j], b[i]]; }
      return b;
    }
    function take() {
      if (!s.bag.length) s.bag = bag();
      return s.bag.pop();
    }
    function spawn() {
      const t = s.next, m = shape(t);
      s.piece = { t, m, x: Math.floor((COLS - m.length) / 2), y: t === "I" ? -1 : 0 };
      s.next = take();
      s.fall = 0; s.lock = 0; s.resets = 0;
      if (!fits(s.piece.m, s.piece.x, s.piece.y)) { s.dead = true; api.over(); }
    }
    const grounded = () => !fits(s.piece.m, s.piece.x, s.piece.y + 1);
    // A move or turn on the floor buys a little more time, but only so often, or a piece could stall forever.
    function touched() {
      if (s.resets < 15 && grounded()) { s.lock = 0; s.resets++; }
    }
    function shift(dx, dy) {
      const p = s.piece;
      if (!p || !fits(p.m, p.x + dx, p.y + dy)) return false;
      p.x += dx; p.y += dy;
      if (dx) touched();
      return true;
    }
    function turn(dir) {
      const p = s.piece;
      if (!p || p.t === "O") return false;
      const m = rotate(p.m, dir);
      for (const [dx, dy] of KICKS) {
        if (Math.abs(dx) === 2 && p.t !== "I") continue;
        if (fits(m, p.x + dx, p.y + dy)) { p.m = m; p.x += dx; p.y += dy; touched(); return true; }
      }
      return false;
    }
    function drop() {
      const p = s.piece;
      if (!p) return;
      let n = 0;
      while (fits(p.m, p.x, p.y + 1)) { p.y++; n++; }
      if (n) api.add(n * 2);
      settle();
    }
    function settle() {
      const p = s.piece;
      let above = false;
      for (const [x, y] of cells(p.m, p.x, p.y)) { if (y < 0) above = true; else s.board[y][x] = p.t; }
      s.piece = null;
      if (above) { s.dead = true; api.over(); return; }
      const full = [];
      s.board.forEach((row, y) => { if (row.every(Boolean)) full.push(y); });
      if (!full.length) { spawn(); return; }
      api.add(CLEAR_POINTS[full.length] * s.level); // the level before this clear sets the price
      s.lines += full.length;
      s.level = 1 + Math.floor(s.lines / 10);
      s.clearing = { rows: full, t: 0 };
      s.cheer = 800;
      if (full.length === 4) s.jump = 900;
    }

    return {
      fit: () => ({ W, H }),
      reset() {
        s = { board: Array.from({ length: ROWS }, () => Array(COLS).fill(null)), bag: [], piece: null, next: null, lines: 0, level: 1,
          fall: 0, lock: 0, resets: 0, dir: 0, das: 0, soft: false, clearing: null, t: 0, cheer: 0, jump: 0, dead: false };
        s.next = take();
      },
      begin: spawn,
      update(ms) {
        s.t += ms;
        s.cheer = Math.max(0, s.cheer - ms);
        s.jump = Math.max(0, s.jump - ms);
        if (s.clearing) {
          s.clearing.t += ms;
          if (s.clearing.t < 240) return;
          // ascending order keeps the indices of the rows still to remove valid
          for (const y of s.clearing.rows) { s.board.splice(y, 1); s.board.unshift(Array(COLS).fill(null)); }
          s.clearing = null;
          spawn();
          return;
        }
        if (!s.piece) return;
        // a held arrow moves once at once, then repeats after a short delay
        if (s.dir) {
          s.das += ms;
          while (s.das >= 170) { s.das -= 45; if (!shift(s.dir, 0)) break; }
        }
        const every = s.soft ? Math.min(35, gravity()) : gravity();
        s.fall += ms;
        while (s.fall >= every) {
          s.fall -= every;
          if (!shift(0, 1)) { s.fall = 0; break; }
          if (s.soft) api.add(1);
        }
        if (!s.piece) return;
        if (grounded()) { s.lock += ms; if (s.lock >= 500) settle(); } else s.lock = 0;
      },
      key(a, down) {
        if (a === "left" || a === "right") {
          const d = a === "left" ? -1 : 1;
          if (!down) { if (s.dir === d) s.dir = 0; return; }
          s.dir = d; s.das = 0;
          shift(d, 0);
        } else if (a === "down") s.soft = down;
        else if (!down) return;
        else if (a === "up" || a === "rotate") turn(1);
        else if (a === "rotateBack") turn(-1);
        else if (a === "space") drop();
      },
      release() { if (s) { s.dir = 0; s.soft = false; } },
      intro: () => ({ top: ["ARROWS MOVE", "UP TURNS", "SPACE DROPS"], bottom: [] }),
      summary: () => [`LINES ${s.lines}`],
      state: () => s,
      draw(g, P, mode) {
        g.fillStyle = P.well;
        g.fillRect(0, 0, W, H);
        g.fillStyle = P.rule2;
        g.fillRect(BX - 1, BY - 1, COLS * C + 2, ROWS * C + 2);
        g.fillStyle = P.bg;
        g.fillRect(BX, BY, COLS * C, ROWS * C);
        g.fillStyle = P.grid;
        for (let y = 1; y < ROWS; y++) for (let x = 1; x < COLS; x++) g.fillRect(BX + x * C, BY + y * C, 1, 1);
        s.board.forEach((row, y) => row.forEach((t, x) => { if (t) block(g, P, BX + x * C, BY + y * C, C, P[PIECE_FILL[t]]); }));
        if (s.clearing && (calm() || Math.floor(s.clearing.t / 60) % 2 === 0)) {
          g.fillStyle = P.white;
          for (const y of s.clearing.rows) g.fillRect(BX, BY + y * C, COLS * C, C);
        }
        const p = s.piece;
        if (p) {
          let gy = p.y;
          while (fits(p.m, p.x, gy + 1)) gy++;
          g.fillStyle = P.rule2;
          for (const [x, y] of cells(p.m, p.x, gy)) if (y >= 0) outline(g, BX + x * C, BY + y * C, C);
          for (const [x, y] of cells(p.m, p.x, p.y)) if (y >= 0) block(g, P, BX + x * C, BY + y * C, C, P[PIECE_FILL[p.t]]);
        }
        // side panels: level and lines on the left, the next piece on the right
        const lx = Math.floor((BX - 1) / 2), rx = RX + Math.floor((W - RX) / 2);
        text(g, "LEVEL", lx, 4, P.muted);
        text(g, s.level, lx, 11, P.ink);
        text(g, "LINES", lx, 22, P.muted);
        text(g, s.lines, lx, 29, P.ink);
        text(g, "NEXT", rx, 4, P.muted);
        g.fillStyle = P.rule2;
        g.fillRect(rx - 12, 11, 24, 14);
        g.fillStyle = P.bg;
        g.fillRect(rx - 11, 12, 22, 12);
        if (s.next) {
          const m = shape(s.next), pc = 4;
          const on = [...cells(m, 0, 0)];
          const minX = Math.min(...on.map((c) => c[0])), maxX = Math.max(...on.map((c) => c[0]));
          const minY = Math.min(...on.map((c) => c[1])), maxY = Math.max(...on.map((c) => c[1]));
          const ox = rx - Math.round(((maxX - minX + 1) * pc) / 2), oy = 18 - Math.round(((maxY - minY + 1) * pc) / 2);
          for (const [x, y] of on) block(g, P, ox + (x - minX) * pc, oy + (y - minY) * pc, pc, P[PIECE_FILL[s.next]]);
        }
        cheerleaders(g, mode, lx, rx);
      },
    };

    // The bot follows the falling piece with its eyes and beams at a clear; the slime jumps for a four-line clear.
    function cheerleaders(g, mode, lx, rx) {
      const bob = !calm() && mode === "play" ? Math.floor(s.t / 420) % 2 : 0;
      const p = s.piece;
      let bot = 0;
      if (s.dead) bot = 3;
      else if (s.cheer > 0) bot = 4;
      else if (s.t % 3300 < 140) bot = 1;
      else if (p && BY + (p.y + 1.5) * C < H - 10) bot = 7;
      blit(g, "bot", bot, lx - 6, H - 15 - bob);

      let slime = 0, flip = "", lift = 0;
      if (s.dead) slime = 3;
      else if (s.jump > 0) {
        const k = 1 - s.jump / 900;
        slime = k < 0.14 || k > 0.88 ? 1 : 0;
        if (!calm()) lift = Math.round(Math.sin(Math.min(1, Math.max(0, (k - 0.14) / 0.74)) * Math.PI) * 16);
      } else if ((s.t + 1700) % 3700 < 140) slime = 3;
      else if (p && p.x + 1.5 < COLS / 2) { slime = 2; flip = "x"; }
      blit(g, "buddy", slime, rx - 8, H - 12 - lift - (bob ? 0 : 1), flip);
    }
  }

  function block(g, P, x, y, c, fill) {
    g.fillStyle = fill;
    g.fillRect(x, y, c, c);
    g.globalAlpha = 0.4;
    g.fillStyle = P.white;
    g.fillRect(x, y, c - 1, 1);
    g.fillRect(x, y + 1, 1, c - 2);
    g.globalAlpha = 0.45;
    g.fillStyle = P.dark;
    g.fillRect(x + 1, y + c - 1, c - 1, 1);
    g.fillRect(x + c - 1, y, 1, c - 1);
    g.globalAlpha = 1;
  }
  function outline(g, x, y, c) {
    g.fillRect(x, y, c, 1);
    g.fillRect(x, y + c - 1, c, 1);
    g.fillRect(x, y + 1, 1, c - 2);
    g.fillRect(x + c - 1, y + 1, 1, c - 2);
  }

  /* =================================================================== SNAKE */
  const CELL = 8;
  const DIRS = { up: { x: 0, y: -1 }, down: { x: 0, y: 1 }, left: { x: -1, y: 0 }, right: { x: 1, y: 0 } };
  const TWINKLE = [2, 3, 4, 3];

  function snake(api) {
    let s = null;
    const onBody = (x, y, skipTail) => s.body.some((b, i) => b.x === x && b.y === y && !(skipTail && i === s.body.length - 1));
    const inBot = (x, y) => { const b = s.bonus; return !!b && x >= b.x && x < b.x + 2 && y >= b.y && y < b.y + 2; };
    const isFood = (x, y) => !!s.food && s.food.x === x && s.food.y === y;
    // A free n by n square: off the body, the food and the bot; the bot also keeps its distance from the head.
    function free(n) {
      const head = s.body[0];
      const ok = (x, y, near) => {
        if (near && Math.abs(x - head.x) + Math.abs(y - head.y) < 4) return false;
        for (let dy = 0; dy < n; dy++) for (let dx = 0; dx < n; dx++) {
          if (onBody(x + dx, y + dy) || inBot(x + dx, y + dy) || isFood(x + dx, y + dy)) return false;
        }
        return true;
      };
      for (let i = 0; i < 80; i++) {
        const x = api.int(s.cols - n + 1), y = api.int(s.rows - n + 1);
        if (ok(x, y, n > 1)) return { x, y };
      }
      for (let y = 0; y <= s.rows - n; y++) for (let x = 0; x <= s.cols - n; x++) if (ok(x, y, false)) return { x, y };
      return null;
    }
    function die() { s.dead = true; api.over(); }
    function step() {
      if (s.queue.length) s.dir = s.queue.shift();
      const head = s.body[0], nx = head.x + s.dir.x, ny = head.y + s.dir.y;
      if (nx < 0 || ny < 0 || nx >= s.cols || ny >= s.rows) return die();
      const eats = isFood(nx, ny), catches = inBot(nx, ny), grows = eats || catches;
      if (onBody(nx, ny, !grows)) return die(); // the tail cell is free unless the snake grows this step
      s.body.unshift({ x: nx, y: ny });
      if (!grows) s.body.pop();
      if (catches) { s.bonus = null; s.chew = 260; api.add(50); }
      if (eats) {
        s.eaten++;
        s.chew = 160;
        api.add(10);
        s.food = null;
        s.food = free(1);
        if (!s.food) return die(); // the board is full: nothing left to eat
        if (s.eaten % 5 === 0 && !s.bonus) {
          const at = free(2);
          if (at) s.bonus = { ...at, ttl: 7000, walk: 0, dx: api.int(2) ? 1 : -1, dy: 0 };
        }
      }
    }
    // The bot strolls one cell at a time, now and then turning aside, never onto the snake or
    // the food. It only turns back when cornered, so it walks rather than jitters on the spot.
    function wander(b) {
      const ahead = [b.dx, b.dy], back = [-b.dx, -b.dy];
      const sides = api.int(2) ? [[b.dy, b.dx], [-b.dy, -b.dx]] : [[-b.dy, -b.dx], [b.dy, b.dx]];
      const opts = api.rand() < 0.3 ? [...sides, ahead, back] : [ahead, ...sides, back];
      for (const [dx, dy] of opts) {
        const x = b.x + dx, y = b.y + dy;
        if (x < 0 || y < 0 || x > s.cols - 2 || y > s.rows - 2) continue;
        let clear = true;
        for (let j = 0; j < 4 && clear; j++) if (onBody(x + (j % 2), y + (j >> 1)) || isFood(x + (j % 2), y + (j >> 1))) clear = false;
        if (clear) { b.x = x; b.y = y; b.dx = dx; b.dy = dy; return; }
      }
    }

    return {
      fit(aw, ah) {
        const k = Math.max(1, Math.floor(Math.min(aw / 136, ah / 104)));
        return { W: clamp(Math.floor(aw / k / CELL), 12, 32) * CELL, H: clamp(Math.floor(ah / k / CELL), 10, 26) * CELL };
      },
      reset(W, H) {
        const cols = W / CELL, rows = H / CELL, x = Math.floor(cols / 3), y = Math.floor(rows / 2);
        s = { cols, rows, body: [{ x, y }, { x: x - 1, y }, { x: x - 2, y }], dir: DIRS.right, queue: [], tick: 0,
          eaten: 0, food: null, bonus: null, chew: 0, t: 0, dead: false };
        s.food = free(1);
      },
      update(ms) {
        s.t += ms;
        s.chew = Math.max(0, s.chew - ms);
        if (s.bonus) {
          const b = s.bonus;
          b.ttl -= ms;
          b.walk += ms;
          if (b.ttl <= 0) s.bonus = null;
          else if (b.walk >= 420) { b.walk -= 420; wander(b); }
        }
        s.tick += ms;
        const every = Math.max(65, 140 - s.eaten * 3);
        while (s.tick >= every && !s.dead) { s.tick -= every; step(); }
      },
      key(a, down) {
        const d = DIRS[a];
        if (!down || !d) return;
        // compare with the last turn already queued, so two quick turns cannot fold the snake onto itself
        const prev = s.queue.length ? s.queue[s.queue.length - 1] : s.dir;
        if ((d.x === -prev.x && d.y === -prev.y) || d === prev) return;
        if (s.queue.length < 2) s.queue.push(d);
      },
      intro: () => ({ top: ["ARROWS OR WASD", "EAT THE SPARKLES", "CATCH THE BOT!"], bottom: [] }),
      summary: () => [`LENGTH ${s.body.length}`],
      state: () => s,
      draw(g, P, mode) {
        const W = s.cols * CELL, H = s.rows * CELL;
        g.fillStyle = P.well;
        g.fillRect(0, 0, W, H);
        g.fillStyle = P.grid;
        for (let y = 0; y < s.rows; y++) for (let x = y % 2; x < s.cols; x += 2) g.fillRect(x * CELL, y * CELL, CELL, CELL);
        if (s.food) blit(g, "sparkle", calm() ? 3 : TWINKLE[Math.floor(s.t / 140) % TWINKLE.length], s.food.x * CELL + 1, s.food.y * CELL + 1);
        const b = s.bonus;
        // the bonus bot walks and blinks; it flickers in its last two seconds to say it is leaving
        if (b && (b.ttl > 2000 || Math.floor(b.ttl / 130) % 2 === 0)) {
          const f = s.dead ? 4 : s.t % 1600 < 140 ? 1 : Math.floor(s.t / 200) % 2 ? 2 : b.dx < 0 ? 5 : 0;
          blit(g, "bot", f, b.x * CELL + 2, b.y * CELL + 3);
        }
        if (mode === "ready") blit(g, "bot", 0, (s.cols - 4) * CELL + 2, s.body[0].y * CELL - 5);
        for (let i = s.body.length - 1; i > 0; i--) {
          const seg = s.body[i], next = s.body[i - 1], x = seg.x * CELL, y = seg.y * CELL;
          g.fillStyle = P.grass;
          g.fillRect(x + 1, y + 1, 6, 6);
          // bridge to the next segment so the body reads as one piece
          if (next.x !== seg.x) g.fillRect(Math.min(x, next.x * CELL) + 7, y + 1, 2, 6);
          else g.fillRect(x + 1, Math.min(y, next.y * CELL) + 7, 6, 2);
          g.globalAlpha = i % 2 ? 0.22 : 0.4;
          g.fillStyle = i % 2 ? P.dark : P.white;
          if (i % 2) g.fillRect(x + 2, y + 2, 4, 4); else g.fillRect(x + 2, y + 2, 1, 1);
          g.globalAlpha = 1;
        }
        // the head is the slime itself, two cells wide, drawn last so it sits on the neck
        const h = s.body[0];
        let f = 0, flip = "";
        if (s.dead) f = 3;
        else if (s.chew > 0) f = 1;
        else if (s.t % 2600 < 130) f = 3;
        else if (s.dir.x) { f = 2; if (s.dir.x < 0) flip = "x"; }
        blit(g, "buddy", f, h.x * CELL - 4, h.y * CELL - 2, flip);
      },
    };
  }

  /* ================================================================== FLAPPY */
  function flappy(api) {
    // a flap rises about 16 px against gaps of 46 down to 40, so even the late game leaves room
    const GROUND = 10, PIPE = 14, SPACING = 76, GRAVITY = 430, FLAP = -118, MAX_FALL = 190, HIT = { w: 9, h: 8 };
    // where each sprite sits around the shared hitbox, so both flyers are judged by the same box
    const OFFSET = { bot: { dx: 2, dy: 3 }, buddy: { dx: 4, dy: 1 } };
    const STARS = [0.05, 0.2, 0.18, 0.62, 0.31, 0.1, 0.44, 0.48, 0.57, 0.26, 0.69, 0.71, 0.78, 0.15, 0.86, 0.55, 0.95, 0.35, 0.12, 0.83];
    let pick = "bot", s = null;
    const px = () => Math.floor(s.W * 0.3);
    const label = (who) => (who === "bot" ? "BOT" : "SLIME");

    function flap() {
      if (s.dead) return;
      s.vy = FLAP;
      s.flap = 140;
    }
    function die() { s.dead = true; api.over(); }
    function addPipe() {
      const last = s.pipes[s.pipes.length - 1];
      const gap = Math.max(40, 46 - 2 * Math.floor(s.passed / 10));
      const lo = 8 + gap / 2, hi = s.H - GROUND - 8 - gap / 2;
      // the next gap moves at most a fair step from the last one
      s.mid = clamp(s.mid + (api.rand() * 2 - 1) * 34, lo, hi);
      const top = Math.round(s.mid - gap / 2);
      s.pipes.push({ x: last ? last.x + SPACING : s.W + 30, top, bottom: top + gap, passed: false });
    }
    function column(g, P, x, y0, y1) {
      if (y1 <= y0) return;
      g.fillStyle = P.stone;
      g.fillRect(x, y0, PIPE, y1 - y0);
      g.fillStyle = P.stoneDeep;
      g.fillRect(x + PIPE - 3, y0, 3, y1 - y0);
      g.globalAlpha = 0.3;
      g.fillStyle = P.white;
      g.fillRect(x + 2, y0, 2, y1 - y0);
      g.globalAlpha = 1;
    }
    function cap(g, P, x, y) {
      g.fillStyle = P.stoneDeep;
      g.fillRect(x - 2, y, PIPE + 4, 4);
      g.fillStyle = P.stone;
      g.fillRect(x - 1, y + 1, PIPE + 2, 2);
    }

    return {
      click: true,
      fit(aw, ah) {
        const k = Math.max(1, Math.floor(Math.min(aw / 120, ah / 104)));
        return { W: clamp(Math.floor(aw / k), 120, 200), H: clamp(Math.floor(ah / k), 104, 140) };
      },
      reset(W, H) {
        s = { W, H, pick, y: Math.round((H - GROUND) * 0.42), vy: 0, pipes: [], passed: 0, mid: (H - GROUND) / 2, t: 0, dist: 0, flap: 0, dead: false };
      },
      begin: () => flap(),
      pick(a) {
        const want = a === "left" ? "bot" : a === "right" ? "buddy" : null;
        if (!want || want === pick) return false;
        pick = want;
        if (s) s.pick = want;
        return true;
      },
      update(ms) {
        const dt = ms / 1000;
        s.t += ms;
        s.flap = Math.max(0, s.flap - ms);
        s.vy = Math.min(MAX_FALL, s.vy + GRAVITY * dt);
        s.y += s.vy * dt;
        if (s.y < 0) { s.y = 0; s.vy = 0; } // the ceiling stops you, only the ground and the columns end the run
        const speed = 42 + Math.min(18, s.passed);
        s.dist += speed * dt;
        for (const p of s.pipes) p.x -= speed * dt;
        while (!s.pipes.length || s.pipes[s.pipes.length - 1].x + SPACING <= s.W + PIPE) addPipe();
        if (s.pipes[0].x + PIPE + 3 < 0) s.pipes.shift();
        const x = px();
        for (const p of s.pipes) {
          if (!p.passed && p.x + PIPE <= x) { p.passed = true; s.passed++; api.add(1); }
          if (x + HIT.w > p.x && x < p.x + PIPE && (s.y < p.top || s.y + HIT.h > p.bottom)) return die();
        }
        if (s.y + HIT.h >= s.H - GROUND) { s.y = s.H - GROUND - HIT.h; die(); }
      },
      key(a, down) { if (down && (a === "space" || a === "up")) flap(); },
      intro: () => ({ top: [`< ${label(pick)} >`], bottom: ["SPACE FLAPS"] }),
      summary: () => [`${label(s.pick === "bot" ? "buddy" : "bot")} WINS THE RACE!`],
      state: () => s,
      draw(g, P, mode) {
        const { W, H } = s, ground = H - GROUND;
        g.fillStyle = P.well;
        g.fillRect(0, 0, W, H);
        // stars drift slower than the columns: a cheap parallax
        g.fillStyle = P.rule2;
        for (let i = 0; i < STARS.length; i += 2) g.fillRect(Math.floor(mod(STARS[i] * W - s.dist * 0.25, W)), Math.floor(STARS[i + 1] * (ground - 12)) + 2, 1, 1);
        for (const p of s.pipes) {
          const x = Math.round(p.x);
          column(g, P, x, 0, p.top - 4);
          cap(g, P, x, p.top - 4);
          column(g, P, x, p.bottom + 4, ground);
          cap(g, P, x, p.bottom);
        }
        g.fillStyle = P.orangeDeep;
        g.fillRect(0, ground, W, GROUND);
        g.fillStyle = P.grass;
        g.fillRect(0, ground, W, 2);
        g.fillStyle = P.orange;
        const off = Math.floor(mod(s.dist, 8));
        for (let x = -off; x < W; x += 8) { g.fillRect(x, ground + 4, 3, 1); g.fillRect(x + 4, ground + 7, 3, 1); }

        if (mode === "ready") {
          // the picker: both friends side by side, the chosen one framed
          const mid = Math.floor(W / 2), y = Math.floor(ground / 2) + 3;
          const spots = { bot: [mid - 22, y - 7, 12, 13], buddy: [mid + 6, y - 4, 16, 10] };
          for (const who of ["bot", "buddy"]) {
            const [x, yy, w, h] = spots[who], on = who === pick;
            if (on) { g.fillStyle = P.yellow; outline4(g, x - 3, yy - 3, w + 6, h + 6); }
            g.globalAlpha = on ? 1 : 0.45;
            blit(g, who, on ? (who === "bot" ? 4 : 2) : 0, x, yy);
            g.globalAlpha = 1;
          }
          return;
        }
        // the other friend races along the ground, just for fun: no collisions
        const rival = s.pick === "bot" ? "buddy" : "bot";
        const rx = clamp(Math.round(px() + 4 + Math.sin(s.t / 900) * 24 + Math.sin(s.t / 340) * 5), 2, W - 18);
        if (rival === "bot") blit(g, "bot", s.dead ? 4 : Math.floor(s.t / 120) % 2 ? 2 : 0, rx, ground - 13);
        else {
          const squash = !s.dead && Math.floor(s.t / 150) % 2 === 1;
          blit(g, "buddy", s.dead ? 2 : squash ? 1 : 0, rx, ground - 10 - (squash || calm() || s.dead ? 0 : 2));
        }
        const o = OFFSET[s.pick], x = px(), y = Math.round(s.y);
        if (s.pick === "bot") blit(g, "bot", s.dead ? 3 : s.vy < 0 ? 7 : 0, x - o.dx, y - o.dy);
        else blit(g, "buddy", s.dead ? 3 : s.flap > 0 ? 1 : 2, x - o.dx, y - o.dy, s.dead ? "y" : "");
      },
    };
  }
  function outline4(g, x, y, w, h) {
    g.fillRect(x, y, w, 1);
    g.fillRect(x, y + h - 1, w, 1);
    g.fillRect(x, y + 1, 1, h - 2);
    g.fillRect(x + w - 1, y + 1, 1, h - 2);
  }

  const FACTORY = { tetris, snake, flappy };

  /* =================================================================== mount */
  // Physical key positions, so WASD stays a cross on any keyboard layout.
  const CODES = {
    ArrowLeft: "left", KeyA: "left", ArrowRight: "right", KeyD: "right", ArrowUp: "up", KeyW: "up", ArrowDown: "down", KeyS: "down",
    Space: "space", Enter: "enter", NumpadEnter: "enter", KeyX: "rotate", KeyZ: "rotateBack", KeyP: "pause", KeyR: "restart",
  };

  function mount(container) {
    const root = document.createElement("div");
    root.className = "games";
    root.innerHTML = `
      <div class="games-head">
        <div class="games-pick" role="group" aria-label="Choose a game">
          ${NAMES.map((n) => `<button type="button" class="games-tab" data-game="${n}" aria-pressed="false">${TITLES[n]}</button>`).join("")}
        </div>
        <dl class="games-stats">
          <div class="games-stat"><dt>Score</dt><dd class="games-num" data-score>0</dd></div>
          <div class="games-stat is-best"><dt>Best</dt><dd class="games-num" data-best>0</dd></div>
        </dl>
      </div>
      <div class="games-stage" tabindex="0" role="application">
        <canvas class="games-canvas" role="img"></canvas>
        <div class="games-hud" aria-hidden="true"><div class="games-hud-top"></div><div class="games-hud-bottom"></div></div>
      </div>
      <p class="games-status" role="status"></p>`;
    container.append(root);
    const q = (sel) => root.querySelector(sel);
    const stage = q(".games-stage"), canvas = q(".games-canvas"), hud = q(".games-hud");
    const hudTop = q(".games-hud-top"), hudBottom = q(".games-hud-bottom"), status = q(".games-status");
    const scoreEl = q("[data-score]"), bestEl = q("[data-best]"), tabs = [...root.querySelectorAll("[data-game]")];
    const g = canvas.getContext("2d");

    const st = { name: "", mode: "ready", score: 0, best: 0, newBest: false, frames: 0, focused: false, visible: true, hold: false, overAt: 0 };
    const games = {};
    let game = null, size = { W: 0, H: 0 }, scale = 1, raf = 0, last = 0, acc = 0;
    let rand = rng((Math.random() * 2 ** 32) >>> 0);

    const api = {
      rand: () => rand(),
      int: (n) => Math.floor(rand() * n),
      add(points) {
        st.score += points;
        if (st.score > st.best) { st.best = st.score; st.newBest = true; saveBest(st.name, st.best); }
        nums();
      },
      over,
    };

    /* ------------------------------------------------------------ the loop */
    const canRun = () => st.focused && st.visible && !document.hidden;
    function wake() {
      if (raf || st.mode !== "play" || st.hold) return;
      last = performance.now();
      raf = requestAnimationFrame(frame);
    }
    function sleep() {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    }
    function frame(now) {
      raf = 0;
      if (st.mode !== "play" || st.hold) return;
      if (!canRun()) { pause(); return; }
      const dt = clamp(now - last, 0, 100); // a long stall resumes gently instead of jumping ahead
      last = now;
      advance(dt);
      st.frames++;
      draw();
      if (st.mode === "play") raf = requestAnimationFrame(frame);
    }
    function advance(ms) {
      acc += ms;
      while (acc >= STEP && st.mode === "play") { acc -= STEP; game.update(STEP); }
    }
    function draw() {
      if (!size.W) return;
      g.imageSmoothingEnabled = false;
      game.draw(g, colors(), st.mode);
    }

    /* ---------------------------------------------------------- the modes */
    function start() {
      if (!layout(true)) return;
      st.best = Math.max(st.best, loadBest(st.name)); // another panel may have raised it
      st.score = 0;
      st.newBest = false;
      st.mode = "play";
      acc = 0;
      game.begin?.();
      if (st.mode !== "play") return; // a run can end on its very first piece
      nums();
      hudRender("");
      draw();
      wake();
    }
    function pause() {
      if (st.mode !== "play") return;
      st.mode = "pause";
      sleep();
      game.release?.();
      hudRender("Paused.");
      draw();
    }
    function resume() {
      if (st.mode !== "pause" || !canRun()) return;
      st.mode = "play";
      hudRender("");
      draw();
      wake();
    }
    function over() {
      st.mode = "over";
      st.overAt = performance.now();
      sleep();
      game.release?.();
      if (st.newBest) saveBest(st.name, st.best);
      nums();
      hudRender(`Game over. Score ${st.score}.${st.newBest ? " New best." : ""}`);
      draw();
    }
    function select(name) {
      if (!FACTORY[name] || (name === st.name && game)) return;
      sleep();
      game?.release?.();
      st.name = name;
      st.mode = "ready";
      st.score = 0;
      st.newBest = false;
      st.best = loadBest(name);
      game = games[name] ||= FACTORY[name](api);
      for (const b of tabs) b.setAttribute("aria-pressed", String(b.dataset.game === name));
      stage.setAttribute("aria-label", `${TITLES[name]}. Focus here, then play with the keyboard.`);
      layout(true);
      nums();
      hudRender("");
    }

    /* -------------------------------------------------------------- input */
    function press(action) {
      const m = st.mode;
      if (action === "pause") { if (m === "play") pause(); else if (m === "pause") resume(); return; }
      if (action === "restart") { start(); return; }
      const go = action === "space" || action === "enter";
      if (m === "ready") {
        if (go) start();
        else if (game.pick?.(action)) { hudRender(""); draw(); }
      } else if (m === "pause") {
        if (go) resume();
      } else if (m === "over") {
        if (go && performance.now() - st.overAt >= RETRY_LOCK) start();
      } else game.key(action, true);
    }
    function onKeyDown(e) {
      if (e.ctrlKey || e.metaKey || e.altKey || e.key === "Tab" || e.key === "Escape") return;
      const action = CODES[e.code];
      if (!action && e.key.length !== 1) return; // F keys, Home, Page Down stay with the browser and the app
      // Single letters are app shortcuts (r refreshes, p opens a pull request, j and k move), so
      // while the game has focus every printable key stays here; only the game's own keys lose their default.
      e.stopPropagation();
      if (!action) return;
      e.preventDefault();
      if (!e.repeat) press(action); // Tetris repeats held arrows itself; OS repeat would double them
    }
    function onKeyUp(e) {
      const action = CODES[e.code];
      if (!action) return;
      e.stopPropagation();
      if (st.mode === "play") game.key(action, false);
    }
    stage.addEventListener("keydown", onKeyDown);
    stage.addEventListener("keyup", onKeyUp);
    stage.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      // a click that only brings the focus back does nothing else, so it cannot cost a flap
      if (document.activeElement !== stage) { stage.focus({ preventScroll: true }); return; }
      if (game.click) press("space");
    });
    stage.addEventListener("focus", () => { st.focused = true; hudRender(); });
    stage.addEventListener("blur", () => {
      st.focused = false;
      if (st.mode === "play") pause();
      else hudRender();
    });
    root.querySelector(".games-pick").addEventListener("click", (e) => {
      const b = e.target.closest("[data-game]");
      if (!b) return;
      select(b.dataset.game);
      stage.focus({ preventScroll: true }); // Space on the button would only click it again
    });

    /* ------------------------------------------------------- size and view */
    function layout(fresh) {
      const aw = stage.clientWidth, ah = stage.clientHeight;
      if (!aw || !ah) { pause(); return false; }
      // a run keeps its board; only the title screen follows the panel's new size
      if (fresh || st.mode === "ready" || !size.W) {
        const z = game.fit(aw, ah);
        if (fresh || z.W !== size.W || z.H !== size.H) { size = z; game.reset(z.W, z.H); }
      }
      const room = Math.min(aw / size.W, ah / size.H);
      scale = room >= 1 ? Math.floor(room) : room; // whole steps keep pixels square; only a tiny panel shrinks
      if (canvas.width !== size.W || canvas.height !== size.H) { canvas.width = size.W; canvas.height = size.H; }
      const cw = Math.round(size.W * scale), ch = Math.round(size.H * scale);
      const box = { left: `${Math.floor((aw - cw) / 2)}px`, top: `${Math.floor((ah - ch) / 2)}px`, width: `${cw}px`, height: `${ch}px` };
      Object.assign(canvas.style, box);
      Object.assign(hud.style, box);
      hud.classList.toggle("is-small", scale < 2);
      draw();
      return true;
    }
    function nums() {
      if (scoreEl.textContent !== String(st.score)) scoreEl.textContent = st.score;
      if (bestEl.textContent !== String(st.best)) bestEl.textContent = st.best;
    }
    const line = (str, cls) => {
      const p = document.createElement("p");
      if (cls) p.className = cls;
      p.textContent = str;
      return p;
    };
    function hudRender(say) {
      const title = TITLES[st.name];
      const m = st.mode;
      const label = {
        ready: `${title}. Press Space to start.`,
        play: `${title}, playing.`,
        pause: `${title}, paused. Press P to resume.`,
        over: `${title}, game over. Score ${st.score}, best ${st.best}. Press Space or R to play again.`,
      }[m];
      canvas.setAttribute("aria-label", label);
      if (say !== undefined) status.textContent = say;
      hud.hidden = m === "play";
      hud.className = `games-hud is-${m}${scale < 2 ? " is-small" : ""}`;
      if (m === "play") return;
      const intro = m === "ready" ? game.intro() : { top: [], bottom: [] };
      const top = [line(m === "ready" ? title.toUpperCase() : m === "pause" ? "PAUSED" : "GAME OVER", "games-title")];
      for (const t of intro.top) top.push(line(t));
      if (m === "over") {
        top.push(line(`SCORE ${st.score}`));
        for (const t of game.summary()) top.push(line(t));
        top.push(st.newBest ? line("NEW BEST!", "games-best") : line(`BEST ${st.best}`));
      }
      const bottom = intro.bottom.map((t) => line(t));
      if (!st.focused) bottom.push(line("CLICK HERE FIRST"));
      bottom.push(line(m === "ready" ? "PRESS SPACE" : m === "pause" ? "P TO RESUME" : "SPACE TO RETRY", "games-key"));
      hudTop.replaceChildren(...top);
      hudBottom.replaceChildren(...bottom);
    }

    /* ------------------------------------------------- staying out of the way */
    const ro = new ResizeObserver(() => layout(false));
    ro.observe(stage);
    const io = new IntersectionObserver((entries) => {
      st.visible = entries[entries.length - 1].isIntersecting;
      if (!st.visible) pause();
    });
    io.observe(stage);
    const onHidden = () => { if (document.hidden) pause(); };
    document.addEventListener("visibilitychange", onHidden);

    select(NAMES[0]);

    // A small test hook: read and patch state, step the clock by hand, hold the real loop.
    const debug = {
      get name() { return st.name; },
      get mode() { return st.mode; },
      get score() { return st.score; },
      get best() { return st.best; },
      get frames() { return st.frames; },
      get scale() { return scale; },
      get size() { return { ...size }; },
      get state() { return game.state(); },
      step(ms) { if (st.mode === "play") { advance(ms); draw(); } },
      hold(on) { st.hold = !!on; if (on) sleep(); else wake(); },
      seed(n) { rand = rng(n); },
      redraw: draw,
    };
    live.add(debug);

    return {
      unmount() {
        sleep();
        ro.disconnect();
        io.disconnect();
        document.removeEventListener("visibilitychange", onHidden);
        live.delete(debug);
        root.remove();
      },
      focus() { stage.focus({ preventScroll: true }); },
      play(name) { select(name); },
    };
  }

  return { mount, names: NAMES, _debug: { get instances() { return [...live]; } } };
})();
