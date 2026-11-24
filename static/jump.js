"use strict";

/* Jump (Ctrl K): one box for anything PixelDeck knows. Pull requests (by number, title,
   ticket, branch, repository or author), terminals, repositories, people, menu commands, and
   music from Spotify (songs, albums, artists, playlists) while Music is connected.

   Every word typed must be found; a word found at the start of a name ranks above one found
   inside it, and a pull request number typed whole goes first. A leading # keeps pull
   requests only, @ people, > commands, $ terminals, ! music. Empty, it lists the pull
   requests opened lately and the terminals. */

const Jump = (() => {
  const LIMIT = { pr: 7, term: 5, repo: 4, person: 5, cmd: 6, music: 8 };
  const ONLY = { "#": "pr", "@": "person", ">": "cmd", $: "term", "!": "music" };
  const HEAD = { pr: "Pull requests", term: "Terminals", repo: "Repositories", person: "People", cmd: "Commands", music: "Music on Spotify" };
  const MUSIC_WAIT = 260;   // ms after the last key before Spotify is asked
  let dialog = null, rows = [], at = 0, index = null, musicTimer = 0, musicFor = "", music = [];

  /* ------------------------------------------------------------ what can be found */

  // Built once per opening: the lists do not change under the box, and typing stays quick.
  function buildIndex() {
    const seen = new Map();
    const add = (list) => { for (const pr of list || []) { const k = prKey(pr); if (!seen.has(k)) seen.set(k, pr); } };
    for (const person of Object.values(state.people || {})) { add(person?.open); add(person?.merged); add(person?.declined); }
    add(state.teamData?.open);
    const prs = [...seen.values()].map((pr) => ({ kind: "pr", pr, id: pr.id,
      fields: [pr.title, pr.repo, pr.source, pr.ticket?.key, pr.author?.name] }));
    const terms = typeof Term !== "undefined" && Term.list ? Term.list().map((t) => ({ kind: "term", t, fields: [t.label, t.title] })) : [];
    const repos = typeof Repos !== "undefined" && Repos.list ? Repos.list().map((r) => ({ kind: "repo", r, fields: [r.name, r.slug] })) : [];
    const people = [...(state.me ? [{ who: "me", name: `${state.me.name} (you)` }] : []), { who: "team", name: "The whole team" },
      ...(state.team || []).map((m) => ({ who: m.uuid, name: m.name }))].map((p) => ({ kind: "person", p, fields: [p.name] }));
    const cmds = MenuBar.commands().map((c) => ({ kind: "cmd", c, fields: [c.label, c.menu] }));
    for (const list of [prs, terms, repos, people, cmds]) {
      for (const e of list) e.text = e.fields.filter(Boolean).map((f) => String(f).toLowerCase());
    }
    return { prs, terms, repos, people, cmds };
  }

  const wordsOf = (q) => q.toLowerCase().split(/\s+/).filter(Boolean);
  // How well the words fit: null when one is missing; 3 for a word at the start of a name or
  // of a word in it, 1 for a word found inside; the first field (the name) counts double.
  function score(entry, ws) {
    let total = 0;
    for (const w of ws) {
      let best = 0;
      entry.text.forEach((t, i) => {
        const at = t.indexOf(w);
        if (at < 0) return;
        const start = at === 0 || /[\s/_.:#-]/.test(t[at - 1]);
        best = Math.max(best, (start ? 3 : 1) * (i === 0 ? 2 : 1));
      });
      if (!best) return null;
      total += best;
    }
    return total;
  }
  function ranked(list, ws, limit, tie) {
    if (!ws.length) return [];
    return list.map((e) => ({ e, s: score(e, ws) })).filter((x) => x.s !== null)
      .sort((a, b) => b.s - a.s || (tie ? tie(a.e, b.e) : 0)).slice(0, limit).map((x) => x.e);
  }
  const newer = (a, b) => Number(b.pr.state === "OPEN") - Number(a.pr.state === "OPEN") || (b.pr.updated_on || "").localeCompare(a.pr.updated_on || "");

  function search(raw) {
    const q = raw.trim();
    const only = ONLY[q[0]] || null;
    const ws = wordsOf(only ? q.slice(1) : q);
    const want = (kind) => !only || only === kind;
    const out = [];
    if (!index) index = buildIndex();
    if (want("pr")) {
      const num = ws.length === 1 && /^\d+$/.test(ws[0]) ? Number(ws[0]) : null;
      let found;
      if (num !== null) found = index.prs.filter((e) => String(e.id).startsWith(ws[0])).sort((a, b) => Number(b.id === num) - Number(a.id === num) || newer(a, b));
      else if (ws.length) found = ranked(index.prs, ws, LIMIT.pr, newer);
      else if (only) found = [...index.prs].sort(newer);
      else found = recentPrs().map((r) => index.prs.find((e) => prKey(e.pr) === prKey(r)) || { kind: "pr", pr: r }).slice(0, 5);
      out.push(...found.slice(0, LIMIT.pr));
    }
    if (want("term")) out.push(...(ws.length ? ranked(index.terms, ws, LIMIT.term) : index.terms.slice(0, only ? 20 : 4)));
    if (want("repo") && ws.length) out.push(...ranked(index.repos, ws, LIMIT.repo));
    if (want("person")) out.push(...(ws.length ? ranked(index.people, ws, LIMIT.person) : only ? index.people : []));
    if (want("cmd")) out.push(...(ws.length ? ranked(index.cmds, ws, LIMIT.cmd) : only ? index.cmds : []));
    if (want("music") && ws.length && musicFor === ws.join(" ")) out.push(...music);
    return out;
  }

  /* ------------------------------------------------------------ music, from Spotify */

  const musicLive = () => typeof Music !== "undefined" && Music.core && (Music.core.mode === "live" || Music.core.mode === "demo");
  function askMusic(raw) {
    clearTimeout(musicTimer);
    const q = raw.trim();
    const only = ONLY[q[0]] || null;
    const text = wordsOf(only ? q.slice(1) : q).join(" ");
    if (!musicLive() || (only && only !== "music") || text.length < 2) { music = []; musicFor = text; setBusy(false); return; }
    if (text === musicFor) return;
    setBusy(true);
    musicTimer = setTimeout(async () => {
      try {
        const res = await Music.core.source().search(text, "all", 0);
        const pick = (key, n) => (res?.[key]?.items || []).slice(0, n);
        const found = [
          ...pick("tracks", only ? 8 : 4).map((x) => ({ kind: "music", type: "track", x })),
          ...pick("artists", 1).map((x) => ({ kind: "music", type: "artist", x })),
          ...pick("albums", only ? 3 : 2).map((x) => ({ kind: "music", type: "album", x })),
          ...pick("playlists", only ? 3 : 1).map((x) => ({ kind: "music", type: "playlist", x })),
        ];
        if (!dialog?.open || currentText() !== text) return;
        music = found.slice(0, LIMIT.music);
        musicFor = text;
        update({ keep: true });
      } catch { /* Spotify did not answer: the rest of the box still works */ }
      setBusy(false);
    }, MUSIC_WAIT);
  }
  function currentText() {
    const q = dialog.querySelector(".jump-input").value.trim();
    const only = ONLY[q[0]] || null;
    return wordsOf(only ? q.slice(1) : q).join(" ");
  }
  function setBusy(on) { dialog?.querySelector(".jump-field")?.classList.toggle("is-busy", on); }

  /* ------------------------------------------------------------ the rows */

  function tag(pr) {
    if (pr.state === "MERGED") return '<span class="jump-tag">MERGED</span>';
    if (pr.state === "DECLINED") return '<span class="jump-tag">DECLINED</span>';
    const s = signals(pr);
    if (s.changes.length) return '<span class="jump-tag is-bad">CHANGES</span>';
    if (conflictsOf(pr, { deep: false })) return `<span class="jump-tag is-wait">${Sprites.svg("warn", { scale: 1 })} CONFLICTS</span>`;
    if (pr.behind?.behind) return `<span class="jump-tag is-wait">${esc(pr.behind.behind)} BEHIND</span>`;
    if (s.approved) return '<span class="jump-tag is-ok">APPROVED</span>';
    return '<span class="jump-tag">OPEN</span>';
  }
  const keyOfRow = (r) => (r.kind === "pr" ? `pr:${prKey(r.pr)}` : r.kind === "term" ? `t:${r.t.id}` : r.kind === "repo" ? `r:${r.r.slug}`
    : r.kind === "person" ? `p:${r.p.who}` : r.kind === "cmd" ? `c:${r.c.menu}:${r.c.label}` : `m:${r.x.uri || r.x.id}`);
  function rowHtml(r, i) {
    const open = (lead, label, right) => `<button class="mb-item jump-row${i === at ? " is-at" : ""}" type="button" data-j="${i}" role="option" aria-selected="${i === at}">
      <span class="jump-lead">${lead}</span><span class="mb-label">${label}</span>${right}</button>`;
    if (r.kind === "pr") {
      const who = r.pr.author?.name && !isMeUuid(r.pr.author.uuid) ? `, ${esc(r.pr.author.name)}` : "";
      return open(`<span class="num8">#${esc(r.pr.id)}</span>`, `${esc(r.pr.title || "")} <small>${esc(r.pr.repo || "")}${who}</small>`, r.pr.state ? tag(r.pr) : "");
    }
    if (r.kind === "term") {
      const st = !r.t.alive ? "ENDED" : r.t.busy ? "WORKING" : r.t.done ? "DONE" : r.t.current ? "SHOWN" : "";
      return open(r.t.claude ? Sprites.svg("buddy", { scale: 1 }) : "$", `${esc(r.t.label)}`, `<span class="mb-key">${st}</span>`);
    }
    if (r.kind === "repo") return open("&#9707;", `${esc(r.r.name)} <small>${esc(r.r.description.slice(0, 80))}</small>`, '<span class="mb-key">REPOS</span>');
    if (r.kind === "person") return open("@", esc(r.p.name), '<span class="mb-key">PULL REQUESTS</span>');
    if (r.kind === "cmd") return open("&gt;", `${esc(r.c.label)} <small>${esc(r.c.menu)}</small>`, `<span class="mb-key">${esc(r.c.key)}</span>`);
    const x = r.x, by = (x.artists || []).join(", ") || x.owner || "";
    const word = { track: "SONG", artist: "ARTIST", album: "ALBUM", playlist: "PLAYLIST" }[r.type];
    return open("&#9835;", `${esc(x.name || "")}${by ? ` <small>${esc(by)}</small>` : ""}`, `<span class="mb-key">${word}</span>`);
  }
  function paint() {
    const list = dialog.querySelector(".jump-list");
    if (!rows.length) {
      const typed = currentText();
      list.innerHTML = `<p class="jump-none">${typed ? "Nothing matches." : "Type a pull request number, a title, a branch, a terminal, a person or a song."} # pull requests, $ terminals, @ people, &gt; commands, ! music.</p>`;
      return;
    }
    let last = "";
    list.innerHTML = rows.map((r, i) => { const h = r.kind !== last ? `<p class="mb-group">${HEAD[r.kind]}</p>` : ""; last = r.kind; return h + rowHtml(r, i); }).join("");
    list.querySelector(".is-at")?.scrollIntoView({ block: "nearest" });
  }
  function update({ keep = false } = {}) {
    const was = keep && rows[at] ? keyOfRow(rows[at]) : null;
    rows = search(dialog.querySelector(".jump-input").value);
    at = was ? Math.max(0, rows.findIndex((r) => keyOfRow(r) === was)) : 0;
    paint();
  }

  /* ------------------------------------------------------------ picking */

  function pick(i) {
    const r = rows[i];
    if (!r) return;
    close();
    if (r.kind === "pr") return openPr(findPr(prKey(r.pr)) || r.pr);
    if (r.kind === "term") { Layout.activate("terminal"); Term.select(r.t.id); return; }
    if (r.kind === "repo") return Repos.open(r.r.slug);
    if (r.kind === "person") { if (state.pr) closePr(); setWho(r.p.who); Layout.activate("queue"); return; }
    if (r.kind === "cmd") { try { r.c.run(); } catch (err) { toast("That did not work.", err.message, true); } return; }
    const x = r.x, core = Music.core;
    if (r.type === "track" && x.uri) return core.playSong(x);
    if ((r.type === "album" || r.type === "playlist") && x.uri) return core.playFrom({ context: x.uri });
    if (r.type === "artist" && typeof MusicLibrary !== "undefined") { core.openPanel(); MusicLibrary.open({ kind: "artist", id: x.id, label: x.name }); }
  }

  /* ------------------------------------------------------------ the box */

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "jump";
    dialog.setAttribute("aria-label", "Jump to");
    dialog.innerHTML = `<label class="jump-field"><span class="jump-prompt" aria-hidden="true">&gt;</span><span class="visually-hidden">Jump to</span>
        <input class="jump-input" type="text" autocomplete="off" spellcheck="false" placeholder="Pull request, terminal, repository, person, command or song" role="combobox" aria-expanded="true">
        <span class="jump-spin" aria-hidden="true"></span></label>
      <div class="jump-list" role="listbox"></div>
      <p class="jump-keys"><span>↑↓ MOVE</span><span>ENTER OPEN</span><span># PR</span><span>$ TERMINAL</span><span>@ PERSON</span><span>&gt; COMMAND</span><span>! MUSIC</span><span>ESC CLOSE</span></p>`;
    document.body.append(dialog);
    const input = dialog.querySelector(".jump-input");
    input.addEventListener("input", () => { update(); askMusic(input.value); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (rows.length) { at = (at + (e.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length; paint(); } }
      else if (e.key === "Enter") { e.preventDefault(); pick(at); }
    });
    // Escape closes with the same animation as a click outside, not at once.
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
    dialog.addEventListener("click", (e) => {
      const row = e.target.closest("[data-j]");
      if (row) return pick(Number(row.dataset.j));
      if (e.target === dialog) close();
    });
  }
  function open(prefix = "") {
    if (!dialog) build();
    MenuBar.close();
    index = buildIndex();
    music = []; musicFor = "";
    dialog.classList.remove("is-closing");
    const input = dialog.querySelector(".jump-input");
    input.value = prefix;
    if (!dialog.open) dialog.showModal();
    update();
    askMusic(prefix);
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
  function close() {
    if (!dialog?.open || dialog.classList.contains("is-closing")) return;
    clearTimeout(musicTimer);
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.motion === "calm";
    if (still) { dialog.close(); return; }
    dialog.classList.add("is-closing");
    setTimeout(() => { dialog.classList.remove("is-closing"); dialog.close(); }, 110);
  }
  // Ctrl K anywhere but a terminal: there it deletes to the end of the line, and stays the shell's.
  function mount() {
    document.addEventListener("keydown", (e) => {
      if (document.activeElement?.closest?.(".xterm")) return;
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault(); e.stopPropagation();
        if (dialog?.open) close(); else open();
      }
    }, true);
    document.getElementById("jump-btn")?.addEventListener("click", () => open());
  }
  return { mount, open, close, search };
})();
