"use strict";

/* The compact music player, twice: a floating window above the layout (drag it by its title
   bar, it stays on screen and remembers where it was), and a dropdown under the status bar's
   mini player (drag its grip to tear it off into the float, where you drop it). Both show
   the art, a marquee, the keys, a thin position bar, the volume and a short list (up next,
   or the playlist or album playing). They read the player's one state through Music.core;
   every string from Spotify is written with esc() or textContent. */

const MusicFloat = (() => {
  const KEY = "prdesk.music.float.v1";
  const MARGIN = 6, ART_CSS = 64, LIST_EVERY = 20000;
  let core = null, float = null, drop = null, anchor = null;
  let drag = null, tear = null, dropOpen = false;
  let st = (() => { try { return { open: false, x: null, y: null, expanded: false, list: "queue", ...(JSON.parse(localStorage.getItem(KEY)) || {}) }; } catch { return { open: false, x: null, y: null, expanded: false, list: "queue" }; } })();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch { /* storage blocked: it forgets on reload */ } };
  const live = () => core && (core.mode === "live" || core.mode === "demo");

  /* ------------------------------------------------------------ the compact player */

  function html(kind) {
    const g = core.glyph;
    const mq = (cls) => `<div class="mu-mq ${cls}"><span class="mu-mq-track"><span class="mu-mq-text"></span><span class="mu-mq-text" aria-hidden="true"></span></span></div>`;
    const tools = kind === "float"
      ? `<button class="mc-btn" type="button" data-mc="list" aria-pressed="false" aria-label="Show the list" data-tip="The list">${g("list", 2)}</button>
         <button class="mc-btn" type="button" data-mc="dock" aria-label="Attach to the tabs" data-tip="Attach to the tabs">${g("dock", 2)}</button>
         <button class="mc-btn" type="button" data-mc="close" aria-label="Close the mini player" data-tip="Close">${g("close", 2)}</button>`
      : `<button class="mc-btn" type="button" data-mc="list" aria-pressed="false" aria-label="Show the list" data-tip="The list">${g("list", 2)}</button>
         <button class="mc-btn" type="button" data-mc="float" aria-label="Float it over the layout" data-tip="Float it">${g("pop", 2)}</button>
         <button class="mc-btn" type="button" data-music="open" aria-label="Open the Music panel" data-tip="Open the Music panel">${g("dock", 2)}</button>`;
    return `<div class="mc-head" data-mc-grip>
        <span class="mc-grip" aria-hidden="true"></span><b class="mc-name num8">${kind === "float" ? "MUSIC" : "NOW PLAYING"}</b>
        <span class="mc-tools">${tools}</span></div>
      <div class="mc-body">
        <button class="mc-art" type="button" data-music="look" aria-label="Album art. Click to change the pixel look"><canvas width="32" height="32"></canvas><span class="mc-look num8"></span></button>
        <div class="mc-info">${mq("mc-title")}${mq("mc-sub")}
          <div class="mc-seek" data-music="seek" role="slider" tabindex="0" aria-label="Position in the song" aria-valuemin="0"><i></i></div>
          <div class="mc-time num8"><span class="mc-pos">0:00</span><span class="mc-dur">0:00</span></div></div></div>
      <div class="mc-keys">
        <button class="mc-key" type="button" data-music="previous" aria-label="Previous">${g("prev", 2)}</button>
        <button class="mc-key mc-key--play" type="button" data-music="toggle" aria-label="Play">${g("play", 2)}${g("pause", 2)}</button>
        <button class="mc-key" type="button" data-music="next" aria-label="Next">${g("next", 2)}</button>
        <button class="mc-key mc-like" type="button" data-music="like" aria-pressed="false" aria-label="Like this song">${g("heart", 2)}</button>
        <div class="mc-vol" data-music="volume" role="slider" tabindex="0" aria-label="Volume" aria-valuemin="0" aria-valuemax="100">${"<i></i>".repeat(10)}</div></div>
      <div class="mc-list" hidden>
        <div class="mc-list-tabs" role="tablist" aria-label="List"><button class="mc-tab" type="button" role="tab" data-mc="tab" data-tab="queue">UP NEXT</button>
          <button class="mc-tab" type="button" role="tab" data-mc="tab" data-tab="context">THIS LIST</button></div>
        <ol class="mc-rows"></ol></div>`;
  }
  function make(kind) {
    const el = document.createElement("div");
    el.className = `mc mc--${kind}`;
    el.hidden = true;
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", kind === "float" ? "Floating music player" : "Music player");
    el.innerHTML = html(kind);
    document.body.append(el);
    const $ = (s) => el.querySelector(s);
    el._r = { title: $(".mc-title"), sub: $(".mc-sub"), art: $(".mc-art canvas"), look: $(".mc-look"), seek: $(".mc-seek"), fill: $(".mc-seek i"),
      pos: $(".mc-pos"), dur: $(".mc-dur"), play: $(".mc-key--play"), like: $(".mc-like"), vol: $(".mc-vol"), list: $(".mc-list"), rows: $(".mc-rows"),
      listBtn: $("[data-mc='list']"), prev: $("[data-music='previous']"), next: $("[data-music='next']") };
    el._list = { kind: st.list, items: null, at: 0, key: "" };
    return el;
  }
  const instances = () => [float, drop].filter((el) => el && !el.hidden);

  function paintOne(el) {
    const r = el._r, p = core.player, item = p?.item, on = core.playing();
    el.classList.toggle("is-playing", on);
    core.marquee(r.title, item ? item.name : p ? "Nothing is playing" : "Tuning in");
    core.marquee(r.sub, item ? [item.artists.join(", "), item.album].filter(Boolean).join("  ·  ") : p ? "Start Spotify somewhere, or pick a song in the library" : "");
    r.play.classList.toggle("is-playing", on);
    r.play.setAttribute("aria-label", on ? "Pause" : "Play");
    r.play.disabled = !item;
    r.prev.disabled = r.next.disabled = !item;
    const liked = item?.id ? core.liked(item.id) : undefined;
    r.like.setAttribute("aria-pressed", String(liked === true));
    r.like.disabled = !item?.id || item.kind === "episode";
    const vol = p?.device?.volume, lit = vol == null ? 0 : Math.round(vol / 10);
    [...r.vol.children].forEach((b, i) => b.classList.toggle("is-on", i < lit));
    r.vol.setAttribute("aria-valuenow", String(vol ?? 0));
    r.look.textContent = core.lookLabel();
    const want = core.artKey(p ? item : null);
    if (r.art.dataset.art !== want) core.drawArt(r.art, want, ART_CSS);
    tickOne(el);
    const key = `${el._list.kind}|${item?.id || ""}|${p?.context?.uri || ""}`;
    if (!el._r.list.hidden && el._list.key !== key) loadList(el);
  }
  function tickOne(el) {
    const r = el._r, dur = core.player?.item?.duration_ms || 0, pos = core.position();
    r.fill.style.width = `${dur ? Math.min(100, (pos / dur) * 100).toFixed(2) : 0}%`;
    r.pos.textContent = core.fmt(pos);
    r.dur.textContent = core.fmt(dur);
    r.seek.setAttribute("aria-valuemax", String(Math.round(dur / 1000)));
    r.seek.setAttribute("aria-valuenow", String(Math.round(pos / 1000)));
    r.seek.setAttribute("aria-valuetext", `${core.fmt(pos)} of ${core.fmt(dur)}`);
  }

  /* ------------------------------------------------------------ the short list */

  async function loadList(el) {
    const p = core.player, ctx = p?.context, kind = el._list.kind;
    const key = `${kind}|${p?.item?.id || ""}|${ctx?.uri || ""}`;
    el._list.key = key;
    const rows = el._r.rows;
    if (kind === "context" && (!ctx || ctx.type === "artist")) { rows.innerHTML = `<li class="mc-empty">${ctx ? "An artist plays from the top: no list to show." : "Nothing plays from a playlist or album now."}</li>`; return; }
    if (!el._list.items) rows.innerHTML = '<li class="mc-empty num8">LOADING</li>';
    try {
      const src = core.source();
      const out = kind === "queue" ? await src.queue() : ctx.type === "playlist" ? await src.playlist(ctx.id, 0) : await src.album(ctx.id, 0);
      if (el._list.key !== key) return;
      el._list.items = (out.items || []).slice(0, 50);
      el._list.at = Date.now();
      paintList(el);
    } catch (err) {
      if (err.message === "reloading" || el._list.key !== key) return;
      rows.innerHTML = `<li class="mc-empty">${esc(err.message)}</li>`;
    }
  }
  function paintList(el) {
    const items = el._list.items || [], current = core.player?.item?.id;
    el._r.rows.innerHTML = items.length ? items.map((t, i) => `<li class="mc-row${t.id && t.id === current ? " is-current" : ""}">
        <button class="mc-row-btn" type="button" data-mc="row" data-i="${i}"${el._list.kind === "queue" ? " disabled" : ""}>
          <span class="mc-n num8">${String(i + 1).padStart(2, "0")}</span><span class="mc-row-text"><b>${esc(t.name)}</b><small>${esc((t.artists || []).join(", "))}</small></span>
          <span class="mc-row-dur num8">${core.fmt(t.duration_ms)}</span></button></li>`).join("")
      : `<li class="mc-empty">${el._list.kind === "queue" ? "The queue is empty." : "Nothing here."}</li>`;
  }
  function setList(el, kind) {
    el._list = { kind, items: null, at: 0, key: "" };
    st.list = kind;
    save();
    for (const b of el.querySelectorAll("[data-mc='tab']")) b.setAttribute("aria-selected", String(b.dataset.tab === kind));
    loadList(el);
  }
  function expand(el, on) {
    el._r.list.hidden = !on;
    el._r.listBtn.setAttribute("aria-pressed", String(on));
    el._r.listBtn.setAttribute("aria-label", on ? "Hide the list" : "Show the list");
    for (const b of el.querySelectorAll("[data-mc='tab']")) b.setAttribute("aria-selected", String(b.dataset.tab === el._list.kind));
    if (on) loadList(el);
    if (el === float) { st.expanded = on; save(); place(st.x, st.y); }
  }

  /* ------------------------------------------------------------ opening and closing, in pixel steps */

  function animate(el, cls, done) {
    el.classList.remove("is-opening", "is-closing", "is-dropping", "is-lifting");
    if (core.still()) { done?.(); return; }
    el.classList.add(cls);
    let finished = false;
    const end = () => { if (finished) return; finished = true; el.classList.remove(cls); done?.(); };
    el.addEventListener("animationend", end, { once: true });
    setTimeout(end, 500);
  }
  function show(el) {
    el.hidden = false;
    paintOne(el);
    core.fit(el._r.title); core.fit(el._r.sub);
  }

  function place(x, y) {
    if (!float) return;
    const w = float.offsetWidth || 300, h = float.offsetHeight || 160;
    const nx = Math.round(Math.min(Math.max(MARGIN, x ?? innerWidth - w - 24), Math.max(MARGIN, innerWidth - w - MARGIN)));
    // first time: top right, under the status bar (toasts own the bottom right)
    const ny = Math.round(Math.min(Math.max(MARGIN, y ?? 96), Math.max(MARGIN, innerHeight - h - MARGIN)));
    float.style.left = `${nx}px`;
    float.style.top = `${ny}px`;
    st.x = nx; st.y = ny;
  }
  function openFloat({ x, y, quiet = false } = {}) {
    if (!float) return;
    const was = !float.hidden;
    show(float);
    expand(float, !!st.expanded);
    place(x ?? st.x, y ?? st.y);
    st.open = true;
    save();
    if (!was && !quiet) animate(float, "is-opening");
  }
  function closeFloat(then) {
    if (!float || float.hidden) { then?.(); return; }
    st.open = false;
    save();
    animate(float, "is-closing", () => { float.hidden = true; then?.(); });
  }
  function popOut() {
    if (!live()) return;
    closeDropdown(true);
    openFloat();
    Layout.hide("music");
  }
  function dock() {
    closeFloat();
    Layout.activate("music");
  }

  function openDropdown(at) {
    if (!drop || !live()) return;
    anchor = at || anchor;
    show(drop);
    expand(drop, true);
    const r = anchor.getBoundingClientRect(), w = drop.offsetWidth || 320;
    drop.style.left = `${Math.round(Math.min(Math.max(MARGIN, r.right - w), innerWidth - w - MARGIN))}px`;
    drop.style.top = `${Math.round(r.bottom + 6)}px`;
    dropOpen = true;
    anchor.querySelector("[data-music='drop']")?.setAttribute("aria-expanded", "true");
    animate(drop, "is-dropping");
    drop.querySelector("[data-music='toggle']")?.focus({ preventScroll: true });
  }
  function closeDropdown(instant = false) {
    if (!drop || drop.hidden) return;
    dropOpen = false;
    anchor?.querySelector("[data-music='drop']")?.setAttribute("aria-expanded", "false");
    if (instant) { drop.hidden = true; return; }
    animate(drop, "is-lifting", () => { if (!dropOpen) drop.hidden = true; });
  }
  function toggleDropdown(at) { if (drop && !drop.hidden && dropOpen) closeDropdown(); else openDropdown(at); }

  /* ------------------------------------------------------------ dragging */

  function onDown(e) {
    const head = e.target.closest?.("[data-mc-grip]");
    if (!head || e.button !== 0 || e.target.closest("button")) return;
    const el = head.closest(".mc"), r = el.getBoundingClientRect();
    e.preventDefault();
    if (el === float) {
      drag = { id: e.pointerId, dx: e.clientX - r.left, dy: e.clientY - r.top };
      float.setPointerCapture(e.pointerId);
      float.classList.add("is-dragging");
    } else {
      tear = { id: e.pointerId, x: e.clientX, y: e.clientY, dx: e.clientX - r.left, dy: e.clientY - r.top };
      drop.setPointerCapture(e.pointerId);
    }
  }
  function onMove(e) {
    if (tear && e.pointerId === tear.id && Math.hypot(e.clientX - tear.x, e.clientY - tear.y) > 6) {
      // torn off: the float takes the pointer where the dropdown was and keeps following it
      const t = tear;
      tear = null;
      st.expanded = !drop._r.list.hidden;
      openFloat({ x: e.clientX - t.dx, y: e.clientY - t.dy, quiet: true });
      drag = { id: e.pointerId, dx: t.dx, dy: t.dy };
      try { float.setPointerCapture(e.pointerId); } catch { /* the pointer is gone already: the float stays where it is */ }
      float.classList.add("is-dragging");
      closeDropdown(true);
      return;
    }
    if (drag && e.pointerId === drag.id) place(e.clientX - drag.dx, e.clientY - drag.dy);
  }
  function onUp(e) {
    if (tear && e.pointerId === tear.id) tear = null;
    if (drag && e.pointerId === drag.id) { drag = null; float.classList.remove("is-dragging"); save(); }
  }

  function onClick(e) {
    const t = e.target.closest?.("[data-mc]");
    if (!t || t.disabled) return;
    const el = t.closest(".mc"), what = t.dataset.mc;
    if (what === "list") return expand(el, el._r.list.hidden);
    if (what === "tab") return setList(el, t.dataset.tab);
    if (what === "close") return closeFloat();
    if (what === "dock") return dock();
    if (what === "float") {
      const r = drop.getBoundingClientRect();
      st.expanded = !drop._r.list.hidden;
      closeDropdown(true);
      return openFloat({ x: r.left, y: r.top });
    }
    if (what === "row") {
      const item = el._list.items?.[Number(t.dataset.i)], ctx = core.player?.context;
      if (item?.uri && ctx) core.playFrom({ context: ctx.uri, offset_uri: item.uri });
    }
  }

  /* ------------------------------------------------------------ mount */

  function mount(c) {
    core = c;
    float = make("float");
    drop = make("drop");
    for (const el of [float, drop]) {
      el.addEventListener("pointerdown", onDown);
      el.addEventListener("pointermove", onMove);
      el.addEventListener("pointerup", onUp);
      el.addEventListener("pointercancel", onUp);
      el.addEventListener("click", onClick);
    }
    addEventListener("resize", () => { if (float && !float.hidden) { place(st.x, st.y); save(); } if (dropOpen) openDropdown(); });
    // the list fills in after it opens: keep the whole window on screen as it grows
    new ResizeObserver(() => { if (!float.hidden && !drag) place(st.x, st.y); }).observe(float);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && dropOpen) { closeDropdown(); anchor?.querySelector("[data-music='drop']")?.focus(); } });
    document.addEventListener("pointerdown", (e) => {
      if (!dropOpen || drop.contains(e.target) || anchor?.contains(e.target)) return;
      closeDropdown();
    }, true);
    setInterval(() => { for (const el of instances()) if (!el._r.list.hidden && Date.now() - el._list.at > LIST_EVERY && el._list.items) loadList(el); }, 5000);
    core.addView({
      visible: () => instances().length > 0,
      paint() { for (const el of instances()) paintOne(el); },
      tick() { for (const el of instances()) tickOne(el); },
      likes() { for (const el of instances()) paintOne(el); },
      refit() { for (const el of instances()) { core.fit(el._r.title); core.fit(el._r.sub); } },
      relook() { for (const el of [float, drop]) { el._r.art.dataset.art = ""; if (!el.hidden) paintOne(el); } },
      queued() { for (const el of instances()) if (el._list.kind === "queue") { el._list.key = ""; loadList(el); } },
      mode(m) {
        if (m === "live" || m === "demo") { if (st.open && float.hidden) openFloat({ quiet: true }); }
        else { float.hidden = true; closeDropdown(true); }
      },
    });
  }

  return {
    mount, popOut, dock, toggleDropdown, openDropdown, closeDropdown,
    isOpen: () => !!float && (!float.hidden || st.open),
  };
})();
