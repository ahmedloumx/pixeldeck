"use strict";

/* Retro pickers: a pixel button and list in place of a native select. The select stays in
   the page as the source of truth (hidden, still labelled), so the code that fills it and
   listens to its change event is untouched: picking here sets its value and fires change.
   Keys: arrows, Home and End move, Enter or Space pick, Escape closes, letters jump. */

const RetroSelect = (() => {
  let open = null; // { select, button, pop, items, index }

  function label(select) {
    const opt = select.options[select.selectedIndex];
    return opt ? opt.textContent : "";
  }
  function enhance(select) {
    if (!select || select._retro) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `rs-button ${select.dataset.retroClass || ""}`.trim();
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    const name = select.closest("label")?.querySelector(".visually-hidden")?.textContent || select.getAttribute("aria-label") || "";
    if (name) button.setAttribute("aria-label", name);
    button.innerHTML = `<span class="rs-value"></span><span class="rs-arrow" aria-hidden="true"></span>`;
    select.classList.add("rs-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    select.after(button);
    select._retro = button;
    button._select = select;
    sync(select);
    new MutationObserver(() => sync(select)).observe(select, { childList: true, subtree: true, attributes: true });
    select.addEventListener("change", () => sync(select));
    button.addEventListener("click", () => (open?.select === select ? close() : show(select)));
    button.addEventListener("keydown", (e) => {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key) && open?.select !== select) { e.preventDefault(); show(select); }
    });
  }
  function sync(select) {
    const b = select?._retro;
    if (!b) return;
    const text = label(select);
    const v = b.querySelector(".rs-value");
    if (v.textContent !== text) v.textContent = text;
    b.title = text;
  }
  function show(select) {
    close();
    const button = select._retro;
    const pop = document.createElement("div");
    pop.className = "rs-pop";
    pop.setAttribute("role", "listbox");
    pop.tabIndex = -1;
    const items = [];
    let html = "";
    for (const child of select.children) {
      if (child.tagName === "OPTGROUP") {
        html += `<div class="rs-group" role="presentation">${esc(child.label)}</div>`;
        for (const opt of child.children) { html += item(opt, items.length); items.push(opt); }
      } else { html += item(child, items.length); items.push(child); }
    }
    pop.innerHTML = html;
    document.body.append(pop);
    const r = button.getBoundingClientRect();
    pop.style.minWidth = `${Math.round(r.width)}px`;
    pop.style.left = `${Math.round(Math.min(r.left, innerWidth - pop.offsetWidth - 8))}px`;
    const below = innerHeight - r.bottom - 12;
    if (below < 180 && r.top > below) { pop.style.bottom = `${Math.round(innerHeight - r.top + 4)}px`; pop.style.maxHeight = `${Math.round(r.top - 12)}px`; }
    else { pop.style.top = `${Math.round(r.bottom + 4)}px`; pop.style.maxHeight = `${Math.round(below)}px`; }
    open = { select, button, pop, items, index: Math.max(0, select.selectedIndex) };
    button.setAttribute("aria-expanded", "true");
    pop.addEventListener("click", (e) => { const el = e.target.closest("[data-rs]"); if (el) choose(Number(el.dataset.rs)); });
    pop.addEventListener("pointermove", (e) => { const el = e.target.closest("[data-rs]"); if (el) mark(Number(el.dataset.rs), false); });
    pop.addEventListener("keydown", onKey);
    mark(open.index, true);
    pop.focus({ preventScroll: true });
  }
  function item(opt, i) {
    return `<div class="rs-item${opt.selected ? " is-selected" : ""}" role="option" id="rs-${i}" data-rs="${i}" aria-selected="${opt.selected}" style="--k:${Math.min(i, 14)}">${esc(opt.textContent)}</div>`;
  }
  function mark(i, scroll) {
    if (!open) return;
    open.index = Math.max(0, Math.min(open.items.length - 1, i));
    for (const el of open.pop.querySelectorAll(".rs-item")) el.classList.toggle("is-active", Number(el.dataset.rs) === open.index);
    const el = open.pop.querySelector(`[data-rs="${open.index}"]`);
    open.pop.setAttribute("aria-activedescendant", el?.id || "");
    if (scroll) el?.scrollIntoView({ block: "nearest" });
  }
  function choose(i) {
    if (!open) return;
    const { select, button, items } = open;
    const opt = items[i];
    close();
    button.focus({ preventScroll: true });
    if (!opt || opt.value === select.value) return;
    select.value = opt.value;
    sync(select);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }
  let typed = "", typedAt = 0;
  function onKey(e) {
    if (!open) return;
    const k = e.key;
    if (k === "Escape" || k === "Tab") { e.preventDefault(); const b = open.button; close(); b.focus(); return; }
    if (k === "ArrowDown") { e.preventDefault(); mark(open.index + 1, true); }
    else if (k === "ArrowUp") { e.preventDefault(); mark(open.index - 1, true); }
    else if (k === "Home") { e.preventDefault(); mark(0, true); }
    else if (k === "End") { e.preventDefault(); mark(open.items.length - 1, true); }
    else if (k === "Enter" || k === " ") { e.preventDefault(); choose(open.index); }
    else if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      typed = performance.now() - typedAt > 700 ? k.toLowerCase() : typed + k.toLowerCase();
      typedAt = performance.now();
      const hit = open.items.findIndex((o) => o.textContent.toLowerCase().startsWith(typed));
      if (hit >= 0) mark(hit, true);
    }
    e.stopPropagation();  // the app's own single-key shortcuts must not fire while picking
  }
  function close() {
    if (!open) return;
    open.pop.remove();
    open.button.setAttribute("aria-expanded", "false");
    open = null;
  }
  document.addEventListener("pointerdown", (e) => { if (open && !open.pop.contains(e.target) && e.target !== open.button && !open.button.contains(e.target)) close(); }, true);
  addEventListener("resize", close);
  addEventListener("scroll", (e) => { if (open && !open.pop.contains(e.target)) close(); }, true);
  const enhanceAll = (root = document) => { for (const s of root.querySelectorAll("select[data-retro]")) enhance(s); };
  return { enhance, enhanceAll, sync, close };
})();
