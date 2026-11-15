"use strict";

/* The Music panel's library: your playlists (as cassettes, with their cover photo as label art
   when they have one), Liked Songs, saved albums, recently played, the queue, search, and a
   playlist, album or artist opened from any of them. Rows play from that song, add it to the
   queue, or like it; arrow keys walk them, Enter plays, "/" finds. Lists come a page at a time,
   with skeleton rows while they load. Every string from Spotify is written with esc();
   pictures are canvases drawn through the player's art pipeline once they scroll into view.
   A view that changes enters with a short stepped animation; one that only repaints (a like,
   a background refresh) does not move. */

const MusicLibrary = (() => {
  const TABS = [["playlists", "Playlists"], ["liked", "Liked Songs"], ["albums", "Albums"], ["recent", "Recent"], ["queue", "Queue"]];
  const STALE = 120000, QUEUE_STALE = 5000, ENTER_MS = 420;
  const TAPE_TONES = ["--sprite-orange", "--sprite-blue", "--sprite-yellow", "--sprite-red", "--sprite-grass", "--color-cool"];
  const ROW_KINDS = new Set(["liked", "recent", "queue", "playlist", "album"]);
  let core = null, root = null, body = null, crumbs = null, input = null, registered = false;
  let stack = [{ kind: "playlists" }];
  const cache = new Map();
  let searchTimer = 0, seen = null, painted = "", enterTimer = 0;

  const top = () => stack[stack.length - 1];
  const keyOf = (v) => (v.id ? `${v.kind}:${v.id}` : v.kind === "search" ? `search:${v.q}` : v.kind);
  const hash = (text) => { let h = 0; for (const ch of String(text)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h; };
  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

  /* ------------------------------------------------------------ loading */

  function loader(v, more) {
    const src = core.source(), entry = cache.get(keyOf(v));
    const offset = more ? entry?.next || 0 : 0;
    switch (v.kind) {
      case "playlists": return src.playlists(offset);
      case "liked": return src.liked(offset);
      case "albums": return src.albums(offset);
      case "recent": return src.recent(more ? entry?.before : undefined);
      case "queue": return src.queue();
      case "playlist": return src.playlist(v.id, offset);
      case "album": return src.album(v.id, offset);
      case "artist": return src.artist(v.id, offset);
      default: return Promise.resolve({ items: [] });
    }
  }
  async function load(v, more = false) {
    const key = keyOf(v), entry = cache.get(key) || {};
    if (entry.loading) return;
    cache.set(key, { ...entry, loading: true, error: "" });
    if (!more) render();
    try {
      const out = await loader(v, more);
      const items = more ? [...(entry.items || []), ...(out.items || [])] : out.items || [];
      cache.set(key, {
        items, next: out.next ?? null, before: out.before ?? null, total: out.total ?? null, locked: out.locked ? out : null,
        head: out.playlist || out.album || out.artist || (more ? entry.head : null), current: out.current || null, at: Date.now(),
        added: more ? (entry.items || []).length : 0,
      });
    } catch (err) {
      if (err.message === "reloading") return;
      cache.set(key, { ...entry, loading: false, error: err.message });
    }
    if (keyOf(top()) === key) render();
  }
  async function loadSearch(q, type, more) {
    const key = `search:${q}`, entry = cache.get(key) || { sections: {} };
    const section = type || "all";
    if (entry.loading === section) return;
    cache.set(key, { ...entry, loading: section, error: "" });
    if (!more) render();
    try {
      const offset = more ? entry.sections[`${type}s`]?.next || 0 : 0;
      const out = await core.source().search(q, type || "all", offset);
      const sections = { ...entry.sections };
      for (const [name, page] of Object.entries(out)) {
        sections[name] = more ? { ...page, items: [...(sections[name]?.items || []), ...(page.items || [])] } : page;
      }
      cache.set(key, { sections, at: Date.now() });
    } catch (err) {
      if (err.message === "reloading") return;
      cache.set(key, { ...entry, loading: false, error: err.message });
    }
    if (keyOf(top()) === key) render();
  }

  /* ------------------------------------------------------------ pieces */

  const btn = (attrs, inner, cls = "ml-tool") => `<button class="${cls}" type="button" ${attrs}>${inner}</button>`;
  const onAir = () => '<span class="ml-onair num8" aria-hidden="true"><i></i>ON AIR</span>';
  function artistLinks(t) {
    return (t.artists || []).map((a, k) => (t.artist_ids?.[k] ? btn(`data-lib="artist" data-id="${esc(t.artist_ids[k])}" tabindex="-1"`, esc(a), "ml-link") : `<span>${esc(a)}</span>`)).join(", ");
  }
  function rowHtml(t, i, { numbers = "index", play = true } = {}) {
    const current = core.player?.item?.id && t.id === core.player.item.id;
    const liked = t.id ? core.liked(t.id) : undefined;
    const n = numbers === "track" && t.number ? t.number : i + 1;
    const album = t.album ? (t.album_id ? btn(`data-lib="album" data-id="${esc(t.album_id)}" tabindex="-1"`, esc(t.album), "ml-link") : `<span>${esc(t.album)}</span>`) : "";
    const label = `${t.name}, ${(t.artists || []).join(", ")}`;
    return `<li class="ml-row${current ? " is-current" : ""}${t.playable === false ? " is-off" : ""}" data-i="${i}" data-id="${esc(t.id || "")}" tabindex="-1" style="--i:${Math.min(i % 50, 14)}" aria-label="${esc(label)}">
      <span class="ml-num num8" aria-hidden="true"><span class="ml-n">${String(n).padStart(2, "0")}</span><span class="ml-eq"><i></i><i></i><i></i></span></span>
      <canvas class="ml-thumb" width="16" height="16" data-thumb="${esc(t.thumb || `plain:${t.id || t.name}`)}" aria-hidden="true"></canvas>
      <span class="ml-main"><b>${esc(t.name)}</b><small>${artistLinks(t)}</small></span>
      <span class="ml-album">${album}</span>
      <span class="ml-dur num8">${core.fmt(t.duration_ms)}</span>
      <span class="ml-tools">
        ${t.id && t.kind !== "episode" ? btn(`data-lib="like" tabindex="-1" aria-pressed="${liked === true}" aria-label="${liked ? "Remove from" : "Add to"} Liked Songs: ${esc(t.name)}"`, core.glyph("heart", 2), "ml-tool ml-like") : ""}
        ${t.uri ? btn(`data-lib="queue" tabindex="-1" aria-label="Add to the queue: ${esc(t.name)}" data-tip="Add to the queue"`, core.glyph("plus", 2)) : ""}
        ${play && t.uri && t.playable !== false ? btn(`data-lib="play" tabindex="-1" aria-label="Play ${esc(t.name)}"`, core.glyph("play", 2), "ml-tool ml-play") : ""}
      </span></li>`;
  }
  // Each list says where its songs live (the view's items, the queue's current song, search songs).
  const rows = (items, opts = {}) => `<ol class="ml-rows" data-list="${opts.list || "items"}">${items.map((t, i) => rowHtml(t, i, opts)).join("")}</ol>`;
  function tapeHtml(p, i) {
    const meta = [p.tracks != null ? plural(p.tracks, "song") : "", p.owner || ""].filter(Boolean).join("  ");
    const art = p.art || p.thumb;
    // the cover photo, when there is one, is the label's art; the label opens the playlist and
    // the play key sits in the tape window, between the reels
    return `<div class="mu-tape ml-tape${art ? " has-art" : ""}" style="--tape:var(${TAPE_TONES[hash(p.id) % TAPE_TONES.length]});--i:${Math.min(i, 14)}" data-id="${esc(p.id)}">
      <span class="mu-tape-label">
        ${art ? `<span class="ml-tape-art"><canvas width="32" height="32" data-thumb="${esc(art)}" data-css="156" aria-hidden="true"></canvas></span>` : ""}
        <button class="ml-tape-open" type="button" data-lib="playlist" data-id="${esc(p.id)}" aria-label="Open the playlist ${esc(p.name)}">
          <span class="mu-tape-top"><span class="mu-tape-side num8" aria-hidden="true">A</span><b>${esc(p.name)}</b></span><small>${esc(meta)}</small></button>
        <span class="mu-tape-window"><i class="mu-reel" aria-hidden="true"></i>
          ${btn(`data-lib="play-context" data-uri="${esc(p.uri || `spotify:playlist:${p.id}`)}" aria-label="Play the playlist ${esc(p.name)}"`, core.glyph("play", 2), "ml-tape-play")}<i class="mu-reel" aria-hidden="true"></i></span></span>
      <span class="mu-tape-foot" aria-hidden="true"><i></i><i></i><i></i></span>${onAir()}</div>`;
  }
  function cardHtml(a, kind, i) {
    const meta = kind === "album" ? [a.artists?.join(", "), a.year].filter(Boolean).join("  ") : (a.genres || []).join(", ");
    return `<button class="ml-card ml-card--${kind}" type="button" data-lib="${kind}" data-id="${esc(a.id)}" data-uri="${esc(a.uri || "")}" style="--i:${Math.min(i, 14)}" aria-label="Open ${esc(a.name)}">
      <span class="ml-card-art"><canvas width="32" height="32" data-thumb="${esc(a.art || a.thumb || `plain:${a.id}`)}" data-css="128" aria-hidden="true"></canvas>${onAir()}</span>
      <b>${esc(a.name)}</b><small>${esc(meta)}</small></button>`;
  }
  const grid = (items, kind) => `<div class="ml-grid ml-grid--${kind}">${items.map((a, i) => (kind === "tape" ? tapeHtml(a, i) : cardHtml(a, kind, i))).join("")}</div>`;
  function more(entry, label = "Load more", attrs = 'data-lib="more"') {
    if (entry.loading) return '<p class="ml-loading num8">LOADING</p>';
    return entry.next != null || entry.before ? `<p class="ml-more"><button class="btn" type="button" ${attrs}>${label}</button></p>` : "";
  }
  function heroHtml(h, kind) {
    if (!h) return "";
    const meta = kind === "playlist" ? [h.owner ? `by ${h.owner}` : "", h.tracks != null ? plural(h.tracks, "song") : ""]
      : kind === "album" ? [h.artists?.join(", "), h.year, h.tracks != null ? plural(h.tracks, "song") : ""] : [(h.genres || []).join(", ")];
    const art = h.art || h.thumb;
    // a playlist without a picture keeps its cassette
    const picture = art || kind !== "playlist" ? `<canvas width="32" height="32" data-thumb="${esc(art || `plain:${h.id}`)}" data-css="128" aria-hidden="true"></canvas>` : `<span class="ml-hero-tape">${core.cassette(5)}</span>`;
    const uri = h.uri || `spotify:${kind}:${h.id}`;
    return `<div class="ml-hero" data-uri="${esc(uri)}"><span class="ml-hero-art${art || kind !== "playlist" ? "" : " is-tape"}">${picture}</span>
      <div class="ml-hero-text"><p class="ml-kind num8">${kind.toUpperCase()}</p>${onAir()}<h4>${esc(h.name)}</h4><p class="ml-meta">${esc(meta.filter(Boolean).join("  ·  "))}</p>
      <p>${btn(`data-lib="play-context" data-uri="${esc(uri)}"`, `${core.glyph("play", 2)}<span>Play</span>`, "btn btn--primary ml-play-all")}</p></div></div>`;
  }
  function lockHtml(locked) {
    return `<div class="ml-lock" role="note">${core.glyph("lock", 4)}<div><h4>One more sign-in unlocks the library</h4><p>${esc(locked.message)}</p>
      <p><button class="btn btn--primary" type="button" data-music="connect">Reconnect once</button></p></div></div>`;
  }
  // An empty list says what fills it; a failed one says what went wrong and offers to try again.
  const empty = (title, hint = "") => `<div class="ml-empty">${core.cassette(3)}<p><b>${esc(title)}</b>${hint ? `<span>${esc(hint)}</span>` : ""}</p></div>`;
  const failed = (message, retry = true) => `<div class="ml-error" role="alert"><span class="ml-error-mark num8" aria-hidden="true">!</span><div><p>${esc(message)}</p>
    ${retry ? '<p><button class="btn" type="button" data-lib="retry">Try again</button></p>' : ""}</div></div>`;

  /* ------------------------------------------------------------ skeletons: the shape of what is coming */

  const skelRow = (i) => `<li class="ml-row ml-row--skel" style="--i:${i}"><span class="ml-num"><span class="ml-skel ml-skel--n"></span></span><span class="ml-skel ml-skel--art"></span>
    <span class="ml-main"><span class="ml-skel ml-skel--t"></span><span class="ml-skel ml-skel--s"></span></span><span class="ml-album"><span class="ml-skel ml-skel--s"></span></span>
    <span class="ml-dur"><span class="ml-skel ml-skel--n"></span></span><span class="ml-tools"></span></li>`;
  const skelRows = (n = 8) => `<ol class="ml-rows" aria-hidden="true">${Array.from({ length: n }, (_, i) => skelRow(i)).join("")}</ol>`;
  const skelCards = (kind, n = 6) => `<div class="ml-grid ml-grid--${kind}" aria-hidden="true">${Array.from({ length: n }, (_, i) =>
    kind === "tape" ? `<div class="mu-tape ml-tape ml-tape--skel" style="--i:${i}"><span class="mu-tape-label"><span class="ml-skel ml-skel--t"></span><span class="ml-skel ml-skel--s"></span>
      <span class="mu-tape-window"><i class="mu-reel"></i><i class="mu-reel"></i></span></span><span class="mu-tape-foot"><i></i><i></i><i></i></span></div>`
      : `<div class="ml-card ml-card--skel" style="--i:${i}"><span class="ml-card-art ml-skel"></span><span class="ml-skel ml-skel--t"></span><span class="ml-skel ml-skel--s"></span></div>`).join("")}</div>`;
  const skelHero = () => `<div class="ml-hero ml-hero--skel" aria-hidden="true"><span class="ml-hero-art ml-skel"></span><div class="ml-hero-text"><span class="ml-skel ml-skel--s"></span>
    <span class="ml-skel ml-skel--h"></span><span class="ml-skel ml-skel--s"></span></div></div>`;
  function skeleton(kind) {
    const busy = '<p class="visually-hidden" role="status">Loading</p>';
    if (kind === "playlists") return busy + skelCards("tape");
    if (kind === "albums") return busy + skelCards("album");
    if (kind === "artist") return busy + skelHero() + skelCards("album", 4);
    if (kind === "playlist" || kind === "album") return busy + skelHero() + skelRows(8);
    if (kind === "search") return busy + skelRows(5) + skelCards("album", 4);
    return busy + skelRows(ROW_KINDS.has(kind) ? 8 : 6);
  }

  /* ------------------------------------------------------------ the views */

  function viewHtml(v, entry) {
    if (entry.locked) return lockHtml(entry.locked);
    if (entry.error && !entry.items?.length && !entry.sections) return failed(entry.error);
    if (!entry.at) return skeleton(v.kind);
    const items = entry.items || [];
    switch (v.kind) {
      case "playlists": return items.length ? grid(items, "tape") + more(entry) : empty("No playlists yet.", "The ones you make or follow in Spotify show up here.");
      case "liked": return items.length ? rows(items) + more(entry) : empty("No liked songs yet.", "The heart on any song adds it here.");
      case "albums": return items.length ? grid(items, "album") + more(entry) : empty("No saved albums yet.", "Albums you save in Spotify show up here.");
      case "recent": return items.length ? rows(items) + more(entry, "Earlier") : empty("Nothing played lately.");
      case "queue": return `${entry.current ? `<h5 class="ml-sub num8">NOW PLAYING</h5>${rows([entry.current], { play: false, list: "current" })}` : ""}
        <h5 class="ml-sub num8">NEXT UP</h5>${items.length ? rows(items, { play: false }) : empty("The queue is empty.", "The plus on any song adds it here.")}`;
      case "playlist": return heroHtml(entry.head, "playlist") + (items.length ? rows(items) : empty("This playlist is empty.")) + more(entry);
      case "album": return heroHtml(entry.head, "album") + rows(items, { numbers: "track" }) + more(entry);
      case "artist": return heroHtml(entry.head, "artist") + `<h5 class="ml-sub num8">ALBUMS AND SINGLES</h5>` + (items.length ? grid(items, "album") : empty("No albums here.")) + more(entry);
      default: return "";
    }
  }
  function searchHtml(v, entry) {
    if (entry.error && !entry.sections) return failed(entry.error, false);
    if (!entry.sections || entry.loading === "all") return skeleton("search");
    const s = entry.sections, part = (name, title, html, type) => {
      const page = s[name];
      if (!page?.items?.length) return "";
      return `<section class="ml-section"><h5 class="ml-sub num8">${title}</h5>${html(page.items)}
        ${entry.loading === type ? '<p class="ml-loading num8">LOADING</p>' : page.next != null ? `<p class="ml-more"><button class="btn btn--quiet" type="button" data-lib="more-search" data-type="${type}">More ${title.toLowerCase()}</button></p>` : ""}</section>`;
    };
    const html = part("tracks", "SONGS", (items) => rows(items, { list: "search" }), "track") + part("artists", "ARTISTS", (items) => grid(items, "artist"), "artist")
      + part("albums", "ALBUMS", (items) => grid(items, "album"), "album") + part("playlists", "PLAYLISTS", (items) => grid(items, "tape"), "playlist");
    return html || empty(`Nothing found for "${v.q}".`, "Fewer or other words find more.");
  }

  function render() {
    if (!root) return;
    const v = top(), key = keyOf(v);
    for (const b of root.querySelectorAll("[data-lib-tab]")) b.setAttribute("aria-selected", String(stack[0].kind === b.dataset.libTab && v.kind !== "search"));
    const trail = stack.length > 1 ? stack.map((s) => s.label || TABS.find(([id]) => id === s.kind)?.[1] || (s.kind === "search" ? `Search "${s.q}"` : s.kind)) : [];
    crumbs.hidden = !trail.length;
    crumbs.innerHTML = trail.length ? `${btn('data-lib="back"', `${core.glyph("back", 2)}<span>Back</span>`, "btn btn--quiet ml-back")}<span class="ml-trail">${trail.map((t) => `<span>${esc(t)}</span>`).join('<i aria-hidden="true">/</i>')}</span>` : "";
    const entry = cache.get(key) || {};
    // A new view, or the first content where a skeleton stood, comes in with the stepped
    // animation; a repaint of the same thing keeps still (and keeps its scroll and focus).
    const state = `${key}|${entry.locked ? "lock" : entry.error ? "err" : entry.at || entry.sections ? "data" : "skel"}`;
    const focusedRow = body.contains(document.activeElement) ? document.activeElement.closest(".ml-row")?.dataset.i : null;
    const entering = state !== painted;
    painted = state;
    body.innerHTML = v.kind === "search" ? searchHtml(v, entry) : viewHtml(v, entry);
    if (entering && !core.still()) {
      body.classList.remove("is-entering");
      void body.offsetWidth;
      body.classList.add("is-entering");
      clearTimeout(enterTimer);
      enterTimer = setTimeout(() => body.classList.remove("is-entering"), ENTER_MS + 300);
    }
    if (!entry.at && !entry.loading && !entry.error && !entry.locked) { if (v.kind === "search") loadSearch(v.q); else load(v); }
    else if (entry.at && Date.now() - entry.at > (v.kind === "queue" ? QUEUE_STALE : STALE) && !entry.loading) {
      entry.at = Date.now();  // stale: show it, and ask again in the background
      if (v.kind === "search") loadSearch(v.q); else load(v);
    }
    hydrate();
    markCurrent();
    rove(focusedRow != null ? body.querySelector(`.ml-row[data-i="${focusedRow}"]:not(.ml-row--skel)`) : null, focusedRow != null);
  }

  let observer = null;
  function hydrate() {
    observer?.disconnect();
    observer = "IntersectionObserver" in window ? new IntersectionObserver((seenItems) => {
      for (const e of seenItems) if (e.isIntersecting) { observer.unobserve(e.target); draw(e.target); }
    }, { rootMargin: "160px" }) : null;
    for (const c of body.querySelectorAll("canvas[data-thumb]")) observer ? observer.observe(c) : draw(c);
    const ids = [...body.querySelectorAll(".ml-row[data-id]")].map((r) => r.dataset.id).filter(Boolean);
    if (ids.length) core.checkLikes(ids);
  }
  const draw = (c) => core.drawArt(c, c.dataset.thumb, Number(c.dataset.css) || 32);

  function markCurrent() {
    if (!body) return;
    const id = core.player?.item?.id, on = core.playing(), ctx = core.player?.context?.uri;
    for (const r of body.querySelectorAll(".ml-row:not(.ml-row--skel)")) {
      const now = !!id && r.dataset.id === id;
      r.classList.toggle("is-current", now);
      if (now) r.classList.remove("is-starting");
      r.toggleAttribute("aria-current", now);
    }
    body.classList.toggle("is-playing", on);
    for (const t of body.querySelectorAll(".ml-tape[data-id]")) t.classList.toggle("is-on", !!ctx && ctx === `spotify:playlist:${t.dataset.id}`);
    for (const c of body.querySelectorAll(".ml-card[data-uri]")) c.classList.toggle("is-on", !!ctx && !!c.dataset.uri && ctx === c.dataset.uri);
    for (const h of body.querySelectorAll(".ml-hero[data-uri]")) h.classList.toggle("is-on", !!ctx && ctx === h.dataset.uri);
  }
  function paintLikes() {
    if (!body) return;
    for (const b of body.querySelectorAll(".ml-row [data-lib='like']")) {
      const id = b.closest(".ml-row").dataset.id, on = core.liked(id) === true;
      if (b.getAttribute("aria-pressed") !== String(on)) b.setAttribute("aria-pressed", String(on));
    }
  }

  /* ------------------------------------------------------------ the keyboard */

  const songRows = () => [...body.querySelectorAll(".ml-row:not(.ml-row--skel)")];
  // One row takes Tab at a time (the one last visited, else the song playing, else the first);
  // the arrows move it.
  function rove(to, focus = false) {
    const all = songRows();
    if (!all.length) return;
    const pick = to || all.find((r) => r.tabIndex === 0) || body.querySelector(".ml-row.is-current") || all[0];
    // the row in reach also lends its buttons to Tab; the rest stay out of the way
    for (const r of all) { const on = r === pick; r.tabIndex = on ? 0 : -1; for (const b of r.querySelectorAll("button")) b.tabIndex = on ? 0 : -1; }
    if (focus) pick.focus({ preventScroll: false });
  }
  function onKey(e) {
    const t = e.target;
    if (t === input) {
      if (e.key === "ArrowDown") { const first = body.querySelector(".ml-row:not(.ml-row--skel), .ml-card, .ml-tape-open"); if (first) { e.preventDefault(); if (first.classList.contains("ml-row")) rove(first, true); else first.focus(); } }
      else if (e.key === "Escape") { if (input.value) { input.value = ""; onSearch(true); } else input.blur(); e.preventDefault(); }
      return;
    }
    const row = t.closest?.(".ml-row");
    if (row && t === row) {
      const all = songRows(), i = all.indexOf(row);
      const to = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: all.length - 1, PageDown: i + 10, PageUp: i - 10 }[e.key];
      if (to != null) { e.preventDefault(); rove(all[Math.max(0, Math.min(all.length - 1, to))], true); return; }
      if (e.key === "Enter") { e.preventDefault(); const play = row.querySelector("[data-lib='play']"); if (play) { play.click(); } return; }
      return;
    }
    const item = t.closest?.(".ml-card, .ml-tape-open");
    if (item && ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key)) {
      const gridEl = item.closest(".ml-grid"), items = [...gridEl.querySelectorAll(".ml-card:not(.ml-card--skel), .ml-tape-open")];
      const cols = Math.max(1, Math.round(gridEl.clientWidth / (items[0].closest(".ml-card, .mu-tape").offsetWidth || 1)));
      const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols }[e.key];
      const next = items[items.indexOf(item) + step];
      if (next) { e.preventDefault(); next.focus(); }
    }
  }

  /* ------------------------------------------------------------ actions */

  function go(v) {
    stack.push(v);
    render();
    (root.closest(".mu-col") ? body : root).scrollIntoView?.({ block: "nearest" });
  }
  function itemsOfRow(row) {
    const entry = cache.get(keyOf(top())) || {}, list = row.closest("ol")?.dataset.list;
    if (list === "current") return entry.current ? [entry.current] : [];
    if (list === "search") return entry.sections?.tracks?.items || [];
    return entry.items || [];
  }
  function playRow(row) {
    const v = top(), list = itemsOfRow(row), t = list[Number(row.dataset.i)];
    if (!t?.uri) return;
    const entry = cache.get(keyOf(v)) || {};
    // the row answers at once; the deck catches up when Spotify does
    if (!core.still()) { row.classList.remove("is-starting"); void row.offsetWidth; row.classList.add("is-starting"); setTimeout(() => row.classList.remove("is-starting"), 1400); }
    if (v.kind === "playlist" || v.kind === "album") {
      core.playFrom({ context: entry.head?.uri || `spotify:${v.kind}:${v.id}`, offset_uri: t.uri });
    } else {
      // Liked Songs, Recent and search have no context to play in: play the list from this song on
      const uris = list.slice(Number(row.dataset.i)).map((x) => x.uri).filter(Boolean).slice(0, 100);
      core.playFrom({ uris, offset_uri: t.uri });
    }
  }
  function onClick(e) {
    let t = e.target.closest?.("[data-lib], [data-lib-tab]");
    // the rest of a cassette (reels, shell) opens it too
    if (!t && e.target.closest?.(".ml-tape:not(.ml-tape--skel)")) t = e.target.closest(".ml-tape").querySelector(".ml-tape-open");
    // a click on a row (not on one of its buttons) makes it the row the keys move from
    if (!t) { const row = e.target.closest?.(".ml-row:not(.ml-row--skel)"); if (row && root.contains(row)) rove(row); return; }
    if (!root?.contains(t) || t.disabled) return;
    if (t.dataset.libTab) return showTab(t.dataset.libTab);
    const what = t.dataset.lib, row = t.closest(".ml-row");
    const label = t.classList.contains("ml-link") ? t.textContent.trim() : t.closest(".ml-card, .mu-tape")?.querySelector("b")?.textContent || t.textContent.trim();
    if (what === "back") { stack.pop(); if (top().kind !== "search") input.value = ""; render(); return; }
    if (what === "playlist" || what === "album" || what === "artist") return go({ kind: what, id: t.dataset.id, label });
    if (what === "play" && row) { rove(row); return playRow(row); }
    if (what === "play-context") return core.playFrom({ context: t.dataset.uri });
    if (what === "queue" && row) { const item = itemsOfRow(row)[Number(row.dataset.i)]; return item?.uri && core.queueAdd(item.uri, item.name); }
    if (what === "like" && row) { const id = row.dataset.id; return id && core.like([id], core.liked(id) !== true); }
    if (what === "more") return load(top(), true);
    if (what === "more-search") return loadSearch(top().q, t.dataset.type, true);
    if (what === "retry") { cache.delete(keyOf(top())); painted = ""; return render(); }
  }
  function showTab(kind) {
    if (!root) return;
    const was = stack[0].kind;
    stack = [{ kind }];
    input.value = "";
    render();
    const tab = root.querySelector(`[data-lib-tab="${kind}"]`);
    if (tab && was !== kind && !core.still()) { tab.classList.remove("is-new"); void tab.offsetWidth; tab.classList.add("is-new"); setTimeout(() => tab.classList.remove("is-new"), 300); }
  }
  function onSearch(now = false) {
    clearTimeout(searchTimer);
    const apply = () => {
      const text = input.value.trim().slice(0, 200);
      const base = stack.filter((s) => s.kind !== "search");
      stack = text ? [...base.slice(0, 1), { kind: "search", q: text }] : base.slice(0, 1);
      render();
    };
    root.querySelector(".ml-search").classList.toggle("is-typing", !!input.value.trim());
    if (now) apply(); else searchTimer = setTimeout(apply, 300);
  }
  function focusSearch() {
    if (!input || !input.getClientRects().length) return false;
    input.focus();
    input.select();
    return true;
  }

  /* ------------------------------------------------------------ mount */

  function mount(el, c) {
    core = c;
    root = el;
    painted = "";
    root.innerHTML = `<section class="ml" aria-labelledby="ml-title">
      <header class="ml-head"><h3 class="ml-title" id="ml-title">Library</h3>
        <nav class="ml-tabs" role="tablist" aria-label="Library">${TABS.map(([id, label]) => `<button class="ml-tab" type="button" role="tab" data-lib-tab="${id}" aria-selected="false">${label}</button>`).join("")}</nav>
        <label class="ml-search"><span class="visually-hidden">Search Spotify</span><input type="search" maxlength="200" placeholder="Search songs, albums, artists, playlists" autocomplete="off" spellcheck="false">
          <kbd class="ml-slash num8" aria-hidden="true">/</kbd></label></header>
      <div class="ml-crumbs" hidden></div>
      <div class="ml-body"></div></section>`;
    body = root.querySelector(".ml-body");
    crumbs = root.querySelector(".ml-crumbs");
    input = root.querySelector(".ml-search input");
    input.addEventListener("input", () => onSearch());
    root.addEventListener("keydown", onKey);
    root.addEventListener("click", onClick);
    if (!registered) {
      registered = true;
      core.addView({
        paint() { const id = core.player?.item?.id ?? null; if (id !== seen) { seen = id; markCurrent(); if (top().kind === "queue") load(top()); } else if (body) body.classList.toggle("is-playing", core.playing()); },
        likes: paintLikes,
        queued() { cache.delete("queue"); if (top().kind === "queue") render(); },
        refreshed() { cache.delete(keyOf(top())); if (root?.isConnected) render(); },
        mode(m, was) { if (m !== was) { cache.clear(); stack = [{ kind: "playlists" }]; painted = ""; if (input) input.value = ""; if (root?.isConnected) render(); } },
        relook() { if (body) for (const cv of body.querySelectorAll("canvas[data-thumb]")) { cv.dataset.art = ""; draw(cv); } },
      });
    }
    render();
  }

  return { mount, open: (v) => go(v), showTab, focusSearch };
})();
