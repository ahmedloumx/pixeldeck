"use strict";

/* The Repos panel: the project's repositories, their branches, commits and code, read only.
   The page drives terminals, so every string from Bitbucket goes through esc() or textContent.
   The panel listens on its own element with data-rp-* attributes: app.js owns data-action,
   .sec-chip and friends on the document. Pull request rows reuse its prRow and data-open. */

const Repos = (() => {
  const KEY = "prdesk.repos.v1";
  const TABS = [["code", "Code", "Code"], ["branches", "Branches", "Branches"], ["commits", "Commits", "Commits"], ["prs", "Pull requests", "PRs"]];
  const GLYPH = {
    folder: [".###.....", "#####....", "#########", "#########", "#########", "#########", "#########"],
    file: ["####...", "#..##..", "#..#.#.", "#..####", "#.....#", "#.....#", "#.....#", "#######"],
    branch: [".#...#.", ".#...#.", ".#..#..", ".#.#...", ".##....", ".#.....", ".#....."],
    back: ["...#....", "..#.....", ".#......", "########", ".#......", "..#.....", "...#...."],
  };
  const enc = encodeURIComponent;
  const query = (o) => Object.entries(o).map(([k, v]) => `${k}=${enc(v)}`).join("&");
  const webPath = (path) => path.split("/").map(enc).join("/");
  const parentOf = (path) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");
  const baseOf = (path) => path.slice(path.lastIndexOf("/") + 1);
  const gone = (err) => err.status === 404 || err.data?.upstream === 404;

  let root = null;
  let searchTimer = 0;
  const st = {
    list: null, listError: "", listFor: null, loading: false, restored: false, filter: "",
    repo: null, tab: "code", branches: null, branchesError: "", branchFilter: "", branchHits: null,
    branch: "", hash: "", path: "", file: "", codeKey: "",
    tree: null, treeError: "", fileData: null, fileError: "", readme: null, commits: null,
  };
  const mem = (() => {
    try { const m = JSON.parse(localStorage.getItem(KEY)); if (m && typeof m.repos === "object" && m.repos) return m; } catch { /* storage blocked */ }
    return { last: "", repos: {} };
  })();

  // The last repository, and per repository its branch, folder, file and tab.
  function remember() {
    if (st.repo) {
      const slug = st.repo.slug;
      delete mem.repos[slug];
      mem.repos[slug] = { branch: st.branch, path: st.path, file: st.file, tab: st.tab };
      mem.last = slug;
    } else mem.last = "";
    const keys = Object.keys(mem.repos);
    for (const k of keys.slice(0, Math.max(0, keys.length - 40))) delete mem.repos[k];
    try { localStorage.setItem(KEY, JSON.stringify(mem)); } catch { /* storage blocked */ }
  }

  /* ------------------------------------------------------------ markup */

  function glyph(name) {
    const rows = GLYPH[name];
    let rects = "";
    rows.forEach((row, y) => [...row].forEach((c, x) => { if (c === "#") rects += `<rect x="${x}" y="${y}" width="1" height="1"/>`; }));
    const w = rows[0].length, h = rows.length;
    return `<svg class="rp-glyph rp-glyph--${name}" viewBox="0 0 ${w} ${h}" width="${w * 2}" height="${h * 2}" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`;
  }
  function size(n) {
    if (n == null) return "";
    if (n < 1024) return `${n} B`;
    if (n < 1048576) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
    return `${(n / 1048576).toFixed(1)} MB`;
  }
  const when = (iso) => (iso ? `<time class="when" datetime="${esc(iso)}" title="${esc(new Date(iso).toLocaleString("en-GB"))}">${esc(ago(iso))}</time>` : "");
  const waiting = (text) => `<div class="rp-wait" role="status">${Sprites.svg("bot", { scale: 2 })}<span>${esc(text)}<span class="rp-dots" aria-hidden="true"></span></span></div>`;
  const nothing = (text) => `<div class="rp-none">${Sprites.svg("buddy", { scale: 2 })}<span>${esc(text)}</span></div>`;
  const failed = (title, detail, act) => `<div class="rp-error" role="alert"><p>${esc(title)}</p>${detail ? `<p class="rp-error-detail">${esc(detail)}</p>` : ""}
    ${act ? `<button class="btn btn--quiet" type="button" data-rp="${act}">Try again</button>` : ""}</div>`;

  /* ------------------------------------------------------------ the list */

  function showList({ keep = false } = {}) {
    st.repo = null;
    if (!keep) remember();  // the first paint must not forget where you were before the list loads
    root.innerHTML = `<div class="rp-view">
      <header class="panel-head-row"><h2 class="panel-title">Repositories</h2><span class="note rp-note"></span></header>
      <div class="rp-tools">
        <label class="field rp-search"><span class="visually-hidden">Search repositories</span>
          <input type="search" data-rp-filter placeholder="Search: name or description" autocomplete="off" spellcheck="false"></label>
        <button class="btn btn--quiet" type="button" data-rp="refresh" title="Read the list again from Bitbucket">Refresh</button>
      </div>
      <div class="rp-results"></div></div>`;
    root.querySelector("[data-rp-filter]").value = st.filter;
    paintList();
  }
  function paintList() {
    const box = root.querySelector(".rp-results");
    if (!box) return;
    const note = root.querySelector(".rp-note");
    if (st.listError && !st.list) { box.innerHTML = failed("Could not read the repositories.", st.listError, "refresh"); return; }
    if (!st.list) { note.textContent = ""; box.innerHTML = waiting("Reading the repositories"); return; }
    const needle = st.filter.trim().toLowerCase();
    const all = st.list.repos;
    const hits = needle ? all.filter((r) => `${r.name}\n${r.slug}\n${r.description}`.toLowerCase().includes(needle)) : all;
    const where = st.list.project ? st.list.project.name || st.list.project.key : "every project";
    note.innerHTML = `<span class="num8">${hits.length}</span>${needle ? ` of ${all.length}` : ""} in ${esc(where)}`;
    if (!hits.length) { box.innerHTML = nothing(needle ? "No repository matches the search." : "No repositories in this project."); return; }
    box.innerHTML = `<ul class="rp-repos">${hits.map((r) => `<li>
      <button class="rp-repo" type="button" data-rp-open="${esc(r.slug)}">
        <span class="rp-repo-top"><span class="rp-repo-name">${esc(r.name)}</span>${r.language ? `<span class="rp-lang">${esc(r.language)}</span>` : ""}${when(r.updated_on)}</span>
        ${r.description ? `<span class="rp-repo-desc">${esc(r.description)}</span>` : ""}
        ${r.mainbranch ? `<span class="rp-repo-main">${glyph("branch")}${esc(r.mainbranch)}</span>` : ""}
      </button></li>`).join("")}</ul>${st.list.total > all.length ? `<p class="rp-sub">The newest ${all.length} of ${st.list.total}.</p>` : ""}`;
  }
  async function load({ force = false } = {}) {
    if (st.loading) return;
    st.loading = true;
    st.listError = "";
    if (!st.repo) paintList();
    try {
      st.list = await api(`repos${force ? "?force=1" : ""}`);
      st.listFor = st.list.project?.key || "";
    } catch (err) {
      st.listError = err.message;
      if (st.list && force) toast("Could not refresh the repositories.", err.message, true);
    }
    st.loading = false;
    if (!st.restored && st.list) {
      st.restored = true;
      if (mem.last && st.list.repos.some((r) => r.slug === mem.last)) return openRepo(mem.last, { restore: true });
    }
    if (st.repo && !st.list?.repos.some((r) => r.slug === st.repo.slug)) return showList();
    if (!st.repo) paintList();
  }
  // app.js switches the project without telling the panels: wait for its answer, then reload.
  function watchProject(want) {
    if (want === st.listFor) return;
    const started = Date.now();
    const tick = () => {
      if ((state.project?.key || "") === want) { st.list = null; st.listError = ""; if (st.repo) showList(); return load(); }
      if (Date.now() - started < 15000) setTimeout(tick, 250);
    };
    setTimeout(tick, 250);
  }

  /* ------------------------------------------------------------ one repository */

  async function openRepo(slug, { restore = false } = {}) {
    const repo = st.list?.repos.find((r) => r.slug === slug);
    if (!repo) return showList();
    const m = mem.repos[slug] || {};
    Object.assign(st, {
      repo, tab: restore && TABS.some(([id]) => id === m.tab) ? m.tab : "code",
      branches: null, branchesError: "", branchFilter: "", branchHits: null,
      branch: m.branch || repo.mainbranch || "", hash: "", path: m.path || "", file: m.file || "", codeKey: "",
      tree: null, treeError: "", fileData: null, fileError: "", readme: null, commits: null,
    });
    remember();
    showRepo();
    scrollTop();
    try {
      const b = await api(`repo-branches?${query({ repo: slug })}`);
      if (st.repo?.slug !== slug) return;
      st.branches = b;
    } catch (err) {
      if (st.repo?.slug !== slug) return;
      st.branchesError = err.message;
    }
    paintTabs();
    if (st.tab === "branches") paintBranchList();
    if (st.branchesError) return paintPane();
    if (!st.branch && st.branches.branches.length) st.branch = st.branches.branches[0].name;
    if (!st.branch) { st.branchesError = "This repository has no branches yet."; return paintPane(); }
    setBranch(st.branch);
  }
  function showRepo() {
    const r = st.repo;
    root.innerHTML = `<div class="rp-view">
      <nav class="rp-crumbs" aria-label="Where you are">
        <button class="rp-back" type="button" data-rp="back" aria-label="Back" data-tip="Back">${glyph("back")}</button>
        <button class="link" type="button" data-rp="list">Repositories</button><span aria-hidden="true">/</span><span class="rp-here">${esc(r.slug)}</span></nav>
      <header class="rp-head"><h2 class="panel-title">${esc(r.name)}</h2>
        <span class="rp-head-meta">${r.mainbranch ? `<span class="rp-repo-main" title="Main branch">${glyph("branch")}${esc(r.mainbranch)}</span>` : ""}
        ${r.language ? `<span class="rp-lang">${esc(r.language)}</span>` : ""}
        <button class="link" type="button" data-external="${esc(r.url)}">Bitbucket</button></span></header>
      ${r.description ? `<p class="rp-desc">${esc(r.description)}</p>` : ""}
      <div class="rp-tabs" role="tablist" aria-label="Repository views"></div>
      <div class="rp-pane" role="tabpanel"></div></div>`;
    paintTabs();
    paintPane();
  }
  function paintTabs() {
    const box = root.querySelector(".rp-tabs");
    if (!box || !st.repo) return;
    const n = { branches: st.branches ? st.branches.total : "", prs: openPrs(st.repo.slug).length || "" };
    box.innerHTML = TABS.map(([id, long, short]) => `<button class="rp-tab${st.tab === id ? " is-current" : ""}" type="button" role="tab" aria-selected="${st.tab === id}" data-rp-tab="${id}">
      <span><span class="sec-long">${long}</span><span class="sec-short">${short}</span></span>${n[id] !== undefined && n[id] !== "" ? `<span class="sec-n">${esc(n[id])}</span>` : ""}</button>`).join("");
  }
  function setTab(id) {
    if (!TABS.some(([t]) => t === id) || !st.repo) return;
    st.tab = id;
    remember();
    paintTabs();
    paintPane();
  }
  function paintPane() {
    const pane = root.querySelector(".rp-pane");
    if (!pane) return;
    if (st.tab === "code") {
      pane.innerHTML = `<div class="rp-code-bar"><label class="field rp-pick"><span class="visually-hidden">Branch</span><select data-retro data-rp-pick></select></label>
        <nav class="rp-path" aria-label="Path"></nav></div><div class="rp-code"></div>`;
      paintPicker();
      RetroSelect.enhanceAll(pane);
      if (st.hash && st.codeKey !== codeKey()) loadCode(); else paintCode();
    } else if (st.tab === "branches") {
      pane.innerHTML = `<div class="rp-tools"><label class="field rp-search"><span class="visually-hidden">Search branches</span>
        <input type="search" data-rp-branch-filter placeholder="Search branches" autocomplete="off" spellcheck="false"></label></div><div class="rp-branch-list"></div>`;
      pane.querySelector("[data-rp-branch-filter]").value = st.branchFilter;
      paintBranchList();
    } else if (st.tab === "commits") {
      paintCommits();
      loadCommits();
    } else paintPrs();
  }
  function scrollTop() { root.closest(".slot-body")?.scrollTo({ top: 0 }); }

  /* ------------------------------------------------------------ branches */

  // A branch name becomes a commit hash before anything is browsed: names hold slashes, hashes do not.
  async function setBranch(name) {
    const slug = st.repo.slug;
    const listed = [...(st.branches?.branches || []), ...(st.branchHits?.branches || [])];
    let hit = listed.find((b) => b.name === name);
    if (!hit) {
      try { hit = await api(`repo-ref?${query({ repo: slug, branch: name })}`); } catch (err) {
        if (st.repo?.slug !== slug) return;
        // A remembered branch may be deleted since: the main branch, or the newest, stands in.
        const fallback = st.repo.mainbranch || st.branches?.branches[0]?.name;
        if (gone(err) && fallback && fallback !== name) return setBranch(fallback);
        st.branchesError = err.message;
        return paintPane();
      }
      if (st.repo?.slug !== slug) return;
    }
    st.branch = hit.name;
    st.hash = hit.hash;
    remember();
    if (st.tab === "code") { paintPicker(); loadCode(); }
    else if (st.tab === "commits") { paintCommits(); loadCommits(); }
    else if (st.tab === "branches") paintBranchList();
  }
  function paintPicker() {
    const select = root.querySelector("[data-rp-pick]");
    if (!select) return;
    const names = (st.branches?.branches || []).map((b) => b.name);
    if (st.branch && !names.includes(st.branch)) names.unshift(st.branch);
    const html = names.length
      ? names.map((n) => `<option value="${esc(n)}"${n === st.branch ? " selected" : ""}>${esc(n)}</option>`).join("")
      : `<option value="">No branch</option>`;
    if (select.dataset.html !== html) { select.innerHTML = html; select.dataset.html = html; }
    select.value = st.branch;
    RetroSelect.sync(select);
  }
  const searchText = () => st.branchFilter.trim().replace(/["\\\x00-\x1f\x7f]/g, "").slice(0, 100);
  function paintBranchList() {
    const box = root.querySelector(".rp-branch-list");
    if (!box) return;
    if (st.branchesError && !st.branches) { box.innerHTML = failed("Could not read the branches.", st.branchesError, "reopen"); return; }
    if (!st.branches) { box.innerHTML = waiting("Reading the branches"); return; }
    const needle = searchText().toLowerCase();
    const seen = new Map();
    for (const b of st.branches.branches) if (!needle || b.name.toLowerCase().includes(needle)) seen.set(b.name, b);
    if (needle && st.branchHits?.search === searchText()) for (const b of st.branchHits.branches) seen.set(b.name, b);
    const rows = [...seen.values()].sort((a, b) => (b.date || "").localeCompare(a.date || ""));
    const searching = needle && !st.branches.complete && st.branchHits?.search !== searchText();
    const head = `<p class="rp-sub">${needle ? `<span class="num8">${rows.length}</span> matching` : `<span class="num8">${st.branches.total}</span> branches, newest first`}${!st.branches.complete && !needle ? `. The newest ${st.branches.branches.length} show; search finds the rest.` : "."}${searching ? " Asking Bitbucket for more." : ""}</p>`;
    if (!rows.length) { box.innerHTML = head + (searching ? waiting("Searching") : nothing("No branch matches the search.")); return; }
    box.innerHTML = head + `<ul class="rp-branches">${rows.map((b) => `<li>
      <button class="rp-branch${b.name === st.branch ? " is-current" : ""}" type="button" data-rp-branch="${esc(b.name)}" title="Browse the code on ${esc(b.name)}">
        <span class="rp-branch-top"><span class="rp-branch-name">${glyph("branch")}<span>${esc(b.name)}</span></span>${b.name === st.repo.mainbranch ? `<span class="rp-lang">main</span>` : ""}${when(b.date)}</span>
        ${b.message ? `<span class="rp-branch-msg">${esc(b.message)}</span>` : ""}<span class="rp-branch-who">${esc(b.author)}</span>
      </button></li>`).join("")}</ul>`;
  }
  // Past the newest 200, the name search goes to Bitbucket: debounced, one call per pause in typing.
  async function searchBranches() {
    const text = searchText();
    const slug = st.repo?.slug;
    if (!text || !slug || st.branches?.complete) return;
    try {
      const hits = await api(`repo-branches?${query({ repo: slug, q: text })}`);
      if (st.repo?.slug !== slug) return;
      st.branchHits = hits;
    } catch (err) {
      if (st.repo?.slug !== slug) return;
      st.branchHits = { search: text, branches: [] };
      toast("Could not search the branches.", err.message, true);
    }
    paintBranchList();
  }

  /* ------------------------------------------------------------ code */

  const codeKey = () => `${st.repo?.slug}|${st.hash}|${st.path}|${st.file}`;
  async function loadCode() {
    const at = codeKey();
    st.codeKey = at;
    st.treeError = st.fileError = "";
    const base = { repo: st.repo.slug, hash: st.hash };
    if (st.file) {
      st.fileData = null;
      paintCode();
      try {
        const d = await api(`repo-file?${query({ ...base, path: st.file })}`);
        if (at !== codeKey()) return;
        st.fileData = d;
      } catch (err) {
        if (at !== codeKey()) return;
        if (gone(err)) { st.file = ""; remember(); return loadCode(); }  // gone at this commit: its folder instead
        st.fileError = err.message;
      }
      return paintCode();
    }
    st.tree = null;
    st.readme = null;
    paintCode();
    let tree;
    try { tree = await api(`repo-tree?${query({ ...base, path: st.path })}`); } catch (err) {
      if (at !== codeKey()) return;
      if (gone(err) && st.path) { st.path = parentOf(st.path); remember(); return loadCode(); }  // one level up
      st.treeError = err.message;
      return paintCode();
    }
    if (at !== codeKey()) return;
    st.tree = tree;
    paintCode();
    if (!tree.readme) return;
    try {
      const d = await api(`repo-file?${query({ ...base, path: tree.readme })}`);
      if (at !== codeKey()) return;
      st.readme = d;
      paintReadme();
    } catch { /* the listing stands without it */ }
  }
  function goPath(path) { st.path = path; st.file = ""; remember(); loadCode(); scrollTop(); }
  function goFile(path) { st.file = path; remember(); loadCode(); scrollTop(); }
  function back() {
    if (st.repo && st.tab === "code" && st.file) return goPath(st.path);
    if (st.repo && st.tab === "code" && st.path) return goPath(parentOf(st.path));
    showList();
    scrollTop();
  }
  function crumbs() {
    const parts = st.path ? st.path.split("/") : [];
    let at = "";
    const out = [`<button class="link" type="button" data-rp-path="">${esc(st.repo.slug)}</button>`];
    parts.forEach((p, i) => {
      at = at ? `${at}/${p}` : p;
      out.push(!st.file && i === parts.length - 1 ? `<span aria-current="page">${esc(p)}</span>` : `<button class="link" type="button" data-rp-path="${esc(at)}">${esc(p)}</button>`);
    });
    if (st.file) out.push(`<span aria-current="page">${esc(baseOf(st.file))}</span>`);
    return out.join(`<span class="rp-slash" aria-hidden="true">/</span>`);
  }
  function paintCode() {
    const box = root.querySelector(".rp-code");
    if (!box) return;
    root.querySelector(".rp-path").innerHTML = st.hash ? crumbs() : "";
    if (!st.hash) { box.innerHTML = st.branchesError ? failed("Could not find the branch.", st.branchesError, "reopen") : waiting("Finding the branch"); return; }
    if (st.file) return paintFile(box);
    if (st.treeError) { box.innerHTML = failed("Could not read this folder.", st.treeError, "retry-code"); return; }
    if (!st.tree) { box.innerHTML = waiting("Reading the folder"); return; }
    const up = st.path ? `<li><button class="rp-entry is-up" type="button" data-rp-path="${esc(parentOf(st.path))}" aria-label="Up one folder">${glyph("folder")}<span class="rp-entry-name">..</span></button></li>` : "";
    const rows = st.tree.entries.map((e) => `<li><button class="rp-entry is-${e.type}" type="button" ${e.type === "dir" ? `data-rp-path="${esc(e.path)}"` : `data-rp-file="${esc(e.path)}"`}>
      ${glyph(e.type === "dir" ? "folder" : "file")}<span class="rp-entry-name">${esc(e.name)}</span>${e.type === "file" ? `<span class="rp-size">${esc(size(e.size))}</span>` : ""}</button></li>`).join("");
    const web = `${st.repo.url}/src/${st.hash}/${st.path ? `${webPath(st.path)}/` : ""}`;
    box.innerHTML = `<ul class="rp-tree">${up}${rows}</ul>
      ${st.tree.entries.length ? "" : nothing("This folder is empty.")}
      ${st.tree.truncated ? `<p class="rp-sub">The first ${st.tree.entries.length} entries only. <button class="link" type="button" data-external="${esc(web)}">The rest on Bitbucket</button></p>` : ""}
      <section class="rp-readme" hidden><h3 class="rp-readme-name"></h3><pre class="rp-readme-text"></pre></section>`;
    paintReadme();
  }
  // The README shows as plain text under its folder, never as markup.
  function paintReadme() {
    const sec = root.querySelector(".rp-readme");
    const d = st.readme;
    if (!sec || !d || d.text == null || st.file) return;
    sec.querySelector(".rp-readme-name").textContent = baseOf(d.path);
    sec.querySelector(".rp-readme-text").textContent = d.cut ? `${d.text}\n\n(cut at 512 KB)` : d.text;
    sec.hidden = false;
  }
  function paintFile(box) {
    const web = `${st.repo.url}/src/${st.hash}/${webPath(st.file)}`;
    if (st.fileError) { box.innerHTML = failed("Could not read this file.", st.fileError, "retry-code"); return; }
    const d = st.fileData;
    if (!d) { box.innerHTML = waiting("Reading the file"); return; }
    const head = (extra) => `<div class="rp-file-head"><span class="rp-file-name">${glyph("file")}<span>${esc(baseOf(st.file))}</span></span>
      <span class="figure-cell">${esc(size(d.size))}</span>${extra}<button class="link" type="button" data-external="${esc(web)}">Bitbucket</button></div>`;
    if (d.binary || d.text == null) {
      const what = d.attributes?.includes("lfs") ? "Stored in Git LFS" : d.attributes?.includes("subrepository") ? "A submodule" : "A binary file";
      box.innerHTML = head("") + `<div class="rp-none rp-binary">${Sprites.svg("buddy", { scale: 2 })}<span>${what}, not shown here. <button class="link" type="button" data-external="${esc(web)}">Open it on Bitbucket</button></span></div>`;
      return;
    }
    const text = d.text.replace(/\r\n?/g, "\n");
    const lines = text.endsWith("\n") ? text.slice(0, -1).split("\n") : text.split("\n");
    const count = text ? lines.length : 0;
    box.innerHTML = head(`<span class="figure-cell">${esc(plural(count, "line"))}</span>`)
      + (d.cut ? `<p class="rp-cut">Cut at 512 KB: the rest is on Bitbucket.</p>` : "")
      + (count ? `<div class="rp-src"><pre class="rp-gutter" aria-hidden="true"></pre><pre class="rp-lines" tabindex="0" aria-label="${esc(baseOf(st.file))}"></pre></div>` : nothing("This file is empty."));
    if (!count) return;
    box.querySelector(".rp-gutter").textContent = Array.from({ length: count }, (_, i) => i + 1).join("\n");
    box.querySelector(".rp-lines").textContent = lines.join("\n");
  }

  /* ------------------------------------------------------------ commits */

  async function loadCommits() {
    if (!st.hash) return;
    const key = `${st.repo.slug}|${st.hash}`;
    if (st.commits?.key === key && !st.commits.error) return;
    st.commits = { key, data: null, error: "" };
    paintCommits();
    try {
      const data = await api(`repo-commits?${query({ repo: st.repo.slug, hash: st.hash })}`);
      if (st.commits?.key !== key) return;
      st.commits.data = data;
    } catch (err) {
      if (st.commits?.key !== key) return;
      st.commits.error = err.message;
    }
    if (st.tab === "commits") paintCommits();
  }
  function paintCommits() {
    const pane = root.querySelector(".rp-pane");
    if (!pane || st.tab !== "commits") return;
    const c = st.commits;
    if (!st.hash) { pane.innerHTML = st.branchesError ? failed("Could not find the branch.", st.branchesError, "reopen") : waiting("Finding the branch"); return; }
    if (!c || c.key !== `${st.repo.slug}|${st.hash}`) { pane.innerHTML = waiting("Reading the commits"); return; }
    if (c.error) { pane.innerHTML = failed("Could not read the commits.", c.error, "retry-commits"); return; }
    if (!c.data) { pane.innerHTML = waiting("Reading the commits"); return; }
    const more = `${st.repo.url}/commits/branch/${enc(st.branch)}`;
    pane.innerHTML = `<p class="rp-sub">On <span class="rp-on">${esc(st.branch)}</span>, newest first.</p>
      ${c.data.commits.length ? `<ol class="commit-list rp-commits">${c.data.commits.map((x) => `<li>
        <button class="link id" type="button" data-external="${esc(`${st.repo.url}/commits/${x.hash}`)}" title="Open this commit on Bitbucket">${esc(x.hash.slice(0, 7))}</button>
        <span class="rp-commit-msg">${esc(x.message)}</span><span class="person">${esc(x.author)}</span>${when(x.date)}</li>`).join("")}</ol>` : nothing("No commits.")}
      ${c.data.more ? `<p class="rp-sub"><button class="link" type="button" data-external="${esc(more)}">Older commits on Bitbucket</button></p>` : ""}`;
  }

  /* ------------------------------------------------------------ pull requests */

  // Only what PixelDeck has already read: yours, the ones you review, and your team's.
  function openPrs(slug) {
    const pools = [...Object.values(state.people || {}).flatMap((d) => [d.open, d.review_requests]), ...(state.teamData?.members || []).map((m) => m.open)];
    const found = new Map();
    for (const pool of pools) for (const pr of pool || []) if (pr.repo === slug && pr.state === "OPEN") found.set(prKey(pr), pr);
    return [...found.values()].sort((a, b) => (b.updated_on || "").localeCompare(a.updated_on || ""));
  }
  function paintPrs() {
    const pane = root.querySelector(".rp-pane");
    if (!pane) return;
    const prs = openPrs(st.repo.slug);
    const web = `${st.repo.url}/pull-requests/`;
    const table = prs.length
      ? `<table class="queue rp-prs"><thead><tr><th class="col-state">State</th><th>Pull request</th><th class="col-who">Reviewers</th><th class="num">Threads</th><th class="num">Updated</th></tr></thead>
        <tbody>${prs.map((pr, i) => prRow(pr, { showAuthor: true, i })).join("")}</tbody></table>`
      : nothing("No open pull request here that PixelDeck has read.");
    pane.innerHTML = `<p class="rp-sub">Open pull requests PixelDeck has read: yours, the ones you review, and your team's. <button class="link" type="button" data-external="${esc(web)}">All on Bitbucket</button></p>${table}`;
  }

  /* ------------------------------------------------------------ events */

  function onClick(e) {
    const t = e.target;
    const open = t.closest("[data-rp-open]");
    if (open) return openRepo(open.dataset.rpOpen);
    const act = t.closest("[data-rp]")?.dataset.rp;
    if (act === "back") return back();
    if (act === "list") { showList(); return scrollTop(); }
    if (act === "refresh") return load({ force: true });
    if (act === "reopen" && st.repo) return openRepo(st.repo.slug);
    if (act === "retry-code") return loadCode();
    if (act === "retry-commits") { st.commits = null; return loadCommits(); }
    const tab = t.closest("[data-rp-tab]");
    if (tab) return setTab(tab.dataset.rpTab);
    const dir = t.closest("[data-rp-path]");
    if (dir) return goPath(dir.dataset.rpPath);
    const file = t.closest("[data-rp-file]");
    if (file) return goFile(file.dataset.rpFile);
    const branch = t.closest("[data-rp-branch]");
    if (branch) { st.tab = "code"; paintTabs(); paintPane(); setBranch(branch.dataset.rpBranch); scrollTop(); }
  }
  function onInput(e) {
    const t = e.target;
    if (t.matches("[data-rp-filter]")) { st.filter = t.value; paintList(); return; }
    if (t.matches("[data-rp-branch-filter]")) {
      st.branchFilter = t.value;
      paintBranchList();
      clearTimeout(searchTimer);
      if (searchText() && st.branches && !st.branches.complete) searchTimer = setTimeout(searchBranches, 350);
    }
  }
  function onKey(e) {
    if (e.key !== "Escape") return;
    const t = e.target;
    if (t.matches("[data-rp-filter], [data-rp-branch-filter]") && t.value) { t.value = ""; t.dispatchEvent(new Event("input", { bubbles: true })); }
  }
  function onChange(e) {
    if (e.target.matches("[data-rp-pick]") && st.repo && e.target.value && e.target.value !== st.branch) setBranch(e.target.value);
  }

  function mount(el) {
    root = el;
    root.classList.add("rp");
    root.addEventListener("click", onClick);
    root.addEventListener("input", onInput);
    root.addEventListener("keydown", onKey);
    root.addEventListener("change", onChange);
    document.addEventListener("change", (e) => { if (e.target?.id === "project") watchProject(e.target.value); });
    showList({ keep: true });
    // Nothing is read until the panel is first in view; each time it shows, the counts catch up.
    new IntersectionObserver((entries) => {
      if (!entries.some((x) => x.isIntersecting)) return;
      if (!st.list && !st.loading) load();
      else if (st.repo) paintTabs();
    }).observe(root);
  }
  return { mount };
})();
