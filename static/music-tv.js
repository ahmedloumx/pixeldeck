"use strict";

/* The YouTube TV: YouTube's own player (youtube-nocookie.com) in a pixel television. It sits in
   the Music panel's YouTube view, or floats over the layout: drag it by its top, it keeps its
   place per browser, stays inside the window and never covers the bar. The blue bot sits on it.
   Nothing is ever drawn over the video (YouTube's terms): the pixel art is the set around it.
   The page talks to the player with postMessage, YouTube's iframe API protocol, to play, pause
   and hear what plays. A player that is not on screen is paused. Moving the set to another
   place loads the player again, so it starts where it was. */

const MusicTV = (() => {
  const KEY = "prdesk.youtube.tv.v1";
  const ORIGIN = "https://www.youtube-nocookie.com";
  const ID = /^[0-9A-Za-z_-]{11}$/;
  const MARGIN = 8, BOT_ROOM = 48, LISTEN_MS = 250, LISTEN_TRIES = 60;
  const STATE = { "-1": "READY", 0: "ENDED", 1: "PLAYING", 2: "PAUSED", 3: "LOADING", 5: "READY" };
  // What the player's error codes mean, said plainly (YouTube's iframe API reference).
  const ERRORS = {
    2: "That is not a video id YouTube knows.", 5: "This video cannot play in this player.",
    100: "This video was removed or is private.", 101: "Its owner does not let it play outside YouTube.",
    150: "Its owner does not let it play outside YouTube.", 153: "YouTube refused to play it here.",
    [-1]: "YouTube is not answering. Check the connection, or watch it on YouTube.",
  };
  let el = null, r = {}, frame = null, slot = null;
  let st = (() => { try { return { floating: false, x: null, y: null, ...(JSON.parse(localStorage.getItem(KEY)) || {}) }; } catch { return { floating: false, x: null, y: null }; } })();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch { /* storage blocked: it forgets on reload */ } };
  let video = null;                          // { id, title, channel }
  let info = { state: -1, time: 0, duration: 0, error: 0 };
  let listening = 0, heard = false, drag = null, seen = "";
  const listeners = new Set();
  const g = (name, px = 2) => (typeof Music !== "undefined" ? Music.core.glyph(name, px) : "");
  const still = () => matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.motion === "calm";

  // Tell the others (the bar, the panel, the control center) only when something they show changed.
  function notify() {
    const now = `${video?.id}|${video?.title}|${!!frame}|${st.floating}|${playing()}|${info.error}`;
    if (now === seen) return;
    seen = now;
    for (const fn of listeners) fn();
  }
  const playing = () => !!frame && (info.state === 1 || info.state === 3);
  const onScreen = () => !!el && el.isConnected && !el.hidden && el.getClientRects().length > 0;
  const fmt = (s) => {
    s = Math.max(0, Math.floor(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
  };

  /* ------------------------------------------------------------ the set */

  // A pixel sunset on the empty screen, drawn once: there is no video there to cover.
  function idleArt() {
    const rows = [];
    for (let y = 0; y < 18; y++) {
      let row = "";
      for (let x = 0; x < 32; x++) {
        const sun = Math.hypot(x - 15.5, y - 11) < 5.2 && y < 12;
        row += y >= 12 ? ((x + y) % 5 === 0 ? "w" : "s") : sun ? "u" : y < 4 ? "a" : y < 8 ? "b" : "c";
      }
      rows.push(row);
    }
    const fill = { a: "ytv-sky-a", b: "ytv-sky-b", c: "ytv-sky-c", u: "ytv-sun", s: "ytv-sea", w: "ytv-wave" };
    let rects = "";
    rows.forEach((row, y) => {
      let x = 0;
      while (x < row.length) {
        let end = x + 1;
        while (end < row.length && row[end] === row[x]) end++;
        rects += `<rect class="${fill[row[x]]}" x="${x}" y="${y}" width="${end - x}" height="1"/>`;
        x = end;
      }
    });
    return `<svg class="ytv-idle-art" viewBox="0 0 32 18" preserveAspectRatio="none" shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`;
  }

  function build() {
    el = document.createElement("section");
    el.className = "ytv";
    el.setAttribute("aria-label", "YouTube TV");
    el.innerHTML = `<div class="ytv-head" data-ytv-grip>
        <span class="ytv-grip" aria-hidden="true"></span><b class="ytv-name num8">NOW SHOWING</b><span class="ytv-title" data-r="title"></span>
        <span class="ytv-tools">
          <button class="ytv-btn ytv-btn--play" type="button" data-ytv="play" data-r="play" aria-label="Play" data-tip="Play">${g("play")}${g("pause")}</button>
          <button class="ytv-btn" type="button" data-ytv="move" data-r="move"></button>
          <button class="ytv-btn" type="button" data-ytv="close" data-r="close" aria-label="Stop and close the video" data-tip="Stop and close">${g("close")}</button>
        </span></div>
      <div class="ytv-bezel"><div class="ytv-screen" data-r="screen">
        <div class="ytv-idle" data-r="idle">${idleArt()}<canvas class="ytv-poster" data-r="poster" width="96" height="54" hidden></canvas>
          <p class="ytv-idle-text num8" data-r="idleText">NO SIGNAL</p>
          <button class="btn btn--primary ytv-idle-btn" type="button" data-ytv="start" data-r="start" hidden>Play</button></div>
      </div></div>
      <div class="ytv-foot"><span class="ytv-led" aria-hidden="true"></span><span class="ytv-state num8" data-r="state" role="status">OFF</span>
        <span class="ytv-knobs" aria-hidden="true"><i></i><i></i></span>
        <button class="link ytv-out" type="button" data-r="out" hidden>Watch on YouTube</button></div>
      <span class="ytv-bot" aria-hidden="true">${typeof Sprites !== "undefined" ? Sprites.svg("bot", { scale: 3, idle: "blink" }) : ""}</span>`;
    r = {};
    for (const x of el.querySelectorAll("[data-r]")) r[x.dataset.r] = x;
  }

  function src(id, start, autoplay) {
    const p = new URLSearchParams({ enablejsapi: "1", playsinline: "1", rel: "0", autoplay: autoplay ? "1" : "0", origin: location.origin });
    if (start >= 1) p.set("start", String(Math.floor(start)));
    return `${ORIGIN}/embed/${id}?${p}`;
  }
  // The player, in its own frame: it may open YouTube in a new window, never steer this page.
  function attach(autoplay) {
    if (!video || !ID.test(video.id)) return;
    detach();
    const f = document.createElement("iframe");
    f.className = "ytv-frame";
    f.title = video.title ? `YouTube: ${video.title}` : "YouTube video";
    f.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
    f.setAttribute("allowfullscreen", "");
    f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-presentation");
    // YouTube's player needs to know which page embeds it; the page itself sends no referrer.
    f.referrerPolicy = "strict-origin-when-cross-origin";
    // a video that played to its end starts over, or it would end again at once
    if (info.duration > 0 && info.time >= info.duration - 1) info.time = 0;
    f.src = src(video.id, info.time, autoplay);
    f.addEventListener("load", listen);
    frame = f;
    heard = false;
    info = { ...info, state: autoplay ? 3 : -1, error: 0 };
    r.screen.append(f);
    paint();
  }
  function detach() {
    clearInterval(listening);
    if (frame) frame.remove();
    frame = null;
    heard = false;
  }
  // The player speaks once it knows someone listens: say so until it answers.
  function listen() {
    clearInterval(listening);
    let tries = 0;
    const hello = () => {
      if (heard || !frame || ++tries > LISTEN_TRIES) {
        clearInterval(listening);
        if (frame && !heard) { info = { ...info, state: -1, error: -1 }; paint(); }
        return;
      }
      frame.contentWindow?.postMessage(JSON.stringify({ event: "listening", id: "pixeldeck", channel: "widget" }), ORIGIN);
    };
    hello();
    listening = setInterval(hello, LISTEN_MS);
  }
  function command(func, args = []) {
    frame?.contentWindow?.postMessage(JSON.stringify({ event: "command", func, args, id: "pixeldeck", channel: "widget" }), ORIGIN);
  }
  function onMessage(e) {
    if (!frame || e.source !== frame.contentWindow || e.origin !== ORIGIN) return;
    let data = e.data;
    if (typeof data === "string") { try { data = JSON.parse(data); } catch { return; } }
    if (!data || typeof data !== "object") return;
    heard = true;
    const what = data.event, said = data.info;
    // initialDelivery is the player's first word, with the title already in it
    if ((what === "infoDelivery" || what === "initialDelivery") && said && typeof said === "object") {
      if (Number.isInteger(said.playerState)) info.state = said.playerState;
      if (Number.isFinite(said.currentTime)) info.time = said.currentTime;
      if (Number.isFinite(said.duration)) info.duration = said.duration;
      const vd = said.videoData;
      if (vd && typeof vd === "object" && vd.video_id === video?.id) {
        const title = typeof vd.title === "string" ? vd.title.trim().slice(0, 300) : "";
        const author = typeof vd.author === "string" ? vd.author.trim().slice(0, 300) : "";
        if (title && (title !== video.title || (author && author !== video.channel))) {
          video = { ...video, title, channel: author || video.channel };
          if (frame) frame.title = `YouTube: ${title}`;
        }
      }
      if (info.state === 1) info.error = 0;
    } else if (what === "onStateChange" && Number.isInteger(said)) {
      info.state = said;
    } else if (what === "onError") {
      info.error = Number(said) || 5;
    }
    paint();
  }

  /* ------------------------------------------------------------ painting */

  function paint() {
    if (!el) return;
    const on = playing(), err = info.error ? ERRORS[info.error] || "YouTube could not play this video here." : "";
    el.classList.toggle("is-floating", st.floating);
    el.classList.toggle("is-playing", on);
    el.classList.toggle("is-loaded", !!frame);
    r.title.textContent = video ? video.title || `Video ${video.id}` : "Nothing on";
    r.title.title = r.title.textContent;
    r.play.classList.toggle("is-playing", on);
    r.play.setAttribute("aria-label", on ? "Pause" : "Play");
    r.play.dataset.tip = on ? "Pause" : "Play";
    r.play.disabled = !video;
    r.close.disabled = !frame && !st.floating;
    r.move.innerHTML = st.floating ? g("dock") : g("pop");
    r.move.setAttribute("aria-label", st.floating ? "Back into the Music panel" : "Float it over the layout");
    r.move.dataset.tip = st.floating ? "Back into the Music panel" : "Float it";
    r.idle.hidden = !!frame;
    r.idleText.textContent = video ? (video.title || "PRESS PLAY").toUpperCase() : "NO SIGNAL";
    // floating and empty, it shows the way to a video; in the panel the list is right there
    r.start.hidden = !video && !st.floating;
    r.start.textContent = video ? "Play" : "Find a video";
    poster();
    const word = err ? "CANNOT PLAY" : !frame ? (video ? "STOPPED" : "OFF") : STATE[info.state] || "READY";
    const time = frame && info.duration > 0 ? `  ${fmt(info.time)} / ${fmt(info.duration)}` : frame && info.time > 0 ? `  ${fmt(info.time)}` : "";
    r.state.textContent = err || `${word}${time}`;
    r.state.classList.toggle("is-error", !!err);
    r.out.hidden = !video;
    if (video) r.out.dataset.external = `https://www.youtube.com/watch?v=${video.id}`;
    notify();
  }
  // The stopped set shows the video's own picture, pixelated, behind the Play button.
  function poster() {
    if (!onScreen()) return;  // fetched when the set shows
    const want = video && !frame ? video.id : "";
    if (r.poster.dataset.id === want) return;
    r.poster.dataset.id = want;
    r.poster.hidden = true;
    if (!want || typeof MusicYouTube === "undefined") return;
    MusicYouTube.thumb(`https://i.ytimg.com/vi/${want}/mqdefault.jpg`).then((bmp) => {
      if (r.poster.dataset.id !== want) return;
      const c = r.poster.getContext("2d");
      c.imageSmoothingEnabled = true;
      c.drawImage(bmp, 0, 0, r.poster.width, r.poster.height);
      r.poster.hidden = false;
    }).catch(() => {});
  }

  /* ------------------------------------------------------------ where it is */

  function barBottom() { return Math.max(0, document.getElementById("bar")?.getBoundingClientRect().bottom || 0); }
  function place(x, y) {
    if (!el || !st.floating) return;
    const w = el.offsetWidth || 440, h = el.offsetHeight || 320;
    const top = Math.round(barBottom() + BOT_ROOM + MARGIN);
    const nx = Math.round(Math.min(Math.max(MARGIN, x ?? innerWidth - w - 24), Math.max(MARGIN, innerWidth - w - MARGIN)));
    // the first time: right, under the bar with room for the bot (toasts own the bottom right)
    const ny = Math.round(Math.min(Math.max(top, y ?? top + 24), Math.max(top, innerHeight - h - MARGIN)));
    el.style.left = `${nx}px`;
    el.style.top = `${ny}px`;
    st.x = nx; st.y = ny;
  }
  // Into the panel or over the layout. A frame that changes parents loads again, so it is
  // made again where it lands, at the time it had, playing if it played.
  function moveTo(floating) {
    if (!el) return;
    const was = playing(), parent = floating ? document.body : slot;
    st.floating = floating;
    save();
    if (parent && el.parentElement !== parent) {
      const had = !!frame;
      detach();
      parent.append(el);
      el.hidden = false;
      // into a panel nobody sees, it stays stopped where it was: Play picks it up there
      if (had && onScreen()) attach(was);
    }
    el.hidden = !parent;
    if (!floating) { el.style.left = ""; el.style.top = ""; }
    paint();
    if (floating) {
      place(st.x, st.y);
      if (!still()) { el.classList.remove("is-opening"); void el.offsetWidth; el.classList.add("is-opening"); setTimeout(() => el.classList.remove("is-opening"), 375); }
    }
    notify();
  }

  /* ------------------------------------------------------------ what the others ask */

  /** Load a video and play it, in the panel or floating. */
  function play(next, { floating = st.floating } = {}) {
    if (!next || !ID.test(next.id)) return;
    // never into a panel nobody sees: a hidden video would play on, unheard of and unseen
    if (!floating && !(slot && slot.getClientRects().length)) floating = true;
    video = { id: next.id, title: next.title || null, channel: next.channel || null };
    info = { state: -1, time: Number(next.time) > 0 ? Number(next.time) : 0, duration: 0, error: 0 };
    detach();
    if (floating !== st.floating || el.parentElement !== (floating ? document.body : slot)) moveTo(floating);
    attach(true);
  }
  /** Show a video without loading the player (after a reload): its picture and a Play button. */
  function cue(next) {
    if (!next || !ID.test(next.id) || frame) return;
    video = { id: next.id, title: next.title || null, channel: next.channel || null };
    info = { state: -1, time: Number(next.time) > 0 ? Number(next.time) : 0, duration: 0, error: 0 };
    paint();
  }
  function toggle() {
    if (!video) return;
    if (!frame) { if (!onScreen()) moveTo(true); attach(true); return; }
    if (playing()) command("pauseVideo"); else command("playVideo");
    info.state = playing() ? 2 : 3;
    paint();
  }
  /** Stop the video; a floating set goes back into the panel, out of the way. */
  function stop() {
    detach();
    info = { ...info, state: -1, error: 0 };
    if (st.floating) moveTo(false); else paint();
  }
  function float() { moveTo(true); }
  /** Back into the Music panel; open says whether to show the panel too (the TV's own button does). */
  function dock({ open = true } = {}) {
    // the panel first, so the video lands where it shows and plays on
    if (open && typeof MusicYouTube !== "undefined") MusicYouTube.openPanel();
    moveTo(false);
  }

  /* ------------------------------------------------------------ dragging */

  function onDown(e) {
    const head = e.target.closest?.("[data-ytv-grip]");
    if (!head || !st.floating || e.button !== 0 || e.target.closest("button")) return;
    const box = el.getBoundingClientRect();
    e.preventDefault();
    drag = { id: e.pointerId, dx: e.clientX - box.left, dy: e.clientY - box.top };
    el.setPointerCapture(e.pointerId);
    el.classList.add("is-dragging");
  }
  function onMove(e) { if (drag && e.pointerId === drag.id) place(e.clientX - drag.dx, e.clientY - drag.dy); }
  function onUp(e) {
    if (!drag || e.pointerId !== drag.id) return;
    drag = null;
    el.classList.remove("is-dragging");
    save();
  }
  // Arrow keys move it too, from its grip, for those not dragging.
  function onKey(e) {
    if (!st.floating || !e.target.closest?.("[data-ytv-grip]") || e.target.closest("button")) return;
    const step = e.shiftKey ? 48 : 12, d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (!d) return;
    e.preventDefault();
    place(st.x + d[0], st.y + d[1]);
    save();
  }
  function onClick(e) {
    const t = e.target.closest?.("[data-ytv]");
    if (!t || t.disabled) return;
    const what = t.dataset.ytv;
    if (what === "start" && !video) return typeof MusicYouTube !== "undefined" && MusicYouTube.openPanel();
    if (what === "play" || what === "start") return toggle();
    if (what === "close") return stop();
    if (what === "move") return st.floating ? dock() : float();
  }

  /* ------------------------------------------------------------ mount */

  function mount(dockSlot) {
    slot = dockSlot;
    build();
    (st.floating ? document.body : slot).append(el);
    el.querySelector(".ytv-head").tabIndex = 0;
    el.querySelector(".ytv-head").setAttribute("aria-label", "YouTube TV. Drag it, or move it with the arrow keys when it floats");
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);
    el.addEventListener("click", onClick);
    el.addEventListener("keydown", onKey);
    addEventListener("message", onMessage);
    addEventListener("resize", () => { if (st.floating) place(st.x, st.y); });
    // Out of sight (the panel hidden, Spotify chosen, the set closed): the video pauses.
    new ResizeObserver(() => {
      if (st.floating && !drag) place(st.x, st.y);
      if (!onScreen() && playing()) { command("pauseVideo"); info.state = 2; paint(); }
      if (onScreen()) poster();
    }).observe(el);
    paint();
    if (st.floating) requestAnimationFrame(() => place(st.x, st.y));
  }

  return {
    mount, play, cue, toggle, stop, float, dock,
    isFloating: () => st.floating,
    playing, loaded: () => !!frame, ended: () => !!frame && info.state === 0,
    video: () => (video ? { ...video, time: info.time } : null),
    onChange: (fn) => listeners.add(fn),
  };
})();
