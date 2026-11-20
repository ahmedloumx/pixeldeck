"use strict";

/* Lyrics, from LRCLIB through PixelDeck's server (the made-up songs of the preview bring
   their own). Timed lyrics follow the song: the line playing glows like the deck's display,
   sung lines dim, the next ones wait below, and a click on a line jumps there. Lyrics
   without times show as text; a song LRCLIB lacks says so.

   The Music panel shows the big view in place of the library; the small player shows a few
   lines under its LYRICS tab. Both follow Spotify's position, so a pause or a skip made on
   the phone moves them too. A song is asked for once: the answer is kept. */

const MusicLyrics = (() => {
  const TICK_MS = 200;
  const cache = new Map();
  const views = new Set();
  let core = null, timer = 0;

  const keyOf = (item) => (item ? `${item.id || ""}|${item.name}|${(item.artists || []).join(",")}` : "");
  const live = () => core && (core.mode === "live" || core.mode === "demo");

  function load(item) {
    const key = keyOf(item);
    let hit = cache.get(key);
    if (hit) return hit;
    hit = { state: "loading" };
    cache.set(key, hit);
    const job = core.mode === "demo" ? Promise.resolve(MusicDemo.lyrics(item)) : api(`lyrics?${new URLSearchParams({
      track: item.name, artist: (item.artists || [])[0] || "", album: item.album || "", duration: String(Math.max(1, Math.round((item.duration_ms || 1000) / 1000))),
    })}`);
    job.then((data) => { hit.state = "ok"; hit.data = data; paintAll(); })
      .catch((err) => { if (err.message === "reloading") return; hit.state = "error"; hit.error = err.message; paintAll(); });
    return hit;
  }

  function linesHtml(data) {
    if (data.synced) {
      return data.synced.map(([ms, text]) => `<button class="ly-line${text ? "" : " is-gap"}" type="button" data-ly-ms="${ms}">${text ? esc(text) : "♪"}</button>`).join("");
    }
    return String(data.plain || "").split("\n").map((t) => `<p class="ly-line is-plain">${t.trim() ? esc(t) : "&nbsp;"}</p>`).join("");
  }
  // What the view says, and the lines when there are some.
  function content(v) {
    const item = core?.player?.item;
    if (!live()) return { note: "Music is not connected." };
    if (!item) return { note: "Nothing plays right now." };
    if (item.kind === "episode") return { note: "Podcasts come without lyrics." };
    const hit = load(item);
    if (hit.state === "loading") return { note: "LOOKING FOR THE LYRICS", busy: true };
    if (hit.state === "error") return { note: `Could not reach LRCLIB: ${hit.error}`, retry: true };
    const d = hit.data || {};
    if (!d.found) return { note: `LRCLIB has no lyrics for "${item.name}" by ${(item.artists || [])[0] || "this artist"}.` };
    if (d.instrumental || (!d.synced && !d.plain)) return { note: "♪ An instrumental: no words to sing." };
    return { data: d, badge: d.synced ? "SYNCED" : "NOT SYNCED" };
  }

  function paint(v) {
    const c = content(v);
    const sig = `${keyOf(core?.player?.item)}|${c.note || ""}|${c.badge || ""}`;
    if (sig === v.sig) return tick(v);
    v.sig = sig; v.at = -2;
    const src = core?.mode === "demo" ? "MADE-UP LYRICS" : "FROM LRCLIB";
    const body = c.data
      ? `<div class="ly-scroll${c.data.synced ? "" : " is-plain"}"><div class="ly-lines">${linesHtml(c.data)}</div></div>`
      : `<div class="ly-scroll is-note"><p class="ly-note${c.busy ? " is-busy" : ""}">${esc(c.note)}</p>${c.retry ? '<button class="btn btn--quiet ly-retry" type="button" data-ly-retry>Try again</button>' : ""}</div>`;
    v.el.innerHTML = v.mini ? body : `<div class="ly-head"><span>LYRICS</span>${c.badge ? `<span class="ly-badge">${c.badge}</span>` : ""}<span class="ly-src">${src}</span></div>
      ${body}<div class="ly-foot"><span>${c.data?.synced && core.can("seeking") ? "CLICK A LINE TO JUMP THERE" : ""}</span><span class="ly-time num8"></span></div>`;
    tick(v);
  }

  function tick(v) {
    const item = core?.player?.item;
    const time = v.el.querySelector(".ly-time");
    if (time) time.textContent = item ? `${core.fmt(core.position())} / ${core.fmt(item.duration_ms)}` : "";
    const lines = v.el.querySelectorAll(".ly-line[data-ly-ms]");
    if (!lines.length) return;
    const pos = core.position();
    let lo = 0, hi = lines.length - 1, at = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (Number(lines[mid].dataset.lyMs) <= pos) { at = mid; lo = mid + 1; } else hi = mid - 1; }
    if (at === v.at) return;
    v.at = at;
    lines.forEach((el, i) => { el.classList.toggle("is-now", i === at); el.classList.toggle("is-past", i < at); });
    const scroll = v.el.querySelector(".ly-scroll"), box = v.el.querySelector(".ly-lines"), cur = lines[Math.max(0, at)];
    if (scroll && box && cur) box.style.transform = `translateY(${Math.round(scroll.clientHeight * (v.mini ? 0.35 : 0.4) - cur.offsetTop - cur.offsetHeight / 2)}px)`;
  }

  function paintAll() {
    for (const v of [...views]) {
      // A panel moved between slots leaves the page for a moment; dropping its view then left the
      // lyrics stuck on what they said before. A view gone for a minute is let go.
      if (!v.el.isConnected) {
        v.gone = v.gone || Date.now();
        if (Date.now() - v.gone > 60000) views.delete(v);
        continue;
      }
      v.gone = 0;
      if (v.el.getClientRects().length) paint(v);
    }
    if (!views.size) { clearInterval(timer); timer = 0; }
  }

  function attach(el, { mini = false } = {}) {
    for (const v of views) if (v.el === el) return v;
    const v = { el, mini, sig: null, at: -2 };
    el.classList.add("ly");
    el.classList.toggle("ly--mini", mini);
    views.add(v);
    paint(v);
    if (!timer) timer = setInterval(paintAll, TICK_MS);
    return v;
  }
  function detach(el) {
    for (const v of views) if (v.el === el) views.delete(v);
  }

  function mount(c) {
    core = c;
    core.onTrack(() => paintAll());
    document.addEventListener("click", (e) => {
      const line = e.target.closest?.(".ly [data-ly-ms]");
      if (line && live() && core.can("seeking")) { core.act("seek", Number(line.dataset.lyMs)); return; }
      if (e.target.closest?.("[data-ly-retry]")) { cache.delete(keyOf(core?.player?.item)); paintAll(); }
    });
  }

  return { mount, attach, detach, paintAll };
})();
