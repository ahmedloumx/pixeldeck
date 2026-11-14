"use strict";

/* The Repos panel, a workbench. The side menu holds the project's repositories (each service
   stacked with its helm chart, the ones you open most first) or the open repository's file
   tree; the right pane holds the folder or the file, its editor, the branches, commits and
   pull requests. A divider between them drags; a narrow panel stacks them.
   The page drives terminals, so every string from Bitbucket goes through esc() or textContent.
   The panel listens on its own element with data-rp-* attributes: app.js owns data-action,
   .sec-chip and friends on the document. Pull request rows reuse its prRow and data-open. */

const Repos = (() => {
  const KEY = "prdesk.repos.v1";  // the last repository, and per repository where you were in it
  const USE_KEY = "prdesk.repos.use.v1";  // how often each repository was opened here, and when
  const UI_KEY = "prdesk.repos.ui.v1";  // the side's size, the filter, pins, open stacks and sections
  const TABS = [["code", "Code", "Code", "code"], ["branches", "Branches", "Branches", "branch"],
    ["commits", "Commits", "Commits", "commit"], ["prs", "Pull requests", "PRs", "pr"]];
  const KINDS = [["all", "All"], ["service", "Services"], ["helm", "Helm charts"], ["other", "Other"]];
  const KIND_NOTE = {
    service: "Services: repositories with a helm chart. Each one stacks its chart.",
    helm: "A chart named after a service plus -helm-chart deploys that service; the rest deploy infrastructure.",
    other: "Everything without a helm chart: tests, tools, jobs, libraries.",
  };
  const PROTECTED = ["main", "master", "develop"];
  const FILE_MAX = 512 * 1024;
  const GLYPH = {
    folder: [".###.....", "#####....", "#########", "#########", "#########", "#########", "#########"],
    file: ["####...", "#..##..", "#..#.#.", "#..####", "#.....#", "#.....#", "#.....#", "#######"],
    branch: [".#...#.", ".#...#.", ".#..#..", ".#.#...", ".##....", ".#.....", ".#....."],
    back: ["...#....", "..#.....", ".#......", "########", ".#......", "..#.....", "...#...."],
    code: ["..#...#.#..", ".#....#..#.", "#....#....#", ".#..#....#.", "..#.#...#.."],
    commit: ["....###....", "...#...#...", "####...####", "...#...#...", "....###...."],
    pr: ["#.....#", "#.....#", ".#...#.", "..#.#..", "...#...", "...#...", "...#..."],
    helm: [".....#.....", ".#...#...#.", "..#######..", "..##.#.##..", "..#.###.#..", "#####.#####",
      "..#.###.#..", "..##.#.##..", "..#######..", ".#...#...#.", ".....#....."],
    service: ["#########", "#.......#", "#.##....#", "#########", "#.......#", "#.##....#", "#########"],
    star: ["...#...", "...#...", "#######", ".#####.", "..###..", ".##.##.", "#.....#"],
    caret: ["#..", "##.", "###", "##.", "#.."],
    stack: [".#######.", ".........", "#########", ".........", "#########", "#########"],
    edit: ["......##", ".....###", "....###.", "...###..", "..###...", ".###....", "##......", "#......."],
    refresh: ["..#####..", ".#.....##", "#.....###", "#........", "#.......#", "........#", "###.....#", "##.....#.", "..#####.."],
  };
  const enc = encodeURIComponent;
  const query = (o) => Object.entries(o).map(([k, v]) => `${k}=${enc(v)}`).join("&");
  const webPath = (path) => path.split("/").map(enc).join("/");
  const parentOf = (path) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");
  const baseOf = (path) => path.slice(path.lastIndexOf("/") + 1);
  const gone = (err) => err.status === 404 || err.data?.upstream === 404;
  const isObj = (v) => !!v && typeof v === "object" && !Array.isArray(v);

  const read = (key) => { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage blocked */ } };
  const mem = (() => { const m = read(KEY); return isObj(m) && isObj(m.repos) ? m : { last: "", repos: {} }; })();
  const use = (() => { const u = read(USE_KEY); return isObj(u) ? u : {}; })();
  const ui = (() => {
    const u = read(UI_KEY);
    const out = { side: 300, sideH: 42, kind: "all", pins: [], open: {}, folds: {} };
    if (!isObj(u)) return out;
    if (Number.isFinite(u.side)) out.side = u.side;
    if (Number.isFinite(u.sideH)) out.sideH = u.sideH;
    if (KINDS.some(([k]) => k === u.kind)) out.kind = u.kind;
    if (Array.isArray(u.pins)) out.pins = u.pins.filter((s) => typeof s === "string").slice(0, 50);
    if (isObj(u.open)) out.open = u.open;
    if (isObj(u.folds)) out.folds = u.folds;
    return out;
  })();
  const saveUi = () => write(UI_KEY, ui);

  let root = null;
  let searchTimer = 0, armTimer = 0;
  const pending = new Map();  // tree requests in flight, so a folder is asked for once
  const st = {
    list: null, listError: "", listFor: null, loading: false, restored: false, filter: "", side: "repos",
    repo: null, tab: "code", branches: null, branchesError: "", branchFilter: "", branchHits: null,
    branch: "", hash: "", path: "", file: "", codeKey: "", branchesAt: 0, refreshing: false, prSig: "",
    trees: new Map(), dirs: new Set(), badTrees: new Set(), treeError: "", fileData: null, fileError: "", readme: null, commits: null,
    edit: null,
  };

  // Where you were: the last repository, the side menu, and per repository its branch,
  // folder, file, tab and the folders open in its tree.
  function remember() {
    if (st.repo) {
      const slug = st.repo.slug;
      delete mem.repos[slug];
      mem.repos[slug] = { branch: st.branch, path: st.path, file: st.file, tab: st.tab, dirs: [...st.dirs].slice(-60) };
      mem.last = slug;
    } else mem.last = "";
    mem.side = st.side;
    const keys = Object.keys(mem.repos);
    for (const k of keys.slice(0, Math.max(0, keys.length - 40))) delete mem.repos[k];
    write(KEY, mem);
  }
  function touch(slug) {
    const u = use[slug] || {};
    use[slug] = { n: (u.n || 0) + 1, at: Date.now() };
    const keys = Object.keys(use);
    if (keys.length > 300) for (const k of keys.sort((a, b) => (use[a].at || 0) - (use[b].at || 0)).slice(0, keys.length - 300)) delete use[k];
    write(USE_KEY, use);
  }
  // Most opened first, then the most recently opened, then the latest change on Bitbucket.
  // A service's stack ranks by the busier of the service and its charts.
  function usage(r, withCharts) {
    const slugs = withCharts ? [r.slug, ...(r.charts || [])] : [r.slug];
    let n = 0, at = 0;
    for (const s of slugs) { n = Math.max(n, use[s]?.n || 0); at = Math.max(at, use[s]?.at || 0); }
    return { n, at, updated: r.updated_on || "" };
  }
  const byUse = (stacked) => (a, b) => {
    const x = usage(a, stacked), y = usage(b, stacked);
    return y.n - x.n || y.at - x.at || y.updated.localeCompare(x.updated);
  };

  /* ------------------------------------------------------------ markup */

  function glyph(name, cls = "") {
    const rows = GLYPH[name];
    let rects = "";
    rows.forEach((row, y) => [...row].forEach((c, x) => { if (c === "#") rects += `<rect x="${x}" y="${y}" width="1" height="1"/>`; }));
    const w = rows[0].length, h = rows.length;
    return `<svg class="rp-glyph rp-glyph--${name}${cls ? ` ${cls}` : ""}" viewBox="0 0 ${w} ${h}" width="${w * 2}" height="${h * 2}" fill="currentColor" shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`;
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
  const repoOf = (slug) => st.list?.repos.find((r) => r.slug === slug) || null;
  const scrollMain = () => root.querySelector(".rp-main")?.scrollTo({ top: 0 });

  /* ------------------------------------------------------------ the frame */

  function frame() {
    root.innerHTML = `<div class="rp-bench" style="--rp-side:${ui.side}px;--rp-side-h:${ui.sideH}%">
      <aside class="rp-side" aria-label="Repositories and files">
        <div class="rp-side-tabs" role="tablist" aria-label="Side menu"></div>
        <div class="rp-side-body"></div>
      </aside>
      <div class="rp-split" role="separator" tabindex="0" aria-label="Resize the side menu" data-tip="Drag to resize"></div>
      <section class="rp-main" aria-label="Repository"></section></div>`;
    const split = root.querySelector(".rp-split");
    split.addEventListener("pointerdown", onSplitDown);
    split.addEventListener("keydown", onSplitKey);
    paintSide();
    paintMain();
  }
  // One divider for both shapes: a tall bar between columns sets the side's width, a wide bar
  // between the stacked panes (a narrow panel) sets the side's share of the height.
  function onSplitDown(e) {
    const split = e.currentTarget, bench = split.parentElement;
    const across = split.offsetHeight > split.offsetWidth;
    const box = bench.getBoundingClientRect();
    split.setPointerCapture(e.pointerId);
    split.classList.add("is-dragging");
    e.preventDefault();
    const move = (ev) => {
      if (across) {
        ui.side = Math.round(Math.min(box.width - 280, Math.max(180, ev.clientX - box.left)));
        bench.style.setProperty("--rp-side", `${ui.side}px`);
      } else {
        ui.sideH = Math.round(Math.min(80, Math.max(15, ((ev.clientY - box.top) / box.height) * 100)));
        bench.style.setProperty("--rp-side-h", `${ui.sideH}%`);
      }
    };
    const up = () => {
      split.removeEventListener("pointermove", move);
      split.removeEventListener("pointerup", up);
      split.removeEventListener("pointercancel", up);
      split.classList.remove("is-dragging");
      saveUi();
    };
    split.addEventListener("pointermove", move);
    split.addEventListener("pointerup", up);
    split.addEventListener("pointercancel", up);
  }
  function onSplitKey(e) {
    const split = e.currentTarget, bench = split.parentElement;
    const across = split.offsetHeight > split.offsetWidth;
    const step = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[e.key];
    if (!step) return;
    e.preventDefault();
    if (across) { ui.side = Math.min(bench.clientWidth - 280, Math.max(180, ui.side + step * 24)); bench.style.setProperty("--rp-side", `${ui.side}px`); }
    else { ui.sideH = Math.min(80, Math.max(15, ui.sideH + step * 4)); bench.style.setProperty("--rp-side-h", `${ui.sideH}%`); }
    saveUi();
  }

  /* ------------------------------------------------------------ the side menu */

  function paintSide() {
    const tabs = root.querySelector(".rp-side-tabs");
    if (!tabs) return;
    if (st.side === "files" && !st.repo) st.side = "repos";
    tabs.innerHTML = [["repos", "Repos", "stack"], ["files", "Files", "folder"]].map(([id, label, icon]) => `
      <button class="rp-stab" type="button" role="tab" aria-selected="${st.side === id}" data-rp-side="${id}"${id === "files" && !st.repo ? " disabled" : ""}>
        ${glyph(icon)}<span>${label}</span></button>`).join("");
    const body = root.querySelector(".rp-side-body");
    if (st.side === "files") {
      body.innerHTML = `<div class="rp-files-head"></div><ul class="rp-ftree" role="tree" aria-label="Files"></ul>`;
      paintTree();
      return;
    }
    body.innerHTML = `<div class="rp-list-head"><span class="rp-list-title">Repositories</span><span class="note rp-note"></span>
        <button class="rp-icon-btn" type="button" data-rp="refresh" aria-label="Read the list again" data-tip="Read the list again from Bitbucket">${glyph("refresh")}</button></div>
      <label class="field rp-search"><span class="visually-hidden">Search repositories</span>
        <input type="search" data-rp-filter placeholder="Search repositories" autocomplete="off" spellcheck="false"></label>
      <div class="rp-kinds" role="group" aria-label="Show"></div><p class="rp-kind-note"></p>
      <div class="rp-results"></div>`;
    body.querySelector("[data-rp-filter]").value = st.filter;
    paintList();
  }
  function paintList() {
    const box = root.querySelector(".rp-results");
    if (!box) return;
    const note = root.querySelector(".rp-note");
    const kinds = root.querySelector(".rp-kinds");
    if (st.listError && !st.list) { box.innerHTML = failed("Could not read the repositories.", st.listError, "refresh"); return; }
    if (!st.list) { note.textContent = ""; kinds.innerHTML = ""; box.innerHTML = waiting("Reading the repositories"); return; }
    const all = st.list.repos;
    const count = (k) => (k === "all" ? all.length : all.filter((r) => r.kind === k).length);
    kinds.innerHTML = KINDS.map(([k, label]) => `<button class="date-chip rp-kind" type="button" data-rp-kind="${k}" aria-pressed="${ui.kind === k}">${label}<span class="rp-kind-n">${count(k)}</span></button>`).join("");
    root.querySelector(".rp-kind-note").textContent = KIND_NOTE[ui.kind] || "";
    const where = st.list.project ? st.list.project.name || st.list.project.key : "every project";
    note.textContent = where;
    const needle = st.filter.trim().toLowerCase();
    const fits = (r) => ui.kind === "all" || r.kind === ui.kind;
    const pinned = new Set(ui.pins);
    let html;
    if (needle) {
      // A search shows each match on its own row, whatever stack it sits in.
      const hits = all.filter((r) => fits(r) && `${r.name}\n${r.slug}\n${r.description}`.toLowerCase().includes(needle)).sort(byUse(false));
      html = hits.length ? `<ul class="rp-repos">${hits.map((r) => `<li class="rp-item">${row(r, { kindTag: true })}</li>`).join("")}</ul>` : nothing("No repository matches the search.");
    } else {
      const pins = ui.pins.map(repoOf).filter((r) => r && fits(r));
      html = section("pinned", "Pinned", pins.map((r) => `<li class="rp-item">${row(r)}</li>`));
      if (ui.kind === "helm") {
        const charts = all.filter((r) => r.kind === "helm" && !pinned.has(r.slug)).sort(byUse(false));
        html += section("charts-of-services", "Charts of a service", charts.filter((r) => r.service).map((r) => `<li class="rp-item">${row(r)}</li>`));
        html += section("charts-of-infra", "Infrastructure charts", charts.filter((r) => !r.service).map((r) => `<li class="rp-item">${row(r)}</li>`));
      } else {
        // A chart that deploys a listed service rides in that service's stack, not on its own row.
        const units = all.filter((r) => fits(r) && !pinned.has(r.slug) && !(r.kind === "helm" && r.service && repoOf(r.service))).sort(byUse(true));
        html += section(pins.length ? `rest-${ui.kind}` : "", pins.length ? "Everything else" : "", units.map((r) => (r.charts?.length ? stack(r) : `<li class="rp-item">${row(r)}</li>`)));
      }
      if (!html) html = nothing("No repositories of this kind here.");
    }
    box.innerHTML = html + (st.list.total > all.length ? `<p class="rp-sub">The newest ${all.length} of ${st.list.total}.</p>` : "");
  }
  function section(key, title, rows) {
    if (!rows.length) return "";
    const cls = `rp-repos${rows.some((r) => r.startsWith('<li class="rp-stack')) ? " has-stacks" : ""}`;
    if (!title) return `<ul class="${cls}">${rows.join("")}</ul>`;
    const open = ui.folds[key] !== false;
    return `<section class="rp-sec"><button class="rp-sec-head" type="button" data-rp-sec="${esc(key)}" aria-expanded="${open}">
      ${glyph("caret", "rp-caret")}<span>${esc(title)}</span><span class="sec-n">${rows.length}</span></button>
      <ul class="${cls}"${open ? "" : " hidden"}>${rows.join("")}</ul></section>`;
  }
  function row(r, { child = false, kindTag = false, badge = "" } = {}) {
    const pinned = ui.pins.includes(r.slug);
    const n = use[r.slug]?.n || 0;
    const tag = kindTag ? `<span class="rp-lang">${r.kind === "helm" ? "chart" : r.kind === "service" ? "service" : "other"}</span>` : "";
    return `<button class="rp-repo${st.repo?.slug === r.slug ? " is-open" : ""}" type="button" data-rp-open="${esc(r.slug)}">
        <span class="rp-repo-top">${r.kind === "helm" ? glyph("helm") : ""}<span class="rp-repo-name">${esc(r.name)}</span>${tag}${when(r.updated_on)}</span>
        ${r.description && !child ? `<span class="rp-repo-desc">${esc(r.description)}</span>` : ""}
        <span class="rp-repo-foot">${r.mainbranch ? `<span class="rp-repo-main">${glyph("branch")}${esc(r.mainbranch)}</span>` : ""}${badge}
          ${n ? `<span class="rp-uses" data-tip="Opened ${n} ${n === 1 ? "time" : "times"} here">${n}x</span>` : ""}</span>
      </button>
      <button class="rp-pin${pinned ? " is-on" : ""}" type="button" data-rp-pin="${esc(r.slug)}" aria-pressed="${pinned}"
        aria-label="${pinned ? "Unpin" : "Pin"} ${esc(r.name)}" data-tip="${pinned ? "Unpin" : "Pin to the top"}">${glyph("star")}</button>`;
  }
  // A service and its helm chart, stacked like cards: folded, the chart peeks out under it.
  function stack(r) {
    const charts = r.charts.map(repoOf).filter(Boolean);
    const open = !!ui.open[r.slug];
    const badge = `<span class="rp-chart-badge">${glyph("helm")}${charts.length > 1 ? `${charts.length} charts` : "chart"}</span>`;
    return `<li class="rp-stack${open ? " is-open" : ""}">
      <div class="rp-item rp-stack-head">
        <button class="rp-fold" type="button" data-rp-stack="${esc(r.slug)}" aria-expanded="${open}" aria-label="${open ? "Fold" : "Unfold"} ${esc(r.name)} and its chart">${glyph("caret", "rp-caret")}</button>
        ${row(r, { badge: open ? "" : badge })}</div>
      <ul class="rp-stack-items"${open ? "" : " hidden"}>${charts.map((c) => `<li class="rp-item is-child">${row(c, { child: true })}</li>`).join("")}</ul></li>`;
  }

  /* ------------------------------------------------------------ the file tree */

  // A folder's listing, asked for once per commit. The answers land in st.trees, which the
  // tree on the side and the folder view on the right share.
  function getTree(path) {
    const at = `${st.repo.slug}|${st.hash}`;
    if (st.trees.has(path)) return Promise.resolve(st.trees.get(path));
    const key = `${at}|${path}`;
    if (pending.has(key)) return pending.get(key);
    const ask = api(`repo-tree?${query({ repo: st.repo.slug, hash: st.hash, path })}`)
      .then((t) => { if (`${st.repo?.slug}|${st.hash}` === at) st.trees.set(path, t); return t; })
      .finally(() => pending.delete(key));
    pending.set(key, ask);
    return ask;
  }
  function needTree(path) {
    if (!st.repo || !st.hash || pending.has(`${st.repo.slug}|${st.hash}|${path}`) || st.badTrees.has(path)) return;
    getTree(path).then(() => paintTree()).catch(() => { st.badTrees.add(path); st.dirs.delete(path); paintTree(); });
  }
  function setHash(hash) {
    if (hash === st.hash) return;
    st.hash = hash;
    st.trees = new Map();
    st.badTrees = new Set();
  }
  function reveal(path) {
    let at = "";
    for (const part of path ? path.split("/") : []) { at = at ? `${at}/${part}` : part; st.dirs.add(at); }
  }
  function paintTree() {
    const box = root.querySelector(".rp-ftree");
    if (!box || !st.repo) return;
    root.querySelector(".rp-files-head").innerHTML = `<span class="rp-files-repo">${esc(st.repo.name)}</span>${st.branch ? `<span class="rp-repo-main">${glyph("branch")}${esc(st.branch)}</span>` : ""}`;
    if (!st.hash) { box.innerHTML = `<li>${st.branchesError ? failed("Could not find the branch.", st.branchesError, "reopen") : waiting("Finding the branch")}</li>`; return; }
    box.innerHTML = treeRows("", 0);
  }
  function treeRows(path, depth) {
    const t = st.trees.get(path);
    if (!t && st.badTrees.has(path)) return `<li class="rp-tnode is-empty" style="--d:${depth}">could not read this folder</li>`;
    if (!t) { needTree(path); return `<li class="rp-tnode is-wait" style="--d:${depth}"><span class="rp-dots" aria-label="Reading"></span></li>`; }
    if (!t.entries.length) return `<li class="rp-tnode is-empty" style="--d:${depth}">empty</li>`;
    return t.entries.map((e) => {
      const dir = e.type === "dir", open = dir && st.dirs.has(e.path);
      const here = st.file ? st.file === e.path : dir && st.path === e.path;
      return `<li class="rp-tnode" role="treeitem"${dir ? ` aria-expanded="${open}"` : ""}${here ? ' aria-current="true"' : ""}>
        <div class="rp-trow${here ? " is-current" : ""}" style="--d:${depth}">
          ${dir ? `<button class="rp-tfold" type="button" data-rp-dir="${esc(e.path)}" aria-expanded="${open}" aria-label="${open ? "Fold" : "Unfold"} ${esc(e.name)}">${glyph("caret", "rp-caret")}</button>` : `<span class="rp-tfold-pad"></span>`}
          <button class="rp-tname" type="button" ${dir ? `data-rp-path="${esc(e.path)}"` : `data-rp-file="${esc(e.path)}"`}>${glyph(dir ? "folder" : "file")}<span>${esc(e.name)}</span></button>
        </div>${open ? `<ul role="group">${treeRows(e.path, depth + 1)}</ul>` : ""}</li>`;
    }).join("");
  }

  /* ------------------------------------------------------------ the list */

  async function load({ force = false } = {}) {
    if (st.loading) return;
    st.loading = true;
    st.listError = "";
    paintList();
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
      if (mem.last && repoOf(mem.last)) return openRepo(mem.last, { restore: true });
    }
    if (st.repo && !repoOf(st.repo.slug) && !st.edit) { st.repo = null; st.side = "repos"; remember(); paintSide(); return paintMain(); }
    if (st.repo) st.repo = repoOf(st.repo.slug) || st.repo;  // its pairing may have changed
    paintList();
    if (!st.repo) paintMain();
  }
  // app.js switches the project without telling the panels: wait for its answer, then reload.
  function watchProject(want) {
    if (want === st.listFor) return;
    const started = Date.now();
    const tick = () => {
      if ((state.project?.key || "") === want) { st.list = null; st.listError = ""; return load(); }
      if (Date.now() - started < 15000) setTimeout(tick, 250);
    };
    setTimeout(tick, 250);
  }
  function closeRepo() {
    if (guardEdit()) return;
    st.repo = null;
    st.side = "repos";
    remember();
    paintSide();
    paintMain();
  }

  /* ------------------------------------------------------------ one repository */

  async function openRepo(slug, { restore = false } = {}) {
    if (guardEdit()) return;
    const repo = repoOf(slug);
    if (!repo) return closeRepo();
    if (!restore) touch(slug);
    const m = mem.repos[slug] || {};
    Object.assign(st, {
      repo, tab: restore && TABS.some(([id]) => id === m.tab) ? m.tab : "code", side: restore && mem.side === "repos" ? "repos" : "files",
      branches: null, branchesError: "", branchFilter: "", branchHits: null, prSig: "",
      branch: m.branch || repo.mainbranch || "", hash: "", path: m.path || "", file: m.file || "", codeKey: "",
      trees: new Map(), badTrees: new Set(), dirs: new Set(Array.isArray(m.dirs) ? m.dirs.filter((d) => typeof d === "string") : []),
      treeError: "", fileData: null, fileError: "", readme: null, commits: null, edit: null,
    });
    reveal(st.path);
    remember();
    paintSide();
    paintMain();
    scrollMain();
    try {
      const b = await api(`repo-branches?${query({ repo: slug })}`);
      if (st.repo?.slug !== slug) return;
      st.branches = b;
      st.branchesAt = Date.now();
    } catch (err) {
      if (st.repo?.slug !== slug) return;
      st.branchesError = err.message;
    }
    paintTabs();
    if (st.tab === "branches") paintBranchList();
    if (st.branchesError) { paintTree(); return paintPane(); }
    if (!st.branch && st.branches.branches.length) st.branch = st.branches.branches[0].name;
    if (!st.branch) { st.branchesError = "This repository has no branches yet."; paintTree(); return paintPane(); }
    setBranch(st.branch);
  }
  function paintMain() {
    const main = root.querySelector(".rp-main");
    if (!main) return;
    if (!st.repo) { main.innerHTML = welcome(); return; }
    const r = st.repo;
    const chart = r.charts?.[0], service = r.service;
    const jump = chart
      ? `<button class="rp-jump" type="button" data-rp-jump="${esc(chart)}" aria-label="Open its helm chart, ${esc(chart)}" data-tip="Its helm chart: ${esc(chart)}">${glyph("helm")}<span>Helm chart</span></button>`
      : service && repoOf(service)
        ? `<button class="rp-jump" type="button" data-rp-jump="${esc(service)}" aria-label="Open the service it deploys, ${esc(service)}" data-tip="The service it deploys: ${esc(service)}">${glyph("service")}<span>Service</span></button>`
        : "";
    main.innerHTML = `<div class="rp-view">
      <nav class="rp-crumbs" aria-label="Where you are">
        <button class="rp-back" type="button" data-rp="back" aria-label="Back" data-tip="Back">${glyph("back")}</button>
        <button class="link" type="button" data-rp="list">Repositories</button><span aria-hidden="true">/</span><span class="rp-here">${esc(r.slug)}</span></nav>
      <header class="rp-head"><h2 class="panel-title">${r.kind === "helm" ? glyph("helm", "rp-title-glyph") : ""}${esc(r.name)}</h2>
        <span class="rp-head-meta">${r.mainbranch ? `<span class="rp-repo-main" title="Main branch">${glyph("branch")}${esc(r.mainbranch)}</span>` : ""}
        ${r.language ? `<span class="rp-lang">${esc(r.language)}</span>` : ""}${jump}
        <button class="link" type="button" data-external="${esc(r.url)}">Bitbucket</button></span></header>
      ${r.description ? `<p class="rp-desc">${esc(r.description)}</p>` : ""}
      <div class="rp-tabs" role="tablist" aria-label="Repository views"></div>
      <div class="rp-pane" role="tabpanel"></div></div>`;
    paintTabs();
    paintPane();
  }
  function welcome() {
    const top = st.list ? st.list.repos.filter((r) => use[r.slug]?.n).sort(byUse(false)).slice(0, 8) : [];
    return `<div class="rp-welcome">${Sprites.svg("buddy", { scale: 3 })}
      <h2 class="panel-title">Pick a repository</h2>
      <p class="rp-sub">The side menu stacks each service with its helm chart and puts the ones you open most first. The star pins a favourite to the top.</p>
      ${top.length ? `<h3 class="rp-welcome-h">Opened most here</h3><ul class="rp-quick">${top.map((r) => `<li><button class="rp-quick-btn" type="button" data-rp-open="${esc(r.slug)}">
        ${glyph(r.kind === "helm" ? "helm" : "folder")}<span>${esc(r.name)}</span><span class="rp-uses">${use[r.slug].n}x</span></button></li>`).join("")}</ul>` : ""}</div>`;
  }
  function paintTabs() {
    const box = root.querySelector(".rp-tabs");
    if (!box || !st.repo) return;
    const n = { branches: st.branches ? st.branches.total : "", prs: openPrs(st.repo.slug).length || "" };
    box.innerHTML = TABS.map(([id, long, short, icon]) => `<button class="rp-tab${st.tab === id ? " is-current" : ""}" type="button" role="tab" aria-selected="${st.tab === id}" data-rp-tab="${id}">
      ${glyph(icon)}<span class="rp-tab-label"><span class="sec-long">${long}</span><span class="sec-short">${short}</span></span>${n[id] !== undefined && n[id] !== "" ? `<span class="sec-n">${esc(n[id])}</span>` : ""}</button>`).join("");
  }
  function setTab(id) {
    if (!TABS.some(([t]) => t === id) || !st.repo) return;
    st.tab = id;
    remember();
    paintTabs();
    paintPane();
    refreshBranches();
  }
  // Branch tips move with every push: past a minute, read them again, and follow the current
  // branch if its tip moved. An open edit keeps the commit it was opened at.
  async function refreshBranches() {
    if (!st.repo || !st.branches || st.refreshing || Date.now() - st.branchesAt < 60000) return;
    const slug = st.repo.slug;
    st.refreshing = true;
    let fresh;
    try { fresh = await api(`repo-branches?${query({ repo: slug })}`); } catch { return; } finally { st.refreshing = false; }
    if (st.repo?.slug !== slug) return;
    st.branches = fresh;
    st.branchesAt = Date.now();
    paintTabs();
    const tip = fresh.branches.find((b) => b.name === st.branch);
    if (tip && st.hash && tip.hash !== st.hash && !st.edit) {
      setHash(tip.hash);
      paintTree();
      if (st.tab === "code") { paintPicker(); loadCode(); }
      else if (st.tab === "commits") { paintCommits(); loadCommits(); }
      else if (st.tab === "branches") paintBranchList();
    } else if (st.tab === "code") paintPicker();
    else if (st.tab === "branches") paintBranchList();
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
        paintTree();
        return paintPane();
      }
      if (st.repo?.slug !== slug) return;
    }
    st.branch = hit.name;
    setHash(hit.hash);
    remember();
    paintTree();
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
    if (st.edit) return paintCode();  // an open edit keeps its draft and the commit it started from
    const at = codeKey();
    st.codeKey = at;
    st.treeError = st.fileError = "";
    if (st.file) {
      st.fileData = null;
      reveal(parentOf(st.file));
      paintCode();
      paintTree();
      try {
        const d = await api(`repo-file?${query({ repo: st.repo.slug, hash: st.hash, path: st.file })}`);
        if (at !== codeKey()) return;
        st.fileData = d;
      } catch (err) {
        if (at !== codeKey()) return;
        if (gone(err)) { st.file = ""; remember(); return loadCode(); }  // gone at this commit: its folder instead
        st.fileError = err.message;
      }
      return paintCode();
    }
    st.readme = null;
    reveal(st.path);
    paintCode();
    paintTree();
    let tree;
    try { tree = await getTree(st.path); } catch (err) {
      if (at !== codeKey()) return;
      if (gone(err) && st.path) { st.dirs.delete(st.path); st.path = parentOf(st.path); remember(); return loadCode(); }  // one level up
      st.treeError = err.message;
      return paintCode();
    }
    if (at !== codeKey()) return;
    paintCode();
    paintTree();
    if (!tree.readme) return;
    try {
      const d = await api(`repo-file?${query({ repo: st.repo.slug, hash: st.hash, path: tree.readme })}`);
      if (at !== codeKey()) return;
      st.readme = d;
      paintReadme();
    } catch { /* the listing stands without it */ }
  }
  function goPath(path) {
    if (guardEdit()) return;
    if (st.tab !== "code") { st.tab = "code"; paintTabs(); }
    st.path = path;
    st.file = "";
    if (path) st.dirs.add(path);
    remember();
    if (!root.querySelector(".rp-code")) paintPane(); else loadCode();
    scrollMain();
  }
  function goFile(path) {
    if (guardEdit()) return;
    if (st.tab !== "code") { st.tab = "code"; paintTabs(); }
    st.path = parentOf(path);
    st.file = path;
    remember();
    if (!root.querySelector(".rp-code")) paintPane(); else loadCode();
    scrollMain();
  }
  function back() {
    if (st.repo && st.tab === "code" && st.file) return goPath(st.path);
    if (st.repo && st.tab === "code" && st.path) return goPath(parentOf(st.path));
    closeRepo();
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
    if (st.edit && st.edit.path === st.file) return paintEditor(box);
    if (st.file) return paintFile(box);
    if (st.treeError) { box.innerHTML = failed("Could not read this folder.", st.treeError, "retry-code"); return; }
    const tree = st.trees.get(st.path);
    if (!tree) { box.innerHTML = waiting("Reading the folder"); return; }
    const up = st.path ? `<li><button class="rp-entry is-up" type="button" data-rp-path="${esc(parentOf(st.path))}" aria-label="Up one folder">${glyph("folder")}<span class="rp-entry-name">..</span></button></li>` : "";
    const rows = tree.entries.map((e) => `<li><button class="rp-entry is-${e.type}" type="button" ${e.type === "dir" ? `data-rp-path="${esc(e.path)}"` : `data-rp-file="${esc(e.path)}"`}>
      ${glyph(e.type === "dir" ? "folder" : "file")}<span class="rp-entry-name">${esc(e.name)}</span>${e.type === "file" ? `<span class="rp-size">${esc(size(e.size))}</span>` : ""}</button></li>`).join("");
    const web = `${st.repo.url}/src/${st.hash}/${st.path ? `${webPath(st.path)}/` : ""}`;
    box.innerHTML = `<ul class="rp-tree">${up}${rows}</ul>
      ${tree.entries.length ? "" : nothing("This folder is empty.")}
      ${tree.truncated ? `<p class="rp-sub">The first ${tree.entries.length} entries only. <button class="link" type="button" data-external="${esc(web)}">The rest on Bitbucket</button></p>` : ""}
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
  function fileHead(d, extra) {
    const web = `${st.repo.url}/src/${st.hash}/${webPath(st.file)}`;
    return `<div class="rp-file-head"><span class="rp-file-name">${glyph("file")}<span>${esc(baseOf(st.file))}</span></span>
      <span class="figure-cell">${esc(size(d.size))}</span>${extra}<button class="link" type="button" data-external="${esc(web)}">Bitbucket</button></div>`;
  }
  function paintFile(box) {
    const web = `${st.repo.url}/src/${st.hash}/${webPath(st.file)}`;
    if (st.fileError) { box.innerHTML = failed("Could not read this file.", st.fileError, "retry-code"); return; }
    const d = st.fileData;
    if (!d) { box.innerHTML = waiting("Reading the file"); return; }
    if (d.binary || d.text == null) {
      const what = d.attributes?.includes("lfs") ? "Stored in Git LFS" : d.attributes?.includes("subrepository") ? "A submodule" : "A binary file";
      box.innerHTML = fileHead(d, "") + `<div class="rp-none rp-binary">${Sprites.svg("buddy", { scale: 2 })}<span>${what}, not shown here. <button class="link" type="button" data-external="${esc(web)}">Open it on Bitbucket</button></span></div>`;
      return;
    }
    const text = d.text.replace(/\r\n?/g, "\n");
    const lines = text.endsWith("\n") ? text.slice(0, -1).split("\n") : text.split("\n");
    const count = text ? lines.length : 0;
    const edit = d.locked
      ? `<button class="btn btn--quiet rp-edit-btn" type="button" aria-disabled="true" data-rp="edit-locked" data-tip="${esc(d.locked)}">${glyph("edit")}Edit</button>`
      : `<button class="btn btn--quiet rp-edit-btn" type="button" data-rp="edit" data-tip="Edit this file, then commit it">${glyph("edit")}Edit</button>`;
    box.innerHTML = fileHead(d, `<span class="figure-cell">${esc(plural(count, "line"))}</span>${edit}`)
      + (d.cut ? `<p class="rp-cut">Cut at 512 KB: the rest is on Bitbucket.</p>` : "")
      + (count ? `<div class="rp-src"><pre class="rp-gutter" aria-hidden="true"></pre><pre class="rp-lines" tabindex="0" aria-label="${esc(baseOf(st.file))}"></pre></div>` : nothing("This file is empty."));
    if (!count) return;
    box.querySelector(".rp-gutter").textContent = Array.from({ length: count }, (_, i) => i + 1).join("\n");
    box.querySelector(".rp-lines").textContent = lines.join("\n");
  }

  /* ------------------------------------------------------------ the editor */

  const guarded = (branch) => PROTECTED.includes(branch) || branch === st.repo?.mainbranch;
  function suggestName(path) {
    const stem = baseOf(path).replace(/\.[^.]*$/, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "file";
    return `edit/${stem.slice(0, 40)}-${Math.random().toString(36).slice(2, 6)}`;
  }
  function badBranch(name) {
    if (!name) return "Name the new branch.";
    if (/[\x00-\x20\x7f~^:?*[\\"]/.test(name) || name.startsWith("-") || name.startsWith("/") || /(\/|\.|\.lock)$/.test(name) || name.includes("..") || name.includes("//") || name.includes("@{"))
      return "A branch name has no spaces, quotes or ~^:?*[\\, and no '..'.";
    if (guarded(name)) return `A new branch cannot be called ${name}.`;
    return "";
  }
  function startEdit() {
    const d = st.fileData;
    if (!d || d.locked || d.text == null || !st.branch || st.hash.length !== 40) return;
    const text = d.text.replace(/\r\n/g, "\n");  // a textarea holds LF; CRLF files get theirs back on commit
    st.edit = {
      repo: st.repo.slug, path: st.file, branch: st.branch, parent: st.hash, endings: d.endings, original: text, text,
      stage: "edit", where: "new", name: suggestName(st.file), message: `Update ${baseOf(st.file)}`,
      armed: false, busy: false, error: "", done: null, discard: false,
    };
    paintCode();
    root.querySelector(".rp-ed-text")?.focus({ preventScroll: true });
  }
  const dirty = () => !!st.edit && !st.edit.done && st.edit.text !== st.edit.original;
  // An edit not yet committed holds the panel: moving away would lose it.
  function guardEdit() {
    if (!dirty()) { st.edit = null; return false; }  // nothing unsaved: a clean or committed edit just closes
    toast("Your edit is not committed.", "Commit it, or cancel it, before moving on.", true);
    if (st.tab !== "code") setTab("code");
    root.querySelector(".rp-ed-text, [data-rp-msg]")?.focus({ preventScroll: true });
    return true;
  }
  function paintEditor(box) {
    const e = st.edit;
    const lines = (e.text.match(/\n/g) || []).length + 1;
    const where = e.where === "new" ? e.name.trim() || "a new branch" : e.branch;
    const head = `<div class="rp-file-head is-editing"><span class="rp-file-name">${glyph("edit")}<span>${esc(baseOf(e.path))}</span></span>
      <span class="rp-editing">editing at <span class="id">${esc(e.parent.slice(0, 7))}</span> on <span class="rp-on">${esc(e.branch)}</span></span>
      <span class="figure-cell rp-ed-count">${esc(plural(lines, "line"))}</span>
      ${e.stage === "done" ? "" : `<button class="btn btn--quiet${e.discard ? " btn--armed" : ""}" type="button" data-rp="edit-cancel">${e.discard ? "Confirm: discard" : "Cancel"}</button>
      ${e.stage === "edit" ? `<button class="btn btn--primary" type="button" data-rp="edit-commit"${dirty() ? "" : " disabled"}>Commit</button>` : ""}`}</div>`;
    if (e.stage === "done") {
      const out = e.done;
      const pr = !out.new_branch ? ""
        : out.pr?.flow === "preview" ? `<button class="btn btn--primary" type="button" data-rp="open-pr">Open a pull request</button>`
          : `<button class="btn btn--primary" type="button" data-external="${esc(out.pr?.url || `${st.repo.url}/pull-requests/new`)}">Open a pull request on Bitbucket</button>`;
      box.innerHTML = head + `<div class="rp-done" role="status">${Sprites.svg("buddy", { scale: 3 })}<div>
        <p>Committed to <span class="rp-on">${esc(out.branch)}</span>${out.hash ? ` as <span class="id">${esc(out.hash.slice(0, 7))}</span>` : ""}.${out.new_branch ? ` A new branch from ${esc(out.from)}.` : ""}</p>
        <div class="rp-done-actions">${pr}<button class="btn" type="button" data-rp="edit-close">See the file on ${esc(out.branch)}</button></div></div></div>`;
      return;
    }
    box.innerHTML = head + `<div class="rp-editor${e.stage === "commit" ? " is-locked" : ""}"><pre class="rp-ed-gutter" aria-hidden="true"></pre>
      <textarea class="rp-ed-text" wrap="off" spellcheck="false" autocomplete="off" autocapitalize="off" aria-label="Edit ${esc(baseOf(e.path))}"${e.stage === "commit" ? " readonly" : ""}></textarea></div>
      ${e.stage === "commit" ? `<form class="rp-commit" data-rp-form novalidate>
        <h3 class="rp-commit-title">Commit</h3>
        <label class="rp-field"><span>Message</span><textarea data-rp-msg rows="3" spellcheck="true"></textarea></label>
        <fieldset class="rp-where"><legend>Where it goes</legend>
          <label class="rp-radio"><input type="radio" name="rp-where" value="new"${e.where === "new" ? " checked" : ""}> A new branch from <span class="rp-on">${esc(e.branch)}</span></label>
          <label class="field rp-newname"><span class="visually-hidden">New branch name</span><input type="text" data-rp-newname autocomplete="off" spellcheck="false"${e.where === "new" ? "" : " disabled"}></label>
          <label class="rp-radio${guarded(e.branch) ? " is-off" : ""}"><input type="radio" name="rp-where" value="current"${e.where === "current" ? " checked" : ""}${guarded(e.branch) ? " disabled" : ""}> Straight to <span class="rp-on">${esc(e.branch)}</span></label>
          ${guarded(e.branch) ? `<p class="rp-guard">PixelDeck never commits straight to ${esc(e.branch)}: a new branch, then a pull request.</p>` : ""}
        </fieldset>
        <p class="rp-commit-error" role="alert"${e.error ? "" : " hidden"}>${esc(e.error)}</p>
        <div class="rp-commit-foot"><button class="btn btn--quiet" type="button" data-rp="edit-back">Back to the edit</button><span class="spacer"></span>
          <button class="btn ${e.armed ? "btn--armed" : "btn--primary"}${e.busy ? " is-busy" : ""}" type="button" data-rp="commit-go"${e.busy ? " disabled" : ""}><span class="spin" aria-hidden="true"></span>${esc(e.armed ? `Confirm: commit to ${where}` : "Commit")}</button></div>
      </form>` : ""}`;
    const ta = box.querySelector(".rp-ed-text"), gutter = box.querySelector(".rp-ed-gutter");
    ta.value = e.text;  // .value, not markup: a leading new line or a < stays as it is
    const number = () => {
      const n = (ta.value.match(/\n/g) || []).length + 1;
      if (Number(gutter.dataset.n) === n) return;
      gutter.dataset.n = n;
      gutter.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n");
      const count = root.querySelector(".rp-ed-count");
      if (count) count.textContent = plural(n, "line");
    };
    number();
    ta.addEventListener("input", () => {
      e.text = ta.value;
      e.discard = false;
      number();
      const go = root.querySelector('[data-rp="edit-commit"]');
      if (go) go.disabled = !dirty();
    });
    ta.addEventListener("scroll", () => { gutter.scrollTop = ta.scrollTop; });
    ta.addEventListener("keydown", (ev) => {
      if (ev.key !== "Tab" || ev.shiftKey || ev.ctrlKey || ev.altKey || ev.metaKey || ta.readOnly) return;
      ev.preventDefault();  // a tab in code; Shift+Tab still leaves the editor
      ta.setRangeText("\t", ta.selectionStart, ta.selectionEnd, "end");
      ta.dispatchEvent(new Event("input"));
    });
    const form = box.querySelector("[data-rp-form]");
    if (!form) return;
    form.querySelector("[data-rp-msg]").value = e.message;
    form.querySelector("[data-rp-newname]").value = e.name;
    form.addEventListener("submit", (ev) => ev.preventDefault());
    form.addEventListener("input", (ev) => {
      if (ev.target.matches("[data-rp-msg]")) e.message = ev.target.value;
      if (ev.target.matches("[data-rp-newname]")) e.name = ev.target.value;
      disarm();
    });
    form.addEventListener("change", (ev) => {
      if (!ev.target.matches('input[name="rp-where"]')) return;
      e.where = ev.target.value;
      form.querySelector("[data-rp-newname]").disabled = e.where !== "new";
      disarm();
    });
  }
  // The commit button arms on the first press and commits on the second; any change disarms it.
  function disarm() {
    const e = st.edit;
    if (!e || !e.armed) return;
    e.armed = false;
    clearTimeout(armTimer);
    const go = root.querySelector('[data-rp="commit-go"]');
    if (go) { go.classList.remove("btn--armed"); go.classList.add("btn--primary"); go.lastChild.textContent = "Commit"; }
  }
  function commitGo() {
    const e = st.edit;
    if (!e || e.busy) return;
    const problem = !e.message.trim() ? "Write a commit message."
      : e.where === "new" ? badBranch(e.name.trim())
        : guarded(e.branch) ? `PixelDeck never commits straight to ${e.branch}.` : "";
    const content = e.endings === "crlf" ? e.text.replace(/\n/g, "\r\n") : e.text;
    const tooBig = new TextEncoder().encode(content).length > FILE_MAX ? "The file must stay under 512 KB to commit it here." : "";
    if (problem || tooBig) { e.error = problem || tooBig; e.armed = false; return paintCode(); }
    if (!e.armed) {
      e.armed = true;
      e.error = "";
      paintCode();
      clearTimeout(armTimer);
      armTimer = setTimeout(disarm, 5000);
      return;
    }
    clearTimeout(armTimer);
    sendCommit(content);
  }
  async function sendCommit(content) {
    const e = st.edit;
    e.busy = true;
    e.armed = false;
    paintCode();
    try {
      e.done = await api("repo-commit", { method: "POST", body: {
        repo: e.repo, branch: e.branch, parent: e.parent, path: e.path, content, message: e.message,
        new_branch: e.where === "new" ? e.name.trim() : "",
      } });
      e.stage = "done";
      toast(`Committed to ${e.done.branch}.`, e.done.new_branch ? "A new branch: open a pull request when you are ready." : e.path);
    } catch (err) {
      e.error = err.message;
    }
    e.busy = false;
    if (st.edit === e) paintCode();
  }
  // After a commit the branch list changed: read it again, then show the file where it went.
  async function closeEdit() {
    const e = st.edit;
    st.edit = null;
    st.codeKey = "";
    if (!e?.done) return loadCode();
    const slug = st.repo.slug;
    try {
      const b = await api(`repo-branches?${query({ repo: slug })}`);
      if (st.repo?.slug !== slug) return;
      st.branches = b;
      st.branchesAt = Date.now();
    } catch { /* the branch is still found by name below */ }
    paintTabs();
    setBranch(e.done.branch);
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
  const prSig = (prs) => prs.map((pr) => `${prKey(pr)}@${pr.updated_on}`).join(",");
  // The app reads pull requests on its own clock: when what it holds for this repository
  // changes (the first load after a reload, a new pull request), the count and the tab catch up.
  function watchPrs() {
    if (!st.repo || !root.isConnected || document.hidden) return;
    const sig = prSig(openPrs(st.repo.slug));
    if (sig === st.prSig) return;
    st.prSig = sig;
    paintTabs();
    if (st.tab === "prs") paintPrs();
  }
  function paintPrs() {
    const pane = root.querySelector(".rp-pane");
    if (!pane) return;
    const prs = openPrs(st.repo.slug);
    st.prSig = prSig(prs);
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
    const pin = t.closest("[data-rp-pin]");
    if (pin) {
      const slug = pin.dataset.rpPin;
      ui.pins = ui.pins.includes(slug) ? ui.pins.filter((s) => s !== slug) : [...ui.pins, slug].slice(-50);
      saveUi();
      return paintList();
    }
    const stackBtn = t.closest("[data-rp-stack]");
    if (stackBtn) {
      const slug = stackBtn.dataset.rpStack;
      if (ui.open[slug]) delete ui.open[slug]; else ui.open[slug] = true;
      saveUi();
      return paintList();
    }
    const sec = t.closest("[data-rp-sec]");
    if (sec) { ui.folds[sec.dataset.rpSec] = sec.getAttribute("aria-expanded") !== "true"; saveUi(); return paintList(); }
    const kind = t.closest("[data-rp-kind]");
    if (kind) { ui.kind = kind.dataset.rpKind; saveUi(); return paintList(); }
    const side = t.closest("[data-rp-side]");
    if (side && !side.disabled) { st.side = side.dataset.rpSide; remember(); return paintSide(); }
    const open = t.closest("[data-rp-open]");
    if (open) return openRepo(open.dataset.rpOpen);
    const jump = t.closest("[data-rp-jump]");
    if (jump) return openRepo(jump.dataset.rpJump);
    const fold = t.closest("[data-rp-dir]");
    if (fold) { const d = fold.dataset.rpDir; if (st.dirs.has(d)) st.dirs.delete(d); else st.dirs.add(d); remember(); return paintTree(); }
    const act = t.closest("[data-rp]")?.dataset.rp;
    if (act === "back") return back();
    if (act === "list") { st.side = "repos"; remember(); return paintSide(); }
    if (act === "refresh") return load({ force: true });
    if (act === "reopen" && st.repo) return openRepo(st.repo.slug, { restore: true });
    if (act === "retry-code") { st.codeKey = ""; return loadCode(); }
    if (act === "retry-commits") { st.commits = null; return loadCommits(); }
    if (act === "edit") return startEdit();
    if (act === "edit-locked") return toast("This file is edited on Bitbucket.", t.closest("[data-tip]")?.dataset.tip || "");
    if (act === "edit-commit" && dirty()) { st.edit.stage = "commit"; st.edit.error = ""; paintCode(); return root.querySelector("[data-rp-msg]")?.focus(); }
    if (act === "edit-back" && st.edit) { st.edit.stage = "edit"; st.edit.armed = false; return paintCode(); }
    if (act === "commit-go") return commitGo();
    if (act === "edit-cancel" && st.edit) {
      if (dirty() && !st.edit.discard) { st.edit.discard = true; return paintCode(); }
      return closeEdit();
    }
    if (act === "edit-close") return closeEdit();
    if (act === "open-pr" && st.edit?.done) return openPreview(st.edit.done.repo, st.edit.done.branch);
    const tab = t.closest("[data-rp-tab]");
    if (tab) return setTab(tab.dataset.rpTab);
    const dir = t.closest("[data-rp-path]");
    if (dir) return goPath(dir.dataset.rpPath);
    const file = t.closest("[data-rp-file]");
    if (file) return goFile(file.dataset.rpFile);
    const branch = t.closest("[data-rp-branch]");
    if (branch) {
      if (guardEdit()) return;
      st.tab = "code";
      remember();
      paintTabs();
      paintPane();
      setBranch(branch.dataset.rpBranch);
      scrollMain();
    }
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
    if (!e.target.matches("[data-rp-pick]") || !st.repo || !e.target.value || e.target.value === st.branch) return;
    if (guardEdit()) { e.target.value = st.branch; RetroSelect.sync(e.target); return; }
    setBranch(e.target.value);
  }

  function mount(el) {
    root = el;
    root.classList.add("rp");
    root.addEventListener("click", onClick);
    root.addEventListener("input", onInput);
    root.addEventListener("keydown", onKey);
    root.addEventListener("change", onChange);
    document.addEventListener("change", (e) => { if (e.target?.id === "project") watchProject(e.target.value); });
    frame();
    // Nothing is read until the panel is first in view; each time it shows, the counts catch up.
    new IntersectionObserver((entries) => {
      if (!entries.some((x) => x.isIntersecting)) return;
      if (!st.list && !st.loading) load();
      else if (st.repo) { paintTabs(); refreshBranches(); }
    }).observe(root);
    setInterval(watchPrs, 3000);
  }
  return { mount };
})();
