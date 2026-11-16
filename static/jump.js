"use strict";

/* Jump (Ctrl K): one box for any pull request PixelDeck has loaded (by number, title, ticket,
   branch, repository or author), any person, and any menu command. Every word typed must
   match. A leading # keeps pull requests only, @ people only, > commands only. */

const Jump = (() => {
  let dialog = null, rows = [], at = 0;
  const LIMIT = { pr: 8, person: 6, cmd: 8 };

  function prs() {
    const seen = new Map();
    const add = (list) => { for (const pr of list || []) { const k = prKey(pr); if (!seen.has(k)) seen.set(k, pr); } };
    for (const person of Object.values(state.people || {})) { add(person?.open); add(person?.merged); }
    add(state.teamData?.open);
    return [...seen.values()];
  }
  function people() {
    return [...(state.me ? [{ who: "me", name: `${state.me.name} (you)` }] : []), { who: "team", name: "The whole team" },
      ...(state.team || []).map((m) => ({ who: m.uuid, name: m.name }))];
  }
  const words = (q) => q.toLowerCase().split(/\s+/).filter(Boolean);
  const hits = (text, ws) => ws.every((w) => text.includes(w));

  function search(raw) {
    const q = raw.trim();
    const only = q[0] === "#" ? "pr" : q[0] === "@" ? "person" : q[0] === ">" ? "cmd" : null;
    const ws = words(only ? q.slice(1) : q);
    const out = [];
    if (!only || only === "pr") {
      const num = ws.length === 1 && /^\d+$/.test(ws[0]) ? Number(ws[0]) : null;
      const found = prs().filter((pr) => (num !== null ? pr.id === num || String(pr.id).startsWith(ws[0]) : hits(
        `#${pr.id} ${pr.title} ${pr.repo} ${pr.source} ${pr.ticket?.key || ""} ${pr.author?.name || ""}`.toLowerCase(), ws)));
      found.sort((a, b) => (num !== null ? Number(b.id === num) - Number(a.id === num) : 0) || Number(b.state === "OPEN") - Number(a.state === "OPEN") || (b.updated_on || "").localeCompare(a.updated_on || ""));
      for (const pr of found.slice(0, ws.length || only ? LIMIT.pr : 5)) out.push({ kind: "pr", pr });
    }
    if (!only || only === "person") {
      for (const p of people().filter((x) => hits(x.name.toLowerCase(), ws)).slice(0, LIMIT.person)) out.push({ kind: "person", p });
    }
    if (!only || only === "cmd") {
      const cmds = MenuBar.commands().filter((c) => hits(`${c.menu} ${c.label}`.toLowerCase(), ws));
      for (const c of cmds.slice(0, ws.length || only ? LIMIT.cmd : 4)) out.push({ kind: "cmd", c });
    }
    return out;
  }

  function tag(pr) {
    if (pr.state === "MERGED") return '<span class="jump-tag">MERGED</span>';
    if (pr.state === "DECLINED") return '<span class="jump-tag">DECLINED</span>';
    const s = signals(pr);
    if (s.changes.length) return '<span class="jump-tag is-bad">CHANGES</span>';
    if (pr.behind?.behind) return `<span class="jump-tag is-wait">${esc(pr.behind.behind)} BEHIND</span>`;
    if (s.approved) return '<span class="jump-tag is-ok">APPROVED</span>';
    return '<span class="jump-tag">OPEN</span>';
  }
  function rowHtml(r, i) {
    const cls = `mb-item jump-row${i === at ? " is-at" : ""}`;
    if (r.kind === "pr") {
      return `<button class="${cls}" type="button" data-j="${i}" role="option" aria-selected="${i === at}"><span class="jump-lead num8">#${esc(r.pr.id)}</span>
        <span class="mb-label">${esc(r.pr.title)} <small>${esc(r.pr.repo)}${r.pr.author?.name && !isMeUuid(r.pr.author.uuid) ? `, ${esc(r.pr.author.name)}` : ""}</small></span>${tag(r.pr)}</button>`;
    }
    if (r.kind === "person") return `<button class="${cls}" type="button" data-j="${i}" role="option" aria-selected="${i === at}"><span class="jump-lead">@</span><span class="mb-label">${esc(r.p.name)}</span><span class="mb-key">PULL REQUESTS</span></button>`;
    return `<button class="${cls}" type="button" data-j="${i}" role="option" aria-selected="${i === at}"><span class="jump-lead">&gt;</span><span class="mb-label">${esc(r.c.label)} <small>${esc(r.c.menu)}</small></span><span class="mb-key">${esc(r.c.key)}</span></button>`;
  }
  function paint() {
    const list = dialog.querySelector(".jump-list");
    if (!rows.length) { list.innerHTML = '<p class="jump-none">Nothing matches. # for a pull request, @ for a person, &gt; for a command.</p>'; return; }
    const head = { pr: "Pull requests", person: "People", cmd: "Commands" };
    let last = "";
    list.innerHTML = rows.map((r, i) => { const h = r.kind !== last ? `<p class="mb-group">${head[r.kind]}</p>` : ""; last = r.kind; return h + rowHtml(r, i); }).join("");
    list.querySelector(".is-at")?.scrollIntoView({ block: "nearest" });
  }
  function update() {
    rows = search(dialog.querySelector(".jump-input").value);
    at = 0;
    paint();
  }
  function pick(i) {
    const r = rows[i];
    if (!r) return;
    dialog.close();
    if (r.kind === "pr") openPr(r.pr);
    else if (r.kind === "person") { if (state.pr) closePr(); setWho(r.p.who); Layout.activate("queue"); }
    else { try { r.c.run(); } catch (err) { toast("That did not work.", err.message, true); } }
  }
  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "jump";
    dialog.setAttribute("aria-label", "Jump to");
    dialog.innerHTML = `<label class="jump-field"><span class="jump-prompt" aria-hidden="true">&gt;</span><span class="visually-hidden">Jump to</span>
        <input class="jump-input" type="text" autocomplete="off" spellcheck="false" placeholder="Pull request number, title, ticket, branch, person or command" role="combobox" aria-expanded="true"></label>
      <div class="jump-list" role="listbox"></div>
      <p class="jump-keys"><span>↑↓ MOVE</span><span>ENTER OPEN</span><span># PULL REQUEST</span><span>@ PERSON</span><span>&gt; COMMAND</span><span>ESC CLOSE</span></p>`;
    document.body.append(dialog);
    const input = dialog.querySelector(".jump-input");
    input.addEventListener("input", update);
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (rows.length) { at = (at + (e.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length; paint(); } }
      else if (e.key === "Enter") { e.preventDefault(); pick(at); }
    });
    dialog.addEventListener("click", (e) => {
      const row = e.target.closest("[data-j]");
      if (row) return pick(Number(row.dataset.j));
      if (e.target === dialog) dialog.close();
    });
  }
  function open(prefix = "") {
    if (!dialog) build();
    MenuBar.close();
    const input = dialog.querySelector(".jump-input");
    input.value = prefix;
    if (!dialog.open) dialog.showModal();
    update();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
  // Ctrl K anywhere but a terminal: there it deletes to the end of the line, and stays the shell's.
  function mount() {
    document.addEventListener("keydown", (e) => {
      if (document.activeElement?.closest?.(".xterm")) return;
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault(); e.stopPropagation();
        if (dialog?.open) dialog.close(); else open();
      }
    }, true);
    document.getElementById("jump-btn")?.addEventListener("click", () => open());
  }
  return { mount, open, search };
})();
