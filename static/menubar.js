"use strict";

/* The menu bar: Desk, View, Go, Pull request, Team, Help, like a desktop editor's. Every item
   is something PixelDeck already does; the menus only gather them in one place. A menu is
   built when it opens, so its checks, counts and greyed items are always current.

   A click opens a menu; while one is open, moving onto another name slides to it. Alt with
   the underlined letter opens one (not from a terminal, whose programs own Alt). Arrows move,
   Enter picks, Escape closes.

   Beside the menus, the scope chip: which project, whose pull requests, in one control. */

const MenuBar = (() => {
  let nav = null, open = -1, built = [];
  const has = (f) => (state.features || []).includes(f);
  const openPrNow = () => state.pr?.detail?.pr || state.pr?.pr || null;
  const isOpenPr = () => openPrNow()?.state === "OPEN";
  const mineOpen = () => isOpenPr() && isMeUuid(openPrNow().author?.uuid);
  const press = (sel) => { const b = document.querySelector(sel); if (b && !b.disabled) b.click(); };
  const exists = (sel) => !!document.querySelector(sel);
  const KEYS = { queue: "Q", insights: "I", terminal: "T" };

  function model() {
    const p = Prefs.get();
    const vis = Layout.visibility();
    const view = viewOf("queue"), kind = viewKind(view);
    const data = currentData(view);
    const b = data ? buckets(data, kind) : null;
    const pr = openPrNow();
    const chrome = typeof PixelChrome !== "undefined" && PixelChrome.available();
    const toggle = (id) => { const x = Layout.visibility().find((v) => v.id === id); if (!x) return; if (x.hidden) { Layout.show(id); Layout.activate(id); } else Layout.hide(id); renderBar(); };
    return [
      { id: "desk", label: "Desk", key: "D", items: [
        { label: "New shell", run: () => { Layout.activate("terminal"); Term.newShell(); } },
        { label: "Jump to a pull request…", key: "Ctrl K", run: () => Jump.open("#") },
        "-",
        { label: "Refresh from Bitbucket", key: "R", run: () => refreshNow() },
        { label: "Notifications", run: () => Notify.toggle(null, true, "notes") },
        { label: "Quick toggles", run: () => Notify.toggle(null, true, "toggles") },
        "-",
        { label: "Settings…", run: () => Settings.open() },
        { label: "Connections", run: () => Settings.open("connections") },
        { label: "Personalise", run: () => Settings.open("look") },
        ...(chrome ? ["-", { label: "Close the window", run: () => PixelChrome.send("close") }] : []),
      ] },
      { id: "view", label: "View", key: "V", items: [
        { group: "Panels" },
        ...vis.filter((x) => !x.copy).map((x) => ({ label: x.title, key: KEYS[x.id], check: !x.hidden, run: () => toggle(x.id) })),
        ...vis.filter((x) => x.copy).map((x) => ({ label: x.title, check: !x.hidden, run: () => (x.hidden ? Layout.show(x.id) : Layout.remove(x.id)) })),
        "-",
        { group: "Layout" },
        { label: "Split", run: () => Layout.preset("split") },
        { label: "Tabs", run: () => Layout.preset("compact") },
        ...vis.filter((x) => !x.copy && x.copyable).map((x) => ({ label: `Copy the ${x.title} panel`, run: () => Layout.duplicate(x.id) })),
        "-",
        { label: "Fold the pull request header", key: "F", enabled: () => !!state.pr, check: !!state.pr && state.prFolded, run: () => applyFold(!state.prFolded) },
        "-",
        { group: "Look" },
        ...THEMES.map(([id, label]) => ({ label, check: document.documentElement.dataset.theme === id, run: () => Prefs.set({ theme: id }) })),
        { label: "Calm motion", check: p.motion === "calm", run: () => Prefs.set({ motion: p.motion === "calm" ? "auto" : "calm" }) },
        { label: "Mascots", check: !!p.mascots, run: () => Prefs.set({ mascots: !p.mascots }) },
        { label: "Games beside the terminal", check: !!p.games, run: () => Prefs.set({ games: !p.games }) },
        "-",
        { group: "Music" },
        { label: "Spotify sync", check: p.spotifySync !== false, run: () => Prefs.set({ spotifySync: p.spotifySync === false }) },
        { label: "Lyrics", enabled: () => typeof Music !== "undefined" && !!Music.setLyrics, check: typeof Music !== "undefined" && !!Music.lyrics?.(), run: () => Music.setLyrics(!Music.lyrics()) },
        ...(chrome ? [{ label: "Use the system title bar", check: p.titlebar === "system", run: () => Prefs.set({ titlebar: p.titlebar === "system" ? "custom" : "system" }) }] : []),
      ] },
      { id: "go", label: "Go", key: "G", items: [
        ...sectionsFor(kind).map((s) => ({ label: s.label, count: b ? b[s.id].length : "", check: view.section === s.id && !state.pr,
          run: () => { if (state.pr) closePr(); Layout.activate("queue"); go({ ...state.route, section: s.id }); } })),
        "-",
        { label: "Next pull request", key: "J", run: () => { Layout.activate("queue"); moveCursor(1); } },
        { label: "Previous pull request", key: "K", run: () => { Layout.activate("queue"); moveCursor(-1); } },
        ...(recentPrs().length ? ["-", { group: "Recent" }, ...recentPrs().slice(0, 6).map((r) => ({
          label: `${r.repo} #${r.id}`, hint: r.title, run: () => { const hit = findPr(`${r.repo}#${r.id}`); openPr(hit || r); } }))] : []),
      ] },
      { id: "pr", label: "Pull request", key: "P", items: [
        { label: "Merge…", enabled: () => mineOpen() && exists(".pr-tools [data-merge]"), run: () => press(".pr-tools [data-merge]") },
        { label: `Sync with ${pr?.dest || "the target"}`, enabled: () => mineOpen() && exists(".sync-bar .sync-now"), run: () => press(".sync-bar .sync-now") },
        "-",
        { label: "Approve", enabled: () => exists('.pr-tools [data-review="approve"]'), run: () => press('.pr-tools [data-review="approve"]') },
        { label: "Request changes", enabled: () => exists('.pr-tools [data-review="request_changes"]'), run: () => press('.pr-tools [data-review="request_changes"]') },
        "-",
        { label: "Fix with Claude", enabled: () => exists('.pr-tools [data-launch="fix"]'), run: () => press('.pr-tools [data-launch="fix"]') },
        { label: "Fix and push with Claude", enabled: () => exists('.pr-tools [data-launch="fix_push"]'), run: () => press('.pr-tools [data-launch="fix_push"]') },
        { label: "Review with Claude", enabled: () => exists('.pr-tools [data-launch="review"]'), run: () => press('.pr-tools [data-launch="review"]') },
        { label: "Summarize the threads", enabled: () => exists('.pr-tools [data-launch="summarize"]'), run: () => press('.pr-tools [data-launch="summarize"]') },
        "-",
        { label: "Open in Bitbucket", enabled: () => !!pr?.url, run: () => openExternal(pr.url) },
        { label: "Open the ticket in Jira", enabled: () => !!pr?.ticket?.url, run: () => openExternal(pr.ticket.url) },
        { label: "Copy the link", enabled: () => !!pr?.url, run: () => press("[data-copy-link]") },
        { label: "Reload it", enabled: () => !!state.pr, run: () => openPr(state.pr.pr, { force: true }) },
        { label: "Back to the queue", enabled: () => !!state.pr, run: () => closePr() },
      ] },
      { id: "team", label: "Team", key: "T", items: [
        { group: "Whose pull requests" },
        ...(state.me ? [{ label: `${state.me.name} (you)`, check: state.route.who === "me", run: () => setWho("me") }] : []),
        { label: "The whole team", check: state.route.who === "team", run: () => setWho("team") },
        ...(state.team || []).map((m) => ({ label: m.name, check: state.route.who === m.uuid, run: () => setWho(m.uuid) })),
        { label: "Edit the team…", run: () => openTeamDialog() },
        "-",
        { group: "Project" },
        { label: "Every project", check: !state.project, run: () => setProject("") },
        ...(state.projects || []).map((x) => ({ label: x.name, check: state.project?.key === x.key, run: () => setProject(x.key) })),
      ] },
      { id: "help", label: "Help", key: "H", items: [
        { label: "Keyboard keys", key: "?", run: () => $("#help-dialog").showModal() },
        { label: "The guide", run: () => Guide.start() },
        "-",
        { label: "About PixelDeck", run: () => Settings.open("about") },
        { label: "PixelDeck on GitHub", run: () => openExternal("https://github.com/ahmedloumx/pixeldeck") },
      ] },
    ];
  }

  const enabledOf = (it) => (typeof it.enabled === "function" ? it.enabled() : it.enabled !== false);
  function itemHtml(it, i) {
    if (it === "-") return `<div class="mb-rule" role="separator"></div>`;
    if (it.group) return `<div class="mb-group" role="presentation">${esc(it.group)}</div>`;
    const on = enabledOf(it);
    const right = it.count !== undefined && it.count !== "" ? `<span class="mb-count num8">${esc(it.count)}</span>` : `<span class="mb-key">${esc(it.key || "")}</span>`;
    return `<button class="mb-item" type="button" role="menuitem" data-mi="${i}"${on ? "" : ' aria-disabled="true"'}${it.hint ? ` title="${esc(it.hint)}"` : ""}>
      <span class="mb-check" aria-hidden="true">${it.check ? "✓" : ""}</span><span class="mb-label">${esc(it.label)}</span>${right}</button>`;
  }
  function paintTitles() {
    nav.innerHTML = built.map((m, i) => {
      const at = m.label.indexOf(m.key);
      const label = at >= 0 ? `${esc(m.label.slice(0, at))}<u>${esc(m.key)}</u>${esc(m.label.slice(at + 1))}` : esc(m.label);
      return `<div class="mb-menu"><button class="mb-title" type="button" data-menu="${m.id}" data-mb="${i}" aria-haspopup="menu" aria-expanded="false">${label}</button></div>`;
    }).join("");
  }
  function show(i, focusFirst = false) {
    close();
    built = model();
    const host = nav.querySelectorAll(".mb-menu")[i];
    if (!host) return;
    const m = built[i];
    const pop = document.createElement("div");
    pop.className = "mb-pop";
    pop.setAttribute("role", "menu");
    pop.setAttribute("aria-label", m.label);
    pop.innerHTML = m.items.map(itemHtml).join("");
    host.append(pop);
    host.querySelector(".mb-title").setAttribute("aria-expanded", "true");
    open = i;
    if (focusFirst) pop.querySelector(".mb-item:not([aria-disabled])")?.focus();
  }
  function close() {
    if (open < 0 || !nav) return;
    nav.querySelector(".mb-pop")?.remove();
    for (const t of nav.querySelectorAll(".mb-title")) t.setAttribute("aria-expanded", "false");
    open = -1;
  }
  function run(i) {
    const it = built[open]?.items[i];
    if (!it || it === "-" || it.group || !enabledOf(it)) return;
    close();
    try { it.run(); } catch (err) { toast("That did not work.", err.message, true); }
  }
  function step(dir) {
    const items = [...nav.querySelectorAll(".mb-item:not([aria-disabled])")];
    if (!items.length) return;
    const at = items.indexOf(document.activeElement);
    items[(at + dir + items.length) % items.length].focus();
  }

  function onKey(e) {
    const inTerm = document.activeElement?.closest?.(".xterm");
    if (e.altKey && !e.ctrlKey && !e.metaKey && !inTerm && /^[a-z]$/i.test(e.key)) {
      const i = built.findIndex((m) => m.key.toLowerCase() === e.key.toLowerCase());
      if (i >= 0) { e.preventDefault(); show(i, true); return; }
    }
    if (open < 0) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); const i = open; close(); nav.querySelectorAll(".mb-title")[i]?.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); step(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); step(-1); }
    else if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); show((open + (e.key === "ArrowRight" ? 1 : built.length - 1)) % built.length, true); }
  }

  /* ---------------------------------------------------------------- scope */
  let scopePop = null;
  function paintScope() {
    const chip = document.getElementById("scope");
    if (!chip) return;
    const who = state.route.who;
    const person = who === "me" ? "Me" : who === "team" ? "Team" : (personName(who) || "Someone");
    const shortName = (n) => String(n).split(/\s+/)[0];
    chip.innerHTML = `<span class="scope-part"><span class="scope-mark" aria-hidden="true">◆</span><span class="scope-text">${esc(state.project?.name || "Every project")}</span></span>
      <span class="scope-part">${who === "team" ? "" : face({ name: who === "me" ? state.me?.name : person })}<span class="scope-text">${esc(who === "me" || who === "team" ? person : shortName(person))}</span><span class="scope-caret" aria-hidden="true">▾</span></span>`;
    chip.setAttribute("aria-label", `Project: ${state.project?.name || "every project"}. Pull requests of: ${person}. Change`);
  }
  function scopeRows(q) {
    const match = (s) => !q || String(s).toLowerCase().includes(q);
    const row = (attr, value, label, on, extra = "") => `<button class="mb-item" type="button" ${attr}="${esc(value)}"><span class="mb-check" aria-hidden="true">${on ? "✓" : ""}</span><span class="mb-label">${extra}${esc(label)}</span><span class="mb-key"></span></button>`;
    const projects = [["", "Every project"], ...(state.projects || []).map((x) => [x.key, x.name])].filter(([, n]) => match(n))
      .map(([k, n]) => row("data-scope-project", k, n, (state.project?.key || "") === k)).join("");
    const people = [...(state.me ? [["me", `${state.me.name} (you)`]] : []), ["team", "The whole team"], ...(state.team || []).map((m) => [m.uuid, m.name])]
      .filter(([, n]) => match(n)).map(([v, n]) => row("data-scope-who", v, n, state.route.who === v)).join("");
    return `<div class="scope-col"><p class="mb-group">Project</p>${projects || '<p class="scope-none">No match</p>'}</div>
      <div class="scope-col"><p class="mb-group">Whose pull requests</p>${people || '<p class="scope-none">No match</p>'}<button class="mb-item" type="button" data-scope-edit><span class="mb-check"></span><span class="mb-label">Edit the team…</span><span class="mb-key"></span></button></div>`;
  }
  function openScope() {
    close();
    if (scopePop) return closeScope();
    const chip = document.getElementById("scope");
    scopePop = document.createElement("div");
    scopePop.className = "mb-pop scope-pop";
    scopePop.setAttribute("role", "dialog");
    scopePop.setAttribute("aria-label", "Project and person");
    scopePop.innerHTML = `<input class="scope-find" type="search" placeholder="Find a project or a person" autocomplete="off"><div class="scope-cols">${scopeRows("")}</div>`;
    const r = chip.getBoundingClientRect();
    scopePop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - 560))}px`;
    scopePop.style.top = `${r.bottom + 4}px`;
    document.body.append(scopePop);
    chip.setAttribute("aria-expanded", "true");
    const find = scopePop.querySelector(".scope-find");
    find.addEventListener("input", () => { scopePop.querySelector(".scope-cols").innerHTML = scopeRows(find.value.trim().toLowerCase()); });
    find.focus();
  }
  function closeScope() {
    scopePop?.remove();
    scopePop = null;
    document.getElementById("scope")?.setAttribute("aria-expanded", "false");
  }

  function mount() {
    nav = document.getElementById("menubar");
    if (!nav) return;
    built = model();
    paintTitles();
    nav.addEventListener("click", (e) => {
      const t = e.target.closest(".mb-title");
      if (t) { const i = Number(t.dataset.mb); return open === i ? close() : show(i); }
      const it = e.target.closest(".mb-item");
      if (it) run(Number(it.dataset.mi));
    });
    nav.addEventListener("mouseover", (e) => {
      const t = e.target.closest(".mb-title");
      if (t && open >= 0 && Number(t.dataset.mb) !== open) show(Number(t.dataset.mb));
    });
    document.getElementById("scope")?.addEventListener("click", openScope);
    document.addEventListener("click", (e) => {
      const p = e.target.closest("[data-scope-project]"), w = e.target.closest("[data-scope-who]");
      if (p) { closeScope(); return setProject(p.dataset.scopeProject); }
      if (w) { closeScope(); return setWho(w.dataset.scopeWho); }
      if (e.target.closest("[data-scope-edit]")) { closeScope(); return openTeamDialog(); }
    });
    // A press anywhere else closes them; capture, so a panel that stops the event still does.
    document.addEventListener("pointerdown", (e) => {
      if (open >= 0 && !nav.contains(e.target)) close();
      if (scopePop && !scopePop.contains(e.target) && !e.target.closest("#scope")) closeScope();
    }, true);
    document.addEventListener("keydown", (e) => {
      if (scopePop && e.key === "Escape") { e.preventDefault(); closeScope(); document.getElementById("scope")?.focus(); return; }
      onKey(e);
    }, true);
    paintScope();
  }
  function commands() {
    built = model();
    return built.flatMap((m) => m.items.filter((it) => it !== "-" && !it.group && enabledOf(it)).map((it) => ({ menu: m.label, label: it.label, key: it.key || "", run: it.run })));
  }

  return { mount, paintScope, close, commands, isOpen: () => open >= 0 || !!scopePop };
})();
