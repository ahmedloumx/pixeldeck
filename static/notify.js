"use strict";

/* Notifications: what moved on the pull requests you and your team have open.
   The server logs what each refresh found (events.py); this polls that log, spins a slot
   machine banner for news, keeps the history behind the bell, and lets you silence it:
   per kind, your own pull requests only, or snoozed for a while. Silenced news still
   lands behind the bell, it just does not spin. A burst (one refresh finding several
   things) is one banner with a count, never a stack. Do not disturb silences it until you turn
   it off. The bell's panel has a second tab, the quick toggles (control.js). */

const Notify = (() => {
  const CURSOR_KEY = "prdesk.events.cursor.v1";
  const EPOCH_KEY = "prdesk.events.epoch.v1";
  const HISTORY_KEY = "prdesk.events.history.v1";
  const PREFS_KEY = "prdesk.notify.v1";
  const HISTORY_MAX = 120;
  const POLL_MS = 5000, POLL_HIDDEN_MS = 30000;
  const STAY_MS = 9000;
  const KINDS = {
    review_requested: { label: "Reviews asked of you", reel: "YOU?", tone: "due" },
    behind: { label: "Your branch fell behind its target", reel: "SYNC", tone: "due" },
    unapproved: { label: "Approvals taken back", reel: "UNDO", tone: "bad" },
    tasks: { label: "New tasks", reel: "TASK", tone: "info" },
    approved: { label: "Approvals", reel: "OK!", tone: "ok" },
    changes_requested: { label: "Changes requested", reel: "FIX", tone: "bad" },
    comments: { label: "New comments", reel: "+", tone: "info" },
    opened: { label: "Pull requests opened", reel: "NEW", tone: "info" },
    merged: { label: "Merged", reel: "WIN", tone: "ok" },
    declined: { label: "Declined", reel: "NOPE", tone: "bad" },
    updated: { label: "New commits", reel: "PUSH", tone: "info" },
  };
  // Everything notifies unless you turn it off: you asked to hear about every change.
  const DEFAULT_KINDS = Object.fromEntries(Object.keys(KINDS).map((k) => [k, true]));
  const FILLER = ["7", "$", "BAR", "?", "777", "!", "%", "#", "*", "+"];
  const read = (key, fallback) => { try { const v = JSON.parse(localStorage.getItem(key)); return v ?? fallback; } catch { return fallback; } };
  const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage blocked */ } };
  const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.motion === "calm";

  const saved = read(PREFS_KEY, {}) || {};
  // Settings saved before every kind was on had new commits off by default, not by choice.
  if (!saved.v) { saved.kinds = { ...(saved.kinds || {}), updated: true }; saved.v = 2; }
  let prefs = { scope: "all", snoozeUntil: 0, ...saved, kinds: { ...DEFAULT_KINDS, ...(saved.kinds || {}) } };
  let history = read(HISTORY_KEY, []);
  if (!Array.isArray(history)) history = [];
  let cursor = read(CURSOR_KEY, null);
  let epoch = read(EPOCH_KEY, null);
  let opts = null, timer = 0, queue = [], current = null, filter = "all", polling = false, tab = "notes";
  const listeners = new Set();
  const changed = () => { for (const fn of listeners) fn(); paintPanel(); };

  /* ---------------------------------------------------------------- words */
  const first = (name) => String(name || "").trim().split(/\s+/)[0] || "Someone";
  const meUuid = () => opts?.me?.()?.uuid || null;
  const isMine = (ev) => !!ev.author?.uuid && ev.author.uuid === meUuid();
  function sentence(ev) {
    const actor = first(ev.actor?.name);
    const whose = isMine(ev) ? "your" : `${first(ev.author?.name)}'s`;
    const pr = `#${ev.pr_id}`;
    switch (ev.kind) {
      case "opened": return `${actor} opened ${pr}`;
      case "approved": return `${actor} approved ${whose} ${pr}`;
      case "changes_requested": return `${actor} asked for changes on ${whose} ${pr}`;
      case "comments": return ev.names?.length === 1
        ? `${first(ev.names[0])} left ${ev.count === 1 ? "a comment" : `${ev.count} comments`} on ${whose} ${pr}`
        : `${ev.count === 1 ? "A new comment" : `${ev.count} new comments`} on ${whose} ${pr}`;
      case "updated": return `${actor} pushed new commits to ${pr}`;
      case "behind": return `${pr} is ${ev.count}${ev.more ? "+" : ""} ${ev.count === 1 && !ev.more ? "commit" : "commits"} behind ${ev.dest || "its target"}`;
      case "unapproved": return `${actor} took back the approval on ${whose} ${pr}`;
      case "tasks": return `${ev.count === 1 ? "A new task" : `${ev.count} new tasks`} on ${whose} ${pr}`;
      case "merged": return ev.actor ? `${actor} merged ${whose} ${pr}` : `${isMine(ev) ? "Your" : whose} ${pr} was merged`;
      case "declined": return ev.actor ? `${actor} declined ${whose} ${pr}` : `${isMine(ev) ? "Your" : whose} ${pr} was declined`;
      case "review_requested": return `${actor} asked you to review ${pr}`;
      default: return `${pr} changed`;
    }
  }
  const face = (ev) => initials(ev.actor?.name || ev.author?.name || "?");
  const reelWord = (ev) => (ev.kind === "comments" ? `+${Math.min(99, ev.count || 1)}` : KINDS[ev.kind]?.reel || "?");

  /* ---------------------------------------------------------------- silence */
  const snoozed = () => prefs.snoozeUntil > Date.now();
  const quiet = () => snoozed() || !!prefs.dnd;
  function setDnd(on) { setPrefs({ dnd: !!on }); if (on) { if (current) leave(current); queue = []; } }
  function setPrefs(patch) {
    prefs = { ...prefs, ...patch, kinds: { ...prefs.kinds, ...(patch.kinds || {}) } };
    write(PREFS_KEY, prefs);
    changed();
  }
  function snooze(ms) { setPrefs({ snoozeUntil: Date.now() + ms }); if (current) leave(current); queue = []; }
  function snoozeTomorrow() {
    const t = new Date(); t.setDate(t.getDate() + 1); t.setHours(9, 0, 0, 0);
    snooze(t.getTime() - Date.now());
  }
  const wake = () => setPrefs({ snoozeUntil: 0 });
  // Your own merges already get the merge moment, so they never spin twice.
  const spins = (ev) => !quiet() && prefs.kinds[ev.kind] !== false
    && (prefs.scope === "all" || isMine(ev) || ev.kind === "review_requested")
    && !(ev.kind === "merged" && isMine(ev));

  /* ---------------------------------------------------------------- polling */
  async function poll() {
    clearTimeout(timer);
    timer = setTimeout(poll, document.hidden ? POLL_HIDDEN_MS : POLL_MS);
    if (!opts || (opts.ready && !opts.ready()) || polling) return;
    polling = true;  // a tab switch must not start a second poll on the same cursor
    try { await pollOnce(); } finally { polling = false; }
  }
  async function pollOnce() {
    let res;
    try { res = await opts.api(`events${cursor == null ? "" : `?since=${cursor}`}`); } catch { return; }
    if (res.epoch && res.epoch !== epoch) {
      // A new log (first run, or the server lost its cache): its ids are not ours. Read
      // what it holds as history, not news, and forget the old history it would collide with.
      const all = cursor == null ? res : await opts.api("events").catch(() => res);
      history = (all.events || []).slice().reverse().map((ev) => ({ ...ev, read: true })).slice(0, HISTORY_MAX);
      cursor = all.last; epoch = res.epoch;
      write(CURSOR_KEY, cursor); write(EPOCH_KEY, epoch); write(HISTORY_KEY, history); changed();
      return;
    }
    const fresh = res.events || [];
    if (cursor == null || res.last < cursor) {
      // An older server without epochs: a first run, or its log started over.
      history = mergeHistory(fresh.map((ev) => ({ ...ev, read: true })));
      cursor = res.last;
      write(CURSOR_KEY, cursor); write(HISTORY_KEY, history); changed();
      return;
    }
    if (!fresh.length) return;
    cursor = Math.max(cursor, ...fresh.map((ev) => ev.id));
    history = mergeHistory(fresh.map((ev) => ({ ...ev, read: false })));
    write(CURSOR_KEY, cursor); write(HISTORY_KEY, history);
    const news = fresh.filter(spins);
    if (news.length) show(news);
    changed();
  }
  function mergeHistory(items) {
    const ids = new Set(history.map((ev) => ev.id));
    return [...items.filter((ev) => !ids.has(ev.id)).reverse(), ...history].slice(0, HISTORY_MAX);
  }

  /* ---------------------------------------------------------------- the slot banner */
  function show(events) {
    if (current) { current.extra += events.length; paintExtra(current); return; }
    queue.push(...events);
    next();
  }
  function next() {
    if (current || !queue.length) return;
    const [ev, ...rest] = queue;
    queue = [];
    spin(ev, rest.length);
  }
  function reel(final, i, still) {
    const n = 7 + i * 3;
    const symbols = still ? [final] : [...Array.from({ length: n - 1 }, (_, k) => FILLER[(k * 7 + i * 3 + final.length) % FILLER.length]), final];
    return `<span class="sb-reel" style="--n:${symbols.length - 1};--d:${700 + i * 350}ms"><span class="sb-strip">${symbols.map((s) => `<span class="sb-sym">${esc(s)}</span>`).join("")}</span></span>`;
  }
  function spin(ev, extra) {
    const box = document.getElementById("notices");
    if (!box) return;
    const tone = KINDS[ev.kind]?.tone || "info";
    const jackpot = (ev.kind === "approved" && isMine(ev)) || ev.kind === "review_requested";
    const still = calm();
    const el = document.createElement("div");
    el.className = `slot-banner is-${tone}${jackpot ? " is-jackpot" : ""}${still ? " is-still" : ""}`;
    el.setAttribute("role", "status");
    el.innerHTML = `<span class="sb-lights" aria-hidden="true"></span>
      <div class="sb-reels" aria-hidden="true">${reel(face(ev), 0, still)}${reel(reelWord(ev), 1, still)}${reel(`#${ev.pr_id}`, 2, still)}</div>
      <button class="sb-body" type="button" data-notify-open="${ev.id}">
        <b class="sb-line">${esc(sentence(ev))}</b>
        <span class="sb-title">${esc(ev.title || "")}</span>
        <span class="sb-meta"><span class="branch">${esc(ev.repo || "")}</span><span class="sb-extra"></span></span>
      </button>
      <div class="sb-tools">
        ${ev.kind === "behind" ? `<button class="sb-tool sb-sync" type="button" data-notify-sync="${ev.id}" data-tip="Merge ${esc(ev.dest || "the target")} into the branch and push, like Bitbucket's Sync">SYNC</button>` : ""}
        <button class="sb-tool" type="button" data-notify-snooze="3600000" title="Silence for an hour" aria-label="Silence for an hour">zZ</button>
        <button class="sb-tool" type="button" data-notify-dismiss aria-label="Dismiss">×</button>
      </div>`;
    current = { el, ev, extra, timer: 0 };
    paintExtra(current);
    const state = current;
    el.addEventListener("mouseenter", () => clearTimeout(state.timer));
    el.addEventListener("mouseleave", () => { state.timer = setTimeout(() => leave(state), 4000); });
    state.timer = setTimeout(() => leave(state), STAY_MS + (still ? 0 : 1400));
    box.prepend(el);
    if (!still) setTimeout(() => el.classList.add("is-landed"), 700 + 2 * 350 + 60);
  }
  function paintExtra(state) {
    const slot = state.el.querySelector(".sb-extra");
    slot.innerHTML = state.extra ? `<button class="sb-more" type="button" data-notify-bell>and ${state.extra} more</button>` : "";
  }
  function leave(state) {
    if (!state || !state.el.isConnected || state.el.classList.contains("is-leaving")) return;
    clearTimeout(state.timer);
    state.el.classList.add("is-leaving");
    setTimeout(() => { state.el.remove(); if (current === state) { current = null; next(); } }, calm() ? 0 : 420);
  }

  /* ---------------------------------------------------------------- the bell */
  const unread = () => history.filter((ev) => !ev.read).length;
  function markRead() { let any = false; for (const ev of history) if (!ev.read) { ev.read = true; any = true; } if (any) { write(HISTORY_KEY, history); changed(); } }
  function dayLabel(at) {
    const d = new Date(at * 1000), t = new Date();
    const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((day(t) - day(d)) / 86400000);
    return diff === 0 ? "Today" : diff === 1 ? "Yesterday" : d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "short" });
  }
  function itemsHtml() {
    const list = history.filter((ev) => filter === "all" || (filter === "mine" ? isMine(ev) || ev.kind === "review_requested" : !isMine(ev)));
    if (!list.length) return `<div class="bell-empty">${typeof Sprites !== "undefined" ? Sprites.svg("buddy", { scale: 3 }) : ""}<p>${history.length ? "Nothing here for this filter." : "Nothing yet. News about open pull requests lands here."}</p></div>`;
    let last = "", out = "", k = 0;
    for (const ev of list) {
      const label = dayLabel(ev.at);
      if (label !== last) { out += `<li class="bell-day">${esc(label)}</li>`; last = label; }
      const tone = KINDS[ev.kind]?.tone || "info";
      out += `<li style="--k:${Math.min(k++, 12)}"><button class="bell-item is-${tone}${ev.read ? "" : " is-unread"}" type="button" data-notify-open="${ev.id}">
        <span class="bell-face" aria-hidden="true">${esc(face(ev))}</span>
        <span class="bell-text"><b>${esc(sentence(ev))}</b><span>${esc(ev.title || "")}</span><span class="bell-meta"><span class="branch">${esc(ev.repo || "")}</span> ${esc(ago(new Date(ev.at * 1000).toISOString()))}</span></span>
        <span class="bell-kind">${esc(reelWord(ev))}</span></button>${ev.kind === "behind" ? `<button class="bell-sync btn" type="button" data-notify-sync="${ev.id}">Sync</button>` : ""}</li>`;
    }
    return `<ol class="bell-list">${out}</ol>`;
  }
  function silenceHtml() {
    if (prefs.dnd) return `<p class="bell-snoozed"><span>Do not disturb is on: no banners until you turn it off.</span><button class="btn btn--quiet" type="button" data-notify-dnd-off>Turn off</button></p>`;
    if (snoozed()) {
      const until = new Date(prefs.snoozeUntil).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" });
      return `<p class="bell-snoozed"><span>Silenced until ${esc(until)}.</span><button class="btn btn--quiet" type="button" data-notify-wake>Wake up</button></p>`;
    }
    return `<div class="bell-silence"><span>Silence</span>
      <button class="date-chip" type="button" data-notify-snooze="3600000">1 hour</button>
      <button class="date-chip" type="button" data-notify-snooze="tomorrow">Until tomorrow</button>
      <button class="date-chip" type="button" data-notify-settings>Choose what spins</button></div>`;
  }
  function panelHtml() {
    const cc = typeof ControlCenter !== "undefined";
    if (!cc) tab = "notes";
    const n = unread();
    const top = cc ? `<div class="bell-switch" role="tablist" aria-label="Bell">
        <button type="button" role="tab" data-bell-tab="notes" aria-selected="${tab === "notes"}">Notifications${n ? ` <span class="bell-count num8">${n > 99 ? "99+" : n}</span>` : ""}</button>
        <button type="button" role="tab" data-bell-tab="toggles" aria-selected="${tab === "toggles"}">Quick toggles</button></div>` : "";
    if (tab === "toggles") return `${top}<div class="bell-scroll bell-scroll--cc" role="tabpanel">${ControlCenter.html()}</div>`;
    const tabs = [["all", "All"], ["mine", "Yours"], ["team", "Team"]].map(([id, label]) => `<button class="date-chip" type="button" data-notify-filter="${id}" aria-pressed="${filter === id}">${label}</button>`).join("");
    return `${top}<header class="bell-head">${cc ? "" : "<h2>Notifications</h2>"}<div class="bell-tabs" role="group" aria-label="Show">${tabs}</div>
        <button class="btn btn--quiet" type="button" data-notify-read${n ? "" : " disabled"}>Mark all read</button></header>
      ${silenceHtml()}<div class="bell-scroll" role="tabpanel">${itemsHtml()}</div>`;
  }
  let panel = null;
  function paintPanel() { if (panel && !panel.hidden) panel.innerHTML = panelHtml(); }
  function toggle(anchor, open, which) {
    if (which) tab = which;
    if (!panel) {
      panel = document.createElement("section");
      panel.className = "bell-panel";
      panel.id = "bell-panel";
      panel.setAttribute("aria-label", "Notifications");
      panel.hidden = true;
      document.body.append(panel);
    }
    const show = open ?? panel.hidden;
    if (!show && panel.hidden) return;  // closing what is closed must not mark everything read
    if (show) {
      const r = (anchor || document.getElementById("bell"))?.getBoundingClientRect();
      if (r) { panel.style.top = `${Math.round(r.bottom + 8)}px`; panel.style.right = `${Math.max(8, Math.round(innerWidth - r.right - 8))}px`; }
      panel.hidden = false;
      paintPanel();
      panel.classList.remove("is-opening");
      void panel.offsetWidth;
      panel.classList.add("is-opening");
      clearTimeout(panel._opening);
      panel._opening = setTimeout(() => panel.classList.remove("is-opening"), 700);
      document.getElementById("bell")?.setAttribute("aria-expanded", "true");
    } else {
      panel.hidden = true;
      document.getElementById("bell")?.setAttribute("aria-expanded", "false");
      if (typeof ControlCenter !== "undefined") ControlCenter.stopEditing();
      markRead();
    }
  }

  /* ---------------------------------------------------------------- settings section */
  function settingsHtml() {
    const kinds = Object.entries(KINDS).map(([k, v]) => `<label class="switch"><input type="checkbox" data-notify-kind="${k}"${prefs.kinds[k] !== false ? " checked" : ""}><span class="switch-knob" aria-hidden="true"></span>${esc(v.label)}</label>`).join("");
    const scope = [["all", "Everything from the team"], ["mine", "Only your pull requests and reviews asked of you"]].map(([v, label]) => `<label class="check"><input type="radio" name="notify-scope" value="${v}" data-notify-scope${prefs.scope === v ? " checked" : ""}> ${esc(label)}</label>`).join("");
    return `<div class="set-block"><h3>What spins a banner</h3><p class="set-hint">Everything still lands behind the bell. Your own actions never notify you.</p><div class="switch-grid">${kinds}</div></div>
      <div class="set-block"><h3>From whom</h3>${scope}</div>
      <div class="set-block"><h3>Silence</h3>${silenceHtml()}<p><button class="btn" type="button" data-notify-test>Spin a test banner</button></p></div>`;
  }

  /* ---------------------------------------------------------------- events */
  function find(id) { return history.find((ev) => String(ev.id) === String(id)); }
  document.addEventListener("click", (e) => {
    const t = e.target;
    const open = t.closest("[data-notify-open]");
    if (open) {
      const ev = find(open.dataset.notifyOpen) || (current && String(current.ev.id) === open.dataset.notifyOpen ? current.ev : null);
      if (ev) { ev.read = true; write(HISTORY_KEY, history); changed(); if (opts?.onOpen) opts.onOpen(ev); }
      if (open.closest(".slot-banner")) leave(current);
      if (open.closest(".bell-panel")) toggle(null, false);
      return;
    }
    const sync = t.closest("[data-notify-sync]");
    if (sync) {
      const ev = find(sync.dataset.notifySync) || (current && String(current.ev.id) === sync.dataset.notifySync ? current.ev : null);
      if (ev && opts?.onSync) { if (current) clearTimeout(current.timer); opts.onSync(ev, sync); }
      return;
    }
    const sn = t.closest("[data-notify-snooze]");
    if (sn) {
      if (sn.dataset.notifySnooze === "tomorrow") snoozeTomorrow(); else snooze(Number(sn.dataset.notifySnooze));
      if (typeof toast === "function") toast("Notifications silenced.", "They still land behind the bell. Wake them from there.");
      return;
    }
    if (t.closest("[data-notify-wake]")) return wake();
    if (t.closest("[data-notify-dnd-off]")) return setDnd(false);
    const bt = t.closest("[data-bell-tab]");
    if (bt) { tab = bt.dataset.bellTab; if (typeof ControlCenter !== "undefined") ControlCenter.stopEditing(); return paintPanel(); }
    if (t.closest("[data-notify-dismiss]")) return leave(current);
    if (t.closest("[data-notify-read]")) return markRead();
    if (t.closest("[data-notify-test]")) return test();
    const f = t.closest("[data-notify-filter]");
    if (f) { filter = f.dataset.notifyFilter; return paintPanel(); }
    if (t.closest("[data-notify-settings]")) { toggle(null, false); if (opts?.onSettings) opts.onSettings("notifications"); return; }
    if (t.closest("[data-notify-bell]") || t.closest("#bell")) { e.preventDefault(); return toggle(t.closest("#bell")); }
    if (panel && !panel.hidden) {
      const path = e.composedPath();
      if (!path.includes(panel) && !path.some((n) => n.id === "st-tray" || n.id === "st-pins")) toggle(null, false);
    }
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.matches("[data-notify-kind]")) setPrefs({ kinds: { [t.dataset.notifyKind]: t.checked } });
    else if (t.matches("[data-notify-scope]")) setPrefs({ scope: t.value });
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && panel && !panel.hidden) toggle(null, false); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && opts) poll(); });

  // A banner made of made-up data, so trying the settings never shows a real name.
  function test() {
    const kinds = ["approved", "review_requested", "changes_requested", "comments", "opened"];
    const kind = kinds[Math.floor(Math.random() * kinds.length)];
    const me = opts?.me?.() || { uuid: "{you}", name: "You" };
    const ev = { id: `test-${Date.now()}`, kind, count: 3, pr_id: 42, title: "Teach the slime to jump higher", repo: "pixel-arcade",
      actor: { name: "Pixel Pat" }, author: kind === "opened" ? { name: "Pixel Pat" } : { uuid: me.uuid, name: me.name } };
    if (current) leave(current);
    setTimeout(() => spin(ev, 0), current ? 450 : 0);
  }

  function start(o) {
    opts = o;
    poll();
  }
  return { start, poll, unread, markRead, toggle, settingsHtml, snoozed: quiet, wake, onChange: (fn) => listeners.add(fn),
    dnd: () => !!prefs.dnd, setDnd, repaint: paintPanel, tab: () => tab,
    prefs: () => prefs, sentence, kinds: KINDS };
})();
