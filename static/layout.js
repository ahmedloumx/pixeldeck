"use strict";

/* The workspace: four areas (left, main, right, bottom), each holding panels as tabs.
   Drag a tab to move it, drag a divider to resize, collapse an area to a strip,
   hide a panel, split it out, or open it in its own window. Remembered per browser. */

const Layout = (() => {
  const KEY = "prdesk.layout.v2";
  const SLOTS = ["left", "main", "right", "bottom"];
  const PRESETS = {
    split: {
      slots: { left: ["queue"], main: ["pr", "insights"], right: [], bottom: ["terminal"] },
      active: { left: "queue", main: "insights", right: "", bottom: "terminal" },
      size: { left: 30, right: 30, bottom: 34 }, collapsed: {}, hidden: [],
    },
    compact: {
      slots: { left: [], main: ["queue", "pr", "insights", "terminal"], right: [], bottom: [] },
      active: { left: "", main: "queue", right: "", bottom: "" },
      size: { left: 30, right: 30, bottom: 34 }, collapsed: {}, hidden: [],
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
  };
  const icon = (n) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[n]}</svg>`;
  const clone = (o) => JSON.parse(JSON.stringify(o));

  let state, root, opts, solo = null;
  const panels = {};
  const badges = {};

  function load() {
    try {
      const s = JSON.parse(localStorage.getItem(KEY));
      if (s && s.slots) return s;
    } catch { /* storage blocked */ }
    return clone(PRESETS.split);
  }
  function save() {
    if (solo) return;
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* storage blocked */ }
  }

  function normalize() {
    const ids = Object.keys(panels);
    const seen = new Set();
    for (const slot of SLOTS) {
      state.slots[slot] = (state.slots[slot] || []).filter((id) => ids.includes(id) && !seen.has(id) && seen.add(id));
    }
    state.hidden = (state.hidden || []).filter((id) => ids.includes(id) && !seen.has(id) && seen.add(id));
    for (const id of ids) if (!seen.has(id)) state.slots.main.push(id);
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
    for (const p of options.panels) {
      const el = document.createElement("div");
      el.className = "panel-body";
      el.dataset.panel = p.id;
      panels[p.id] = { ...p, el };
    }
    solo = new URLSearchParams(location.search).get("panel");
    if (solo && panels[solo]) {
      state = clone(PRESETS.compact);
      state.slots.main = [solo];
      state.hidden = Object.keys(panels).filter((id) => id !== solo);
      state.active.main = solo;
      document.body.classList.add("is-solo");
    } else {
      solo = null;
      state = load();
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
    root.addEventListener("click", onClick);
    root.addEventListener("dragstart", onDragStart);
    root.addEventListener("dragend", () => root.classList.remove("is-dragging"));
    root.addEventListener("dragover", onDragOver);
    root.addEventListener("drop", onDrop);
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
        <button class="slot-tab" type="button" role="tab" draggable="true" data-tab="${id}" aria-selected="${state.active[slot] === id}">
          <span>${esc(panels[id].title)}</span>${badges[id] ? `<span class="slot-badge">${esc(badges[id])}</span>` : ""}
        </button>`).join("");
      const tool = (act, ic, label) => `<button class="slot-tool" type="button" data-act="${act}" data-area="${slot}" title="${label}" aria-label="${label}">${icon(ic)}</button>`;
      const active = state.active[slot];
      const tools = [];
      if (!solo && active) {
        if (slot === "main") { tools.push(tool("split-right", "right", "Split this panel to the right"), tool("split-down", "down", "Split this panel to the bottom")); }
        else { tools.push(tool("to-tabs", "tabs", "Put this panel back in the main tabs")); }
        tools.push(tool("window", "window", "Open this panel in its own window"));
        if (slot !== "main") tools.push(tool("collapse", state.collapsed[slot] ? "expand" : "collapse", state.collapsed[slot] ? "Expand this area" : "Collapse this area"));
        tools.push(tool("hide", "hide", "Hide this panel"));
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
    else if (act === "hide") hide(id);
    else if (act === "collapse") { state.collapsed[slot] = !state.collapsed[slot]; save(); paint(); }
  }

  function onDragStart(e) {
    const tab = e.target.closest(".slot-tab");
    if (!tab) return;
    e.dataTransfer.setData("text/x-panel", tab.dataset.tab);
    e.dataTransfer.effectAllowed = "move";
    root.classList.add("is-dragging");
  }
  function dropTarget(e) {
    const zone = e.target.closest(".dropzone");
    if (zone) return { slot: zone.dataset.zone, before: null };
    const slotEl = e.target.closest(".slot");
    if (!slotEl) return null;
    const tab = e.target.closest(".slot-tab");
    return { slot: slotEl.dataset.area, before: tab ? tab.dataset.tab : null };
  }
  function onDragOver(e) {
    if (!e.dataTransfer.types.includes("text/x-panel")) return;
    if (dropTarget(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }
  }
  function onDrop(e) {
    const id = e.dataTransfer.getData("text/x-panel");
    const target = dropTarget(e);
    root.classList.remove("is-dragging");
    if (!id || !target || !panels[id]) return;
    e.preventDefault();
    if (target.before === id) return;
    move(id, target.slot, target.before);
  }

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
  function preset(name) {
    state = clone(PRESETS[name] || PRESETS.split);
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
  const visibility = () => Object.keys(panels).map((id) => ({ id, title: panels[id].title, hidden: state.hidden.includes(id) }));

  return { init, body, activate, hide, show, preset, popout, setBadge, isShown, visibility, isSolo: () => !!solo };
})();
