"use strict";

/* The workspace: four areas (left, main, right, bottom), each holding panels as tabs.
   Drag a tab to move it, drag a divider to resize, collapse an area to a strip,
   hide a panel, split it out, or open it in its own window. Remembered per browser.
   Some panels can be copied: a copy is a panel of its own (id "queue~2") with its own
   view settings, so the same panel can show two people or two ranges side by side. */

const Layout = (() => {
  const KEY = "prdesk.layout.v2";
  const SLOTS = ["left", "main", "right", "bottom"];
  const PRESETS = {
    split: {
      slots: { left: ["repos"], main: ["queue", "insights"], right: [], bottom: ["terminal"] },
      active: { left: "repos", main: "queue", right: "", bottom: "terminal" },
      size: { left: 30, right: 30, bottom: 34 }, collapsed: {}, hidden: ["music"],
    },
    compact: {
      slots: { left: [], main: ["queue", "repos", "insights", "terminal"], right: [], bottom: [] },
      active: { left: "", main: "queue", right: "", bottom: "" },
      size: { left: 30, right: 30, bottom: 34 }, collapsed: {}, hidden: ["music"],
    },
  };
  const ICON = {
    right: '<rect x="3.5" y="4.5" width="17" height="15" rx="1"/><path d="M12 4.5v15"/>',
    down: '<rect x="3.5" y="4.5" width="17" height="15" rx="1"/><path d="M3.5 12h17"/>',
    tabs: '<rect x="3.5" y="7.5" width="17" height="12" rx="1"/><path d="M3.5 7.5V5h6v2.5"/>',
    window: '<rect x="7.5" y="3.5" width="13" height="11" rx="1"/><path d="M16.5 14.5v5h-13v-11h4"/>',
    collapse: '<path d="M6 12h12"/>',
    expand: '<path d="M6 12h12M12 6v12"/>',
    hide: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="1"/><path d="M15.5 8.5v-4h-11v11h4"/>',
  };
  // A pixel glyph for each panel's tab, 9 by 9: # is a lit pixel. A copy wears its base's glyph.
  const GLYPH = {
    queue: [".#.....#.", "###...###", ".#.....#.", ".#....#..", ".#...#...", ".#..#....", ".#.#.....", "###......", ".#......."],
    repos: [".........", ".###.....", "#...####.", "#########", "#.......#", "#.......#", "#.......#", "#########", "........."],
    insights: [".......##", ".......##", "....##.##", "....##.##", ".##.##.##", ".##.##.##", ".##.##.##", ".........", "#########"],
    terminal: ["#########", "#.......#", "#.#.....#", "#..#....#", "#.#..##.#", "#.......#", "#########", ".........", "........."],
    music: ["...######", "...######", "...#....#", "...#....#", "...#....#", ".###..###", "####.####", ".##...##.", "........."],
  };
  const glyph = (id) => {
    const rows = GLYPH[panels[id]?.base] || GLYPH[id];
    if (!rows) return "";
    let r = "";
    rows.forEach((row, y) => [...row].forEach((c, x) => { if (c === "#") r += `<rect x="${x}" y="${y}" width="1" height="1"/>`; }));
    return `<svg class="tab-glyph" viewBox="0 0 9 9" width="11" height="11" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true">${r}</svg>`;
  };
  const icon = (n) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[n]}</svg>`;
  const clone = (o) => JSON.parse(JSON.stringify(o));

  let state, root, opts, solo = null;
  const panels = {};
  const badges = {};

  // The pull request panel became part of the queue. A layout saved with it gives the queue
  // the place the pull request had: that was the room a pull request was read in.
  function migrate(s) {
    const where = SLOTS.find((slot) => (s.slots[slot] || []).includes("pr"));
    if (!where) return s;
    const list = s.slots[where];
    for (const slot of SLOTS) if (slot !== where) s.slots[slot] = (s.slots[slot] || []).filter((id) => id !== "queue");
    s.hidden = (s.hidden || []).filter((id) => id !== "queue");
    if (list.includes("queue")) list.splice(list.indexOf("pr"), 1);
    else list.splice(list.indexOf("pr"), 1, "queue");
    if (s.active && s.active[where] === "pr") s.active[where] = "queue";
    return s;
  }
  function load() {
    try {
      const s = JSON.parse(localStorage.getItem(KEY));
      if (s && s.slots) return migrate(s);
    } catch { /* storage blocked */ }
    return clone(PRESETS.split);
  }
  function save() {
    if (solo) return;
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* storage blocked */ }
  }

  // A copy keeps its base panel's look and its own settings (params), saved with the layout.
  function register(id, title, base = id) {
    if (panels[id]) return panels[id];
    const el = document.createElement("div");
    el.className = "panel-body";
    el.dataset.panel = id;
    el.dataset.base = base;
    panels[id] = { ...(panels[base] || {}), id, title, base, el };
    return panels[id];
  }
  const copyTitle = (base, n) => `${panels[base].title} ${n}`;

  function normalize() {
    state.copies = state.copies || {};
    for (const [id, c] of Object.entries(state.copies)) {
      if (!panels[c.base] || panels[c.base].base !== c.base) { delete state.copies[id]; continue; }
      register(id, copyTitle(c.base, c.n), c.base);
    }
    const ids = Object.keys(panels).filter((id) => panels[id].base === id || state.copies[id]);
    const seen = new Set();
    for (const slot of SLOTS) {
      state.slots[slot] = (state.slots[slot] || []).filter((id) => ids.includes(id) && !seen.has(id) && seen.add(id));
    }
    state.hidden = (state.hidden || []).filter((id) => ids.includes(id) && !seen.has(id) && seen.add(id));
    // A panel new to a saved layout goes where it asks (place: an area, or "hidden").
    for (const id of ids) {
      if (seen.has(id)) continue;
      const place = panels[id].place;
      if (place === "hidden") state.hidden.push(id);
      else state.slots[SLOTS.includes(place) ? place : "main"].push(id);
    }
    state.active = state.active || {};
    state.collapsed = state.collapsed || {};
    state.size = { ...PRESETS.split.size, ...(state.size || {}) };
    for (const slot of SLOTS) {
      if (!state.slots[slot].includes(state.active[slot])) state.active[slot] = state.slots[slot][0] || "";
    }
    if (!state.slots.main.length) {
      // the main area is never empty: borrow from the first area that has panels
      const donor = SLOTS.find((s) => state.slots[s].length);
      if (donor) { state.slots.main.push(state.slots[donor].shift()); normalize(); }
    }
  }

  function init(options) {
    opts = options;
    root = document.getElementById("workspace");
    for (const p of options.panels) panels[p.id] = { ...register(p.id, p.title), ...p, base: p.id };
    solo = new URLSearchParams(location.search).get("panel");
    const saved = load();
    // A copy opened in its own window is only known to the saved layout of the main one.
    const soloCopy = solo && saved.copies?.[solo];
    if (soloCopy && panels[soloCopy.base]) register(solo, copyTitle(soloCopy.base, soloCopy.n), soloCopy.base);
    if (solo && panels[solo]) {
      state = clone(PRESETS.compact);
      state.copies = soloCopy ? { [solo]: soloCopy } : {};
      state.slots.main = [solo];
      state.hidden = Object.keys(panels).filter((id) => id !== solo && panels[id].base === id);
      state.active.main = solo;
      document.body.classList.add("is-solo");
    } else {
      solo = null;
      state = saved;
    }
    normalize();
    build();
    paint();
  }

  function build() {
    root.innerHTML = SLOTS.map((slot) => `
      <section class="slot" data-area="${slot}">
        <header class="slot-head"><div class="slot-tabs" role="tablist" aria-label="${slot} panels"></div><div class="slot-tools"></div></header>
        <div class="slot-body"></div>
      </section>`).join("") +
      `<div class="splitter" data-split="left" role="separator" aria-orientation="vertical" aria-label="Resize the left area" tabindex="0"></div>
       <div class="splitter" data-split="right" role="separator" aria-orientation="vertical" aria-label="Resize the right area" tabindex="0"></div>
       <div class="splitter" data-split="bottom" role="separator" aria-orientation="horizontal" aria-label="Resize the bottom area" tabindex="0"></div>
       <div class="dropzone" data-zone="left">Left</div><div class="dropzone" data-zone="right">Right</div><div class="dropzone" data-zone="bottom">Bottom</div>`;
    root.addEventListener("click", onClick, true);
    root.addEventListener("pointerdown", onTabDown);
    for (const sp of root.querySelectorAll(".splitter")) {
      sp.addEventListener("pointerdown", onSplitStart);
      sp.addEventListener("keydown", onSplitKey);
    }
  }

  function slotOf(id) { return SLOTS.find((s) => state.slots[s].includes(id)) || null; }

  function paint() {
    const vars = root.style;
    const shown = (slot) => state.slots[slot].length > 0;
    const width = (slot) => (!shown(slot) ? "0px" : state.collapsed[slot] ? "40px" : `${state.size[slot]}%`);
    vars.setProperty("--l", width("left"));
    vars.setProperty("--r", width("right"));
    vars.setProperty("--b", !shown("bottom") ? "0px" : state.collapsed.bottom ? "38px" : `${state.size.bottom}%`);
    vars.setProperty("--sl", shown("left") && !state.collapsed.left ? "6px" : shown("left") ? "2px" : "0px");
    vars.setProperty("--sr", shown("right") && !state.collapsed.right ? "6px" : shown("right") ? "2px" : "0px");
    vars.setProperty("--sb", shown("bottom") && !state.collapsed.bottom ? "6px" : shown("bottom") ? "2px" : "0px");
    for (const slot of SLOTS) {
      const el = root.querySelector(`.slot[data-area="${slot}"]`);
      const ids = state.slots[slot];
      el.hidden = !ids.length;
      el.classList.toggle("is-collapsed", !!state.collapsed[slot] && slot !== "main");
      el.querySelector(".slot-tabs").innerHTML = ids.map((id) => `
        <button class="slot-tab${isCopy(id) ? " is-copy" : ""}" type="button" role="tab" data-tab="${id}" aria-selected="${state.active[slot] === id}">
          ${glyph(id)}<span class="slot-tab-title">${esc(panels[id].label || panels[id].title)}</span>${badges[id] ? `<span class="slot-badge">${esc(badges[id])}</span>` : ""}
        </button>`).join("");
      const tool = (act, ic, label) => `<button class="slot-tool" type="button" data-act="${act}" data-area="${slot}" data-tip="${label}" aria-label="${label}">${icon(ic)}</button>`;
      const active = state.active[slot];
      const tools = [];
      if (!solo && active) {
        if (slot === "main") { tools.push(tool("split-right", "right", "Split this panel to the right"), tool("split-down", "down", "Split this panel to the bottom")); }
        else { tools.push(tool("to-tabs", "tabs", "Put this panel back in the main tabs")); }
        if (canCopy(active)) tools.push(tool("duplicate", "copy", "Copy this panel: the copy keeps its own person and view"));
        tools.push(tool("window", "window", "Open this panel in its own window"));
        if (slot !== "main") tools.push(tool("collapse", state.collapsed[slot] ? "expand" : "collapse", state.collapsed[slot] ? "Expand this area" : "Collapse this area"));
        tools.push(tool("hide", "hide", isCopy(active) ? "Close this copy" : "Hide this panel"));
      }
      el.querySelector(".slot-tools").innerHTML = tools.join("");
      const body = el.querySelector(".slot-body");
      const want = active ? panels[active].el : null;
      if (body.firstElementChild !== want) {
        body.replaceChildren(...(want ? [want] : []));
      }
    }
    if (opts.onChange) requestAnimationFrame(() => opts.onChange());
    if (opts.onPaint) opts.onPaint(state);
  }

  function move(id, slot, before = null) {
    const from = slotOf(id);
    if (from) state.slots[from] = state.slots[from].filter((x) => x !== id);
    state.hidden = state.hidden.filter((x) => x !== id);
    const list = state.slots[slot];
    const at = before && list.includes(before) ? list.indexOf(before) : list.length;
    list.splice(at, 0, id);
    state.active[slot] = id;
    state.collapsed[slot] = false;
    normalize();
    save();
    paint();
  }

  function onClick(e) {
    if (swallow) { e.stopPropagation(); e.preventDefault(); return; }
    const tab = e.target.closest(".slot-tab");
    if (tab) { activate(tab.dataset.tab); return; }
    const tool = e.target.closest(".slot-tool");
    if (!tool) return;
    const slot = tool.dataset.area, id = state.active[slot];
    const act = tool.dataset.act;
    if (act === "split-right") move(id, "right");
    else if (act === "split-down") move(id, "bottom");
    else if (act === "to-tabs") move(id, "main");
    else if (act === "window") popout(id);
    else if (act === "duplicate") duplicate(id);
    else if (act === "hide") { if (isCopy(id)) remove(id); else hide(id); }
    else if (act === "collapse") { state.collapsed[slot] = !state.collapsed[slot]; save(); paint(); }
  }

  /* Tabs move with the pointer, not the browser's drag and drop: WebKitGTK, the Linux
     window, drops custom drag data, so a dragged tab never landed there. Drag along a row to
     reorder; drop on another area's tabs, or on Left, Right or Bottom, to move it there. */
  let swallow = false;
  function dropTarget(x, y, id) {
    const el = document.elementFromPoint(x, y);
    if (!el) return null;
    const zone = el.closest(".dropzone");
    if (zone) return { slot: zone.dataset.zone, before: null };
    const slotEl = el.closest(".slot");
    if (!slotEl || !root.contains(slotEl)) return null;
    const slot = slotEl.dataset.area;
    const tabs = [...slotEl.querySelectorAll(".slot-tab")].filter((t) => t.dataset.tab !== id);
    const after = tabs.find((t) => { const r = t.getBoundingClientRect(); return x < r.left + r.width / 2; });
    return { slot, before: after ? after.dataset.tab : null, mark: after || tabs[tabs.length - 1] || null, end: !after };
  }
  function onTabDown(e) {
    const tab = e.target.closest(".slot-tab");
    if (!tab || e.button !== 0 || solo) return;
    const id = tab.dataset.tab, x0 = e.clientX, y0 = e.clientY;
    let ghost = null, target = null, marked = null;
    const unmark = () => { if (marked) marked.classList.remove("is-drop-before", "is-drop-after"); marked = null; };
    const move = (ev) => {
      if (!ghost && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 6) return;
      if (!ghost) {
        ghost = tab.cloneNode(true);
        ghost.className = "slot-tab tab-ghost";
        ghost.removeAttribute("aria-selected");
        document.body.append(ghost);
        tab.classList.add("is-dragged");
        root.classList.add("is-dragging");
      }
      ghost.style.left = `${ev.clientX + 8}px`;
      ghost.style.top = `${ev.clientY + 8}px`;
      target = dropTarget(ev.clientX, ev.clientY, id);
      unmark();
      if (target?.mark) { marked = target.mark; marked.classList.add(target.end ? "is-drop-after" : "is-drop-before"); }
    };
    const up = () => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", up);
      if (!ghost) return;
      ghost.remove();
      unmark();
      tab.classList.remove("is-dragged");
      root.classList.remove("is-dragging");
      swallow = true;  // the click that ends a drag must not also select the tab
      setTimeout(() => { swallow = false; }, 0);
      if (target && panels[id] && target.before !== id) move_(id, target.slot, target.before);
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
    addEventListener("pointercancel", up);
  }
  const move_ = (id, slot, before) => move(id, slot, before);

  let drag = null;
  function onSplitStart(e) {
    const which = e.currentTarget.dataset.split;
    if (state.collapsed[which]) return;
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    drag = { which, rect: root.getBoundingClientRect() };
    root.classList.add("is-resizing");
    const onMove = (ev) => {
      const r = drag.rect;
      let v;
      if (drag.which === "left") v = ((ev.clientX - r.left) / r.width) * 100;
      else if (drag.which === "right") v = ((r.right - ev.clientX) / r.width) * 100;
      else v = ((r.bottom - ev.clientY) / r.height) * 100;
      state.size[drag.which] = Math.round(Math.min(drag.which === "bottom" ? 80 : 65, Math.max(12, v)) * 10) / 10;
      paintSizes();
    };
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      root.classList.remove("is-resizing");
      drag = null;
      save();
      if (opts.onChange) opts.onChange();
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
  }
  function onSplitKey(e) {
    const which = e.currentTarget.dataset.split;
    const grow = which === "left" ? "ArrowRight" : which === "right" ? "ArrowLeft" : "ArrowUp";
    const shrink = which === "left" ? "ArrowLeft" : which === "right" ? "ArrowRight" : "ArrowDown";
    if (e.key !== grow && e.key !== shrink) return;
    e.preventDefault();
    state.size[which] = Math.min(80, Math.max(12, state.size[which] + (e.key === grow ? 2 : -2)));
    paintSizes();
    save();
    if (opts.onChange) opts.onChange();
  }
  let sizeFrame = 0;
  function paintSizes() {
    cancelAnimationFrame(sizeFrame);
    sizeFrame = requestAnimationFrame(() => {
      const shown = (slot) => state.slots[slot].length > 0;
      if (shown("left") && !state.collapsed.left) root.style.setProperty("--l", `${state.size.left}%`);
      if (shown("right") && !state.collapsed.right) root.style.setProperty("--r", `${state.size.right}%`);
      if (shown("bottom") && !state.collapsed.bottom) root.style.setProperty("--b", `${state.size.bottom}%`);
      if (opts.onChange) opts.onChange();
    });
  }

  function activate(id) {
    if (!panels[id]) return;
    let slot = slotOf(id);
    if (!slot) { move(id, "main"); return; }
    state.active[slot] = id;
    state.collapsed[slot] = false;
    save();
    paint();
  }
  function hide(id) {
    const slot = slotOf(id);
    if (!slot) return;
    if (slot === "main" && state.slots.main.length === 1 && !SLOTS.some((s) => s !== "main" && state.slots[s].length)) return;
    state.slots[slot] = state.slots[slot].filter((x) => x !== id);
    state.hidden.push(id);
    normalize();
    save();
    paint();
  }
  function show(id) { if (state.hidden.includes(id)) move(id, "main"); }

  const isCopy = (id) => !!panels[id] && panels[id].base !== id;
  const canCopy = (id) => !!panels[id] && !!panels[panels[id].base].copyable && !solo;
  // The copy opens beside its source: a panel in the main area gets its copy on the right.
  function duplicate(id, params = null) {
    if (!canCopy(id)) return null;
    const base = panels[id].base;
    const taken = new Set(Object.values(state.copies).filter((c) => c.base === base).map((c) => c.n));
    let n = 2;
    while (taken.has(n)) n++;
    const copyId = `${base}~${n}`;
    state.copies[copyId] = { base, n, params: clone(params || (opts.paramsFor ? opts.paramsFor(id) : {}) || {}) };
    register(copyId, copyTitle(base, n), base);
    const from = slotOf(id);
    move(copyId, from === "main" ? "right" : "main");
    if (opts.onCreate) opts.onCreate(copyId);
    return copyId;
  }
  function remove(id) {
    if (!isCopy(id)) return;
    const slot = slotOf(id);
    if (slot) state.slots[slot] = state.slots[slot].filter((x) => x !== id);
    state.hidden = state.hidden.filter((x) => x !== id);
    delete state.copies[id];
    delete panels[id];
    normalize();
    save();
    paint();
    if (opts.onRemove) opts.onRemove(id);
  }
  const params = (id) => (isCopy(id) ? state.copies[id]?.params || {} : null);
  function setParams(id, patch) {
    if (!isCopy(id)) return;
    state.copies[id].params = { ...state.copies[id].params, ...patch };
    save();
  }
  // A short tab label the page can set (a copy says whose pull requests it shows).
  function setLabel(id, label) {
    if (!panels[id] || panels[id].label === label) return;
    panels[id].label = label;
    for (const t of root?.querySelectorAll(`.slot-tab[data-tab="${id}"] .slot-tab-title`) || []) t.textContent = label || panels[id].title;
  }
  const instances = (base) => Object.keys(panels).filter((id) => panels[id].base === base);
  function preset(name) {
    const copies = state.copies;  // a preset arranges the copies too, it does not close them
    state = clone(PRESETS[name] || PRESETS.split);
    state.copies = copies;
    normalize();
    save();
    paint();
  }
  function popout(id) {
    const url = `${location.pathname}?panel=${encodeURIComponent(id)}${location.hash}`;
    window.open(url, `prdesk-${id}`, "popup=yes,width=1180,height=780");
  }
  function setBadge(id, text) {
    if (badges[id] === text) return;
    badges[id] = text;
    if (!root) return;
    for (const tab of root.querySelectorAll(`.slot-tab[data-tab="${id}"]`)) {
      const b = tab.querySelector(".slot-badge");
      if (text) { if (b) b.textContent = text; else tab.insertAdjacentHTML("beforeend", `<span class="slot-badge">${esc(text)}</span>`); }
      else if (b) b.remove();
    }
  }
  const body = (id) => panels[id]?.el;
  const isShown = (id) => { const s = slotOf(id); return !!s && state.active[s] === id && !state.collapsed[s]; };
  const visibility = () => Object.keys(panels).map((id) => ({ id, title: panels[id].label || panels[id].title, hidden: state.hidden.includes(id), copy: isCopy(id), copyable: canCopy(id), base: panels[id].base }));

  return { init, body, activate, hide, show, preset, popout, setBadge, isShown, visibility, isSolo: () => !!solo,
    duplicate, remove, params, setParams, setLabel, isCopy, instances, baseOf: (id) => panels[id]?.base };
})();
