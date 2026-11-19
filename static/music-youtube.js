"use strict";

/* YouTube in the Music panel. A switch on top of the panel picks Spotify (the boombox, drawn by
   music.js below it, untouched) or YouTube: a search when an API key is saved, a field for a
   pasted link always (that needs no key), the results, what plays next (the queue, with the
   rest of a pasted playlist), and what you played lately, all kept per browser. When a video
   ends the next one in the queue plays, unless autoplay is off. Videos play in the TV
   (music-tv.js), here or floating. The bar's mini player gets a
   first button that switches the two; with YouTube it shows the video and toggles the TV.
   Everything from YouTube is written with esc() or textContent; thumbnails come through
   /api/youtube-thumb, because the page's CSP allows no other host, and are drawn small.
   Nothing here runs on a timer: it answers clicks, and asks the server for the key's state
   once at the start and after the settings change it. */

const MusicYouTube = (() => {
  const KEY = "prdesk.youtube.v1", RECENT_MAX = 20, THUMB_W = 96, THUMB_H = 54;
  const ID = /^[0-9A-Za-z_-]{11}$/;
  let panel = null, els = {}, status = null, results = null, error = "", notice = "", busy = false, mini = null, miniEls = {};
  const listeners = new Set();
  const text = (v) => (typeof v === "string" && v.trim() ? v.trim().slice(0, 300) : null);
  const clean = (v) => (v && typeof v === "object" && ID.test(v.id)
    ? { id: v.id, title: text(v.title), channel: text(v.channel), live: v.live === true, time: Number(v.time) > 0 ? Math.floor(v.time) : 0 } : null);
  let kept = (() => {
    try {
      const v = JSON.parse(localStorage.getItem(KEY)) || {};
      const list = (x, max) => (Array.isArray(x) ? x : []).map(clean).filter(Boolean).slice(0, max);
      return { current: clean(v.current), recent: list(v.recent, RECENT_MAX), queue: list(v.queue, TubeQueue.MAX), auto: v.auto !== false };
    } catch { return { current: null, recent: [], queue: [], auto: true }; }
  })();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(kept)); } catch { /* storage blocked: it forgets on reload */ } };
  const icon = (id, px) => (typeof ControlCenter !== "undefined" ? ControlCenter.appIcon(id, px) : "");
  const g = (name, px = 2) => (typeof Music !== "undefined" ? Music.core.glyph(name, px) : "");
  const tv = () => (typeof MusicTV !== "undefined" ? MusicTV : null);

  /* ------------------------------------------------------------ which source */

  const source = () => (typeof Prefs !== "undefined" && Prefs.get("musicSource") === "youtube" ? "youtube" : "spotify");
  const spotifyOn = () => typeof Music !== "undefined" && ["live", "demo"].includes(Music.core.mode);
  /** YouTube is in use: a key is saved or a video is loaded. */
  const live = () => !!kept.current || !!status?.key_set;
  function setSource(next) {
    if (typeof Prefs !== "undefined" && next !== source()) Prefs.set({ musicSource: next });
  }
  function applySource() {
    if (!panel) return;
    const yt = source() === "youtube";
    els.spotifyHost.hidden = yt;
    els.tubeHost.hidden = !yt;
    for (const b of panel.querySelectorAll("[data-tube-src]")) b.setAttribute("aria-pressed", String(b.dataset.tubeSrc === source()));
  }

  /* ------------------------------------------------------------ thumbnails, four at a time */

  const bitmaps = new Map();
  let inflight = 0;
  const waiting = [];
  function queued(task) {
    return new Promise((resolve, reject) => {
      const run = () => { inflight++; task().then(resolve, reject).finally(() => { inflight--; waiting.shift()?.(); }); };
      if (inflight < 4) run(); else waiting.push(run);
    });
  }
  function thumb(url) {
    if (bitmaps.has(url)) return bitmaps.get(url);
    const job = queued(async () => {
      const res = await fetch(`/api/youtube-thumb?url=${encodeURIComponent(url)}`, { headers: { "X-Desk-Token": TOKEN } });
      if (!res.ok) throw new Error(`thumbnail ${res.status}`);
      return createImageBitmap(await res.blob());
    });
    bitmaps.set(url, job);
    job.catch(() => bitmaps.delete(url));
    while (bitmaps.size > 200) bitmaps.delete(bitmaps.keys().next().value);
    return job;
  }
  // Drawn small and shown bigger with crisp pixels: the list reads as pixel art, and costs little.
  function drawThumbs(scope) {
    if (!scope.getClientRects().length) return;  // drawn when the view shows
    for (const c of scope.querySelectorAll("canvas[data-thumb]:not([data-drawn])")) {
      c.dataset.drawn = "1";
      const draw = (bmp) => { if (c.isConnected) c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height); };
      // a live stream's picture goes away when the stream ends; its plain one stays
      const plain = `https://i.ytimg.com/vi/${c.dataset.id}/mqdefault.jpg`;
      thumb(c.dataset.thumb).then(draw, () => (c.dataset.thumb !== plain ? thumb(plain).then(draw) : null)).catch(() => {});
    }
  }
  const thumbOf = (v) => v.thumb || `https://i.ytimg.com/vi/${v.id}/mqdefault.jpg`;

  /* ------------------------------------------------------------ the panel */

  function viewHtml() {
    return `<div class="yt" data-view="youtube">
      <section class="yt-col yt-col--tv" aria-label="The YouTube TV">
        <div class="yt-dock" data-ref="dock"></div>
        <div class="yt-away" data-ref="away" hidden><span class="num8">THE TV FLOATS</span>
          <button class="btn btn--quiet" type="button" data-tube="dock">Bring it back here</button></div>
        <p class="yt-note">YouTube's own player, in a pixel set. Nothing is drawn over the video.</p>
        <section class="yt-recent yt-queue" data-ref="queueBox" aria-labelledby="yt-queue-title" hidden>
          <header class="yt-sub"><h3 class="num8" id="yt-queue-title" data-ref="queueTitle">UP NEXT</h3>
            <span class="yt-sub-acts"><button class="link" type="button" data-tube="auto" data-ref="auto"></button>
              <button class="link" type="button" data-tube="clear-queue">Clear</button></span></header>
          <ol class="yt-rows" data-ref="queue"></ol></section>
        <section class="yt-recent" data-ref="recentBox" aria-labelledby="yt-recent-title" hidden>
          <header class="yt-sub"><h3 class="num8" id="yt-recent-title">RECENTLY PLAYED</h3>
            <button class="link" type="button" data-tube="forget">Clear</button></header>
          <ol class="yt-rows" data-ref="recent"></ol></section>
      </section>
      <section class="yt-col yt-col--find" aria-label="Find a video">
        <form class="yt-find" data-tube-find autocomplete="off" role="search">
          <input class="yt-input" name="q" data-ref="input" spellcheck="false" aria-label="Search YouTube or paste a link">
          <button class="btn btn--primary" type="submit" data-ref="go">Search</button></form>
        <p class="yt-hint" data-ref="hint"></p>
        <p class="yt-error" data-ref="error" role="alert" hidden></p>
        <p class="yt-hint yt-notice" data-ref="notice" role="status" hidden></p>
        <ol class="yt-rows yt-rows--results" data-ref="results" aria-live="polite"></ol>
      </section>
    </div>`;
  }

  function rowHtml(v, list, i) {
    const now = kept.current?.id === v.id;
    return `<li class="yt-row${now ? " is-current" : ""}">
      <button class="yt-row-main" type="button" data-tube="play" data-list="${list}" data-i="${i}" aria-label="Play ${esc(v.title || `video ${v.id}`)}">
        <span class="yt-thumb"><canvas width="${THUMB_W}" height="${THUMB_H}" data-thumb="${esc(thumbOf(v))}" data-id="${esc(v.id)}" aria-hidden="true"></canvas>
          ${v.live ? '<span class="yt-live num8">LIVE</span>' : ""}</span>
        <span class="yt-row-text"><b>${esc(v.title || `Video ${v.id}`)}</b>${v.channel ? `<small>${esc(v.channel)}</small>` : ""}</span></button>
      <span class="yt-row-acts"><button class="yt-mini" type="button" data-tube="play" data-list="${list}" data-i="${i}" aria-label="Play here">${g("play", 1)}<span>PLAY</span></button>
        ${list === "queue"
          ? `<button class="yt-mini" type="button" data-tube="unqueue" data-i="${i}" aria-label="Take it out of the queue">${g("close", 1)}<span>REMOVE</span></button>`
          : `<button class="yt-mini" type="button" data-tube="queue" data-list="${list}" data-i="${i}" aria-label="Play it after the others">${g("plus", 1)}<span>QUEUE</span></button>
        <button class="yt-mini" type="button" data-tube="float" data-list="${list}" data-i="${i}" aria-label="Play it floating">${g("pop", 1)}<span>FLOAT</span></button>`}</span></li>`;
  }

  function paintFind() {
    if (!els.input) return;
    const key = !!status?.key_set;
    els.input.placeholder = key ? "Search YouTube, or paste a link" : "Paste a YouTube link or video id";
    els.go.textContent = key ? "Search" : "Play";
    els.go.disabled = busy;
    els.hint.innerHTML = key
      ? "Searches use your free API key: 10,000 units a day, about 100 searches. The same search again is free for a few minutes."
      : `A pasted link plays without a key. To search YouTube, add a free API key in
        <button class="link" type="button" data-tube="settings">Settings, Connected apps, YouTube</button>.`;
    els.error.hidden = !error;
    els.error.textContent = error;
    els.notice.hidden = !notice;
    els.notice.textContent = notice;
    const list = results?.items || [];
    const html = results ? (list.length ? list.map((v, i) => rowHtml(v, "results", i)).join("") : '<li class="yt-empty">No videos found. Try other words.</li>') : "";
    if (els.results._html !== html) { els.results.innerHTML = html; els.results._html = html; drawThumbs(els.results); }
  }
  function paintRecent() {
    if (!els.recent) return;
    els.recentBox.hidden = !kept.recent.length;
    const html = kept.recent.map((v, i) => rowHtml(v, "recent", i)).join("");
    if (els.recent._html !== html) { els.recent.innerHTML = html; els.recent._html = html; drawThumbs(els.recent); }
  }
  function paintQueue() {
    tv()?.setSkip(kept.queue.length ? playNext : null);
    if (!els.queue) return;
    els.queueBox.hidden = !kept.queue.length;
    els.queueTitle.textContent = `UP NEXT · ${kept.queue.length}`;
    els.auto.textContent = kept.auto ? "Autoplay on" : "Autoplay off";
    els.auto.setAttribute("aria-pressed", String(kept.auto));
    els.auto.title = kept.auto ? "The next video plays when one ends" : "Videos stop at their end";
    const html = kept.queue.map((v, i) => rowHtml(v, "queue", i)).join("");
    if (els.queue._html !== html) { els.queue.innerHTML = html; els.queue._html = html; drawThumbs(els.queue); }
  }
  function paintTvPlace() {
    if (!els.away) return;
    const floating = !!tv()?.isFloating();
    els.away.hidden = !floating;
  }
  function paintPanel() { paintFind(); paintQueue(); paintRecent(); paintTvPlace(); }

  /** The Music panel's switch and the YouTube view; answers the host the deck is drawn in. */
  function mountPanel(el) {
    panel = el;
    el.classList.add("has-source");
    el.innerHTML = `<div class="mu-source" role="group" aria-label="Music source">
        <button class="mu-src-btn" type="button" data-tube-src="spotify" aria-pressed="true">${icon("spotify", 1.2)}<span class="num8">SPOTIFY</span></button>
        <button class="mu-src-btn mu-src-btn--yt" type="button" data-tube-src="youtube" aria-pressed="false">${icon("youtube", 1.2)}<span class="num8">YOUTUBE</span></button>
      </div><div class="mu-host"></div><div class="yt-host" hidden>${viewHtml()}</div>`;
    els = { spotifyHost: el.querySelector(".mu-host"), tubeHost: el.querySelector(".yt-host") };
    for (const x of els.tubeHost.querySelectorAll("[data-ref]")) els[x.dataset.ref] = x;
    if (tv()) {
      tv().mount(els.dock);
      if (kept.current) tv().cue(kept.current);
      tv().onChange(onTv);
      tv().onEnded(() => { if (kept.auto) playNext(); });
    }
    applySource();
    paintPanel();
    // Thumbnails wait until the view shows: nothing is fetched for a panel nobody looks at.
    new ResizeObserver(() => drawThumbs(els.tubeHost)).observe(els.tubeHost);
    refresh();
    return els.spotifyHost;
  }

  /* ------------------------------------------------------------ playing */

  function remember(v) {
    const entry = clean(v);
    if (!entry) return;
    kept.current = entry;
    kept.recent = [entry, ...kept.recent.filter((x) => x.id !== entry.id)].slice(0, RECENT_MAX);
    save();
  }
  function play(v, floating) {
    if (!v || !ID.test(v.id) || !tv()) return;
    remember(v);
    setSource("youtube");
    // A video started from the panel plays in the panel unless the TV floats already.
    tv().play(v, { floating: floating ?? tv().isFloating() });
    paintPanel();
    refreshMini();
  }
  /** The first queued video, now: when one ends (with autoplay on) or on Next. */
  function playNext() {
    const { item, rest } = TubeQueue.take(kept.queue);
    if (!item || !tv()) return;
    kept.queue = rest;
    remember(item);
    tv().next(item);
    paintPanel();
    refreshMini();
  }
  /** Videos into the queue: at its end, or with next at its front. */
  function enqueue(items, opts) {
    kept.queue = TubeQueue.add(kept.queue, (items || []).map(clean).filter(Boolean), opts);
    save();
    paintQueue();
  }
  // A pasted playlist: the video its link names (or its first) plays, and the rest of it is queued next.
  async function openList(list, linked) {
    let answer;
    try {
      answer = await api(`youtube-playlist?list=${encodeURIComponent(list)}`);
    } catch (err) {
      if (err.message === "reloading") return;
      // the linked video still plays; the note says why the rest of the list is not queued
      if (linked) { play(linked); notice = err.message; } else error = err.message;
      return;
    }
    const items = answer.items || [];
    const first = (linked && items.find((v) => v.id === linked.id)) || linked || items[0];
    if (!first) { error = "That playlist has no video that can play here."; return; }
    const rest = TubeQueue.after(items, first.id);
    enqueue(rest, { next: true });
    play(first);
    notice = rest.length ? `${rest.length} video${rest.length === 1 ? "" : "s"} from the playlist queued next${answer.more ? " (its first 200)" : ""}.` : "";
  }
  // What the player says (a title for a pasted link, playing or not) goes to the list and the bar.
  function onTv() {
    const v = tv().video();
    if (v && kept.current?.id === v.id && (v.title !== kept.current.title || v.channel !== kept.current.channel)) {
      kept.current = { ...kept.current, title: v.title || kept.current.title, channel: v.channel || kept.current.channel };
      kept.recent = kept.recent.map((x) => (x.id === v.id ? { ...kept.current } : x));
      save();
      paintRecent();
    }
    paintTvPlace();
    refreshMini();
    for (const fn of listeners) fn();
  }
  // Before the page goes, keep where the video was, so Play starts there next time.
  addEventListener("pagehide", () => {
    const v = tv()?.video();
    if (v && kept.current?.id === v.id && v.time > 0) { kept.current.time = tv().ended() ? 0 : Math.floor(v.time); save(); }
  });

  async function find(form) {
    const q = form.q.value.trim();
    if (!q || busy) return;
    busy = true; error = ""; notice = "";
    paintFind();
    try {
      const out = await api(`youtube-search?q=${encodeURIComponent(q)}`);
      if (out.link) {
        form.q.value = "";
        if (out.list) await openList(out.list, out.items?.[0]);
        else if (out.items?.[0]) play(out.items[0]);
      } else {
        results = out;
      }
    } catch (err) {
      if (err.message === "reloading") return;
      error = err.message;
    } finally {
      busy = false;
      paintFind();
    }
  }

  function openPanel() {
    setSource("youtube");
    if (typeof Layout === "undefined") return;
    if (Layout.visibility?.().find((v) => v.id === "music")?.hidden) Layout.show("music");
    Layout.activate("music");
  }
  function openSettings() {
    if (typeof Settings !== "undefined") Settings.open("connections", { app: "youtube" });
  }

  /* ------------------------------------------------------------ the bar's mini player */

  function refreshMini() {
    const el = document.getElementById("music-mini");
    if (!el) return;
    el.hidden = !spotifyOn() && !live();
    paintMini(el);
  }
  /** Called by music.js each time it paints the mini player, and here when YouTube changes. */
  function paintMini(el) {
    mini = el;
    const sp = spotifyOn(), yt = live();
    const shown = source() === "youtube" ? (yt ? "youtube" : "spotify") : (sp ? "spotify" : "youtube");
    // music.js writes the Spotify parts afresh when it first goes live: put ours back if so.
    if (!miniEls.src?.isConnected) {
      el.querySelector(".mm-src")?.remove();
      el.insertAdjacentHTML("afterbegin", '<button class="mm-src" type="button" data-tube="source"></button>');
      miniEls.src = el.querySelector(".mm-src");
    }
    if (!miniEls.tube?.isConnected) {
      el.querySelector(".mm-yt")?.remove();
      el.insertAdjacentHTML("beforeend", `<span class="mm-yt"><button class="mm-play mm-yt-play" type="button" data-tube="toggle" aria-label="Play">${g("play")}${g("pause")}</button>
        <button class="mm-title mm-yt-title" type="button" data-tube="tv"><span class="mu-mq mm-mq"><span class="mu-mq-track"><span class="mu-mq-text"></span><span class="mu-mq-text" aria-hidden="true"></span></span></span></button></span>`);
      miniEls.tube = el.querySelector(".mm-yt");
      miniEls.play = miniEls.tube.querySelector(".mm-yt-play");
      miniEls.title = miniEls.tube.querySelector(".mm-yt-title");
      miniEls.mq = miniEls.tube.querySelector(".mm-mq");
    }
    el.classList.toggle("is-yt", shown === "youtube");
    miniEls.src.hidden = !(sp && yt);
    const other = shown === "youtube" ? "Spotify" : "YouTube";
    const srcHtml = shown === "youtube" ? `${icon("youtube", 1)}<span class="num8">YOUTUBE</span>` : `${icon("spotify", 1)}<span class="num8">SPOTIFY</span>`;
    if (miniEls.src._html !== srcHtml) { miniEls.src.innerHTML = srcHtml; miniEls.src._html = srcHtml; }
    miniEls.src.setAttribute("aria-label", `${shown === "youtube" ? "YouTube" : "Spotify"} in the bar. Switch to ${other}`);
    miniEls.src.dataset.tip = `Switch to ${other}`;
    if (shown !== "youtube") return;
    const v = tv()?.video() || kept.current, on = !!tv()?.playing(), floating = !!tv()?.isFloating();
    miniEls.play.classList.toggle("is-playing", on);
    miniEls.play.setAttribute("aria-label", on ? "Pause" : "Play");
    miniEls.play.disabled = !v;
    const title = v ? v.title || `Video ${v.id}` : "Pick a video";
    if (typeof Music !== "undefined") Music.core.marquee(miniEls.mq, title);
    miniEls.title.setAttribute("aria-label", v ? `YouTube: ${title}. ${floating ? "Put the TV back" : "Float the TV"}` : "Pick a video in the Music panel");
    miniEls.title.dataset.tip = v ? (floating ? "Put the TV back in the Music panel" : "Float the TV") : "Pick a video in Music";
    miniEls.title.setAttribute("aria-pressed", String(floating));
  }

  /* ------------------------------------------------------------ events */

  const listOf = (name) => (name === "recent" ? kept.recent : name === "queue" ? kept.queue : results?.items || []);
  document.addEventListener("click", (e) => {
    const s = e.target.closest?.("[data-tube-src]");
    if (s) { setSource(s.dataset.tubeSrc); return; }
    const t = e.target.closest?.("[data-tube]");
    if (!t || t.disabled) return;
    const what = t.dataset.tube;
    if (what === "play" || what === "float") {
      const list = t.dataset.list, i = Number(t.dataset.i), v = listOf(list)[i];
      if (list === "queue") kept.queue = TubeQueue.remove(kept.queue, i);  // played now, so no longer next
      return play(v, what === "float" ? true : undefined);
    }
    if (what === "queue") return enqueue([listOf(t.dataset.list)[Number(t.dataset.i)]]);
    if (what === "unqueue") { kept.queue = TubeQueue.remove(kept.queue, Number(t.dataset.i)); save(); return paintQueue(); }
    if (what === "auto") { kept.auto = !kept.auto; save(); return paintQueue(); }
    if (what === "clear-queue") { kept.queue = []; save(); return paintQueue(); }
    if (what === "source") return setSource(source() === "youtube" ? "spotify" : "youtube");
    if (what === "toggle") return tv()?.toggle();
    if (what === "tv") {
      if (!(tv()?.video() || kept.current)) return openPanel();
      return tv().isFloating() ? tv().dock({ open: false }) : tv().float();
    }
    if (what === "dock") return tv()?.dock();
    if (what === "settings") return openSettings();
    if (what === "open") { const d = t.closest("dialog"); if (d?.open) d.close(); return openPanel(); }
    if (what === "forget") { kept.recent = []; save(); paintRecent(); }
  });
  document.addEventListener("submit", (e) => {
    const form = e.target.closest?.("[data-tube-find]");
    if (!form) return;
    e.preventDefault();
    find(form);
  });
  if (typeof Prefs !== "undefined") Prefs.onChange((_p, patch) => { if ("musicSource" in patch) { applySource(); refreshMini(); for (const fn of listeners) fn(); } });

  /** Ask the server whether a key is saved (never the key itself). */
  async function refresh() {
    try { status = await api("youtube-status"); } catch (err) { if (err.message === "reloading") return status; status = null; }
    paintPanel();
    refreshMini();
    for (const fn of listeners) fn();
    return status;
  }

  return {
    mountPanel, paintMini, refresh, openPanel, openSettings, play, playNext, enqueue, thumb,
    queue: () => kept.queue.slice(),
    status: () => status, live, source, setSource,
    current: () => kept.current,
    onChange: (fn) => listeners.add(fn),
  };
})();
