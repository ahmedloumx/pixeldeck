"use strict";

/* Where the music plays. Spotify names every device it knows and says what kind it is
   (Computer, Smartphone, Speaker, TV...). The deck's PLAY ON button shows the one playing,
   with a pixel picture of its kind, and opens the list of all of them; picking one moves the
   music there, and a light flies from the old device to the new one to show it. */

const MusicDevices = (() => {
  // 12 or 16 pixels wide, # is lit. A kind Spotify adds later falls back to the computer.
  const PICS = {
    computer: ["................", ".##############.", ".#............#.", ".#.##########.#.", ".#.#........#.#.", ".#.#........#.#.", ".#.#........#.#.", ".#.##########.#.", ".#............#.", ".##############.", "#..............#", "################"],
    phone: ["..########..", ".##########.", ".#........#.", ".#........#.", ".#........#.", ".#........#.", ".#........#.", ".#........#.", ".#........#.", ".#........#.", ".#........#.", ".#........#.", ".##########.", ".####..####.", ".##########.", "..########.."],
    speaker: [".##########.", "##########.#", "##..####...#", "#.##.##.##.#", "#.##.##.##.#", "##..####...#", "############", "####....####", "###.####.###", "##.##..##.##", "##.#....#.##", "##.##..##.##", "###.####.###", "####....####", "############", ".##########."],
    tv: ["................", "....#......#....", ".....#....#.....", "######..########", "#..............#", "#.############.#", "#.#..........#.#", "#.#..........#.#", "#.#..........#.#", "#.############.#", "#..............#", "################"],
    car: ["................", "....########....", "...#..#..#..#...", "..#...#..#...#..", ".##############.", "#..............#", "#.##........##.#", "#.##........##.#", "################", ".###........###.", ".###........###.", "................"],
    console: ["................", "................", "..####....####..", ".#....####....#.", "#..#........#..#", "#.###......#.#.#", "#..#........#..#", "#......##......#", "#....#....#....#", ".#..#......#..#.", "..##........##..", "................"],
  };
  const KIND = {
    computer: "computer", smartphone: "phone", tablet: "phone", speaker: "speaker", avr: "speaker", castaudio: "speaker", audiodongle: "speaker",
    tv: "tv", stb: "tv", castvideo: "tv", automobile: "car", gameconsole: "console",
  };
  const WORD = { computer: "Computer", phone: "Phone", speaker: "Speaker", tv: "TV", car: "Car", console: "Console" };
  const picOf = (type) => KIND[String(type || "").toLowerCase()] || "computer";
  function icon(type, cls = "md-pic") {
    const rows = PICS[picOf(type)], w = rows[0].length, h = rows.length;
    let r = "";
    rows.forEach((row, y) => [...row].forEach((c, x) => { if (c === "#") r += `<rect x="${x}" y="${y}" width="1" height="1"/>`; }));
    return `<svg class="${cls}" viewBox="0 0 ${w} ${h}" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true">${r}</svg>`;
  }
  const kindWord = (type) => WORD[picOf(type)];

  let pop = null, anchor = null, onPick = null, last = null;

  function button(btn, { list, current, chosen, playing }) {
    if (!btn) return;
    const shown = current || list.find((d) => d.id === chosen) || list.find((d) => d.active) || null;
    btn.disabled = !list.length && !current;
    btn.classList.toggle("is-live", !!(current && playing));
    const html = shown
      ? `${icon(shown.type)}<span class="md-btn-text"><span class="md-btn-name">${esc(shown.name)}</span><span class="md-btn-kind num8">${current && playing ? "PLAYING" : current ? "PAUSED" : "READY"}</span></span>
         <span class="md-eq" aria-hidden="true"><i></i><i></i><i></i></span><span class="md-caret" aria-hidden="true">▾</span>`
      : `<span class="md-btn-text"><span class="md-btn-name">No device</span><span class="md-btn-kind num8">OPEN SPOTIFY SOMEWHERE</span></span>`;
    if (btn._html !== html) { btn.innerHTML = html; btn._html = html; }  // a rewrite would restart the bars
    btn.setAttribute("aria-label", shown ? `Play on: ${shown.name}${current && playing ? ", playing" : ""}. Choose another device` : "No device: open Spotify somewhere");
    last = { list, current, chosen, playing };
    if (pop) paintList();
  }

  function paintList() {
    const { list, current, playing } = last || { list: [] };
    const all = [...list];
    if (current?.id && !all.some((d) => d.id === current.id)) all.unshift(current);
    pop.innerHTML = all.length ? all.map((d) => {
      const on = current?.id === d.id;
      const vol = on && d.volume != null ? `<span class="md-vol" aria-hidden="true"><i style="width:${Math.max(0, Math.min(100, d.volume))}%"></i></span>` : "";
      return `<button class="md-card${on ? " is-on" : ""}" type="button" role="option" aria-selected="${on}" data-md-pick="${esc(d.id || "")}"${d.restricted || !d.id ? " disabled" : ""}>
        ${icon(d.type)}<span class="md-card-text"><span class="md-card-name">${esc(d.name)}</span><span class="md-card-kind num8">${on ? (playing ? "PLAYING HERE" : "PAUSED HERE") : esc(kindWord(d.type).toUpperCase())}${d.restricted ? " · LOCKED" : ""}</span></span>
        ${on && playing ? '<span class="md-eq" aria-hidden="true"><i></i><i></i><i></i></span>' : ""}${vol}</button>`;
    }).join("") : '<p class="md-none">Spotify knows no device right now. Open Spotify on this computer or your phone, then look again.</p>';
  }

  function open(btn, pick) {
    if (pop) return close();
    anchor = btn; onPick = pick;
    pop = document.createElement("div");
    pop.className = "md-pop";
    pop.setAttribute("role", "listbox");
    pop.setAttribute("aria-label", "Play on");
    document.body.append(pop);
    paintList();
    const r = btn.getBoundingClientRect();
    const w = Math.max(r.width, 260);
    pop.style.width = `${w}px`;
    pop.style.left = `${Math.max(8, Math.min(r.right - w, innerWidth - w - 8))}px`;
    const below = innerHeight - r.bottom, h = pop.offsetHeight;
    pop.style.top = `${below > h + 12 || r.top < h + 12 ? r.bottom + 6 : r.top - h - 6}px`;
    btn.setAttribute("aria-expanded", "true");
    (pop.querySelector(".md-card.is-on") || pop.querySelector(".md-card:not(:disabled)"))?.focus();
  }
  function close() {
    pop?.remove();
    pop = null;
    anchor?.setAttribute("aria-expanded", "false");
  }

  // A light flies from the device that played to the one picked: the music moving over.
  function fly(fromEl, toEl) {
    if (!fromEl || !toEl || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const a = fromEl.getBoundingClientRect(), b = toEl.getBoundingClientRect();
    for (let k = 0; k < 6; k++) {
      const dot = document.createElement("i");
      dot.className = "md-fly";
      dot.style.left = `${a.left + a.width / 2}px`;
      dot.style.top = `${a.top + a.height / 2}px`;
      document.body.append(dot);
      dot.animate([{ transform: "translate(0, 0)", opacity: 1 }, { transform: `translate(${b.left + b.width / 2 - a.left - a.width / 2}px, ${b.top + b.height / 2 - a.top - a.height / 2}px)`, opacity: 0.2 }],
        { duration: 440, delay: k * 50, easing: "steps(8, end)", fill: "forwards" }).onfinish = () => dot.remove();
    }
  }

  document.addEventListener("click", (e) => {
    const pick = e.target.closest?.("[data-md-pick]");
    if (pick && pop?.contains(pick)) {
      const from = pop.querySelector(".md-card.is-on");
      if (from && from !== pick) fly(from, anchor);
      const id = pick.dataset.mdPick, fn = onPick;
      close();
      if (id && fn) fn(id);
      return;
    }
    if (pop && !pop.contains(e.target) && !anchor?.contains(e.target)) close();
  });
  document.addEventListener("keydown", (e) => {
    if (!pop) return;
    if (e.key === "Escape") { e.preventDefault(); close(); anchor?.focus(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const cards = [...pop.querySelectorAll(".md-card:not(:disabled)")];
      if (!cards.length) return;
      const at = cards.indexOf(document.activeElement);
      cards[(at + (e.key === "ArrowDown" ? 1 : cards.length - 1)) % cards.length].focus();
    }
  });

  return { icon, kindWord, button, open, close, isOpen: () => !!pop };
})();
