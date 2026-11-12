"use strict";

/* Pixel characters and the little game moments: the slime buddy on the Claude Code
   buttons, the desk bot mascot, tombstones and flags in the merge tree, a death
   when something is deleted, fireworks when a pull request merges.
   Sprites are tiny strings of palette letters drawn as SVG rects; every colour is a
   token in tokens.css. Frames switch by a data attribute, so a sprite costs no
   timer until it moves. */

const Sprites = (() => {
  const PALETTE = {
    o: "p-orange", O: "p-orange-deep", k: "p-dark", b: "p-blue", B: "p-blue-deep", y: "p-yellow",
    r: "p-red", w: "p-white", s: "p-stone", S: "p-stone-deep", g: "p-grass", a: "p-amber", c: "p-ink", t: "p-tint",
  };

  const DEFS = {
    // the buddy: a green slime that runs the fixes (rest, squash, look aside, blink)
    buddy: {
      frames: [
        ["................", "......gggg......", "....gggggggg....", "...gwwgggggggg..", "..gwwgggggggggg.",
          "..ggggkggggkggg.", ".gggggkggggkgggg", ".ggggggkkkggggg.", "gggggggggggggggg", ".gggggggggggggg."],
        ["................", "................", ".....gggggg.....", "...gwwgggggggg..", ".gwwgggggggggggg",
          ".gggggkggggkgggg", "ggggggkggggkgggg", "gggggggkkkgggggg", "gggggggggggggggg", ".gggggggggggggg."],
        ["................", "......gggg......", "....gggggggg....", "...gwwgggggggg..", "..gwwgggggggggg.",
          "..gggggkggggkgg.", ".ggggggkggggkggg", ".ggggggkkkggggg.", "gggggggggggggggg", ".gggggggggggggg."],
        ["................", "......gggg......", "....gggggggg....", "...gwwgggggggg..", "..gwwgggggggggg.",
          "..ggggggggggggg.", ".gggggSggggSgggg", ".ggggggkkkggggg.", "gggggggggggggggg", ".gggggggggggggg."],
      ],
    },
    // a sparkle that turns while the buddy works
    sparkle: {
      frames: [
        [".......", ".......", ".......", "...y...", ".......", ".......", "......."],
        [".......", ".......", "...y...", "..yyy..", "...y...", ".......", "......."],
        [".......", ".y...y.", "..y.y..", "...y...", "..y.y..", ".y...y.", "......."],
        ["...y...", ".y.y.y.", "..yyy..", "yyyyyyy", "..yyy..", ".y.y.y.", "...y..."],
        ["y..y..y", ".y.y.y.", "..yyy..", "yyy.yyy", "..yyy..", ".y.y.y.", "y..y..y"],
      ],
    },
    // the desk bot: the app icon grown legs (blue screen, yellow side bar, red light)
    bot: {
      frames: [
        [".....rr.....", ".....rr.....", "......B.....", "..bbbbbbbb..", ".ybbbbbbbbb.", ".ybwwbbbwwb.", ".ybwkbbbwkb.", ".ybbbbbbbbb.", ".ybbwwwwwbb.", ".ybbbbbbbbb.", "..bbbbbbbb..", "...B....B...", "..BB....BB.."],
        [".....rr.....", ".....rr.....", "......B.....", "..bbbbbbbb..", ".ybbbbbbbbb.", ".ybbbbbbbbb.", ".ybkkbbbkkb.", ".ybbbbbbbbb.", ".ybbwwwwwbb.", ".ybbbbbbbbb.", "..bbbbbbbb..", "...B....B...", "..BB....BB.."],
        [".....rr.....", ".....rr.....", "......B.....", "..bbbbbbbb..", ".ybbbbbbbbb.", ".ybwwbbbwwb.", ".ybwkbbbwkb.", ".ybbbbbbbbb.", ".ybbwwwwwbb.", ".ybbbbbbbbb.", "..bbbbbbbb..", "....B..B....", "....BB.BB..."],
        [".....rr.....", ".....rr.....", "......B.....", "..bbbbbbbb..", ".ybbbbbbbbb.", ".ybkbbbbkbb.", ".ybbkbbkbbb.", ".ybkbbbbkbb.", ".ybbwbwbwbb.", ".ybbbbbbbbb.", "..bbbbbbbb..", "...B....B...", "..BB....BB.."],
        [".....rr.....", ".....rr.....", "......B.....", "..bbbbbbbb..", ".ybbbbbbbbb.", ".ybbkbbbkbb.", ".ybkbkbkbkb.", ".ybbbbbbbbb.", ".ybbwwwwwbb.", ".ybbbwwwbbb.", "..bbbbbbbb..", "...B....B...", "..BB....BB.."],
        // looking left, up and left, up and right (frame 0 looks down and right)
        [".....rr.....", ".....rr.....", "......B.....", "..bbbbbbbb..", ".ybbbbbbbbb.", ".ybwwbbbwwb.", ".ybkwbbbkwb.", ".ybbbbbbbbb.", ".ybbwwwwwbb.", ".ybbbbbbbbb.", "..bbbbbbbb..", "...B....B...", "..BB....BB.."],
        [".....rr.....", ".....rr.....", "......B.....", "..bbbbbbbb..", ".ybbbbbbbbb.", ".ybkwbbbkwb.", ".ybwwbbbwwb.", ".ybbbbbbbbb.", ".ybbwwwwwbb.", ".ybbbbbbbbb.", "..bbbbbbbb..", "...B....B...", "..BB....BB.."],
        [".....rr.....", ".....rr.....", "......B.....", "..bbbbbbbb..", ".ybbbbbbbbb.", ".ybwkbbbwkb.", ".ybwwbbbwwb.", ".ybbbbbbbbb.", ".ybbwwwwwbb.", ".ybbbbbbbbb.", "..bbbbbbbb..", "...B....B...", "..BB....BB.."],
      ],
    },
    // a small critter that stands for a branch or a pull request when it is removed
    critter: {
      frames: [
        ["...aaaa...", ".aaaaaaaa.", "aawwaawwaa", "aawkaawkaa", "aaaaaaaaaa", "aaaakkaaaa", ".aaaaaaaa.", ".a.a..a.a."],
        ["...aaaa...", ".aaaaaaaa.", "aaaaaaaaaa", "aakkaakkaa", "aaaaaaaaaa", "aaaakkaaaa", ".aaaaaaaa.", ".a.a..a.a."],
      ],
    },
    tomb: {
      frames: [["..ssss..", ".ssssss.", "sssSSsss", "ssSSSSss", "sssSSsss", "sssSSsss", "ssssssss", "ssssssss", "gggggggg"]],
    },
    flag: {
      frames: [
        ["Saaaa...", "Saaaaaa.", "Saaaaa..", "Saaa....", "S.......", "S.......", "S.......", "S.......", "SS......"],
        ["Saaa....", "Saaaaa..", "Saaaaaa.", "Saaaa...", "S.......", "S.......", "S.......", "S.......", "SS......"],
      ],
    },
    push: { frames: [["....c....", "...ccc...", "..ccccc..", ".ccccccc.", "...ccc...", "...ccc...", "...ccc...", ".........", ".ccccccc."]] },
    read: { frames: [["ccccccc..", "c.....cc.", "c.ccc..cc", "c.......c", "c.cccc..c", "c.......c", "c.ccc...c", "c.......c", "ccccccccc"]] },
    decline: { frames: [["cc.....cc", "ccc...ccc", ".ccc.ccc.", "..ccccc..", "...ccc...", "..ccccc..", ".ccc.ccc.", "ccc...ccc", "cc.....cc"]] },
    up: { frames: [["....c....", "...ccc...", "..cc.cc..", ".cc...cc.", "cc.....cc"]] },
    down: { frames: [["cc.....cc", ".cc...cc.", "..cc.cc..", "...ccc...", "....c...."]] },
    changes: { frames: [["......cc.", ".....cccc", "....cccc.", "...cccc..", "..cccc...", ".cccc....", "cccc.....", "cc.......", "c........"]] },
    sync: { frames: [["..ccccc..", ".c.....cc", "c.....ccc", "c........", "c.......c", "........c", "ccc.....c", "cc.....c.", "..ccccc.."]] },
    merge: { frames: [["cc.....cc", "cc.....cc", ".cc...cc.", "..cc.cc..", "...ccc...", "...ccc...", "...ccc...", "...ccc...", "...ccc..."]] },
    copy: { frames: [["...cccccc", "...c....c", "cccccc..c", "c....c..c", "c....c..c", "c....cccc", "c....c...", "c....c...", "cccccc..."]] },
    // the builder's hammer: raised, then struck
    hammer: { frames: [
      ["ss......", "sss.....", "sssa....", "...a....", "....a...", ".....a..", "......a.", "......."],
      ["........", "........", "........", "........", "ss......", "sssaaaaa", "ss......", "........"],
    ] },
    spark: { frames: [["..y..", "..y..", "yywyy", "..y..", "..y.."]] },
    check: { frames: [[".........", "........c", ".......cc", "c.....cc.", "cc...cc..", ".cc.cc...", "..ccc....", "...c.....", "........."]] },
  };

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const cache = new Map();
  function frameRects(rows) {
    let out = "";
    rows.forEach((row, y) => {
      let x = 0;
      while (x < row.length) {
        const ch = row[x];
        let end = x + 1;
        while (end < row.length && row[end] === ch) end++;
        if (PALETTE[ch]) out += `<rect class="${PALETTE[ch]}" x="${x}" y="${y}" width="${end - x}" height="1"/>`;
        x = end;
      }
    });
    return out;
  }
  function body(name) {
    if (cache.has(name)) return cache.get(name);
    const def = DEFS[name];
    const w = Math.max(...def.frames.flat().map((r) => r.length));
    const h = Math.max(...def.frames.map((f) => f.length));
    const groups = def.frames.map((f, i) => `<g data-f="${i}">${frameRects(f)}</g>`).join("");
    const out = { w, h, groups };
    cache.set(name, out);
    return out;
  }

  /** SVG markup for a sprite. scale is the size of one pixel in CSS px. */
  function svg(name, { scale = 2, cls = "", idle = "", x = null, y = null, tip = "", follow = false } = {}) {
    const { w, h, groups } = body(name);
    const pos = x != null ? ` x="${x}" y="${y}"` : "";
    return `<svg class="spr spr-${name}${cls ? ` ${cls}` : ""}" viewBox="0 0 ${w} ${h}" width="${w * scale}" height="${h * scale}"${pos} shape-rendering="crispEdges" data-sprite="${name}" data-frame="0"${idle ? ` data-idle="${idle}"` : ""}${follow ? " data-follow" : ""}${tip ? ` data-tip="${tip}"` : ""} aria-hidden="true">${groups}</svg>`;
  }

  /* --------------------------------------------------------- frame playback */

  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SEQ = {
    buddy: [[0, 1, 0, 1, 2, 2, 0, 3], 130],
    sparkle: [[0, 1, 2, 3, 4, 3, 2, 1], 110],
    bot: [[0, 2], 140],
    flag: [[0, 1], 260],
    critter: [[0], 200],
  };
  function play(el, seq, ms) {
    if (!el || reduced()) return;
    stop(el);
    const [frames, step] = seq || SEQ[el.dataset.sprite] || [[0], 200];
    let i = 0;
    el._timer = setInterval(() => { i = (i + 1) % frames.length; el.dataset.frame = frames[i]; }, ms || step);
  }
  function stop(el, frame = 0) {
    if (!el) return;
    clearInterval(el._timer);
    el._timer = 0;
    el.dataset.frame = frame;
  }
  function flash(el, frame, ms = 160) {
    if (!el || el._timer || reduced()) return;
    el.dataset.frame = frame;
    setTimeout(() => { if (!el._timer) el.dataset.frame = el._look || 0; }, ms);
  }

  // A bot marked data-follow watches the pointer: its pupils turn toward it.
  const LOOK = { "down-right": 0, "down-left": 5, "up-left": 6, "up-right": 7 };
  let lookQueued = false, pointerX = 0, pointerY = 0;
  function look() {
    lookQueued = false;
    for (const el of document.querySelectorAll(".spr-bot[data-follow]")) {
      const r = el.getBoundingClientRect();
      if (!r.width || el._timer) continue;
      const cx = r.left + r.width / 2, cy = r.top + r.height * 0.45;
      el._look = LOOK[`${pointerY < cy - 4 ? "up" : "down"}-${pointerX < cx ? "left" : "right"}`];
      if (el.dataset.frame !== "1") el.dataset.frame = el._look; // not in the middle of a blink
    }
  }
  addEventListener("pointermove", (e) => {
    pointerX = e.clientX;
    pointerY = e.clientY;
    if (!lookQueued && !reduced()) { lookQueued = true; requestAnimationFrame(look); }
  }, { passive: true });

  // Hovering a button that carries a sprite plays it; leaving stops it on frame 0.
  function hoverables(root = document) {
    const enter = (e) => {
      const host = e.target.closest?.("[data-spr-hover]");
      if (!host || host.contains(e.relatedTarget)) return;
      host.querySelectorAll(".spr").forEach((s) => play(s));
    };
    const leave = (e) => {
      const host = e.target.closest?.("[data-spr-hover]");
      if (!host || host.contains(e.relatedTarget) || host.classList.contains("is-busy")) return;
      host.querySelectorAll(".spr").forEach((s) => stop(s));
    };
    root.addEventListener("mouseover", enter);
    root.addEventListener("mouseout", leave);
    root.addEventListener("focusin", enter);
    root.addEventListener("focusout", leave);
  }

  // Idle life: now and then a bot blinks or a flag flutters. One timer for the page.
  function idle() {
    setInterval(() => {
      if (document.hidden || reduced()) return;
      const all = [...document.querySelectorAll(".spr[data-idle]")];
      if (!all.length) return;
      const pick = all[Math.floor(Math.random() * all.length)];
      if (pick.dataset.idle === "blink") flash(pick, 1, 150);
      else if (pick.dataset.idle === "wave") flash(pick, 1, 420);
    }, 1800);
  }

  /* --------------------------------------------------------------- moments */

  const layer = () => document.getElementById("fx");
  function place(node, rect) {
    node.style.setProperty("--x", `${Math.round(rect.left + rect.width / 2)}px`);
    node.style.setProperty("--y", `${Math.round(rect.top + rect.height / 2)}px`);
    layer().append(node);
    return node;
  }

  /** A critter at rect blinks, gets knocked out, hops and falls off the screen; a tombstone stays a moment. */
  function die(rect, kind = "branch") {
    if (!layer() || !rect) return;
    const box = document.createElement("div");
    box.className = `fx-die is-${kind}`;
    box.innerHTML = `<span class="fx-tomb">${svg("tomb", { scale: 3 })}</span><span class="fx-body">${svg("critter", { scale: 3 })}</span>`;
    place(box, rect);
    if (reduced()) { setTimeout(() => box.remove(), 900); return; }
    const critter = box.querySelector(".spr-critter");
    setTimeout(() => { critter.dataset.frame = 1; box.classList.add("is-hit"); }, 420);
    setTimeout(() => box.remove(), 2100);
  }

  /** Merge: the bot jumps under a banner while pixel fireworks go off. */
  function celebrate(pr) {
    if (!layer()) return;
    const box = document.createElement("div");
    box.className = "fx-merge";
    box.setAttribute("role", "status");
    const ticket = pr.ticket?.key ? `<span class="fx-ticket">${esc(pr.ticket.key)}</span>` : "";
    box.innerHTML = `
      <div class="fx-banner"><span class="fx-word">MERGED</span><span class="fx-pr">#${esc(pr.id)} ${ticket}</span>
        <span class="fx-title">${esc(pr.title || "")}</span></div>
      <span class="fx-hero">${svg("bot", { scale: 4 })}</span>
      <span class="fx-plus">+1</span>`;
    const colours = ["yellow", "red", "blue", "grass", "orange", "white"];
    if (!reduced()) {
      for (let b = 0; b < 4; b++) {
        const burst = document.createElement("span");
        burst.className = "fx-burst";
        burst.style.setProperty("--bx", `${[-180, 170, -90, 110][b]}px`);
        burst.style.setProperty("--by", `${[-30, -50, -110, -120][b]}px`);
        burst.style.setProperty("--d", `${180 + b * 260}ms`);
        for (let i = 0; i < 12; i++) {
          const a = (Math.PI * 2 * i) / 12;
          const bit = document.createElement("i");
          bit.style.setProperty("--c", `var(--sprite-${colours[(i + b) % colours.length]})`);
          bit.style.setProperty("--dx", `${Math.round(Math.cos(a) * 64)}px`);
          bit.style.setProperty("--dy", `${Math.round(Math.sin(a) * 64)}px`);
          burst.append(bit);
        }
        box.append(burst);
      }
    }
    layer().append(box);
    const hero = box.querySelector(".spr-bot");
    hero.dataset.frame = 4;
    setTimeout(() => box.classList.add("is-leaving"), 2900);
    setTimeout(() => box.remove(), 3400);
  }

  /* ------------------------------------------------------- the builder bot
     New shell: the bot beams out of its seat by the New shell button, beams in beside the
     new tab, hammers it together while sparks fly, and beams home. term.js picks the spot. */
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const stepped = (el, frames, ms, n) => el.animate(frames, { duration: ms, easing: `steps(${n}, end)`, fill: "forwards" }).finished.catch(() => {});
  const BEAM = [{ transform: "translateY(0) scaleY(1)", opacity: 1 }, { transform: "translateY(-90px) scaleY(1.6)", opacity: 0 }];
  const SQUASH = [{ transform: "scale(1, 1)" }, { transform: "scale(0.3, 1.5)" }, { transform: "scale(0.08, 0.1)" }];
  async function vanish(actor) {
    await stepped(actor.body, SQUASH, 240, 3);
    await stepped(actor.beam, BEAM, 220, 4);
  }
  async function appear(actor) {
    await stepped(actor.beam, [...BEAM].reverse(), 220, 4);
    await stepped(actor.body, [...SQUASH].reverse(), 240, 3);
    await stepped(actor.beam, [{ opacity: 1 }, { opacity: 0 }], 90, 2);
  }
  // Two mascots. The green slime stands for AI work (Claude Code); the blue bot for PixelDeck's
  // own deterministic actions (a new shell, merge, sync, decline, delete).
  const ROLE = { bot: { work: [0, 2], happy: 4, ko: 3 }, buddy: { work: [0, 1], happy: 2, ko: 3 } };
  const moveTo = (actor, { x, y }) => { actor.el.style.left = `${Math.round(x)}px`; actor.el.style.top = `${Math.round(y)}px`; };
  const seatOf = (actor) => { const r = actor.home.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.bottom }; };

  /** Takes the bot off its seat and starts it beaming out. Null when there is no show to give. */
  function builder(seat) {
    const home = seat?.querySelector(".spr");
    if (!home || !layer() || reduced() || seat.dataset.away || !home.getBoundingClientRect().width) return null;
    seat.dataset.away = "1";
    const el = document.createElement("div");
    el.className = "fx-actor";
    el.innerHTML = `<span class="fx-beam"></span><span class="fx-actor-body">${svg("bot", { scale: 2 })}</span><span class="fx-hammer" hidden>${svg("hammer", { scale: 2 })}</span>`;
    const actor = { el, seat, home, kind: "bot", beam: el.querySelector(".fx-beam"), body: el.querySelector(".fx-actor-body"), bot: el.querySelector(".spr-bot"), hammer: el.querySelector(".fx-hammer") };
    moveTo(actor, seatOf(actor));
    layer().append(el);
    home.style.visibility = "hidden";
    actor.out = vanish(actor);
    return actor;
  }
  /** Beams in beside target() (a live rect: the tab can grow while its name arrives), hammers, beams out. */
  async function construct(actor, onStart, target) {
    const spot = () => { const r = target?.(); return r?.width ? { x: r.right + 28, y: r.bottom } : null; };
    await actor.out;
    const at = spot();
    if (!at) return;
    moveTo(actor, at);
    await appear(actor);
    onStart?.();
    actor.hammer.hidden = false;
    const swing = actor.hammer.querySelector(".spr");
    let n = 0;
    const timer = setInterval(() => {
      n++;
      swing.dataset.frame = n % 2;
      actor.bot.dataset.frame = ROLE[actor.kind].work[n % 2];
      const now = spot();
      if (now) moveTo(actor, now);
      if (!(n % 2)) spark(target?.());
    }, 120);
    await sleep(960);
    clearInterval(timer);
    actor.hammer.hidden = true;
    actor.bot.dataset.frame = ROLE[actor.kind].happy;
    await sleep(260);
    actor.bot.dataset.frame = 0;
    await vanish(actor);
  }
  /** Beams back onto the seat and hands it back. An agent with no seat just leaves. */
  async function goHome(actor) {
    await actor.out;
    if (!actor.home) { actor.el.remove(); return; }
    moveTo(actor, seatOf(actor));
    await appear(actor);
    actor.el.remove();
    actor.home.style.visibility = "";
    delete actor.seat.dataset.away;
    flash(actor.home, 4, 500);
  }
  // An actor standing just left of a button, squashed flat until it beams in.
  function actorBeside(button, kind) {
    const r = button.getBoundingClientRect();
    const el = document.createElement("div");
    el.className = "fx-actor";
    el.innerHTML = `<span class="fx-beam"></span><span class="fx-actor-body">${svg(kind, { scale: 2 })}</span><span class="fx-hammer" hidden>${svg("hammer", { scale: 2 })}</span>`;
    const actor = { el, seat: null, home: null, kind, beam: el.querySelector(".fx-beam"), body: el.querySelector(".fx-actor-body"), bot: el.querySelector(`.spr-${kind}`), hammer: el.querySelector(".fx-hammer") };
    actor.body.style.transform = "scale(0.08, 0.1)";
    moveTo(actor, { x: r.left - 18, y: r.bottom });
    layer().append(el);
    return actor;
  }
  const usable = (button) => layer() && !reduced() && button?.isConnected && button.getBoundingClientRect().width;

  /** The blue bot works beside a button while a deterministic action runs; done(ok) sends it off. */
  function worker(button) {
    if (!usable(button)) return { done() {} };
    const actor = actorBeside(button, "bot");
    const shown = appear(actor).then(() => play(actor.bot, [ROLE.bot.work, 140]));
    return {
      async done(ok = true) {
        await shown;
        stop(actor.bot, ok ? ROLE.bot.happy : ROLE.bot.ko);
        await sleep(ok ? 450 : 700);
        await vanish(actor);
        actor.el.remove();
      },
    };
  }

  /** The green slime answers an AI action: it pops up at the button, then is handed to the
      terminal to build the new tab (construct, then goHome, which lets it go). */
  function agent(button) {
    if (!usable(button)) return null;
    const actor = actorBeside(button, "buddy");
    actor.out = (async () => {
      await appear(actor);
      play(actor.bot, [ROLE.buddy.work, 130]);
      await sleep(520);
      stop(actor.bot);
      await vanish(actor);
    })();
    return actor;
  }
  async function dismiss(actor) {
    if (!actor) return;
    await actor.out;
    actor.el.remove();
  }

  function spark(rect) {
    if (!rect || !rect.width || !layer()) return;
    const bit = document.createElement("span");
    bit.className = "fx-spark";
    bit.innerHTML = svg("spark", { scale: 2 });
    bit.style.left = `${Math.round(rect.right - 4)}px`;
    bit.style.top = `${Math.round(rect.top + 2 + Math.random() * Math.max(0, rect.height - 12))}px`;
    bit.style.setProperty("--sx", `${Math.round(6 + Math.random() * 12)}px`);
    bit.style.setProperty("--sy", `${Math.round(-6 - Math.random() * 12)}px`);
    layer().append(bit);
    setTimeout(() => bit.remove(), 450);
  }

  /** A new tab pops in with a small puff. */
  function spawn(el) {
    if (!el || reduced()) return;
    el.classList.remove("is-spawning");
    void el.offsetWidth;
    el.classList.add("is-spawning");
    setTimeout(() => el.classList.remove("is-spawning"), 700);
  }

  hoverables();
  idle();
  return { svg, play, stop, flash, die, celebrate, builder, construct, goHome, worker, agent, dismiss, spawn };
})();
