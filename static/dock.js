"use strict";

/* The arcade dock: Tetris, Snake and Flappy beside the terminal, switched on from the Layout
   menu or the settings. And the bot on the terminal bar, which now and then suggests a game
   while one of your pull requests waits on reviewers or on its merge. It never takes focus
   from a terminal, never repeats a line it said recently, and goes quiet when told. */

const Arcade = (() => {
  const WIDTH_KEY = "prdesk.dock.width.v1";
  const SAID_KEY = "prdesk.bubble.said.v1";
  const QUIET_KEY = "prdesk.bubble.quiet.v1";
  const FIRST_AFTER = 2 * 60 * 1000, GAP = 10 * 60 * 1000, PER_DAY = 6, SHOW_MS = 14000;
  const NAMES = { tetris: "Tetris", snake: "Snake", flappy: "Flappy" };
  let root = null, dock = null, slot = null, game = null, bubble = null;
  let lastBubble = performance.now() - GAP + FIRST_AFTER, lastTyping = 0, timer = 0;
  const read = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
  const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } };

  /* ---------------------------------------------------------------- the dock */
  function attach(termRoot) {
    root = termRoot;
    dock = root.querySelector(".games-dock");
    slot = dock.querySelector(".games-slot");
    const width = Number(read(WIDTH_KEY, 360)) || 360;
    dock.style.setProperty("--dock-w", `${Math.min(640, Math.max(260, width))}px`);
    dock.querySelector(".dock-handle").addEventListener("pointerdown", resize);
    dock.addEventListener("click", (e) => { if (e.target.closest("[data-dock-close]")) Prefs.set({ games: false }); });
    root.addEventListener("keydown", () => { lastTyping = performance.now(); }, true);
    root.addEventListener("click", (e) => {
      const play = e.target.closest("[data-bubble-play]");
      if (play) { hideBubble(); open(play.dataset.bubblePlay, true); return; }
      if (e.target.closest("[data-bubble-later]")) { hideBubble(); write(QUIET_KEY, Date.now() + 30 * 60 * 1000); }
    });
    Prefs.onChange((p, patch) => { if ("games" in patch) (p.games ? open(null, false) : close()); if ("bubbles" in patch && !p.bubbles) hideBubble(); });
    if (Prefs.get("games")) open(null, false);
    clearInterval(timer);
    timer = setInterval(maybeSuggest, 30000);
  }
  function open(name, focus) {
    if (!dock || typeof Games === "undefined") return;
    if (!Prefs.get("games")) { Prefs.set({ games: true }); }
    dock.hidden = false;
    if (!game) game = Games.mount(slot);
    if (name && Games.names.includes(name)) game.play(name);
    if (focus) game.focus();
    if (typeof Layout !== "undefined") Layout.activate("terminal");
    requestAnimationFrame(() => Term.fit());
  }
  function close() {
    if (!dock) return;
    if (game) { game.unmount(); game = null; }
    dock.hidden = true;
    requestAnimationFrame(() => Term.fit());
  }
  function resize(e) {
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    const right = dock.getBoundingClientRect().right;
    const move = (ev) => dock.style.setProperty("--dock-w", `${Math.round(Math.min(640, Math.max(260, right - ev.clientX)))}px`);
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      write(WIDTH_KEY, parseInt(dock.style.getPropertyValue("--dock-w"), 10));
      Term.fit();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  }

  /* ---------------------------------------------------------------- what the bot says */
  const pick = (list, seed) => list[Math.abs(seed) % list.length];
  const OPENERS = [
    (w) => `#${w.id} is still waiting on ${w.who}.`,
    (w) => `#${w.id} has waited ${w.age} so far.`,
    (w) => `Still no merge on #${w.id}.`,
    (w) => `${w.repo} #${w.id} is sitting in the queue.`,
    (w) => `Your branch ${w.branch} is waiting its turn.`,
    (w) => (w.approved ? `#${w.id} is approved. Now it waits for a merge.` : `Nobody has approved #${w.id} yet.`),
    (w) => `Reviewers are busy. #${w.id} is patient.`,
    (w) => `I checked #${w.id} again. Nothing new.`,
  ];
  const MIDDLES = [
    "Watching a pull request does not make it merge faster. I tested it.",
    "The slime says it can beat your high score.",
    "My circuits are getting bored.",
    "I ran the numbers: a quick game is more fun than refreshing.",
    "Rumour says reviewers merge faster when nobody is looking.",
    "I polished my pixels twice already.",
    "Refreshing is not a hobby.",
    "The queue will still be here in five minutes.",
    "I promise to tell you the moment something moves.",
    "Fun fact: I never lose at waiting. I do lose at Tetris.",
  ];
  const PITCHES = {
    tetris: ["One round of Tetris while we wait?", "Lines clear faster than reviews. Tetris?", "Stack some blocks with me?"],
    snake: ["Snake? The slime volunteers as the head.", "Help the slime eat some sparkles?", "Snake round? I will be the bonus target."],
    flappy: ["Race me in Flappy? Slime against bot.", "Flappy: I bet the slime crashes first.", "Fly through some pipes with me?"],
  };
  function best(name) { const v = Number(read(`prdesk.games.best.${name}`, 0)); return Number.isFinite(v) ? v : 0; }
  // Lines are picked by their parts, and a combination said in the last 200 lines is skipped.
  function line(waiting) {
    const said = read(SAID_KEY, []);
    const seen = new Set(Array.isArray(said) ? said : []);
    const names = Object.keys(PITCHES);
    let tries = 0, choice = null;
    while (tries++ < 60) {
      const o = Math.floor(Math.random() * OPENERS.length), m = Math.floor(Math.random() * MIDDLES.length);
      const g = names[Math.floor(Math.random() * names.length)];
      const p = Math.floor(Math.random() * (PITCHES[g].length + (best(g) ? 1 : 0)));
      const id = `${o}.${m}.${g}.${p}.${waiting.id}`;
      if (seen.has(id)) continue;
      choice = { id, o, m, g, p };
      break;
    }
    if (!choice) { write(SAID_KEY, []); return line(waiting); }
    write(SAID_KEY, [...seen, choice.id].slice(-200));
    const pitch = choice.p < PITCHES[choice.g].length ? PITCHES[choice.g][choice.p] : `Your ${NAMES[choice.g]} best is ${best(choice.g)}. Beat it?`;
    return { text: `${OPENERS[choice.o](waiting)} ${MIDDLES[choice.m]} ${pitch}`, game: choice.g };
  }
  function waitingPr() {
    if (!state.me) return null;
    const mine = state.people[state.me.uuid];
    if (!mine) return null;
    const b = buckets(mine, "me");
    const list = [...b.approved, ...b.waiting];
    if (!list.length) return null;
    const pr = list[Math.floor(Math.random() * list.length)];
    const s = signals(pr);
    const pending = (pr.reviews || []).filter((r) => r.reviewer && r.state === "pending").map((r) => firstName(r.name));
    const age = duration(Date.now() - new Date(pr.created_on).getTime());
    return { id: pr.id, repo: pr.repo, branch: pr.source, approved: s.approved, who: pending.length ? listNames(pending) : "the reviewers",
      age: age ? `${age.n} ${{ min: "minute", h: "hour", d: "day" }[age.unit] || age.unit}${age.n === 1 ? "" : "s"}` : "a while" };
  }
  function maybeSuggest(force = false) {
    if (!root || bubble) return false;
    if (!force) {
      if (!Prefs.get("bubbles") || !Prefs.get("mascots") || document.hidden) return false;
      if (performance.now() - lastBubble < GAP || performance.now() - lastTyping < 60000) return false;
      if (Date.now() < Number(read(QUIET_KEY, 0))) return false;
      if (game && dock.contains(document.activeElement)) return false;  // already playing
      if (typeof Layout !== "undefined" && !Layout.isShown("terminal")) return false;
      const today = new Date().toDateString();
      const count = read("prdesk.bubble.count.v1", { day: "", n: 0 });
      if (count.day === today && count.n >= PER_DAY) return false;
      write("prdesk.bubble.count.v1", { day: today, n: count.day === today ? count.n + 1 : 1 });
    }
    const waiting = waitingPr() || (force ? { id: 42, repo: "pixel-arcade", branch: "feat/high-scores", approved: false, who: "the reviewers", age: "a while" } : null);
    if (!waiting) return false;
    const { text, game: g } = line(waiting);
    showBubble(text, g);
    return true;
  }
  function showBubble(text, g) {
    const bar = root.querySelector(".term-bar");
    const mascot = bar?.querySelector(".term-mascot");
    if (!bar || !mascot) return;
    lastBubble = performance.now();
    bubble = document.createElement("div");
    bubble.className = "bot-bubble";
    bubble.setAttribute("role", "status");
    bubble.innerHTML = `<p>${esc(text)}</p><div class="bot-bubble-actions">
      <button class="btn btn--primary" type="button" data-bubble-play="${g}">Play ${NAMES[g]}</button>
      <button class="btn btn--quiet" type="button" data-bubble-later>Not now</button></div>`;
    const m = mascot.getBoundingClientRect(), b = bar.getBoundingClientRect();
    // The bubble hangs from the bar's right edge; its tail points up at the bot.
    // From the bubble's inner right edge (bar edge, 8px gap, 9px frame) to the tail's tip.
    bubble.style.setProperty("--tail-r", `${Math.max(0, Math.round(b.right - (m.left + m.width / 2) - 8 - 9 - 4))}px`);
    bar.append(bubble);
    const svgEl = mascot.querySelector(".spr");
    if (svgEl && typeof Sprites !== "undefined") Sprites.flash(svgEl, 4, 700);  // a little wave
    const t = setTimeout(hideBubble, SHOW_MS);
    bubble.addEventListener("mouseenter", () => clearTimeout(t), { once: true });
    bubble.addEventListener("mouseleave", () => setTimeout(hideBubble, 4000), { once: true });
  }
  function hideBubble() {
    if (!bubble) return;
    const el = bubble;
    bubble = null;
    el.classList.add("is-leaving");
    setTimeout(() => el.remove(), 300);
  }
  return { attach, open, close, suggest: () => maybeSuggest(true), isOpen: () => !!game, line };
})();
