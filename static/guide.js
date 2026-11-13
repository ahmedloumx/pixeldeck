"use strict";

/* The guide: a short tour on the first run, with a spotlight on the real thing each stop
   talks about and the slime or the bot telling it. It comes back from the ? in the status
   bar. A stop whose target is not on screen is skipped. Arrows move, Escape ends it. */

const Guide = (() => {
  const KEY = "prdesk.guide.v1";
  const STOPS = [
    { title: "Welcome to PixelDeck", who: "both", text: "Your pull requests, your team's, and the terminals and agents that work on them, in one window. A quick tour: nine stops, about a minute." },
    { target: ".bar .field:has(#person)", who: "bot", title: "Whose pull requests", text: "Pick yourself, a teammate or the whole team. The picker on its left keeps one Bitbucket project, or all of them." },
    { target: '[data-panel="queue"] .sec-chips', panel: "queue", who: "bot", title: "The queue", text: "Sorted by what needs you: changes asked, threads to answer, tasks. Then what waits on reviewers, what is approved, what merged. j and k move, Enter opens." },
    { target: '.slot-tab[data-tab="insights"]', panel: "insights", who: "buddy", title: "Your numbers", text: "Merges per week, time to merge, who reviews you, when merges land. Pick a range from 7 days to 120 at the top of the panel." },
    { target: '.slot-tool[data-act="duplicate"]', who: "buddy", title: "Copies", text: "Copy the Queue or Insights to put two people, or two ranges, side by side. Each copy keeps its own view." },
    { target: ".term-bar", panel: "terminal", who: "buddy", title: "Terminals and Claude", text: "The Claude buttons on a pull request start Claude Code here with a brief, and I come along. Drop files on a terminal to paste their paths." },
    { target: "#status .st-api", who: "bot", title: "The status bar", text: "API calls used this hour, when your pull requests were read and the next read, and whether Bitbucket answers." },
    { target: "#bell", who: "bot", title: "Notifications", text: "Approvals, changes asked, new pull requests: they spin in like a slot machine and land behind the bell. Silence them any time." },
    { target: "#layout-menu", who: "buddy", title: "Layout and arcade", text: "Arrange the panels, copy them, and switch on the games beside the terminal. Waiting on a merge is more fun with Tetris." },
    { target: "#settings-btn", who: "bot", title: "Settings", text: "What PixelDeck is connected to, what notifies you, and your look. The ? next to it brings this tour back." },
  ];
  let el = null, i = 0, stops = [];
  const visible = (sel) => { const t = sel && document.querySelector(sel); if (!t) return null; const r = t.getBoundingClientRect(); return r.width && r.height && !t.closest("[hidden]") ? t : null; };

  function start() {
    end(false);
    stops = STOPS;
    i = 0;
    el = document.createElement("div");
    el.className = "guide";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-label", "Guide");
    el.innerHTML = `<div class="guide-hole" aria-hidden="true"></div><section class="guide-card" tabindex="-1"></section>`;
    document.body.append(el);
    el.addEventListener("click", onClick);
    addEventListener("keydown", onKey, true);
    addEventListener("resize", place);
    show(0, 1);
  }
  function show(n, dir) {
    if (!el) return;
    // skip stops whose target is not on screen
    while (n >= 0 && n < stops.length) {
      const s = stops[n];
      if (s.panel && typeof Layout !== "undefined") Layout.activate(s.panel);
      if (!s.target || visible(s.target)) break;
      n += dir;
    }
    if (n < 0) n = 0;
    if (n >= stops.length) return end(true);
    i = n;
    const s = stops[i];
    const who = s.who === "both" ? `${Sprites.svg("buddy", { scale: 3 })}${Sprites.svg("bot", { scale: 3 })}` : Sprites.svg(s.who, { scale: 3 });
    const dots = stops.map((_, k) => `<i${k === i ? ' class="is-on"' : ""}></i>`).join("");
    el.querySelector(".guide-card").innerHTML = `<div class="guide-who" aria-hidden="true">${who}</div>
      <div class="guide-words"><h2>${esc(s.title)}</h2><p>${esc(s.text)}</p></div>
      <footer class="guide-foot"><span class="guide-dots" aria-label="Stop ${i + 1} of ${stops.length}">${dots}</span>
        <button class="btn btn--quiet" type="button" data-guide="skip">${i === stops.length - 1 ? "Close" : "Skip"}</button>
        ${i ? '<button class="btn" type="button" data-guide="back">Back</button>' : ""}
        <button class="btn btn--primary" type="button" data-guide="next">${i === stops.length - 1 ? "Let's go" : "Next"}</button></footer>`;
    requestAnimationFrame(place);
    el.querySelector('[data-guide="next"]').focus({ preventScroll: true });
  }
  function place() {
    if (!el) return;
    const s = stops[i];
    const hole = el.querySelector(".guide-hole"), card = el.querySelector(".guide-card");
    const t = s.target && visible(s.target);
    el.classList.toggle("is-center", !t);
    if (!t) { hole.style.cssText = ""; card.style.left = card.style.top = ""; return; }
    const r = t.getBoundingClientRect(), pad = 6;
    hole.style.cssText = `left:${r.left - pad}px;top:${r.top - pad}px;width:${r.width + pad * 2}px;height:${r.height + pad * 2}px`;
    const cw = card.offsetWidth, ch = card.offsetHeight;
    const below = r.bottom + pad + 14 + ch < innerHeight;
    const top = below ? r.bottom + pad + 14 : Math.max(12, r.top - pad - 14 - ch);
    const left = Math.min(innerWidth - cw - 12, Math.max(12, r.left + r.width / 2 - cw / 2));
    card.style.left = `${Math.round(left)}px`;
    card.style.top = `${Math.round(top)}px`;
  }
  function onClick(e) {
    const b = e.target.closest("[data-guide]");
    if (!b) return;
    const act = b.dataset.guide;
    if (act === "next") show(i + 1, 1);
    else if (act === "back") show(i - 1, -1);
    else end(true);
  }
  function onKey(e) {
    if (!el) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); end(true); }
    else if (e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); show(i + 1, 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); show(i - 1, -1); }
    else if (e.key === "Tab") {
      // keep Tab inside the card
      const items = [...el.querySelectorAll("button")];
      const at = items.indexOf(document.activeElement);
      e.preventDefault();
      items[(at + (e.shiftKey ? -1 : 1) + items.length) % items.length]?.focus();
    } else if (!e.ctrlKey && !e.metaKey && !e.altKey) e.stopPropagation();  // no shortcuts under the tour
  }
  function end(done) {
    if (!el) return;
    removeEventListener("keydown", onKey, true);
    removeEventListener("resize", place);
    el.remove();
    el = null;
    if (done) { try { localStorage.setItem(KEY, "done"); } catch { /* storage blocked */ } }
  }
  // The first run only; a test or a window opened for one panel passes ?noguide or ?panel.
  function firstRun() {
    const q = new URLSearchParams(location.search);
    if (q.has("panel") || q.has("noguide")) return;
    let seen = null;
    try { seen = localStorage.getItem(KEY); } catch { return; }
    if (!seen) setTimeout(start, 700);
  }
  return { start, firstRun, end: () => end(true), isOpen: () => !!el };
})();
