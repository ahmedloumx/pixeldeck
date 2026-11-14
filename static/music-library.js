"use strict";

/* The Music panel's library: your playlists (as cassettes), Liked Songs, saved albums,
   recently played, the queue, search, and a playlist, album or artist opened from any of
   them. Rows play from that song, add it to the queue, or like it. Lists come a page at a
   time. Every string from Spotify is written with esc(); thumbnails are canvases drawn
   through the player's art pipeline, only once they scroll into view. */

const MusicLibrary = (() => {
  const TABS = [["playlists", "Playlists"], ["liked", "Liked Songs"], ["albums", "Albums"], ["recent", "Recent"], ["queue", "Queue"]];
  const STALE = 120000, QUEUE_STALE = 5000;
  const TAPE_TONES = ["--sprite-orange", "--sprite-blue", "--sprite-yellow", "--sprite-red", "--sprite-grass", "--color-cool"];
  let core = null, root = null, body = null, crumbs = null, input = null, registered = false;
  let stack = [{ kind: "playlists" }];
  const cache = new Map();
  let searchTimer = 0, seen = null;

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
  function artistLinks(t) {
    return (t.artists || []).map((a, k) => (t.artist_ids?.[k] ? btn(`data-lib="artist" data-id="${esc(t.artist_ids[k])}"`, esc(a), "ml-link") : `<span>${esc(a)}</span>`)).join(", ");
  }
  function rowHtml(t, i, { numbers = "index", play = true } = {}) {
    const current = core.player?.item?.id && t.id === core.player.item.id;
    const liked = t.id ? core.liked(t.id) : undefined;
    const n = numbers === "track" && t.number ? t.number : i + 1;
    const album = t.album ? (t.album_id ? btn(`data-lib="album" data-id="${esc(t.album_id)}"`, esc(t.album), "ml-link") : `<span>${esc(t.album)}</span>`) : "";
    return `<li class="ml-row${current ? " is-current" : ""}${t.playable === false ? " is-off" : ""}" data-i="${i}" data-id="${esc(t.id || "")}">
      <span class="ml-num num8" aria-hidden="true"><span class="ml-n">${String(n).padStart(2, "0")}</span><span class="ml-eq"><i></i><i></i><i></i></span></span>
      <canvas class="ml-thumb" width="16" height="16" data-thumb="${esc(t.thumb || `plain:${t.id || t.name}`)}" aria-hidden="true"></canvas>
      <span class="ml-main"><b>${esc(t.name)}</b><small>${artistLinks(t)}</small></span>
      <span class="ml-album">${album}</span>
      <span class="ml-dur num8">${core.fmt(t.duration_ms)}</span>
      <span class="ml-tools">
        ${t.id && t.kind !== "episode" ? btn(`data-lib="like" aria-pressed="${liked === true}" aria-label="${liked ? "Remove from" : "Add to"} Liked Songs: ${esc(t.name)}"`, core.glyph("heart", 2), "ml-tool ml-like") : ""}
        ${t.uri ? btn(`data-lib="queue" aria-label="Add to the queue: ${esc(t.name)}" data-tip="Add to the queue"`, core.glyph("plus", 2)) : ""}
        ${play && t.uri && t.playable !== false ? btn(`data-lib="play" aria-label="Play ${esc(t.name)}"`, core.glyph("play", 2), "ml-tool ml-play") : ""}
      </span></li>`;
  }
  // Each list says where its songs live (the view's items, the queue's current song, search songs).
  const rows = (items, opts = {}) => `<ol class="ml-rows" data-list="${opts.list || "items"}">${items.map((t, i) => rowHtml(t, i, opts)).join("")}</ol>`;
  function tapeHtml(p) {
    const meta = [p.tracks != null ? plural(p.tracks, "song") : "", p.owner || ""].filter(Boolean).join("  ");
    // the label opens the playlist; the play key sits in the tape window, between the reels
    return `<div class="mu-tape ml-tape" style="--tape:var(${TAPE_TONES[hash(p.id) % TAPE_TONES.length]})" data-id="${esc(p.id)}">
      <span class="mu-tape-label">
        <button class="ml-tape-open" type="button" data-lib="playlist" data-id="${esc(p.id)}" aria-label="Open the playlist ${esc(p.name)}">
          <span class="mu-tape-top"><span class="mu-tape-side num8" aria-hidden="true">A</span><b>${esc(p.name)}</b></span><small>${esc(meta)}</small></button>
        <span class="mu-tape-window"><i class="mu-reel" aria-hidden="true"></i>
          ${btn(`data-lib="play-context" data-uri="${esc(p.uri || `spotify:playlist:${p.id}`)}" aria-label="Play the playlist ${esc(p.name)}"`, core.glyph("play", 2), "ml-tape-play")}<i class="mu-reel" aria-hidden="true"></i></span></span>
      <span class="mu-tape-foot" aria-hidden="true"><i></i><i></i><i></i></span>
      <span class="mu-tape-sticker num8" aria-hidden="true">ON AIR</span></div>`;
  }
  function cardHtml(a, kind) {
    const meta = kind === "album" ? [a.artists?.join(", "), a.year].filter(Boolean).join("  ") : (a.genres || []).join(", ");
    return `<button class="ml-card ml-card--${kind}" type="button" data-lib="${kind}" data-id="${esc(a.id)}" aria-label="Open ${esc(a.name)}">
      <span class="ml-card-art"><canvas width="32" height="32" data-thumb="${esc(a.art || a.thumb || `plain:${a.id}`)}" data-css="128" aria-hidden="true"></canvas></span>
      <b>${esc(a.name)}</b><small>${esc(meta)}</small></button>`;
  }
  const grid = (items, kind) => `<div class="ml-grid ml-grid--${kind}">${items.map((a) => (kind === "tape" ? tapeHtml(a) : cardHtml(a, kind))).join("")}</div>`;
  function more(entry, label = "Load more", attrs = 'data-lib="more"') {
    if (entry.loading) return '<p class="ml-loading num8">LOADING</p>';
    return entry.next != null || entry.before ? `<p class="ml-more"><button class="btn" type="button" ${attrs}>${label}</button></p>` : "";
  }
  function heroHtml(h, kind) {
    if (!h) return "";
    const meta = kind === "playlist" ? [h.owner ? `by ${h.owner}` : "", h.tracks != null ? plural(h.tracks, "song") : ""]
      : kind === "album" ? [h.artists?.join(", "), h.year, h.tracks != null ? plural(h.tracks, "song") : ""] : [(h.genres || []).join(", ")];
    return `<div class="ml-hero"><span class="ml-hero-art"><canvas width="32" height="32" data-thumb="${esc(h.art || h.thumb || `plain:${h.id}`)}" data-css="128" aria-hidden="true"></canvas></span>
      <div class="ml-hero-text"><p class="ml-kind num8">${kind.toUpperCase()}</p><h4>${esc(h.name)}</h4><p class="ml-meta">${esc(meta.filter(Boolean).join("  ·  "))}</p>
      <p>${btn(`data-lib="play-context" data-uri="${esc(h.uri || `spotify:${kind}:${h.id}`)}"`, `${core.glyph("play", 2)}<span>Play</span>`, "btn btn--primary ml-play-all")}</p></div></div>`;
  }
  function lockHtml(locked) {
    return `<div class="ml-lock" role="note">${core.glyph("lock", 4)}<div><h4>Reconnect to unlock the library</h4><p>${esc(locked.message)}</p>
      <p><button class="btn btn--primary" type="button" data-music="connect">Reconnect Spotify</button></p></div></div>`;
  }
  const empty = (text) => `<p class="ml-empty">${esc(text)}</p>`;

  /* ------------------------------------------------------------ the views */

  function viewHtml(v, entry) {
    if (entry.locked) return lockHtml(entry.locked);
    if (entry.error && !entry.items?.length && !entry.sections) return `<div class="ml-error"><p>${esc(entry.error)}</p><p><button class="btn" type="button" data-lib="retry">Try again</button></p></div>`;
    if (!entry.at) return '<p class="ml-loading num8">LOADING</p>';
    const items = entry.items || [];
    switch (v.kind) {
      case "playlists": return items.length ? grid(items, "tape") + more(entry) : empty("No playlists yet. The ones you make or follow in Spotify show up here.");
      case "liked": return items.length ? rows(items) + more(entry) : empty("No liked songs yet. The heart on any song adds it here.");
      case "albums": return items.length ? grid(items, "album") + more(entry) : empty("No saved albums yet.");
      case "recent": return items.length ? rows(items) + more(entry, "Earlier") : empty("Nothing played lately.");
      case "queue": return `${entry.current ? `<h5 class="ml-sub num8">NOW PLAYING</h5>${rows([entry.current], { play: false, list: "current" })}` : ""}
        <h5 class="ml-sub num8">NEXT UP</h5>${items.length ? rows(items, { play: false }) : empty("The queue is empty. The plus on any song adds it here.")}`;
      case "playlist": return heroHtml(entry.head, "playlist") + (items.length ? rows(items) : empty("This playlist is empty.")) + more(entry);
      case "album": return heroHtml(entry.head, "album") + rows(items, { numbers: "track" }) + more(entry);
      case "artist": return heroHtml(entry.head, "artist") + `<h5 class="ml-sub num8">ALBUMS AND SINGLES</h5>` + (items.length ? grid(items, "album") : empty("No albums here.")) + more(entry);
      default: return "";
    }
  }
  function searchHtml(v, entry) {
    if (entry.error && !entry.sections) return `<div class="ml-error"><p>${esc(entry.error)}</p></div>`;
    if (!entry.sections || entry.loading === "all") return '<p class="ml-loading num8">SEARCHING</p>';
    const s = entry.sections, part = (name, title, html, type) => {
      const page = s[name];
      if (!page?.items?.length) return "";
      return `<section class="ml-section"><h5 class="ml-sub num8">${title}</h5>${html(page.items)}
        ${entry.loading === type ? '<p class="ml-loading num8">LOADING</p>' : page.next != null ? `<p class="ml-more"><button class="btn btn--quiet" type="button" data-lib="more-search" data-type="${type}">More ${title.toLowerCase()}</button></p>` : ""}</section>`;
    };
    const html = part("tracks", "SONGS", (items) => rows(items, { list: "search" }), "track") + part("artists", "ARTISTS", (items) => grid(items, "artist"), "artist")
      + part("albums", "ALBUMS", (items) => grid(items, "album"), "album") + part("playlists", "PLAYLISTS", (items) => grid(items, "tape"), "playlist");
    return html || empty(`Nothing found for "${v.q}".`);
  }

  function render() {
    if (!root) return;
    const v = top(), key = keyOf(v);
    for (const b of root.querySelectorAll("[data-lib-tab]")) b.setAttribute("aria-selected", String(stack[0].kind === b.dataset.libTab && v.kind !== "search"));
    const trail = stack.length > 1 ? stack.map((s) => s.label || TABS.find(([id]) => id === s.kind)?.[1] || (s.kind === "search" ? `Search "${s.q}"` : s.kind)) : [];
    crumbs.hidden = !trail.length;
    crumbs.innerHTML = trail.length ? `${btn('data-lib="back"', `${core.glyph("back", 2)}<span>Back</span>`, "btn btn--quiet ml-back")}<span class="ml-trail">${trail.map((t) => `<span>${esc(t)}</span>`).join('<i aria-hidden="true">/</i>')}</span>` : "";
    const entry = cache.get(key) || {};
    body.innerHTML = v.kind === "search" ? searchHtml(v, entry) : viewHtml(v, entry);
    if (!entry.at && !entry.loading && !entry.error && !entry.locked) { if (v.kind === "search") loadSearch(v.q); else load(v); }
    else if (entry.at && Date.now() - entry.at > (v.kind === "queue" ? QUEUE_STALE : STALE) && !entry.loading) {
      entry.at = Date.now();  // stale: show it, and ask again in the background
      if (v.kind === "search") loadSearch(v.q); else load(v);
    }
    hydrate();
    markCurrent();
  }

  let observer = null;
  function hydrate() {
    observer?.disconnect();
    observer = "IntersectionObserver" in window ? new IntersectionObserver((seenItems) => {
      for (const e of seenItems) if (e.isIntersecting) { observer.unobserve(e.target); draw(e.target); }
    }, { rootMargin: "120px" }) : null;
    for (const c of body.querySelectorAll("canvas[data-thumb]")) observer ? observer.observe(c) : draw(c);
    const ids = [...body.querySelectorAll(".ml-row[data-id]")].map((r) => r.dataset.id).filter(Boolean);
    if (ids.length) core.checkLikes(ids);
  }
  const draw = (c) => core.drawArt(c, c.dataset.thumb, Number(c.dataset.css) || 32);

  function markCurrent() {
    if (!body) return;
    const id = core.player?.item?.id, on = core.playing();
    for (const r of body.querySelectorAll(".ml-row")) r.classList.toggle("is-current", !!id && r.dataset.id === id);
    body.classList.toggle("is-playing", on);
    const ctx = core.player?.context?.uri;
    for (const t of body.querySelectorAll(".ml-tape")) t.classList.toggle("is-on", !!ctx && ctx === `spotify:playlist:${t.dataset.id}`);
  }
  function paintLikes() {
    if (!body) return;
    for (const b of body.querySelectorAll(".ml-row [data-lib='like']")) {
      const id = b.closest(".ml-row").dataset.id, on = core.liked(id) === true;
      if (b.getAttribute("aria-pressed") !== String(on)) b.setAttribute("aria-pressed", String(on));
    }
  }

  /* ------------------------------------------------------------ actions */

  function go(v) {
    stack.push(v);
    render();
    root.scrollIntoView?.({ block: "nearest" });
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
    if (!t && e.target.closest?.(".ml-tape")) t = e.target.closest(".ml-tape").querySelector(".ml-tape-open");
    if (!t || !root?.contains(t) || t.disabled) return;
    if (t.dataset.libTab) { stack = [{ kind: t.dataset.libTab }]; input.value = ""; render(); return; }
    const what = t.dataset.lib, row = t.closest(".ml-row");
    const label = t.classList.contains("ml-link") ? t.textContent.trim() : t.closest(".ml-card, .mu-tape")?.querySelector("b")?.textContent || t.textContent.trim();
    if (what === "back") { stack.pop(); if (top().kind !== "search") input.value = ""; render(); return; }
    if (what === "playlist" || what === "album" || what === "artist") return go({ kind: what, id: t.dataset.id, label });
    if (what === "play" && row) return playRow(row);
    if (what === "play-context") return core.playFrom({ context: t.dataset.uri });
    if (what === "queue" && row) { const item = itemsOfRow(row)[Number(row.dataset.i)]; return item?.uri && core.queueAdd(item.uri, item.name); }
    if (what === "like" && row) { const id = row.dataset.id; return id && core.like([id], core.liked(id) !== true); }
    if (what === "more") return load(top(), true);
    if (what === "more-search") return loadSearch(top().q, t.dataset.type, true);
    if (what === "retry") { cache.delete(keyOf(top())); return render(); }
  }
  function onSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const text = input.value.trim().slice(0, 200);
      const base = stack.filter((s) => s.kind !== "search");
      stack = text ? [...base.slice(0, 1), { kind: "search", q: text }] : base.slice(0, 1);
      render();
    }, 350);
  }

  /* ------------------------------------------------------------ mount */

  function mount(el, c) {
    core = c;
    root = el;
    root.innerHTML = `<section class="ml" aria-labelledby="ml-title">
      <header class="ml-head"><h3 class="ml-title" id="ml-title">Library</h3>
        <nav class="ml-tabs" role="tablist" aria-label="Library">${TABS.map(([id, label]) => `<button class="ml-tab" type="button" role="tab" data-lib-tab="${id}" aria-selected="false">${label}</button>`).join("")}</nav>
        <label class="ml-search"><span class="visually-hidden">Search Spotify</span><input type="search" maxlength="200" placeholder="Search songs, albums, artists, playlists" autocomplete="off" spellcheck="false"></label></header>
      <div class="ml-crumbs" hidden></div>
      <div class="ml-body"></div></section>`;
    body = root.querySelector(".ml-body");
    crumbs = root.querySelector(".ml-crumbs");
    input = root.querySelector(".ml-search input");
    input.addEventListener("input", onSearch);
    input.addEventListener("keydown", (e) => { if (e.key === "Escape" && input.value) { input.value = ""; onSearch(); } });
    root.addEventListener("click", onClick);
    if (!registered) {
      registered = true;
      core.addView({
        paint() { const id = core.player?.item?.id ?? null; if (id !== seen) { seen = id; markCurrent(); if (top().kind === "queue") load(top()); } else if (body) body.classList.toggle("is-playing", core.playing()); },
        likes: paintLikes,
        queued() { cache.delete("queue"); if (top().kind === "queue") render(); },
        mode(m, was) { if (m !== was) { cache.clear(); stack = [{ kind: "playlists" }]; if (input) input.value = ""; if (root?.isConnected) render(); } },
        relook() { if (body) for (const cv of body.querySelectorAll("canvas[data-thumb]")) { cv.dataset.art = ""; draw(cv); } },
      });
    }
    render();
  }

  return { mount, open: (v) => go(v) };
})();
